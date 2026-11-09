---
name: learn-from-lessons
description: Lessons this person's past agent sessions taught — corrections, pitfalls, preferences, project facts — each linked to the transcript turns it came from. Use before a risky, irreversible or recurring task (deploys, git history, migrations, deleting files, reporting numbers, paper or config edits), when starting work in a project you have not seen this session, or right after the person corrects you. Also use when they ask what you have learned, or about "lessons", "dream", or memory from past sessions.
---

# learn-from-lessons

Lessons are predictions from this person's own transcripts: "in this situation, doing X avoids a correction".
`verified` ones have held in two or more sessions; `candidate` ones are plausible but unconfirmed. The always-loaded
index (between `learn-from-lessons` markers in CLAUDE.md/AGENTS.md) lists verified lessons only; everything else
is searched on demand.

## Before the task

```bash
lessons search "<what you are about to do, in a few words>" --cwd
```

Open the file of any lesson whose `trigger` fits the situation and follow its **Do**. Treat a `candidate` as a
hint, not a rule. If a lesson conflicts with what the person says now, the person wins — mention the conflict.
(With the MCP server configured, `search_lessons` / `get_lesson` do the same.)

## After

- The person corrected you on something a lesson should have covered, or a lesson proved wrong: say so in one line.
- No lessons yet, or it has been a week or more of sessions: suggest `lessons dream` (reads local transcripts,
  extracts and merges lessons with a model; it costs model calls, so ask first). Then `lessons index` and
  `lessons inject --target CLAUDE.md` (a diff; `--apply` writes it).

Do not edit lesson files by hand from inside a task, and never copy secrets into one.
