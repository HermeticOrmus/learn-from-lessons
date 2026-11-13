"""dream: turn transcript moments into verified, evidence-linked lessons with the person's own agent CLI as the model.

Lessons follow docs/LESSON_FORMAT.md (v1): each sits at a level (principle > rule > instance, linked by `parent`),
lists `scenarios` from docs/SCENARIOS.md and a `strength` (must | should | prefer).

Two passes, both incremental:
  extract      new moments (lessons.signals over every source, or exported shard files) are batched into prompts
               (prompts/extract.md); the model answers with rules and instances as JSON; lessons citing references
               that are not in the batch are dropped, unknown scenarios are dropped with a warning; dates and `seen`
               come from the cited turns, never from the model.
  consolidate  (a) rules/instances are clustered locally (title/trigger/theme overlap); each cluster that holds
               something new is rewritten by the model (prompts/consolidate.md): merged, kept apart or refuted.
               (b) abstraction: rules without a principle are grouped and the model proposes principles over >= 2
               rules each, and files narrow rules as instances under a broader rule (prompts/abstract.md);
               `--abstraction low|medium|high` sets how aggressively, `--max-principles` caps the count.
               (c) seen/status are recomputed: a principle is verified iff >= 2 of its rules are, and dissolves
               when fewer than 2 rules remain under it. (d) Evidence is only ever unioned, never deleted. Every
               change, level and parent changes included, is appended to CHANGELOG.md.

The model is whichever agent CLI is installed: `claude -p`, `codex exec` or `opencode run` (no API keys).
`--engine fake` answers deterministically for tests; `--dry-run` prints the batches and prompts and calls nothing.

    python3 -m lessons.dream --from 'work/shards/*.jsonl' --max-batches 1 --engine claude --abstraction medium \
        --out work/dream-smoke
"""
from __future__ import annotations
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile, time
from collections import Counter
from typing import Callable, Iterable

from .redact import redact
from .state import State, digest, moment_key

try:
    from .frontmatter import load as _fm_load
except Exception:  # pragma: no cover - frontmatter.py is optional
    _fm_load = None

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = os.path.join(HERE, "prompts")
SCENARIOS_DOC = os.path.join(os.path.dirname(HERE), "docs", "SCENARIOS.md")
KINDS = ("preference", "workflow", "pitfall", "fact", "communication")
TOOLS = ("claude-code", "codex", "opencode")
LEVELS = ("principle", "rule", "instance")
STRENGTHS = ("must", "should", "prefer")  # strongest first
EXTRACT_MARK = "<!-- lessons:extract -->"
CONSOLIDATE_MARK = "<!-- lessons:consolidate -->"
ABSTRACT_MARK = "<!-- lessons:abstract -->"
PROMPT_MARKS = (EXTRACT_MARK, CONSOLIDATE_MARK, ABSTRACT_MARK)

# How aggressively to generalise. merge: similarity needed to send two lessons to the dedupe pass (lower = more
# merging); group: similarity used to split rules into abstraction calls; chunk: rules per abstraction call (a
# bigger chunk lets one principle span more rules).
ABSTRACTION = {
    "low": {"merge": 0.40, "group": 0.20, "chunk": 8, "guidance": (
        "Abstraction: low. Be conservative: write a principle only where its rules plainly state the same tenet in "
        "different situations; keep principles close to their rules. When unsure, leave rules as they are.")},
    "medium": {"merge": 0.30, "group": 0.10, "chunk": 16, "guidance": (
        "Abstraction: medium. Write a principle where rules share a real tenet, even across different scenarios; "
        "leave rules that share nothing.")},
    "high": {"merge": 0.22, "group": 0.0, "chunk": 32, "guidance": (
        "Abstraction: high. Generalise boldly: prefer fewer, broader principles that each cover many rules, as "
        "long as each is still actionable. Attach rules to an existing principle whenever it fits.")},
}
_FALLBACK_SCENARIOS = ("any", "writing-paper", "writing-outward", "figures-visual", "coding", "debugging",
                       "experiments", "research-ideation", "planning-discussion", "release-deploy", "reporting",
                       "long-autonomous", "cost-resources")


def load_scenarios(path: str = SCENARIOS_DOC) -> dict:
    """id -> description, from the controlled vocabulary table in docs/SCENARIOS.md."""
    try:
        with open(path, encoding="utf-8") as fh:
            rows = re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*(.*?)\s*\|\s*$", fh.read(), re.M)
    except OSError:
        rows = []
    return dict(rows) if rows else {s: "" for s in _FALLBACK_SCENARIOS}


SCENARIOS = load_scenarios()
SYSTEM = ("You turn a person's coding-agent transcripts into short, evidence-linked lessons. Use only what the given "
          "text supports. Answer only with the JSON asked for.")


# --------------------------------------------------------------------------------------------------------------
# moments


def _turn_dict(t) -> dict:
    return {"role": t.role, "tool": t.tool, "error": t.error, "ts": t.ts, "ref": t.ref, "text": t.text}


def moment_dict(m) -> dict:
    """lessons.signals.Moment -> the exported-shard shape."""
    s = m.session
    return {"kind": m.kind, "source": s.source, "project": s.cwd, "cwd": s.cwd, "session": s.id,
            "trigger": _turn_dict(m.turn), "context": [_turn_dict(t) for t in m.context if t is not m.turn],
            "followup": [_turn_dict(t) for t in getattr(m, "followup", [])]}


def _is_scripted(s) -> bool:
    """One-turn scripted runs (`claude -p`, our own dream prompts) and subagents have no person to learn from."""
    users = s.user_turns
    if s.subagent or len(users) <= 1:
        return True
    return any(mark in users[0].text[:200] for mark in PROMPT_MARKS)


def _fingerprint(s) -> str:
    last = s.turns[-1].ref if s.turns else ""
    return digest(len(s.turns), last)


def live_moments(state: State | None, sources: Iterable[str]) -> tuple[list[dict], dict]:
    from . import projects
    from .signals import find
    from .sources import SOURCES
    out, prints = [], {}
    seen: set = set()  # turns already found in a forked or resumed session
    patterns = projects.load()
    table = projects.sources({name: SOURCES[name] for name in sources}, patterns)
    for name in sources:
        for s in table[name]():
            if _is_scripted(s):
                continue
            fp = _fingerprint(s)
            sid = f"{s.source}:{s.id}"
            if state and state.session_unchanged(sid, fp):
                continue
            found = [moment_dict(m) for m in find(s, seen)]
            kept = allowed_moments(found, patterns)
            if len(kept) == len(found):  # a session with a dropped moment stays open for when the file changes
                prints[sid] = fp
            out += kept
    return out, prints


def allowed_moments(moments: list[dict], patterns: list[str]) -> list[dict]:
    """Shards may predate the exclude list: drop moments from excluded folders again."""
    from .projects import excluded, mentions, moment_texts, words
    terms = words(patterns)
    return [m for m in moments if not excluded(m.get("cwd") or m.get("project") or "", patterns)
            and not mentions(moment_texts(m), terms)]


def shard_moments(patterns: Iterable[str]) -> list[dict]:
    out = []
    for pat in patterns:
        for path in sorted(glob.glob(os.path.expanduser(pat))) or [pat]:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            continue
    return out


def tokens_estimate(text: str) -> int:
    ascii_n = sum(1 for c in text if ord(c) < 128)
    return ascii_n // 4 + (len(text) - ascii_n)


def _clip(text: str, n: int) -> str:
    text = redact(text or "").strip()
    return text if len(text) <= n else text[:n] + " …"


LIMITS = {"trigger": 1500, "user": 700, "assistant": 350, "tool": 250, "error": 500}


