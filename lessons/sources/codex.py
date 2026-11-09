"""Codex CLI: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl."""
from __future__ import annotations
import glob, json, os
from typing import Iterator
from ..model import Session, Turn

ROOT = os.path.expanduser("~/.codex/sessions")
import re
# A tool turn failed only when it says so; "Script completed" with exit 0 and empty searches are not failures.
FAILED = re.compile(r'(?:exit(?:ed)?(?: with)?(?: code)?:?\s*[1-9]\d*\b|"exit_code":\s*[1-9]|"status":\s*"rejected"|Traceback \(most recent call last\)|command not found|No such file or directory)', re.I)
HARNESS = ("<environment_context>", "<user_instructions>", "# AGENTS.md", "<permissions", "<turn_aborted>", "<INSTRUCTIONS>")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and "text" in c)


def read(path: str) -> Session | None:
    sess = Session(source="codex", id=os.path.basename(path)[:-6], path=path)
    with open(path, errors="ignore") as fh:
        for n, line in enumerate(fh, 1):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p, ts, ref = d.get("payload") or {}, d.get("timestamp", ""), f"{path}:{n}"
            if d.get("type") == "session_meta":
                sess.id, sess.cwd, sess.started = p.get("id", sess.id), p.get("cwd", ""), p.get("timestamp", ts)
            elif d.get("type") == "turn_context" and not sess.cwd:
                sess.cwd = p.get("cwd", "")
            if d.get("type") != "response_item":
                continue
            kind = p.get("type")
            if kind == "message" and p.get("role") in ("user", "assistant"):
                text = _text(p.get("content")).strip()
                if not text or (p["role"] == "user" and text.startswith(HARNESS)):
                    continue
                sess.turns.append(Turn(p["role"], text, ts, ref=ref))
            elif kind in ("function_call", "custom_tool_call"):
                args = p.get("arguments") or p.get("input") or ""
                sess.turns.append(Turn("assistant", str(args)[:600], ts, tool=p.get("name", ""), ref=ref))
            elif kind in ("function_call_output", "custom_tool_call_output"):
                out = p.get("output")
                out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                failed = bool(FAILED.search(out[:4000]))
                sess.turns.append(Turn("tool", out[:2000], ts, error=failed, ref=ref))
    return sess


def sessions(root: str = ROOT) -> Iterator[Session]:
    for path in sorted(glob.iglob(os.path.join(root, "**", "*.jsonl"), recursive=True)):
        s = read(path)
        if s and s.turns:
            yield s
