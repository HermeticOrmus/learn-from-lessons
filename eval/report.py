"""Tables, bootstrap CIs and SVG charts from predictions + grades -> work/eval/RESULTS.md, *.svg.

    python3 eval/report.py
"""
from __future__ import annotations
import hashlib, json, os, random, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, ROOT, jload, redact  # noqa: E402
from libraries import schemes, cited_sessions, EXTERNAL  # noqa: E402

B = 2000


def phash(t):
    return hashlib.sha1(t.strip().encode()).hexdigest()[:12]


def boot(vals, b=B, seed=0):
    if not vals:
        return (float("nan"),) * 3
    rng = random.Random(seed)
    n = len(vals)
    ms = sorted(sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(b))
    return sum(vals) / n, ms[int(0.025 * b)], ms[int(0.975 * b) - 1]


def pct(x):
    return f"{100 * x:.1f}" if x == x else "–"


def ci(t):
    return f"{pct(t[0])} [{pct(t[1])}, {pct(t[2])}]"


def main():
    sp = jload("split.json")
    test = {m["id"]: m for m in sp["test"]}
    ctrl = {m["id"]: m for m in sp["control"]}
    grades = jload("grades.json")
    pred = jload("predictable.json") if os.path.exists(os.path.join(WORK, "predictable.json")) else {}
    rows = [json.loads(l) for l in open(os.path.join(WORK, "predictions.jsonl"))]
    S = schemes(sp["cutoff"])
    P = defaultdict(dict)
    for r in rows:
        P[r["scheme"]][r["id"]] = r

    def grade(r):
        if not r["pushback"] or not r["prediction"].strip():
            return "no"
        g = grades.get(r["id"], {}).get(phash(r["prediction"]), "missing")
        return g if g in ("yes", "partial") else "no"

    order = [s for s in S if s in P and all(i in P[s] for i in test)]
    ids = sorted(test)
    score = {s: {i: {"yes": 1.0, "partial": 0.5}.get(grade(P[s][i]), 0.0) for i in ids} for s in order}
    strict = {s: {i: float(grade(P[s][i]) == "yes") for i in ids} for s in order}
    folders = dict({"v0": os.path.join(ROOT, "library")}, **{k: os.path.join(ROOT, v) for k, v in EXTERNAL.items()})
    texts = {}

    def clean_ids(s):
        fam = S[s][1].get("family")
        if fam not in folders:
            return ids
        if fam not in texts:
            texts[fam] = cited_sessions(folders[fam])
        return [i for i in ids if test[i]["session"] not in texts[fam]]

    table = []
    for s in order:
        meta = S[s][1]
        sc, st = boot(list(score[s].values())), boot(list(strict[s].values()))
        d = boot([score[s][i] - score["none"][i] for i in ids]) if s != "none" else (0, 0, 0)
        fa_vals = [float(P[s][i]["pushback"]) for i in ctrl if i in P[s]]
        fa = boot(fa_vals)
        flag = sum(P[s][i]["pushback"] for i in ids) / len(ids)
        tok = sum(P[s][i]["mem_tokens"] for i in ids) / len(ids)
        cids = clean_ids(s) if meta.get("leak") == "as-built" else []
        cd = boot([score[s][i] - score["none"][i] for i in cids]) if s != "none" and cids else None
        av = [i for i in ids if pred.get(i) == "avoidable"]
        avs = boot([score[s][i] for i in av])
        avd = boot([score[s][i] - score["none"][i] for i in av]) if s != "none" else (0, 0, 0)
        table.append({"scheme": s, "leak": meta.get("leak"), "family": meta.get("family"), "n_lessons": meta.get("n"),
                      "score": sc, "strict": st, "delta": d, "fa": fa, "n_ctrl": len(fa_vals), "flag": flag,
                      "tokens": tok, "clean_n": len(cids), "clean_delta": cd, "av": avs, "av_delta": avd, "n_av": len(av),
                      "J": flag - fa[0],
                      "dist": Counter(grade(P[s][i]) for i in ids)})
    jsave_rows(table)
    write_md(sp, table, len(ids), len(ctrl))
    svg_forest(table)
    svg_scatter(table)
    print(open(os.path.join(WORK, "RESULTS.md")).read()[:6000])


