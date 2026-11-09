<!-- Paste into AGENTS.md (Codex, opencode, and other agents that read it). -->

## Lessons from past sessions

This machine has `lessons` (learn-from-lessons): lessons extracted from this person's earlier agent sessions,
each citing the transcript turns it came from. `verified` = held in two or more sessions; `candidate` = unconfirmed.

- Before a risky, irreversible or recurring task (deploys, git history, migrations, deleting files, reporting
  numbers), and when you start work in a project, run:
  `lessons search "<what you are about to do>" --cwd`
  Read any lesson whose trigger fits and follow its **Do**. Treat candidates as hints. The person's current
  instructions override a lesson; mention the conflict.
- If the MCP server is configured, the `search_lessons` and `get_lesson` tools do the same.
- If there are no lessons yet, or the person corrects you on something that keeps recurring, suggest
  `lessons dream` (it calls a model, so ask first).
- The block between `<!-- learn-from-lessons:start -->` and `<!-- learn-from-lessons:end -->` is generated
  by `lessons inject`; do not edit it by hand.
