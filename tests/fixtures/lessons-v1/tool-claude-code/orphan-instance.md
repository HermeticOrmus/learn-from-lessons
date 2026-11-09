---
id: hooks-run-in-repo-root
title: Hooks run from the repository root, not the current directory
level: instance
parent: a-rule-that-does-not-exist
scope: tool:claude-code
kind: fact
scenarios: [coding]
strength: prefer
trigger: Writing a hook script that uses relative paths
status: candidate
evidence:
  - ref: /Users/alice/.claude/projects/-Users-alice-app/beef0001.jsonl:9
    source: claude-code
    date: 2026-11-01
    quote: "the hook can't find ./scripts, it runs from the root"
seen: 1
themes: [hooks]
---

**Do:** Use absolute paths in hook scripts.
