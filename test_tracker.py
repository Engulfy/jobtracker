"""Run with: python -m unittest -v"""
from __future__ import annotations
import unittest
import tempfile
import json
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import tracker


def job(title, *, location="Singapore, Singapore", description="", provider="Lever", uid="lever:demo:123", company="Demo"):
    return {"uid": uid, "company": company, "provider": provider, "source": "lever:demo", "title": title,
            "location": location, "description": description, "url": "https://jobs.example.com/123",
            "posted_at": "", "updated_at": "", "employment_type": ""}


class FilterTests(unittest.TestCase):
    def test_intern(self):
        x = job("Software Engineer, Internship")
        self.assertTrue(tracker.eligible(x))
        self.assertEqual(x["category"], "Internship")

    def test_grad(self):
        x = job("Platform Engineer, New Grad (2027)")
        self.assertTrue(tracker.eligible(x))
        self.assertEqual(x["category"], "New grad / entry-level")

    def test_generic_entry_level_from_description(self):
        x = job("Backend Software Engineer", description="Applicants with 0-2 years of experience are welcome")
        self.assertTrue(tracker.eligible(x))

    def test_graduate_requires_tech(self):
        self.assertFalse(tracker.eligible(job("Marketing Graduate")))

    def test_geography(self):
        self.assertFalse(tracker.eligible(job("Software Engineer Intern", location="London")))
        self.assertFalse(tracker.eligible(job("Software Engineer Intern", location="Remote, APAC")))
        self.assertTrue(tracker.eligible(job("Software Engineer Intern", location="SG - Singapore")))

    def test_exclude_senior(self):
        self.assertFalse(tracker.eligible(job("Senior Software Engineer, New Grad")))
        self.assertFalse(tracker.eligible(job("Principal Security Engineer Intern")))

    def test_not_excluding_sre(self):
        self.assertTrue(tracker.eligible(job("Site Reliability Engineer Intern")))
        self.assertTrue(tracker.eligible(job("SRE Intern")))

    def test_product_and_research_tech(self):
        self.assertTrue(tracker.eligible(job("Product Management Intern")))
        self.assertTrue(tracker.eligible(job("Research Scientist Intern")))

    def test_exclude_intern_not_tech(self):
        self.assertFalse(tracker.eligible(job("People and Culture Intern")))

    def test_keep_separate_requisitions(self):
        a = job("Software Engineer Intern", uid="lever:demo:1")
        b = job("Software Engineer Intern", uid="lever:demo:2")
        self.assertEqual(len(tracker.dedupe_run([a, b])), 2)

    def test_direct_priority_over_aggregator(self):
        direct = job("Software Engineer Intern", company="Palantir", uid="lever:palantir:1")
        aggregated = job("Software Engineer Intern", provider="Adzuna", company="Palantir Technologies Inc.", uid="adzuna:50")
        self.assertEqual(len(tracker.dedupe_run([aggregated, direct])), 1)
        self.assertEqual(tracker.dedupe_run([aggregated, direct])[0]["uid"], direct["uid"])

    def test_date(self):
        self.assertTrue(tracker.iso_utc("2026-11-17T05:00:00Z").startswith("2026-11-17T05:00:00"))
        self.assertEqual(tracker.iso_utc(None), "")