def render_batch(moments: list[dict]) -> tuple[str, dict]:
    """Moments as prompt text, each turn tagged [rN]. Returns (text, handle -> turn info)."""
    refs, blocks, n = {}, [], 0
    for i, m in enumerate(moments, 1):
        trig = m.get("trigger") or {}
        head = (f"### Moment {i}: {m.get('kind', '?')} · {m.get('source', '?')} · project {m.get('project') or m.get('cwd') or '?'}"
                f" · {(trig.get('ts') or '')[:10]}")
        lines = [head]
        after = list(m.get("followup") or [])
        for t in list(m.get("context") or []) + [trig] + after:
            n += 1
            h = f"r{n}"
            is_trig = t is trig
            is_after = any(t is a for a in after)
            role = t.get("role", "")
            limit = LIMITS["trigger"] if is_trig else LIMITS["error"] if t.get("error") else LIMITS.get(role, 300)
            text = _clip(t.get("text", ""), limit)
            label = "USER" if role == "user" else role
            if t.get("tool"):
                label += f" ({t['tool']})"
            if t.get("error"):
                label += " ERROR"
            if is_trig:
                label += " <- the moment"
            elif is_after:
                label += " (next turn)"
            lines.append(f"[{h}] {label}: {text if text else '(empty)'}")
            refs[h] = {"ref": t.get("ref", ""), "source": m.get("source", ""), "session": m.get("session", ""),
                       "date": (t.get("ts") or trig.get("ts") or "")[:10], "role": role,
                       "text": redact(t.get("text") or ""), "project": m.get("project") or m.get("cwd") or "",
                       "kind": m.get("kind", "")}
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), refs


def _template(name: str) -> str:
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as fh:
        return fh.read()


def make_batches(moments: list[dict], budget_tokens: int, scenarios=None) -> list[list[dict]]:
    """Greedy packing; moments of one session stay together so the model sees repeats side by side."""
    order = sorted(moments, key=lambda m: (m.get("project") or "", m.get("session") or "",
                                           (m.get("trigger") or {}).get("ts") or ""))
    budget_tokens = max(1, budget_tokens - tokens_estimate(extract_prompt([], scenarios)[0]))
    batches, cur, used = [], [], 0
    for m in order:
        cost = tokens_estimate(render_batch([m])[0])
        if cur and used + cost > budget_tokens:
            batches.append(cur)
            cur, used = [], 0
        cur.append(m)
        used += cost
    if cur:
        batches.append(cur)
    return batches


def scenario_list(allowed=None) -> str:
    ids = [s for s in SCENARIOS if not allowed or s in allowed or s == "any"]
    lines = [f"- `{s}`: {SCENARIOS[s]}" if SCENARIOS.get(s) else f"- `{s}`" for s in ids]
    if allowed:
        lines.append("\nThis run only learns lessons for the scenarios above: skip moments that teach nothing for them.")
    return "\n".join(lines)


def extract_prompt(batch: list[dict], scenarios=None) -> tuple[str, dict]:
    text, refs = render_batch(batch)
    prompt = _template("extract.md").replace("{{SCENARIOS}}", scenario_list(scenarios))
    return prompt.replace("{{MOMENTS}}", text), refs


# --------------------------------------------------------------------------------------------------------------
# model output


def parse_json(text: str):
    """The first JSON value in a model answer (fenced or bare)."""
    text = (text or "").strip()
    for m in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.S):
        try:
            return json.loads(m.group(1))
        except ValueError:
            continue
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                return dec.raw_decode(text[i:])[0]
            except ValueError:
                continue
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().strip("\"'“”").lower()


def _str(v, n: int = 600) -> str:
    if isinstance(v, list):
        v = " ".join(str(x) for x in v)
    return redact(str(v or "")).strip()[:n]


def slugify(text: str, n: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n].rstrip("-") or "lesson")


def _themes(v) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,;]", v)
    out = []
    for t in v or []:
        t = re.sub(r"[^a-z0-9 -]+", "", str(t).lower()).strip()
        if t and t not in out:
            out.append(t)
    return out[:6]


def _scenarios(v, unknown: list | None = None) -> list[str]:
    """Scenario ids from the vocabulary; unknown ones are dropped (and reported in `unknown`)."""
    if isinstance(v, str):
        v = re.split(r"[,;\s]+", v)
    out = []
    for x in v or []:
        x = re.sub(r"[^a-z0-9-]+", "-", str(x).strip().strip("`").lower()).strip("-")
        if not x:
            continue
        if x not in SCENARIOS:
            if unknown is not None:
                unknown.append(x)
            continue
        if x not in out:
            out.append(x)
    if "any" in out and len(out) > 1:
        out.remove("any")
    return out


def _strength(v, default: str = "should") -> str:
    v = str(v or "").strip().lower()
    return v if v in STRENGTHS else default


def _strongest(values) -> str:
    vals = [v for v in values if v in STRENGTHS]
    return min(vals, key=STRENGTHS.index) if vals else "should"


def _level(v, default: str = "rule", allowed=LEVELS) -> str:
    v = str(v or "").strip().lower()
    return v if v in allowed else default


def _scope(raw, evidence: list[dict]) -> str:
    raw = str(raw or "global").strip()
    if raw.startswith("tool:") and raw[5:] in TOOLS:
        return raw
    if raw.startswith("project"):
        given = raw.partition(":")[2].strip()
        if given and os.path.isabs(given):
            return f"project:{given}"
        projects = Counter(e.get("project") for e in evidence if e.get("project"))
        if projects:
            return f"project:{projects.most_common(1)[0][0]}"
    return "global"


def _finish(lesson: dict) -> dict:
    """Derive seen / first_seen / last_seen / status from the evidence. Principles have no evidence of their own:
    theirs is recomputed from their rules (see recompute)."""
    lesson.setdefault("level", "rule")
    lesson.setdefault("parent", "")
    lesson.setdefault("scenarios", ["any"])
    lesson.setdefault("strength", "should")
    if lesson["level"] == "principle":
        lesson.setdefault("seen", 0)
        lesson.setdefault("first_seen", "")
        lesson.setdefault("last_seen", "")
        lesson.setdefault("status", "candidate")
        return lesson
    ev = lesson["evidence"]
    sessions = {e.get("session") or e.get("ref", "").rsplit(":", 1)[0] for e in ev}
    dates = sorted(e["date"] for e in ev if e.get("date"))
    lesson["seen"] = len(sessions)
    lesson["first_seen"] = dates[0] if dates else ""
    lesson["last_seen"] = dates[-1] if dates else ""
    if lesson.get("status") != "refuted":
        lesson["status"] = "verified" if lesson["seen"] >= 2 or lesson.get("status") == "verified" else "candidate"
    return lesson


