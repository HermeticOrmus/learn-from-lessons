---
id: estimate-cost-before-paid-run
title: Before any paid run, state calls, model and a dollar estimate, then wait
level: rule
parent: ask-where-the-person-decides
scope: global
kind: preference
scenarios: [experiments, cost-resources]
strength: must
trigger: About to launch a batch that calls a paid API or rents compute
status: verified
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-bench/77aa01ff.jsonl:1203
    source: claude-code
    date: 2026-08-11
    quote: "how much did that cost?? you ran all 40 models"
  - ref: /Users/alice/.claude/projects/-Users-alice-bench/9c3d2e10.jsonl:56
    source: claude-code
    date: 2026-10-02
    quote: "estimate first, use the cheap model, only the missing runs"
  - ref: "opencode.db#ses_4F2#msg_19"
    source: opencode
    date: 2026-10-30
    quote: "stop — that key is for production runs only"
seen: 3
first_seen: 2026-08-11
last_seen: 2026-10-30
themes: [cost, consent, experiments]
---

**Do:** Print the number of calls, the model, and a cost estimate; default to the cheapest adequate model and only the missing runs.

**Why:** An unannounced full sweep spent the monthly budget in an afternoon.
