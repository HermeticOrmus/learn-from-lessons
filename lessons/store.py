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


def load(root: str | None = None) -> list[dict]:
    """Every lesson under root as a dict: frontmatter fields + body, path (relative to root), abspath."""
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
            d["body"] = body.strip()
            d["path"] = os.path.relpath(p, root)
            d["abspath"] = p
            out.append(d)
    out.sort(key=lambda d: d["path"])
    return out


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


def search(query: str, scope: str | None = None, k: int = 8, root: str | None = None,
           lessons: list[dict] | None = None, include_refuted: bool = False) -> list[dict]:
    """BM25 over title/trigger/themes/body. `scope` is a cwd: project lessons for other projects are dropped,
    matching ones boosted. Verified lessons rank above candidates. Returns dicts with a `score`."""
    docs = lessons if lessons is not None else load(root)
    docs = [d for d in docs if include_refuted or d.get("status") != "refuted"]
    docs = [d for d in docs if scope is None or scope_matches(d.get("scope", ""), scope)]
    q = set(tokens(query))
    if not q or not docs:
        return []
    toks = [_doc(d) for d in docs]
    n = len(docs)
    avg = sum(len(t) for t in toks) / n or 1.0
    df = Counter(w for t in toks for w in set(t))
    k1, b = 1.2, 0.75
    scored = []
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
        if scope and str(d.get("scope", "")).startswith("project:"):
            s *= 1.3
        scored.append(dict(d, score=round(s, 4)))
    scored.sort(key=lambda d: -d["score"])
    return scored[:k]
