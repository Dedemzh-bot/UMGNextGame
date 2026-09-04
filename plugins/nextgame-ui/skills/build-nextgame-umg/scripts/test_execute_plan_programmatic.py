import base64
import contextlib
import io
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("execute_plan_programmatic.py")
SPEC = importlib.util.spec_from_file_location("execute_plan_programmatic_under_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


@contextlib.contextmanager
def workspace_temp_directory():
    base = Path.cwd() / "Saved" / "CodexTestExecutor"
    base.mkdir(parents=True, exist_ok=True)
    directory = base / uuid.uuid4().hex
    directory.mkdir()
    try:
        yield str(directory)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
        try:
            base.rmdir()
        except OSError:
            pass


def make_step(step_id, tool, arguments=None, save=None):
    step = {
        "stepId": step_id,
        "toolsetName": "UMGToolSet.UMGToolSet",
        "toolName": tool,
        "arguments": arguments or {},
    }
    if save:
        step["saveResultAs"] = save
    return step


def make_plan(steps):
    return {"assetPath": "/Game/UI/UMG/Role/umg_role", "steps": steps}


class ScriptClient:
    """A fake restricted executor that evaluates generated scripts locally."""

    def __init__(self, responder=None):
        self.calls = []
        self.tool_calls = []
        self.responder = responder

    def call_tool(self, full_name, arguments):
        self.calls.append((full_name, arguments))
        if full_name == MODULE.ENV:
            return {"environment": "fake", "registeredToolCount": 5}
        if full_name != MODULE.EXEC:
            raise AssertionError(full_name)
        tool_calls = []

        def execute_tool(name, raw_arguments):
            parsed = json.loads(raw_arguments)
            tool_calls.append((name, parsed))
            self.tool_calls.append((name, parsed))
            if self.responder is not None:
                response = self.responder(name, parsed)
                if response is not None:
                    return response
            if name.endswith(".Create"):
                return {"returnValue": {"refPath": "/Game/UI/UMG/Role/umg_role"}}
            if name.endswith(".AddWidget"):
                return {"returnValue": {"refPath": "/Game/UI/UMG/Role/Child"}}
            return {"returnValue": True}

        namespace = {"execute_tool": execute_tool}
        exec(arguments["script"], namespace)
        result = namespace["run"]()
        result["toolCalls"] = tool_calls
        return result


class ProgrammaticExecutorTests(unittest.TestCase):
    def test_generated_script_executes_once_serialized_json(self):
        plan = make_plan([
            make_step("create", "Create", save="blueprint"),
            make_step("add", "AddWidget", {"parent": "${blueprint.returnValue.refPath}"}, "child"),
        ])
        script = MODULE.build_programmatic_script(plan, plan["steps"], {})
        self.assertIn("PLAN=json.loads('", script)
        self.assertNotIn("PLAN=json.loads(\"'", script)
        client = ScriptClient()
        result = client.call_tool(MODULE.EXEC, {"script": script})
        self.assertTrue(result["ok"])
        self.assertNotIn("saved", result)
        self.assertEqual(set(result["savedDelta"]), {"blueprint", "child"})
        self.assertEqual(result["toolCalls"][1][1]["parent"], "/Game/UI/UMG/Role/umg_role")

    def test_resume_partial_sequential_log_restores_saved_reference(self):
        plan = make_plan([
            make_step("create", "Create", save="blueprint"),
            make_step("add", "AddWidget", {"parent": "${blueprint.returnValue.refPath}"}, "child"),
        ])
        with workspace_temp_directory() as directory:
            root = Path(directory)
            log = root / "sequential.json"
            checkpoint = root / "checkpoint.json"
            log.write_text(json.dumps([
                {"status": "completed", "index": 1, "stepId": "create",
                 "result": {"returnValue": {"refPath": "/Game/UI/UMG/Role/umg_role"}}},
            ]), encoding="utf-8")
            client = ScriptClient()
            result = MODULE.run_plan(
                plan, client, checkpoint=checkpoint, resume_log=log,
                run_all=True, max_steps_per_chunk=1,
            )
            self.assertTrue(result["ok"])
            execute_call = [call for call in client.calls if call[0] == MODULE.EXEC][0]
            self.assertNotIn('"stepId":"create"', execute_call[1]["script"])
            self.assertIn('"stepId":"add"', execute_call[1]["script"])
            add_calls = [item for item in client.tool_calls if item[0].endswith(".AddWidget")]
            self.assertEqual(add_calls[0][1]["parent"], "/Game/UI/UMG/Role/umg_role")
            self.assertNotIn("saved", result)
            self.assertNotIn("events", result)
            self.assertEqual(json.loads(checkpoint.read_text(encoding="utf-8"))["saved"], {})

    def test_chunking_never_splits_property_triplet(self):
        steps = [
            make_step("add-one", "AddWidget"),
            make_step("list-one", "list_properties"),
            make_step("get-one", "get_properties"),
            make_step("set-one", "set_properties"),
            make_step("add-two", "AddWidget"),
            make_step("list-two", "list_properties"),
            make_step("get-two", "get_properties"),
            make_step("set-two", "set_properties"),
        ]
        chunks = MODULE.chunk_steps(steps, 1)
        self.assertEqual([[item["stepId"] for item in chunk] for chunk in chunks], [
            ["add-one", "list-one", "get-one", "set-one"],
            ["add-two", "list-two", "get-two", "set-two"],
        ])

    def test_cli_client_parses_nonempty_wrapped_return_value(self):
        client = object.__new__(MODULE._CliClient)

        class WrappedClient:
            def call_tool(self, toolset, tool, arguments):
                self.last = (toolset, tool, arguments)
                return {"returnValue": '{"ok":true,"completed":1}'}

        client.client = WrappedClient()
        result = client.call_tool(MODULE.EXEC, {"script": "ignored"})
        self.assertEqual(result, {"ok": True, "completed": 1})

    def test_only_live_saved_inputs_are_injected_and_only_delta_is_returned(self):
        plan = make_plan([
            make_step("inspect", "Inspect", {"instance": "${needed.returnValue.refPath}"}),
        ])
        saved = {
            "needed": {"returnValue": {"refPath": "/Game/Needed"}},
            "dead": {"payload": "do-not-inject-" + "x" * 20000},
        }
        script = MODULE.build_programmatic_script(plan, plan["steps"], saved)
        self.assertIn("/Game/Needed", script)
        self.assertNotIn("do-not-inject", script)
        result = ScriptClient().call_tool(MODULE.EXEC, {"script": script})
        self.assertTrue(result["ok"])
        self.assertEqual(result["savedDelta"], {})
        self.assertNotIn("saved", result)

    def test_unresolved_and_partial_tokens_fail_before_execute_call(self):
        unknown_plan = make_plan([
            make_step("bad", "Inspect", {"instance": "${missing.returnValue.refPath}"}),
        ])
        with self.assertRaises(KeyError):
            MODULE.build_programmatic_script(unknown_plan, unknown_plan["steps"], {})
        partial_plan = make_plan([
            make_step("bad", "Inspect", {"instance": "prefix-${known.returnValue}"}),
        ])
        with self.assertRaises(ValueError):
            MODULE.build_programmatic_script(
                partial_plan, partial_plan["steps"], {"known": {"returnValue": True}}
            )
        client = ScriptClient()
        result = MODULE.run_plan(unknown_plan, client, run_all=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "saved-reference-invalid")
        self.assertEqual([name for name, _ in client.calls], [MODULE.ENV])

    def test_future_checkpoint_format_is_rejected_before_editor_call(self):
        plan = make_plan([make_step("finish", "Finish")])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "future.json"
            checkpoint.write_text(json.dumps({
                "formatVersion": MODULE.CHECKPOINT_FORMAT_VERSION + 1,
                "planSha256": MODULE.plan_digest(plan),
                "completedPrefix": 0,
                "saved": {},
                "events": [],
                "status": "checkpoint",
            }), encoding="utf-8")
            client = ScriptClient()
            with self.assertRaisesRegex(RuntimeError, "formatVersion"):
                MODULE.run_plan(plan, client, checkpoint=checkpoint)
            self.assertEqual(client.calls, [])

    def test_schema_evidence_is_deduplicated_and_checkpoint_is_compact(self):
        sentinel = "RAW-SCHEMA-SENTINEL-" + "z" * 10000
        schema_raw = json.dumps({"alpha": {}, "beta": {}, "documentation": sentinel})

        def responder(name, _arguments):
            if name.endswith(".list_properties"):
                return {"returnValue": schema_raw}
            if name.endswith(".get_properties"):
                return {"returnValue": {"alpha": 1, "beta": 2}}
            return None

        steps = [
            make_step("add-one", "AddWidget"),
            make_step("list-one", "list_properties"),
            make_step("get-one", "get_properties", {"properties": ["alpha"]}),
            make_step("set-one", "set_properties", {"values": {"alpha": 3}}),
            make_step("add-two", "AddWidget"),
            make_step("list-two", "list_properties"),
            make_step("get-two", "get_properties", {"properties": ["beta"]}),
            make_step("set-two", "set_properties", {"values": {"beta": 4}}),
        ]
        with workspace_temp_directory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoint.json"
            artifacts = root / "evidence"
            result = MODULE.run_plan(
                make_plan(steps), ScriptClient(responder), checkpoint=checkpoint,
                artifact_dir=artifacts, run_all=True, max_steps_per_chunk=1,
            )
            self.assertTrue(result["ok"])
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            schema_evidence = state["evidence"]["schemas"]
            self.assertEqual(len(schema_evidence), 1)
            schema_events = [
                event for event in state["events"]
                if event["stepId"].startswith("list-")
            ]
            self.assertEqual(len(schema_events), 2)
            self.assertEqual(
                schema_events[0]["result"]["schemaHash"],
                schema_events[1]["result"]["schemaHash"],
            )
            self.assertTrue(all(event["result"]["requiredNamesPresent"] for event in schema_events))
            checkpoint_text = checkpoint.read_text(encoding="utf-8")
            self.assertNotIn(sentinel, checkpoint_text)
            schema_files = list((artifacts / "schemas").glob("*"))
            self.assertEqual(len(schema_files), 1)
            self.assertEqual(schema_files[0].read_text(encoding="utf-8"), schema_raw)

    def test_schema_guard_blocks_missing_property_before_get_or_set(self):
        schema_raw = json.dumps({"alpha": {"type": "number"}})

        def responder(name, _arguments):
            if name.endswith(".list_properties"):
                return {"returnValue": schema_raw}
            return None

        plan = make_plan([
            make_step("list", "list_properties"),
            make_step("get", "get_properties", {"properties": ["missing"]}),
            make_step("set", "set_properties", {"values": {"missing": 1}}),
        ])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            client = ScriptClient(responder)
            result = MODULE.run_plan(
                plan, client, checkpoint=checkpoint, run_all=True
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["completedPrefix"], 0)
            self.assertEqual(result["errorCode"], "programmatic-step-failed")
            self.assertEqual(
                [name.rsplit(".", 1)[-1] for name, _ in client.tool_calls],
                ["list_properties"],
            )
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(state["completedPrefix"], 0)
            self.assertEqual(state["events"][-1]["status"], "failed")

    def test_schema_guard_accepts_properties_wrapper_without_type(self):
        schema_raw = json.dumps({"properties": {"alpha": {"type": "number"}}})

        def responder(name, _arguments):
            if name.endswith(".list_properties"):
                return {"returnValue": schema_raw}
            return None

        plan = make_plan([
            make_step("list", "list_properties"),
            make_step("get", "get_properties", {"properties": ["alpha"]}),
        ])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            client = ScriptClient(responder)
            result = MODULE.run_plan(
                plan, client, checkpoint=checkpoint, run_all=True
            )
            self.assertTrue(result["ok"])
            self.assertEqual(
                [name.rsplit(".", 1)[-1] for name, _ in client.tool_calls],
                ["list_properties", "get_properties"],
            )

    def test_schema_plan_without_evidence_directory_fails_before_editor_call(self):
        plan = make_plan([make_step("list", "list_properties")])
        client = ScriptClient()
        result = MODULE.run_plan(plan, client, run_all=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "artifact-directory-required")
        self.assertEqual([name for name, _ in client.calls], [MODULE.ENV])

    def test_saved_schema_is_externalized_and_hydrated_for_resume(self):
        schema_raw = json.dumps({"alpha": {"type": "float"}, "padding": "q" * 8000})

        def responder(name, arguments):
            if name.endswith(".list_properties"):
                return {"returnValue": schema_raw}
            if name.endswith(".Inspect"):
                self.assertEqual(arguments["schema"], schema_raw)
            return None

        plan = make_plan([
            make_step("list", "list_properties", save="schema"),
            make_step("add-boundary", "AddWidget", {"schema": "${schema.returnValue}"}),
        ])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            client = ScriptClient(responder)
            first = MODULE.run_plan(
                plan, client, checkpoint=checkpoint, run_all=False,
                max_steps_per_chunk=1,
            )
            self.assertTrue(first["ok"])
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertIn(MODULE.SCHEMA_SAVED_MARKER, state["saved"]["schema"])
            self.assertNotIn("q" * 100, checkpoint.read_text(encoding="utf-8"))
            resumed = MODULE.run_plan(
                plan, client, checkpoint=checkpoint, run_all=True,
                max_steps_per_chunk=1,
            )
            self.assertTrue(resumed["ok"])
            add_call = [item for item in client.tool_calls if item[0].endswith(".AddWidget")][-1]
            self.assertEqual(add_call[1]["schema"], schema_raw)

    def test_png_payload_is_assetized_and_never_reaches_checkpoint_or_result(self):
        png_base64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
            "x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
        )

        def responder(name, _arguments):
            if name.endswith(".Capture"):
                return {
                    "returnValue": {
                        "type": "image", "mimeType": "image/png", "data": png_base64,
                    }
                }
            return None

        plan = make_plan([
            make_step("capture", "Capture", save="preview"),
            make_step("add", "AddWidget", {"previewPath": "${preview.returnValue.artifactPath}"}),
        ])
        with workspace_temp_directory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoint.json"
            artifacts = root / "artifacts"
            client = ScriptClient(responder)
            first = MODULE.run_plan(
                plan, client, checkpoint=checkpoint, artifact_dir=artifacts,
                run_all=False, max_steps_per_chunk=1,
            )
            self.assertTrue(first["ok"])
            self.assertLess(len(json.dumps(first)), 2000)
            self.assertNotIn("saved", first)
            self.assertNotIn("events", first)
            self.assertEqual(first["telemetryMetrics"]["imageCount"], 1)
            self.assertEqual(first["telemetryMetrics"]["imagePixels"], 1)
            self.assertGreaterEqual(
                first["telemetryMetrics"]["artifactBytes"],
                len(base64.b64decode(png_base64)),
            )
            checkpoint_text = checkpoint.read_text(encoding="utf-8")
            self.assertNotIn(png_base64, checkpoint_text)
            self.assertNotIn('"data":', checkpoint_text)
            state = json.loads(checkpoint_text)
            descriptor = state["saved"]["preview"]["returnValue"]
            self.assertEqual((descriptor["width"], descriptor["height"]), (1, 1))
            self.assertEqual(descriptor["mimeType"], "image/png")
            image_path = Path(descriptor["artifactPath"])
            self.assertTrue(image_path.is_file())
            self.assertTrue(image_path.read_bytes().startswith(b"\x89PNG"))
            self.assertEqual(
                descriptor["sha256"],
                __import__("hashlib").sha256(base64.b64decode(png_base64)).hexdigest(),
            )
            resumed = MODULE.run_plan(
                plan, client, checkpoint=checkpoint, artifact_dir=artifacts,
                run_all=True, max_steps_per_chunk=1,
            )
            self.assertTrue(resumed["ok"])
            self.assertEqual(resumed["telemetryMetrics"]["imageCount"], 0)
            add_call = [item for item in client.tool_calls if item[0].endswith(".AddWidget")][-1]
            self.assertEqual(add_call[1]["previewPath"], str(image_path))
            self.assertEqual(json.loads(checkpoint.read_text(encoding="utf-8"))["saved"], {})

    def test_whole_externalized_saved_payload_fails_closed_before_editor_call(self):
        png_base64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
            "x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
        )

        def responder(name, _arguments):
            if name.endswith(".Capture"):
                return {"returnValue": {
                    "type": "image", "mimeType": "image/png", "data": png_base64,
                }}
            return None

        plan = make_plan([
            make_step("capture", "Capture", save="preview"),
            make_step("add", "AddWidget", {"preview": "${preview}"}),
        ])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            first_client = ScriptClient(responder)
            first = MODULE.run_plan(
                plan, first_client, checkpoint=checkpoint,
                run_all=False, max_steps_per_chunk=1,
            )
            self.assertTrue(first["ok"])
            second_client = ScriptClient(responder)
            second = MODULE.run_plan(
                plan, second_client, checkpoint=checkpoint,
                run_all=True, max_steps_per_chunk=1,
            )
            self.assertFalse(second["ok"])
            self.assertEqual(second["errorCode"], "saved-input-invalid")
            self.assertEqual([name for name, _ in second_client.calls], [MODULE.ENV])
            self.assertNotIn(png_base64, checkpoint.read_text(encoding="utf-8"))

    def test_image_dimensions_are_bounded_before_artifact_write(self):
        with workspace_temp_directory() as directory:
            root = Path(directory) / "artifacts"
            cases = ((0, 1), (0xFFFFFFFF, 0xFFFFFFFF))
            for width, height in cases:
                with self.subTest(width=width, height=height):
                    header = (
                        b"\x89PNG\r\n\x1a\n"
                        + b"\x00\x00\x00\rIHDR"
                        + width.to_bytes(4, "big")
                        + height.to_bytes(4, "big")
                    )
                    value = {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": base64.b64encode(header).decode("ascii"),
                    }
                    with self.assertRaisesRegex(RuntimeError, "dimensions"):
                        MODULE._externalize_tree(value, root, {})
            self.assertFalse(root.exists())

    def test_image_descriptor_shape_is_stable_when_content_has_metadata(self):
        png_base64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
            "x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
        )
        with workspace_temp_directory() as directory:
            result = MODULE._externalize_tree(
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": png_base64,
                    "annotations": {"audience": ["assistant"]},
                    "_meta": {"codex/imageDetail": "original"},
                },
                Path(directory) / "artifacts",
                {},
            )
            self.assertIn("artifactPath", result)
            self.assertIn("sha256", result)
            self.assertNotIn("artifact", result)
            self.assertEqual(
                result["sourceMetadata"]["annotations"]["audience"],
                ["assistant"],
            )
            self.assertEqual(
                result["sourceMetadata"]["_meta"]["codex/imageDetail"],
                "original",
            )

    def test_nested_metadata_artifact_is_hash_checked(self):
        png_base64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
            "x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
        )
        gif_base64 = base64.b64encode(
            b"GIF89a\x01\x00\x01\x00"
        ).decode("ascii")
        with workspace_temp_directory() as directory:
            root = Path(directory) / "artifacts"
            result = MODULE._externalize_tree(
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": png_base64,
                    "_meta": {
                        "nested": {
                            "type": "image",
                            "mimeType": "image/gif",
                            "data": gif_base64,
                        }
                    },
                },
                root,
                {},
            )
            nested = result["sourceMetadata"]["_meta"]["nested"]
            Path(nested["artifactPath"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "corrupt"):
                MODULE._validate_artifact_descriptors(result, root)

    def test_corrupt_saved_artifact_fails_hash_check_before_editor_call(self):
        png_base64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
            "x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
        )

        def responder(name, _arguments):
            if name.endswith(".Capture"):
                return {"returnValue": {
                    "type": "image", "mimeType": "image/png", "data": png_base64,
                }}
            return None

        plan = make_plan([
            make_step("capture", "Capture", save="preview"),
            make_step("add", "AddWidget", {"previewPath": "${preview.returnValue.artifactPath}"}),
        ])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            first = MODULE.run_plan(
                plan, ScriptClient(responder), checkpoint=checkpoint,
                run_all=False, max_steps_per_chunk=1,
            )
            self.assertTrue(first["ok"])
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            image_path = Path(state["saved"]["preview"]["returnValue"]["artifactPath"])
            image_path.write_bytes(b"tampered")
            second_client = ScriptClient(responder)
            second = MODULE.run_plan(
                plan, second_client, checkpoint=checkpoint,
                run_all=True, max_steps_per_chunk=1,
            )
            self.assertFalse(second["ok"])
            self.assertEqual(second["errorCode"], "saved-input-invalid")
            self.assertEqual([name for name, _ in second_client.calls], [MODULE.ENV])

    def test_completed_legacy_checkpoint_is_rewritten_without_raw_payloads(self):
        png_base64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
            "x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
        )
        schema_sentinel = "LEGACY-RAW-SCHEMA-" + "s" * 6000
        schema_raw = json.dumps({"alpha": {}, "docs": schema_sentinel})
        plan = make_plan([
            make_step("capture", "Capture", save="preview"),
            make_step("list", "list_properties", save="schema"),
        ])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "planSha256": MODULE.plan_digest(plan),
                "completedPrefix": 2,
                "saved": {
                    "preview": {"returnValue": {
                        "type": "image", "mimeType": "image/png", "data": png_base64,
                    }},
                    "schema": {"returnValue": schema_raw},
                },
                "events": [
                    {"status": "completed", "index": 1, "stepId": "capture",
                     "result": {"returnValue": {
                         "type": "image", "mimeType": "image/png", "data": png_base64,
                     }}},
                    {"status": "completed", "index": 2, "stepId": "list",
                     "result": {"returnValue": schema_raw}},
                ],
                "status": "completed",
            }), encoding="utf-8")
            client = ScriptClient()
            result = MODULE.run_plan(plan, client, checkpoint=checkpoint, run_all=True)
            self.assertTrue(result["ok"])
            self.assertTrue(result["checkpointRewritten"])
            self.assertEqual([name for name, _ in client.calls], [MODULE.ENV])
            rewritten = checkpoint.read_text(encoding="utf-8")
            self.assertNotIn(png_base64, rewritten)
            self.assertNotIn(schema_sentinel, rewritten)
            state = json.loads(rewritten)
            self.assertEqual(state["formatVersion"], 2)
            self.assertEqual(state["saved"], {})
            self.assertEqual(len(state["evidence"]["schemas"]), 1)
            self.assertEqual(len(state["evidence"]["artifacts"]), 1)

    def test_legacy_chunk_local_event_index_uses_step_id_for_schema_migration(self):
        schema_sentinel = "LEGACY-CHUNK-SCHEMA-" + "s" * 6000
        schema_raw = json.dumps({"visibility": {}, "sentinel": schema_sentinel})
        plan = make_plan([
            make_step("create", "Create"),
            make_step("add-boundary", "AddWidget"),
            make_step("list-later", "list_properties"),
            make_step("get-later", "get_properties", {"properties": ["visibility"]}),
            make_step("set-later", "set_properties", {"values": {"visibility": "Visible"}}),
        ])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "legacy.json"
            checkpoint.write_text(json.dumps({
                "planSha256": MODULE.plan_digest(plan),
                "completedPrefix": len(plan["steps"]),
                "saved": {},
                "events": [{
                    "status": "completed",
                    # Old generated scripts restarted event indices per chunk.
                    "index": 2,
                    "stepId": "list-later",
                    "result": {"returnValue": schema_raw},
                }],
                "status": "completed",
            }), encoding="utf-8")
            result = MODULE.run_plan(
                plan, ScriptClient(), checkpoint=checkpoint, run_all=True
            )
            self.assertTrue(result["ok"])
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertNotIn(schema_sentinel, checkpoint.read_text(encoding="utf-8"))
            event = state["events"][0]
            self.assertEqual(event["index"], 3)
            self.assertEqual(event["tool"], "UMGToolSet.UMGToolSet.list_properties")
            self.assertIn("schemaHash", event["result"])
            self.assertTrue(event["result"]["requiredNamesPresent"])

    def test_large_unsaved_result_moves_to_sidecar_and_summary_stays_bounded(self):
        sentinel = "HUGE-RESULT-SENTINEL-" + "r" * 50000

        def responder(name, _arguments):
            if name.endswith(".Dump"):
                return {"returnValue": {"rows": [sentinel]}}
            return None

        with workspace_temp_directory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoint.json"
            result = MODULE.run_plan(
                make_plan([make_step("dump", "Dump")]),
                ScriptClient(responder), checkpoint=checkpoint, run_all=True,
            )
            self.assertTrue(result["ok"])
            rendered = json.dumps(result)
            self.assertLess(len(rendered), 2000)
            self.assertNotIn(sentinel, rendered)
            checkpoint_text = checkpoint.read_text(encoding="utf-8")
            self.assertNotIn(sentinel, checkpoint_text)
            state = json.loads(checkpoint_text)
            self.assertEqual(len(state["evidence"]["results"]), 1)
            result_path = Path(next(iter(state["evidence"]["results"].values()))["artifactPath"])
            self.assertIn(sentinel, result_path.read_text(encoding="utf-8"))

    def test_measured_client_and_telemetry_ledger_store_only_metrics(self):
        class EchoClient:
            def call_tool(self, _full_name, arguments):
                self.last_arguments = arguments
                return {"returnValue": "raw-payload-must-not-enter-ledger"}

        measured = MODULE._MeasuredClient(EchoClient())
        response = measured.call_tool("Toolset.Tool", {"secret": "input-payload"})
        self.assertEqual(response["returnValue"], "raw-payload-must-not-enter-ledger")
        metrics = measured.metrics()
        self.assertEqual(metrics["toolCallCount"], 1)
        self.assertGreater(metrics["toolInputBytes"], 0)
        self.assertGreater(metrics["toolOutputBytes"], 0)

        with workspace_temp_directory() as directory:
            ledger = Path(directory) / "telemetry.json"
            module = MODULE._load_token_telemetry_module()
            result = {
                "ok": True,
                "status": "checkpoint",
                "telemetryMetrics": {
                    **metrics,
                    "artifactBytes": 123,
                    "imageCount": 1,
                    "imagePixels": 64,
                },
            }
            receipt = MODULE._append_execution_telemetry(
                module, ledger, "request-test", result
            )
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["operation"], "execute-plan-checkpoint")
            self.assertEqual(receipt["eventKind"], "local-operation")
            ledger_text = ledger.read_text(encoding="utf-8")
            self.assertNotIn("input-payload", ledger_text)
            self.assertNotIn("raw-payload-must-not-enter-ledger", ledger_text)
            summary = module.summarize_ledger(ledger)
            self.assertEqual(summary["totals"]["toolCallCount"], 1)
            self.assertEqual(summary["totals"]["artifactBytes"], 123)
            self.assertEqual(summary["totals"]["imagePixels"], 64)
            self.assertEqual(
                summary["metricCoverageCounts"]["inputTokens"], 1
            )
            self.assertEqual(summary["eventKindCounts"]["model-call"], 0)
            token_budget = module.check_budget(
                summary, total_limits={"inputTokens": 0}
            )
            self.assertFalse(token_budget["withinBudget"])
            self.assertEqual(
                token_budget["unavailableMetrics"][0]["unavailabilityReason"],
                "no-model-call-receipts",
            )
            self.assertTrue(
                module.check_budget(
                    summary, total_limits={"toolCallCount": 1}
                )["withinBudget"]
            )

    def test_failed_call_omits_unknown_output_metric(self):
        class FailingClient:
            def call_tool(self, _full_name, _arguments):
                raise RuntimeError("PRIVATE-FAILED-TOOL-OUTPUT")

        measured = MODULE._MeasuredClient(FailingClient())
        with self.assertRaisesRegex(RuntimeError, "PRIVATE-FAILED-TOOL-OUTPUT"):
            measured.call_tool("Toolset.Tool", {"value": 1})
        metrics = measured.metrics()
        self.assertEqual(metrics["toolCallCount"], 1)
        self.assertIn("toolInputBytes", metrics)
        self.assertNotIn("toolOutputBytes", metrics)

    def test_cli_connection_failure_is_telemetried_without_raw_error(self):
        class FailingCliClient:
            def __init__(self, _url, _timeout):
                raise RuntimeError("PRIVATE-MCP-CONNECTION-DETAIL")

        with workspace_temp_directory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            ledger = root / "telemetry.json"
            plan_path.write_text(
                json.dumps(make_plan([make_step("one", "Create")])),
                encoding="utf-8",
            )
            args = mock.Mock(
                plan=plan_path,
                checkpoint=None,
                artifact_dir=None,
                resume_log=None,
                resume_after_get_widgets=False,
                run_all=False,
                max_steps_per_chunk=24,
                mcp_url="http://127.0.0.1:1/mcp",
                timeout=0.01,
                telemetry_ledger=ledger,
                request_id="connection-failure",
            )
            output = io.StringIO()
            with mock.patch.object(MODULE, "_CliClient", FailingCliClient):
                with contextlib.redirect_stdout(output):
                    exit_code = MODULE._run_cli(
                        args, MODULE.argparse.ArgumentParser()
                    )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errorCode"], "execution-exception")
            self.assertEqual(payload["telemetry"]["operation"], "execute-plan-failed")
            self.assertNotIn("PRIVATE-MCP-CONNECTION-DETAIL", output.getvalue())
            ledger_text = ledger.read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE-MCP-CONNECTION-DETAIL", ledger_text)
            event = json.loads(ledger_text)["events"][0]
            self.assertEqual(event["toolCallCount"], 0)
            self.assertEqual(event["eventKind"], "local-operation")

    def test_errors_are_bounded_hashed_and_local_paths_are_redacted(self):
        raw_error = (
            r"failed while reading E:\Users\operator\private\payload.json "
            + "SECRET-" * 4000
        )
        compact = MODULE._compact_error(raw_error)
        self.assertTrue(compact["truncated"])
        self.assertEqual(compact["charLength"], len(raw_error))
        self.assertLessEqual(
            len(compact["message"]), MODULE.MAX_ERROR_MESSAGE_CHARS + 3
        )
        self.assertNotIn(r"E:\Users\operator", compact["message"])
        self.assertNotIn("SECRET-", compact["message"])
        self.assertRegex(compact["sha256"], r"^[0-9a-f]{64}$")

        def fail_with_large_error(name, _arguments):
            if name.endswith(".Mutate"):
                raise RuntimeError(raw_error)
            return None

        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            result = MODULE.run_plan(
                make_plan([make_step("mutate", "Mutate")]),
                ScriptClient(fail_with_large_error),
                checkpoint=checkpoint,
                run_all=True,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result["errorTruncated"])
            self.assertLess(len(json.dumps(result)), 2000)
            self.assertNotIn(raw_error, checkpoint.read_text(encoding="utf-8"))

    def test_cli_missing_plan_returns_compact_json_without_traceback_or_path(self):
        with workspace_temp_directory() as directory:
            missing = Path(directory) / "private-missing-plan.json"
            completed = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), str(missing)],
                cwd=Path.cwd(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertNotIn("Traceback", completed.stdout + completed.stderr)
            self.assertNotIn(str(missing), completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["stage"], "startup")

    def test_partial_failed_chunk_checkpoints_confirmed_completed_prefix(self):
        plan = make_plan([
            make_step("capture", "Capture", save="captured"),
            make_step("mutate", "Mutate"),
            make_step(
                "add-boundary",
                "AddWidget",
                {"source": "${captured.returnValue.refPath}"},
            ),
        ])

        def fail_second(name, _arguments):
            if name.endswith(".Capture"):
                return {"returnValue": {"refPath": "/Game/UI/Captured"}}
            if name.endswith(".Mutate"):
                raise RuntimeError("second step failed")
            return None

        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            failed = MODULE.run_plan(
                plan,
                ScriptClient(fail_second),
                checkpoint=checkpoint,
                run_all=True,
            )
            self.assertFalse(failed["ok"])
            self.assertTrue(failed["checkpointAdvanced"])
            self.assertEqual(failed["completedPrefix"], 1)
            self.assertFalse(failed["requiresGetWidgets"])
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(state["completedPrefix"], 1)
            self.assertIn("captured", state["saved"])
            self.assertEqual(
                [event["status"] for event in state["events"]],
                ["completed", "failed"],
            )

            resumed_client = ScriptClient()
            resumed = MODULE.run_plan(
                plan, resumed_client, checkpoint=checkpoint, run_all=True
            )
            self.assertTrue(resumed["ok"])
            self.assertEqual(resumed["completedPrefix"], 3)
            resumed_tool_names = [name for name, _ in resumed_client.tool_calls]
            self.assertFalse(any(name.endswith(".Capture") for name in resumed_tool_names))
            self.assertTrue(any(name.endswith(".Mutate") for name in resumed_tool_names))
            self.assertTrue(any(name.endswith(".AddWidget") for name in resumed_tool_names))

    def test_checkpoint_write_failure_after_add_widget_requires_manual_recovery(self):
        plan = make_plan([make_step("add", "AddWidget")])
        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            with mock.patch.object(
                MODULE,
                "_atomic",
                side_effect=OSError("PRIVATE-CHECKPOINT-DETAIL"),
            ):
                result = MODULE.run_plan(
                    plan,
                    ScriptClient(),
                    checkpoint=checkpoint,
                    run_all=True,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "checkpoint-write-failed")
            self.assertEqual(result["completedPrefix"], 1)
            self.assertEqual(result["durableCompletedPrefix"], 0)
            self.assertFalse(result["checkpointPersisted"])
            self.assertTrue(result["requiresGetWidgets"])
            self.assertTrue(result["manualRecoveryRequired"])
            self.assertNotIn("PRIVATE-CHECKPOINT-DETAIL", json.dumps(result))
            self.assertFalse(checkpoint.exists())

    def test_failed_add_widget_requires_explicit_recovery_before_resume(self):
        plan = make_plan([
            make_step("create", "Create", save="blueprint"),
            make_step("add", "AddWidget", {"parent": "${blueprint.returnValue.refPath}"}),
        ])

        def fail_add(name, _arguments):
            if name.endswith(".AddWidget"):
                raise RuntimeError("simulated AddWidget failure")
            return None

        with workspace_temp_directory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            first = MODULE.run_plan(
                plan, ScriptClient(), checkpoint=checkpoint,
                run_all=False, max_steps_per_chunk=1,
            )
            self.assertTrue(first["ok"])
            self.assertEqual(first["completedPrefix"], 1)
            failed = MODULE.run_plan(
                plan, ScriptClient(fail_add), checkpoint=checkpoint,
                run_all=True, max_steps_per_chunk=1,
            )
            self.assertFalse(failed["ok"])
            self.assertEqual(failed["completedPrefix"], 1)
            self.assertFalse(failed["checkpointAdvanced"])
            self.assertTrue(failed["requiresGetWidgets"])
            failed_state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(failed_state["completedPrefix"], 1)
            self.assertIn("blueprint", failed_state["saved"])
            blocked_client = ScriptClient()
            blocked = MODULE.run_plan(
                plan, blocked_client, checkpoint=checkpoint,
                run_all=True, max_steps_per_chunk=1,
            )
            self.assertFalse(blocked["ok"])
            self.assertTrue(blocked["recoveryConfirmationRequired"])
            self.assertEqual(blocked_client.calls, [])
            resumed = MODULE.run_plan(
                plan, ScriptClient(), checkpoint=checkpoint,
                run_all=True, max_steps_per_chunk=1,
                resume_after_get_widgets=True,
            )
            self.assertTrue(resumed["ok"])
            self.assertEqual(resumed["completedPrefix"], 2)


if __name__ == "__main__":
    unittest.main()
