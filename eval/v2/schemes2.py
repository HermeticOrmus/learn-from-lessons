"""v2 memory schemes. Each scheme(m) -> (memory text, injected v0 ids) for a moment, filtered at the moment's cutoff.

Leak handling (after REVIEW-v1 §2):
- rules/instances: kept only if every evidence item predates the cutoff (`strict`);
- principles / traits (no evidence of their own, written from all children): *rebuilt* by Sonnet from the kept
  pre-cutoff children only (`rebuild_parents`, cached per library x cutoff); the original wording is never injected;
- playbook: a check line is kept only if it cites >= 1 v0 lesson and all cited lessons are strict;
- graph resolutions: kept only for edges whose two endpoints are both injected strict rules (resolution text was
  written by an author who saw all data: residual leak, labelled `strict*`);
- `fresh-index`: lessons.dream library built from training moments < c1 only: genuinely leak-free, window A only.
"""
from __future__ import annotations
import json, os, random, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v2common import call, clip, parse_json, tokens_estimate, C1  # noqa: E402
import libraries as L  # noqa: E402  (eval/libraries.py)
from common import ROOT, WORK  # noqa: E402

SCHEMES = ["none", "v0-index", "v1-scenario", "graph-scenario", "profile", "playbook", "fresh-index"]
LEAK = {"none": "none", "fresh-index": "none", "v0-index": "strict*", "v1-scenario": "strict*",
        "graph-scenario": "strict*", "profile": "strict*", "playbook": "strict*"}
_cache: dict = {}


def strict_children(ls, cutoff):
    return [l for l in ls if l.get("level") != "principle" and L.dates(l) and L.dates(l)[-1] < cutoff]


REBUILD_SYS = "You summarise a person's preferences from rules learned about them. Answer only with JSON."
REBUILD = """Below are groups of rules learned from one person's corrections of their coding agents. Each group belongs under one parent heading (a {what}). For each parent, write a new title and a statement of the general preference that **only these listed rules** support — do not add anything the rules do not say. {extra}

{groups}

Answer: {{"<parent id>": {{"title": "...", "do": "..."}}, ...}}"""


def rebuild_parents(name, ls, cutoff):
    """Principles/traits rebuilt from pre-cutoff children only. Returns list of parent dicts (level=principle)."""
    kept = strict_children(ls, cutoff)
    by_id = {l["id"]: l for l in ls}
    groups = {}
    for l in kept:  # attach each kept lesson to its top-most principle ancestor
        p, top = l.get("parent"), None
        while p and p in by_id:
            if by_id[p].get("level") == "principle":
                top = p
            p = by_id[p].get("parent")
        if top:
            groups.setdefault(top, []).append(l)
    if not groups:
        return []
    what = "trait: an observable behaviour the person rewards or corrects" if name == "profile" else "principle"
    extra = ("For traits, 'do' says what the person rewards or corrects and what it predicts, <= 70 words."
             if name == "profile" else "'do' <= 40 words.")
    txt = "\n\n".join(f"### parent {pid}\n" + "\n".join(f"- {clip(c['title'], 200)} — do: {clip(c.get('do', ''), 250)}"
                                                       for c in cs) for pid, cs in sorted(groups.items()))
    ans = parse_json(call(REBUILD.format(what=what, extra=extra, groups=txt), system=REBUILD_SYS, model="sonnet",
                          tag=f"rebuild:{name}:{cutoff}")) or {}
    out = []
    for pid in sorted(groups):
        a = ans.get(pid) if isinstance(ans, dict) else None
        if isinstance(a, dict) and a.get("title"):
            out.append({"id": pid, "title": str(a["title"]), "do": str(a.get("do", "")), "trigger": "",
                        "level": "principle", "scenarios": ["any"], "parent": None})
    return out


