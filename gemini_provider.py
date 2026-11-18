"""Gemini REST adapter with persistent request limits; no paid fallback providers."""
import json
import os
import re
import time
from datetime import datetime, timezone

import requests

DEFAULT_MODEL = "gemini-3.5-flash-lite"


class AIUnavailable(RuntimeError):
    pass


class Gemini:
    def __init__(self, store):
        self.store = store
        self.key = os.getenv("GEMINI_API_KEY", "")
        self.model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        if not re.fullmatch(r"gemini-[a-z0-9.-]+", self.model):
            raise ValueError("Invalid GEMINI_MODEL")
        self.requests = 0
        self.last_request = 0.0

    def reserve(self):
        if not self.key:
            raise AIUnavailable("AI is off: add a free-tier GEMINI_API_KEY. Keyword commands still work.")
        day = datetime.now(timezone.utc).date().isoformat()
        budget = json.loads(self.store.setting("gemini_budget", "{}"))
        used = budget.get("used", 0) if budget.get("day") == day else 0
        daily = max(0, min(100, int(os.getenv("GEMINI_DAILY_REQUESTS", "15"))))
        per_run = max(0, min(12, int(os.getenv("GEMINI_RUN_REQUESTS", "6"))))
        if used >= daily or self.requests >= per_run:
            raise AIUnavailable("Free AI request allowance reached. Keyword commands still work; try AI again later.")
        if float(self.store.setting("gemini_cooldown", "0")) > time.time():
            raise AIUnavailable("Free model is cooling down after a quota limit. Try later; keyword commands still work.")
        if self.last_request:
            time.sleep(max(0, 13 - (time.monotonic() - self.last_request)))
        # Reserve before the network request: timeouts/errors consume the local allowance too.
        self.store.set_setting("gemini_budget", json.dumps({"day": day, "used": used + 1}))
        self.requests += 1
        self.last_request = time.monotonic()

    def request(self, payload):
        if len(json.dumps(payload)) > 100000:
            raise AIUnavailable("Request too large for the free agent. Shorten your profile or question.")
        self.reserve()
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.key}, json=payload, timeout=75)
        except requests.RequestException:
            raise AIUnavailable("Free model connection failed. Try later; keyword commands still work.") from None
        if response.status_code == 429:
            self.store.set_setting("gemini_cooldown", time.time() + 3600)
            raise AIUnavailable("Free model quota reached; pausing AI for one hour. Keyword commands still work.")
        if not response.ok:
            raise AIUnavailable(f"Free model unavailable (HTTP {response.status_code}). Check the key and model; no paid fallback was used.")
        try:
            candidates = response.json().get("candidates", [])
            if not candidates or candidates[0].get("finishReason") != "STOP":
                raise AIUnavailable("Free model returned a blocked or incomplete response. Try a shorter request.")
            return candidates[0]["content"]
        except (ValueError, KeyError, TypeError):
            raise AIUnavailable("Free model returned an invalid response.") from None

    def response(self, items, instructions, tools=None):
        contents, call_names = [], {}
        for item in items:
            kind = item.get("type")
            if kind == "gemini_content":
                # Keep all original parts and thought signatures unchanged.
                contents.append(item["content"])
            elif kind == "function_call":
                call_names[item["call_id"]] = item["name"]
            elif kind == "function_call_output":
                result = {"name": call_names[item["call_id"]], "response": {"result": json.loads(item["output"])}}
                if item["call_id"].startswith("native:"):
                    result["id"] = item["call_id"][7:]
                part = {"functionResponse": result}
                if contents and contents[-1]["role"] == "user" and any("functionResponse" in p for p in contents[-1]["parts"]):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
            elif not kind and item.get("role") in ("user", "assistant"):
                contents.append({"role": "model" if item["role"] == "assistant" else "user",
                                 "parts": [{"text": item["content"]}]})
        payload = {"systemInstruction": {"parts": [{"text": instructions}]}, "contents": contents,
                   "generationConfig": {"maxOutputTokens": 4096}}
        if tools:
            declarations = []
            for t in tools:
                schema = {k: v for k, v in t["parameters"].items() if k != "additionalProperties"}
                declarations.append({"name": t["name"], "description": t["description"], "parameters": schema})
            payload["tools"] = [{"functionDeclarations": declarations}]
        content = self.request(payload)
        output = [{"type": "gemini_content", "content": content}]
        for i, part in enumerate(content.get("parts", [])):
            if "functionCall" in part:
                call = part["functionCall"]
                call_id = "native:" + call["id"] if call.get("id") else f"local:{len(items)}:{i}"
                output.append({"type": "function_call", "name": call["name"],
                               "arguments": json.dumps(call.get("args", {})), "call_id": call_id})
        return {"output": output}

    @staticmethod
    def text(response):
        return "\n".join(part["text"] for item in response.get("output", []) if item.get("type") == "gemini_content"
                         for part in item["content"].get("parts", []) if "text" in part and not part.get("thought"))

    def generate(self, instruction, data):
        return self.text(self.response([{"role": "user", "content": json.dumps(data)}], instruction))

    def generate_json(self, instruction, data):
        content = self.request({"systemInstruction": {"parts": [{"text": instruction}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(data)}]}],
            "generationConfig": {"maxOutputTokens": 4096, "responseMimeType": "application/json"}})
        return json.loads("".join(p["text"] for p in content.get("parts", []) if "text" in p and not p.get("thought")))
