"""Project folders whose sessions are never read: one pattern a line in ~/.lessons/exclude-projects
(LESSONS_EXCLUDE_FILE to move it). A plain path covers that folder and everything below it; a pattern with
* ? or [ is a glob on the whole folder path, without case. Work on a project often starts in the home folder,
so words count too, without case: `session:<word>` skips every session that mentions the word in any turn, tool
output included; `text:<word>` drops only the moments whose turns (the ones sent to the model) mention it.
With any line set, a session whose folder is unknown is skipped too."""
from __future__ import annotations
import fnmatch, os
from typing import Callable, Iterator

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


def excluded(cwd: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    if not cwd:
        return True
    folder = os.path.normpath(os.path.expanduser(cwd))
    for p in patterns:
        if p.startswith((TEXT, SESSION)):
            continue
        if any(ch in p for ch in "*?["):
            if fnmatch.fnmatchcase(folder.lower(), p.lower()):
                return True
        else:
            root = os.path.normpath(p)
            if folder == root or folder.startswith(root.rstrip(os.sep) + os.sep):
                return True
    return False


def words(patterns: list[str], kinds: tuple = (TEXT, SESSION)) -> list[str]:
    out = []
    for p in patterns:
        for k in kinds:
            if p.startswith(k) and p[len(k):].strip():
                out.append(p[len(k):].strip().lower())
    return out


def mentions(texts, terms: list[str]) -> bool:
    return bool(terms) and any(t in (x or "").lower() for x in texts for t in terms)


def excluded_session(s: Session, patterns: list[str]) -> bool:
    return excluded(s.cwd, patterns) or mentions((t.text for t in s.turns), words(patterns, (SESSION,)))


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
