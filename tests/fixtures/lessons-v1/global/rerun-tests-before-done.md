---
id: rerun-tests-before-done
title: Re-run the failing test before saying a fix works
level: rule
parent: verify-before-claiming
scope: global
kind: workflow
scenarios: [coding, debugging]
strength: should
trigger: About to report that a bug is fixed
status: candidate
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-app/5e6f7a8b.jsonl:930
    source: claude-code
    date: 2026-10-19
    quote: "it still fails <script>alert('x')</script> — did you even run it?"
seen: 1
first_seen: 2026-10-19
last_seen: 2026-10-19
themes: [testing, verification]
---

**Do:** Run the exact failing command again and paste its last lines into the report.

**Why:** A "fixed" bug came back on the first run; see <b>this</b> is not bold. Links like [bad](javascript:alert(1)) must not work.
