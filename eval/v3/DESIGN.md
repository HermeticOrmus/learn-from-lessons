# Evaluation v3: behaviour tests (run with subagents, no API calls)

Question: when an agent meets a NEW situation, does a lesson make it behave the way the person wants — and is the
bottleneck *matching* (finding the right lesson) or *applying* it?

## Items
For 24 behaviour-testable verified rules (library-v2), three items each (72 total), written by generator subagents
that see the lesson; the agent under test never sees the item's criterion:
- `apply-1`, `apply-2`: a new project and wording (not from the evidence) where the lesson should change behaviour.
- `control`: a similar-looking situation where the lesson must NOT fire (the right move is to proceed / not ask /
  not hold back) — measures over-application (the "suspicious agent" effect seen in v1).
Each item: `situation` (what the agent knows: project, files, history), `tools` (simulated: read_file, edit_file,
run_shell, git_push, open_pr, run_paid_job{est_cost_usd}, ask_user, reply), `user_message`, `criterion` (pass/fail
rule on the agent's actions, observable), `lesson_id`, `scenario`.

## Conditions (memory block given to the agent)
- `none`: empty notes block (same system text in every arm).
- `all`: every verified principle and rule of library-v2 (≈ the full injected layer).
- `matched`: principles + top-5 rules from `lessons search` with the item's situation+message as context (scenario
  inferred) — the product's real matching path, computed locally (no model).
- `oracle`: principles + only the target rule.

## Runs
Agent subagents (one per condition × slice; a subagent never sees two conditions) answer each item with the tool calls
they would make and their reply (JSON). Judge subagents, blind to condition and shuffled, apply the criterion:
pass / fail / unclear. Metrics: pass rate on apply items, false-hold rate on control items, per condition, with
bootstrap CIs over items; matched-recall = share of items whose target rule is in the matched block.
Interpretation: oracle ≫ none → lessons work when found; matched ≈ none while oracle ≫ none → matching is the
bottleneck; all < oracle → dilution; control failures rising with memory → over-application.