def validate(raw, refs: dict, scenarios=None) -> tuple[list[dict], dict]:
    """Model lessons -> v1 rule/instance candidates. Evidence must cite a reference from this batch; a quote that is
    not in the cited turn is replaced by that turn's own words. Lessons left without evidence are dropped. Scenario
    ids outside docs/SCENARIOS.md are dropped (listed in stats["scenarios_unknown"]); a lesson left with none gets
    `any`. With `scenarios` (a restriction), lessons for none of them (and not `any`) are dropped. `must` needs a
    cited turn of the person's own; otherwise it becomes `should`."""
    stats = {"lessons_in": 0, "kept": 0, "dropped_no_evidence": 0, "refs_invented": 0, "quotes_replaced": 0,
             "dropped_scenario": 0, "strength_downgraded": 0, "scenarios_unknown": []}
    by_full = {v["ref"]: h for h, v in refs.items() if v["ref"]}
    out = []
    if isinstance(raw, dict):
        raw = raw.get("lessons", [])
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        stats["lessons_in"] += 1
        title, do = _str(item.get("title"), 160), _str(item.get("do"))
        evidence, used, by_person = [], set(), False
        for e in item.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            h = str(e.get("ref", "")).strip().strip("[]")
            h = h if h in refs else by_full.get(h, "")
            if not h:
                stats["refs_invented"] += 1
                continue
            if h in used:
                continue
            used.add(h)
            info = refs[h]
            quote = _str(e.get("quote"), 400)
            if not quote or _norm(quote) not in _norm(info["text"]):
                stats["quotes_replaced"] += bool(quote)
                quote = info["text"]
            quote = re.sub(r"\s+", " ", quote).strip()[:200]
            evidence.append({"ref": info["ref"], "source": info["source"], "date": info["date"], "quote": quote,
                             "session": info["session"], "project": info["project"]})
            by_person = by_person or info.get("role") == "user" and info.get("kind") != "repeated-error"
        if not evidence or not title or not do:
            stats["dropped_no_evidence"] += 1
            continue
        unknown: list[str] = []
        scen = _scenarios(item.get("scenarios"), unknown) or ["any"]
        stats["scenarios_unknown"] += unknown
        if scenarios and not (set(scen) & (set(scenarios) | {"any"})):
            stats["dropped_scenario"] += 1
            continue
        strength = _strength(item.get("strength"))
        if strength == "must" and not by_person:
            strength = "should"
            stats["strength_downgraded"] += 1
        kind = str(item.get("kind", "")).strip().lower()
        lesson = {"title": title, "level": _level(item.get("level"), "rule", ("rule", "instance")), "parent": "",
                  "scope": _scope(item.get("scope"), evidence), "kind": kind if kind in KINDS else "pitfall",
                  "scenarios": scen, "strength": strength, "trigger": _str(item.get("trigger")),
                  "status": "candidate", "themes": _themes(item.get("themes")), "evidence": evidence,
                  "do": do, "why": _str(item.get("why")), "check": _str(item.get("check"))}
        out.append(_finish(lesson))
        stats["kept"] += 1
    return out, stats


# --------------------------------------------------------------------------------------------------------------
# lesson files


def scope_dir(scope: str) -> str:
    if scope.startswith("project:"):
        return "project-" + slugify(os.path.basename(scope[8:].rstrip("/")) or "root", 40)
    if scope.startswith("tool:"):
        return "tool-" + scope[5:]
    return "global"


def _q(v) -> str:
    return json.dumps(str(v if v is not None else ""), ensure_ascii=False)


def _qlist(v) -> str:
    return "[" + ", ".join(_q(t) for t in v or []) + "]"


def dump_lesson(l: dict) -> str:
    lines = ["---", f"id: {l['id']}", f"title: {_q(l['title'])}", f"level: {l.get('level') or 'rule'}",
             f"parent: {_q(l.get('parent') or '')}", f"scope: {_q(l['scope'])}", f"kind: {l['kind']}",
             f"scenarios: {_qlist(l.get('scenarios') or ['any'])}", f"strength: {l.get('strength') or 'should'}",
             f"trigger: {_q(l.get('trigger', ''))}", f"status: {l['status']}",
             f"themes: {_qlist(l.get('themes'))}", "evidence:" if l["evidence"] else "evidence: []"]
    for e in l["evidence"]:
        lines.append(f"  - ref: {_q(e.get('ref'))}")
        for k in ("source", "date", "session", "project", "quote"):
            if e.get(k):
                lines.append(f"    {k}: {_q(e[k])}")
    lines += [f"seen: {l['seen']}", f"first_seen: {l.get('first_seen', '')}", f"last_seen: {l.get('last_seen', '')}"]
    if l.get("refuted_by"):
        lines.append("refuted_by: [" + ", ".join(_q(r) for r in l["refuted_by"]) + "]")
    lines += ["---", "", f"**Do:** {l.get('do', '')}", "", f"**Why:** {l.get('why', '')}", ""]
    if l.get("check"):
        lines += [f"**Check:** {l['check']}", ""]
    return "\n".join(lines)


def _mini_parse(text: str) -> tuple[dict, str]:
    """Fallback for the format dump_lesson writes, if lessons/frontmatter.py is missing."""
    m = re.match(r"---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text
    meta, key, item = {}, None, None
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s:
            continue
        val = lambda v: json.loads(v) if v[:1] == '"' else ([json.loads(x) for x in re.findall(r'"(?:[^"\\]|\\.)*"', v)]
                                                              if v[:1] == "[" else (int(v) if v.isdigit() else v))
        if not line.startswith(" ") and ":" in s:
            key, _, v = s.partition(":")
            meta[key] = val(v.strip()) if v.strip() else []
        elif s.startswith("- ") and key:
            k, _, v = s[2:].partition(":")
            item = {k.strip(): val(v.strip())}
            meta[key].append(item)
        elif item is not None:
            k, _, v = s.partition(":")
            item[k.strip()] = val(v.strip())
    return meta, text[m.end():]


def parse_lesson(text: str) -> tuple[dict, str]:
    if _fm_load:
        try:
            meta, body = _fm_load(text)
            if isinstance(meta, dict) and meta:
                return meta, body
        except Exception:
            pass
    return _mini_parse(text)


def read_lessons(root: str) -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(root, "**", "*.md"), recursive=True)):
        if os.path.basename(path) in ("CHANGELOG.md", "INDEX.md", "README.md"):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                meta, body = parse_lesson(fh.read())
        except OSError:
            continue
        if not meta.get("title"):
            continue
        sections = dict(re.findall(r"\*\*(Do|Why|Check):\*\*\s*(.*?)(?=\n\s*\*\*(?:Do|Why|Check):\*\*|\Z)", body, re.S))
        ev = [e for e in meta.get("evidence") or [] if isinstance(e, dict) and e.get("ref")]
        level = _level(meta.get("level"))
        l = {"id": str(meta.get("id") or slugify(meta["title"])), "title": str(meta["title"]), "level": level,
             "parent": str(meta.get("parent") or "") if level != "principle" else "",
             "scenarios": _scenarios(meta.get("scenarios")) or ["any"], "strength": _strength(meta.get("strength")),
             "scope": str(meta.get("scope") or "global"), "kind": str(meta.get("kind") or "pitfall"),
             "trigger": str(meta.get("trigger") or ""), "status": str(meta.get("status") or "candidate"),
             "themes": _themes(meta.get("themes")), "evidence": [{k: str(v) for k, v in e.items()} for e in ev],
             "do": sections.get("Do", "").strip(), "why": sections.get("Why", "").strip(),
             "check": sections.get("Check", "").strip(), "path": path}
        if meta.get("refuted_by"):
            l["refuted_by"] = meta["refuted_by"] if isinstance(meta["refuted_by"], list) else [meta["refuted_by"]]
        if level == "principle":
            for k in ("first_seen", "last_seen"):
                l[k] = str(meta.get(k) or "")
            try:
                l["seen"] = int(meta.get("seen") or 0)
            except (TypeError, ValueError):
                l["seen"] = 0
        out.append(_finish(l))
    return out


def write_lesson(root: str, l: dict) -> str:
    d = os.path.join(root, scope_dir(l["scope"]))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{l['id']}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dump_lesson(l))
    return path


def _unique_id(base: str, taken: set) -> str:
    i, cand = 2, base
    while cand in taken:
        cand, i = f"{base}-{i}", i + 1
    taken.add(cand)
    return cand


# --------------------------------------------------------------------------------------------------------------
# consolidation

