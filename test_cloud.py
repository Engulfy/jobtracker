import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
import agent
import cloud
from gemini_provider import Gemini, AIUnavailable


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = agent.Store(self.home / "a")
        self.env = patch.dict(os.environ, {"GEMINI_API_KEY": "fake-secret", "GEMINI_DAILY_REQUESTS": "15",
                                          "GEMINI_RUN_REQUESTS": "6"})
        self.env.start()
        self.ai = Gemini(self.store)

    def tearDown(self):
        self.env.stop()
        self.store.db.close()
        self.temp.cleanup()

    def test_encrypted_round_trip_and_unchanged_no_rewrite(self):
        self.store.set_setting("private", "sensitive note")
        path = self.home / "state.enc"
        cipher = Fernet(Fernet.generate_key())
        self.assertTrue(cloud.save(self.store, path, cipher, b""))
        self.assertNotIn(b"sensitive note", path.read_bytes())
        before_bytes = path.read_bytes()
        other = agent.Store(self.home / "b")
        try:
            cloud.restore(other, path, cipher)
            self.assertEqual(other.setting("private"), "sensitive note")
            self.assertFalse(cloud.save(other, path, cipher, cloud.snapshot(other)))
            self.assertEqual(path.read_bytes(), before_bytes)
        finally:
            other.db.close()

    def test_wrong_key_fails_without_reset(self):
        self.store.set_setting("keep", "value")
        path = self.home / "state.enc"
        cloud.save(self.store, path, Fernet(Fernet.generate_key()), b"")
        with self.assertRaisesRegex(ValueError, "decryption"):
            cloud.restore(self.store, path, Fernet(Fernet.generate_key()))
        self.assertEqual(self.store.setting("keep"), "value")

    def test_request_budgets_survive_new_provider(self):
        with patch.dict(os.environ, {"GEMINI_DAILY_REQUESTS": "1"}):
            self.ai.reserve()
            with self.assertRaisesRegex(AIUnavailable, "allowance"):
                Gemini(self.store).reserve()

    def test_quota_cooldown_no_retry_or_paid_fallback(self):
        response = Mock(status_code=429, ok=False)
        with patch("gemini_provider.requests.post", return_value=response) as post:
            with self.assertRaises(AIUnavailable):
                self.ai.request({"contents": []})
            with self.assertRaisesRegex(AIUnavailable, "cooling"):
                Gemini(self.store).request({"contents": []})
            self.assertEqual(post.call_count, 1)
            self.assertIn("generativelanguage.googleapis.com", post.call_args.args[0])

    def test_native_tool_parts_and_thought_signatures_preserved(self):
        content = {"role": "model", "parts": [{"functionCall": {"name": "search_jobs", "args": {"query": "python"}, "id": "abc"},
                                                 "thoughtSignature": "opaque"}]}
        with patch.object(self.ai, "request", return_value=content):
            response = self.ai.response([{"role": "user", "content": "find jobs"}], "rules", agent.TOOLS)
        items = [{"role": "user", "content": "find jobs"}] + response["output"]
        items.append({"type": "function_call_output", "call_id": "native:abc", "output": "[]"})
        with patch.object(self.ai, "request", return_value={"role": "model", "parts": [{"text": "done"}]}) as request:
            result = self.ai.response(items, "rules", agent.TOOLS)
            sent = request.call_args.args[0]
            self.assertEqual(sent["contents"][1], content)
            function_response = sent["contents"][2]["parts"][0]["functionResponse"]
            self.assertEqual(function_response["name"], "search_jobs")
            self.assertEqual(function_response["id"], "abc")
            self.assertEqual(self.ai.text(result), "done")

    def test_structured_json_output(self):
        with patch.object(self.ai, "request", return_value={"parts": [{"text": '{"score":75}'}]}) as request:
            self.assertEqual(self.ai.generate_json("rules", {}), {"score": 75})
            self.assertEqual(request.call_args.args[0]["generationConfig"]["responseMimeType"], "application/json")

    def test_contact_and_raw_cv_excluded_and_cache_stable(self):
        profile = {"name": "secret name", "cv_text": "secret CV", "email": "secret@example.com", "skills": ["Python"]}
        self.assertEqual(agent.ai_profile(profile), {"skills": ["Python"]})
        first = agent.assessment_key({"uid": "one", "description": "Python", "last_seen": "yesterday"}, profile, "model")
        second = agent.assessment_key({"uid": "one", "description": "Python", "last_seen": "today"}, profile, "model")
        self.assertEqual(first, second)

    def test_cloud_poll_is_bounded_and_offset_used(self):
        self.store.set_setting("telegram_offset", "42")
        bot, telegram = Mock(store=self.store), Mock()
        telegram.call.return_value = []
        cloud.poll_once(bot, telegram)
        self.assertEqual(telegram.call.call_args.args[1], {"offset": 42, "timeout": 0, "limit": 10, "allowed_updates": ["message"]})

    def test_no_scan_or_digest_when_not_due(self):
        timestamp = 200000
        self.store.set_setting("cloud_scan_at", timestamp - 60)
        self.store.set_setting("last_digest", timestamp - 60)
        self.store.set_setting("cloud_notified", "[]")
        bot, telegram = Mock(store=self.store), Mock()
        with patch.object(cloud, "poll_once"), patch.object(agent, "scan") as scan:
            cloud.tick(bot, telegram, timestamp)
            scan.assert_not_called()
            telegram.send.assert_not_called()

    def test_batch_assessment_one_request_and_cache(self):
        jobs = {"one": {"uid": "one", "title": "Python engineer", "description": "Python"},
                "two": {"uid": "two", "title": "Java engineer", "description": "Java"}}
        bot = agent.Agent(self.store, self.ai)
        response = {"assessments": [{"uid": uid, "score": 60, "evidence": [], "gaps": [], "uncertainty": []} for uid in jobs]}
        with patch.object(self.store, "jobs", return_value=jobs), patch.object(self.ai, "generate_json", return_value=response) as generate:
            bot.assess_many(list(jobs.values()))
            bot.assess_many(list(jobs.values()))
            self.assertEqual(generate.call_count, 1)
            response["assessments"][0]["uid"] = "invented"
            self.store.db.execute("DELETE FROM assessments")
            with self.assertRaisesRegex(ValueError, "IDs"):
                bot.assess_many(list(jobs.values()))

    def test_digest_falls_back_without_losing_matches(self):
        bot = Mock()
        bot.ai.key = "test"
        bot.search.return_value = [{"uid": "one"}]
        bot.assess_many.side_effect = AIUnavailable("quota")
        bot.handle.return_value = "Keyword matches"
        self.assertIn("Keyword matches", agent.digest(bot))

    def test_invalid_native_response_and_network_errors_are_redacted(self):
        with patch("gemini_provider.requests.post") as post:
            post.return_value.ok = True
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"candidates": [{"finishReason": "MAX_TOKENS"}]}
            with self.assertRaisesRegex(AIUnavailable, "incomplete"):
                self.ai.request({})
        self.assertEqual(json.loads(self.store.setting("gemini_budget"))["used"], 1)

    def test_full_cloud_run_persists_command_and_resumes_without_repeat(self):
        root = self.home / "repo"
        root.mkdir()
        key = Fernet.generate_key().decode()
        telegram = Mock(chat="123")
        telegram.call.return_value = [{"update_id": 50, "message": {
            "chat": {"id": 123, "type": "private"}, "text": "/status one applied Submitted"}}]
        jobs = {"one": {"uid": "one", "title": "Engineer", "company": "Example", "status": "Open"}}
        with patch.dict(os.environ, {"AGENT_STATE_KEY": key, "AGENT_PROFILE_JSON": '{"skills":["Python"]}',
                                     "JOB_AGENT_HOME": str(self.home / "cloud-home")}), \
             patch.object(cloud, "ROOT", root), patch.object(agent.Store, "jobs", return_value=jobs), \
             patch.object(agent, "Telegram", return_value=telegram), patch.object(agent, "scan") as scan, \
             patch.object(agent, "digest", return_value="Daily digest"):
            cloud.run()
            self.assertEqual(scan.call_count, 1)
            ciphertext = (root / "data" / "agent-state.enc").read_bytes()
            self.assertNotIn(b"Submitted", ciphertext)
            telegram.call.return_value = []
            telegram.send.reset_mock()
            cloud.run()
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(telegram.call.call_args.args[1]["offset"], 51)
            telegram.send.assert_not_called()
            self.assertEqual((root / "data" / "agent-state.enc").read_bytes(), ciphertext)

    def test_missing_initialized_cloud_state_fails_before_network(self):
        root = self.home / "repo"
        (root / "data").mkdir(parents=True)
        (root / "data" / "agent-state.initialized").write_text("v1")
        with patch.dict(os.environ, {"AGENT_STATE_KEY": Fernet.generate_key().decode()}), patch.object(cloud, "ROOT", root):
            with self.assertRaisesRegex(ValueError, "missing"):
                cloud.run()


if __name__ == "__main__":
    unittest.main()
