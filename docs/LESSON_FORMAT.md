# Lesson format (v0)

A lesson is a **prediction**: "next time this situation comes up, doing X avoids a correction". It is short, it
cites the transcript turns it came from, and it says whether it has been seen to hold.

One lesson per file: `lessons/<scope-dir>/<slug>.md`, where scope-dir is `global/`, `project-<name>/` or
`tool-<claude-code|codex|opencode>/`.

```markdown
---
id: <slug>                      # kebab-case, unique
title: <one imperative line: what to do or avoid>
scope: global | project:<absolute path> | tool:<name>
kind: preference | workflow | pitfall | fact | communication
trigger: <when this applies, concretely: the situation an agent would recognise>
status: candidate | verified | refuted   # verified = seen in >= 2 sessions, or the fix was confirmed later
evidence:
  - ref: <path:line or db#message id>   # exact place in the transcript
    source: claude-code | codex | opencode
    date: YYYY-MM-DD
    quote: "<the person's words, <= 200 chars, secrets removed>"
seen: <number of distinct sessions>
first_seen: YYYY-MM-DD
last_seen: YYYY-MM-DD
---

**Do:** <the behaviour, one or two sentences>

**Why:** <what went wrong without it, from the evidence>

**Check:** <how an agent can tell, in the moment, that it is following it (optional)>
```

Rules for writing lessons:
- Purpose, not a log: a lesson must change what an agent does next time. Drop one-off task chatter.
- Generalise one level up from the incident, no further ("verify numbers against the source table before
  reporting them", not "Table 3 had 0.42"); keep project-specific facts project-scoped.
- Never invent: every claim must be supported by the cited turns. Unclear → leave it out.
- Merge rather than duplicate: one lesson with several evidence entries beats several near-copies.
- Keep the person's own words in `quote`; write the rest in English.
