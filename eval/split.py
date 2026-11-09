"""Build the time split: cutoff, test moments, control moments, training sample for the fresh dream library.

    python3 eval/split.py            # writes work/eval/split.json, train_sample.jsonl
"""
from __future__ import annotations
import json, os, random, re, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, load_moments, mid, jsave, ROOT  # noqa: E402
from lessons.dream import _is_scripted  # noqa: E402
from lessons.signals import CORRECTION, FRUSTRATION, INTERRUPT  # noqa: E402
from lessons.sources import claude_code  # noqa: E402

SEED = 0
N_TEST, N_CONTROL, N_TRAIN, PER_SESSION = 130, 60, 260, 10
# a plain approval / go-ahead, optionally followed by a short next step ("好的 继续下一步", "ok now run it")
APPROVAL = re.compile(r"^\s*(ok(ay)?\b|continue\b|go (on|ahead)\b|thanks?\b|thank you|thx\b|nice\b|great\b|good\b|"
                      r"perfect\b|yes\b|yep\b|sure\b|lgtm\b|cool\b|好的?|好滴|继续|可以|行|嗯|谢谢|没问题|对的?|很好|不错)", re.I)
NEG = re.compile(r"(不|别|没|但|but|not|n't|why|为什么|\?|？|wrong|错)", re.I)
BARE = re.compile(r"^\s*(continue|继续|go on|ok|好)\s*[.!。]*\s*$", re.I)


def ts(m):
    return (m.get("trigger") or {}).get("ts") or ""


def pushback(m) -> str:
    if m["kind"] == "interrupt":
        fu = [t for t in m.get("followup") or [] if t.get("role") == "user"]
        return fu[0]["text"] if fu else ""
    return m["trigger"]["text"]


def main():
    ms = [m for m in load_moments() if ts(m)[:2] == "20"]
    corr = sorted(ts(m)[:10] for m in ms if m["kind"] == "correction")
    cutoff = corr[int(0.7 * len(corr))]
    train = [m for m in ms if ts(m)[:10] < cutoff]
    later = [m for m in ms if ts(m)[:10] >= cutoff and m["kind"] in ("correction", "frustration", "interrupt")]
    cand, seen_text = [], set()
    for m in sorted(later, key=ts):
        pb = pushback(m).strip()
        vis = re.sub(r"\s+", "", pb)
        if len(vis) < 8 or BARE.match(pb) or INTERRUPT.match(pb) or not m.get("context"):
            continue
        k = vis[:200]
        if k in seen_text:
            continue
        seen_text.add(k)
        cand.append(m)
    rng = random.Random(SEED)
    rng.shuffle(cand)
    per, test = Counter(), []
    for m in cand:
        if per[m["session"]] >= PER_SESSION:
            continue
        per[m["session"]] += 1
        test.append(m)
    test = test[:N_TEST]
    test_ids = {mid(m) for m in test}

    # control: plain approvals after an assistant turn, in post-cutoff sessions
    ctrl = []
    for s in claude_code.sessions():
        if _is_scripted(s) or not s.turns or (s.turns[-1].ts or "")[:10] < cutoff:
            continue
        for i, t in enumerate(s.turns):
            if t.role != "user" or (t.ts or "")[:10] < cutoff or i < 3:
                continue
            txt = t.text.strip()
            if len(txt) > 60 or not APPROVAL.match(txt) or NEG.search(txt) or CORRECTION.match(txt) or FRUSTRATION.search(txt):
                continue
            prev = s.turns[i - 1]
            if prev.role != "assistant":
                continue
            ctx = [{"role": x.role, "tool": x.tool, "error": x.error, "ts": x.ts, "ref": x.ref, "text": x.text}
                   for x in s.turns[max(0, i - 6):i]]
            ctrl.append({"kind": "control", "source": "claude-code", "project": s.cwd, "session": s.id,
                         "trigger": {"role": "user", "ts": t.ts, "ref": t.ref, "text": txt}, "context": ctx,
                         "followup": []})
    rng.shuffle(ctrl)
    per, control = Counter(), []
    for m in ctrl:
        if per[m["session"]] >= 5:
            continue
        per[m["session"]] += 1
        control.append(m)
    control = control[:N_CONTROL]

    tr = train[:]
    rng.shuffle(tr)
    train_sample = [m for m in tr if m["kind"] != "repeated-error"][:N_TRAIN]
    with open(os.path.join(WORK, "train_sample.jsonl"), "w") as fh:
        for m in train_sample:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")

    for m in test:
        m["id"], m["pushback"] = mid(m), pushback(m)
    for m in control:
        m["id"] = mid(m)
    jsave("split.json", {"cutoff": cutoff, "seed": SEED, "n_moments": len(ms), "n_train": len(train),
                         "n_test_candidates": len(cand), "test": test, "control": control,
                         "n_control_candidates": len(ctrl)})
    print(json.dumps({"cutoff": cutoff, "train": len(train), "test_candidates": len(cand), "test": len(test),
                      "test_kinds": Counter(m["kind"] for m in test), "test_sessions": len({m['session'] for m in test}),
                      "control_candidates": len(ctrl), "control": len(control), "train_sample": len(train_sample)},
                     default=dict))


if __name__ == "__main__":
    main()
