---
id: ask-before-spending-credit
title: Ask before any call that spends the reserved Anthropic credit
scope: global
kind: preference
trigger: About to run a batch or test that would call the Anthropic API directly
status: candidate
evidence:
  - ref: /Users/assassin808/.claude/projects/-Users-assassin808-humanstudy/c9d0e1f2.jsonl:1203
    source: claude-code
    date: 2026-11-06
    quote: "stop — don't use that key, it's reserved. ask me first"
seen: 1
first_seen: 2026-11-06
last_seen: 2026-11-06
themes: [cost]
---

**Do:** Use OpenRouter for tests; ask the person before using the Anthropic key, even for one call.

**Why:** The remaining credit is reserved for batch work; an unannounced run spent part of it.
