# Evaluation: which lesson memory lets an agent anticipate corrections?

Method: `DESIGN.md`. Results land in `work/eval/` (private; never committed: the moments and the quotes in them
are the person's transcripts).

## Reproduce

Needs the local `claude` CLI (logged in) and the exported moments in `work/shards2/*.jsonl`.

```bash
python3 eval/split.py                     # cutoff, test/control sets, training sample -> work/eval/split.json
# leak-free library built only from training moments (sonnet, ~$1-3):
python3 -m lessons.dream --from work/eval/train_sample.jsonl --max-batches 0 --engine claude \
        --model sonnet --out work/eval/lib-fresh --budget-usd 4
python3 eval/run.py --pilot               # 20 moments x {none, v0-all}: check the judge by hand
python3 eval/run.py                       # every scheme in libraries.schemes(): predict (haiku) + judge (sonnet)
python3 eval/predictable.py               # avoidable / new-info label per test moment (sonnet)
python3 eval/report.py                    # RESULTS.md, results.json, scores.svg, tokens.svg
```

- Schemes are registered in `libraries.py:schemes()`. `library-<name>/` folders (v2 hierarchy, cluster, graph,
  profile, playbook) join automatically once their `DONE` file exists; rerun `run.py` and only the new
  scheme's calls are paid for.
- Every model call is cached by prompt hash in `work/eval/cache/` and logged with its cost in
  `work/eval/calls.jsonl`; `EVAL_BUDGET_USD` (default 25) stops new calls once spent.
- Judge grades are cached per (moment, prediction text), so adding a scheme grades only its new predictions.
- Sampling is seeded (`split.py: SEED`, bootstrap seed 0). Model outputs are not deterministic: rerunning with
  an empty cache will move numbers by a few points.

## Files
| file | what |
|---|---|
| `common.py` | moments, context rendering (redacted), cached `claude -p` call with cost log |
| `split.py` | time split, test/control/training sample |
| `libraries.py` | library loading, date filters (`first`, `strict`), BM25 search, scenario inference, schemes |
| `run.py` | predictor and judge prompts; runs and merges predictions + grades |
| `predictable.py` | ceiling check: could the pushback be anticipated at all? |
| `report.py` | bootstrap CIs, session-clean (leak) check, RESULTS.md and SVG charts; the hand-written findings text is the `FINDINGS` constant — update it if numbers change |
