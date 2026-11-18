#!/usr/bin/env python3
"""One bounded cloud run; private state is authenticated and encrypted before Git persistence."""
import argparse
import json
import logging
import os
from pathlib import Path
import time
import zlib
import requests

from cryptography.fernet import Fernet, InvalidToken

import agent
import tracker

ROOT = Path(__file__).resolve().parent
TABLES = {
    "applications": ("uid", "status", "notes", "updated_at"),
    "assessments": ("cache_key", "result"),
    "history": ("id", "role", "content"),
    "settings": ("key", "value"),
    "drafts": ("id", "uid", "body", "created_at"),
}
MAX_STATE = 8 * 1024 * 1024


def snapshot(store):
    tables = {name: [dict(r) for r in store.db.execute(f"SELECT * FROM {name} ORDER BY 1")]
              for name in TABLES}
    return json.dumps({"version": 1, "tables": tables,
        "descriptions": agent.read_json(store.home / "descriptions.json", {})},
        sort_keys=True, separators=(",", ":")).encode()


def restore(store, path, cipher):
    if not path.exists():
        return
    if path.stat().st_size > MAX_STATE:
        raise ValueError("Encrypted state exceeds the size limit")
    try:
        compressed = cipher.decrypt(path.read_bytes())
    except InvalidToken:
        raise ValueError("State decryption failed; restore the original AGENT_STATE_KEY. State was not reset.") from None
    unpacker = zlib.decompressobj()
    raw = unpacker.decompress(compressed, MAX_STATE + 1)
    if len(raw) > MAX_STATE or not unpacker.eof:
        raise ValueError("Invalid or oversized encrypted state")
    state = json.loads(raw)
    if state.get("version") != 1 or set(state.get("tables", {})) != set(TABLES):
        raise ValueError("Unsupported encrypted state format")
    # Fixed table/column identifiers; values are parameterized.
    with store.db:
        for name, columns in TABLES.items():
            rows = state["tables"][name]
            if not isinstance(rows, list) or any(set(row) != set(columns) for row in rows):
                raise ValueError("Invalid encrypted table data")
            store.db.execute(f"DELETE FROM {name}")
            store.db.executemany(f"INSERT INTO {name} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                                 [tuple(row[c] for c in columns) for row in rows])
    tracker.atomic_json(store.home / "descriptions.json", state.get("descriptions", {}))


def save(store, path, cipher, before):
    raw = snapshot(store)
    if raw == before and path.exists():
        return False
    if len(raw) > MAX_STATE:
        raise ValueError("Private state exceeds 8 MiB; export/trim old drafts before continuing")
    encrypted = cipher.encrypt(zlib.compress(raw, 9))
    if len(encrypted) > MAX_STATE:
        raise ValueError("Encrypted state exceeds size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(encrypted)
    temp.replace(path)
    return True


def prune(store):
    # Preserve application history and drafts; bound disposable model caches.
    store.db.execute("DELETE FROM assessments WHERE rowid NOT IN (SELECT rowid FROM assessments ORDER BY rowid DESC LIMIT 250)")
    store.db.commit()
    jobs = store.jobs()
    descriptions = agent.read_json(store.home / "descriptions.json", {})
    descriptions = {uid: desc for uid, desc in descriptions.items()
                    if uid in jobs and jobs[uid].get("status") == "Open"}
    tracker.atomic_json(store.home / "descriptions.json", descriptions)


def poll_once(bot, telegram, limit=10):
    offset = int(bot.store.setting("telegram_offset", "0"))
    updates = telegram.call("getUpdates", {"offset": offset, "timeout": 0, "limit": limit,
                                           "allowed_updates": ["message"]})
    for update in updates:
        agent.process_update(bot, telegram, update)


def tick(bot, telegram, clock=None):
    timestamp = time.time() if clock is None else clock
    # Handle queued messages before a potentially slow scan. At most ten updates per run.
    try:
        poll_once(bot, telegram)
    except RuntimeError:
        logging.warning("Telegram poll failed; unacknowledged updates will be retried.")
    last_scan = float(bot.store.setting("cloud_scan_at", "0"))
    scanned = False
    if timestamp - last_scan >= 7200:
        try:
            agent.scan(bot.store)
            bot.store.set_setting("cloud_scan_at", timestamp)
            scanned = True
        except (RuntimeError, ValueError, requests.RequestException):
            logging.warning("Job scan failed; preserving prior listings and retrying next run.")
    last_digest = float(bot.store.setting("last_digest", "0"))
    if timestamp - last_digest >= 86400:
        telegram.send(agent.digest(bot))
        bot.store.set_setting("last_digest", timestamp)
    elif scanned:
        # Free keyword notifications between daily AI digests.
        notified = set(json.loads(bot.store.setting("cloud_notified", "[]")))
        matches = [j for j in bot.search(limit=20) if j["uid"] not in notified]
        if matches:
            telegram.send("New shortlist entries (keyword matching):\n\n" + "\n\n".join(
                f"{j['title']} — {j['company']} | {j['provider']}\nID: {j['uid']}\n{j['url']}" for j in matches[:5]))
            notified.update(j["uid"] for j in matches[:5])
            bot.store.set_setting("cloud_notified", json.dumps(sorted(notified)))
    if not bot.store.setting("cloud_notified"):
        bot.store.set_setting("cloud_notified", json.dumps([j["uid"] for j in bot.search(limit=20)]))


def run():
    key = os.getenv("AGENT_STATE_KEY", "")
    if not key:
        raise ValueError("Set AGENT_STATE_KEY before enabling cloud mode")
    cipher = Fernet(key.encode())
    state_path = ROOT / "data" / "agent-state.enc"
    marker = ROOT / "data" / "agent-state.initialized"
    if marker.exists() and not state_path.exists():
        raise ValueError("Encrypted state is missing; restore it from Git rather than silently resetting applications")
    profile = json.loads(os.getenv("AGENT_PROFILE_JSON", "{}"))
    if not isinstance(profile, dict) or not profile:
        raise ValueError("Set AGENT_PROFILE_JSON to a structured career profile")
    store = agent.Store()
    try:
        restore(store, state_path, cipher)
        before = snapshot(store)
        tracker.atomic_json(store.home / "profile.json", profile)
        store.profile()  # Validate before network calls or writes to public exports.
        bot, telegram = agent.Agent(store), agent.Telegram()
        # Save even after a Telegram failure so completed commands and quota reservations survive.
        try:
            tick(bot, telegram)
        finally:
            prune(store)
            save(store, state_path, cipher, before)
            marker.write_text("v1\n", encoding="utf-8")
    finally:
        store.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "keygen"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.command == "keygen":
            path = ROOT / "private" / "cloud-key.txt"
            path.parent.mkdir(exist_ok=True)
            # Never replace an existing encryption key.
            with path.open("x", encoding="utf-8") as handle:
                handle.write(Fernet.generate_key().decode())
            print(f"Saved key to {path}. Add its contents as the AGENT_STATE_KEY GitHub secret.")
        else:
            run()
    except (ValueError, RuntimeError, OSError, zlib.error):
        parser.exit(1, "Cloud run failed. Check required secrets, original encryption key, state size and service access. Private state was not printed.\n")


if __name__ == "__main__":
    main()
