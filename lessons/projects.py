"""Project folders whose sessions are never read: one pattern a line in ~/.lessons/exclude-projects
(LESSONS_EXCLUDE_FILE to move it). A plain path covers that folder and everything below it; a pattern with
* ? or [ is a glob on the folder path or any folder above it, without case (a path that exists is plain).
Folders are compared after symlinks are resolved. Work on a project often starts in the home folder, so words
count too, without case: `session:<word>` skips every session that mentions the word in any turn, tool output
included; `text:<word>` drops only the moments whose rendered parts (turns, tool names, project) mention it.
With any line set, a session whose folder is unknown is skipped too."""
from __future__ import annotations
import fnmatch, os
from typing import Callable, Iterable, Iterator

from .model import Session

DEFAULT_FILE = "~/.lessons/exclude-projects"
TEXT, SESSION = "text:", "session:"


def default_file() -> str:
    return os.path.expanduser(os.environ.get("LESSONS_EXCLUDE_FILE") or DEFAULT_FILE)


def load(path: str | None = None) -> list[str]:
    try:
        with open(path or default_file(), encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh]
    except FileNotFoundError:
        return []
    return [os.path.expanduser(ln) for ln in lines if ln and not ln.startswith("#")]


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _below(folder: str, root: str) -> bool:
    return folder == root or folder.startswith(root.rstrip(os.sep) + os.sep)


def excluded(cwd: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    if not cwd:
        return True
    folders = {os.path.normpath(os.path.expanduser(cwd)), _real(cwd)}
    for p in patterns:
        if p.startswith((TEXT, SESSION)):
            continue
        if any(ch in p for ch in "*?[") and not os.path.exists(p):
            pat = p.rstrip(os.sep).lower()
            for f in folders:
                parts = f.split(os.sep)
                if any(fnmatch.fnmatchcase(os.sep.join(parts[:i]).lower(), pat) for i in range(2, len(parts) + 1)):
                    return True
        elif any(_below(f, root) for f in folders for root in {os.path.normpath(p), _real(p)}):
            return True
    return False


def words(patterns: list[str], kinds: tuple = (TEXT, SESSION)) -> list[str]:
    out = []
    for p in patterns:
        for k in kinds:
            if p.startswith(k) and p[len(k):].strip():
                out.append(p[len(k):].strip().lower())
    return out


def mentions(texts: Iterable[str], terms: list[str]) -> bool:
    return bool(terms) and any(t in (x or "").lower() for x in texts for t in terms)


def excluded_session(s: Session, patterns: list[str]) -> bool:
    texts = [s.cwd] + [x for t in s.turns for x in (t.text, t.tool)]
    return excluded(s.cwd, patterns) or mentions(texts, words(patterns, (SESSION,)))


def moment_texts(m: dict) -> list[str]:
    """Everything of a moment record that a prompt or a lesson can carry."""
    turns = [m.get("trigger") or {}] + list(m.get("context") or []) + list(m.get("followup") or [])
    out = [str(m.get(k) or "") for k in ("project", "cwd", "kind", "source", "session")]
    for t in turns:
        if isinstance(t, dict):
            out += [str(t.get(k) or "") for k in ("text", "tool", "ref")]
    return out


def lesson_texts(l: dict) -> list[str]:
    out = [str(l.get(k) or "") for k in ("id", "title", "trigger", "do", "why", "check", "scope", "body")]
    out += [str(x) for x in l.get("themes") or []]
    for e in l.get("evidence") or []:
        if isinstance(e, dict):
            out += [str(v) for v in e.values()]
    return out


def lesson_excluded(l: dict, patterns: list[str]) -> bool:
    if not patterns:
        return False
    folders = [str(e.get("project") or "") for e in l.get("evidence") or [] if isinstance(e, dict)]
    scope = str(l.get("scope") or "")
    if scope.startswith("project:"):
        folders.append(scope[len("project:"):].strip())
    return any(f and excluded(f, patterns) for f in folders) or mentions(lesson_texts(l), words(patterns))


def matching_lessons(lessons: list[dict], patterns: list[str]) -> list[dict]:
    return [l for l in lessons if lesson_excluded(l, patterns)]


def allowed_lessons(lessons: list[dict], patterns: list[str]) -> list[dict]:
    return [l for l in lessons if not lesson_excluded(l, patterns)]


def only_allowed(read: Callable[[], Iterator[Session]], patterns: list[str], counts: dict,
                 name: str) -> Callable[[], Iterator[Session]]:
    def sessions() -> Iterator[Session]:
        for s in read():
            if excluded_session(s, patterns):
                counts[name] = counts.get(name, 0) + 1
                continue
            yield s
    return sessions


SKIPPED: dict = {}  # sessions skipped per source in this process, for `scan` to report


def sources(table: dict, patterns: list[str] | None = None) -> dict:
    pats = load() if patterns is None else patterns
    return {name: only_allowed(read, pats, SKIPPED, name) for name, read in table.items()}
