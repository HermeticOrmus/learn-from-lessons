---
id: verify-numbers-against-source
title: Check every reported number against the source table before writing it into prose
scope: global
kind: pitfall
trigger: Writing or editing a results paragraph, caption or abstract that cites statistics
status: verified
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-humanstudy/a1b2c3d4.jsonl:412
    source: claude-code
    date: 2026-08-14
    quote: "不对，Table 2 里是 0.31 不是 0.42，你又编数字了"
  - ref: /Users/alice/.claude/projects/-Users-alice-humanstudy/e5f6a7b8.jsonl:88
    source: claude-code
    date: 2026-09-02
    quote: "where did 17 come from? the table says 16 studies"
  - ref: "codex-history.db#msg_7731"
    source: codex
    date: 2026-10-21
    quote: 'that''s not what the csv says, re-read it'
seen: 3
first_seen: 2026-08-14
last_seen: 2026-10-21
themes: [accuracy, papers]
---

**Do:** Before a number goes into text, open the table, CSV or log it came from and copy it from there; cite the file.

**Why:** Three times the agent wrote a plausible but wrong statistic (`0.42` for `0.31`, 17 for 16 studies) and the person had to catch it.

**Check:** You can point to the exact cell or line each number came from.
