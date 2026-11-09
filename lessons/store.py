"""Read lesson files and search them. Lessons are Markdown with YAML-ish frontmatter (docs/LESSON_FORMAT.md)."""
from __future__ import annotations
import math, os, re
from collections import Counter

DEFAULT_DIR = os.path.expanduser(os.environ.get("LESSONS_DIR", "~/.lessons/lessons"))


def _scalar(v: str):
    v = v.strip()
    if not v:
        return ""
    if v[0] in "\"'":
        q = v[0]
        end = v.rfind(q)
        s = v[1:end] if end > 0 else v[1:]
        return s.replace('\\"', '"') if q == '"' else s.replace("''", "'")
    v = re.sub(r"\s+#.*$", "", v).strip()  # trailing comment
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if v in ("true", "false"):
        return v == "true"
    return v


def _parse_min(text: str) -> tuple[dict, str]:
    """Just enough YAML for lesson frontmatter: scalars, inline lists, lists of scalars or flat maps."""
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return {}, text
    meta: dict = {}
    key = None
    item = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if indent == 0 and ":" in s and not s.startswith("- "):
            key, _, val = s.partition(":")
            key = key.strip()
            meta[key] = _scalar(val) if val.strip() else []
            item = None
        elif key is not None and s.startswith("- "):
            body = s[2:]
            if re.match(r"^[\w-]+:\s", body + " ") and not body[0] in "\"'":
                k, _, v = body.partition(":")
                item = {k.strip(): _scalar(v)}
                if not isinstance(meta[key], list):
                    meta[key] = []
                meta[key].append(item)
            else:
                item = None
                if not isinstance(meta[key], list):
                    meta[key] = []
                meta[key].append(_scalar(body))
        elif item is not None and ":" in s:
            k, _, v = s.partition(":")
            item[k.strip()] = _scalar(v)
    return meta, text[m.end():]


try:  # another module may provide a fuller parser
    from .frontmatter import load as _parse  # type: ignore
except Exception:  # pragma: no cover - fallback
    _parse = _parse_min


def parse(text: str) -> tuple[dict, str]:
    try:
        out = _parse(text)
        if isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], dict):
            return out
    except Exception:
        pass
    return _parse_min(text)


LEVELS = ["principle", "rule", "instance"]          # most general first
STRENGTHS = ["must", "should", "prefer"]            # strongest first


def _as_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    return [x.strip().lower() for x in str(v).split(",") if x.strip()]


def normalize(d: dict) -> dict:
    """Fill v1 fields for v0 files (no `level`): a v0 lesson is a rule for any scenario, strength should.
    `version` records which format the file was written in."""
    d["version"] = 1 if "level" in d else 0
    lvl = str(d.get("level") or "").strip().lower()
    d["level"] = lvl if lvl in LEVELS else "rule"
    par = d.get("parent")
    if isinstance(par, list):
        par = par[0] if par else ""
    d["parent"] = str(par or "").strip()
    d["scenarios"] = _as_list(d.get("scenarios")) or ["any"]
    st = str(d.get("strength") or "").strip().lower()
    d["strength"] = st if st in STRENGTHS else "should"
    d.setdefault("status", "candidate")
    return d


def load(root: str | None = None) -> list[dict]:
    """Every lesson under root as a dict: frontmatter fields (v1-normalized) + body, path (relative to root), abspath."""
    root = os.path.expanduser(root or DEFAULT_DIR)
    out = []
    for dirpath, _, files in os.walk(root):
        for f in sorted(files):
            if not f.endswith(".md") or f.upper() == "INDEX.MD":
                continue
            p = os.path.join(dirpath, f)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    meta, body = parse(fh.read())
            except OSError:
                continue
            if not meta:
                continue
            d = dict(meta)
            d.setdefault("id", f[:-3])
            d["id"] = str(d["id"])
            d["body"] = body.strip()
            d["path"] = os.path.relpath(p, root)
            d["abspath"] = p
            out.append(normalize(d))
    out.sort(key=lambda d: d["path"])
    return out


# ---------------------------------------------------------------- hierarchy

def hierarchy(lessons: list[dict]) -> dict:
    """{by_id, children: id -> [child ids], principles: [ids], orphans: [ids whose parent is missing],
    unparented: [non-principles with no parent], misleveled: [ids whose parent is not one level up]}.
    Every list is sorted, so output built from it is deterministic."""
    by_id = {d["id"]: d for d in lessons}
    children: dict[str, list[str]] = {}
    orphans, unparented, misleveled = [], [], []
    for d in lessons:
        par = d.get("parent") or ""
        if d["level"] == "principle":
            continue
        if not par:
            unparented.append(d["id"])
        elif par not in by_id or par == d["id"]:
            orphans.append(d["id"])
        else:
            children.setdefault(par, []).append(d["id"])
            if LEVELS.index(by_id[par]["level"]) != LEVELS.index(d["level"]) - 1:
                misleveled.append(d["id"])
    for v in children.values():
        v.sort()
    return {"by_id": by_id, "children": children,
            "principles": sorted(d["id"] for d in lessons if d["level"] == "principle"),
            "orphans": sorted(orphans), "unparented": sorted(unparented), "misleveled": sorted(misleveled)}


