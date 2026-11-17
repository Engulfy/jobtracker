"""Regression tests for major-tech connectors and incomplete-feed safeguards."""
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch

import job_sources
import tracker


def response(payload):
    result = Mock()
    result.json.return_value = payload
    return result


class WorkdayTests(unittest.TestCase):
    source = dict(type="workday", slug="demo", cluster="wd5", site="External", company="Demo")

    def test_multilocation_role_and_description(self):
        session = Mock()
        session.post.return_value = response({"total": 1, "jobPostings": [
            {"title": "Software Engineer", "externalPath": "/job/Remote/Engineer_R123"}]})
        session.get.return_value = response({"jobPostingInfo": {
            "jobReqId": "R123", "title": "Software Engineer", "location": "Remote, USA",
            "additionalLocations": ["Singapore"], "jobDescription": "<p>0-2 years of experience</p>",
            "canApply": True, "startDate": "2026-11-17"}})
        jobs = job_sources.fetch_workday(session, self.source)
        self.assertTrue(tracker.eligible(jobs[0]))
        self.assertEqual(jobs[0]["uid"], "workday:demo:R123")
        self.assertEqual(jobs[0]["url"], "https://demo.wd5.myworkdayjobs.com/External/job/Remote/Engineer_R123")

    def test_incomplete_page_fails_instead_of_closing_jobs(self):
        session = Mock()
        session.post.return_value = response({"total": 1, "jobPostings": []})
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            job_sources.fetch_workday(session, self.source)

    def test_closed_job_is_excluded(self):
        session = Mock()
        session.post.return_value = response({"total": 1, "jobPostings": [
            {"title": "Software Engineer Intern", "externalPath": "/job/intern"}]})
        session.get.return_value = response({"jobPostingInfo": {"jobReqId": "R1", "canApply": False}})
        self.assertEqual(job_sources.fetch_workday(session, self.source), [])


class AmazonTests(unittest.TestCase):
    source = dict(type="amazon", slug="amazon", company="Amazon / AWS")

    def test_pagination_and_qualifications(self):
        session = Mock()
        def job(i):
            return dict(id=str(i), id_icims=str(i), title="Software Engineer", country_code="SGP",
                        basic_qualifications="2 years of experience", description="")
        session.get.side_effect = [response({"hits": 101, "jobs": [job(i) for i in range(100)]}),
                                   response({"hits": 101, "jobs": [job(100)]})]
        jobs = job_sources.fetch_amazon(session, self.source)
        self.assertEqual(len(jobs), 101)
        self.assertTrue(tracker.eligible(jobs[0]))
        self.assertEqual(session.get.call_args.kwargs["params"]["offset"], 100)

    def test_wrong_country_fails(self):
        session = Mock()
        session.get.return_value = response({"hits": 1, "jobs": [{"country_code": "USA"}]})
        with self.assertRaisesRegex(ValueError, "Singapore filter"):
            job_sources.fetch_amazon(session, self.source)


class GoogleTests(unittest.TestCase):
    source = dict(type="google", slug="google", company="Google")
    card = '''<li class="lLd3Je"><h3>Software Engineer</h3>
      <span class="r0wTof">Singapore</span><span class="wVSTAb">Early</span>
      <ul><li>Bachelor degree</li></ul>
      <a href="jobs/results/123-software-engineer" aria-label="Learn more about Software Engineer"></a></li>'''

    def test_nested_qualifications_and_next_page(self):
        session = Mock()
        first, second = Mock(), Mock()
        first.text = self.card + '<a aria-label="Go to next page"></a>'
        second.text = "No jobs found"
        session.get.side_effect = [first, second]
        jobs = job_sources.fetch_google(session, self.source)
        self.assertEqual(len(jobs), 1)
        self.assertTrue(tracker.eligible(jobs[0]))
        self.assertEqual(jobs[0]["location"], "Singapore")
        self.assertEqual(session.get.call_count, 2)

    def test_repeated_page_fails(self):
        session = Mock()
        session.get.return_value.text = self.card + '<a aria-label="Go to next page"></a>'
        with self.assertRaisesRegex(ValueError, "repeated page"):
            job_sources.fetch_google(session, self.source)

    def test_changed_markup_fails(self):
        session = Mock()
        session.get.return_value.text = "Please enable JavaScript"
        with self.assertRaisesRegex(ValueError, "cards missing"):
            job_sources.fetch_google(session, self.source)


class EmployerSearchTests(unittest.TestCase):
    def test_employer_directory_has_explicit_coverage(self):
        root = Path(__file__).parent
        config = json.loads((root / "config.json").read_text())
        companies = json.loads((root / "employers.json").read_text())
        source_keys = {s["type"] + ":" + s["slug"].lower() for s in config["sources"]}
        self.assertEqual(len(source_keys), len(config["sources"]))
        self.assertEqual(len(companies), 63)
        for company in companies:
            self.assertTrue(company["careers_url"].startswith("https://"))
            if company["coverage"] == "direct":
                self.assertTrue(company["sources"])
                self.assertTrue(set(company["sources"]).issubset(source_keys))
            else:
                self.assertEqual(company["coverage"], "adzuna_optional")
                self.assertIn(company.get("adzuna_company", company["company"]), config["adzuna_companies"])

    def test_adzuna_uses_employer_parameter(self):
        session = Mock()
        session.get.return_value = response({"results": []})
        with patch.dict("os.environ", {"ADZUNA_APP_ID": "test", "ADZUNA_APP_KEY": "test"}), patch.object(tracker.time, "sleep"):
            tracker.fetch_adzuna(session, {"adzuna_companies": ["Microsoft"]})
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["company"], "Microsoft")
        self.assertEqual(params["what"], "")

    def test_business_development_is_not_a_technical_role(self):
        self.assertFalse(tracker.eligible(dict(title="Business Development Manager", location="Singapore", experience_level="Entry level")))

    def test_quant_research_and_trading_internships(self):
        for title in ("Quantitative Research Intern", "Quantitative Trading Internship", "Quant Researcher, New Grad", "Technology Intern - 2027 - Singapore"):
            self.assertTrue(tracker.eligible(dict(title=title, location="Singapore")))
        self.assertFalse(tracker.eligible(dict(title="Quantitative Research Intern", location="London")))

    def test_greenhouse_uses_new_grad_metadata(self):
        session = Mock()
        session.get.return_value = response({"jobs": [{"id": 123, "title": "Software Engineer, 2027 Grads",
            "location": {"name": "Singapore"}, "metadata": [{"name": "Job Type", "value": ["Full-Time: New Grad"]}]}]})
        job = tracker.fetch_greenhouse(session, {"slug": "demo", "company": "Demo"})[0]
        self.assertTrue(tracker.eligible(job))


class RecruiteeTests(unittest.TestCase):
    source = dict(type="recruitee", slug="ogp", company="OGP", base_url="https://careers.open.gov.sg")

    def test_student_metadata_and_secondary_location(self):
        session = Mock()
        session.get.return_value = response({"offers": [{"id": 1, "title": "Software Engineer",
            "location": "Remote", "locations": [{"city": "Singapore", "country": "Singapore"}],
            "experience_code": "student", "description": "Build public services", "careers_url": "https://careers.open.gov.sg/o/test"}]})
        jobs = job_sources.fetch_recruitee(session, self.source)
        self.assertTrue(tracker.eligible(jobs[0]))
        self.assertEqual(jobs[0]["category"], "Internship")

    def test_incomplete_feed_fails(self):
        session = Mock()
        session.get.return_value = response({"offers": [], "has_more": True})
        with self.assertRaises(ValueError):
            job_sources.fetch_recruitee(session, self.source)