STOP = set("a an and the to of in on for with when before after not no do don't dont is are be it its this that by "
           "from as at or if into than then any every each your you they them their what how".split())


def _words(l: dict) -> set:
    text = f"{l.get('title', '')} {l.get('trigger', '')}".lower()
    words = {w for w in re.findall(r"[a-z][a-z0-9]+", text) if w not in STOP and len(w) > 2}
    return {w[:6] for w in words}  # crude stemming: verify/verified/verifying


def similarity(a: dict, b: dict) -> float:
    wa, wb = _words(a), _words(b)
    j = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
    ta, tb = set(a.get("themes") or []), set(b.get("themes") or [])
    t = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return 0.6 * j + 0.4 * t


def cluster(lessons: list[dict], threshold: float = 0.3, max_size: int = 8) -> list[list[dict]]:
    """Leader clustering: each lesson joins the cluster whose members it is most similar to on average."""
    clusters: list[list[dict]] = []
    for l in sorted(lessons, key=lambda x: (x.get("_new", False), x.get("first_seen", ""))):
        best, score = None, threshold
        for c in clusters:
            if len(c) >= max_size:
                continue
            s = sum(similarity(l, m) for m in c) / len(c)
            if s >= score:
                best, score = c, s
        if best is None:
            clusters.append([l])
        else:
            best.append(l)
    return clusters


def consolidate_prompt(members: list[dict]) -> tuple[str, dict]:
    blocks, ehandles, n = [], {}, 0
    for i, l in enumerate(members, 1):
        lines = [f"### L{i} ({'new candidate' if l.get('_new') else 'existing'} {l.get('level', 'rule')}, "
                 f"status {l['status']}, seen in {l['seen']} sessions, scope {l['scope']}, kind {l['kind']}, "
                 f"strength {l.get('strength', 'should')}, scenarios {', '.join(l.get('scenarios') or ['any'])})",
                 f"title: {l['title']}", f"trigger: {l.get('trigger', '')}", f"do: {l.get('do', '')}",
                 f"why: {l.get('why', '')}"]
        if l.get("check"):
            lines.append(f"check: {l['check']}")
        if l.get("themes"):
            lines.append("themes: " + ", ".join(l["themes"]))
        lines.append("evidence:")
        for e in l["evidence"]:
            n += 1
            ehandles[f"e{n}"] = e
            lines.append(f"  [e{n}] {e.get('date', '')} {e.get('source', '')}: \"{_clip(e.get('quote', ''), 200)}\"")
        blocks.append("\n".join(lines))
    prompt = _template("consolidate.md").replace("{{SCENARIOS}}", ", ".join(SCENARIOS))
    return prompt.replace("{{LESSONS}}", "\n\n".join(blocks)), ehandles


def _union_evidence(members: list[dict]) -> list[dict]:
    out, seen = [], set()
    for l in members:
        for e in l["evidence"]:
            key = e.get("ref")
            if key not in seen:
                seen.add(key)
                out.append(e)
    return sorted(out, key=lambda e: (e.get("date", ""), e.get("ref", "")))


def apply_merge(members: list[dict], answer, ehandles: dict) -> tuple[list[dict], list[str]]:
    """Model answer -> (lessons that replace the cluster, changelog lines). Members not covered stay as they are."""
    labels = {f"L{i}": l for i, l in enumerate(members, 1)}
    used, out, log = set(), [], []
    items = answer.get("lessons") if isinstance(answer, dict) else answer if isinstance(answer, list) else []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        group = [labels[x] for x in dict.fromkeys(str(x).strip() for x in item.get("merge") or [])
                 if x in labels and x not in used]
        if not group:
            continue
        used.update(k for k, v in labels.items() if any(v is g for g in group))
        evidence = _union_evidence(group)
        base = next((g for g in group if not g.get("_new")), group[0])
        refuted = [ehandles[r]["ref"] for r in item.get("refuted_by") or [] if r in ehandles]
        kind = str(item.get("kind", base["kind"])).lower()
        member_scen = []
        for g in group:
            member_scen += [x for x in g.get("scenarios") or [] if x not in member_scen]
        scen = _scenarios(item.get("scenarios")) or _scenarios(member_scen) or ["any"]
        default_level = "rule" if any(g.get("level", "rule") == "rule" for g in group) else "instance"
        parents = Counter(g.get("parent") for g in group if g.get("parent"))
        merged = {"id": base.get("id", ""), "title": _str(item.get("title"), 160) or base["title"],
                  "level": _level(item.get("level"), default_level, ("rule", "instance")),
                  "parent": base.get("parent") or (parents.most_common(1)[0][0] if parents else ""),
                  "scenarios": scen,
                  "strength": _strength(item.get("strength"), _strongest(g.get("strength") for g in group)),
                  "scope": _scope(item.get("scope") or base["scope"], evidence) if item.get("scope") else base["scope"],
                  "kind": kind if kind in KINDS else base["kind"],
                  "trigger": _str(item.get("trigger")) or base.get("trigger", ""),
                  "themes": _themes(item.get("themes")) or base.get("themes", []), "evidence": evidence,
                  "do": _str(item.get("do")) or base.get("do", ""), "why": _str(item.get("why")) or base.get("why", ""),
                  "check": _str(item.get("check")) or base.get("check", ""),
                  "status": "verified" if any(g["status"] == "verified" for g in group) else "candidate",
                  "_members": group}
        if refuted:
            merged["status"], merged["refuted_by"] = "refuted", refuted
        elif any(g.get("status") == "refuted" for g in group):
            merged["status"] = "refuted"
            merged["refuted_by"] = [r for g in group for r in g.get("refuted_by") or []]
        _finish(merged)
        note = _str(item.get("note"), 200)
        verb = "refuted" if merged["status"] == "refuted" else "merged" if len(group) > 1 else "rewrote"
        log.append(f"- {verb}: {merged['title']} ({len(group)} lesson(s), {len(evidence)} evidence, seen "
                   f"{merged['seen']}, {merged['status']})" + (f" — {note}" if note else ""))
        out.append(merged)
    for k, l in labels.items():
        if k not in used:
            out.append(dict(l, _members=[l], _passthrough=True))
    return out, log


# --------------------------------------------------------------------------------------------------------------
# abstraction: principles over rules, instances under rules


def _orphans(lessons: list[dict]) -> list[dict]:
    """Rules without a principle and instances without a rule: what the abstraction pass may (re)home."""
    return [l for l in lessons if l.get("level") in ("rule", "instance") and not l.get("parent")
            and l.get("status") != "refuted"]


def abstraction_groups(rules: list[dict], abstraction: str = "medium") -> list[list[dict]]:
    """Split rules into abstraction calls. Related rules (by similarity) stay together; higher abstraction packs
    more rules into one call, so a principle can span more of them."""
    p = ABSTRACTION[abstraction]
    if len(rules) <= p["chunk"]:
        return [list(rules)] if rules else []
    groups = cluster(rules, p["group"], max_size=p["chunk"]) if p["group"] > 0 else [list(rules)]
    chunks, cur = [], []
    for g in sorted(groups, key=len, reverse=True):
        while len(g) > p["chunk"]:
            chunks.append(g[:p["chunk"]])
            g = g[p["chunk"]:]
        if cur and len(cur) + len(g) > p["chunk"]:
            chunks.append(cur)
            cur = []
        cur = cur + g
    if cur:
        chunks.append(cur)
    return chunks


