"""Claude Code: ~/.claude/projects/<project>/<session>.jsonl (subagents: agent-*.jsonl or isSidechain)."""
from __future__ import annotations
import glob, json, os
from typing import Iterator
from ..model import Session, Turn

ROOT = os.path.expanduser("~/.claude/projects")
# Text the harness writes into the user role: not something the person typed.
HARNESS = ("<command-name>", "<local-command", "<system-reminder>", "<task-notification>", "Caveat: The messages below",
           "This session is being continued", "<bash-input>", "<bash-stdout>", "[SYSTEM NOTIFICATION")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def read(path: str) -> Session | None:
    sess = Session(source="claude-code", id=os.path.basename(path)[:-6], path=path,
                   subagent=os.path.basename(path).startswith("agent-"))
    try:
        fh = open(path, errors="ignore")
    except OSError:
        return None
    with fh:
        for n, line in enumerate(fh, 1):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            kind = d.get("type")
            if kind == "queue-operation" and d.get("operation") == "enqueue":
                text = str(d.get("content") or "").strip()
                if text and not text.startswith(("<",) + HARNESS):  # typed by the person while the agent worked
                    sess.turns.append(Turn("user", text, d.get("timestamp", ""), ref=f"{path}:{n}"))
                continue
            if kind not in ("user", "assistant"):
                continue
            sess.cwd = sess.cwd or d.get("cwd", "")
            sess.started = sess.started or d.get("timestamp", "")
            if d.get("isSidechain"):
                sess.subagent = True
            msg = d.get("message") or {}
            content = msg.get("content")
            ts, ref = d.get("timestamp", ""), f"{path}:{n}"
            if kind == "user":
                if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            out = c.get("content")
                            out = _text(out) if not isinstance(out, str) else out
                            sess.turns.append(Turn("tool", out[:2000], ts, error=bool(c.get("is_error")), ref=ref))
                    continue
                text = _text(content).strip()
                if not text or d.get("isMeta") or text.startswith(HARNESS):
                    continue
                sess.turns.append(Turn("user", text, ts, ref=ref))
            else:
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text" and c.get("text", "").strip():
                        sess.turns.append(Turn("assistant", c["text"], ts, ref=ref))
                    elif c.get("type") == "tool_use":
                        args = json.dumps(c.get("input", {}), ensure_ascii=False)[:600]
                        sess.turns.append(Turn("assistant", args, ts, tool=c.get("name", ""), ref=ref))
    return sess


def sessions(root: str = ROOT) -> Iterator[Session]:
    for path in glob.iglob(os.path.join(root, "*", "*.jsonl")):
        s = read(path)
        if s and s.turns:
            yield s
