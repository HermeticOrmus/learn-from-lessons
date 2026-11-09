<!-- lessons:extract -->
You are reading moments from one person's coding-agent transcripts. In each moment the person corrected,
stopped or redirected their agent, or a tool kept failing. The transcripts are the ground truth; your job is to
write down what an agent should do differently next time so the person does not have to correct it again.

A lesson is a prediction: "next time this situation comes up, doing X avoids a correction". Write a lesson only
when the moment shows something an agent could change. Most interrupts and one-off task chatter teach nothing;
skip them. It is fine, and common, to return an empty list.

What makes a lesson good:
- It changes behaviour next time. Generalise one level up from the incident, no further ("verify numbers against
  the source table before reporting them", not "Table 3 had 0.42").
- It is supported by the cited turns. Never invent a reason, a rule or a quote. If the moment is unclear, leave it out.
- Facts that only hold in one project are scoped to that project; habits of the person are global; quirks of one
  agent tool are scoped to that tool.
- One lesson with several pieces of evidence beats several near-copies. If two moments teach the same thing,
  write one lesson citing both.

Every turn below carries a reference in square brackets, like `[r12]`. Cite evidence only with those references,
copied exactly. Never make one up: lessons citing a reference that is not below are thrown away. Prefer citing
the person's own turn (marked USER); its quote must be words the person actually wrote, copied verbatim
(at most 200 characters, secrets removed). Write everything else in English.

Answer with a JSON list only, no prose around it. Each element:

```json
{
  "title": "one imperative line: what to do or avoid",
  "scope": "global | project | tool:claude-code | tool:codex | tool:opencode",
  "kind": "preference | workflow | pitfall | fact | communication",
  "trigger": "when this applies, concretely: the situation an agent would recognise",
  "do": "the behaviour, one or two sentences",
  "why": "what went wrong without it, from the evidence",
  "check": "how an agent can tell, in the moment, that it is following it (may be empty)",
  "themes": ["two to four short lowercase topic words, e.g. git, figures, verification"],
  "evidence": [{"ref": "r12", "quote": "the person's words"}]
}
```

Moments:

{{MOMENTS}}
