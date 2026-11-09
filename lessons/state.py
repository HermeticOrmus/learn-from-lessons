"""Incremental state for `dream`: which moments have already been turned into candidates, and which sessions
were unchanged since the last look, so a second run only pays for what is new.

Stored as JSON in `<home>/state.json` (default `~/.lessons/state.json`). Keys are hashes, never transcript text.
"""
from __future__ import annotations
import hashlib, json, os, tempfile, time

VERSION = 1


def digest(*parts: object) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()[:20]


def moment_key(m: dict) -> str:
    """A moment is identified by where its triggering turn lives (and what it said, in case refs shift)."""
    t = m.get("trigger") or {}
    return digest(m.get("source", ""), m.get("session", ""), t.get("ref", ""), (t.get("text") or "")[:500])


class State:
    def __init__(self, path: str):
        self.path = path
        self.data = {"version": VERSION, "moments": {}, "sessions": {}, "runs": []}
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    # moments ---------------------------------------------------------------
    def seen(self, key: str) -> bool:
        return key in self.data["moments"]

    def mark(self, keys, run: str) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for k in keys:
            self.data["moments"][k] = {"run": run, "at": now}

    # sessions --------------------------------------------------------------
    def session_unchanged(self, sid: str, fingerprint: str) -> bool:
        return self.data["sessions"].get(sid) == fingerprint

    def set_session(self, sid: str, fingerprint: str) -> None:
        self.data["sessions"][sid] = fingerprint

    # runs ------------------------------------------------------------------
    def add_run(self, info: dict) -> None:
        self.data["runs"] = (self.data.get("runs") or [])[-49:] + [info]

    def save(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
