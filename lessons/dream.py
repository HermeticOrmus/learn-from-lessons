"""dream: turn transcript moments into verified, evidence-linked lessons with the person's own agent CLI as the model.

Two passes, both incremental:
  extract      new moments (lessons.signals over every source, or exported shard files) are batched into prompts
               (prompts/extract.md); the model answers with lessons as JSON; lessons citing references that are not
               in the batch are dropped; dates and `seen` come from the cited turns, never from the model.
  consolidate  candidates and existing lessons are clustered locally (title/trigger/theme overlap); each cluster
               that holds something new is rewritten by the model (prompts/consolidate.md): merged, kept apart or
               refuted. Evidence is only ever unioned, never deleted. Every change is appended to CHANGELOG.md.

The model is whichever agent CLI is installed: `claude -p`, `codex exec` or `opencode run` (no API keys).
`--engine fake` answers deterministically for tests; `--dry-run` prints the batches and prompts and calls nothing.

    python3 -m lessons.dream --from 'work/shards/*.jsonl' --max-batches 1 --engine claude --out work/dream-smoke
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
KINDS = ("preference", "workflow", "pitfall", "fact", "communication")
TOOLS = ("claude-code", "codex", "opencode")
EXTRACT_MARK = "<!-- lessons:extract -->"
CONSOLIDATE_MARK = "<!-- lessons:consolidate -->"
PROMPT_MARKS = (EXTRACT_MARK, CONSOLIDATE_MARK)
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
    from .signals import find
    from .sources import SOURCES
    out, prints = [], {}
    seen: set = set()  # turns already found in a forked or resumed session
    for name in sources:
        for s in SOURCES[name]():
            if _is_scripted(s):
                continue
            fp = _fingerprint(s)
            sid = f"{s.source}:{s.id}"
            if state and state.session_unchanged(sid, fp):
                continue
            prints[sid] = fp
            out += [moment_dict(m) for m in find(s, seen)]
    return out, prints


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
        for t in list(m.get("context") or []) + [trig] + list(m.get("followup") or []):
            n += 1
            h = f"r{n}"
            is_trig = t is trig
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
            lines.append(f"[{h}] {label}: {text if text else '(empty)'}")
            refs[h] = {"ref": t.get("ref", ""), "source": m.get("source", ""), "session": m.get("session", ""),
                       "date": (t.get("ts") or trig.get("ts") or "")[:10], "role": role,
                       "text": redact(t.get("text") or ""), "project": m.get("project") or m.get("cwd") or ""}
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), refs


def _template(name: str) -> str:
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as fh:
        return fh.read()


def make_batches(moments: list[dict], budget_tokens: int) -> list[list[dict]]:
    """Greedy packing; moments of one session stay together so the model sees repeats side by side."""
    order = sorted(moments, key=lambda m: (m.get("project") or "", m.get("session") or "",
                                           (m.get("trigger") or {}).get("ts") or ""))
    budget_tokens = max(1, budget_tokens - tokens_estimate(_template("extract.md")))
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


def extract_prompt(batch: list[dict]) -> tuple[str, dict]:
    text, refs = render_batch(batch)
    return _template("extract.md").replace("{{MOMENTS}}", text), refs


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
    """Derive seen / first_seen / last_seen / status from the evidence."""
    ev = lesson["evidence"]
    sessions = {e.get("session") or e.get("ref", "").rsplit(":", 1)[0] for e in ev}
    dates = sorted(e["date"] for e in ev if e.get("date"))
    lesson["seen"] = len(sessions)
    lesson["first_seen"] = dates[0] if dates else ""
    lesson["last_seen"] = dates[-1] if dates else ""
    if lesson.get("status") != "refuted":
        lesson["status"] = "verified" if lesson["seen"] >= 2 or lesson.get("status") == "verified" else "candidate"
    return lesson


def validate(raw, refs: dict) -> tuple[list[dict], dict]:
    """Model lessons -> candidates. Evidence must cite a reference from this batch; a quote that is not in the cited
    turn is replaced by that turn's own words. Lessons left without evidence are dropped."""
    stats = {"lessons_in": 0, "kept": 0, "dropped_no_evidence": 0, "refs_invented": 0, "quotes_replaced": 0}
    by_full = {v["ref"]: h for h, v in refs.items() if v["ref"]}
    out = []
    if isinstance(raw, dict):
        raw = raw.get("lessons", [])
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        stats["lessons_in"] += 1
        title, do = _str(item.get("title"), 160), _str(item.get("do"))
        evidence, used = [], set()
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
        if not evidence or not title or not do:
            stats["dropped_no_evidence"] += 1
            continue
        kind = str(item.get("kind", "")).strip().lower()
        lesson = {"title": title, "scope": _scope(item.get("scope"), evidence),
                  "kind": kind if kind in KINDS else "pitfall", "trigger": _str(item.get("trigger")),
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


def dump_lesson(l: dict) -> str:
    lines = ["---", f"id: {l['id']}", f"title: {_q(l['title'])}", f"scope: {_q(l['scope'])}", f"kind: {l['kind']}",
             f"trigger: {_q(l.get('trigger', ''))}", f"status: {l['status']}",
             "themes: [" + ", ".join(_q(t) for t in l.get("themes") or []) + "]", "evidence:"]
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
        l = {"id": str(meta.get("id") or slugify(meta["title"])), "title": str(meta["title"]),
             "scope": str(meta.get("scope") or "global"), "kind": str(meta.get("kind") or "pitfall"),
             "trigger": str(meta.get("trigger") or ""), "status": str(meta.get("status") or "candidate"),
             "themes": _themes(meta.get("themes")), "evidence": [{k: str(v) for k, v in e.items()} for e in ev],
             "do": sections.get("Do", "").strip(), "why": sections.get("Why", "").strip(),
             "check": sections.get("Check", "").strip(), "path": path}
        if meta.get("refuted_by"):
            l["refuted_by"] = meta["refuted_by"] if isinstance(meta["refuted_by"], list) else [meta["refuted_by"]]
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
        lines = [f"### L{i} ({'new candidate' if l.get('_new') else 'existing'}, status {l['status']}, "
                 f"seen in {l['seen']} sessions, scope {l['scope']}, kind {l['kind']})",
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
    return _template("consolidate.md").replace("{{LESSONS}}", "\n\n".join(blocks)), ehandles


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
        merged = {"id": base.get("id", ""), "title": _str(item.get("title"), 160) or base["title"],
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
    """Deterministic stand-in. Extract: one lesson per moment, citing its trigger turn, titled from the person's words.
    Consolidate: merge every member into one. Pass `respond(prompt) -> str` to script anything else."""
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
                out.append({"title": f"Avoid: {words}", "scope": "global", "kind": "pitfall",
                            "trigger": "when " + words, "do": "Do what the person asked.", "why": m.group(2)[:100],
                            "themes": ["fake"], "evidence": [{"ref": m.group(1), "quote": m.group(2)[:80]}]})
            return json.dumps(out)
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
            max_batches: int, budget_usd: float = 0.0, dry_run: bool = False, log=print) -> tuple[list[dict], dict]:
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
    batches = make_batches(fresh, budget_tokens)
    stats = {"moments_total": len(moments), "moments_new": len(fresh), "batches_total": len(batches),
             "batches_run": 0, "lessons_in": 0, "kept": 0, "refs_invented": 0, "dropped_no_evidence": 0,
             "quotes_replaced": 0, "errors": 0}
    candidates = []
    for bi, batch in enumerate(batches[:max_batches] if max_batches else batches, 1):
        prompt, refs = extract_prompt(batch)
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
        found, s = validate(parsed, refs)
        for k in ("lessons_in", "kept", "refs_invented", "dropped_no_evidence", "quotes_replaced"):
            stats[k] += s[k]
        stats["batches_run"] += 1
        log(f"  {s['kept']} lesson(s) kept of {s['lessons_in']}; {s['refs_invented']} invented ref(s) dropped")
        for c in found:
            c["_new"] = True
        candidates += found
        if state:
            state.mark([k for m in batch for k in m["_keys"]], run_id)
            state.save()
    return candidates, stats


def consolidate(existing: list[dict], candidates: list[dict], engine: Engine | None, *, out_dir: str, run_id: str,
                budget_usd: float = 0.0, dry_run: bool = False, threshold: float = 0.3, log=print) -> dict:
    for c in candidates:
        c["_new"] = True
    clusters = cluster(existing + candidates, threshold)
    stats = {"clusters": len(clusters), "model_calls": 0, "written": 0, "removed_files": 0, "errors": 0}
    changes: list[str] = []
    final: list[dict] = []
    for c in clusters:
        has_new = any(l.get("_new") for l in c)
        if not has_new:
            final += [dict(l, _members=[l], _passthrough=True) for l in c]
            continue
        if len(c) == 1:
            final.append(dict(c[0], _members=[c[0]], _passthrough=True))
            changes.append(f"- added: {c[0]['title']} ({len(c[0]['evidence'])} evidence, seen {c[0]['seen']}, "
                           f"{c[0]['status']})")
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
                changes.append(f"- added: {l['title']} ({len(l['evidence'])} evidence, seen {l['seen']}, {l['status']})")
        final += merged
    if dry_run:
        return stats
    # ids: existing ids stay (git-friendly rewrites in place), new lessons get unique slugs
    taken = {l["id"] for l in final if l.get("id")}
    for l in final:
        if not l.get("id"):
            l["id"] = _unique_id(slugify(l["title"]), taken)
    keep_paths = set()
    for l in final:
        changed = l.get("_new") or not l.get("_passthrough") or not l.get("path")
        path = os.path.join(out_dir, scope_dir(l["scope"]), f"{l['id']}.md")
        keep_paths.add(os.path.abspath(path))
        if changed or not os.path.exists(path):
            write_lesson(out_dir, l)
            stats["written"] += 1
    # files of lessons that were merged into another one: their evidence now lives in the merged file
    for l in final:
        for m in l["_members"]:
            p = m.get("path")
            if p and os.path.abspath(p) not in keep_paths and os.path.exists(p):
                os.remove(p)
                stats["removed_files"] += 1
    stats["lessons"] = len(final)
    stats["verified"] = sum(l["status"] == "verified" for l in final)
    stats["refuted"] = sum(l["status"] == "refuted" for l in final)
    if changes:
        append_changelog(out_dir, run_id, changes, stats)
    return stats


def append_changelog(out_dir: str, run_id: str, changes: list[str], stats: dict) -> None:
    path = os.path.join(out_dir, "CHANGELOG.md")
    os.makedirs(out_dir, exist_ok=True)
    head = "" if os.path.exists(path) else "# Lessons changelog\n\nWhat each dream changed. Newest last.\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{head}\n## {run_id}\n\n{len(changes)} change(s); {stats.get('lessons', 0)} lessons, "
                 f"{stats.get('verified', 0)} verified, {stats.get('refuted', 0)} refuted.\n\n")
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
    p.add_argument("--threshold", type=float, default=0.3, help="similarity needed to cluster two lessons")
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
    state = None if args.dry_run else State(os.path.join(home, "state.json"))
    if engine is None and not args.dry_run:
        engine = pick_engine(args.engine, args.model, args.timeout)
    result = {"home": home, "out": out_dir, "engine": getattr(engine, "name", "none")}

    candidates: list[dict] = []
    if not args.consolidate_only:
        prints = {}
        if args.shards:
            moments = shard_moments(args.shards)
        else:
            moments, prints = live_moments(state, [s for s in args.sources.split(",") if s])
        candidates, result["extract"] = extract(
            moments, engine, state=state, run_id=run_id, budget_tokens=args.batch_tokens,
            max_batches=args.max_batches, budget_usd=args.budget_usd, dry_run=args.dry_run, log=log)
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
    for d in args.add_candidates:
        extra = read_lessons(os.path.expanduser(d))
        for l in extra:
            l.pop("path", None)  # never delete someone else's candidate files
            l["id"] = ""
        candidates += extra

    if not args.no_consolidate and (candidates or args.dry_run):
        existing = read_lessons(out_dir) if os.path.isdir(out_dir) else []
        result["consolidate"] = consolidate(existing, candidates, engine, out_dir=out_dir, run_id=run_id,
                                            budget_usd=args.budget_usd, dry_run=args.dry_run,
                                            threshold=args.threshold, log=log)
    if engine is not None:
        result["model_calls"], result["cost_usd"] = engine.calls, round(engine.cost_usd, 4)
    if state:
        state.add_run({"run": run_id, "engine": result["engine"], "extract": result.get("extract"),
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