def abstract_prompt(rules: list[dict], principles: list[dict], everything: list[dict], *, abstraction: str,
                    max_new: int) -> tuple[str, dict, dict]:
    """Returns (prompt, R-label -> rule, P-label -> principle)."""
    rl, pl, blocks, pblocks = {}, {}, [], []
    for i, l in enumerate(rules, 1):
        rl[f"R{i}"] = l
        blocks.append(f"### R{i} ({l.get('level', 'rule')}, strength {l.get('strength', 'should')}, scenarios "
                      f"{', '.join(l.get('scenarios') or ['any'])}, scope {l['scope']}, seen {l['seen']}, "
                      f"{l['status']})\ntitle: {l['title']}\ntrigger: {l.get('trigger', '')}\ndo: {l.get('do', '')}")
    for i, p in enumerate(principles, 1):
        pl[f"P{i}"] = p
        kids = [k["title"] for k in everything if k.get("parent") == p.get("id") and k.get("level") == "rule"]
        pblocks.append(f"### P{i}: {p['title']}\ndo: {p.get('do', '')}\nits rules: "
                       + ("; ".join(kids[:5]) + (" …" if len(kids) > 5 else "") if kids else "(none listed)"))
    prompt = (_template("abstract.md").replace("{{GUIDANCE}}", ABSTRACTION[abstraction]["guidance"])
              .replace("{{MAX_NEW}}", str(max(0, max_new))).replace("{{SCENARIOS}}", ", ".join(SCENARIOS))
              .replace("{{PRINCIPLES}}", "\n\n".join(pblocks) or "(none yet)")
              .replace("{{RULES}}", "\n\n".join(blocks)))
    return prompt, rl, pl


def _labels(v) -> list[str]:
    if isinstance(v, str):
        v = re.findall(r"[RP]\d+", v)
    return list(dict.fromkeys(str(x).strip().strip("[]") for x in v or []))


def apply_abstraction(answer, rl: dict, pl: dict, everything: list[dict], *, max_new: int,
                      taken: set) -> tuple[list[dict], list[str], dict]:
    """Model answer -> (new principles, changelog lines, stats). Mutates rules in place (parent, level).
    Enforced here, whatever the model said: a new principle needs >= 2 rules; a rule becomes an instance only under
    a rule that ends up with >= 2 instances; no more than `max_new` new principles; nothing loses evidence."""
    stats = {"principles_new": 0, "principles_rejected": 0, "attached": 0, "filed_as_instance": 0}
    log: list[str] = []
    if not isinstance(answer, dict):
        return [], log, stats
    # 1. instances under a broader rule
    proposals: dict[str, list[dict]] = {}
    for it in answer.get("instances") or []:
        if not isinstance(it, dict):
            continue
        child, under = rl.get(str(it.get("rule", "")).strip()), rl.get(str(it.get("under", "")).strip())
        if child is None or under is None or child is under or under.get("level") != "rule":
            continue
        proposals.setdefault(id(under), [under])
        if all(child is not c for c in proposals[id(under)][1:]):
            proposals[id(under)].append(child)
    unders = {k for k in proposals}
    for key, (under, *kids) in proposals.items():
        kids = [k for k in kids if id(k) not in unders  # a rule with instances of its own stays a rule
                and not any(x.get("parent") == k.get("id") for x in everything if k.get("id"))]
        have = sum(1 for x in everything if x.get("level") == "instance" and x.get("parent") == under.get("id")
                   and under.get("id"))
        if have + len(kids) < 2:  # a lone narrow case stays a rule
            kids = [k for k in kids if k.get("level") == "instance"]
        for k in kids:
            was = k.get("level", "rule")
            k["level"], k["parent"], k["_dirty"] = "instance", under["id"], True
            k["_placed"] = True
            stats["filed_as_instance"] += 1
            log.append(f"- level: {k['title']} — {was} → instance under \"{under['title']}\"")
    # 2. principles
    plans = []
    for it in answer.get("principles") or []:
        if not isinstance(it, dict):
            continue
        kids = [rl[x] for x in _labels(it.get("rules")) if x in rl]
        kids = [k for k in kids if k.get("level") == "rule" and not k.get("_placed") and k.get("status") != "refuted"]
        use = pl.get(str(it.get("use") or "").strip())
        title = _str(it.get("title"), 160)
        if use is None and title:  # a near-copy of an existing principle is that principle
            wt = _words({"title": title})
            use = next((p for p in pl.values() if wt and len(wt & _words({"title": p["title"]}))
                        / len(wt | _words({"title": p["title"]})) >= 0.6), None)
        plans.append((use, kids, it))
    plans.sort(key=lambda x: (x[0] is None, -len(x[1])))
    new: list[dict] = []
    for use, kids, it in plans:
        kids = [k for k in kids if not k.get("_placed")]
        if use is not None:
            for k in kids:
                k["parent"], k["_dirty"], k["_placed"] = use["id"], True, True
                stats["attached"] += 1
                log.append(f"- parent: {k['title']} → principle \"{use['title']}\"")
            continue
        title, do = _str(it.get("title"), 160), _str(it.get("do"))
        if len(kids) < 2 or not title or not do or len(new) >= max_new:
            stats["principles_rejected"] += 1
            continue
        scopes = {k["scope"] for k in kids}
        kinds = Counter(k["kind"] for k in kids)
        kind = str(it.get("kind", "")).strip().lower()
        union = []
        for k in kids:
            union += [x for x in k.get("scenarios") or [] if x not in union]
        p = {"id": _unique_id(slugify(title), taken), "title": title, "level": "principle", "parent": "",
             "scope": scopes.pop() if len(scopes) == 1 else "global",
             "kind": kind if kind in KINDS else kinds.most_common(1)[0][0],
             "scenarios": _scenarios(it.get("scenarios")) or (_scenarios(union) if len(union) <= 3 else ["any"]),
             "strength": _strength(it.get("strength"), _strongest(k.get("strength") for k in kids)),
             "trigger": _str(it.get("trigger")), "do": do, "why": _str(it.get("why")), "check": _str(it.get("check")),
             "themes": _themes(it.get("themes")), "evidence": [], "status": "candidate", "seen": 0,
             "first_seen": "", "last_seen": "", "_new": True, "_members": []}
        for k in kids:
            k["parent"], k["_dirty"], k["_placed"] = p["id"], True, True
        note = _str(it.get("note"), 200)
        log.append(f"- principle: {title} (over {len(kids)} rules: " + "; ".join(k["title"] for k in kids) + ")"
                   + (f" — {note}" if note else ""))
        new.append(p)
        stats["principles_new"] += 1
    return new, log, stats


def recompute(lessons: list[dict], principles: list[dict]) -> tuple[list[dict], list[str]]:
    """Fix links and derive principle status. Returns (dissolved principles, changelog lines).
    A rule's parent must be a principle, an instance's a rule; a principle with < 2 live rules dissolves (it has no
    evidence of its own, so nothing is lost); a principle is verified iff >= 2 of its rules are verified."""
    log: list[str] = []
    by_id = {l["id"]: l for l in lessons + principles if l.get("id")}
    for l in lessons:
        want = "principle" if l.get("level") == "rule" else "rule"
        par = by_id.get(l.get("parent") or "")
        if l.get("parent") and (par is None or par.get("level") != want or par is l):
            l["parent"], l["_dirty"] = "", True
    dissolved = []
    for p in principles:
        kids = [l for l in lessons if l.get("parent") == p["id"] and l.get("level") == "rule"
                and l.get("status") != "refuted"]
        if len(kids) < 2:
            for l in lessons:
                if l.get("parent") == p["id"]:
                    l["parent"], l["_dirty"] = "", True
            dissolved.append(p)
            if not p.get("_new"):
                log.append(f"- dissolved principle: {p['title']} (fewer than 2 rules left)")
            continue
        sessions = {e.get("session") or e.get("ref", "").rsplit(":", 1)[0] for k in kids for e in k["evidence"]}
        dates = sorted(e["date"] for k in kids for e in k["evidence"] if e.get("date"))
        before = (p.get("status"), p.get("seen"), p.get("first_seen"), p.get("last_seen"))
        p["status"] = "verified" if sum(k["status"] == "verified" for k in kids) >= 2 else "candidate"
        p["seen"], p["first_seen"], p["last_seen"] = len(sessions), dates[0] if dates else "", dates[-1] if dates else ""
        if before != (p["status"], p["seen"], p["first_seen"], p["last_seen"]):
            p["_dirty"] = True
            if before[0] and before[0] != p["status"] and not p.get("_new"):
                log.append(f"- status: principle {p['title']} {before[0]} → {p['status']}")
    return dissolved, log


