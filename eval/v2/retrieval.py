"""Retrieval: which v0 lesson (if any) would have prevented each foreseeable pushback, and does each scheme's
selection contain it?

    python3 eval/v2/retrieval.py     # -> work/eval/v2/retrieval.json
"""
from __future__ import annotations
import os, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v2common import call, clip, load, save, parse_json, render_context, split_context, tokens_estimate, spent  # noqa: E402
from schemes2 import SCHEMES, memory, applicable, _lib  # noqa: E402
import libraries as L  # noqa: E402
from common import ROOT  # noqa: E402

FULL = L.load(os.path.join(ROOT, "library", "lessons"))
LIB_TXT = "\n".join(f"- [{l['id']}] {clip(l['title'], 160)} — do: {clip(l.get('do', ''), 200)}" for l in FULL)
SYSTEM = "You match incidents to lessons. Answer only with JSON."
PROMPT = """A person works with a coding agent. Below: the conversation, what the agent did, and the person's pushback. Then a library of lessons learned about this person.

## Conversation (most recent last)
{context}

## What the agent did
{action}

## What the person said next
{pushback}

## Lesson library
{lib}

Which lessons (0 to 3), had the agent followed them, would most directly have prevented this pushback? Only list a lesson if following it would clearly have avoided this specific objection; list none if no lesson does.

Answer: {{"ids": ["lesson-id", ...], "why": "one short sentence"}}"""


def truth(m):
    pre, act = split_context(m)
    ans = parse_json(call(PROMPT.format(context=render_context(pre, 4), action=render_context(act, 3) or "(not shown)",
                                        pushback=clip(m["pushback"], 900), lib=LIB_TXT),
                          system=SYSTEM, model="sonnet", tag="retrieval-truth")) or {}
    valid = {l["id"] for l in FULL}
    ids = [i for i in (ans.get("ids") if isinstance(ans, dict) else []) or [] if i in valid]
    return m["id"], ids


def main():
    S = load("set.json")
    F = [m for m in S["eval"] if m["foreseeable"]]
    with ThreadPoolExecutor(8) as ex:
        gt = dict(ex.map(truth, F))
    sel = {}
    for m in F:
        strict = _lib("v0", m["cutoff"])
        bm = L.BM25(strict)
        row = {"strict-library": ({l["id"] for l in strict}, None)}
        for k in (3, 8):
            hits = bm.top(L.query_of(m), k)
            row[f"search-top{k}"] = ({l["id"] for l in hits}, tokens_estimate(L.render(hits)))
        for s in SCHEMES:
            if s not in ("none", "fresh-index") and applicable(s, m):
                t, ids = memory(s, m)
                row[s] = (set(ids), tokens_estimate(t))
        sel[m["id"]] = {k: (sorted(v[0]), v[1]) for k, v in row.items()}
    save("retrieval.json", {"truth": gt, "selections": sel})
    print("moments", len(F), "with a relevant lesson", sum(1 for v in gt.values() if v), f"spent ${spent():.2f}")


if __name__ == "__main__":
    main()
