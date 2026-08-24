#!/usr/bin/env python3
"""Run prepared UMG plans through ProgrammaticToolset in resumable chunks.

This executor deliberately keeps plan semantics in the established
``execute_plan.py`` helpers, but batches Editor work through
``execute_tool_script``.  A checkpoint is only advanced after a whole chunk
has completed, so it is safe to retry a failed request.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ENV = "editor_toolset.toolsets.programmatic.ProgrammaticToolset.get_execution_environment"
EXEC = "editor_toolset.toolsets.programmatic.ProgrammaticToolset.execute_tool_script"


def plan_digest(plan: dict[str, Any]) -> str:
    """Return the stable digest used to bind a checkpoint to a plan."""
    payload = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_property_sequences(steps: list[dict[str, Any]]) -> None:
    """Require every property write to retain its list/get/set schema guard."""
    for index, step in enumerate(steps):
        if step.get("toolName") != "set_properties":
            continue
        previous = [item.get("toolName") for item in steps[max(0, index - 2):index]]
        if previous != ["list_properties", "get_properties"]:
            raise ValueError("set_properties sequence invalid at %d" % (index + 1))


def chunk_steps(steps: list[dict[str, Any]], max_steps_per_chunk: int) -> list[list[dict[str, Any]]]:
    """Group work at AddWidget boundaries without splitting property triplets."""
    if max_steps_per_chunk <= 0:
        raise ValueError("max_steps_per_chunk must be positive")
    validate_property_sequences(steps)

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for step in steps:
        if current and step.get("toolName") == "AddWidget":
            groups.append(current)
            current = []
        current.append(step)
    if current:
        groups.append(current)

    chunks: list[list[dict[str, Any]]] = []
    current = []
    for group in groups:
        if current and len(current) + len(group) > max_steps_per_chunk:
            chunks.append(current)
            current = []
        current += group
    if current:
        chunks.append(current)
    return chunks


def build_programmatic_script(
    plan: dict[str, Any], steps: list[dict[str, Any]], saved: dict[str, Any]
) -> str:
    """Build the restricted ProgrammaticToolset script for one execution chunk."""
    chunk_plan = dict(plan)
    chunk_plan["steps"] = steps
    # ``repr`` is intentionally applied exactly once to each JSON document.
    # Applying it twice turns the JSON string into quoted JSON and makes
    # ProgrammaticToolset fail at ``json.loads`` with "Expecting value".
    encoded_plan = repr(json.dumps(chunk_plan, ensure_ascii=False, separators=(",", ":")))
    encoded_saved = repr(json.dumps(saved or {}, ensure_ascii=False, separators=(",", ":")))
    return f'''import json
import math
import datetime
import copy
import re
import time
PLAN=json.loads({encoded_plan})
SAVED=copy.deepcopy(json.loads({encoded_saved}))
TOKEN=re.compile(r"^\\$\\{{(.+)\\}}$")
def lookup(e):
    for k in sorted(SAVED,key=len,reverse=True):
        if e==k:return SAVED[k]
        if e.startswith(k+"."):
            v=SAVED[k]
            for part in e[len(k)+1:].split("."):v=v[part]
            return v
    raise KeyError(e)
def resolve(v):
    if isinstance(v,str):
        m=TOKEN.fullmatch(v);return lookup(m.group(1)) if m else v
    if isinstance(v,list):return [resolve(x) for x in v]
    if isinstance(v,dict):return {{k:resolve(x) for k,x in v.items()}}
    return v
def run():
    events=[];done=0
    try:
        for s in PLAN.get("steps",[]):
            a=resolve(s.get("arguments",{{}}))
            if s["toolName"]=="set_properties" and isinstance(a.get("values"),dict):a["values"]=json.dumps(a["values"],ensure_ascii=False,separators=(",",":"))
            r=execute_tool(s["toolsetName"]+"."+s["toolName"],json.dumps(a,ensure_ascii=False,separators=(",",":")))
            if s.get("assertion")=="returnValue must be true" and (not isinstance(r,dict) or r.get("returnValue") is not True):raise RuntimeError(s["stepId"])
            if s.get("saveResultAs"):SAVED[s["saveResultAs"]]=r
            done+=1;events.append({{"status":"completed","index":done,"stepId":s["stepId"],"result":r}})
        return {{"ok":True,"completed":done,"saved":SAVED,"events":events}}
    except Exception as e:
        return {{"ok":False,"completed":done,"saved":SAVED,"events":events+[
            {{"status":"failed","index":done+1,"error":str(e)}}],"error":str(e)}}
'''


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Cannot read JSON " + str(path)) from exc


def _atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _resume(plan: dict[str, Any], path: Path | None) -> tuple[int, dict[str, Any]]:
    if not path:
        return 0, {}
    try:
        from execute_plan import _load_log_events, _prepare_resume
    except ModuleNotFoundError:
        # Running the script by path does not necessarily add its directory to
        # sys.path.  Preserve the legacy helpers in that case as well.
        import importlib.util

        legacy_path = Path(__file__).with_name("execute_plan.py")
        spec = importlib.util.spec_from_file_location("execute_plan", legacy_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load resume helpers: {legacy_path}")
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        _load_log_events = legacy._load_log_events
        _prepare_resume = legacy._prepare_resume
    return _prepare_resume(plan, _load_log_events(path))


def _fail(
    path: Path | None,
    digest: str,
    done: int,
    saved: dict[str, Any],
    events: list[dict[str, Any]],
    error: str,
    chunk: list[dict[str, Any]],
    environment: Any,
) -> dict[str, Any]:
    requires_get_widgets = any(step.get("toolName") == "AddWidget" for step in chunk)
    if path:
        _atomic(path, {
            "planSha256": digest,
            "completedPrefix": done,
            "saved": saved,
            "events": events,
            "status": "failed",
            "error": error,
            "requiresGetWidgets": requires_get_widgets,
            "schemaBinding": {"environment": environment},
        })
    return {
        "ok": False,
        "completedPrefix": done,
        "checkpointAdvanced": False,
        "error": error,
        "requiresGetWidgets": requires_get_widgets,
    }


def run_plan(
    plan: dict[str, Any],
    client: Any,
    checkpoint: Path | None = None,
    resume_log: Path | None = None,
    run_all: bool = False,
    max_steps_per_chunk: int = 24,
) -> dict[str, Any]:
    """Run at most one chunk by default, or all pending chunks when requested."""
    digest = plan_digest(plan)
    done = 0
    saved: dict[str, Any] = {}
    state: dict[str, Any] = {}
    if checkpoint and checkpoint.exists() and checkpoint.stat().st_size > 0:
        state = _load(checkpoint)
        if state.get("planSha256") != digest:
            raise RuntimeError("Checkpoint plan SHA256 does not match input plan")
        done = int(state.get("completedPrefix", 0))
        saved = dict(state.get("saved", {}))

    log_done, log_saved = _resume(plan, resume_log)
    if log_done > done:
        done, saved = log_done, log_saved

    # This must happen before a script request so the generated program can
    # rely on the server's current restricted execution contract.
    environment = client.call_tool(ENV, {})
    chunks = chunk_steps(plan.get("steps", []), max_steps_per_chunk)
    indexed: list[tuple[int, int, list[dict[str, Any]]]] = []
    current = 0
    for chunk in chunks:
        indexed.append((current + 1, current + len(chunk), chunk))
        current += len(chunk)
    pending = [item for item in indexed if item[1] > done]
    if not run_all:
        pending = pending[:1]

    events = list(state.get("events", []))
    for _first, last, chunk in pending:
        try:
            result = client.call_tool(EXEC, {"script": build_programmatic_script(plan, chunk, saved)})
        except Exception as exc:
            return _fail(checkpoint, digest, done, saved, events, str(exc), chunk, environment)
        if not isinstance(result, dict) or result.get("ok") is not True:
            error = result.get("error", "chunk failed") if isinstance(result, dict) else "invalid"
            return _fail(checkpoint, digest, done, saved, events, error, chunk, environment)

        done = last
        saved = dict(result.get("saved", saved))
        events += result.get("events", [])
        if checkpoint:
            _atomic(checkpoint, {
                "planSha256": digest,
                "completedPrefix": done,
                "saved": saved,
                "events": events,
                "status": "completed" if done == len(plan.get("steps", [])) else "checkpoint",
                "schemaBinding": {"environment": environment},
                "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })

    return {
        "ok": True,
        "completedPrefix": done,
        "saved": saved,
        "events": events,
        "chunksExecuted": len(pending),
        "planSha256": digest,
    }


class _CliClient:
    def __init__(self, url: str, timeout: float) -> None:
        from execute_plan import McpClient

        self.client = McpClient(url, timeout)
        self.client.initialize()

    def call_tool(self, full_name: str, arguments: dict[str, Any]) -> Any:
        toolset, tool = full_name.rsplit(".", 1)
        result = self.client.call_tool(toolset, tool, arguments)
        wrapped = result.get("returnValue") if isinstance(result, dict) else None
        if full_name == EXEC and isinstance(wrapped, str) and wrapped.strip():
            try:
                return json.loads(wrapped)
            except json.JSONDecodeError as exc:
                preview = wrapped.strip()[:200]
                raise RuntimeError(
                    f"ProgrammaticToolset returned invalid JSON in returnValue: {preview!r}"
                ) from exc
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume-log", type=Path)
    parser.add_argument("--run-all", action="store_true")
    parser.add_argument("--max-steps-per-chunk", type=int, default=24)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    result = run_plan(
        _load(args.plan),
        _CliClient(args.mcp_url, args.timeout),
        args.checkpoint,
        args.resume_log,
        args.run_all,
        args.max_steps_per_chunk,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
