# Evaluation design: do lessons let an agent anticipate the person's pushback?

In the spirit of Anthropic's *components of agent memory* (memory designs compared on the same tasks, gain vs.
token cost: wiki vs search vs both, rules vs freedom), we compare **memory schemes** — which lessons are
injected, and how — on one question: *given the conversation up to the agent's last action, can a model with
these lessons predict that the person is about to push back, and on what?* A lesson is a prediction
("doing X here draws a correction"), so we score it as one.

## 1. Held-out split by time

- Moments: `work/shards2/*.jsonl` (1,052, already fork-deduplicated by `lessons.signals`).
- **Cutoff** = 70th percentile of correction-moment dates (computed, ≈ 2026-09-28). Training = every moment
  before the cutoff. Test = moments on/after it.
- **Test set**: `correction` and `frustration` moments (the trigger text *is* the pushback) and `interrupt`
  moments whose next user turn says why (the follow-up is the pushback). Filters: pushback text ≥ 8 visible
  chars and not a bare "continue"; duplicate pushback texts dropped (residual fork copies); at most 6 per session
  so one long session can't dominate; sampled with a fixed seed to ≈ 130.
- **Control set** (false alarms): from the same post-cutoff sessions, user turns that are plain
  approvals/continuations ("ok", "continue", "thanks", "好的", "继续", "可以" …, ≤ 40 chars, not matched by any
  signal) that directly follow an assistant turn. Same context window (6 turns before). ≈ 60.
- **Libraries use only training data.**
  - `v0` (`library/lessons`, 74 lessons): keep a lesson only if its first evidence date < cutoff. Only its
    `title`, `trigger` and **Do** line are injected — never the **Why** or evidence quotes, which may describe
    post-cutoff incidents (the lesson text itself may still have been *worded* with later evidence in view: a
    known residual leak, see limitations).
  - `v1` (`library-v2/`, hierarchical; used if `library-v2/DONE` exists): same date filter on rules/instances;
    a principle is kept if at least one kept child supports it.
  - `fresh`: built here by `lessons.dream` from a fixed random sample of *training* moments only — the
    leak-free end-to-end pipeline.

## 2. Task

**Predictor** (`claude -p --model haiku`, lean flags as in `dream.py`, no tools): sees the redacted context
turns before the pushback (never the pushback or follow-up) plus the injected memory, and answers JSON:
`{"pushback": true|false, "lessons": [ids], "prediction": "what the person will object to / want instead"}`.
Baseline `none` gets the same instructions without memory.

**Judge** (`claude -p --model sonnet`, separate call per moment): sees the last turns of context, the person's
actual pushback, and every scheme's prediction for that moment under shuffled anonymous labels, and grades each
independently `yes` (same concern / same requested change), `partial` (right area, wrong specifics), `no`.
Predictions with `pushback:false` are scored `no` without judging.

**Metrics**
- *anticipated* = share of test moments graded `yes` (strict); *anticipated+partial* reported too
  (partial = 0.5). 95% CI by bootstrap over moments (2,000 resamples, seed 0). Paired differences vs `none` with
  paired bootstrap CI.
- *memory tokens* = mean estimated tokens of injected memory per call (`dream.tokens_estimate`).
- *false-alarm rate* = share of control moments where the predictor says `pushback:true`.
- Cost per call is logged to `work/eval/calls.jsonl`; every call is cached by prompt hash so reruns are free and
  identical.

## 3. Schemes

| scheme | memory injected |
|---|---|
| `none` | nothing |
| `v0-all` | every v0 lesson: id, title, trigger, Do (flat "wiki") |
| `v0-index` | v0 titles + triggers only (the small injected index) |
| `v0-search3`, `v0-search8` | BM25 top-k v0 lessons, query = last context turns (search) |
| `v0-index+search3` | index + full text of the top-3 (wiki + search) |
| `fresh-all` | every lesson of the fresh training-only dream library |
| `v1-principle` | v1 principles only |
| `v1-rule` | principles + rules |
| `v1-instance` | principles + rules + instances (abstraction sweep, all levels) |
| `v1-scenario` | principles + rules/instances whose `scenarios` match the moment's scenario (inferred by haiku from context, cached) |

Added after the coordinator's scope change: every `library-<scheme>/` with a `DONE` file (v2 hierarchy, cluster,
graph, profile, playbook) enters as `<scheme>-index` (its INDEX.md as built: leaky), `<scheme>-strict` (lesson
files whose every evidence date < cutoff) and `<scheme>-strict-scenario`; the playbook as
`playbook-scenario` / `playbook-strict-scenario` (playbook-any + the inferred scenario's playbook; strict keeps
only checks whose cited v0 lessons are all pre-cutoff). For as-built rows, the report also gives Δ on test moments
whose session is cited nowhere in that library. `fresh-index` / `fresh-scenario` variants of the fresh library.

Noise and ceiling checks: `none`, the top scheme and `fresh-index` are rerun (`-rerun`, cache-busted) to measure
predictor run-to-run noise; `predictable.py` labels each test pushback avoidable / new-info / unclear (Sonnet) and
the report gives scores on the avoidable subset. J = flag rate on test − false-alarm rate on control.

## 4. Limitations to state with results
One person's transcripts; LLM predictor and LLM judge (one judge model, judging all schemes side by side);
n ≈ 130 test / 60 control, so differences under ~8 points are within noise; residual leakage in v0/v1 lesson
wording; prediction is a proxy for "the agent avoids the correction", not a measurement of it.
