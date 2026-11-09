"""Action replay (primary metric) + control.

    python3 eval/v2/replay.py --pilot          # 15 foreseeable moments x all schemes x rep 0
    python3 eval/v2/replay.py                  # full: F x 3 reps, not-F sample x 1 rep, control x 1 rep
Rows -> work/eval/v2/replies.jsonl, verdicts -> work/eval/v2/verdicts.jsonl (every call cached + cost-logged).
"""
from __future__ import annotations
import argparse, hashlib, json, os, random, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v2common import V2WORK, call, clip, load, parse_json, render_context, spent, split_context, tokens_estimate, leak_reasons  # noqa: E402
from schemes2 import SCHEMES, memory, applicable  # noqa: E402

AGENT_MODEL, JUDGE_MODEL = "haiku", "sonnet"
N_NF, N_CTRL = 18, 15
# identical text in both arms (REVIEW-v1 §5): the no-memory arm gets an empty notes block
AGENT_SYSTEM = ("You are a coding agent (like Claude Code) working for one particular person on their projects. "
                "Below are notes learned from this person's earlier feedback to their agents; they may or may not be "
                "relevant to the current situation. Do not mention the notes.{rep}\n\n<notes>\n{notes}\n</notes>")
AGENT_PROMPT = """Recent conversation (USER = the person, ASSISTANT = you, TOOL = tool results; most recent last):

{context}

Write your next turn. You cannot run tools here, so describe it: one line per tool call you would make, as `ACTION: <tool>: <what exactly>`, then the message you would send to the person, if any, as `MESSAGE: ...`. At most 150 words, in the language the person writes in."""

JUDGE_SYSTEM = "You compare two candidate replies of a coding agent. Answer only with JSON."
JUDGE_PROMPT = """A person works with a coding agent. Below: the conversation, what the agent actually did next, and what the person then said (they pushed back). Two candidate replies, A and B, were written for the same point in the conversation **without** seeing the person's reaction.

## Conversation (most recent last)
{context}

## What the agent actually did
{action}

## What the person said next
{pushback}

## Candidate A
{a}

## Candidate B
{b}

Which candidate would this person have been less likely to push back on? Judge substance: does the candidate do what the person turned out to want, or does it repeat what they objected to? Do not prefer a candidate for being more cautious, hedging, asking permission or being longer; asking counts only if the person's message shows they wanted to be asked. You must choose exactly one. Set "clear" to false if the difference is marginal.

Answer: {{"choice": "A" or "B", "clear": true or false, "why": "at most 20 words"}}"""

CTRL_PROMPT = """A person works with a coding agent. Below is the conversation so far. Two candidate replies, A and B, were written for the agent's next turn.

## Conversation (most recent last)
{context}

## Candidate A
{a}

## Candidate B
{b}

Which candidate better fulfils the person's request and is what a careful person would want as the agent's next step? Judge substance, not length or politeness. You must choose exactly one. Set "clear" to false if the difference is marginal.

Answer: {{"choice": "A" or "B", "clear": true or false, "why": "at most 20 words"}}"""


def reply(scheme, m, rep):
    notes, ids = memory(scheme, m, rep)
    system = AGENT_SYSTEM.format(rep=f" (Session {rep + 1}.)" if rep else "", notes=notes or "(no notes)")
    pre, _ = split_context(m)
    raw = call(AGENT_PROMPT.format(context=render_context(pre, 5)), system=system, model=AGENT_MODEL,
               tag=f"agent:{scheme}")
    return {"id": m["id"], "scheme": scheme, "rep": rep, "set": m["_set"], "reply": raw.strip()[:2000],
            "mem_tokens": tokens_estimate(notes), "ids": sorted(ids)}


