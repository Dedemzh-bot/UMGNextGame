import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("execute_plan.py")
SPEC = importlib.util.spec_from_file_location("execute_plan_under_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, toolset, tool, arguments):
        self.calls.append((toolset, tool, arguments))
        if tool == "create":
            return {"returnValue": {"refPath": "/Game/UI/Role"}}
        if tool == "add":
            return {"returnValue": {"refPath": "/Game/UI/Role/Child"}}
        return {"returnValue": True}


def plan():
    return {
        "objectPath": "/Game/UI/Role",
        "assetPath": "/Game/UI/Role",
        "steps": [
            {
                "stepId": "create",
                "toolsetName": "editor",
                "toolName": "create",
                "arguments": {},
                "saveResultAs": "blueprint",
            },
            {
                "stepId": "add",
                "toolsetName": "editor",
                "toolName": "add",
                "arguments": {"parent": "${blueprint.returnValue.refPath}"},
                "saveResultAs": "child",
            },
            {
                "stepId": "finish",
                "toolsetName": "editor",
                "toolName": "finish",
                "arguments": {"child": "${child.returnValue.refPath}"},
            },
        ],
    }


class ResumeExecutionTests(unittest.TestCase):
    def write_log(self, events):
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        with handle:
            json.dump(events, handle)
        return Path(handle.name)

    def test_resume_restarts_started_step_and_restores_reference(self):
        events = [
            {"status": "started", "index": 1, "stepId": "create"},
            {"status": "completed", "index": 1, "stepId": "create",
             "result": {"returnValue": {"refPath": "/Game/UI/Role"}}},
            {"status": "started", "index": 2, "stepId": "add"},
        ]
        path = self.write_log(events)
        client = FakeClient()
        result = MODULE.execute(plan(), client, path, resume_log=path)
        self.assertEqual([call[1] for call in client.calls], ["add", "finish"])
        self.assertEqual(client.calls[0][2]["parent"], "/Game/UI/Role")
        self.assertEqual(client.calls[1][2]["child"], "/Game/UI/Role/Child")
        self.assertEqual(result["stepsCompleted"], 3)
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted[:3], events)
        self.assertEqual(persisted[3]["status"], "resumed")

    def test_resume_rejects_hole(self):
        path = self.write_log([
            {"status": "completed", "index": 1, "stepId": "create",
             "result": {"returnValue": {"refPath": "/Game/UI/Role"}}},
            {"status": "completed", "index": 3, "stepId": "finish",
             "result": {"returnValue": True}},
        ])
        with self.assertRaisesRegex(RuntimeError, "completed step after an incomplete prefix"):
            MODULE.execute(plan(), FakeClient(), path, resume_log=path)

    def test_resume_rejects_step_id_mismatch(self):
        path = self.write_log([
            {"status": "completed", "index": 1, "stepId": "wrong",
             "result": {"returnValue": {"refPath": "/Game/UI/Role"}}},
        ])
        with self.assertRaisesRegex(RuntimeError, "step mismatch"):
            MODULE.execute(plan(), FakeClient(), path, resume_log=path)

    def test_default_execution_is_unchanged(self):
        client = FakeClient()
        handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        handle.close()
        log = Path(handle.name)
        result = MODULE.execute(plan(), client, log)
        self.assertEqual([call[1] for call in client.calls], ["create", "add", "finish"])
        self.assertEqual(result["stepsCompleted"], 3)
        persisted = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual([event["status"] for event in persisted], [
            "started", "completed", "started", "completed", "started", "completed"
        ])



if __name__ == "__main__":
    unittest.main()
