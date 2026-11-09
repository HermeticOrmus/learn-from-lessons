---
id: status-in-three-lines
title: End a long unattended stretch with a three-line status
level: rule
parent: report-briefly
scope: global
kind: communication
scenarios: [reporting, long-autonomous]
strength: prefer
trigger: Returning after working unattended for more than a few steps
status: candidate
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-app/c0ffee12.jsonl:1540
    source: claude-code
    date: 2026-10-28
    quote: "too long. what changed, what's broken, what do you need from me"
seen: 1
first_seen: 2026-10-28
last_seen: 2026-10-28
themes: [brevity, reporting]
---

**Do:** Three lines: what changed, what is blocked, what you need from the person.

**Why:** A two-screen summary hid the one question that needed an answer.
