<!-- lessons:abstract -->
You are organising one person's lessons (learned from their coding-agent transcripts) into a hierarchy, so an
agent can load a few general principles always and the specific rules only when they apply.

- A `principle` is a behavioural tenet that several rules are cases of, e.g. *"Don't act on assumptions where
  the person holds the decision: ask for the essential information first."* Its rules might be *"push or open a
  PR only after an explicit go-ahead"* and *"ask before launching a paid run"*.
- A `rule` is an actionable lesson for a recognisable situation. An `instance` is a narrow case under a rule.

Your purpose: find the principles that would let an agent do the right thing in a situation none of these rules
names yet. A principle must be actionable, not a platitude:
- It says what to do and when, so an agent could follow it with no rule in front of it. "Be careful", "communicate
  well", "respect the person" and "follow instructions" are platitudes: never write them.
- It is what its rules have in common, stated once; it adds no claim that its rules do not support.
- It covers at least 2 of the rules below (or rules already under it). Never make a principle from one rule.
- Rules that share no real tenet stay without a principle; that is fine and common.
- Prefer attaching rules to an existing principle (by its P-number) over writing a near-copy of it.

{{GUIDANCE}}

You may also file a narrow rule as an `instance` under a broader rule from the list, when it is a special case
of that rule (same behaviour, one project or one tool). Only do this where the broader rule gets at least two
such instances; otherwise leave both as rules.

Write at most {{MAX_NEW}} new principle(s). Write in English.

Scenario ids you may use: {{SCENARIOS}} (principles are usually `any`).

Answer with a JSON object only, no prose around it:

```json
{
  "principles": [
    {
      "use": "",
      "rules": ["R1", "R4"],
      "title": "one imperative line: the tenet",
      "trigger": "the kind of situation it applies to",
      "do": "the behaviour, one or two sentences",
      "why": "what goes wrong without it, as its rules show",
      "check": "how an agent can tell, in the moment, that it is following it (may be empty)",
      "kind": "preference | workflow | pitfall | fact | communication",
      "scenarios": ["any"],
      "strength": "must | should | prefer",
      "themes": ["two to four short topic words"],
      "note": "one line for the changelog"
    }
  ],
  "instances": [{"rule": "R3", "under": "R2", "note": "one line"}]
}
```

To attach rules to an existing principle, set `"use": "P2"` and list the rules; the other fields may be empty.

Existing principles:

{{PRINCIPLES}}

Rules without a principle yet:

{{RULES}}