def verdict(m, mem_row, none_row):
    """Pairwise, blind, order randomised per (moment, scheme, rep)."""
    rng = random.Random(f"{m['id']}|{mem_row['scheme']}|{mem_row['rep']}")
    mem_first = rng.random() < 0.5
    a, b = (mem_row, none_row) if mem_first else (none_row, mem_row)
    pre, action = split_context(m)
    if m["_set"] == "control":
        prompt = CTRL_PROMPT.format(context=render_context(pre, 3), a=clip(a["reply"], 900), b=clip(b["reply"], 900))
    else:
        prompt = JUDGE_PROMPT.format(context=render_context(pre, 3), action=render_context(action, 2) or "(not shown)",
                                     pushback=clip(m["pushback"], 900), a=clip(a["reply"], 900), b=clip(b["reply"], 900))
    ans = parse_json(call(prompt, system=JUDGE_SYSTEM, model=JUDGE_MODEL, tag=f"judge:{m['_set']}", effort="low")) or {}
    ch = str(ans.get("choice", "")).strip().upper() if isinstance(ans, dict) else ""
    if ch not in ("A", "B"):
        out = "error"
    else:
        mem_won = (ch == "A") == mem_first
        out = ("win" if mem_won else "loss") if ans.get("clear") not in (False, "false") else "tie"
        out_forced = "win" if mem_won else "loss"
    return {"id": m["id"], "session": m["session"], "set": m["_set"], "foreseeable": m.get("foreseeable"),
            "window": m.get("window"), "scheme": mem_row["scheme"], "rep": mem_row["rep"], "outcome": out,
            "forced": out_forced if out != "error" else "error", "mem_first": mem_first,
            "why": str(ans.get("why", ""))[:200] if isinstance(ans, dict) else "",
            "identical": mem_row["reply"].strip() == none_row["reply"].strip()}


BATCH_EVAL = """A person works with a coding agent. Below: the conversation, what the agent actually did next, and what the person then said (they pushed back). Several candidate replies (R1, R2, ...) were written for the same point in the conversation **without** seeing the person's reaction.

## Conversation (most recent last)
{context}

## What the agent actually did
{action}

## What the person said next
{pushback}

## Candidates
{cands}

For each numbered pair below, decide independently which of its two candidates this person would have been less likely to push back on. Judge substance: does it do what the person turned out to want, or repeat what they objected to? Do not prefer a candidate for being more cautious, hedging, asking permission or being longer; asking counts only if the person's message shows they wanted to be asked. Choose exactly one per pair; set "clear" false if the difference is marginal.

Pairs:
{pairs}

Answer with JSON only: {{"1": {{"pick": "R?", "clear": true or false}}, "2": ...}}"""

BATCH_CTRL = """A person works with a coding agent. Below is the conversation so far and several candidate replies (R1, R2, ...) for the agent's next turn.

## Conversation (most recent last)
{context}

## Candidates
{cands}

For each numbered pair below, decide independently which of its two candidates better fulfils the person's request and is what a careful person would want as the agent's next step. Judge substance, not length or politeness. Choose exactly one per pair; set "clear" false if the difference is marginal.

Pairs:
{pairs}

Answer with JSON only: {{"1": {{"pick": "R?", "clear": true or false}}, "2": ...}}"""


