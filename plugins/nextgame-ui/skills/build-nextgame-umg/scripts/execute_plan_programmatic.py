#!/usr/bin/env python3
"""Run prepared UMG plans through ProgrammaticToolset in resumable chunks.

The model-facing result is deliberately small. Raw evidence that is not
needed to resume execution is content-addressed below an explicit artifact
directory, while the checkpoint retains only the live saved results required
by future plan references.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime
import hashlib
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Iterable


ENV = "editor_toolset.toolsets.programmatic.ProgrammaticToolset.get_execution_environment"
EXEC = "editor_toolset.toolsets.programmatic.ProgrammaticToolset.execute_tool_script"
TOKEN = re.compile(r"^\$\{(.+)\}$")
DATA_URL = re.compile(
    r"^data:(?P<mime>[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*)(?:;[^,]*)?;base64,(?P<data>.*)$",
    re.DOTALL,
)
MAX_DECODED_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_384 * 16_384
MAX_INLINE_EVENT_RESULT_BYTES = 4096
MAX_ERROR_MESSAGE_CHARS = 512
CHECKPOINT_FORMAT_VERSION = 2
SCHEMA_SAVED_MARKER = "$externalSchemaResult"
WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/][^\s\"'<>|]+"
)


def plan_digest(plan: dict[str, Any]) -> str:
    """Return the stable digest used to bind a checkpoint to a plan."""
    payload = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _compact_error(
    value: Any,
    public_message: str = "Operation failed; correlate with errorSha256",
) -> dict[str, Any]:
    """Bound error context while retaining a stable identity for correlation."""

    raw = str(value)
    normalized = re.sub(r"\s+", " ", public_message).strip() or "Operation failed"
    normalized = WINDOWS_ABSOLUTE_PATH.sub("<path>", normalized)
    truncated = len(raw) > MAX_ERROR_MESSAGE_CHARS
    message = normalized[:MAX_ERROR_MESSAGE_CHARS]
    if len(normalized) > MAX_ERROR_MESSAGE_CHARS:
        message = message.rstrip() + "..."
    return {
        "message": message,
        "sha256": hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest(),
        "charLength": len(raw),
        "truncated": truncated,
    }


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


def _reference_expressions(value: Any) -> Iterable[str]:
    """Yield exact saved-result expressions and reject partial token syntax."""
    if isinstance(value, str):
        match = TOKEN.fullmatch(value)
        if match:
            if not match.group(1):
                raise ValueError("Empty saved-result reference")
            yield match.group(1)
        elif "${" in value or ("}" in value and "$" in value):
            raise ValueError(f"Saved-result token must occupy the complete string: {value!r}")
        return
    if isinstance(value, list):
        for item in value:
            yield from _reference_expressions(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _reference_expressions(item)


def _matching_saved_key(expression: str, keys: Iterable[str]) -> str | None:
    for key in sorted(keys, key=len, reverse=True):
        if expression == key or expression.startswith(key + "."):
            return key
    return None


def _is_artifact_descriptor(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("artifactPath"), str)
        and isinstance(value.get("sha256"), str)
        and isinstance(value.get("mimeType"), str)
    )


def _contains_artifact_descriptor(value: Any) -> bool:
    if _is_artifact_descriptor(value):
        return True
    if isinstance(value, dict):
        return any(_contains_artifact_descriptor(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_artifact_descriptor(item) for item in value)
    return False


def _validate_artifact_descriptors(value: Any, root: Path | None) -> None:
    if _is_artifact_descriptor(value):
        if root is None:
            raise RuntimeError("Saved artifact descriptor has no configured artifact directory")
        digest = value["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError("Saved artifact descriptor has an invalid SHA-256")
        mime_type = value["mimeType"]
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", mime_type
        ):
            raise RuntimeError("Saved artifact descriptor has an unsafe MIME type")
        path = Path(value["artifactPath"])
        if not path.is_absolute():
            raise RuntimeError("Saved artifact path must be absolute")
        path = path.resolve()
        if not _inside(path, root):
            raise RuntimeError("Saved artifact escaped the configured directory")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeError(f"Saved artifact is corrupt: {path}")
        byte_length = value.get("byteLength")
        if byte_length is not None and byte_length != len(payload):
            raise RuntimeError(f"Saved artifact byteLength is stale: {path}")
        for dimension in ("width", "height"):
            number = value.get(dimension)
            if number is not None and (not isinstance(number, int) or number <= 0):
                raise RuntimeError(f"Saved artifact {dimension} is invalid")
        width = value.get("width")
        height = value.get("height")
        if isinstance(width, int) and isinstance(height, int) and width * height > MAX_IMAGE_PIXELS:
            raise RuntimeError("Saved artifact image dimensions exceed the safety limit")
        # A descriptor may carry recursively externalized source metadata.
        # Continue below so every nested descriptor is hash-checked as well.
    if isinstance(value, dict):
        for item in value.values():
            _validate_artifact_descriptors(item, root)
    elif isinstance(value, list):
        for item in value:
            _validate_artifact_descriptors(item, root)


def live_saved_for_steps(
    steps: list[dict[str, Any]],
    saved: dict[str, Any],
    validate_paths: bool = True,
) -> dict[str, Any]:
    """Return only external saved values referenced by ``steps``.

    Producers earlier in the same chunk satisfy later references without
    injecting an old value. A reference to a missing or later producer is an
    error before any Editor mutation is requested.
    """
    available = set(saved)
    produced_here: set[str] = set()
    live_keys: set[str] = set()
    for step in steps:
        for expression in _reference_expressions(step.get("arguments", {})):
            key = _matching_saved_key(expression, available)
            if key is None:
                raise KeyError(
                    f"Unresolved saved-result reference {expression!r} at step {step.get('stepId')!r}"
                )
            if key not in produced_here:
                live_keys.add(key)
                if validate_paths:
                    current = saved[key]
                    if expression != key:
                        for part in expression[len(key) + 1:].split("."):
                            if not isinstance(current, dict) or part not in current:
                                raise KeyError(
                                    f"Saved-result reference {expression!r} failed at {part!r} "
                                    f"in step {step.get('stepId')!r}"
                                )
                            current = current[part]
                    if _contains_artifact_descriptor(current):
                        raise ValueError(
                            f"Saved-result reference {expression!r} would substitute an externalized "
                            "artifact descriptor for the original MCP payload; reference an explicit "
                            "artifactPath/sha256/mimeType/dimension field instead"
                        )
        save_key = step.get("saveResultAs")
        if isinstance(save_key, str) and save_key:
            available.add(save_key)
            produced_here.add(save_key)
    return {key: saved[key] for key in sorted(live_keys)}


def build_programmatic_script(
    plan: dict[str, Any], steps: list[dict[str, Any]], saved: dict[str, Any]
) -> str:
    """Build one restricted script with only the chunk's live saved inputs."""
    chunk_plan = dict(plan)
    prepared_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        prepared = dict(step)
        if step.get("toolName") == "list_properties":
            prepared["_requiredPropertyNames"] = _required_property_names(
                steps, index
            )
        prepared_steps.append(prepared)
    chunk_plan["steps"] = prepared_steps
    live_saved = live_saved_for_steps(steps, saved)
    # ``repr`` is intentionally applied exactly once to each JSON document.
    encoded_plan = repr(json.dumps(chunk_plan, ensure_ascii=False, separators=(",", ":")))
    encoded_saved = repr(json.dumps(live_saved, ensure_ascii=False, separators=(",", ":")))
    return f'''import json
import math
import datetime
import copy
import re
import time
PLAN=json.loads({encoded_plan})
SAVED=copy.deepcopy(json.loads({encoded_saved}))
SAVED_DELTA={{}}
TOKEN=re.compile(r"^\\$\\{{(.+)\\}}$")
def lookup(e):
    for k in sorted(SAVED,key=len,reverse=True):
        if e==k:return SAVED[k]
        if e.startswith(k+"."):
            v=SAVED[k]
            for part in e[len(k)+1:].split("."):
                if not isinstance(v,dict) or part not in v:raise KeyError(e)
                v=v[part]
            return v
    raise KeyError(e)
def resolve(v):
    if isinstance(v,str):
        m=TOKEN.fullmatch(v)
        if m:return lookup(m.group(1))
        if "${{" in v:raise ValueError("partial saved-result token: "+v)
        return v
    if isinstance(v,list):return [resolve(x) for x in v]
    if isinstance(v,dict):return {{k:resolve(x) for k,x in v.items()}}
    return v
def schema_names(r):
    raw=r.get("returnValue") if isinstance(r,dict) else r
    if isinstance(raw,str):
        try:raw=json.loads(raw)
        except Exception:return set()
    if isinstance(raw,dict):
        p=raw.get("properties")
        if isinstance(p,dict):return set(str(k) for k in p)
        return set(str(k) for k in raw)
    if isinstance(raw,list):
        names=set()
        for item in raw:
            if isinstance(item,str):names.add(item)
            elif isinstance(item,dict) and isinstance(item.get("name"),str):names.add(item["name"])
        return names
    return set()
def run():
    events=[];done=0
    try:
        for s in PLAN.get("steps",[]):
            a=resolve(s.get("arguments",{{}}))
            if s["toolName"]=="set_properties" and isinstance(a.get("values"),dict):a["values"]=json.dumps(a["values"],ensure_ascii=False,separators=(",",":"))
            tool=s["toolsetName"]+"."+s["toolName"]
            r=execute_tool(tool,json.dumps(a,ensure_ascii=False,separators=(",",":")))
            if s["toolName"]=="list_properties":
                required=set(str(x) for x in s.get("_requiredPropertyNames",[]))
                if not required.issubset(schema_names(r)):raise RuntimeError("property schema guard failed")
            if s.get("assertion")=="returnValue must be true" and (not isinstance(r,dict) or r.get("returnValue") is not True):raise RuntimeError(s["stepId"])
            if s.get("saveResultAs"):
                SAVED[s["saveResultAs"]]=r
                SAVED_DELTA[s["saveResultAs"]]=r
            done+=1;events.append({{"status":"completed","index":done,"stepId":s["stepId"],"tool":tool,"result":r}})
        return {{"ok":True,"completed":done,"savedDelta":SAVED_DELTA,"events":events}}
    except Exception as e:
        failed=PLAN.get("steps",[])[done] if done<len(PLAN.get("steps",[])) else {{}}
        return {{"ok":False,"completed":done,"savedDelta":SAVED_DELTA,"events":events+[
            {{"status":"failed","index":done+1,"stepId":failed.get("stepId"),"tool":(failed.get("toolsetName","")+"."+failed.get("toolName","")).strip("."),"error":str(e)}}],"error":str(e)}}
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


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _artifact_root(checkpoint: Path | None, artifact_dir: Path | None) -> Path | None:
    if artifact_dir is not None:
        return artifact_dir.resolve()
    if checkpoint is not None:
        return checkpoint.with_name(checkpoint.name + ".artifacts").resolve()
    return None


def _write_content_addressed(
    root: Path | None,
    category: str,
    digest: str,
    extension: str,
    payload: bytes,
) -> Path | None:
    if root is None:
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError("Invalid artifact SHA-256")
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
        raise RuntimeError("Unsafe artifact extension")
    path = root / category / (digest + extension)
    if not _inside(path, root):
        raise RuntimeError("Artifact path escaped the configured directory")
    if path.exists():
        existing = path.read_bytes()
        if hashlib.sha256(existing).hexdigest() != digest:
            raise RuntimeError(f"Hash-bound artifact is corrupt: {path}")
    else:
        _atomic_bytes(path, payload)
    return path


def _resume(plan: dict[str, Any], path: Path | None) -> tuple[int, dict[str, Any]]:
    if not path:
        return 0, {}
    try:
        from execute_plan import _load_log_events, _prepare_resume
    except ModuleNotFoundError:
        legacy_path = Path(__file__).with_name("execute_plan.py")
        spec = importlib.util.spec_from_file_location("execute_plan", legacy_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load resume helpers: {legacy_path}")
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        _load_log_events = legacy._load_log_events
        _prepare_resume = legacy._prepare_resume
    return _prepare_resume(plan, _load_log_events(path))


def _declared_mime(mapping: dict[str, Any]) -> str | None:
    for key in ("mimeType", "mime_type", "mediaType", "contentType"):
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.split(";", 1)[0].strip().lower()
    return None


def _jpeg_size(payload: bytes) -> tuple[int, int] | None:
    offset = 2
    while offset + 9 < len(payload):
        if payload[offset] != 0xFF:
            offset += 1
            continue
        marker = payload[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(payload):
            break
        length = int.from_bytes(payload[offset:offset + 2], "big")
        if length < 2 or offset + length > len(payload):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 7:
            height = int.from_bytes(payload[offset + 3:offset + 5], "big")
            width = int.from_bytes(payload[offset + 5:offset + 7], "big")
            return width, height
        offset += length
    return None


def _image_info(payload: bytes) -> tuple[str, str, int, int] | None:
    if len(payload) >= 24 and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", payload[16:24])
        return "image/png", ".png", width, height
    if len(payload) >= 10 and payload[:6] in {b"GIF87a", b"GIF89a"}:
        width, height = struct.unpack("<HH", payload[6:10])
        return "image/gif", ".gif", width, height
    if len(payload) >= 26 and payload.startswith(b"BM"):
        width = int.from_bytes(payload[18:22], "little", signed=True)
        height = abs(int.from_bytes(payload[22:26], "little", signed=True))
        return "image/bmp", ".bmp", abs(width), height
    if len(payload) >= 4 and payload.startswith(b"\xff\xd8\xff"):
        dimensions = _jpeg_size(payload)
        if dimensions:
            return "image/jpeg", ".jpg", dimensions[0], dimensions[1]
    return None


def _payload_candidate(mapping: dict[str, Any]) -> tuple[str, str, str | None] | None:
    for key in ("data", "base64", "imageData", "image_data"):
        value = mapping.get(key)
        if not isinstance(value, str):
            continue
        match = DATA_URL.fullmatch(value.strip())
        if match:
            return key, match.group("data"), match.group("mime").lower()
        declared = _declared_mime(mapping)
        encoding = str(mapping.get("encoding", "")).lower()
        kind = str(mapping.get("type", "")).lower()
        if declared or encoding == "base64" or kind == "image" or key != "data":
            return key, value, declared
    return None


def _decode_payload(encoded: str) -> bytes:
    compact = "".join(encoded.split())
    if len(compact) > ((MAX_DECODED_ARTIFACT_BYTES + 2) // 3) * 4 + 4:
        raise RuntimeError("Encoded artifact exceeds the safety limit")
    try:
        payload = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("MCP returned malformed base64 artifact data") from exc
    if len(payload) > MAX_DECODED_ARTIFACT_BYTES:
        raise RuntimeError("Decoded artifact exceeds the safety limit")
    return payload


def _externalize_tree(
    value: Any,
    root: Path | None,
    artifact_evidence: dict[str, dict[str, Any]],
) -> Any:
    if isinstance(value, list):
        return [_externalize_tree(item, root, artifact_evidence) for item in value]
    if not isinstance(value, dict):
        return value

    candidate = _payload_candidate(value)
    if candidate is None:
        return {
            key: _externalize_tree(item, root, artifact_evidence)
            for key, item in value.items()
        }
    if root is None:
        raise RuntimeError("Base64 MCP evidence requires --artifact-dir or --checkpoint")

    payload_key, encoded, declared_mime = candidate
    payload = _decode_payload(encoded)
    image = _image_info(payload)
    if declared_mime and declared_mime.startswith("image/"):
        if image is None:
            raise RuntimeError(f"MCP artifact declared {declared_mime} but has no supported image signature")
        compatible = {image[0]}
        if image[0] == "image/jpeg":
            compatible.add("image/jpg")
        if declared_mime not in compatible:
            raise RuntimeError(
                f"MCP artifact MIME {declared_mime} does not match detected {image[0]}"
            )
    if image:
        mime_type, extension, width, height = image
        if width <= 0 or height <= 0:
            raise RuntimeError("MCP image artifact has invalid dimensions")
        if width * height > MAX_IMAGE_PIXELS:
            raise RuntimeError("MCP image artifact dimensions exceed the safety limit")
        category = "images"
    else:
        if declared_mime and not re.fullmatch(
            r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", declared_mime
        ):
            raise RuntimeError(f"Unsafe MCP artifact MIME type: {declared_mime!r}")
        mime_type = declared_mime or "application/octet-stream"
        extension = ".bin"
        width = height = None
        category = "binary"
    digest = hashlib.sha256(payload).hexdigest()
    path = _write_content_addressed(root, category, digest, extension, payload)
    assert path is not None
    descriptor: dict[str, Any] = {
        "artifactPath": str(path),
        "sha256": digest,
        "mimeType": mime_type,
        "byteLength": len(payload),
    }
    if width is not None and height is not None:
        descriptor["width"] = width
        descriptor["height"] = height
    artifact_evidence[digest] = descriptor

    metadata_keys = {
        payload_key, "mimeType", "mime_type", "mediaType", "contentType",
        "encoding", "width", "height", "size", "type",
    }
    remaining = {
        key: _externalize_tree(item, root, artifact_evidence)
        for key, item in value.items()
        if key not in metadata_keys
    }
    result = dict(descriptor)
    if remaining:
        result["sourceMetadata"] = remaining
    return result


def _schema_payload(result: Any) -> tuple[bytes, Any, str]:
    raw = result.get("returnValue") if isinstance(result, dict) else result
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
        try:
            parsed = json.loads(raw)
            mime_type = "application/json"
        except json.JSONDecodeError:
            parsed = None
            mime_type = "text/plain"
        return payload, parsed, mime_type
    payload = _json_bytes(raw)
    return payload, raw, "application/json"


def _schema_property_names(parsed: Any) -> set[str]:
    if isinstance(parsed, dict):
        properties = parsed.get("properties")
        if isinstance(properties, dict):
            return {str(name) for name in properties}
        return {str(name) for name in parsed}
    if isinstance(parsed, list):
        names: set[str] = set()
        for item in parsed:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
        return names
    return set()


def _required_property_names(steps: list[dict[str, Any]], index: int) -> list[str]:
    required: set[str] = set()
    for following in steps[index + 1:index + 3]:
        arguments = following.get("arguments", {})
        if following.get("toolName") == "get_properties":
            properties = arguments.get("properties", [])
            if isinstance(properties, list):
                required.update(str(item) for item in properties)
        elif following.get("toolName") == "set_properties":
            values = arguments.get("values", {})
            if isinstance(values, dict):
                required.update(str(item) for item in values)
    return sorted(required)


def _record_schema(
    result: Any,
    required_names: list[str],
    root: Path | None,
    schema_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if root is None:
        raise RuntimeError("list_properties evidence requires --artifact-dir or --checkpoint")
    payload, parsed, mime_type = _schema_payload(result)
    digest = hashlib.sha256(payload).hexdigest()
    extension = ".json" if mime_type == "application/json" else ".txt"
    path = _write_content_addressed(root, "schemas", digest, extension, payload)
    property_names = _schema_property_names(parsed)
    missing = [name for name in required_names if name not in property_names]
    descriptor: dict[str, Any] = {
        "sha256": digest,
        "mimeType": mime_type,
        "byteLength": len(payload),
        "propertyCount": len(property_names),
    }
    if path is not None:
        descriptor["artifactPath"] = str(path)
    schema_evidence[digest] = descriptor
    compact: dict[str, Any] = {
        "schemaHash": digest,
        "propertyCount": len(property_names),
        "requiredNamesPresent": not missing,
    }
    if required_names:
        compact["requiredNames"] = required_names
    if missing:
        compact["missingRequiredNames"] = missing
    return compact


def _store_schema_saved_result(
    result: Any,
    root: Path | None,
    schema_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Replace a saved raw schema with a hash-bound reconstructable marker."""
    compact = _record_schema(result, [], root, schema_evidence)
    digest = compact["schemaHash"]
    descriptor = schema_evidence[digest]
    raw = result.get("returnValue") if isinstance(result, dict) else result
    encoding = "utf8-string" if isinstance(raw, str) else "json"
    extras = {
        key: value for key, value in result.items() if key != "returnValue"
    } if isinstance(result, dict) else {}
    return {
        SCHEMA_SAVED_MARKER: {
            "schemaHash": digest,
            "artifactPath": descriptor["artifactPath"],
            "mimeType": descriptor["mimeType"],
            "returnValueEncoding": encoding,
            "resultWasMapping": isinstance(result, dict),
            "extras": extras,
        }
    }


def _hydrate_schema_saved(value: Any, root: Path | None) -> Any:
    """Materialize saved schema markers only for live script injection."""
    if isinstance(value, list):
        return [_hydrate_schema_saved(item, root) for item in value]
    if not isinstance(value, dict):
        return value
    marker = value.get(SCHEMA_SAVED_MARKER)
    if len(value) == 1 and isinstance(marker, dict):
        if root is None:
            raise RuntimeError("Cannot hydrate schema evidence without an artifact directory")
        digest = marker.get("schemaHash")
        artifact_path = marker.get("artifactPath")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError("Saved schema marker has an invalid SHA-256")
        if not isinstance(artifact_path, str):
            raise RuntimeError("Saved schema marker has no artifact path")
        path = Path(artifact_path).resolve()
        if not _inside(path, root):
            raise RuntimeError("Saved schema artifact escaped the configured directory")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeError(f"Saved schema artifact is corrupt: {path}")
        if marker.get("returnValueEncoding") == "utf8-string":
            raw: Any = payload.decode("utf-8")
        elif marker.get("returnValueEncoding") == "json":
            raw = json.loads(payload.decode("utf-8"))
        else:
            raise RuntimeError("Saved schema marker has an invalid encoding")
        if marker.get("resultWasMapping"):
            extras = marker.get("extras", {})
            if not isinstance(extras, dict):
                raise RuntimeError("Saved schema marker extras are invalid")
            result = {
                key: _hydrate_schema_saved(item, root)
                for key, item in extras.items()
            }
            result["returnValue"] = raw
            return result
        return raw
    return {
        key: _hydrate_schema_saved(item, root) for key, item in value.items()
    }


def _externalize_saved_schemas(
    saved: dict[str, Any],
    completed_steps: list[dict[str, Any]],
    root: Path | None,
    schema_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    producers = {
        step["saveResultAs"]: step.get("toolName")
        for step in completed_steps
        if step.get("saveResultAs")
    }
    result = dict(saved)
    for key, tool_name in producers.items():
        if tool_name != "list_properties" or key not in result:
            continue
        current = result[key]
        if isinstance(current, dict) and SCHEMA_SAVED_MARKER in current:
            continue
        result[key] = _store_schema_saved_result(
            current, root, schema_evidence
        )
    return result


def _result_summary(result: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if isinstance(result, dict):
        summary["topLevelKeys"] = sorted(str(key) for key in result)[:32]
        return_value = result.get("returnValue")
        if isinstance(return_value, bool):
            summary["returnValue"] = return_value
    elif isinstance(result, list):
        summary["itemCount"] = len(result)
    ref_paths: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if isinstance(item.get("refPath"), str):
                ref_paths.add(item["refPath"])
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(result)
    if ref_paths:
        summary["refPaths"] = sorted(ref_paths)[:32]
        if len(ref_paths) > 32:
            summary["refPathCount"] = len(ref_paths)
    return summary


def _compact_result(
    result: Any,
    root: Path | None,
    result_evidence: dict[str, dict[str, Any]],
) -> Any:
    payload = _json_bytes(result)
    if len(payload) <= MAX_INLINE_EVENT_RESULT_BYTES:
        return result
    digest = hashlib.sha256(payload).hexdigest()
    path = _write_content_addressed(root, "results", digest, ".json", payload)
    descriptor: dict[str, Any] = {
        "resultSha256": digest,
        "mimeType": "application/json",
        "byteLength": len(payload),
    }
    if path is not None:
        descriptor["artifactPath"] = str(path)
    result_evidence[digest] = dict(descriptor)
    descriptor.update(_result_summary(result))
    return descriptor


def _environment_binding(
    environment: Any,
    root: Path | None,
    environment_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payload = _json_bytes(environment)
    digest = hashlib.sha256(payload).hexdigest()
    path = _write_content_addressed(root, "environment", digest, ".json", payload)
    descriptor: dict[str, Any] = {
        "environmentSha256": digest,
        "mimeType": "application/json",
        "byteLength": len(payload),
    }
    if path is not None:
        descriptor["artifactPath"] = str(path)
    environment_evidence[digest] = dict(descriptor)
    return descriptor


def _new_evidence_metrics(
    evidence: dict[str, dict[str, dict[str, Any]]],
    baseline_keys: dict[str, set[str]],
) -> dict[str, int]:
    """Return exact byte/image proxies for evidence first seen in this run."""
    artifact_bytes = 0
    image_count = 0
    image_pixels = 0
    for category, records in evidence.items():
        prior = baseline_keys.get(category, set())
        for digest, descriptor in records.items():
            if digest in prior:
                continue
            byte_length = descriptor.get("byteLength")
            if isinstance(byte_length, int) and not isinstance(byte_length, bool) and byte_length >= 0:
                artifact_bytes += byte_length
            if category != "artifacts" or not str(descriptor.get("mimeType", "")).startswith("image/"):
                continue
            image_count += 1
            width = descriptor.get("width")
            height = descriptor.get("height")
            if (
                isinstance(width, int)
                and not isinstance(width, bool)
                and width > 0
                and isinstance(height, int)
                and not isinstance(height, bool)
                and height > 0
            ):
                image_pixels += width * height
    return {
        "artifactBytes": artifact_bytes,
        "imageCount": image_count,
        "imagePixels": image_pixels,
    }


def _checkpoint_payload(
    digest: str,
    done: int,
    saved: dict[str, Any],
    events: list[dict[str, Any]],
    status: str,
    schema_binding: dict[str, Any],
    evidence: dict[str, dict[str, dict[str, Any]]],
    artifact_root: Path | None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "formatVersion": CHECKPOINT_FORMAT_VERSION,
        "planSha256": digest,
        "completedPrefix": done,
        "saved": saved,
        "events": events,
        "status": status,
        "schemaBinding": schema_binding,
        "evidence": evidence,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if artifact_root is not None:
        payload["artifactDirectory"] = str(artifact_root)
    payload.update(extra)
    return payload


def _fail(
    path: Path | None,
    digest: str,
    done: int,
    saved: dict[str, Any],
    events: list[dict[str, Any]],
    error: str,
    chunk: list[dict[str, Any]],
    schema_binding: dict[str, Any],
    evidence: dict[str, dict[str, dict[str, Any]]],
    artifact_root: Path | None,
    checkpoint_advanced: bool = False,
    error_code: str = "execution-failed",
    public_error: str = "Execution failed; correlate with errorSha256",
) -> dict[str, Any]:
    requires_get_widgets = any(step.get("toolName") == "AddWidget" for step in chunk)
    compact_error = _compact_error(error, public_error)
    checkpoint_persisted: bool | None = None
    checkpoint_error: dict[str, Any] | None = None
    if path:
        try:
            _atomic(path, _checkpoint_payload(
                digest, done, saved, events, "failed", schema_binding, evidence,
                artifact_root,
                errorCode=error_code,
                error=compact_error["message"],
                errorSha256=compact_error["sha256"],
                errorLength=compact_error["charLength"],
                errorTruncated=compact_error["truncated"],
                requiresGetWidgets=requires_get_widgets,
            ))
            checkpoint_persisted = True
        except Exception as exc:
            checkpoint_persisted = False
            checkpoint_error = _compact_error(
                exc,
                "Checkpoint persistence failed; manual recovery is required",
            )
    result = {
        "ok": False,
        "completedPrefix": done,
        "checkpointAdvanced": checkpoint_advanced and checkpoint_persisted is not False,
        "errorCode": error_code,
        "error": compact_error["message"],
        "errorSha256": compact_error["sha256"],
        "errorLength": compact_error["charLength"],
        "errorTruncated": compact_error["truncated"],
        "requiresGetWidgets": requires_get_widgets,
        "planSha256": digest,
        "checkpointPath": str(path.resolve()) if path else None,
    }
    if checkpoint_persisted is not None:
        result["checkpointPersisted"] = checkpoint_persisted
    if checkpoint_error is not None:
        result.update({
            "checkpointErrorCode": "checkpoint-write-failed",
            "checkpointError": checkpoint_error["message"],
            "checkpointErrorSha256": checkpoint_error["sha256"],
            "manualRecoveryRequired": True,
        })
    return result


def _checkpoint_write_failure(
    path: Path,
    digest: str,
    completed_prefix: int,
    durable_prefix: int,
    error: Any,
    completed_chunk: list[dict[str, Any]],
) -> dict[str, Any]:
    compact_error = _compact_error(
        error,
        "Checkpoint persistence failed after confirmed Editor progress; "
        "manual recovery is required",
    )
    requires_get_widgets = any(
        step.get("toolName") == "AddWidget" for step in completed_chunk
    )
    return {
        "ok": False,
        "status": "checkpoint-write-failed",
        "completedPrefix": completed_prefix,
        "durableCompletedPrefix": durable_prefix,
        "checkpointAdvanced": False,
        "checkpointPersisted": False,
        "errorCode": "checkpoint-write-failed",
        "error": compact_error["message"],
        "errorSha256": compact_error["sha256"],
        "errorLength": compact_error["charLength"],
        "errorTruncated": compact_error["truncated"],
        "requiresGetWidgets": requires_get_widgets,
        "recoveryConfirmationRequired": requires_get_widgets,
        "manualRecoveryRequired": True,
        "planSha256": digest,
        "checkpointPath": str(path.resolve()),
    }


def _normalize_existing_events(
    events: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    root: Path | None,
    evidence: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    step_by_id: dict[str, tuple[int, dict[str, Any]] | None] = {}
    for global_index, step in enumerate(steps, start=1):
        step_id = step.get("stepId")
        if not isinstance(step_id, str) or not step_id:
            continue
        if step_id in step_by_id:
            step_by_id[step_id] = None
        else:
            step_by_id[step_id] = (global_index, step)
    for event in events:
        if not isinstance(event, dict):
            continue
        item = {key: value for key, value in event.items() if key != "result"}
        index = event.get("index")
        matched: tuple[int, dict[str, Any]] | None = None
        step_id = event.get("stepId")
        if isinstance(step_id, str):
            matched = step_by_id.get(step_id)
        if matched is None and isinstance(index, int) and 1 <= index <= len(steps):
            matched = (index, steps[index - 1])
        if matched is not None:
            global_index, matched_step = matched
            item["index"] = global_index
            item.setdefault(
                "tool",
                matched_step.get("toolsetName", "")
                + "."
                + matched_step.get("toolName", ""),
            )
        else:
            matched_step = None
        if "result" in event:
            result = _externalize_tree(event["result"], root, evidence["artifacts"])
            if matched_step is not None and matched_step.get("toolName") == "list_properties":
                if isinstance(result, dict) and isinstance(result.get("schemaHash"), str):
                    item["result"] = result
                else:
                    required = _required_property_names(steps, global_index - 1)
                    item["result"] = _record_schema(result, required, root, evidence["schemas"])
            else:
                item["result"] = _compact_result(result, root, evidence["results"])
        normalized.append(item)
    return normalized


def _process_chunk_response(
    result: dict[str, Any],
    chunk: list[dict[str, Any]],
    first: int,
    root: Path | None,
    evidence: dict[str, dict[str, dict[str, Any]]],
) -> tuple[bool, int, dict[str, Any], list[dict[str, Any]], str | None]:
    """Validate and compact a complete or partially completed script response."""

    succeeded = result.get("ok") is True
    completed = result.get("completed")
    if isinstance(completed, bool) or not isinstance(completed, int):
        raise RuntimeError("ProgrammaticToolset completed count is invalid")
    if completed < 0 or completed > len(chunk):
        raise RuntimeError("ProgrammaticToolset completed count is outside the chunk")
    if succeeded and completed != len(chunk):
        raise RuntimeError("ProgrammaticToolset returned an incomplete successful chunk")
    if not succeeded and completed >= len(chunk):
        raise RuntimeError("ProgrammaticToolset failed response has no failed step")

    raw_events = result.get("events", [])
    expected_event_count = completed if succeeded else completed + 1
    if not isinstance(raw_events, list) or len(raw_events) != expected_event_count:
        raise RuntimeError("ProgrammaticToolset event count does not match the chunk progress")

    completed_steps = chunk[:completed]
    raw_delta = result.get("savedDelta")
    # Accept an older server-side script response only as a compatibility
    # bridge; newly generated scripts never return the complete SAVED map.
    if raw_delta is None and isinstance(result.get("saved"), dict):
        raw_delta = {
            key: result["saved"][key]
            for key in {
                item.get("saveResultAs")
                for item in completed_steps
                if item.get("saveResultAs")
            }
            if key in result["saved"]
        }
    if not isinstance(raw_delta, dict):
        raise RuntimeError("ProgrammaticToolset savedDelta is invalid")
    expected_delta = {
        item["saveResultAs"] for item in completed_steps if item.get("saveResultAs")
    }
    if set(raw_delta) != expected_delta:
        raise RuntimeError("ProgrammaticToolset savedDelta keys do not match completed steps")

    delta = _externalize_tree(raw_delta, root, evidence["artifacts"])
    delta = _externalize_saved_schemas(
        delta, completed_steps, root, evidence["schemas"]
    )
    compact_events: list[dict[str, Any]] = []
    for offset, (raw_event, step) in enumerate(
        zip(raw_events[:completed], completed_steps)
    ):
        if not isinstance(raw_event, dict):
            raise RuntimeError("ProgrammaticToolset returned a non-object event")
        if (
            raw_event.get("status") != "completed"
            or raw_event.get("stepId") != step.get("stepId")
        ):
            raise RuntimeError("ProgrammaticToolset event identity does not match the plan")
        event_result = _externalize_tree(
            raw_event.get("result"), root, evidence["artifacts"]
        )
        if step.get("toolName") == "list_properties":
            compact = _record_schema(
                event_result,
                _required_property_names(chunk, offset),
                root,
                evidence["schemas"],
            )
        else:
            compact = _compact_result(event_result, root, evidence["results"])
        compact_events.append({
            "status": "completed",
            "index": first + offset,
            "stepId": step.get("stepId"),
            "tool": step.get("toolsetName", "") + "." + step.get("toolName", ""),
            "result": compact,
        })

    if succeeded:
        return True, completed, delta, compact_events, None

    failed_raw = raw_events[completed]
    failed_step = chunk[completed]
    if not isinstance(failed_raw, dict):
        raise RuntimeError("ProgrammaticToolset returned a non-object failed event")
    if (
        failed_raw.get("status") != "failed"
        or failed_raw.get("stepId") != failed_step.get("stepId")
    ):
        raise RuntimeError("ProgrammaticToolset failed event identity does not match the plan")
    error = str(result.get("error", failed_raw.get("error", "chunk failed")))
    compact_error = _compact_error(
        error, "ProgrammaticToolset step failed; correlate with errorSha256"
    )
    compact_events.append({
        "status": "failed",
        "index": first + completed,
        "stepId": failed_step.get("stepId"),
        "tool": failed_step.get("toolsetName", "") + "." + failed_step.get("toolName", ""),
        "errorCode": "programmatic-step-failed",
        "error": compact_error["message"],
        "errorSha256": compact_error["sha256"],
        "errorLength": compact_error["charLength"],
        "errorTruncated": compact_error["truncated"],
    })
    return False, completed, delta, compact_events, error


def run_plan(
    plan: dict[str, Any],
    client: Any,
    checkpoint: Path | None = None,
    resume_log: Path | None = None,
    run_all: bool = False,
    max_steps_per_chunk: int = 24,
    artifact_dir: Path | None = None,
    resume_after_get_widgets: bool = False,
) -> dict[str, Any]:
    """Run pending chunks and return only a compact control-plane summary."""
    digest = plan_digest(plan)
    steps = list(plan.get("steps", []))
    artifact_root = artifact_dir.resolve() if artifact_dir is not None else None
    done = 0
    saved: dict[str, Any] = {}
    state: dict[str, Any] = {}
    evidence: dict[str, dict[str, dict[str, Any]]] = {
        "schemas": {}, "artifacts": {}, "results": {}, "environments": {},
    }
    if checkpoint and checkpoint.exists() and checkpoint.stat().st_size > 0:
        state = _load(checkpoint)
        if state.get("planSha256") != digest:
            raise RuntimeError("Checkpoint plan SHA256 does not match input plan")
        format_version = state.get("formatVersion")
        if format_version is not None and format_version != CHECKPOINT_FORMAT_VERSION:
            raise RuntimeError("Checkpoint formatVersion is not supported by this executor")
        recorded_root = state.get("artifactDirectory")
        if recorded_root:
            recorded_path = Path(recorded_root)
            if not recorded_path.is_absolute():
                raise RuntimeError("Checkpoint artifact directory must be absolute")
            recorded_path = recorded_path.resolve()
            if artifact_root and recorded_path != artifact_root:
                raise RuntimeError("Checkpoint artifact directory does not match this execution")
            artifact_root = recorded_path
        done = int(state.get("completedPrefix", 0))
        if done < 0 or done > len(steps):
            raise RuntimeError("Checkpoint completedPrefix is outside the plan")
        saved = dict(state.get("saved", {}))
        raw_evidence = state.get("evidence", {})
        if isinstance(raw_evidence, dict):
            for category in evidence:
                value = raw_evidence.get(category, {})
                if isinstance(value, dict):
                    evidence[category] = dict(value)

        if (
            state.get("status") == "failed"
            and state.get("requiresGetWidgets") is True
            and not resume_after_get_widgets
        ):
            return {
                "ok": False,
                "completedPrefix": done,
                "checkpointAdvanced": False,
                "error": (
                    "Checkpoint resume is blocked until GetWidgets recovery "
                    "has been completed and explicitly confirmed"
                ),
                "requiresGetWidgets": True,
                "recoveryConfirmationRequired": True,
                "planSha256": digest,
                "checkpointPath": str(checkpoint.resolve()),
            }

    evidence_baseline_keys = {
        category: set(records) for category, records in evidence.items()
    }

    if artifact_root is None:
        artifact_root = _artifact_root(checkpoint, None)

    # Externalize payloads from legacy checkpoints or sequential logs before
    # they can be persisted again or injected into another program.
    saved = _externalize_tree(saved, artifact_root, evidence["artifacts"])
    events = _normalize_existing_events(
        list(state.get("events", [])), steps, artifact_root, evidence
    )

    log_done, log_saved = _resume(plan, resume_log)
    if log_done > done:
        done = log_done
        saved = _externalize_tree(log_saved, artifact_root, evidence["artifacts"])
    saved = _externalize_saved_schemas(
        saved, steps[:done], artifact_root, evidence["schemas"]
    )

    # Validate the current restricted environment before any execution call.
    environment = client.call_tool(ENV, {})
    schema_binding = _environment_binding(
        environment, artifact_root, evidence["environments"]
    )

    chunks = chunk_steps(steps, max_steps_per_chunk)
    indexed: list[tuple[int, int, list[dict[str, Any]]]] = []
    current = 0
    for chunk in chunks:
        indexed.append((current + 1, current + len(chunk), chunk))
        current += len(chunk)
    pending: list[tuple[int, int, list[dict[str, Any]]]] = []
    for first, last, chunk in indexed:
        if last <= done:
            continue
        if first <= done:
            offset = done - first + 1
            chunk = chunk[offset:]
            first = done + 1
        pending.append((first, last, chunk))
    if not run_all:
        pending = pending[:1]

    # Only retain saved results needed by the still-pending plan suffix.
    try:
        saved = live_saved_for_steps(
            steps[done:], saved, validate_paths=False
        ) if done < len(steps) else {}
    except (KeyError, ValueError) as exc:
        next_chunk = pending[0][2] if pending else []
        return _fail(
            checkpoint, digest, done, saved, events, str(exc), next_chunk,
            schema_binding, evidence, artifact_root,
            error_code="saved-reference-invalid",
            public_error="Saved-result reference validation failed",
        )

    if artifact_root is None and any(
        step.get("toolName") == "list_properties"
        for step in steps[done:]
    ):
        return _fail(
            checkpoint,
            digest,
            done,
            saved,
            events,
            "list_properties execution requires --artifact-dir or --checkpoint",
            pending[0][2] if pending else [],
            schema_binding,
            evidence,
            artifact_root,
            error_code="artifact-directory-required",
            public_error="Schema evidence requires a checkpoint or artifact directory",
        )

    executed_chunks = 0
    checkpoint_advanced = False
    checkpoint_rewritten = False
    for first, last, chunk in pending:
        durable_done_before_chunk = done
        try:
            _validate_artifact_descriptors(saved, artifact_root)
            script_saved = _hydrate_schema_saved(saved, artifact_root)
            script = build_programmatic_script(plan, chunk, script_saved)
        except (OSError, KeyError, RuntimeError, ValueError) as exc:
            return _fail(
                checkpoint, digest, done, saved, events, str(exc), chunk,
                schema_binding, evidence, artifact_root,
                error_code="saved-input-invalid",
                public_error="Saved-result input validation failed before Editor execution",
            )
        try:
            result = client.call_tool(EXEC, {"script": script})
        except Exception as exc:
            return _fail(
                checkpoint, digest, done, saved, events, str(exc), chunk,
                schema_binding, evidence, artifact_root,
                error_code="mcp-call-failed",
                public_error="MCP execution failed before a valid chunk response was returned",
            )
        if not isinstance(result, dict):
            return _fail(
                checkpoint, digest, done, saved, events,
                "ProgrammaticToolset returned a non-object response", chunk,
                schema_binding, evidence, artifact_root,
                error_code="programmatic-response-invalid",
                public_error="ProgrammaticToolset returned an invalid response",
            )
        try:
            succeeded, completed, delta, chunk_events, chunk_error = (
                _process_chunk_response(
                    result, chunk, first, artifact_root, evidence
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            original_error = result.get("error")
            error = (
                str(original_error) + "; invalid partial progress: " + str(exc)
                if original_error is not None
                else str(exc)
            )
            return _fail(
                checkpoint, digest, done, saved, events, error, chunk,
                schema_binding, evidence, artifact_root,
                error_code="programmatic-response-invalid",
                public_error="ProgrammaticToolset returned invalid partial progress",
            )

        saved.update(delta)
        events.extend(chunk_events)
        if not succeeded:
            done += completed
            try:
                saved = live_saved_for_steps(
                    steps[done:], saved, validate_paths=False
                ) if done < len(steps) else {}
            except (KeyError, ValueError) as exc:
                return _fail(
                    checkpoint, digest, done, saved, events, str(exc),
                    chunk[:completed + 1], schema_binding, evidence,
                    artifact_root, checkpoint_advanced=completed > 0,
                    error_code="saved-reference-invalid",
                    public_error="Saved-result reference validation failed",
                )
            return _fail(
                checkpoint,
                digest,
                done,
                saved,
                events,
                chunk_error or "chunk failed",
                chunk[:completed + 1],
                schema_binding,
                evidence,
                artifact_root,
                checkpoint_advanced=completed > 0,
                error_code="programmatic-step-failed",
                public_error="ProgrammaticToolset step failed; correlate with errorSha256",
            )

        done = last
        executed_chunks += 1
        checkpoint_advanced = True
        try:
            saved = live_saved_for_steps(
                steps[done:], saved, validate_paths=False
            ) if done < len(steps) else {}
        except (KeyError, ValueError) as exc:
            return _fail(
                checkpoint, digest, done, saved, events, str(exc), chunk,
                schema_binding, evidence, artifact_root,
                error_code="saved-reference-invalid",
                public_error="Saved-result reference validation failed",
            )
        if checkpoint:
            try:
                _atomic(checkpoint, _checkpoint_payload(
                    digest,
                    done,
                    saved,
                    events,
                    "completed" if done == len(steps) else "checkpoint",
                    schema_binding,
                    evidence,
                    artifact_root,
                ))
            except Exception as exc:
                return _checkpoint_write_failure(
                    checkpoint,
                    digest,
                    done,
                    durable_done_before_chunk,
                    exc,
                    chunk,
                )

    # A completed legacy checkpoint may have no pending chunk. Persist the
    # normalized v2 events/evidence anyway so raw schemas or base64 do not stay
    # on disk after a successful migration-only invocation.
    if checkpoint and not checkpoint_advanced:
        try:
            _atomic(checkpoint, _checkpoint_payload(
                digest,
                done,
                saved,
                events,
                "completed" if done == len(steps) else "checkpoint",
                schema_binding,
                evidence,
                artifact_root,
            ))
        except Exception as exc:
            return _checkpoint_write_failure(
                checkpoint, digest, done, done, exc, []
            )
        checkpoint_rewritten = True

    return {
        "ok": True,
        "completedPrefix": done,
        "totalSteps": len(steps),
        "status": "completed" if done == len(steps) else "checkpoint",
        "checkpointAdvanced": checkpoint_advanced,
        "checkpointRewritten": checkpoint_rewritten,
        "chunksExecuted": executed_chunks,
        "planSha256": digest,
        "checkpointPath": str(checkpoint.resolve()) if checkpoint else None,
        "artifactDirectory": str(artifact_root) if artifact_root else None,
        "liveSavedKeys": sorted(saved),
        "eventCount": len(events),
        "evidenceCounts": {key: len(value) for key, value in evidence.items()},
        "telemetryMetrics": _new_evidence_metrics(
            evidence, evidence_baseline_keys
        ),
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
                digest = hashlib.sha256(wrapped.encode("utf-8")).hexdigest()
                raise RuntimeError(
                    "ProgrammaticToolset returned invalid JSON in returnValue "
                    f"(sha256={digest}, chars={len(wrapped)})"
                ) from exc
        return result


def _json_size(value: Any) -> int | None:
    try:
        return len(_json_bytes(value))
    except (TypeError, ValueError):
        return None


class _MeasuredClient:
    """Measure the logical MCP boundary without retaining either payload."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.tool_call_count = 0
        self.tool_input_bytes = 0
        self.tool_output_bytes = 0
        self.tool_input_complete = True
        self.tool_output_complete = True

    def call_tool(self, full_name: str, arguments: dict[str, Any]) -> Any:
        self.tool_call_count += 1
        input_bytes = _json_size(arguments)
        if input_bytes is None:
            self.tool_input_complete = False
        else:
            self.tool_input_bytes += input_bytes
        try:
            result = self.client.call_tool(full_name, arguments)
        except Exception:
            self.tool_output_complete = False
            raise
        output_bytes = _json_size(result)
        if output_bytes is None:
            self.tool_output_complete = False
        else:
            self.tool_output_bytes += output_bytes
        return result

    def metrics(self) -> dict[str, int]:
        result = {"toolCallCount": self.tool_call_count}
        if self.tool_input_complete:
            result["toolInputBytes"] = self.tool_input_bytes
        if self.tool_output_complete:
            result["toolOutputBytes"] = self.tool_output_bytes
        return result


def _load_token_telemetry_module() -> Any:
    module_path = Path(__file__).resolve().parents[3] / "scripts" / "token_telemetry.py"
    spec = importlib.util.spec_from_file_location(
        "nextgame_ui_token_telemetry", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("NextGame UI token telemetry module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _append_execution_telemetry(
    module: Any,
    ledger: Path,
    request_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    raw_metrics = result.get("telemetryMetrics", {})
    metrics = {
        key: value
        for key, value in raw_metrics.items()
        if key in {
            "toolInputBytes",
            "toolOutputBytes",
            "artifactBytes",
            "imageCount",
            "imagePixels",
            "toolCallCount",
        }
    } if isinstance(raw_metrics, dict) else {}
    if not result.get("ok"):
        operation = "execute-plan-failed"
    elif result.get("status") == "completed":
        operation = "execute-plan-completed"
    else:
        operation = "execute-plan-checkpoint"
    event = module.make_local_operation_event(
        request_id,
        "umg-build",
        operation,
        **metrics,
    )
    return module.append_event(ledger, event)


def _run_cli(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.request_id and not args.telemetry_ledger:
        parser.error("--request-id requires --telemetry-ledger")

    plan = _load(args.plan)
    telemetry_module = None
    request_id = args.request_id or ("plan-" + plan_digest(plan)[:16])
    if args.telemetry_ledger:
        try:
            telemetry_module = _load_token_telemetry_module()
            telemetry_module.validate_ledger_location(args.telemetry_ledger)
            telemetry_module.make_local_operation_event(
                request_id, "umg-build", "execute-plan", toolCallCount=0
            )
        except Exception as exc:
            parser.error(
                "invalid telemetry configuration (" + type(exc).__name__ + ")"
            )

    measured_client: _MeasuredClient | None = None
    try:
        measured_client = _MeasuredClient(_CliClient(args.mcp_url, args.timeout))
        result = run_plan(
            plan,
            measured_client,
            args.checkpoint,
            args.resume_log,
            args.run_all,
            args.max_steps_per_chunk,
            args.artifact_dir,
            args.resume_after_get_widgets,
        )
    except Exception as exc:
        compact_error = _compact_error(
            exc, "Executor failed before a structured plan result was available"
        )
        result = {
            "ok": False,
            "status": "failed",
            "stage": "execution",
            "errorCode": "execution-exception",
            "errorType": type(exc).__name__,
            "error": compact_error["message"],
            "errorSha256": compact_error["sha256"],
            "errorLength": compact_error["charLength"],
            "errorTruncated": compact_error["truncated"],
        }
    artifact_metrics = result.get("telemetryMetrics", {})
    # A client-construction failure is still a measured zero-call attempt.
    # If a call itself failed, _MeasuredClient deliberately omits the unknown
    # output byte metric so budget coverage remains fail-closed.
    metrics = (
        measured_client.metrics()
        if measured_client is not None
        else {"toolCallCount": 0}
    )
    if isinstance(artifact_metrics, dict):
        metrics.update(artifact_metrics)
    result["telemetryMetrics"] = metrics

    telemetry_failed = False
    if args.telemetry_ledger and telemetry_module is not None:
        try:
            result["telemetry"] = _append_execution_telemetry(
                telemetry_module,
                args.telemetry_ledger,
                request_id,
                result,
            )
        except Exception as exc:
            telemetry_failed = True
            result["telemetry"] = {
                "ok": False,
                "errorCode": "telemetry-write-failed",
                "errorType": type(exc).__name__,
            }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    if not result.get("ok"):
        return 1
    return 2 if telemetry_failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--resume-log", type=Path)
    parser.add_argument(
        "--resume-after-get-widgets",
        action="store_true",
        help="Confirm required GetWidgets recovery before resuming a failed AddWidget chunk.",
    )
    parser.add_argument("--run-all", action="store_true")
    parser.add_argument("--max-steps-per-chunk", type=int, default=24)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--telemetry-ledger",
        type=Path,
        help="Append privacy-bounded token/proxy metrics to this JSON ledger.",
    )
    parser.add_argument(
        "--request-id",
        help="Opaque telemetry request identifier; defaults to the plan digest.",
    )
    args = parser.parse_args()
    try:
        return _run_cli(args, parser)
    except SystemExit:
        raise
    except Exception as exc:
        compact_error = _compact_error(exc)
        result = {
            "ok": False,
            "stage": "startup",
            "errorCode": "startup-failed",
            "errorType": type(exc).__name__,
            "error": compact_error["message"],
            "errorSha256": compact_error["sha256"],
            "errorLength": compact_error["charLength"],
            "errorTruncated": compact_error["truncated"],
        }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    sys.exit(main())