def jsave_rows(table):
    with open(os.path.join(WORK, "results.json"), "w") as fh:
        json.dump(table, fh, indent=1, default=list)


def cost():
    tot, by = 0.0, Counter()
    for l in open(os.path.join(WORK, "calls.jsonl")):
        r = json.loads(l)
        tot += r["cost_usd"]
        by[r["tag"].split(":")[0]] += r["cost_usd"]
    return tot, by


def write_md(sp, table, n, nc):
    tot, by = cost()
    L = ["# Results: which lesson memory lets an agent anticipate the person's pushback?", "",
         f"Held-out by time: cutoff **{sp['cutoff']}** (70th percentile of correction dates). Training = "
         f"{sp['n_train']} moments before it; test = **{n}** later correction / frustration / interrupt-with-reason "
         f"moments ({len({m['session'] for m in sp['test']})} sessions); control = **{nc}** later agent turns the "
         "person simply accepted. Predictor: Claude Haiku (`claude -p --model haiku`); judge: Claude Sonnet. "
         "Design: `eval/DESIGN.md`.", "",
         "**score** = mean over test moments of yes=1, partial=0.5, no=0 (the judge's match between the prediction and "
         "the actual pushback); **yes** = strict share graded yes; **Δ** = paired difference in score vs `none`; "
         "**false alarms** = share of control turns where the predictor expected pushback; **flag** = share of test "
         "moments where it expected pushback at all; **tokens** = mean injected memory per call. 95% bootstrap CIs "
         "over moments. All numbers are percentages.", "",
         "**leak** column: `strict` = only lessons whose every evidence item predates the cutoff (leak-free with "
         "respect to evidence); `first` = lessons whose first evidence predates the cutoff, later evidence dropped "
         "but wording may reflect it; `as-built` = the library's INDEX as built from all data (leaky; see the "
         "session-clean column: Δ on test moments whose session is never cited in that library); `none` = no "
         "post-cutoff data by construction (baseline, fresh library built only from training moments).", "",
         "**avoidable** = score on the test moments a separate Sonnet call labelled *avoidable* (the pushback "
         "was foreseeable from the context or a general preference; `eval/predictable.py`), n = "
         f"{table[0]['n_av']} of {n}; the rest bring new information nobody could predict. **J** = flag rate on test "
         "− false-alarm rate on control: how well the yes/no 'pushback coming?' decision separates the two sets.", "",
         "| scheme | leak | lessons | score | yes | Δ score vs none | avoidable score | Δ avoidable | false alarms | flag | J | tokens |",
         "|---|---|---:|---|---:|---|---|---|---|---:|---:|---:|"]
    for r in sorted(table, key=lambda r: -r["score"][0]):
        L.append(f"| `{r['scheme']}` | {r['leak']} | {r['n_lessons'] or '–'} | {ci(r['score'])} | {pct(r['strict'][0])} | "
                 f"{ci(r['delta']) if r['scheme'] != 'none' else '–'} | {ci(r['av'])} | "
                 f"{ci(r['av_delta']) if r['scheme'] != 'none' else '–'} | {ci(r['fa'])} | {pct(r['flag'])} | "
                 f"{pct(r['J'])} | {r['tokens']:.0f} |")
    L += ["", "**Leakage check for as-built libraries** (built from all data, including the test period): Δ score vs "
          "`none` on test moments whose session id appears nowhere in that library's folder:", ""]
    for r in table:
        if r["leak"] == "as-built":
            L.append(f"- `{r['scheme']}`: {ci(r['clean_delta']) if r['clean_delta'] else '–'} (n = {r['clean_n']})")
    L += ["", FINDINGS.strip()]
    L += ["", "![score vs scheme](scores.svg)", "", "![score vs tokens](tokens.svg)", "",
          f"Grade distribution per scheme (yes/partial/no): " + "; ".join(
              f"`{r['scheme']}` {r['dist'].get('yes', 0)}/{r['dist'].get('partial', 0)}/{r['dist'].get('no', 0)}"
              for r in table), "",
          f"**Cost:** ${tot:.2f} total (" + ", ".join(f"{k} ${v:.2f}" for k, v in by.most_common()) + "), every call "
          "logged in `calls.jsonl` and cached in `cache/`.", ""]
    with open(os.path.join(WORK, "RESULTS.md"), "w") as fh:
        fh.write("\n".join(L))


