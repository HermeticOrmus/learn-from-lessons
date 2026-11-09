---
id: edit-compiled-tex-only
title: Edit only the .tex files the main document actually inputs
scope: project:/Users/assassin808/humanstudy/tech-report
kind: workflow
trigger: Asked to change wording in the paper while several near-identical drafts exist in parts/
status: verified
evidence:
  - ref: /Users/assassin808/.claude/projects/-Users-assassin808-humanstudy/aa11bb22.jsonl:530
    source: claude-code
    date: 2026-09-18
    quote: "you edited p3-draft.tex, that file isn't even compiled"
  - ref: /Users/assassin808/.claude/projects/-Users-assassin808-humanstudy/cc33dd44.jsonl:77
    source: claude-code
    date: 2026-10-03
    quote: "again?? check README for which parts are live"
seen: 2
first_seen: 2026-09-18
last_seen: 2026-10-03
themes: [papers, latex]
---

**Do:** Before editing, read the `\input{}` lines of `neurips_2026.tex` (or the list in [README](README.md)) and edit only those files.

**Why:** Edits to dead drafts did nothing and the person noticed only after compiling.

**Check:**
- The file you are editing appears in an `\input{}`.
- A rebuild shows the change.
