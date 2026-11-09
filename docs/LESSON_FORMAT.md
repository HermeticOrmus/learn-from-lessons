# Lesson format (v1)

A lesson is a **prediction**: "when this situation comes up, doing X avoids a correction". It cites the
transcript turns it came from, says whether it has been seen to hold, and sits at one **level of abstraction**
in a hierarchy, the way Anthropic's agent memory separates raw transcripts, notes, consolidated pages and a
small injected index (https://www.anthropic.com/research/components-of-agent-memory).

## The hierarchy (abstraction strength)

| level | what it is | how many | example |
|---|---|---|---|
| `principle` | A general behavioural tenet that holds across scenarios. No evidence of its own: it is supported by its rules. | 5–12 | *Don't act on assumptions where the person holds the decision: ask for essential information first.* |
| `rule` | An actionable lesson for a recognisable situation; parent = a principle. | most lessons | *Push, open a PR or launch a paid run only after an explicit go-ahead for this batch.* |
| `instance` | A concrete, narrow case: a project fact, a tool quirk, one recurring incident; parent = a rule. | as needed | *In the paper repo, Overleaf is the source of truth: pull before every edit.* |

An agent loads principles always (they are few), rules for the current scenario, and instances only when the
project or tool matches. Abstraction is a parameter: `--level principle|rule|instance` caps how specific the
injected layer gets.

## Scenarios

Every lesson lists the `scenarios` it applies to, from `docs/SCENARIOS.md` (a small controlled vocabulary, e.g.
`writing-paper`, `coding`, `experiments`, `release-deploy`). A lesson about pushing branches applies to `coding` and
`release-deploy`, not to `writing-paper` — so filtering by scenario keeps the injected set relevant.
`scenarios: [any]` means it applies everywhere (most principles).

## File

One lesson per file: `lessons/<scope-dir>/<slug>.md`, scope-dir = `global/`, `project-<name>/`, `tool-<name>/`.

```markdown
---
id: <slug>
title: <one imperative line>
level: principle | rule | instance
parent: <id of the principle (for a rule) or rule (for an instance); empty for a principle>
scope: global | project:<absolute path> | tool:<claude-code|codex|opencode>
kind: preference | workflow | pitfall | fact | communication
scenarios: [<from docs/SCENARIOS.md>, ...]
strength: must | should | prefer       # must = the person stopped or reverted the agent over it
trigger: <when this applies, concretely>
status: candidate | verified | refuted # verified = >= 2 distinct conversations (forks counted once)
evidence:                              # principles: none (they cite their rules)
  - ref: <path:line or db#message id>
    source: claude-code | codex | opencode
    date: YYYY-MM-DD
    quote: "<the person's words, <= 200 chars, secrets removed>"
seen: <distinct conversations>
first_seen: YYYY-MM-DD
last_seen: YYYY-MM-DD
themes: [<2-4 keywords>]
---

**Do:** <the behaviour>

**Why:** <what went wrong without it>

**Check:** <how to tell in the moment (optional)>
```

## Writing rules
- Purpose, not a log: a lesson changes what an agent does next time.
- Pick the level deliberately: generalise to the highest level that is still actionable; keep narrower
  variants as children instead of duplicates. A principle must be supported by ≥ 2 rules.
- Never invent: every claim is supported by cited turns. Keep the person's words in `quote`; write the rest in English.
