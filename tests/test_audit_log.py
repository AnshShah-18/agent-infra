import json
import os
import tempfile
import unittest

from compliance.audit_log import AuditLogger, Outcome


class TestAuditLogger(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self.path)  # let AuditLogger create it fresh
        self.log = AuditLogger(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_empty_log_verifies_clean(self):
        ok, reason = self.log.verify_chain()
        self.assertTrue(ok, reason)

    def test_single_entry_is_recorded_and_verifies(self):
        entry = self.log.log_event("user:ansh", "submit_task", "agent:planner", Outcome.SUCCESS,
                                    {"task_id": "t-1"})
        self.assertEqual(entry.seq, 1)
        ok, reason = self.log.verify_chain()
        self.assertTrue(ok, reason)

    def test_entries_chain_in_order(self):
        self.log.log_event("user:ansh", "submit_task", "agent:planner", Outcome.SUCCESS, {})
        self.log.log_event("agent:planner", "trigger_agent_run", "agent:executor", Outcome.SUCCESS, {})
        self.log.log_event("agent:executor", "call_tool", "tool:web_search", Outcome.DENIED,
                            {"reason": "missing permission"})
        entries = self.log.read_all()
        self.assertEqual([e.seq for e in entries], [1, 2, 3])
        self.assertEqual(entries[1].prev_hash, entries[0].entry_hash)
        self.assertEqual(entries[2].prev_hash, entries[1].entry_hash)
        ok, reason = self.log.verify_chain()
        self.assertTrue(ok, reason)

    def test_tampering_with_a_past_entry_is_detected(self):
        self.log.log_event("user:ansh", "submit_task", "agent:planner", Outcome.SUCCESS, {})
        self.log.log_event("agent:planner", "trigger_agent_run", "agent:executor", Outcome.SUCCESS, {})

        # Simulate an attacker/operator editing history directly in the file.
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        first = json.loads(lines[0])
        first["action"] = "trigger_agent_run"  # changed after the fact, hash not recomputed
        lines[0] = json.dumps(first) + "\n"
        with open(self.path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        ok, reason = self.log.verify_chain()
        self.assertFalse(ok)
        self.assertIn("entry_hash", reason)

    def test_deleting_a_past_entry_is_detected(self):
        self.log.log_event("user:ansh", "submit_task", "agent:planner", Outcome.SUCCESS, {})
        self.log.log_event("agent:planner", "trigger_agent_run", "agent:executor", Outcome.SUCCESS, {})
        self.log.log_event("agent:executor", "call_tool", "tool:web_search", Outcome.DENIED, {})

        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        del lines[1]  # remove the middle entry entirely
        with open(self.path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        ok, reason = self.log.verify_chain()
        self.assertFalse(ok)

    def test_log_persists_across_logger_instances(self):
        self.log.log_event("user:ansh", "submit_task", "agent:planner", Outcome.SUCCESS, {})
        reopened = AuditLogger(self.path)
        entry = reopened.log_event("agent:planner", "trigger_agent_run", "agent:executor",
                                    Outcome.SUCCESS, {})
        self.assertEqual(entry.seq, 2)
        ok, reason = reopened.verify_chain()
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