def parent_chain(d: dict, by_id: dict) -> list[dict]:
    """Ancestors of d, nearest first (rule -> principle), cycle-safe."""
    out, seen = [], {d["id"]}
    cur = d
    while cur.get("parent") and cur["parent"] in by_id and cur["parent"] not in seen:
        cur = by_id[cur["parent"]]
        seen.add(cur["id"])
        out.append(cur)
    return out


def brief(d: dict) -> dict:
    return {k: d.get(k) for k in ("id", "title", "level", "strength", "status", "scenarios", "scope", "trigger")}


def get(lesson_id: str, root: str | None = None) -> dict | None:
    for d in load(root):
        if d.get("id") == lesson_id or d["path"] == lesson_id or d["path"][:-3] == lesson_id:
            return d
    return None


def scope_matches(scope: str, cwd: str | None) -> bool:
    """A project:<path> scope applies when cwd is that path or inside it."""
    scope = str(scope or "")
    if not scope.startswith("project:"):
        return True
    if not cwd:
        return False
    proj = os.path.realpath(os.path.expanduser(scope[len("project:"):].strip()))
    here = os.path.realpath(os.path.expanduser(cwd))
    return here == proj or here.startswith(proj.rstrip(os.sep) + os.sep)


_TOKEN = re.compile(r"[a-z0-9]+|[一-鿿]", re.I)


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text or "")]


def _doc(d: dict) -> list[str]:
    themes = d.get("themes") or []
    themes = " ".join(themes) if isinstance(themes, list) else str(themes)
    title, trig = str(d.get("title", "")), str(d.get("trigger", ""))
    # title and trigger say what the lesson is about: count them twice
    return tokens(" ".join([title, title, trig, trig, themes, str(d.get("kind", "")), d.get("body", "")]))


def scenario_matches(d: dict, scenario: str | None) -> bool:
    """Principles are always eligible; others when they list the scenario or `any`."""
    if not scenario or d.get("level") == "principle":
        return True
    sc = d.get("scenarios") or ["any"]
    return "any" in sc or scenario.lower() in sc


def eligible(d: dict, scenario: str | None = None, level_max: str = "rule", cwd: str | None = None,
             verified_only: bool = True, strength_min: str | None = None, include_refuted: bool = False,
             any_scope: bool = False) -> bool:
    """The filters search and index share. `cwd` drops project lessons of other projects (None = drop all
    project lessons unless any_scope)."""
    if d.get("status") == "refuted" and not include_refuted:
        return False
    lvl = level_max if level_max in LEVELS else "rule"
    if LEVELS.index(d.get("level", "rule")) > LEVELS.index(lvl):
        return False
    if verified_only and d.get("level") != "principle" and d.get("status") != "verified":
        return False
    if strength_min and strength_min in STRENGTHS and \
            STRENGTHS.index(d.get("strength", "should")) > STRENGTHS.index(strength_min):
        return False
    if not any_scope and not scope_matches(d.get("scope", ""), cwd):
        return False
    return scenario_matches(d, scenario)


_STRENGTH_BOOST = {"must": 1.3, "should": 1.0, "prefer": 0.85}


def search(query: str, scope: str | None = None, k: int = 8, root: str | None = None,
           lessons: list[dict] | None = None, include_refuted: bool = False, scenario: str | None = None,
           level_max: str = "rule", cwd: str | None = None, verified_only: bool = True,
           strength_min: str | None = None) -> list[dict]:
    """BM25 over title/trigger/themes/body, after the `eligible` filters.
    `cwd` (or the older name `scope`) is a working directory: project lessons for other projects are dropped,
    matching ones boosted; with neither, project lessons are searched too. Verified and `must` lessons rank
    higher. An empty query lists the eligible lessons (principles first, then by strength and seen).
    Each hit carries `score` and `chain` (its ancestors, nearest first, as brief dicts)."""
    all_docs = lessons if lessons is not None else load(root)
    all_docs = [d if "version" in d else normalize(dict(d)) for d in all_docs]
    by_id = {d["id"]: d for d in all_docs}
    where = cwd or scope
    docs = [d for d in all_docs if eligible(d, scenario, level_max, where, verified_only, strength_min,
                                            include_refuted, any_scope=where is None)]
    q = set(tokens(query))
    if not docs:
        return []
    scored = []
    if not q:
        for d in docs:
            scored.append(dict(d, score=0.0))
        scored.sort(key=lambda d: (LEVELS.index(d["level"]), STRENGTHS.index(d["strength"]),
                                   -_seen(d), d["id"]))
    else:
        toks = [_doc(d) for d in docs]
        n = len(docs)
        avg = sum(len(t) for t in toks) / n or 1.0
        df = Counter(w for t in toks for w in set(t))
        k1, b = 1.2, 0.75
        for d, t in zip(docs, toks):
            tf = Counter(t)
            s = 0.0
            for w in q:
                if w in tf:
                    idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                    s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg))
            if s <= 0:
                continue
            if d.get("status") == "verified":
                s *= 1.5
            s *= _STRENGTH_BOOST.get(d.get("strength", "should"), 1.0)
            if where and str(d.get("scope", "")).startswith("project:"):
                s *= 1.3
            scored.append(dict(d, score=round(s, 4)))
        scored.sort(key=lambda d: (-d["score"], d["id"]))
    out = scored[:k]
    for h in out:
        h["chain"] = [brief(p) for p in parent_chain(h, by_id)]
    return out


def _seen(d: dict) -> int:
    try:
        return int(d.get("seen") or 0)
    except (TypeError, ValueError):
        return 0
