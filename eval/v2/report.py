"""Tables, session-clustered bootstrap (resampling sessions and, per moment, one of its reps), charts.

    python3 eval/v2/report.py     # -> work/eval/v2/results.json, primary.svg, retrieval.svg (RESULTS.md is hand-written from these)
"""
from __future__ import annotations
import json, os, random, statistics, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v2common import V2WORK, load, save, spent  # noqa: E402
from schemes2 import SCHEMES, LEAK  # noqa: E402

B = 2000


def rows(name):
    p = os.path.join(V2WORK, name)
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def rates(outs, key):
    n = len(outs)
    if not n:
        return None
    if key == "forced":
        return sum(o == "win" for o in outs) / n
    return {k: sum(o == k for o in outs) / n for k in ("win", "tie", "loss")}


def stat(outs_by_m, sess, f, rng):
    """outs_by_m: moment -> [outcomes per rep]; f(list of outcomes) -> float. Session-clustered, rep-resampled."""
    by_s = defaultdict(list)
    for mid_ in outs_by_m:
        by_s[sess[mid_]].append(mid_)
    S = sorted(by_s)
    point = f([o for mid_ in outs_by_m for o in outs_by_m[mid_]])
    boots = []
    for _ in range(B):
        pick = []
        for s in (rng.choice(S) for _ in S):
            for mid_ in by_s[s]:  # two-stage: resample this moment's reps with replacement (k draws)
                o = outs_by_m[mid_]
                pick.extend(rng.choice(o) for _ in o)
        boots.append(f(pick))
    return point, boots  # unsorted: replicate i of F can be paired with replicate i of NF


def ci(boots):
    b = sorted(boots)
    return b[int(0.025 * len(b))], b[int(0.975 * len(b)) - 1]


def win(o): return sum(x == "win" for x in o) / len(o) if o else float("nan")
def loss(o): return sum(x == "loss" for x in o) / len(o) if o else float("nan")
def tie(o): return sum(x == "tie" for x in o) / len(o) if o else float("nan")
def net(o): return win(o) - loss(o)


