#!/usr/bin/env python3
"""Singapore tech early-career job tracker. No scraping/login required."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from html import escape, unescape
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("sg-jobs")
UTC = timezone.utc
HEADERS = {"User-Agent": "SGTechEarlyCareerTracker/1.0 (personal monitoring)", "Accept": "application/json"}

# Match technologies in the actual job title, rather than any incidental skills
# mentioned in a non-technical job's description.
TECH = re.compile(
    r"\b(?:software|developer|development|programmer|full[ -]?stack|front[ -]?end|back[ -]?end|"
    r"site reliability|\bsre\b|devops|cloud|platform|infrastructure|\binfra\b|"
    r"cyber(?:security)?|security|penetration|vulnerabilit\w*|malware|"
    r"data (?:science|scientist|engineering|engineer|analyst|analytics)|"
    r"machine learning|\bml\b|\bai\b|artificial intelligence|deep learning|"
    r"computer vision|\bllm\b|\bgenai\b|automation|robotic\w*|"
    r"(?:test|quality assurance|\bqa\b) engineer|embedded|firmware|"
    r"systems? engineer|network engineer|quant(?:itative)? (?:developer|researcher|engineer)|"
    r"research engineer|research scientist|applied scientist|product engineer|product manager|product management|technical product|product design|"
    r"ui\s*[/&-]\s*ux|user experience|technology analyst|technology consultant|"
    r"technical analyst|information technology|\bit\b (?:engineer|analyst|support)|"
    r"solutions? engineer|mobile engineer|\bios\b developer|android developer|"
    r"cloud computing|linux|big data|database|algorithm\w*|blockchain|hardware|fpga|semiconductor|computer science|information systems|"
    r"security researcher|computing|web developer)\b",
    re.I,
)
INTERNSHIP = re.compile(r"\b(?:intern|interns|internship|internships|industrial attachment|co[ -]?op)\b", re.I)
GRAD_TITLE = re.compile(
    r"\b(?:new[ -]?grad(?:uate)?|graduate|graduating|fresh[ -]?grad(?:uate)?|"
    r"university grad(?:uate)?|entry[ -]?level|early[ -]?career|junior|"
    r"trainee|apprentice|associate|campus hire|analyst program(?:me)?)\b", re.I
)
GRAD_DESCRIPTION = re.compile(
    r"\b(?:recent (?:university )?graduate|fresh graduate|new graduate|"
    r"entry[ -]?level|no (?:prior )?(?:work )?experience required|"
    r"0\s*[-–to]{1,4}\s*2\s*years?|up to 2 years?|"
    r"less than 2 years?|(?:graduat(?:ing|ed)|graduation) (?:in|between) 20\d{2})\b", re.I
)
ENTRY_I = re.compile(r"\b(?:engineer|developer|scientist|analyst)\s+(?:i|1)\b", re.I)
SENIOR = re.compile(r"\b(?:senior|sr\.?|staff|principal|lead|director|head of|vp|vice president|"
                    r"managerial|engineering manager|manager,|architect|expert|chief|distinguished)\b", re.I)
SG = re.compile(r"\bsingapore\b|\bsg\b", re.I)
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")


def tidy(s: Any) -> str:
    return SPACE.sub(" ", unescape(TAG.sub(" ", str(s or "")))).strip()


def iso_utc(value: Any) -> str:
    """Parse timestamps when supplied. Return blank if unknown."""
    if value is None or value == "":
        return ""
    try:
        if isinstance(value, (int, float)):
            timestamp = float(value) / (1000 if float(value) > 1e11 else 1)
            return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat(timespec="seconds")
    except (ValueError, OverflowError, TypeError):
        LOGGER.debug("Unparseable date: %r", value)
    return ""


def make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "POST"])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update(HEADERS)
    return s


def get_json(session: requests.Session, url: str, params: dict | None = None) -> Any:
    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def fetch_greenhouse(session: requests.Session, source: dict) -> list[dict]:
    slug = source["slug"]
    payload = get_json(session, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                       {"content": "true"})
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("Unexpected Greenhouse response")
    return [dict(uid=f"greenhouse:{slug.lower()}:{j['id']}", company=source["company"],
                 provider="Greenhouse", source=f"greenhouse:{slug.lower()}", title=j.get("title", ""),
                 location=(j.get("location") or {}).get("name", ""),
                 description=tidy(j.get("content", "")),
                 url=j.get("absolute_url", ""),
                 posted_at="", updated_at=iso_utc(j.get("updated_at")),
                 employment_type="") for j in payload["jobs"] if j.get("id")]


def fetch_lever(session: requests.Session, source: dict) -> list[dict]:
    slug = source["slug"]
    all_rows = []
    # Lever endpoint pages with skip/limit rather than returning everything at once.
    limit = 100
    for skip in range(0, 2000, limit):
        rows = get_json(session, f"https://api.lever.co/v0/postings/{slug}",
                        {"mode": "json", "skip": skip, "limit": limit})
        if not isinstance(rows, list):
            raise ValueError("Unexpected Lever response")
        for j in rows:
            cats = j.get("categories") or {}
            desc = " ".join([str(j.get("descriptionPlain") or j.get("description") or ""),
                             str(j.get("additionalPlain") or ""),
                             str(j.get("lists") or "")])
            all_rows.append(dict(uid=f"lever:{slug.lower()}:{j['id']}", company=source["company"],
                                 provider="Lever", source=f"lever:{slug.lower()}",
                                 title=j.get("text", ""), location=cats.get("location", ""),
                                 description=tidy(desc), url=j.get("hostedUrl") or j.get("applyUrl") or "",
                                 posted_at=iso_utc(j.get("createdAt")),
                                 updated_at=iso_utc(j.get("updatedAt")),
                                 employment_type=cats.get("commitment", "")))
        if len(rows) < limit:
            return all_rows
        time.sleep(0.3)
    raise ValueError("Lever pagination exceeded 2,000 jobs: expand page limit")


def fetch_ashby(session: requests.Session, source: dict) -> list[dict]:
    slug = source["slug"]
    payload = get_json(session, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("Unexpected Ashby response")
    found = []
    for j in payload["jobs"]:
        if j.get("isListed") is False:
            continue
        other = []
        for loc in j.get("secondaryLocations") or []:
            if isinstance(loc, dict):
                address = loc.get("address") or {}
                other.append(loc.get("location") or address.get("addressLocality") or "")
            elif isinstance(loc, str):
                other.append(loc)
        job_url = j.get("jobUrl") or j.get("applyUrl") or ""
        job_id = j.get("id") or hashlib.sha256(job_url.encode()).hexdigest()[:20]
        found.append(dict(uid=f"ashby:{slug.lower()}:{job_id}", company=source["company"],
                          provider="Ashby", source=f"ashby:{slug.lower()}",
                          title=j.get("title", ""), location="; ".join([j.get("location") or "", *other]).strip("; "),
                          description=tidy(j.get("descriptionPlain") or j.get("descriptionHtml") or ""),
                          url=job_url, posted_at=iso_utc(j.get("publishedAt")),
                          updated_at=iso_utc(j.get("updatedAt")),
                          employment_type=j.get("employmentType") or ""))
    return found



def fetch_smartrecruiters(session: requests.Session, source: dict) -> list[dict]:
    """Read public postings on SmartRecruiters; country filter keeps volume low."""
    slug = source["slug"]
    out = []
    for offset in range(0, 2000, 100):
        payload = get_json(session, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                           {"limit": 100, "offset": offset, "country": "sg", "destination": "PUBLIC"})
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
            raise ValueError("Unexpected SmartRecruiters response")
        rows = payload["content"]
        for j in rows:
            loc = j.get("location") or {}
            parts = [loc.get("city") or "", loc.get("region") or "", loc.get("country") or ""]
            url = j.get("jobAdUrl") or j.get("applyUrl") or f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"
            out.append(dict(uid=f"smartrecruiters:{slug.lower()}:{j['id']}",
                            company=source["company"], provider="SmartRecruiters",
                            source=f"smartrecruiters:{slug.lower()}", title=j.get("name") or "",
                            location=", ".join(x for x in parts if x), description="",
                            url=url, posted_at=iso_utc(j.get("releasedDate")),
                            updated_at="", employment_type=(j.get("typeOfEmployment") or {}).get("label", "")
                            if isinstance(j.get("typeOfEmployment"), dict) else j.get("typeOfEmployment", ""),
                            experience_level=(j.get("experienceLevel") or {}).get("label", "")
                            if isinstance(j.get("experienceLevel"), dict) else j.get("experienceLevel", "")))
        if len(rows) < 100 or offset + len(rows) >= int(payload.get("totalFound", 0)):
            return out
        time.sleep(0.3)
    raise ValueError("SmartRecruiters pagination exceeded 2,000 Singapore jobs")

def fetch_adzuna(session: requests.Session, config: dict) -> list[dict]:
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        LOGGER.info("Adzuna skipped: add ADZUNA_APP_ID and ADZUNA_APP_KEY to expand coverage")
        return []
    all_rows = []
    for query in config.get("adzuna_queries", []):
        try:
            payload = get_json(session, "https://api.adzuna.com/v1/api/jobs/sg/search/1", {
                "app_id": app_id, "app_key": app_key, "results_per_page": 50,
                "what": query, "where": "Singapore", "sort_by": "date", "content-type": "application/json",
            })
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("Unexpected Adzuna response")
            for j in payload["results"]:
                location = j.get("location") or {}
                company = j.get("company") or {}
                all_rows.append(dict(uid=f"adzuna:{j['id']}",
                                     company=company.get("display_name") or "Unknown company",
                                     provider="Adzuna", source="adzuna", title=j.get("title") or "",
                                     location=location.get("display_name") or "Singapore",
                                     description=tidy(j.get("description")),
                                     url=j.get("redirect_url") or "",
                                     posted_at=iso_utc(j.get("created")), updated_at="", employment_type=""))
        except (requests.RequestException, ValueError, KeyError) as exc:
            LOGGER.warning("Adzuna query %r failed: %s", query, exc)
        time.sleep(0.25)
    return all_rows


def eligible(job: dict) -> bool:
    title, description = tidy(job.get("title")), tidy(job.get("description"))
    location = tidy(job.get("location"))
    if not SG.search(location) or not TECH.search(title):
        return False
    # A title with an explicitly senior scope is not an entry-level opportunity.
    if SENIOR.search(title):
        return False
    if INTERNSHIP.search(title) or INTERNSHIP.search(str(job.get("employment_type", ""))):
        job["category"] = "Internship"
        return True
    if (GRAD_TITLE.search(title) or ENTRY_I.search(title) or GRAD_DESCRIPTION.search(description)
            or GRAD_TITLE.search(str(job.get("experience_level") or ""))):
        job["category"] = "New grad / entry-level"
        return True
    return False


def priority(job: dict, config: dict) -> int:
    title = job.get("title", "").lower()
    score = 10 if job.get("category") == "New grad / entry-level" else 8
    score += sum(3 for term in config.get("priority_keywords", [])
                 if re.search(r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)", title))
    score += 5 if job.get("company", "").casefold() in {s.casefold() for s in config.get("priority_companies", [])} else 0
    return score


def dedupe_run(rows: list[dict]) -> list[dict]:
    """Deduplicate provider re-exports, keeping direct employer links ahead of Adzuna.

    Never collapse two different requisition IDs from the *same* ATS: they may
    represent separate application cycles. Repeated IDs are always removed.
    """
    unique: dict[str, dict] = {}
    for j in sorted(rows, key=lambda x: x.get("provider") == "Adzuna"):
        unique.setdefault(j["uid"], j)
    direct_signatures = set()
    def signature(j: dict) -> tuple[str, str]:
        company = re.sub(r"\b(?:pte|ltd|limited|inc|technologies|technology|corp|corporation)\b", "", j["company"], flags=re.I)
        return re.sub(r"[^a-z0-9]", "", company.lower()), re.sub(r"[^a-z0-9]", "", j["title"].lower())
    for job in unique.values():
        if job["provider"] != "Adzuna":
            direct_signatures.add(signature(job))
    return [job for job in unique.values() if job["provider"] != "Adzuna" or signature(job) not in direct_signatures]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "initialized": False, "initial_digest_sent": False, "jobs": {}}
    db = json.loads(path.read_text(encoding="utf-8"))
    if db.get("version") != 1 or not isinstance(db.get("jobs"), dict):
        raise ValueError("Invalid or unsupported state file")
    db.setdefault("initial_digest_sent", False)
    return db


def reconcile(db: dict, fetched: list[dict], succeeded: set[str], now: str, config: dict) -> list[dict]:
    created = []
    seen = set()
    for job in fetched:
        uid = job["uid"]
        seen.add(uid)
        job["score"] = priority(job, config)
        old = db["jobs"].get(uid)
        if old:
            was_closed = old.get("status") == "Closed"
            old.update(job)
            old.update(last_seen=now, missing_scans=0, status="Open")
            if was_closed:
                old["notified"] = False
                old["reopened_at"] = now
                old["event"] = "Reopened"
                created.append(old)
        else:
            job.update(first_seen=now, last_seen=now, missing_scans=0, status="Open",
                       notified=False, event="New")
            db["jobs"][uid] = job
            created.append(job)
    for uid, job in db["jobs"].items():
        if uid in seen:
            continue
        if job.get("source") in succeeded and job.get("provider") != "Adzuna" and job.get("status") != "Closed":
            job["missing_scans"] = job.get("missing_scans", 0) + 1
            if job["missing_scans"] >= 2:
                job["status"] = "Closed"
                job["closed_at"] = now
        elif job.get("provider") == "Adzuna" and job.get("status") == "Open":
            last = datetime.fromisoformat(job["last_seen"])
            if datetime.fromisoformat(now) - last >= timedelta(days=14):
                job["status"] = "Unverified"
    db["initialized"] = True
    db["last_scan"] = now
    return created


def telegram_message(session: requests.Session, token: str, chat_id: str, message: str) -> None:
    result = session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": message, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
    result.raise_for_status()
    if not result.json().get("ok"):
        raise RuntimeError(f"Telegram failed: {result.text[:200]}")


def job_line(job: dict) -> str:
    link = escape(str(job.get("url", "")), quote=True)
    label = escape(job["title"])
    via = ' — <a href="https://www.adzuna.co.uk/">Adzuna</a>' if job.get("provider") == "Adzuna" else ""
    return f"• <a href=\"{link}\">{label}</a> — {escape(job['company'])} ({escape(job['category'])}){via}"


def alert_jobs(session: requests.Session, db: dict, config: dict, notify: bool) -> None:
    if not notify:
        return
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        LOGGER.info("Telegram disabled: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return
    active = [j for j in db["jobs"].values() if j.get("status") == "Open"]
    active.sort(key=lambda x: (-x.get("score", 0), x["company"], x["title"]))
    dashboard = config.get("dashboard_url", "").strip()
    suffix = f'\n<a href="{escape(dashboard, quote=True)}">View all jobs on dashboard</a>' if dashboard.startswith("https://") else "\nView the dashboard / CSV in the repository."
    if not db.get("initial_digest_sent"):
        header = f"<b>🇸🇬 Initial Singapore tech jobs scan</b>\nFound {len(active)} matching openings. Some top matches:\n\n"
        top_lines = []
        for job in active[:15]:
            line = job_line(job)
            if len(header) + len("\n".join(top_lines)) + len(line) + len(suffix) >= 3900:
                break
            top_lines.append(line)
        msg = header + "\n".join(top_lines) + suffix
        telegram_message(session, token, chat_id, msg)
        db["initial_digest_sent"] = True
        for j in db["jobs"].values():
            j["notified"] = True
        LOGGER.info("Initial digest delivered")
        return

    pending = [j for j in active if not j.get("notified")]
    pending.sort(key=lambda x: (-x.get("score", 0), x["first_seen"]))
    pending = pending[:config.get("max_alerts_per_scan", 30)]
    for start in range(0, len(pending), 8):
        batch = pending[start:start + 8]
        msg = f"<b>🆕 {len(batch)} Singapore tech job opening(s) discovered</b>\n\n"
        msg += "\n".join(job_line(j) for j in batch)
        msg += suffix
        telegram_message(session, token, chat_id, msg)
        for j in batch:
            j["notified"] = True
        time.sleep(0.5)
    if pending:
        LOGGER.info("Delivered %d new/reopened job alerts", len(pending))


def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def write_exports(db: dict) -> None:
    records = list(db["jobs"].values())
    records.sort(key=lambda j: (j.get("status") != "Open", -j.get("score", 0), j.get("company", "")))
    public_fields = ["uid", "company", "title", "location", "category", "provider", "url", "posted_at",
                     "updated_at", "first_seen", "last_seen", "status", "score"]
    public = [{k: j.get(k, "") for k in public_fields} for j in records]
    atomic_json(ROOT / "docs" / "jobs.json", {"last_scan": db.get("last_scan", ""), "jobs": public})
    csv_path = ROOT / "docs" / "jobs.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=public_fields)
        writer.writeheader()
        writer.writerows(public)


def run(config: dict, notify: bool) -> None:
    session = make_session()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db = load_state(ROOT / "data" / "jobs.json")
    fetched, succeeded = [], set()
    providers = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "smartrecruiters": fetch_smartrecruiters}
    for source in config["sources"]:
        key = f"{source['type']}:{source['slug'].lower()}"
        try:
            rows = providers[source["type"]](session, source)
            # A suddenly empty board likely means a temporary broken response; don't close jobs.
            if not rows and any(j.get("source") == key and j.get("status") == "Open" for j in db["jobs"].values()):
                LOGGER.warning("Empty response for %s; preserving previous statuses", key)
            else:
                succeeded.add(key)
            fetched.extend([j for j in rows if eligible(j) and str(j.get("url", "")).startswith(("https://", "http://"))])
            LOGGER.info("%s: %d raw openings", key, len(rows))
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            LOGGER.warning("Failed source %s; preserving old results: %s", key, exc)
        time.sleep(0.25)
    if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"):
        for job in fetch_adzuna(session, config):
            if eligible(job) and str(job.get("url", "")).startswith(("https://", "http://")):
                fetched.append(job)
    else:
        LOGGER.info("Adzuna optional search is not configured")
    if not succeeded and not fetched:
        raise RuntimeError("No job sources returned data successfully. State unchanged; see source warnings above.")
    fetched = dedupe_run(fetched)
    # Keep long descriptions in memory only for eligibility checks. Avoid bloating
    # the persistent database and Git history every four hours.
    for job in fetched:
        job.pop("description", None)
        job.pop("experience_level", None)
    changes = reconcile(db, fetched, succeeded, now, config)
    LOGGER.info("Matched %d unique jobs; %d new/reopened; %d stored total", len(fetched), len(changes), len(db["jobs"]))
    try:
        alert_jobs(session, db, config, notify)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        LOGGER.error("Telegram alert failed; unsent jobs will be retried next scan: %s", exc)
    finally:
        atomic_json(ROOT / "data" / "jobs.json", db)
        write_exports(db)
    LOGGER.info("Dashboard and CSV exported")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config.json"), help="Config file path")
    parser.add_argument("--no-notify", action="store_true", help="Refresh database without Telegram")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open(args.config, encoding="utf-8") as f:
        configuration = json.load(f)
    run(configuration, notify=not args.no_notify)
