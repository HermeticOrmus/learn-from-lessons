---
id: csv-header-offset
title: The results CSV has two header rows; row numbers are off by one
level: instance
parent: numbers-from-the-table
scope: project:/Users/alice/bench
kind: fact
scenarios: [experiments, writing-paper]
strength: prefer
trigger: Reading results.csv by row number
status: refuted
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-bench/1a2b3c4d.jsonl:77
    source: claude-code
    date: 2026-09-05
    quote: "row 12 is the header, you're one off"
seen: 1
first_seen: 2026-09-05
last_seen: 2026-09-05
themes: [accuracy, data]
---

**Do:** Read the CSV by column name, not row index.

**Why:** Later exports have a single header row; this held only for the September export.
