"""Lesson libraries, training-only filters, and the memory schemes compared in the evaluation.

A scheme maps a moment to the memory text injected into the predictor. Every scheme is registered in `schemes()`;
external libraries (`library-<name>/`) are added automatically once their DONE file exists.
"""
from __future__ import annotations
import glob, math, os, re
from collections import Counter

from common import ROOT, WORK, clip, call, parse_json, render_context, tokens_estimate
from lessons.dream import read_lessons

SKIP = {"CHANGELOG.md", "INDEX.md", "README.md", "SCHEME.md", "PREDICT.md", "DETECTOR_FEEDBACK.md"}
SCENARIOS = ["writing-paper", "writing-outward", "figures-visual", "coding", "debugging", "experiments",
             "research-ideation", "planning-discussion", "release-deploy", "reporting", "long-autonomous",
             "cost-resources"]
EXTERNAL = {"v1": "library-v2", "cluster": "library-cluster", "graph": "library-graph",
            "profile": "library-profile", "playbook": "library-playbook"}


# ---------------------------------------------------------------------------------------------------------------
# loading + filtering

def load(folder: str) -> list[dict]:
    out = []
    for l in read_lessons(folder):
        if os.path.basename(l.get("path", "")) in SKIP or l.get("status") == "refuted":
            continue
        if not l.get("do"):
            try:
                body = globals()["body"](l["path"])
            except OSError:
                body = ""
            l["do"] = re.sub(r"\s+", " ", body).strip()[:400]
        out.append(l)
    return out


def dates(l):
    return sorted(e.get("date", "") for e in l.get("evidence") or [] if e.get("date"))


def filt_first(ls, cutoff):
    """Keep lessons whose first evidence predates the cutoff (evidence-less lessons are judged by their children)."""
    keep = [l for l in ls if dates(l) and dates(l)[0] < cutoff]
    return _with_parents(ls, keep)


def filt_strict(ls, cutoff):
    """Keep lessons whose *every* evidence item predates the cutoff: no lesson saw a post-cutoff incident."""
    keep = [l for l in ls if dates(l) and dates(l)[-1] < cutoff]
    return _with_parents(ls, keep)


def _with_parents(ls, keep):
    """Evidence-less lessons (principles, traits, playbooks) are kept iff a kept lesson descends from them."""
    by_id = {l["id"]: l for l in ls}
    ids = {l["id"] for l in keep}
    for l in list(keep):
        p = l.get("parent")
        while p and p in by_id and p not in ids:
            ids.add(p)
            p = by_id[p].get("parent")
    return [l for l in ls if l["id"] in ids]


def cited_sessions(folder: str) -> str:
    """All text of a library folder, to test whether a test session id is cited anywhere in it."""
    buf = []
    for p in glob.glob(os.path.join(folder, "**", "*"), recursive=True):
        if os.path.isfile(p):
            try:
                buf.append(open(p, encoding="utf-8", errors="ignore").read())
            except OSError:
                pass
    return "\n".join(buf)


# ---------------------------------------------------------------------------------------------------------------
# rendering

def full(l) -> str:
    s = f"- [{l['id']}] {clip(l['title'], 200)}"
    if l.get("trigger"):
        s += f"\n  when: {clip(l['trigger'], 260)}"
    if l.get("do"):
        s += f"\n  do: {clip(l['do'], 420)}"
    return s


def index_line(l) -> str:
    return f"- [{l['id']}] {clip(l['title'], 200)} — {clip(l.get('trigger', ''), 160)}"


def render(ls, fn=full) -> str:
    return "\n".join(fn(l) for l in ls)


# ---------------------------------------------------------------------------------------------------------------
# search (BM25 over title + trigger + themes + do; English words and CJK bigrams)

def toks(text: str) -> list[str]:
    text = (text or "").lower()
    out = re.findall(r"[a-z][a-z0-9_\-]{1,}", text)
    for run in re.findall(r"[一-鿿]+", text):
        out += [run[i:i + 2] for i in range(max(1, len(run) - 1))]
    return out


