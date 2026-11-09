"""A tiny, dependency-free parser for lesson frontmatter (see docs/LESSON_FORMAT.md).

Handles what lesson files use: `key: scalar`, inline lists `[a, b]`, block lists `- item`, and block lists of
dicts (`- ref: x` followed by indented `source: y`). Scalars may be bare, "double" (with backslash escapes) or
'single' (with '' escapes); a ` # comment` after a bare value is dropped. Anything else is kept as text rather
than raising, because lesson files are written by agents and a viewer should still show them.
"""
from __future__ import annotations
import re

_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:(?:\s+(.*)|\s*)$")


def split(text: str) -> tuple[str, str]:
    """Return (frontmatter, body). No frontmatter -> ("", text)."""
    text = text.lstrip("﻿")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return "", text


def _strip_comment(s: str) -> str:
    """Drop a trailing ` # comment` outside quotes."""
    quote = ""
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and (i == 0 or s[i - 1] in " \t[,:"):
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        body = s[1:-1]
        out, i = [], 0
        esc = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "r": "\r"}
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                out.append(esc.get(body[i + 1], "\\" + body[i + 1]))
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def _scalar(raw: str):
    s = raw.strip()
    if not s:
        return ""
    if s[0] not in "\"'":
        s = _strip_comment(s)
    elif not (len(s) >= 2 and s[-1] == s[0]):
        # A quoted value followed by a comment: "x"  # note
        m = re.match(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*')\s*(?:#.*)?$""", s)
        if m:
            s = m.group(1)
    if s.startswith("[") and s.endswith("]"):
        return [_unquote(p) for p in _split_inline(s[1:-1]) if p.strip()]
    if s[0] not in "\"'" and re.fullmatch(r"-?\d+", s):
        return int(s)
    if s in ("null", "~"):
        return ""
    return _unquote(s)


def _split_inline(s: str) -> list[str]:
    parts, cur, quote = [], [], ""
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch == ",":
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return parts


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def parse(fm: str) -> dict:
    """Parse frontmatter text into a dict."""
    out: dict = {}
    lines = [ln.rstrip("\r\n") for ln in fm.splitlines()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or _indent(line) > 0:
            i += 1
            continue
        m = _KEY.match(line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), (m.group(2) or "")
        i += 1
        if _strip_comment(rest).strip() and rest.strip() not in ("|", ">"):
            out[key] = _scalar(rest)
            continue
        # Block value: collect indented lines (and `- ` items at column 0, which YAML allows).
        block = []
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > 0 or lines[i].startswith("- ")):
            block.append(lines[i])
            i += 1
        if rest.strip() in ("|", ">"):
            texts = [b.strip() for b in block]
            out[key] = ("\n" if rest.strip() == "|" else " ").join(texts).strip()
        else:
            out[key] = _block(block)
    return out


def _block(block: list[str]):
    items: list = []
    cur = None
    is_list = any(b.strip().startswith("- ") or b.strip() == "-" for b in block)
    if not is_list:
        return " ".join(b.strip() for b in block if b.strip())
    for b in block:
        s = b.strip()
        if not s or s.startswith("#"):
            continue
        if s == "-" or s.startswith("- "):
            item = s[1:].strip()
            m = _KEY.match(item) if item and item[0] not in "\"'" else None
            if m:
                cur = {m.group(1): _scalar(m.group(2) or "")}
                items.append(cur)
            else:
                cur = None
                items.append(_scalar(item))
            continue
        m = _KEY.match(s)
        if m and isinstance(cur, dict):
            cur[m.group(1)] = _scalar(m.group(2) or "")
        elif isinstance(cur, dict) and cur:
            # continuation of the previous value (a wrapped quote)
            last = list(cur)[-1]
            cur[last] = f"{cur[last]} {_unquote(s)}".strip()
    return items


def load(text: str) -> tuple[dict, str]:
    """Parse a lesson file into (meta, body)."""
    fm, body = split(text)
    return parse(fm), body