def abstract(lessons: list[dict], principles: list[dict], engine: Engine | None, *, abstraction: str,
             max_principles: int, taken: set, budget_usd: float = 0.0, log=print) -> tuple[list[dict], list[str], dict]:
    """Run the abstraction pass over rules without a home, when any of them is new or changed this run."""
    stats = {"abstraction_calls": 0, "principles_new": 0, "principles_rejected": 0, "attached": 0,
             "filed_as_instance": 0, "errors": 0}
    changes: list[str] = []
    orphans = _orphans(lessons)
    fresh = [l for l in orphans if l.get("_new") or not l.get("_passthrough") or l.get("_dirty")]
    if not fresh or (len(orphans) < 2 and not principles):
        return principles, changes, stats
    for group in abstraction_groups(orphans, abstraction):
        group = [l for l in group if not l.get("_placed")]
        if len(group) < 2 and not principles:
            continue
        if budget_usd and engine.cost_usd >= budget_usd:
            log(f"budget ${budget_usd:.2f} reached; skipping the rest of the abstraction pass")
            break
        max_new = max(0, max_principles - len(principles))
        prompt, rl, pl = abstract_prompt(group, principles, lessons, abstraction=abstraction, max_new=max_new)
        log(f"abstract {len(group)} rule(s) against {len(principles)} principle(s), ~{tokens_estimate(prompt)} tokens …")
        try:
            answer = parse_json(engine(prompt))
        except EngineError as e:
            stats["errors"] += 1
            log(f"  engine error while abstracting: {e}")
            continue
        stats["abstraction_calls"] += 1
        new, lines, s = apply_abstraction(answer, rl, pl, lessons, max_new=max_new, taken=taken)
        for k in ("principles_new", "principles_rejected", "attached", "filed_as_instance"):
            stats[k] += s[k]
        log(f"  {s['principles_new']} new principle(s), {s['attached']} attached to existing, "
            f"{s['filed_as_instance']} filed as instance(s)")
        principles = principles + new
        changes += lines
    for l in lessons:
        l.pop("_placed", None)
    return principles, changes, stats


# --------------------------------------------------------------------------------------------------------------
# engines


class EngineError(RuntimeError):
    pass


class Engine:
    name = "?"
    cost_usd = 0.0
    calls = 0

    def __call__(self, prompt: str) -> str:
        raise NotImplementedError


class CLIEngine(Engine):
    def __init__(self, name: str, model: str = "", timeout: int = 900):
        self.name, self.model, self.timeout = name, model, timeout

    def _run(self, cmd: list[str], prompt: str | None, cwd: str) -> str:
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout, cwd=cwd)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise EngineError(f"{self.name}: {e}") from e
        if p.returncode != 0:
            raise EngineError(f"{self.name} exited {p.returncode}: {(p.stderr or p.stdout)[-800:]}")
        return p.stdout

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        with tempfile.TemporaryDirectory(prefix="lessons-dream-") as cwd:  # no project files for the model to wander into
            if self.name == "claude":
                # Lean headless call: no tools, MCP servers, skills, or default agent system prompt (with them a
                # single call carried ~100k tokens of context and cost dollars; without them ~600 tokens).
                cmd = ["claude", "-p", "--output-format", "json", "--no-session-persistence", "--tools", "",
                       "--strict-mcp-config", "--disable-slash-commands", "--system-prompt", SYSTEM]
                cmd += ["--model", self.model] if self.model else []
                out = self._run(cmd, prompt, cwd)
                try:
                    d = json.loads(out)
                except ValueError:
                    return out
                if isinstance(d, list):  # some versions print the event list
                    d = next((x for x in reversed(d) if isinstance(x, dict) and x.get("type") == "result"), {})
                self.cost_usd += float(d.get("total_cost_usd") or d.get("cost_usd") or 0)
                if d.get("is_error"):
                    raise EngineError(f"claude: {str(d.get('result'))[:500]}")
                return str(d.get("result", ""))
            if self.name == "codex":
                last = os.path.join(cwd, "last.txt")
                cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--output-last-message", last]
                cmd += ["--model", self.model] if self.model else []
                out = self._run(cmd + ["-"], prompt, cwd)
                try:
                    with open(last, encoding="utf-8") as fh:
                        return fh.read()
                except OSError:
                    return out
            if self.name == "opencode":
                cmd = ["opencode", "run"] + (["--model", self.model] if self.model else []) + [prompt]
                return self._run(cmd, None, cwd)
        raise EngineError(f"unknown engine {self.name}")


class FakeEngine(Engine):
    """Deterministic stand-in. Extract: one rule per moment, citing its trigger turn, titled from the person's words
    (scenarios `coding` plus an unknown id, to exercise validation). Consolidate: merge every member into one.
    Abstract: one principle over every listed rule (the code rejects it when that is a single rule).
    Pass `respond(prompt) -> str` to script anything else."""
    name = "fake"

    def __init__(self, respond: Callable[[str], str] | None = None):
        self.respond = respond
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if self.respond:
            return self.respond(prompt)
        if prompt.startswith(EXTRACT_MARK):
            out = []
            for block in prompt.split("### Moment ")[1:]:
                m = re.search(r"\[(r\d+)\] USER[^:]*<- the moment: (.*)", block)
                if not m:
                    continue
                words = " ".join(re.findall(r"[A-Za-z]+", m.group(2))[:6]) or "follow the person's correction"
                out.append({"title": f"Avoid: {words}", "level": "rule", "scope": "global", "kind": "pitfall",
                            "scenarios": ["coding", "not-a-scenario"], "strength": "must",
                            "trigger": "when " + words, "do": "Do what the person asked.", "why": m.group(2)[:100],
                            "themes": [max(words.split(), key=len).lower()],
                            "evidence": [{"ref": m.group(1), "quote": m.group(2)[:80]}]})
            return json.dumps(out)
        if prompt.startswith(ABSTRACT_MARK):
            rules = re.findall(r"^### (R\d+)", prompt, re.M)
            first = re.search(r"^### R1 .*\ntitle: (.*)$", prompt, re.M)
            return json.dumps({"principles": [{"rules": rules, "title": "Principle: " + (first.group(1) if first else "x"),
                                               "do": "Ask before acting on the person's behalf.", "scenarios": ["any"],
                                               "note": "fake principle"}], "instances": []})
        labels = re.findall(r"^### (L\d+)", prompt, re.M)
        title = re.search(r"^title: (.*)$", prompt, re.M)
        return json.dumps({"lessons": [{"merge": labels, "title": title.group(1) if title else "merged",
                                        "note": "fake merge"}]})


