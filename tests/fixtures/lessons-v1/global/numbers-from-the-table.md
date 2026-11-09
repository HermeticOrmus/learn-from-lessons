---
id: numbers-from-the-table
title: Copy every reported number from the table it came from, never from memory
level: rule
parent: verify-before-claiming
scope: global
kind: pitfall
scenarios: [writing-paper, reporting]
strength: should
trigger: Writing a results paragraph, caption or abstract that cites statistics
status: verified
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-papers/a1b2c3d4.jsonl:412
    source: claude-code
    date: 2026-08-14
    quote: "Table 2 says 0.31, not 0.42 — where did that come from?"
  - ref: /Users/alice/.claude/projects/-Users-alice-papers/e5f6a7b8.jsonl:88
    source: claude-code
    date: 2026-09-02
    quote: "the table says 16 studies, not 17"
seen: 2
first_seen: 2026-08-14
last_seen: 2026-09-02
themes: [accuracy, papers]
---

**Do:** Open the table or CSV, copy the value, and cite the file next to the sentence while drafting.

**Why:** Wrong statistics reached a draft twice and the person had to catch them.

**Check:** Each number in the paragraph has a cell or line you can point to.
