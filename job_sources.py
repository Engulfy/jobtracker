"""Public employer career-site connectors (no accounts or credentials)."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import re
from urllib.parse import urljoin


def record(source, provider, job_id, title, location, description, url, **extra):
    key = f"{source['type']}:{source['slug'].lower()}"
    return dict(uid=f"{key}:{job_id}", source=key, provider=provider,
                company=source["company"], title=title, location=location,
                description=description, url=url, posted_at="", updated_at="",
                employment_type="", **extra)


def fetch_workday(session, source, candidate_filter=lambda title: True):
    tenant, site = source["slug"], source["site"]
    host = f"https://{tenant}.{source['cluster']}.myworkdayjobs.com"
    base = f"{host}/wday/cxs/{tenant}/{site}"
    rows, paths = [], set()
    for offset in range(0, 2000, 20):
        response = session.post(base + "/jobs", json={"appliedFacets": {}, "limit": 20,
                                "offset": offset, "searchText": "Singapore"}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("jobPostings"), list) or not isinstance(payload.get("total"), int):
            raise ValueError("Unexpected Workday listing response")
        page = payload["jobPostings"]
        if not page and offset < payload["total"]:
            raise ValueError("Incomplete Workday pagination")
        for job in page:
            path = job["externalPath"]
            if path in paths:
                raise ValueError("Workday returned a repeated page")
            paths.add(path)
            rows.append(job)
        if offset + len(page) >= payload["total"]:
            break
    else:
        raise ValueError("Workday exceeded 2,000 search results")

    def detail(job):
        # Search can match descriptions as well as locations. Always verify the
        # actual location, including multi-location jobs, using the detail feed.
        response = session.get(base + job["externalPath"], timeout=20)
        response.raise_for_status()
        info = response.json().get("jobPostingInfo")
        if not isinstance(info, dict) or not info.get("jobReqId"):
            raise ValueError("Unexpected Workday detail response")
        if info.get("canApply") is False or info.get("posted") is False:
            return None
        location = "; ".join([info.get("location") or "", *(info.get("additionalLocations") or [])])
        result = record(source, "Workday", info["jobReqId"], info["title"], location,
                        info.get("jobDescription") or "", host + "/" + site + job["externalPath"])
        result["employment_type"] = info.get("timeType") or ""
        result["posted_at"] = info.get("startDate") or ""
        return result

    # Skip explicitly senior and nontechnical titles before fetching details.
    candidates = [j for j in rows if candidate_filter(j.get("title") or "")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [job for job in pool.map(detail, candidates) if job is not None]


def fetch_amazon(session, source):
    out, seen = [], set()
    for offset in range(0, 2000, 100):
        response = session.get("https://www.amazon.jobs/en/search.json", params={
            "country": "SGP", "offset": offset, "result_limit": 100, "sort": "recent"}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("jobs"), list) or payload.get("error"):
            raise ValueError("Unexpected Amazon response")
        rows = payload["jobs"]
        for job in rows:
            if job.get("country_code") != "SGP":
                raise ValueError("Amazon ignored the Singapore filter")
            job_id = str(job.get("id_icims") or job["id"])
            if job_id in seen:
                raise ValueError("Amazon returned a repeated page")
            seen.add(job_id)
            result = record(source, "Amazon", job_id, job["title"], "Singapore",
                            " ".join(job.get(k) or "" for k in ("description", "basic_qualifications", "preferred_qualifications")),
                            f"https://www.amazon.jobs/en/jobs/{job_id}")
            result["employment_type"] = "Internship" if job.get("is_intern") else ""
            out.append(result)
        if offset + len(rows) >= int(payload["hits"]):
            return out
        if not rows:
            raise ValueError("Incomplete Amazon pagination")
    raise ValueError("Amazon exceeded 2,000 Singapore jobs")


def fetch_recruitee(session, source):
    """Read the public offers feed used by Open Government Products."""
    response = session.get(source["base_url"].rstrip("/") + "/api/offers/", timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload.get("offers"), list) or payload.get("has_more"):
        raise ValueError("Unexpected or incomplete Recruitee offers response")
    out = []
    for offer in payload["offers"]:
        locations = [offer.get("location") or ""]
        locations += [", ".join(str(loc.get(k) or "") for k in ("city", "country")) for loc in offer.get("locations") or []]
        job = record(source, "Recruitee", offer.get("id") or offer["slug"], offer["title"],
                     "; ".join(locations), (offer.get("description") or "") + " " + (offer.get("requirements") or ""),
                     offer["careers_url"])
        job["employment_type"] = offer.get("employment_type_code") or ""
        job["experience_level"] = {"entry_level": "Entry level", "student": "Internship"}.get(offer.get("experience_code"), "")
        if offer.get("experience_code") == "student":
            job["employment_type"] = "Internship"
        out.append(job)
    return out


class GoogleCards(HTMLParser):
    """Read rendered job cards without executing scripts or private RPCs."""
    def __init__(self):
        super().__init__()
        self.cards, self.card, self.depth = [], None, 0
        self.capture, self.capture_depth = None, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "li":
            if self.card is not None:
                self.depth += 1
            elif "lLd3Je" in classes:
                self.card = {"text": [], "locations": [], "levels": []}
                self.depth = 1
        if self.card is None:
            return
        if tag == "a" and re.match(r"(?:https://www.google.com/about/careers/applications/)?jobs/results/\d", attrs.get("href", "")):
            self.card["href"] = attrs["href"]
            self.card["title"] = attrs.get("aria-label", "").removeprefix("Learn more about ")
        if tag == "span":
            if self.capture:
                self.capture_depth += 1
            elif "r0wTof" in classes or "wVSTAb" in classes:
                self.capture = "locations" if "r0wTof" in classes else "levels"
                self.capture_depth = 1

    def handle_data(self, data):
        if self.card is not None:
            self.card["text"].append(data)
            if self.capture:
                self.card[self.capture].append(data)

    def handle_endtag(self, tag):
        if self.card is None:
            return
        if tag == "span" and self.capture:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                self.capture = None
        if tag == "li":
            self.depth -= 1
            if self.depth == 0:
                self.cards.append(self.card)
                self.card = None


def fetch_google(session, source):
    out, seen = [], set()
    for page in range(1, 31):
        response = session.get("https://www.google.com/about/careers/applications/jobs/results/", params={
            "location": "Singapore", "target_level": ["EARLY", "INTERN_AND_APPRENTICE"],
            "page": page, "hl": "en"}, timeout=20)
        response.raise_for_status()
        parser = GoogleCards()
        parser.feed(response.text)
        if not parser.cards:
            if re.search(r"(?:No jobs found|Your search did not match|0 jobs matched)", response.text, re.I):
                return out
            raise ValueError("Google job cards missing; career page may have changed")
        for card in parser.cards:
            match = re.search(r"jobs/results/(\d+)", card.get("href", ""))
            if not match or not card.get("title") or not card["locations"]:
                raise ValueError("Incomplete Google job card")
            job_id = match[1]
            if job_id in seen:
                raise ValueError("Google returned a repeated page")
            seen.add(job_id)
            job = record(source, "Google", job_id, card["title"], "; ".join(card["locations"]),
                         " ".join(card["text"]), urljoin("https://www.google.com/about/careers/applications/", card["href"]).split("?")[0])
            levels = " ".join(card["levels"])
            if "Early" in levels:
                job["experience_level"] = "Entry level"
            if "Intern" in levels:
                job["employment_type"] = "Internship"
            out.append(job)
        if not re.search(r'aria-label="(?:Go to )?[Nn]ext(?: page)?"', response.text):
            return out
    raise ValueError("Google exceeded 30 search pages")
