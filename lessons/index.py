"""INDEX.md: the small layer an agent always sees, one line per lesson.
`build_index` (v1): principles, then verified rules for a scenario, then matching project/tool instances.
`build` is the v0 layout (verified lessons grouped by scope and kind), kept for callers that use it.
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


def estimate_tokens(text: str) -> int:
    """Rough: ~4 characters per token for Latin text, ~1 token per CJK character."""
    cjk = sum(1 for ch in text if "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef")
    return int(round((len(text) - cjk) / 4 + cjk))


def _instance_scope_ok(d: dict, cwd: str | None, tool: str | None) -> bool:
    sc = str(d.get("scope", ""))
    if sc.startswith("project:"):
        return bool(cwd) and store.scope_matches(sc, cwd)
    if sc.startswith("tool:"):
        return bool(tool) and sc[len("tool:"):].strip() == tool
    return False  # a global instance is a rule in disguise; it stays on disk for search


def _line(d: dict, link_base: str | None, show_strength: bool = True) -> str:
    trig = _one_line(d.get("trigger"))
    tag = f" `{d['strength']}`" if show_strength and d.get("strength") == "must" else ""
    cand = " _(candidate)_" if d.get("level") != "principle" and d.get("status") != "verified" else ""
    return (f"- [{_one_line(d.get('title') or d.get('id'))}]({_link(d, link_base)}){tag}{cand}"
            + (f" — {trig}" if trig else ""))


def build_index(lessons: list[dict] | None = None, level: str = "rule", scenario: str | None = None,
                max_lines: int = 120, cwd: str | None = None, tool: str | None = None,
                include_candidates: bool = False, strength_min: str | None = None,
                link_base: str | None = None, root: str | None = None, all_projects: bool = False) -> str:
    """The injected layer, v1: principles first (always), then rules for `scenario` (verified unless
    include_candidates), grouped under their principle, then instances whose project (cwd) or tool scope
    matches. `level` caps how specific it gets. Deterministic; ends with a comment giving counts and a token
    estimate. Over budget, rules with strength `must` and most sessions seen survive."""
    lessons = lessons if lessons is not None else store.load(root)
    lessons = [d if "version" in d else store.normalize(dict(d)) for d in lessons]
    level = level if level in store.LEVELS else "rule"
    h = store.hierarchy(lessons)
    by_id = h["by_id"]

    def ok(d: dict) -> bool:
        return store.eligible(d, scenario, level, cwd, not include_candidates, strength_min,
                              any_scope=all_projects)

    principles = sorted((d for d in lessons if d["level"] == "principle" and ok(d)), key=lambda d: d["id"])
    rules = [d for d in lessons if d["level"] == "rule" and ok(d)]
    instances = [d for d in lessons if d["level"] == "instance" and ok(d) and
                 (all_projects or _instance_scope_ok(d, cwd, tool))]

    def prio(d: dict) -> tuple:
        return (store.STRENGTHS.index(d["strength"]), d.get("status") != "verified", -store._seen(d), d["id"])

    sc_txt = f" for scenario `{scenario}`" if scenario else ""
    header = ["# Lessons", "",
              f"Lessons from your past agent sessions{sc_txt}: predictions to follow unless the person says",
              "otherwise. Principles always apply; open a linked file before acting on a rule.",
              "`lessons search \"<task>\" --scenario <s>` finds the rest.", ""]
    footer_reserve = 3  # omitted note, blank, footer comment
    budget = max_lines - len(header) - footer_reserve

    out_p = ["## Principles", ""] + [_line(d, link_base, show_strength=False) for d in principles] + [""] \
        if principles else []
    budget -= len(out_p)

    # Rules: keep by priority within the budget, then lay out grouped by principle.
    kept_r, kept_i, dropped = [], [], 0
    groups: set = set()
    used = 2  # "## Rules" + blank
    for d in sorted(rules, key=prio):
        g = d["parent"] if d["parent"] in by_id else ""
        cost = 1 + (0 if g in groups else 2)
        if used + cost > budget:
            dropped += 1
            continue
        groups.add(g)
        used += cost
        kept_r.append(d)
    if not kept_r:
        used = 0
    if instances:
        used += 2
    for d in sorted(instances, key=prio):
        if used + 1 > budget:
            dropped += 1
            continue
        used += 1
        kept_i.append(d)

    out = list(header) + out_p
    if kept_r:
        out += ["## Rules", ""]
        order = sorted(groups, key=lambda g: (g == "", g))
        for g in order:
            out.append(f"### {g}" if g else "### other")
            for d in sorted((d for d in kept_r if (d["parent"] if d["parent"] in by_id else "") == g),
                            key=lambda d: (store.STRENGTHS.index(d["strength"]), d["id"])):
                out.append(_line(d, link_base))
            out.append("")
    if kept_i:
        out += ["## Project and tool instances", ""]
        for d in sorted(kept_i, key=lambda d: (str(d.get("scope", "")), d["id"])):
            out.append(_line(d, link_base))
        out.append("")
    if not (principles or kept_r or kept_i):
        out += ["_No lessons match yet. Run `lessons dream` to extract some._", ""]
    if dropped:
        out += [f"_{dropped} more lessons omitted to stay under {max_lines} lines; use `lessons search`._", ""]
    while out and not out[-1]:
        out.pop()
    body = "\n".join(out) + "\n"
    foot = (f"<!-- lessons index v1: level={level} scenario={scenario or 'all'} principles={len(principles)} "
            f"rules={len(kept_r)} instances={len(kept_i)} omitted={dropped} lines={len(out) + 2} "
            f"~{estimate_tokens(body)} tokens -->")
    return body + "\n" + foot + "\n"


def write(root: str | None = None, cwd: str | None = None, out: str | None = None, **kw) -> str:
    """Write INDEX.md (default <root>/INDEX.md) with build_index; links are relative to the file."""
    root = os.path.expanduser(root or store.DEFAULT_DIR)
    out = out or os.path.join(root, "INDEX.md")
    text = build_index(root=root, cwd=cwd, link_base=os.path.dirname(os.path.abspath(out)), **kw)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    return out
