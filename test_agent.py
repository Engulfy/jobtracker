import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import agent
import tracker


class FakeAI:
    key = "test"

    def __init__(self):
        self.inputs = []
        self.generated = 0

    def response(self, items, instructions, tools=None):
        self.inputs.append(list(items))
        if len(self.inputs) == 1:
            return {"output": [{"type": "reasoning", "encrypted_content": "opaque"},
                               {"type": "function_call", "name": "search_jobs", "arguments": '{"query":""}', "call_id": "c1"}]}
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Found a match."}]}]}

    @staticmethod
    def text(response):
        return "\n".join(c["text"] for item in response.get("output", []) if item.get("type") == "message"
                         for c in item.get("content", []) if c.get("type") == "output_text")

    def generate(self, instructions, data):
        self.generated += 1
        return json.dumps({"score": 75, "evidence": ["Python in description"], "gaps": [], "uncertainty": ["Eligibility unknown"]})


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = agent.Store(Path(self.temp.name))
        self.jobs = {
            "one": {"uid": "one", "title": "Backend Engineer", "company": "Example", "location": "Singapore", "status": "Open", "description": "Python SQL", "url": "https://example.org/job"},
            "two": {"uid": "two", "title": "Java Engineer", "company": "Other", "status": "Open", "description": "JavaScript"},
            "three": {"uid": "three", "title": "Backend Engineer", "company": "Closed", "status": "Closed"},
        }
        self.patcher = patch.object(self.store, "jobs", return_value=self.jobs)
        self.patcher.start()
        tracker.atomic_json(self.store.home / "profile.json", {"skills": ["Python", "Java"], "preferred_roles": ["backend"]})
        self.ai = FakeAI()
        self.agent = agent.Agent(self.store, self.ai)

    def tearDown(self):
        self.patcher.stop()
        self.store.db.close()
        self.temp.cleanup()

    def test_personalized_ranking_and_token_boundaries(self):
        results = self.agent.search()
        self.assertEqual([r["uid"] for r in results], ["one", "two"])
        self.assertEqual(results[0]["matched_skills"], ["Python"])
        self.assertEqual(results[1]["matched_skills"], ["Java"])
        self.assertFalse(agent.contains("JavaScript", "Java"))

    def test_filter_and_hide_persist(self):
        self.agent.handle("/status one hidden no longer interested")
        self.assertEqual([r["uid"] for r in self.agent.search()], ["two"])
        self.assertEqual(self.store.applications()[0]["notes"], "no longer interested")
        self.assertEqual(self.agent.search("missing"), [])

    def test_status_validation_and_unknown_job(self):
        with self.assertRaises(ValueError):
            self.store.status("one", "invented")
        with self.assertRaises(ValueError):
            self.store.status("missing", "applied")

    def test_ai_cannot_write_application_state(self):
        result = self.agent.execute("set_status", {"uid": "one", "status": "applied", "notes": ""})
        self.assertIn("error", result)
        self.assertEqual(self.store.applications(), [])

    def test_tool_loop_preserves_reasoning_and_results(self):
        self.assertEqual(self.agent.chat("Find jobs"), "Found a match.")
        self.assertTrue(any(i.get("type") == "reasoning" for i in self.ai.inputs[1]))
        self.assertTrue(any(i.get("type") == "function_call_output" for i in self.ai.inputs[1]))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM history").fetchone()[0], 2)

    def test_assessment_cache_invalidated_by_profile(self):
        self.agent.assess("one")
        self.agent.assess("one")
        self.assertEqual(self.ai.generated, 1)
        tracker.atomic_json(self.store.home / "profile.json", {"skills": ["SQL"]})
        self.agent.assess("one")
        self.assertEqual(self.ai.generated, 2)

    def test_draft_private_and_forget_preserves_applications(self):
        result = self.agent.draft("one")
        self.assertFalse(result["submitted"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)
        self.store.status("one", "shortlisted")
        self.store.remember("user", "hello")
        self.agent.handle("/forget")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM history").fetchone()[0], 0)
        self.assertEqual(len(self.store.applications()), 1)

    def test_no_key_still_supports_commands(self):
        with patch.dict(agent.os.environ, {"GEMINI_API_KEY": ""}):
            local = agent.Agent(self.store, agent.Gemini(self.store))
            self.assertIn("Backend Engineer", local.handle("/top"))
            self.assertIn("GEMINI_API_KEY", local.handle("Hello"))

    def test_profile_validation(self):
        tracker.atomic_json(self.store.home / "profile.json", {"skills": "Python"})
        with self.assertRaisesRegex(ValueError, "list of strings"):
            self.agent.search()

    def test_telegram_authorization_and_restart_offset(self):
        telegram = Mock(chat="123")
        for chat in ({"id": 999, "type": "private"}, {"id": 123, "type": "group"}):
            agent.process_update(self.agent, telegram, {"update_id": 10, "message": {"chat": chat, "text": "/applications"}})
        telegram.send.assert_not_called()
        agent.process_update(self.agent, telegram, {"update_id": 11, "message": {
            "chat": {"id": 123, "type": "private"}, "text": "/help"}})
        telegram.send.assert_called_once_with(agent.HELP)
        self.assertEqual(self.store.setting("telegram_offset"), "12")

    def test_failed_telegram_send_does_not_advance_offset(self):
        telegram = Mock(chat="123")
        telegram.send.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            agent.process_update(self.agent, telegram, {"update_id": 11, "message": {
                "chat": {"id": 123, "type": "private"}, "text": "/help"}})
        self.assertEqual(self.store.setting("telegram_offset", "0"), "0")

    def test_turn_budget_limits_tool_calls(self):
        self.ai.response = Mock(return_value={"output": [{"type": "function_call", "name": "search_jobs",
            "arguments": '{"query":""}', "call_id": "repeat"}]})
        with self.assertRaisesRegex(RuntimeError, "turn limit"):
            self.agent.chat("keep searching")
        self.assertEqual(self.ai.response.call_count, 6)

    def test_public_exports_do_not_include_private_fields(self):
        root = Path(self.temp.name) / "public"
        (root / "docs").mkdir(parents=True)
        job = {**self.jobs["one"], "description": "private description", "cv_text": "private CV", "notes": "private note"}
        with patch.object(tracker, "ROOT", root):
            tracker.write_exports({"jobs": {"one": job}})
        exported = (root / "docs" / "jobs.json").read_text(encoding="utf-8")
        self.assertNotIn("private", exported)



if __name__ == "__main__":
    unittest.main()