def main():
    S = load("set.json")
    p_f = S["p_foreseeable"]
    V = [v for v in rows("verdicts.jsonl") if v["outcome"] != "error"]
    errors = sum(1 for v in rows("verdicts.jsonl") if v["outcome"] == "error")
    R = rows("replies.jsonl")
    sess = {v["id"]: v["session"] for v in V}
    out = {"n": {}, "primary": {}, "control": {}, "per_rep": {}, "tokens": {}, "errors": errors}
    tok = defaultdict(list)
    for r in R:
        if r["set"] == "eval":
            tok[r["scheme"]].append(r["mem_tokens"])
    for s in SCHEMES[1:]:
        res = {}
        for subset in ("F", "NF"):
            d = defaultdict(list)
            dforced = defaultdict(list)
            for v in V:
                if v["set"] == "eval" and v["scheme"] == s and (v["foreseeable"] == (subset == "F")):
                    d[v["id"]].append(v["outcome"])
                    dforced[v["id"]].append(v["forced"])
            res[subset] = (d, dforced)
        row = {"leak": LEAK[s], "tokens": round(statistics.mean(tok[s])) if tok[s] else 0}
        for subset in ("F", "NF"):
            d, dfo = res[subset]
            row[f"n_{subset}"] = len(d)
            for nm, f, src in (("win", win, d), ("tie", tie, d), ("loss", loss, d), ("net", net, d),
                               ("forced_win", win, dfo)):
                pt, bt = stat(src, sess, f, random.Random(0 if subset == "F" else 1)) if src else (float("nan"), [float("nan")] * B)
                row[f"{subset}_{nm}"] = (pt, *ci(bt))
                row[f"_{subset}_{nm}_boots"] = bt
        # all = post-stratified
        for nm in ("win", "tie", "loss", "net", "forced_win"):
            fb, nb = row.pop(f"_F_{nm}_boots"), row.pop(f"_NF_{nm}_boots")
            if row["n_NF"]:
                bt = [p_f * a + (1 - p_f) * b for a, b in zip(fb, nb)]  # independent replicates
                pt = p_f * row[f"F_{nm}"][0] + (1 - p_f) * row[f"NF_{nm}"][0]
                row[f"all_{nm}"] = (pt, *ci(bt))
        # per-rep variance on F (forced net and clear net)
        reps = defaultdict(list)
        for v in V:
            if v["set"] == "eval" and v["scheme"] == s and v["foreseeable"]:
                reps[v["rep"]].append(v)
        pr = {r: {"net": net([x["outcome"] for x in vs]), "forced_win": win([x["forced"] for x in vs]), "n": len(vs)}
              for r, vs in sorted(reps.items())}
        out["per_rep"][s] = pr
        if len(pr) > 1:
            row["F_net_rep_mean_sd"] = (statistics.mean(x["net"] for x in pr.values()),
                                        statistics.stdev(x["net"] for x in pr.values()))
            row["F_forced_rep_mean_sd"] = (statistics.mean(x["forced_win"] for x in pr.values()),
                                           statistics.stdev(x["forced_win"] for x in pr.values()))
        # control
        dc = defaultdict(list)
        dcf = defaultdict(list)
        for v in V:
            if v["set"] == "control" and v["scheme"] == s:
                dc[v["id"]].append(v["outcome"])
                dcf[v["id"]].append(v["forced"])
        if dc:
            for nm, f, src in (("win", win, dc), ("tie", tie, dc), ("loss", loss, dc), ("net", net, dc),
                               ("forced_win", win, dcf)):
                pt, bt = stat(src, sess, f, random.Random(0))
                row[f"ctrl_{nm}"] = (pt, *ci(bt))
            row["n_ctrl"] = len(dc)
        row["identical_share"] = sum(v["identical"] for v in V if v["scheme"] == s) / max(1, sum(1 for v in V if v["scheme"] == s))
        out["primary"][s] = row
    # pooled memory vs none on F (all schemes pooled, moment-level)
    pooled = defaultdict(list)
    for v in V:
        if v["set"] == "eval" and v["foreseeable"]:
            pooled[v["id"]].append(v["forced"])
    pt, bt = stat(pooled, sess, win, random.Random(0))
    out["pooled_F_forced_win"] = (pt, *ci(bt))
    # retrieval
    if os.path.exists(os.path.join(V2WORK, "retrieval.json")):
        RT = load("retrieval.json")
        gt, sel = RT["truth"], RT["selections"]
        has = [m for m in gt if gt[m]]
        rt = {"n": len(gt), "with_relevant": len(has)}
        names = sorted({k for v in sel.values() for k in v})
        for k in names:
            hit, rec, prec, ks, toks = [], [], [], [], []
            for m in has:
                if k not in sel[m]:
                    continue
                ids, t = sel[m][k]
                g = set(gt[m])
                inter = g & set(ids)
                hit.append(bool(inter))
                rec.append(len(inter) / len(g))
                ks.append(len(ids))
                if ids:
                    prec.append(len(inter) / len(ids))
                if t is not None:
                    toks.append(t)
            rt[k] = {"n": len(hit), "hit": statistics.mean(hit) if hit else None, "recall": statistics.mean(rec) if rec else None,
                     "precision": statistics.mean(prec) if prec else 0.0, "k": statistics.mean(ks) if ks else 0,
                     "tokens": round(statistics.mean(toks)) if toks else None}
        # does having the relevant lesson in memory make the replay win more? (pooled over schemes, forced)
        cond = {True: [], False: []}
        for v in V:
            if v["set"] == "eval" and v["foreseeable"] and v["id"] in gt and gt[v["id"]] and v["scheme"] in sel[v["id"]]:
                inside = bool(set(gt[v["id"]]) & set(sel[v["id"]][v["scheme"]][0]))
                cond[inside].append(v["forced"])
        rt["win_if_relevant_injected"] = (win(cond[True]), len(cond[True]))
        rt["win_if_not_injected"] = (win(cond[False]), len(cond[False]))
        out["retrieval"] = rt
    out["n"] = {"F": sum(1 for m in S["eval"] if m["foreseeable"]), "NF_sample": len({v["id"] for v in V if v["set"] == "eval" and not v["foreseeable"]}),
                "F_judged": len({v["id"] for v in V if v["set"] == "eval" and v["foreseeable"]}),
                "control": len({v["id"] for v in V if v["set"] == "control"}),
                "judgements": len(V), "p_f": p_f, "labelled": S["n_labelled"], "usable": S["n_usable"],
                "excluded": len(S["excluded"]), "sessions_F": len({m["session"] for m in S["eval"] if m["foreseeable"]})}
    out["spent"] = spent()
    save("results.json", out)
    charts(out)
    print(json.dumps({k: v for k, v in out.items() if k != "per_rep"}, default=str, indent=1)[:6000])