def pick_engine(name: str, model: str = "", timeout: int = 900) -> Engine:
    if name == "fake":
        return FakeEngine()
    if name == "auto":
        for cand, exe in (("claude", "claude"), ("codex", "codex"), ("opencode", "opencode")):
            if shutil.which(exe):
                return CLIEngine(cand, model, timeout)
        raise EngineError("no agent CLI found on PATH (claude, codex or opencode); use --engine fake or --dry-run")
    if not shutil.which(name):
        raise EngineError(f"{name} not found on PATH")
    return CLIEngine(name, model, timeout)


# --------------------------------------------------------------------------------------------------------------
# passes


def extract(moments: list[dict], engine: Engine | None, *, state: State | None, run_id: str, budget_tokens: int,
            max_batches: int, budget_usd: float = 0.0, dry_run: bool = False, scenarios=None,
            log=print) -> tuple[list[dict], dict]:
    fresh, keys_by_dupe = [], {}
    for m in moments:
        k = moment_key(m)
        if state and state.seen(k):
            continue
        t = m.get("trigger") or {}
        dupe = digest(t.get("ts", ""), (t.get("text") or "")[:300])  # the same turn copied into a resumed session
        if dupe in keys_by_dupe:
            keys_by_dupe[dupe].append(k)
            continue
        keys_by_dupe[dupe] = [k]
        m["_keys"] = keys_by_dupe[dupe]
        fresh.append(m)
    batches = make_batches(fresh, budget_tokens, scenarios)
    stats = {"moments_total": len(moments), "moments_new": len(fresh), "batches_total": len(batches),
             "batches_run": 0, "lessons_in": 0, "kept": 0, "refs_invented": 0, "dropped_no_evidence": 0,
             "quotes_replaced": 0, "dropped_scenario": 0, "strength_downgraded": 0, "scenarios_unknown": 0, "errors": 0}
    candidates = []
    for bi, batch in enumerate(batches[:max_batches] if max_batches else batches, 1):
        prompt, refs = extract_prompt(batch, scenarios)
        if dry_run:
            log(f"\n===== extract batch {bi}/{len(batches)}: {len(batch)} moments, ~{tokens_estimate(prompt)} tokens =====")
            log(prompt)
            continue
        if budget_usd and engine.cost_usd >= budget_usd:
            log(f"budget ${budget_usd:.2f} reached (${engine.cost_usd:.2f} spent); stopping extraction")
            break
        log(f"extract batch {bi}/{len(batches)}: {len(batch)} moments, ~{tokens_estimate(prompt)} tokens …")
        try:
            answer = engine(prompt)
        except EngineError as e:
            stats["errors"] += 1
            log(f"  engine error: {e}")
            continue
        parsed = parse_json(answer)
        if parsed is None:
            stats["errors"] += 1
            log(f"  could not parse the model's answer: {answer[:300]!r}")
            continue
        found, s = validate(parsed, refs, scenarios)
        for k in ("lessons_in", "kept", "refs_invented", "dropped_no_evidence", "quotes_replaced", "dropped_scenario",
                  "strength_downgraded"):
            stats[k] += s[k]
        stats["scenarios_unknown"] += len(s["scenarios_unknown"])
        stats["batches_run"] += 1
        log(f"  {s['kept']} lesson(s) kept of {s['lessons_in']}; {s['refs_invented']} invented ref(s) dropped")
        if s["scenarios_unknown"]:
            log(f"  warning: unknown scenario(s) dropped: {', '.join(sorted(set(s['scenarios_unknown'])))} "
                f"(see docs/SCENARIOS.md)")
        for c in found:
            c["_new"] = True
        candidates += found
        if state:
            state.mark([k for m in batch for k in m["_keys"]], run_id)
            state.save()
    return candidates, stats


def consolidate(existing: list[dict], candidates: list[dict], engine: Engine | None, *, out_dir: str, run_id: str,
                budget_usd: float = 0.0, dry_run: bool = False, threshold: float | None = None,
                abstraction: str = "medium", max_principles: int = 10, log=print) -> dict:
    if threshold is None:
        threshold = ABSTRACTION[abstraction]["merge"]
    for c in candidates:
        c["_new"] = True
    principles = [l for l in existing if l.get("level") == "principle"]
    existing = [l for l in existing if l.get("level") != "principle"]
    clusters = cluster(existing + candidates, threshold)
    stats = {"clusters": len(clusters), "model_calls": 0, "written": 0, "removed_files": 0, "errors": 0}
    changes: list[str] = []
    final: list[dict] = []
    # (a) merge duplicate rules / instances
    for c in clusters:
        has_new = any(l.get("_new") for l in c)
        if not has_new:
            final += [dict(l, _members=[l], _passthrough=True) for l in c]
            continue
        if len(c) == 1:
            final.append(dict(c[0], _members=[c[0]], _passthrough=True))
            changes.append(f"- added: {c[0]['title']} ({c[0].get('level', 'rule')}, {len(c[0]['evidence'])} evidence, "
                           f"seen {c[0]['seen']}, {c[0]['status']})")
            continue
        prompt, ehandles = consolidate_prompt(c)
        if dry_run:
            log(f"\n===== consolidate cluster of {len(c)} =====")
            log(prompt)
            continue
        merged = None
        if not (budget_usd and engine.cost_usd >= budget_usd):
            try:
                answer = parse_json(engine(prompt))
                stats["model_calls"] += 1
                merged, log_lines = apply_merge(c, answer, ehandles)
                changes += log_lines
            except EngineError as e:
                stats["errors"] += 1
                log(f"  engine error while consolidating: {e}")
        if merged is None:  # no model: keep everything, nothing lost
            merged = [dict(l, _members=[l], _passthrough=True) for l in c]
        for l in merged:
            if l.get("_new") and l.get("_passthrough"):
                changes.append(f"- added: {l['title']} ({l.get('level', 'rule')}, {len(l['evidence'])} evidence, "
                               f"seen {l['seen']}, {l['status']})")
            elif not l.get("_passthrough"):
                was = {m.get("level", "rule") for m in l["_members"] if not m.get("_new")}
                if was and l.get("level") not in was:
                    changes.append(f"- level: {l['title']} — {'/'.join(sorted(was))} → {l['level']}")
        final += merged
    if dry_run:
        return stats
    # ids: existing ids stay (git-friendly rewrites in place), new lessons get unique slugs; links follow merges
    taken = {l["id"] for l in final + principles if l.get("id")}
    for l in final:
        if not l.get("id"):
            l["id"] = _unique_id(slugify(l["title"]), taken)
    moved = {m["id"]: l["id"] for l in final for m in l["_members"] if m.get("id") and m["id"] != l["id"]}
    for l in final:
        if l.get("parent") in moved:
            l["parent"], l["_dirty"] = moved[l["parent"]], True
    # (b) abstraction: principles over rules, instances under rules
    principles = [dict(p, _members=[p], _passthrough=True) for p in principles]
    principles, lines, astats = abstract(final, principles, engine, abstraction=abstraction,
                                         max_principles=max_principles, taken=taken, budget_usd=budget_usd, log=log)
    changes += lines
    stats["model_calls"] += astats["abstraction_calls"]
    stats["errors"] += astats["errors"]
    stats.update({k: v for k, v in astats.items() if k not in ("errors",)})
    # (c) links and status
    dissolved, lines = recompute(final, principles)
    changes += lines
    principles = [p for p in principles if all(p is not d for d in dissolved)]
    everything = principles + final
    keep_paths = set()
    for l in everything:
        changed = l.get("_new") or not l.get("_passthrough") or not l.get("path") or l.get("_dirty")
        path = os.path.join(out_dir, scope_dir(l["scope"]), f"{l['id']}.md")
        keep_paths.add(os.path.abspath(path))
        if changed or not os.path.exists(path):
            write_lesson(out_dir, l)
            stats["written"] += 1
    # (d) files of lessons merged into another one (their evidence now lives in the merged file), and of dissolved
    # principles (which carry no evidence)
    for l in final + dissolved:
        for m in l.get("_members") or []:
            p = m.get("path")
            if p and os.path.abspath(p) not in keep_paths and os.path.exists(p):
                os.remove(p)
                stats["removed_files"] += 1
    stats["lessons"] = len(everything)
    for lv in LEVELS:
        stats[lv + "s"] = sum(l.get("level") == lv for l in everything)
    stats["verified"] = sum(l["status"] == "verified" for l in everything)
    stats["refuted"] = sum(l["status"] == "refuted" for l in everything)
    if changes:
        append_changelog(out_dir, run_id, changes, stats)
    return stats


