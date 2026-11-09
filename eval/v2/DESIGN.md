# Evaluation v2: does consulting lessons change what the agent *does*?

v1 (`eval/DESIGN.md`, results `work/eval/RESULTS.md`) asked a model to *predict* the person's pushback. No scheme
beat no-memory beyond run-to-run noise (±5 points), lessons tripled false alarms, and only 26/112 pushbacks were
judged foreseeable. Three suspected causes: most pushbacks bring new information; the "will they push back?"
question primes suspicion; and prediction is not the real use. v2 measures the real use: an agent that reads the
lessons **before acting** — does it act in a way the person would have pushed back on less?

## 1. Moments and the foreseeable subset

- **Window A** (cutoff c1 = 2026-09-28, v1's split): v1's 112 test moments plus the remaining candidates that v1's
  per-session cap (10) dropped, with the cap raised to 16. Same filters as `eval/split.py` (pushback ≥ 8 visible
  chars, not a bare "continue", duplicates dropped).
- **Window B** (earlier-cutoff variant, c2 = 2026-09-04, the 50th percentile of correction dates): moments in
  [c2, c1) with the same filters and cap. For these, every library is strict-filtered at **c2**, so no lesson saw
  them. (Window-A moments use libraries strict-filtered at c1.)
- **Foreseeable label**: v1's `eval/predictable.py` prompt and model (Sonnet), unchanged; v1 labels are reused
  (cache hits) and new moments get the same call. foreseeable = `avoidable`; not = `new-info` or `unclear`.
- **Evaluation set**: all foreseeable moments (target n ≥ 60) + a seeded random sample of 30 not-foreseeable ones.
  Metrics on **all** are post-stratified: weighted by the share of foreseeable moments among all labelled
  candidates (≈ 23%), so "all" estimates the natural mix, not the oversampled one.
- **Control**: 30 of v1's 60 accepted turns (person replied "ok / 继续 / 可以"), seeded sample, cutoff c1.

## 2. Schemes (all leak-free: strict filter, every evidence item < the moment's cutoff)

| scheme | memory injected |
|---|---|
| `none` | nothing |
| `v0-index` | flat v0 index: id, title, trigger (strict) |
| `v1-scenario` | v1 hierarchy (`library-v2`): principles + rules/instances whose scenarios match the moment's scenario (Haiku-inferred, cached, as in v1) |
| `graph-scenario` | graph library, scenario-filtered as above, **plus the resolution line of every conflict / trade-off edge** whose two endpoints are both injected |
| `profile` | profile library: 8 traits + strict rules/instances |
| `playbook` | `playbook-any` + the scenario's playbook, keeping only checks whose cited v0 lessons are all strict |

## 3. Action replay (primary metric)

- **Cut**: the agent's turn that drew the pushback = the last `ASSISTANT` entry of the stored context (and any tool
  output after it). The agent sees the context entries before it (up to 5, redacted, clipped as in v1). If the
  context ends with a user entry, the agent sees all of it.
- **Agent** (`claude -p --model haiku`, lean flags, no tools): system = "you are the person's coding agent" +
  memory (if any) with the instruction to apply the notes where relevant and not to mention them. Prompt: write
  your next turn — the tool calls you would make (one line each) and/or the message to the person, ≤ 150 words.
  No mention of pushback or of the person's reaction.
- **Judge** (`claude -p --model sonnet`), one call per (moment, scheme, rep): sees the context, the agent's
  original action, the person's actual pushback, and two candidate replies `A`/`B` = memory and no-memory in an
  order randomised per (moment, scheme, rep), never told which has memory. Question: given what this person said,
  which reply would they have been less likely to push back on — `A`, `B` or `tie` (tie when both avoid it or both
  repeat the problem). Instructed to ignore length and polish.
- **Score**: per scheme, win / tie / loss rates for memory vs none; net = win − loss. 95% CI by bootstrap over
  moments (2,000 resamples, seed 0), stratified F / not-F for "all".
- **Control** (accepted turns, 1 rep): same replay; a judge with **no** reaction info picks which reply better
  fulfils the person's request / is what a careful person would want next (`A`/`B`/`tie`). Memory should not lose.

