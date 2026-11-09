"""A static, offline viewer for lesson files: one self-contained index.html a person can review.

    python -m lessons.view <lessons_dir> [--moments 'work/shards/*.jsonl'] [--out work/site] [--open]

Everything from a lesson or transcript is escaped: the page data travels as JSON with <, >, & escaped, the
page script only ever sets textContent, and lesson bodies are rendered here from an escaped markdown subset.
"""
from __future__ import annotations
import argparse
import glob
import html
import json
import re
import sys
import webbrowser
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as urlquote

from . import frontmatter

MOMENT_KINDS = ["correction", "frustration", "interrupt", "repeated-error"]
STATUSES = ["verified", "candidate", "refuted"]
LEVELS = ["principle", "rule", "instance"]
STRENGTHS = ["must", "should", "prefer"]
SCENARIOS = ["any", "writing-paper", "writing-outward", "figures-visual", "coding", "debugging", "experiments",
             "research-ideation", "planning-discussion", "release-deploy", "reporting", "long-autonomous",
             "cost-resources"]  # docs/SCENARIOS.md order; unknown ids are kept and sorted after these
_TEMPLATE = Path(__file__).with_name("templates") / "view.html"

# ---------------------------------------------------------------- loading


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(_s(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {_s(x)}" for k, x in v.items())
    return str(v)


def _day(v) -> str:
    """YYYY-MM-DD from an ISO string, a date, or epoch seconds/ms; "" when unknown."""
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{9,14}", v.strip())):
        n = float(v)
        if n > 1e11:
            n /= 1000.0
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", str(v))
    return m.group(1) if m else ""


def _list(v) -> list[str]:
    """A frontmatter list (or comma-separated string) as stripped, non-empty strings."""
    items = v if isinstance(v, list) else str(v or "").split(",")
    return [x for x in (_s(t).strip() for t in items) if x]


def tokens_estimate(text: str) -> int:
    """Same rough rule as dream.tokens_estimate: ~4 ASCII chars per token, one per other character."""
    ascii_n = sum(1 for c in text if ord(c) < 128)
    return ascii_n // 4 + (len(text) - ascii_n)


_SECTION = re.compile(r"^\s*\*\*(Do|Why|Check)\s*:\*\*\s*", re.I)


def split_sections(body: str) -> dict:
    """{"do", "why", "check", "rest"} markdown from a lesson body (paragraphs starting with **Do:** etc.)."""
    out = {"do": [], "why": [], "check": [], "rest": []}
    cur = "rest"
    for para in re.split(r"\n\s*\n", body.strip()):
        m = _SECTION.match(para)
        if m:
            cur = m.group(1).lower()
            para = para[m.end():]
        elif cur != "rest" and not para.lstrip().startswith(("-", "*", "1.")):
            cur = "rest"
        out[cur].append(para)
    return {k: "\n\n".join(v).strip() for k, v in out.items()}


def _week(day: str) -> str:
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def load_lessons(lessons_dir) -> tuple[list[dict], list[str]]:
    """Every *.md with frontmatter under lessons_dir, as plain dicts. Returns (lessons, warnings)."""
    root = Path(lessons_dir)
    lessons, warnings = [], []
    for p in sorted(root.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            warnings.append(f"{p}: {e}")
            continue
        meta, body = frontmatter.load(text)
        if not meta:
            if p.stem.upper() not in ("NOTES", "README", "INDEX"):
                warnings.append(f"{p.relative_to(root)}: no frontmatter")
            continue
        ev = []
        for e in meta.get("evidence") or []:
            if isinstance(e, dict):
                ev.append({"ref": _s(e.get("ref")), "source": _s(e.get("source")),
                           "date": _day(e.get("date")) or _s(e.get("date")), "quote": _s(e.get("quote"))})
            elif e:
                ev.append({"ref": _s(e), "source": "", "date": "", "quote": ""})
        themes = _list(meta.get("themes"))
        level = _s(meta.get("level")).lower().strip() or "rule"  # v0 files have no level: they are rules
        sections = split_sections(body)
        try:
            seen = int(meta.get("seen"))
        except (TypeError, ValueError):
            seen = len({e["ref"].rsplit(":", 1)[0] for e in ev}) or 0
        dates = sorted(e["date"] for e in ev if _day(e["date"]))
        lesson = {
            "id": _s(meta.get("id")) or p.stem,
            "title": _s(meta.get("title")) or p.stem,
            "scope": _s(meta.get("scope")) or "global",
            "kind": _s(meta.get("kind")).lower() or "unknown",
            "status": _s(meta.get("status")).lower() or "candidate",
            "trigger": _s(meta.get("trigger")),
            "evidence": ev,
            "seen": seen,
            "first_seen": _day(meta.get("first_seen")) or (dates[0] if dates else ""),
            "last_seen": _day(meta.get("last_seen")) or (dates[-1] if dates else ""),
            "themes": themes,
            "level": level,
            "parent": _s(meta.get("parent")).strip(),
            "scenarios": [x.lower() for x in _list(meta.get("scenarios"))],
            "strength": _s(meta.get("strength")).lower().strip(),
            "do_html": render_markdown(sections["do"]),
            "why_html": render_markdown(sections["why"]),
            "check_html": render_markdown(sections["check"]),
            "rest_html": render_markdown(sections["rest"]),
            "tokens": tokens_estimate(" ".join([_s(meta.get("title")) or p.stem, _s(meta.get("trigger")),
                                                sections["do"]])) + 4,
            "sources": sorted({e["source"] for e in ev if e["source"]}),
            "path": str(p.relative_to(root)),
            "file": str(p.resolve()),
            "body_html": render_markdown(body),
        }
        missing = [k for k in ("id", "title", "kind", "status", "trigger") if not meta.get(k)]
        if level not in LEVELS:
            warnings.append(f"{lesson['path']}: unknown level {level!r}")
        if missing:
            warnings.append(f"{lesson['path']}: missing {', '.join(missing)}")
        lessons.append(lesson)
    # Duplicate ids would collide in the page's #hash routing.
    counts = Counter(l["id"] for l in lessons)
    for l in lessons:
        if counts[l["id"]] > 1:
            warnings.append(f"{l['path']}: duplicate id {l['id']}")
            l["id"] = f"{l['id']}~{l['path']}"
    order = {s: i for i, s in enumerate(STATUSES)}
    lessons.sort(key=lambda l: (order.get(l["status"], 9), -l["seen"], l["title"].lower()))
    warnings += build_hierarchy(lessons)
    return lessons, warnings


def _rank(l: dict) -> tuple:
    st = {s: i for i, s in enumerate(STRENGTHS)}
    return (st.get(l["strength"], 9), {s: i for i, s in enumerate(STATUSES)}.get(l["status"], 9), -l["seen"],
            l["title"].lower())


def build_hierarchy(lessons: list[dict]) -> list[str]:
    """Link parent -> children in place. Sets children (ids, ranked), ancestors (root first), root, depth.

    A parent that is missing, is the lesson itself, or would close a cycle is dropped (with a warning), so the
    lesson becomes a root. A level mismatch (a rule under a rule) is only warned about: the link is kept."""
    by = {l["id"]: l for l in lessons}
    warnings = []
    for l in lessons:
        l["children"] = []
        p = l["parent"]
        if p and p not in by:
            warnings.append(f"{l['path']}: parent {p!r} not found")
            l["parent"] = ""
        elif p == l["id"]:
            warnings.append(f"{l['path']}: is its own parent")
            l["parent"] = ""
    for l in lessons:  # break cycles: walk up; if we come back to l, cut l's link
        seen, cur = set(), l
        while cur["parent"]:
            if cur["id"] in seen:
                break
            seen.add(cur["id"])
            cur = by[cur["parent"]]
            if cur is l:
                warnings.append(f"{l['path']}: parent cycle through {l['parent']!r}")
                l["parent"] = ""
                break
    expect = {"rule": "principle", "instance": "rule"}
    for l in lessons:
        if l["parent"]:
            par = by[l["parent"]]
            par["children"].append(l["id"])
            if l["level"] == "principle" or (l["level"] in expect and par["level"] != expect[l["level"]]):
                warnings.append(f"{l['path']}: {l['level']} under {par['level']} {par['id']!r}")
    for l in lessons:
        l["children"].sort(key=lambda i: _rank(by[i]))
        chain, cur = [], l
        while cur["parent"]:
            cur = by[cur["parent"]]
            chain.append(cur["id"])
        l["ancestors"] = chain[::-1]
        l["root"] = (chain[-1] if chain else l["id"])
        l["depth"] = len(chain)
    return warnings


def related_edges(lessons: list[dict], per_node: int = 3) -> list[list]:
    """Light edges between lessons that share themes or specific scenarios (not "any"), skipping pairs that
    are already parent/child. Each lesson keeps its `per_node` strongest; returns [a, b, weight, why]."""
    cand = {}
    for i, a in enumerate(lessons):
        for b in lessons[i + 1:]:
            if a["parent"] == b["id"] or b["parent"] == a["id"]:
                continue
            th = sorted(set(a["themes"]) & set(b["themes"]))
            sc = sorted((set(a["scenarios"]) & set(b["scenarios"])) - {"any"})
            w = len(th) + 0.5 * len(sc)
            if w >= 1:
                cand[(a["id"], b["id"])] = (w, ", ".join(th + sc))
    keep = set()
    for l in lessons:
        mine = sorted(((w, k) for k, (w, _) in cand.items() if l["id"] in k), key=lambda t: (-t[0], t[1]))
        keep.update(k for _, k in mine[:per_node])
    return [[a, b, cand[(a, b)][0], cand[(a, b)][1]] for a, b in sorted(keep)]


def load_moments(pattern: str | None) -> list[dict]:
    """Only what the chart needs from the moment shards: kind, source, day."""
    out = []
    if not pattern:
        return out
    for f in sorted(glob.glob(pattern)):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                day = _day((d.get("trigger") or {}).get("ts"))
                if day:
                    out.append({"kind": str(d.get("kind", "")), "source": str(d.get("source", "")), "day": day})
    return out


# ---------------------------------------------------------------- markdown subset

_LINK = re.compile(r"\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_SAFE_HREF = re.compile(r"^(?:https?://|file://|mailto:|#|\.{0,2}/|[\w.-]+(?:/|\.\w+|$))", re.I)


def _inline(text: str) -> str:
    """Escape, then apply `code`, **bold**, *italic*, [links](url). Code spans are left untouched."""
    parts = re.split(r"(`[^`\n]+`)", text)
    out = []
    for part in parts:
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue

        def link(m):
            label, href = m.group(1), html.unescape(m.group(2))
            if not _SAFE_HREF.match(href) or re.match(r"^\s*(?:javascript|data|vbscript):", href, re.I):
                return label
            return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

        s = html.escape(part, quote=False)
        s = _LINK.sub(link, s)
        s = _BOLD.sub(r"<strong>\1</strong>", s)
        s = _ITAL.sub(r"<em>\1</em>", s)
        out.append(s)
    return "".join(out)


def render_markdown(md: str) -> str:
    """Paragraphs, - / 1. lists, **bold**, *italic*, `code`, [links](url). Everything else is escaped text."""
    blocks, para, items, ordered = [], [], [], False

    def flush_para():
        if para:
            blocks.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_list():
        if items:
            tag = "ol" if ordered else "ul"
            blocks.append(f"<{tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{tag}>")
            items.clear()

    for raw in md.splitlines():
        line = raw.strip()
        m = re.match(r"^(?:[-*+]|(\d+)[.)])\s+(.*)$", line)
        if not line:
            flush_para()
            flush_list()
        elif m:
            flush_para()
            if items and ordered != bool(m.group(1)):
                flush_list()
            ordered = bool(m.group(1))
            items.append(m.group(2))
        elif items and raw[:1] in " \t":
            items[-1] += " " + line
        elif re.match(r"^#{1,6}\s", line):
            flush_para()
            flush_list()
            blocks.append("<p><strong>" + _inline(line.lstrip("#").strip()) + "</strong></p>")
        else:
            flush_list()
            para.append(line)
    flush_para()
    flush_list()
    return "\n".join(blocks)


# ---------------------------------------------------------------- charts (inline SVG)


def _weeks(lo: str, hi: str) -> list[str]:
    a, b = date.fromisoformat(_week(lo)), date.fromisoformat(_week(hi))
    out = []
    while a <= b:
        out.append(a.isoformat())
        a += timedelta(days=7)
    return out


def moments_chart(moments: list[dict]) -> str:
    """Small multiples: one row per moment kind, one hairline column per week."""
    if not moments:
        return ""
    days = sorted(m["day"] for m in moments)
    weeks = _weeks(days[0], days[-1])
    idx = {w: i for i, w in enumerate(weeks)}
    per = defaultdict(Counter)
    for m in moments:
        per[m["kind"]][idx[_week(m["day"])]] += 1
    kinds = [k for k in MOMENT_KINDS if per[k]] + sorted(k for k in per if k not in MOMENT_KINDS)
    peak = max(max(c.values()) for c in per.values())
    label_w, total_w, row_h, gap = 116, 44, 34, 10
    plot_w = max(360, min(760, len(weeks) * 16))
    step = plot_w / max(1, len(weeks))
    width = label_w + plot_w + total_w
    height = len(kinds) * (row_h + gap) + 22
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
           f'aria-label="Moments per week by kind">']
    for r, k in enumerate(kinds):
        y0 = r * (row_h + gap)
        base = y0 + row_h
        ck = f"k-{re.sub(r'[^a-z-]', '', k.lower()) or 'other'}"
        out.append(f'<text x="0" y="{base - 4}" class="lab">{html.escape(k)}</text>')
        out.append(f'<line x1="{label_w}" x2="{label_w + plot_w}" y1="{base}" y2="{base}" class="hair"/>')
        for i, n in sorted(per[k].items()):
            x = label_w + (i + 0.5) * step
            h = max(1.5, (n / peak) * (row_h - 4))
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{base}" y2="{base - h:.1f}" class="bar {ck}">'
                       f'<title>{html.escape(k)} · week of {weeks[i]}: {n}</title></line>')
        out.append(f'<text x="{width}" y="{base - 4}" class="num" text-anchor="end">'
                   f'{sum(per[k].values())}</text>')
    ty = height - 4
    out.append(f'<text x="{label_w}" y="{ty}" class="tick">{weeks[0]}</text>')
    if len(weeks) > 1:
        out.append(f'<text x="{label_w + plot_w}" y="{ty}" class="tick" text-anchor="end">{weeks[-1]}</text>')
    out.append("</svg>")
    return "".join(out)


def sparkline(dates: list[str], weeks: list[str]) -> str:
    """Dots on a hairline: one dot per week that has evidence, bigger for more."""
    if not weeks:
        return ""
    idx = {w: i for i, w in enumerate(weeks)}
    c = Counter(idx[_week(d)] for d in dates if _day(d) and _week(d) in idx)
    w, h = 160, 14
    step = w / max(1, len(weeks))
    parts = [f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">',
             f'<line x1="0" x2="{w}" y1="{h / 2}" y2="{h / 2}" class="hair"/>']
    for i, n in sorted(c.items()):
        parts.append(f'<circle cx="{(i + 0.5) * step:.1f}" cy="{h / 2}" r="{min(5, 2 + n)}" class="dot"/>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- page


def _ref_link(ref: str) -> str:
    """file:// URL for a local `path[:line]` ref, else ""."""
    m = re.match(r"^(/[^#]+?)(?::\d+)?$", ref.strip())
    if not m:
        return ""
    return "file://" + urlquote(m.group(1))


def _json_for_script(data) -> str:
    s = json.dumps(data, ensure_ascii=False)
    return s.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e") \
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _scope_label(scope: str) -> str:
    if scope.startswith("project:"):
        return "project:" + (Path(scope[8:].strip()).name or scope[8:].strip())
    return scope


def build_site(lessons_dir, out_dir, moments_glob=None) -> Path:
    """Write out_dir/index.html for the lessons under lessons_dir and return its path."""
    lessons, warnings = load_lessons(lessons_dir)
    moments = load_moments(moments_glob)
    ev_days = [e["date"] for l in lessons for e in l["evidence"] if _day(e["date"])]
    span = sorted([m["day"] for m in moments] + ev_days) if moments else sorted(ev_days)
    weeks = _weeks(span[0], span[-1]) if span else []
    for l in lessons:
        l["spark"] = sparkline([e["date"] for e in l["evidence"]], weeks)
        l["scope_label"] = _scope_label(l["scope"])
        for e in l["evidence"]:
            e["href"] = _ref_link(e["ref"])

    def tally(key):
        c = Counter()
        for l in lessons:
            vals = l[key] if isinstance(l[key], list) else [l[key]]
            c.update(vals)
        return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))

    status = Counter(l["status"] for l in lessons)

    def ordered(key, vocab):
        c = Counter()
        for l in lessons:
            c.update(l[key] if isinstance(l[key], list) else [l[key]] if l[key] else [])
        return [(s, c[s]) for s in vocab if c[s]] + sorted((s, n) for s, n in c.items() if s not in vocab)

    data = {
        "lessons": lessons,
        "facets": {
            "status": [(s, status[s]) for s in STATUSES if status[s]]
                      + sorted((s, n) for s, n in status.items() if s not in STATUSES),
            "level": ordered("level", LEVELS),
            "strength": ordered("strength", STRENGTHS),
            "scenario": ordered("scenarios", SCENARIOS),
            "kind": tally("kind"),
            "scope": tally("scope_label"),
            "source": tally("sources"),
            "theme": tally("themes"),
        },
        "moments": {"total": len(moments), "by_kind": dict(Counter(m["kind"] for m in moments)),
                    "by_source": dict(Counter(m["source"] for m in moments)),
                    "from": span[0] if moments else "", "to": span[-1] if moments else ""},
        "graph": {"related": related_edges(lessons)},
        "warnings": warnings,
        "lessons_dir": str(Path(lessons_dir).resolve()),
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    page = _TEMPLATE.read_text(encoding="utf-8")
    page = page.replace("{{CHART}}", moments_chart(moments))
    page = page.replace("{{DATA}}", _json_for_script(data))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lessons.view", description="Build a static lesson viewer.")
    ap.add_argument("lessons_dir")
    ap.add_argument("--moments", help="glob of moment shards, e.g. 'work/shards/*.jsonl'")
    ap.add_argument("--out", default="work/site")
    ap.add_argument("--open", action="store_true", help="open the page in a browser")
    a = ap.parse_args(argv)
    if not Path(a.lessons_dir).is_dir():
        print(f"not a directory: {a.lessons_dir}", file=sys.stderr)
        return 2
    path = build_site(a.lessons_dir, a.out, a.moments)
    print(path)
    if a.open:
        webbrowser.open(path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
