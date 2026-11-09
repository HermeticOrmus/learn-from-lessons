---
id: overleaf-pull-first
title: In the paper repo, Overleaf is the source of truth; pull before every edit
level: instance
parent: push-only-after-go-ahead
scope: project:/Users/alice/papers/report
kind: fact
scenarios: [writing-paper, release-deploy]
strength: must
trigger: Editing .tex files in the report repository
status: verified
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-papers-report/44ab90cd.jsonl:61
    source: claude-code
    date: 2026-09-20
    quote: "you overwrote my coauthor's edits, pull first"
  - ref: /Users/alice/.claude/projects/-Users-alice-papers-report/51de02aa.jsonl:12
    source: claude-code
    date: 2026-10-11
    quote: "again — git pull before touching main.tex"
seen: 2
first_seen: 2026-09-20
last_seen: 2026-10-11
themes: [git, papers]
---

**Do:** `git pull` before the first edit of a session and before every push.

**Why:** Local edits overwrote a coauthor's changes made in the web editor.