class SourceTests(unittest.TestCase):
    def test_greenhouse_format(self):
        fake = Mock()
        fake.get.return_value.json.return_value = {"jobs": [{"id": 3, "title": "SRE Intern",
            "location": {"name": "Singapore"}, "absolute_url": "https://example.com/3",
            "content": "<p>Write code</p>", "updated_at": "2026-11-17T01:00:00Z"}]}
        fake.get.return_value.raise_for_status.return_value = None
        rows = tracker.fetch_greenhouse(fake, {"slug": "demo", "company": "Demo"})
        self.assertEqual(rows[0]["uid"], "greenhouse:demo:3")
        self.assertEqual(rows[0]["description"], "Write code")

    def test_lever_format(self):
        fake = Mock()
        fake.get.return_value.json.return_value = [{"id": "abc", "text": "Software Engineer, New Grad",
            "categories": {"location": "Singapore", "commitment": "Full-time"},
            "createdAt": 1780000000000, "hostedUrl": "https://jobs.lever.co/test/abc"}]
        rows = tracker.fetch_lever(fake, {"slug": "test", "company": "Demo"})
        self.assertEqual(rows[0]["location"], "Singapore")
        self.assertTrue(rows[0]["posted_at"])

    def test_ashby_secondary_location_and_unlisted(self):
        fake = Mock()
        fake.get.return_value.json.return_value = {"jobs": [{"id": "x", "title": "Cloud Engineer Intern",
            "location": "Remote", "secondaryLocations": [{"location": "Singapore"}],
            "isListed": True, "jobUrl": "https://jobs.ashbyhq.com/demo/x"},
            {"id": "z", "title": "Backend Engineer Intern", "isListed": False}]}
        rows = tracker.fetch_ashby(fake, {"slug": "demo", "company": "Demo"})
        self.assertEqual(len(rows), 1)
        self.assertTrue(tracker.eligible(rows[0]))

    def test_smartrecruiters_entry_level_metadata(self):
        fake = Mock()
        fake.get.return_value.json.return_value = {"totalFound": 1, "content": [{
            "id": "123", "name": "Software Developer", "location": {"city": "Singapore", "country": "SG"},
            "experienceLevel": {"label": "Entry Level"}, "typeOfEmployment": {"label": "Full-time"}}]}
        rows = tracker.fetch_smartrecruiters(fake, {"slug": "demo", "company": "Demo"})
        self.assertTrue(tracker.eligible(rows[0]))
        self.assertEqual(rows[0]["category"], "New grad / entry-level")

    def test_smartrecruiters_format(self):
        fake = Mock()
        fake.get.return_value.json.return_value = {"totalFound": 1, "content": [{"id": "123", "name": "AI Engineer Intern",
            "location": {"city": "Singapore", "country": "sg"},
            "releasedDate": "2026-11-17T05:00:00.000Z"}]}
        rows = tracker.fetch_smartrecruiters(fake, {"slug": "demo", "company": "Demo"})
        self.assertEqual(len(rows), 1)
        self.assertTrue(tracker.eligible(rows[0]))
        self.assertEqual(rows[0]["url"], "https://jobs.smartrecruiters.com/demo/123")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.db = {"version": 1, "initialized": False, "initial_digest_sent": False, "jobs": {}}
        self.config = {"priority_keywords": [], "priority_companies": []}
        self.a = job("Software Engineer Intern")
        tracker.eligible(self.a)

    def test_first_scan_then_same_scan_no_new(self):
        t = "2026-11-17T00:00:00+00:00"
        first = tracker.reconcile(self.db, [deepcopy(self.a)], {"lever:demo"}, t, self.config)
        second = tracker.reconcile(self.db, [deepcopy(self.a)], {"lever:demo"}, t, self.config)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(self.db["jobs"][self.a["uid"]]["status"], "Open")

    def test_closure_requires_two_successful_scans(self):
        t = "2026-11-17T00:00:00+00:00"
        tracker.reconcile(self.db, [deepcopy(self.a)], {"lever:demo"}, t, self.config)
        tracker.reconcile(self.db, [], set(), t, self.config)  # outage should not close
        self.assertEqual(self.db["jobs"][self.a["uid"]]["missing_scans"], 0)
        tracker.reconcile(self.db, [], {"lever:demo"}, t, self.config)
        self.assertEqual(self.db["jobs"][self.a["uid"]]["status"], "Open")
        tracker.reconcile(self.db, [], {"lever:demo"}, t, self.config)
        self.assertEqual(self.db["jobs"][self.a["uid"]]["status"], "Closed")
        reopened = tracker.reconcile(self.db, [deepcopy(self.a)], {"lever:demo"}, t, self.config)
        self.assertEqual(reopened[0]["event"], "Reopened")

    def test_adzuna_becomes_unverified_not_closed(self):
        a = deepcopy(self.a)
        a.update(uid="adzuna:foo", source="adzuna", provider="Adzuna")
        tracker.reconcile(self.db, [a], set(), "2026-10-01T00:00:00+00:00", self.config)
        tracker.reconcile(self.db, [], set(), "2026-11-01T00:00:00+00:00", self.config)
        self.assertEqual(self.db["jobs"]["adzuna:foo"]["status"], "Unverified")


class IntegrationTests(unittest.TestCase):
    def test_two_runs_export_and_persist(self):
        with tempfile.TemporaryDirectory() as root:
            fake_job = job("Software Engineer Intern")
            config = {"sources": [{"type": "lever", "slug": "demo", "company": "Demo"}],
                      "priority_keywords": [], "priority_companies": []}
            with patch.object(tracker, "ROOT", Path(root)), \
                 patch.object(tracker, "fetch_lever", return_value=[fake_job]), \
                 patch.object(tracker, "make_session", return_value=Mock()), \
                 patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "ADZUNA_APP_ID": "", "ADZUNA_APP_KEY": ""}):
                tracker.run(config, notify=True)
                db = json.loads((Path(root) / "data/jobs.json").read_text())
                self.assertTrue(db["initialized"])
                self.assertEqual(len(db["jobs"]), 1)
                self.assertNotIn("description", next(iter(db["jobs"].values())))
                self.assertTrue((Path(root) / "docs/jobs.csv").exists())
                tracker.run(config, notify=True)
                db2 = json.loads((Path(root) / "data/jobs.json").read_text())
                self.assertEqual(len(db2["jobs"]), 1)


if __name__ == "__main__":
    unittest.main()