class BM25:
    def __init__(self, ls, k1=1.5, b=0.75):
        self.ls = ls
        self.docs = [toks(" ".join([l["title"], l.get("trigger", ""), " ".join(l.get("themes") or []), l.get("do", "")]))
                     for l in ls]
        self.avg = sum(map(len, self.docs)) / max(1, len(self.docs))
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in self.docs]
        self.k1, self.b = k1, b

    def top(self, query: str, k: int):
        q = set(toks(query))
        scores = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avg))
                    for t in q if t in tf)
            scores.append((s, i))
        scores.sort(key=lambda x: (-x[0], x[1]))
        return [self.ls[i] for s, i in scores[:k] if s > 0]


def query_of(m) -> str:
    return render_context(m.get("context") or [], 4)


# ---------------------------------------------------------------------------------------------------------------
# scenario inference (cached haiku call per moment)

SCEN_SYSTEM = "You classify what a coding agent is currently doing. Answer only with JSON."


def scenario(m) -> list[str]:
    prompt = ("Which of these scenarios describe what the agent is doing in the conversation below? Pick 1-3.\n"
              f"Scenarios: {', '.join(SCENARIOS)}\n\nConversation (most recent last):\n"
              f"{render_context(m.get('context') or [], 6)}\n\nAnswer: {{\"scenarios\": [..]}}")
    ans = parse_json(call(prompt, system=SCEN_SYSTEM, model="haiku", tag="scenario")) or {}
    got = [s for s in (ans.get("scenarios") if isinstance(ans, dict) else []) or [] if s in SCENARIOS]
    return got or ["coding"]


def by_scenario(ls, m):
    sc = set(scenario(m)) | {"any"}
    return [l for l in ls if l.get("level") == "principle" or set(l.get("scenarios") or ["any"]) & sc]


# ---------------------------------------------------------------------------------------------------------------
# schemes

def _index_md(folder):
    p = os.path.join(folder, "INDEX.md")
    if not os.path.exists(p):
        return ""
    txt = open(p, encoding="utf-8").read()
    txt = re.sub(r"\]\([^)]*\.md\)", "]", txt)  # file links carry no meaning for the predictor
    return clip(txt, 60000)


def body(path: str) -> str:
    txt = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n.*?\n---\n", txt, re.S)
    return txt[m.end():].strip() if m else txt


def playbook_text(pb: dict, m, allowed: set | None = None) -> str:
    """playbook-any + the playbooks of the moment's inferred scenarios. With `allowed` (v0 lesson ids whose evidence
    all predates the cutoff), a check is kept only if every lesson it cites is allowed."""
    names = ["playbook-any"] + [f"playbook-{s}" for s in scenario(m)]
    out = []
    for n in names:
        if n not in pb:
            continue
        lines = []
        for line in body(pb[n]["path"]).splitlines():
            ids = re.findall(r"`([a-z0-9][a-z0-9\-]+)`", line.split(" — ")[-1]) if " — `" in line else []
            if allowed is not None and ids and not set(ids) <= allowed:
                continue
            lines.append(line)
        out.append(clip("\n".join(lines), 8000))
    return "\n\n".join(out)