def verdict_batch(m, rep, mem_rows, none_row):
    """One Sonnet call per (moment, rep): all replies under shuffled anonymous labels, one forced choice per
    (memory, none) pair, pair order and within-pair order randomised. The judge is never told which has memory."""
    rng = random.Random(f"{m['id']}|{rep}|batch")
    allr = [none_row] + mem_rows
    order = list(range(len(allr)))
    rng.shuffle(order)
    lab = {order[i]: f"R{i + 1}" for i in range(len(allr))}  # row index -> label
    cands = "\n\n".join(f"### {lab[j]}\n{clip(allr[j]['reply'], 900)}" for j in sorted(lab, key=lambda j: lab[j]))
    pairs, meta = [], []
    idxs = list(range(1, len(allr)))
    rng.shuffle(idxs)
    for k, j in enumerate(idxs, 1):
        mem_first = rng.random() < 0.5
        x, y = (lab[j], lab[0]) if mem_first else (lab[0], lab[j])
        pairs.append(f"{k}. {x} vs {y}")
        meta.append((k, j, mem_first))
    pre, action = split_context(m)
    if m["_set"] == "control":
        prompt = BATCH_CTRL.format(context=render_context(pre, 3), cands=cands, pairs="\n".join(pairs))
    else:
        prompt = BATCH_EVAL.format(context=render_context(pre, 3), action=render_context(action, 2) or "(not shown)",
                                   pushback=clip(m["pushback"], 900), cands=cands, pairs="\n".join(pairs))
    ans = parse_json(call(prompt, system=JUDGE_SYSTEM, model=JUDGE_MODEL, tag=f"judgeb:{m['_set']}", effort="low")) or {}
    out = []
    for k, j, mem_first in meta:
        a = ans.get(str(k)) if isinstance(ans, dict) else None
        pick = str(a.get("pick", "")).strip().upper() if isinstance(a, dict) else ""
        mr = allr[j]
        if pick not in (lab[j], lab[0]):
            o = f = "error"
        else:
            f = "win" if pick == lab[j] else "loss"
            o = f if a.get("clear") not in (False, "false") else "tie"
        out.append({"id": m["id"], "session": m["session"], "set": m["_set"], "foreseeable": m.get("foreseeable"),
                    "window": m.get("window"), "scheme": mr["scheme"], "rep": rep, "outcome": o, "forced": f,
                    "mem_first": mem_first, "identical": mr["reply"].strip() == none_row["reply"].strip()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-control", action="store_true")
    ap.add_argument("--separate", action="store_true", help="pilot only: also run one judge call per pair")
    a = ap.parse_args()
    S = load("set.json")
    F = [m for m in S["eval"] if m["foreseeable"]]
    NF = [m for m in S["eval"] if not m["foreseeable"]]
    NF, ctrl = NF[:N_NF], S["control"][:N_CTRL]  # budget trim (sample order is already random)
    for m in F + NF:
        m["_set"] = "eval"
    for m in ctrl:
        m["_set"] = "control"
    if a.pilot:
        plan = [(m, 0) for m in F[:15]]
    else:
        plan = [(m, r) for m in F for r in range(a.reps)] + [(m, 0) for m in NF]
        if not a.no_control:
            plan += [(m, 0) for m in ctrl]
    for m, _ in plan:
        assert not leak_reasons(m), m["id"]
    jobs = [(s, m, r) for m, r in plan for s in SCHEMES if applicable(s, m)]
    print(f"{len(plan)} (moment, rep) x schemes = {len(jobs)} agent calls; spent ${spent():.2f}", flush=True)
    def safe_reply(j):
        try:
            return reply(*j)
        except RuntimeError as e:
            print("agent skipped:", j[0], j[1]["id"], j[2], str(e)[:80], flush=True)
            return None
    with ThreadPoolExecutor(a.workers) as ex:
        rows = [r for r in ex.map(safe_reply, jobs) if r]
    print(f"agent done, spent ${spent():.2f}", flush=True)
    idx = {(r["id"], r["scheme"], r["rep"]): r for r in rows}
    groups = [(m, r, [idx[(m["id"], s, r)] for s in SCHEMES if s != "none" and applicable(s, m)
                      and (m["id"], s, r) in idx], idx[(m["id"], "none", r)]) for m, r in plan if (m["id"], "none", r) in idx]
    def safe(g):
        try:
            return verdict_batch(*g)
        except RuntimeError as e:  # budget reached or call failed: keep what we have
            print("judge skipped:", g[0]["id"], g[1], str(e)[:80], flush=True)
            return []
    with ThreadPoolExecutor(a.workers) as ex:
        vs = [v for g in ex.map(safe, groups) for v in g]
    if a.pilot and a.separate:  # validation: separate pairwise calls (one per pair) on the pilot
        pairs = [(m, idx[(m["id"], s, r)], idx[(m["id"], "none", r)]) for m, r in plan for s in SCHEMES
                 if s != "none" and applicable(s, m)]
        with ThreadPoolExecutor(a.workers) as ex:
            sep = list(ex.map(lambda p: verdict(*p), pairs))
        with open(os.path.join(V2WORK, "verdicts-pilot-separate.jsonl"), "w") as fh:
            for v in sep:
                fh.write(json.dumps(v, ensure_ascii=False) + "\n")
    tag = "-pilot" if a.pilot else ""
    with open(os.path.join(V2WORK, f"replies{tag}.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(V2WORK, f"verdicts{tag}.jsonl"), "w") as fh:
        for v in vs:
            fh.write(json.dumps(v, ensure_ascii=False) + "\n")
    from collections import Counter
    print(Counter((v["scheme"], v["outcome"]) for v in vs))
    print(f"done, spent ${spent():.2f}")


if __name__ == "__main__":
    main()
