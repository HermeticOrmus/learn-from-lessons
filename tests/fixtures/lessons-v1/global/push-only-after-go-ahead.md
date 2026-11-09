---
id: push-only-after-go-ahead
title: Push, open a PR or deploy only after an explicit go-ahead for this batch
level: rule
parent: ask-where-the-person-decides
scope: global
kind: workflow
scenarios: [coding, release-deploy]
strength: must
trigger: Work is committed locally and the next step touches a shared remote
status: verified
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-app/0a1b2c3d.jsonl:214
    source: claude-code
    date: 2026-07-02
    quote: "why did you push? I said wait until I review"
  - ref: /Users/alice/.codex/sessions/2026/09/rollout-9f8e.jsonl:77
    source: codex
    date: 2026-09-15
    quote: "don't open the PR yet, just show me the diff"
seen: 2
first_seen: 2026-07-02
last_seen: 2026-09-15
themes: [git, consent]
---

**Do:** Stop after the local commit, show the diff summary, and ask "push?".

**Why:** Twice a push went out before review and had to be reverted on a shared branch.

**Check:** The last message from the person in this batch says push, ship or merge.
