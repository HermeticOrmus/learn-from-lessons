"""Pass rates per condition from blind verdicts.   python3 -m eval.v3.score"""
import glob, json, os, random
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "work", "eval", "v3")
CONDS = ["none", "matched", "all", "oracle"]

def ci(xs, n=4000, seed=3):
    if not xs: return (float("nan"),) * 2
    r = random.Random(seed); m = len(xs)
    means = sorted(sum(r.choice(xs) for _ in range(m)) / m for _ in range(n))
    return means[int(.025 * n)], means[int(.975 * n)]

def main():
    key = json.load(open(os.path.join(OUT, "answer-key.json")))
    verdicts = {}
    for p in glob.glob(os.path.join(OUT, "judge", "verdicts-*.json")):
        for v in json.load(open(p)):
            verdicts[v["answer_id"]] = v
    matched = json.load(open(os.path.join(OUT, "matched.json")))
    table = {}
    for aid, k in key.items():
        v = verdicts.get(aid)
        if not v or v.get("verdict") not in ("pass", "fail"): continue
        kind = "control" if k["item"].endswith("--control") else "apply"
        table.setdefault((k["condition"], kind), []).append((k["item"], 1.0 if v["verdict"] == "pass" else 0.0))
    lines = ["| condition | apply pass % [95% CI] | n | control pass % [95% CI] (not over-applied) | n |", "|---|---|---|---|---|"]
    for c in CONDS:
        a = [x for _, x in table.get((c, "apply"), [])]; b = [x for _, x in table.get((c, "control"), [])]
        fa = ci(a); fb = ci(b)
        lines.append(f"| {c} | {100*sum(a)/max(1,len(a)):.0f} [{100*fa[0]:.0f}, {100*fa[1]:.0f}] | {len(a)} | "
                     f"{100*sum(b)/max(1,len(b)):.0f} [{100*fb[0]:.0f}, {100*fb[1]:.0f}] | {len(b)} |")
    # matched split by whether retrieval found the target rule
    found = {i for i, m in matched.items() if m["target_found"]}
    for flag, name in ((True, "target retrieved"), (False, "target missed")):
        a = [x for i, x in table.get(("matched", "apply"), []) if (i in found) == flag]
        lines.append(f"| matched, {name} | {100*sum(a)/max(1,len(a)):.0f} | {len(a)} | | |")
    unclear = sum(1 for v in verdicts.values() if v.get("verdict") not in ("pass", "fail"))
    lines.append(f"\nverdicts {len(verdicts)} of {len(key)}; unclear {unclear}")
    text = "\n".join(lines); print(text)
    open(os.path.join(OUT, "RESULTS.md"), "w").write("# Evaluation v3: behaviour tests\n\n" + text + "\n")

if __name__ == "__main__":
    main()