# Hand-written reading of the table (update when the numbers change).
FINDINGS = """
## Findings

1. **No memory scheme reliably helps the predictor anticipate the person's pushback.** Across the 28 memory
   variants the anticipation score lies between 5.4% and 11.2% (mean 7.7%), against 7.6% with no memory (7.1% on a
   rerun). The top row, `graph-strict-scenario` (11.2%, Δ +3.6 [0.4, 7.1]), **did not replicate**: the same scheme
   rerun with identical inputs scored 6.2%. With 28 comparisons and a run-to-run spread of about ±5 points, no
   ranking among schemes is supported; none of the organisations (hierarchy, cluster, graph, profile, playbook)
   can be declared best on this data. The leaky as-built indexes, which cite 105–109 of the 112 test sessions, do
   no better than the leak-free ones — further evidence against a hidden effect.
2. **Memory roughly triples false alarms.** On control turns the person simply accepted, false alarms rise from
   11.7% (13.3% rerun) without memory to 27–47% (mean 38%) with any lessons, while the flag rate on real pushback
   moments rises only from 27% to about 45%. The yes/no decision "is pushback coming?" separates the two sets
   *worse* with memory (J 15 → mean 7). Lessons make the model more suspicious, not more discerning.
3. **The ceiling is low.** A separate Sonnet pass judged only 26 of 112 test pushbacks *avoidable* from the context
   plus general preferences; 63 bring a new direction or fact no memory could predict. On the avoidable subset
   several schemes score higher (up to 19%, Δ up to +9.6), but with n = 26 the intervals are wide and the top
   scheme's rerun fell back to 9.6%.
4. **Token cost spans 0.5k–13k per call with no score trend**: three search hits (~500 tokens) score like the full
   flat library (~10k) or the fresh dream library (~13k).

**Leak-free numbers** are the rows with leak = `strict` or `none` (blue in the charts): `strict` keeps only lessons
whose every cited turn predates the cutoff (for the playbook, only checks whose cited v0 lessons all do); `fresh-*`
was built by `lessons.dream` from 260 training moments only. `first` and `as-built` rows are leaky, shown for
reference; their session-clean subsets (3–7 moments) are too small to mean anything.

## Limitations

- One person's transcripts (mostly Claude Code, research projects, Chinese/English), 112 test moments from 31
  sessions, 60 control turns. Moments within a session are correlated; the bootstrap resamples moments, so CIs are
  optimistic.
- Predictor (Haiku) and judge (Sonnet) are LLMs; the judge grades all schemes' predictions for a moment side by
  side (anonymised, shuffled) and was spot-checked only on the 20-moment pilot. Predictor run-to-run noise is as
  large as every scheme difference (see the `-rerun` rows).
- The task is a proxy: naming the person's objection in advance is harder than avoiding the mistake. An agent that
  acts with lessons might avoid pushback it could not name. A behavioural replay (redo the turn with/without
  lessons, judge the new action) is the natural next test.
- `strict` filtering removes most lessons (34 of 74 v0 lessons survive), so leak-free libraries are smaller than
  what an agent would load; wording in `first`/`as-built` rows may reflect post-cutoff incidents.
- Control turns are approvals ("ok", "继续", "可以 …"); neutral new requests would be a harder control.
"""

INK, MUTED, ACC, HAIR = "#1f2328", "#6e7781", "#0b6bcb", "#d0d7de"