def append_changelog(out_dir: str, run_id: str, changes: list[str], stats: dict) -> None:
    path = os.path.join(out_dir, "CHANGELOG.md")
    os.makedirs(out_dir, exist_ok=True)
    head = "" if os.path.exists(path) else "# Lessons changelog\n\nWhat each dream changed. Newest last.\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{head}\n## {run_id}\n\n{len(changes)} change(s); {stats.get('lessons', 0)} lessons "
                 f"({stats.get('principles', 0)} principles, {stats.get('rules', 0)} rules, "
                 f"{stats.get('instances', 0)} instances), {stats.get('verified', 0)} verified, "
                 f"{stats.get('refuted', 0)} refuted.\n\n")
        fh.write("\n".join(changes) + "\n")


def write_candidates(cand_dir: str, candidates: list[dict]) -> None:
    taken = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(cand_dir, "**", "*.md"), recursive=True)}
    for c in candidates:
        c = dict(c, id=_unique_id(slugify(c["title"]), taken))
        write_lesson(cand_dir, c)


# --------------------------------------------------------------------------------------------------------------
# entry point


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lessons dream", description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="shards", action="append", default=[], metavar="GLOB",
                   help="exported moment shards (JSONL); default: read every source's transcripts")
    p.add_argument("--sources", default=",".join(TOOLS), help="sources to read when --from is not given")
    p.add_argument("--engine", default="auto", choices=["auto", "claude", "codex", "opencode", "fake"])
    p.add_argument("--model", default="", help="model name passed to the engine CLI")
    p.add_argument("--home", default="", help="state + candidates directory (default ~/.lessons, or <out>.dream when --out is given)")
    p.add_argument("--out", default="", help="final lessons directory (default <home>/lessons)")
    p.add_argument("--add-candidates", action="append", default=[], metavar="DIR",
                   help="also consolidate lesson files from DIR (e.g. hand-extracted candidates)")
    p.add_argument("--max-batches", type=int, default=10, help="extract at most N batches this run (0 = all)")
    p.add_argument("--batch-tokens", type=int, default=12000, help="token budget per extract prompt (estimate)")
    p.add_argument("--budget-usd", type=float, default=0.0, help="advisory: stop calling the model once spent (claude reports cost)")
    p.add_argument("--threshold", type=float, default=None,
                   help="similarity needed to send two lessons to the merge pass (default: set by --abstraction)")
    p.add_argument("--abstraction", default="medium", choices=list(ABSTRACTION),
                   help="how aggressively to generalise: high = fewer, broader principles and rules")
    p.add_argument("--max-principles", type=int, default=10, help="cap on the number of principles")
    p.add_argument("--scenarios", default="", help="comma-separated scenario ids (docs/SCENARIOS.md): only learn "
                                                   "lessons for these")
    p.add_argument("--timeout", type=int, default=900, help="seconds per model call")
    p.add_argument("--no-consolidate", action="store_true", help="extract candidates only")
    p.add_argument("--consolidate-only", action="store_true", help="skip extraction")
    p.add_argument("--dry-run", action="store_true", help="print batches and prompts; call no model, write nothing")
    return p


def run(args, engine: Engine | None = None, log=print) -> dict:
    out_dir = os.path.abspath(os.path.expanduser(args.out)) if args.out else ""
    if args.home:
        home = os.path.abspath(os.path.expanduser(args.home))
    elif out_dir:
        home = out_dir.rstrip("/") + ".dream"
    else:
        home = os.path.expanduser("~/.lessons")
    out_dir = out_dir or os.path.join(home, "lessons")
    run_id = time.strftime("%Y-%m-%d %H:%M:%S")
    abstraction = getattr(args, "abstraction", "medium") or "medium"
    max_principles = getattr(args, "max_principles", 10)
    scenarios = [x.strip() for x in (getattr(args, "scenarios", "") or "").split(",") if x.strip()]
    bad = [x for x in scenarios if x not in SCENARIOS]
    if bad:
        raise EngineError(f"unknown scenario(s) {', '.join(bad)}; known: {', '.join(SCENARIOS)}")
    state = None if args.dry_run else State(os.path.join(home, "state.json"))
    if engine is None and not args.dry_run:
        engine = pick_engine(args.engine, args.model, args.timeout)
    result = {"home": home, "out": out_dir, "engine": getattr(engine, "name", "none")}

    candidates: list[dict] = []
    if not args.consolidate_only:
        prints = {}
        if args.shards:
            from . import projects
            moments = allowed_moments(shard_moments(args.shards), projects.load())
        else:
            moments, prints = live_moments(state, [s for s in args.sources.split(",") if s])
        candidates, result["extract"] = extract(
            moments, engine, state=state, run_id=run_id, budget_tokens=args.batch_tokens,
            max_batches=args.max_batches, budget_usd=args.budget_usd, dry_run=args.dry_run,
            scenarios=scenarios or None, log=log)
        if state:
            pending = {}
            for m in moments:  # a session is done when all its moments are
                sid = f"{m.get('source')}:{m.get('session')}"
                pending[sid] = pending.get(sid, True) and state.seen(moment_key(m))
            for sid, fp in prints.items():
                if pending.get(sid, True):
                    state.set_session(sid, fp)
        if candidates:
            write_candidates(os.path.join(home, "candidates", run_id[:10]), candidates)
    from . import projects
    patterns = projects.load()
    for d in args.add_candidates:
        extra = projects.allowed_lessons(read_lessons(os.path.expanduser(d)), patterns)
        for l in extra:
            l.pop("path", None)  # never delete someone else's candidate files
            l["id"] = ""
        candidates += extra

    if not args.no_consolidate and (candidates or args.dry_run):
        existing = read_lessons(out_dir) if os.path.isdir(out_dir) else []
        hit = projects.matching_lessons(existing, patterns)
        if hit:  # dropping them could dissolve a principle and delete its file, so stop before any model call
            raise EngineError(f"{len(hit)} existing lesson(s) match {projects.default_file()}: "
                              f"{', '.join(sorted(str(l.get('id')) for l in hit))}; move or delete them first")
        result["consolidate"] = consolidate(existing, candidates, engine, out_dir=out_dir, run_id=run_id,
                                            budget_usd=args.budget_usd, dry_run=args.dry_run,
                                            threshold=args.threshold, abstraction=abstraction,
                                            max_principles=max_principles, log=log)
    if engine is not None:
        result["model_calls"], result["cost_usd"] = engine.calls, round(engine.cost_usd, 4)
    if state:
        state.add_run({"run": run_id, "engine": result["engine"], "abstraction": abstraction,
                       "max_principles": max_principles, "scenarios": scenarios, "extract": result.get("extract"),
                       "consolidate": result.get("consolidate"), "cost_usd": result.get("cost_usd", 0)})
        state.save()
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except EngineError as e:
        print(f"dream: {e}", file=sys.stderr)
        return 2
    if not args.dry_run:
        print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
