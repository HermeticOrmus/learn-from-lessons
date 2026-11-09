"""Build the v2 moment set: window A (v1 test + cap-dropped), window B (earlier cutoff), foreseeable labels, control.

    python3 eval/v2/build_set.py      # -> work/eval/v2/set.json
"""
from __future__ import annotations
import random, re, sys, os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v2common import C1, C2, save, mid, split_context, leak_reasons  # noqa: E402
import common  # noqa: E402
from split import pushback, ts, BARE  # noqa: E402  (eval/ is on sys.path via v2common)
from predictable import label  # noqa: E402
from lessons.signals import INTERRUPT  # noqa: E402

APPROVAL_WORDS = (r"(okay|ok|continue|goon|goahead|thanks|thank you|thankyou|thx|nice|great|good|perfect|yes|yep|sure|lgtm|"
                  r"cool|好的|好滴|好|继续|可以|行|嗯|谢谢|没问题|对的|对|很好|不错|吧|啊|呀|了|的)")
CAP, N_NF, N_CTRL, SEED = 16, 30, 30, 0


def candidates(ms, lo, hi):
    later = [m for m in ms if lo <= ts(m)[:10] < hi and m["kind"] in ("correction", "frustration", "interrupt")]
    out, seen = [], set()
    for m in sorted(later, key=ts):
        pb = pushback(m).strip()
        vis = re.sub(r"\s+", "", pb)
        if len(vis) < 8 or BARE.match(pb) or INTERRUPT.match(pb) or not m.get("context"):
            continue
        if vis[:200] in seen:
            continue
        seen.add(vis[:200])
        out.append(m)
    rng = random.Random(SEED)
    rng.shuffle(out)
    per, keep = Counter(), []
    for m in out:
        if per[m["session"]] < CAP:
            per[m["session"]] += 1
            keep.append(m)
    return keep


def main():
    v1 = common.jload("split.json")
    ms = [m for m in common.load_moments() if ts(m)[:2] == "20"]
    v1ids = {m["id"] for m in v1["test"]}
    A = list(v1["test"]) + [m for m in candidates(ms, C1, "9999") if mid(m) not in v1ids]
    B = candidates(ms, C2, C1)
    for m, w, c in [(m, "A", C1) for m in A] + [(m, "B", C2) for m in B]:
        m["id"], m["pushback"], m["window"], m["cutoff"] = mid(m), pushback(m), w, c
    allm = A + B
    with ThreadPoolExecutor(8) as ex:
        labels = dict(ex.map(label, allm))
    for m in allm:
        m["label"] = labels[m["id"]]
        m["foreseeable"] = labels[m["id"]] == "avoidable"
        pre, act = split_context(m)
        m["has_prefix"] = bool(pre)
        m["leak"] = leak_reasons(m)
    excluded = {m["id"]: m["leak"] or ["no-prefix"] for m in allm if m["leak"] or not m["has_prefix"]}
    usable = [m for m in allm if m["id"] not in excluded]
    assert not any(leak_reasons(m) for m in usable)
    F = [m for m in usable if m["foreseeable"]]
    NF = [m for m in usable if not m["foreseeable"]]
    rng = random.Random(SEED)
    nf = rng.sample(NF, min(N_NF, len(NF)))
    clean = [m for m in v1["control"] if not leak_reasons(m) and split_context(m)[0]
             and len(re.sub(APPROVAL_WORDS, "", re.sub(r"[\s\W_]+", "", m["trigger"]["text"].lower()))) <= 2]
    ctrl = random.Random(SEED).sample(clean, min(N_CTRL, len(clean)))
    for m in ctrl:
        m["window"], m["cutoff"] = "A", C1
    p_f = len(F) / max(1, len(usable))
    save("set.json", {"c1": C1, "c2": C2, "p_foreseeable": p_f, "n_labelled": len(allm), "n_usable": len(usable),
                      "label_counts": Counter(m["label"] for m in allm), "eval": F + nf, "control": ctrl,
                      "labels": {m["id"]: m["label"] for m in allm}, "excluded": excluded,
                      "n_clean_control": len(clean)})
    print({"A": len(A), "B": len(B), "labels": Counter((m["window"], m["label"]) for m in allm),
           "usable": len(usable), "excluded": len(excluded), "clean_control": len(clean), "F": len(F), "F_by_window": Counter(m["window"] for m in F),
           "F_sessions": len({m["session"] for m in F}), "NF_sample": len(nf), "control": len(ctrl),
           "p_f": round(p_f, 3)})


if __name__ == "__main__":
    main()