## 4. Retrieval

For each foreseeable moment, Sonnet sees the context, the original action, the pushback and the **full v0 library**
(74 lessons, id/title/trigger/do; all v1 libraries share these ids) and names the lessons (0–3) that, if followed,
would have prevented the pushback. These are the ground-truth ids. For each scheme we compute the v0 ids it
injects (playbooks: ids cited by kept checks; hierarchies: leaf ids, principles excluded) plus BM25 search top-3 /
top-8 over the strict v0 library (query = last 4 context turns), and report: share of moments with any relevant
lesson in the full / strict library (coverage), hit rate (selection contains ≥ 1 relevant id), recall over ids,
precision (|selected ∩ relevant| / |selected|), mean injected tokens.

## 5. Variance

The primary metric runs 3× on the foreseeable moments (rep r > 0: lesson-list notes in a seeded shuffled order — rep 0
and the playbook keep the original order — and a rep tag that
busts the cache for both the agent and `none`; judge order re-randomised); rep r memory is paired with rep r none.
Report mean ± sd of net win across reps. Not-foreseeable moments run 1× (budget).

## 6. Budget and hygiene

≤ $15. Every uncached call is logged with its cost in `work/eval/v2/calls.jsonl` (`EVAL_BUDGET_USD=15` stops new
calls); prompts are cached by hash (shared cache `work/eval/cache/`). All transcript text passes `lessons.redact`
before any call. Nothing under `work/` is committed. Pilot: 15 moments × all schemes × 1 rep, read by hand, then full.

## 6b. Changes after the pilot and the v1 review (`work/eval/REVIEW-v1.md`), before the full run

- **Context leaks** (review §1): `v2common.leak_reasons` drops any moment whose stored context contains the pushback
  or follow-up text (substring, ≥ 0.7 similarity, or a queued user message that prefixes the pushback), any
  tool-rejection / interrupt marker, or a non-human trigger (Codex approval-review blobs). It catches all 10 moments
  the review names; 51 of 269 labelled moments are excluded (with those lacking a shown prefix), and `replay.py`
  asserts the check on every moment it runs. Controls: also dropped if leaky or not a bare approval (review §5).
- **Parent principles** (review §2): never injected in their original wording; rebuilt by Sonnet from the kept
  pre-cutoff children only, per library and cutoff (`schemes2.rebuild_parents`). Playbook lines that cite nothing are
  dropped. Rows are labelled `strict*` (evidence-filtered, but rule wording by an author who saw all data);
  `fresh-index` (dream library built from training moments < c1; window A only) and `none` are the only fully
  leak-free rows.
- **Same system prompt in both arms** (review §5): the no-memory arm gets `<notes>(no notes)</notes>`.
- **Forced choice** (review §4): the judge must pick A or B; `clear: false` marks marginal picks, reported as
  "tie" in win/tie/loss; the forced win rate (50% = no effect) is the headline.
- **CIs** (review §3): two-stage bootstrap — resample sessions, then each moment's reps with replacement — so
  agent/judge randomness is inside the interval (fixed after REVIEW-v2, which found the first version drew one rep
  per moment and paired sorted replicates for post-stratification).
- **Batched judging (budget)**: separate pairwise calls cost $0.013 each (Sonnet thinks even at `--effort low`),
  ≈ $17 for the full run. The judge therefore gets one call per (moment, rep) with all replies under shuffled labels
  R1..Rk and makes one independent forced choice per listed (memory, none) pair, pair order and within-pair order
  randomised; it is never told which reply has memory. On the 15 pilot moments (90 pairs) batched and separate
  calls agreed on 71% of forced choices, with the same overall lean (memory won 36/90 batched, 40/90 separate).
- The judge runs at `--effort low` (an `effort` option added to `eval/common.call`, part of the cache key).

## 7. Limitations to state

One person; LLM agent and LLM judge; the judge knows the pushback, so it measures "avoids this specific
objection", which favours replies that ask or hedge — the control checks that this does not make accepted turns
worse. Replay is one turn, not a session. Strict filtering shrinks libraries (window B more so). Moments within a
session are correlated; the bootstrap over moments is optimistic.