def schemes(cutoff: str) -> dict:
    """name -> (memory_fn(moment) -> str, meta). meta: family, leak ('strict'|'first'|'as-built'|'none'), folder."""
    S = {"none": (lambda m: "", {"family": "baseline", "leak": "none"})}
    v0_all = load(os.path.join(ROOT, "library", "lessons"))
    v0 = filt_first(v0_all, cutoff)
    v0s = filt_strict(v0_all, cutoff)
    bm = BM25(v0)
    t_all, t_idx, t_strict = render(v0), render(v0, index_line), render(v0s)
    S["v0-all"] = (lambda m: t_all, {"family": "v0", "leak": "first", "n": len(v0)})
    S["v0-strict"] = (lambda m: t_strict, {"family": "v0", "leak": "strict", "n": len(v0s)})
    S["v0-index"] = (lambda m: t_idx, {"family": "v0", "leak": "first", "n": len(v0)})
    S["v0-search3"] = (lambda m: render(bm.top(query_of(m), 3)), {"family": "v0", "leak": "first", "n": 3})
    S["v0-search8"] = (lambda m: render(bm.top(query_of(m), 8)), {"family": "v0", "leak": "first", "n": 8})
    S["v0-index+search3"] = (lambda m: t_idx + "\n\nMost relevant lessons in full:\n" + render(bm.top(query_of(m), 3)),
                             {"family": "v0", "leak": "first", "n": len(v0)})
    fresh_dir = os.path.join(WORK, "lib-fresh")
    if os.path.isdir(fresh_dir):
        fr = load(fresh_dir)
        if fr:
            t_fr = render(fr)
            t_fri = render(fr, index_line)
            S["fresh-all"] = (lambda m: t_fr, {"family": "fresh", "leak": "none", "n": len(fr)})
            S["fresh-index"] = (lambda m: t_fri, {"family": "fresh", "leak": "none", "n": len(fr)})
            S["fresh-scenario"] = (lambda m: render(by_scenario(fr, m)), {"family": "fresh", "leak": "none", "n": len(fr)})
    for name, sub in EXTERNAL.items():
        folder = os.path.join(ROOT, sub)
        if not os.path.exists(os.path.join(folder, "DONE")):
            continue
        ls = load(folder)
        st = filt_strict(ls, cutoff)
        idx = _index_md(folder)
        meta = {"family": name, "folder": folder}
        if idx:
            S[f"{name}-index"] = ((lambda t: lambda m: t)(idx), {**meta, "leak": "as-built", "n": len(ls)})
        if st:
            t_st = render(st)
            S[f"{name}-strict"] = ((lambda t: lambda m: t)(t_st), {**meta, "leak": "strict", "n": len(st)})
            if any(set(l.get("scenarios") or ["any"]) - {"any"} for l in st):
                S[f"{name}-strict-scenario"] = ((lambda L: lambda m: render(by_scenario(L, m)))(st),
                                                {**meta, "leak": "strict", "n": len(st)})
        if name == "playbook":
            pb = {os.path.basename(l["path"])[:-3]: l for l in ls}
            allowed = {l["id"] for l in v0s}
            S["playbook-scenario"] = ((lambda P: lambda m: playbook_text(P, m))(pb),
                                      {**meta, "leak": "as-built", "n": len(pb)})
            S["playbook-strict-scenario"] = ((lambda P, A: lambda m: playbook_text(P, m, A))(pb, allowed),
                                             {**meta, "leak": "strict", "n": len(pb)})
        if name == "v1" and st:  # abstraction sweep on the hierarchy
            lv = {"principle": ["principle"], "rule": ["principle", "rule"]}
            for top, keep in lv.items():
                sub_ls = [l for l in st if l.get("level") in keep]
                if sub_ls:
                    S[f"v1-strict-{top}"] = ((lambda t: lambda m: t)(render(sub_ls)),
                                             {**meta, "leak": "strict", "n": len(sub_ls)})
    for base in ("none", "graph-strict-scenario", "fresh-index"):
        if base in S:
            S[base + "-rerun"] = (S[base][0], dict(S[base][1], rerun_of=base))
    return S


if __name__ == "__main__":
    import json, sys
    from common import jload
    cut = jload("split.json")["cutoff"]
    for k, (fn, meta) in schemes(cut).items():
        m = jload("split.json")["test"][0]
        print(k, meta.get("leak"), meta.get("n"), tokens_estimate(fn(m)) if "scenario" not in k else "(per-moment)")
