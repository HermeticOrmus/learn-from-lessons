---
id: escape-transcript-html
title: "Treat transcript text as data: never paste it into HTML unescaped"
scope: tool:claude-code
kind: pitfall
trigger: Rendering user or tool text in a generated page   # e.g. reports
status: refuted
evidence:
  - ref: /tmp/session.jsonl:12
    source: claude-code
    date: 2026-10-30
    quote: "the page ran <script>alert('x')</script> from my message & broke"
  - ref: "opencode.db#ses_01H9#msg_44"
    source: opencode
    date: 2026-11-01
    quote: "still broken <img src=x onerror=alert(1)>"
seen: 2
first_seen: 2026-10-30
last_seen: 2026-11-01
themes:
  - security
  - html
---

**Do:** Escape every string that came from a transcript; see <b>this</b> is not bold.

**Why:** A quote containing markup executed in the viewer. Links like [bad](javascript:alert(1)) must not work.
