#!/usr/bin/env python3
"""Execute a prepared NextGame UMG call plan against the local Unreal MCP server."""

from __future__ import annotations

import argparse
import json
import re
import sys
try:
    import requests
except ImportError as exc:
    raise RuntimeError("execute_plan.py requires the Python requests package.") from exc
from pathlib import Path
from typing import Any

TOKEN = re.compile(r"^\$\{(.+)\}$")


class McpClient:
    def __init__(self, url: str, timeout: float) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self.next_id = 1

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            response = requests.post(self.url, headers=headers, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"Cannot connect to Unreal MCP at {self.url}: {exc}") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"MCP HTTP {response.status_code}: {response.text}")
        if response.headers.get("Mcp-Session-Id"):
            self.session_id = response.headers["Mcp-Session-Id"]
        message = self._decode_response(response.text)
        return message, response.headers

    @staticmethod
    def _decode_response(raw: str) -> dict[str, Any]:
        stripped = raw.strip()
        if not stripped:
            return {}
        if stripped.startswith("{"):
            return json.loads(stripped)
        data_lines = [line[6:] for line in stripped.splitlines() if line.startswith("data: ")]
        if not data_lines:
            raise RuntimeError(f"Unsupported MCP response: {stripped[:500]}")
        return json.loads(data_lines[-1])

    def initialize(self) -> None:
        message, _ = self._post({
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "nextgame-ui-plan-executor", "version": "0.1.0"},
            },
        })
        self.next_id += 1
        if message.get("error"):
            raise RuntimeError(f"MCP initialize failed: {message['error']}")
        if not self.session_id:
            raise RuntimeError("MCP server did not return Mcp-Session-Id.")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call_tool(self, toolset: str, tool: str, arguments: dict[str, Any]) -> Any:
        message, _ = self._post({
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": "tools/call",
            "params": {
                "name": "call_tool",
                "arguments": {
                    "toolset_name": toolset,
                    "tool_name": tool,
                    "arguments": arguments,
                },
            },
        })
        self.next_id += 1
        if message.get("error"):
            raise RuntimeError(f"MCP tool error: {message['error']}")
        result = message.get("result", {})
        if result.get("isError"):
            content = result.get("content", [])
            detail = content[0].get("text") if content else result
            raise RuntimeError(f"Tool {toolset}.{tool} failed: {detail}")
        content = result.get("content", [])
        if not content:
            return None
        text = content[0].get("text")
        if text is None:
            return content[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def lookup_reference(expression: str, saved: dict[str, Any]) -> Any:
    for key in sorted(saved, key=len, reverse=True):
        if expression == key:
            return saved[key]
        prefix = key + "."
        if expression.startswith(prefix):
            value = saved[key]
            for part in expression[len(prefix):].split("."):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    raise KeyError(f"Reference {expression!r} failed at {part!r}.")
            return value
    raise KeyError(f"Unknown saved result in reference: {expression!r}")


def resolve(value: Any, saved: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = TOKEN.fullmatch(value)
        return lookup_reference(match.group(1), saved) if match else value
    if isinstance(value, list):
        return [resolve(item, saved) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, saved) for key, item in value.items()}
    return value


def ref_paths(result: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(result, dict):
        if isinstance(result.get("refPath"), str):
            found.add(result["refPath"])
        for value in result.values():
            found.update(ref_paths(value))
    elif isinstance(result, list):
        for value in result:
            found.update(ref_paths(value))
    return found


def compact_result(tool_name: str, result: Any) -> Any:
    """Keep logs useful without embedding multi-kilobyte property schemas."""
    if tool_name == "list_properties" and isinstance(result, dict):
        raw = result.get("returnValue")
        if isinstance(raw, str):
            try:
                properties = json.loads(raw)
            except json.JSONDecodeError:
                return {"returnValue": "<property schema omitted>"}
            if isinstance(properties, dict):
                return {"propertyNames": sorted(properties)}
    return result


def _load_log_events(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Resume log is not valid JSON: {path}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RuntimeError(f"Resume log must contain an event array: {path}")
    return payload


def _prepare_resume(plan: dict[str, Any], events: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    """Validate a log and recover only its contiguous completed prefix."""
    steps = plan.get("steps", [])
    expected = {index: step["stepId"] for index, step in enumerate(steps, start=1)}
    latest: dict[int, dict[str, Any]] = {}
    for event in events:
        status = event.get("status")
        index = event.get("index")
        if status not in {"started", "completed", "skipped"} or index is None:
            continue
        if not isinstance(index, int) or index not in expected:
            raise RuntimeError(f"Resume log contains an invalid step index: {index!r}")
        step_id = event.get("stepId")
        if step_id != expected[index]:
            raise RuntimeError(
                f"Resume log step mismatch at index {index}: expected {expected[index]!r}, got {step_id!r}."
            )
        latest[index] = event

    prefix = 0
    for index in range(1, len(steps) + 1):
        event = latest.get(index)
        if not event or event.get("status") != "completed":
            break
        prefix = index

    # A later completed step with an incomplete predecessor is a hole, not a
    # resumable prefix. Refuse it rather than silently skipping unsafe work.
    for index, event in latest.items():
        if index > prefix and event.get("status") == "completed":
            raise RuntimeError(f"Resume log has a completed step after an incomplete prefix at index {index}.")

    saved: dict[str, Any] = {}
    for index in range(1, prefix + 1):
        step = steps[index - 1]
        if not step.get("saveResultAs"):
            continue
        event = latest[index]
        if "result" not in event:
            raise RuntimeError(f"Resume log completed event at index {index} has no raw result to restore.")
        saved[step["saveResultAs"]] = event["result"]
    return prefix, saved


def execute(
    plan: dict[str, Any],
    client: McpClient,
    log_path: Path | None,
    resume_existing: bool = False,
    resume_log: Path | None = None,
) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    resume_prefix = 0
    if resume_log:
        if resume_existing:
            raise RuntimeError("--resume-log cannot be combined with the legacy --resume-existing option.")
        events = _load_log_events(resume_log)
        resume_prefix, saved = _prepare_resume(plan, events)
        if log_path is None:
            log_path = resume_log
    object_path = plan["objectPath"]
    destination_found = False

    def record(event: dict[str, Any]) -> None:
        events.append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps(events, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if resume_log:
        record({
            "status": "resumed",
            "resumeFromIndex": resume_prefix + 1,
            "completedPrefix": resume_prefix,
            "sourceLog": str(resume_log),
        })

    for index, step in enumerate(plan.get("steps", []), start=1):
        if index <= resume_prefix:
            continue
        step_id = step["stepId"]
        if step_id == "create-blueprint" and resume_existing and destination_found:
            record({"status": "skipped", "index": index, "stepId": step_id, "reason": "Resuming the confirmed existing partial prototype."})
            continue
        arguments = resolve(step.get("arguments", {}), saved)
        if step["toolName"] == "set_properties" and isinstance(arguments.get("values"), dict):
            arguments["values"] = json.dumps(arguments["values"], separators=(",", ":"), ensure_ascii=False)
        record({"status": "started", "index": index, "stepId": step_id, "tool": f"{step['toolsetName']}.{step['toolName']}"})
        result = client.call_tool(step["toolsetName"], step["toolName"], arguments)

        if step_id == "check-destination":
            destination_found = object_path in ref_paths(result)
            if destination_found:
                if not resume_existing:
                    raise RuntimeError(f"Destination already exists: {object_path}")
                saved["blueprint"] = {"returnValue": {"refPath": object_path}}
            elif resume_existing:
                raise RuntimeError(f"Cannot resume because destination does not exist: {object_path}")
        if step.get("assertion") == "returnValue must be true":
            if not isinstance(result, dict) or result.get("returnValue") is not True:
                raise RuntimeError(f"Assertion failed at {step_id}: {result!r}")
        if step.get("saveResultAs"):
            saved[step["saveResultAs"]] = result
        record({"status": "completed", "index": index, "stepId": step_id, "result": compact_result(step["toolName"], result)})

    return {"ok": True, "assetPath": plan.get("assetPath"), "stepsCompleted": len(plan.get("steps", [])), "savedResults": sorted(saved)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--check-only", action="store_true", help="Initialize MCP but do not mutate the Editor.")
    parser.add_argument("--resume-existing", action="store_true", help="Resume only a confirmed existing partial prototype and skip creation.")
    parser.add_argument("--resume-log", type=Path, help="Resume the contiguous completed prefix recorded in an existing execution log.")
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        client = McpClient(args.mcp_url, args.timeout)
        client.initialize()
        if args.check_only:
            print(json.dumps({"ok": True, "mcpUrl": args.mcp_url, "session": "initialized"}, indent=2))
            return 0
        result = execute(plan, client, args.log, args.resume_existing, args.resume_log)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())