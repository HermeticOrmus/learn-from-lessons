"""INDEX.md: the small layer an agent always sees. Verified lessons only, one line each, at most 200 lines.
Everything else stays on disk for `lessons search` / the MCP server to find on demand."""
from __future__ import annotations
import os
from . import store

MAX_LINES = 200
KIND_ORDER = ["pitfall", "workflow", "preference", "communication", "fact"]


def _scope_rank(scope: str) -> tuple:
    if scope.startswith("project:"):
        return (0, scope)
    if scope == "global":
        return (1, scope)
    return (2, scope)


def _heading(scope: str) -> str:
    if scope.startswith("project:"):
        return f"Project `{scope[len('project:'):].strip()}`"
    if scope.startswith("tool:"):
        return f"Tool: {scope[len('tool:'):].strip()}"
    return scope.capitalize() or "Global"


def _link(d: dict, link_base: str | None) -> str:
    p = d["abspath"]
    if link_base:
        return os.path.relpath(p, link_base)
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home + os.sep) else p


def _one_line(s) -> str:
    return " ".join(str(s or "").split())


def build(lessons: list[dict] | None = None, cwd: str | None = None, root: str | None = None,
          link_base: str | None = None, max_lines: int = MAX_LINES) -> str:
    """Markdown for INDEX.md. `cwd` decides which project lessons appear; links are relative to link_base
    (or ~-absolute when None)."""
    lessons = lessons if lessons is not None else store.load(root)
    keep = [d for d in lessons if d.get("status") == "verified" and store.scope_matches(d.get("scope", ""), cwd)]
    # When over budget, the lessons seen in the most sessions survive.
    def seen(d: dict) -> int:
        try:
            return int(d.get("seen") or 0)
        except (TypeError, ValueError):
            return 0
    keep.sort(key=lambda d: (-seen(d), str(d.get("id", ""))))
    header = ["# Lessons", "",
              "Verified lessons from your past agent sessions. Read the linked file before acting on one;",
              "run `lessons search \"<task>\"` for candidates and details.", ""]
    budget = max_lines - len(header) - 2
    groups: dict[tuple, list[dict]] = {}
    lines_needed = 0
    chosen, dropped = [], 0
    for d in keep:
        g = (str(d.get("scope", "global")), str(d.get("kind", "fact")))
        cost = 1 + (0 if g in groups else 2) + (0 if any(x[0] == g[0] for x in groups) else 2)
        if lines_needed + cost > budget:
            dropped += 1
            continue
        groups.setdefault(g, []).append(d)
        lines_needed += cost
        chosen.append(d)
    out = list(header)
    if not chosen:
        out.append("_No verified lessons yet. Run `lessons dream` to extract some._")
    scopes = sorted({g[0] for g in groups}, key=_scope_rank)
    for sc in scopes:
        out += [f"## {_heading(sc)}", ""]
        kinds = sorted({g[1] for g in groups if g[0] == sc},
                       key=lambda k: (KIND_ORDER.index(k) if k in KIND_ORDER else 99, k))
        for kind in kinds:
            out.append(f"### {kind}")
            for d in sorted(groups[(sc, kind)], key=lambda d: str(d.get("title", ""))):
                trig = _one_line(d.get("trigger"))
                out.append(f"- [{_one_line(d.get('title') or d.get('id'))}]({_link(d, link_base)})"
                           + (f" — {trig}" if trig else ""))
            out.append("")
    if dropped:
        out.append(f"_{dropped} more verified lessons omitted to stay under {max_lines} lines; "
                   "use `lessons search`._")
    while out and not out[-1]:
        out.pop()
    return "\n".join(out[:max_lines]) + "\n"


def write(root: str | None = None, cwd: str | None = None, out: str | None = None) -> str:
    root = os.path.expanduser(root or store.DEFAULT_DIR)
    out = out or os.path.join(root, "INDEX.md")
    text = build(root=root, cwd=cwd, link_base=os.path.dirname(os.path.abspath(out)))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    return out
