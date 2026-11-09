"""Predict + judge for every scheme on the held-out test moments and the control set.

    python3 eval/run.py --pilot                 # 20 test moments x {none, v0-all}
    python3 eval/run.py                         # every registered scheme, all moments
    python3 eval/run.py --schemes none,v0-all   # a subset
Results: work/eval/predictions.jsonl (one row per scheme x moment), work/eval/grades.json.
Every model call is cached (work/eval/cache), so a rerun only pays for what is new.
"""
from __future__ import annotations
import argparse, hashlib, json, os, random, sys, threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, call, clip, jload, jsave, parse_json, render_context, spent, tokens_estimate  # noqa: E402
from libraries import schemes  # noqa: E402

PRED_MODEL, JUDGE_MODEL = "haiku", "sonnet"
PRED_SYSTEM = ("You watch a coding agent working for one particular person and predict that person's reactions. "
               "Answer only with the JSON asked for.")
MEMORY_HEAD = ("\n\nNotes learned from this person's earlier corrections of their agents (they may or may not apply "
               "here):\n")
PRED_PROMPT = """Here is the end of a conversation between the person (USER) and their coding agent. The last turns are the agent's most recent actions and messages.

{context}

Question: will the person's next message push back on what the agent just did — correct it, stop it, or say it is not what they wanted? Only say yes if you expect pushback; most agent turns are accepted.{use}

Answer with JSON only:
{{"pushback": true or false, "lessons": [ids of the notes you relied on, if any], "prediction": "if true: what the person will object to and what they want instead, concretely, in one or two sentences; if false: empty"}}"""
USE = " Use the notes where they apply to this situation."

JUDGE_SYSTEM = "You grade predictions against what really happened. Be strict and literal. Answer only with JSON."
JUDGE_PROMPT = """A model watched a person working with a coding agent and predicted the person's next reaction. Below are the last turns before the reaction, what the person actually said, and several independent predictions (labels are arbitrary).

## Last turns before the reaction
{context}

## What the person actually said next ({kind})
{pushback}

## Predictions
{preds}

Grade each prediction independently against the actual reaction:
- "yes": it names the same concern or the same requested change as the person (wording may differ; it must be specific enough that an agent acting on it would have avoided this pushback).
- "partial": right general area (e.g. "the scope is wrong", "should ask first") but misses or contradicts the person's specific point.
- "no": a different concern, too generic to act on ("the person may want changes"), or wrong.

Answer with JSON only: {{"<label>": "yes" | "partial" | "no", ...}} for every label."""

lock = threading.Lock()


def predict(name, fn, m):
    mem = fn(m)
    system = PRED_SYSTEM + (MEMORY_HEAD + mem if mem else "")
    if name.endswith("-rerun"):  # same scheme, cache-busting whitespace: measures run-to-run noise
        system += " "
    prompt = PRED_PROMPT.format(context=render_context(m["context"], 6), use=USE if mem else "")
    raw = call(prompt, system=system, model=PRED_MODEL, tag=f"predict:{name}")
    ans = parse_json(raw)
    ok = isinstance(ans, dict)
    ans = ans if ok else {}
    pb = ans.get("pushback")
    pb = pb if isinstance(pb, bool) else str(pb).lower() == "true"
    return {"scheme": name, "id": m["id"], "set": m.get("_set"), "pushback": pb,
            "lessons": ans.get("lessons") if isinstance(ans.get("lessons"), list) else [],
            "prediction": str(ans.get("prediction") or "")[:800], "parsed": ok,
            "mem_tokens": tokens_estimate(mem)}


def phash(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:12]


def judge(m, preds, grades):
    """Grade the not-yet-graded distinct prediction texts for one moment in one call."""
    g = grades.setdefault(m["id"], {})
    todo = {}
    for p in preds:
        if p["pushback"] and p["prediction"].strip():
            h = phash(p["prediction"])
            if h not in g:
                todo[h] = p["prediction"]
    if not todo:
        return
    items = sorted(todo.items())
    random.Random(m["id"]).shuffle(items)
    labels = {f"P{i + 1}": h for i, (h, _) in enumerate(items)}
    block = "\n".join(f"{lab}: {clip(todo[h], 800)}" for lab, h in labels.items())
    kind = "the reason given after interrupting the agent" if m["kind"] == "interrupt" else m["kind"]
    prompt = JUDGE_PROMPT.format(context=render_context(m["context"], 3), kind=kind,
                                 pushback=clip(m["pushback"], 1500), preds=block)
    ans = parse_json(call(prompt, system=JUDGE_SYSTEM, model=JUDGE_MODEL, tag="judge")) or {}
    with lock:
        for lab, h in labels.items():
            v = str(ans.get(lab, "")).lower().strip() if isinstance(ans, dict) else ""
            g[h] = v if v in ("yes", "partial", "no") else "error"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--schemes", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-judge", action="store_true")
    a = ap.parse_args()
    sp = jload("split.json")
    S = schemes(sp["cutoff"])
    names = [s for s in (a.schemes.split(",") if a.schemes else S) if s]
    test, control = sp["test"], sp["control"]
    if a.pilot:
        names, test, control = ["none", "v0-all"], test[:20], []
    for m in test:
        m["_set"] = "test"
    for m in control:
        m["_set"] = "control"
    jobs = [(n, m) for n in names for m in test + control]
    print(f"{len(names)} schemes x {len(test)} test + {len(control)} control = {len(jobs)} predictions; "
          f"spent so far ${spent():.2f}", flush=True)
    rows = []
    with ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(predict, n, S[n][0], m) for n, m in jobs]
        for i, f in enumerate(futs, 1):
            rows.append(f.result())
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)} predictions, ${spent():.2f}", flush=True)
    # merge into the predictions file (one row per scheme x moment, latest wins)
    path = os.path.join(WORK, "predictions.jsonl")
    old = {}
    if os.path.exists(path):
        for l in open(path):
            r = json.loads(l)
            old[(r["scheme"], r["id"])] = r
    for r in rows:
        old[(r["scheme"], r["id"])] = r
    with open(path, "w") as fh:
        for r in old.values():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    if a.no_judge:
        return
    gpath = os.path.join(WORK, "grades.json")
    grades = json.load(open(gpath)) if os.path.exists(gpath) else {}
    by_m = {}
    for r in old.values():
        if r["set"] == "test":
            by_m.setdefault(r["id"], []).append(r)
    tm = {m["id"]: m for m in test}
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(lambda mid_: judge(tm[mid_], by_m[mid_], grades), [k for k in by_m if k in tm]))
    jsave("grades.json", grades)
    print(f"done; total spent ${spent():.2f}")


if __name__ == "__main__":
    main()