def charts(out):
    P = out["primary"]
    names = list(P)
    W, H, L0, R0 = 640, 70 + 34 * len(names), 150, 30
    x = lambda v: L0 + v * (W - L0 - R0)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica,Arial,sans-serif" font-size="12">',
         '<rect width="100%" height="100%" fill="#fff"/>',
         f'<text x="{L0}" y="18" fill="#222">Memory reply preferred over no-memory (forced choice, judge sees the pushback)</text>',
         f'<text x="{L0}" y="34" fill="#666" font-size="11">● foreseeable (3 reps)   ○ all (post-stratified)   95% CI, session bootstrap</text>']
    for v in (0, .25, .5, .75, 1):
        s.append(f'<line x1="{x(v)}" y1="44" x2="{x(v)}" y2="{H - 20}" stroke="{"#999" if v == .5 else "#eee"}" stroke-width="1"/>')
        s.append(f'<text x="{x(v)}" y="{H - 6}" text-anchor="middle" fill="#666" font-size="10">{int(v * 100)}%</text>')
    for i, n in enumerate(names):
        y = 60 + 34 * i
        s.append(f'<text x="{L0 - 10}" y="{y + 4}" text-anchor="end" fill="#222">{n}</text>')
        for key, dy, fill in (("F_forced_win", -5, "#222"), ("all_forced_win", 7, "#fff")):
            if key in P[n] and P[n][key][0] == P[n][key][0]:
                pt, lo, hi = P[n][key]
                s.append(f'<line x1="{x(lo)}" y1="{y + dy}" x2="{x(hi)}" y2="{y + dy}" stroke="#444" stroke-width="1"/>')
                s.append(f'<circle cx="{x(pt)}" cy="{y + dy}" r="3.5" fill="{fill}" stroke="#222"/>')
    s.append("</svg>")
    open(os.path.join(V2WORK, "primary.svg"), "w").write("\n".join(s))
    rt = out.get("retrieval")
    if not rt:
        return
    items = [(k, v) for k, v in rt.items() if isinstance(v, dict) and v.get("tokens")]
    W, H, L0, B0 = 640, 360, 60, 300
    maxt = max(v["tokens"] for _, v in items) * 1.1
    X = lambda t: L0 + t / maxt * (W - L0 - 40)
    Y = lambda h: B0 - h * (B0 - 50)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica,Arial,sans-serif" font-size="12">',
         '<rect width="100%" height="100%" fill="#fff"/>',
         f'<text x="{L0}" y="22" fill="#222">Retrieval: relevant lesson injected (hit rate) vs injected tokens, foreseeable moments</text>']
    for h in (0, .25, .5, .75, 1):
        s.append(f'<line x1="{L0}" y1="{Y(h)}" x2="{W - 40}" y2="{Y(h)}" stroke="#eee"/>')
        s.append(f'<text x="{L0 - 6}" y="{Y(h) + 4}" text-anchor="end" fill="#666" font-size="10">{int(h * 100)}%</text>')
    s.append(f'<text x="{(W + L0) / 2}" y="{H - 20}" text-anchor="middle" fill="#666" font-size="11">mean injected tokens</text>')
    for k, v in items:
        s.append(f'<circle cx="{X(v["tokens"])}" cy="{Y(v["hit"])}" r="4" fill="#222"/>')
        s.append(f'<text x="{X(v["tokens"]) + 7}" y="{Y(v["hit"]) + 4}" fill="#222" font-size="11">{k}</text>')
    s.append("</svg>")
    open(os.path.join(V2WORK, "retrieval.svg"), "w").write("\n".join(s))


if __name__ == "__main__":
    main()