def svg_forest(table):
    rows = sorted(table, key=lambda r: -r["score"][0])
    W, top, rh, left, plotw = 760, 40, 24, 210, 380
    H = top + rh * len(rows) + 40
    hi = max(r["score"][2] for r in rows) * 1.1 or 0.1
    x = lambda v: left + plotw * v / hi
    base = next(r["score"][0] for r in rows if r["scheme"] == "none")
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica,Arial,sans-serif" font-size="12">',
         f'<rect width="{W}" height="{H}" fill="#fff"/>',
         f'<text x="{left}" y="20" fill="{INK}" font-size="13">anticipation score (yes=1, partial=½), 95% CI</text>',
         f'<text x="{left + plotw + 20}" y="20" fill="{MUTED}">tokens · false alarms</text>',
         f'<line x1="{x(base)}" y1="{top - 8}" x2="{x(base)}" y2="{H - 30}" stroke="{MUTED}" stroke-dasharray="3,3"/>']
    for k, r in enumerate(rows):
        y = top + k * rh + 8
        col = ACC if r["leak"] in ("strict", "none") else MUTED
        o.append(f'<text x="{left - 10}" y="{y + 4}" text-anchor="end" fill="{INK}">{r["scheme"]}</text>')
        o.append(f'<line x1="{x(r["score"][1])}" y1="{y}" x2="{x(r["score"][2])}" y2="{y}" stroke="{HAIR}" stroke-width="2"/>')
        o.append(f'<circle cx="{x(r["score"][0])}" cy="{y}" r="4" fill="{col}"/>')
        o.append(f'<text x="{left + plotw + 20}" y="{y + 4}" fill="{MUTED}">{r["tokens"]:.0f} · {pct(r["fa"][0])}%</text>')
    for v in [i / 20 for i in range(0, int(hi * 20) + 1)]:
        o.append(f'<text x="{x(v)}" y="{H - 14}" text-anchor="middle" fill="{MUTED}">{v * 100:.0f}</text>')
    o.append(f'<text x="{W - 10}" y="{H - 2}" text-anchor="end" fill="{MUTED}" font-size="10">blue = leak-free (strict/none), grey = leaky (first/as-built); dashed = no memory</text>')
    o.append("</svg>")
    open(os.path.join(WORK, "scores.svg"), "w").write("\n".join(o))


def svg_scatter(table):
    import math
    W, H, l, b, t, rgt = 640, 380, 60, 50, 30, 30
    xs = [math.log10(max(r["tokens"], 1)) for r in table]
    hi = max(r["score"][0] for r in table) * 1.15 or 0.1
    X = lambda v: l + (W - l - rgt) * v / max(xs + [1])
    Y = lambda v: H - b - (H - b - t) * v / hi
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica,Arial,sans-serif" font-size="11">',
         f'<rect width="{W}" height="{H}" fill="#fff"/>',
         f'<line x1="{l}" y1="{H - b}" x2="{W - rgt}" y2="{H - b}" stroke="{HAIR}"/>',
         f'<text x="{W / 2}" y="{H - 12}" text-anchor="middle" fill="{MUTED}">injected memory tokens per call (log10; 0 = none)</text>',
         f'<text x="14" y="{t - 10}" fill="{MUTED}">score %</text>']
    for e in range(0, int(max(xs + [1])) + 1):
        o.append(f'<text x="{X(e)}" y="{H - b + 16}" text-anchor="middle" fill="{MUTED}">{10 ** e}</text>')
    for v in [i / 20 for i in range(0, int(hi * 20) + 1)]:
        o.append(f'<text x="{l - 8}" y="{Y(v) + 4}" text-anchor="end" fill="{MUTED}">{v * 100:.0f}</text>')
    for r, xv in zip(table, xs):
        col = ACC if r["leak"] in ("strict", "none") else MUTED
        o.append(f'<circle cx="{X(xv)}" cy="{Y(r["score"][0])}" r="4" fill="{col}"/>')
        o.append(f'<text x="{X(xv) + 6}" y="{Y(r["score"][0]) - 5}" fill="{INK}">{r["scheme"]}</text>')
    o.append("</svg>")
    open(os.path.join(WORK, "tokens.svg"), "w").write("\n".join(o))


if __name__ == "__main__":
    main()
