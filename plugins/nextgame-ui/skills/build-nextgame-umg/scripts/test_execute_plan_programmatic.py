import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("execute_plan_programmatic.py")
SPEC = importlib.util.spec_from_file_location("execute_plan_programmatic_under_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


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

    def __init__(self):
        self.calls = []

    def call_tool(self, full_name, arguments):
        self.calls.append((full_name, arguments))
        if full_name == MODULE.ENV:
            return {"environment": "fake"}
        if full_name != MODULE.EXEC:
            raise AssertionError(full_name)
        tool_calls = []

        def execute_tool(name, raw_arguments):
            parsed = json.loads(raw_arguments)
            tool_calls.append((name, parsed))
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
        self.assertEqual(result["toolCalls"][1][1]["parent"], "/Game/UI/UMG/Role/umg_role")

    def test_resume_partial_sequential_log_restores_saved_reference(self):
        plan = make_plan([
            make_step("create", "Create", save="blueprint"),
            make_step("add", "AddWidget", {"parent": "${blueprint.returnValue.refPath}"}, "child"),
        ])
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        with handle:
            json.dump([
                {"status": "completed", "index": 1, "stepId": "create",
                 "result": {"returnValue": {"refPath": "/Game/UI/UMG/Role/umg_role"}}},
            ], handle)
        log = Path(handle.name)
        client = ScriptClient()
        result = MODULE.run_plan(plan, client, resume_log=log, run_all=True, max_steps_per_chunk=1)
        self.assertTrue(result["ok"])
        execute_call = [call for call in client.calls if call[0] == MODULE.EXEC][0]
        self.assertNotIn('"stepId":"create"', execute_call[1]["script"])
        self.assertIn('"stepId":"add"', execute_call[1]["script"])
        self.assertEqual(result["saved"]["child"]["returnValue"]["refPath"], "/Game/UI/UMG/Role/Child")

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


if __name__ == "__main__":
    unittest.main()
