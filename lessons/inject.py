"""Put the lesson index into CLAUDE.md / AGENTS.md between markers. Dry-run (a diff) unless apply=True.
Nothing outside the markers is ever changed."""
from __future__ import annotations
import difflib, os

START = "<!-- learn-from-lessons:start -->"
END = "<!-- learn-from-lessons:end -->"


def render_block(body: str) -> str:
    return f"{START}\n{body.rstrip()}\n{END}"


def splice(original: str, body: str) -> str:
    """Replace the marked block, or append one if there is none."""
    block = render_block(body)
    s = original.find(START)
    e = original.find(END, s + len(START)) if s != -1 else -1
    if s != -1 and e != -1:
        return original[:s] + block + original[e + len(END):]
    if s != -1 or original.find(END) != -1:
        raise ValueError("found only one learn-from-lessons marker; fix the file by hand")
    if not original:
        return block + "\n"
    sep = "" if original.endswith("\n\n") else ("\n" if original.endswith("\n") else "\n\n")
    return original + sep + block + "\n"


def diff(target: str, body: str) -> tuple[str, str, str]:
    """(old text, new text, unified diff)."""
    try:
        with open(target, encoding="utf-8") as fh:
            old = fh.read()
    except FileNotFoundError:
        old = ""
    new = splice(old, body)
    d = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                     fromfile=f"a/{os.path.basename(target)}", tofile=f"b/{os.path.basename(target)}"))
    return old, new, d


def inject(target: str, body: str, apply: bool = False) -> str:
    old, new, d = diff(target, body)
    if apply and new != old:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(new)
    return d