def _lib(name, cutoff):
    key = (name, cutoff)
    if key in _cache:
        return _cache[key]
    folder = {"v0": "library/lessons", "v1": "library-v2", "graph": "library-graph", "profile": "library-profile",
              "playbook": "library-playbook"}.get(name)
    if name == "fresh":
        val = L.load(os.path.join(WORK, "lib-fresh"))
    else:
        ls = L.load(os.path.join(ROOT, folder))
        if name in ("v1", "graph", "profile"):
            val = rebuild_parents(name, ls, cutoff) + strict_children(ls, cutoff)
        elif name == "playbook":
            val = {os.path.basename(l["path"])[:-3]: l for l in ls}
        else:
            val = strict_children(ls, cutoff)
    _cache[key] = val
    return val


def graph_resolutions(ids):
    g = json.load(open(os.path.join(ROOT, "library-graph", "graph.json")))
    out = []
    for e in g["edges"]:
        if e["type"] in ("conflicts-with", "trade-off") and e.get("resolution") and e["source"] in ids and e["target"] in ids:
            out.append(f"- {e['source']} vs {e['target']} ({e['type']}): {clip(e['resolution'], 400)}")
    return out


def playbook(m, cutoff, allowed):
    pb = _lib("playbook", cutoff)
    names = ["playbook-any"] + [f"playbook-{s}" for s in L.scenario(m)]
    out, ids = [], set()
    for n in names:
        if n not in pb:
            continue
        lines = []
        for line in L.body(pb[n]["path"]).splitlines():
            if re.match(r"^\s*\d+\.", line):
                cited = re.findall(r"`([a-z0-9][a-z0-9\-]+)`", line.split(" — ")[-1]) if " — " in line else []
                if not cited or not set(cited) <= allowed:
                    continue
                ids |= set(cited)
                line = line.split(" — ")[0]
            elif line.startswith("Run the checks") or line.startswith("**When"):
                pass
            elif not line.startswith("#"):
                continue
            lines.append(line)
        out.append("\n".join(lines))
    return "\n\n".join(out), ids


def memory(name, m, rep=0):
    """-> (text, v0 ids injected). rep > 0 shuffles the lesson order with seed (moment, rep)."""
    cutoff = m["cutoff"]
    v0ids = {l["id"] for l in _lib("v0", C1)} | {l["id"] for l in L.load(os.path.join(ROOT, "library/lessons"))}
    if name == "none":
        return "", set()
    if name == "playbook":
        allowed = {l["id"] for l in _lib("v0", cutoff)}
        txt, ids = playbook(m, cutoff, allowed)
        return txt, ids
    if name == "v0-index":
        ls, fn = list(_lib("v0", cutoff)), L.index_line
    elif name == "fresh-index":
        ls, fn = list(_lib("fresh", cutoff)), L.index_line
    elif name in ("v1-scenario", "graph-scenario"):
        ls, fn = L.by_scenario(_lib(name.split("-")[0], cutoff), m), L.full
    elif name == "profile":
        ls, fn = list(_lib("profile", cutoff)), L.full
    else:
        raise KeyError(name)
    if rep:
        random.Random(f"{m['id']}|{rep}").shuffle(ls)
    txt = L.render(ls, fn)
    ids = {l["id"] for l in ls} & v0ids
    if name == "graph-scenario":
        res = graph_resolutions({l["id"] for l in ls if l.get("level") != "principle"})
        if res:
            txt += "\n\nWhen two of these notes conflict, resolve them like this:\n" + "\n".join(res)
    return txt, ids


def applicable(name, m):
    return not (name == "fresh-index" and m.get("window") != "A")


if __name__ == "__main__":
    from v2common import load
    S = load("set.json")
    for w in ("A", "B"):
        m = next(x for x in S["eval"] if x["window"] == w)
        for n in SCHEMES:
            if applicable(n, m):
                t, ids = memory(n, m)
                print(w, n, "tokens", tokens_estimate(t), "v0 ids", len(ids))
