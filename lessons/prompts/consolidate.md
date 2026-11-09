<!-- lessons:consolidate -->
You are tidying one person's memory of lessons learned from their coding-agent transcripts. The lessons below
were grouped because they look related. Some are new candidates, some already existed. Each is a prediction
("next time this comes up, doing X avoids a correction") backed by quotes from the transcripts, which are the
ground truth.

Your purpose: leave the smallest set of lessons that says everything the evidence supports, each one sharp
enough to change what an agent does next time.
- Lessons that say the same thing become one: rewrite it so it covers all of its evidence, generalised one
  level up from the incidents, no further.
- Lessons that only look alike stay separate; do not force a merge.
- If later evidence shows a lesson was wrong (the person reversed it, or the advice caused the next
  correction), mark it refuted and cite the contradicting evidence.
- Never invent a claim the quotes do not support. Write in English; the quotes stay as they are.

You do not handle evidence: every lesson you output automatically keeps all the evidence of the lessons it
merges. Lessons you leave out of your answer are kept unchanged.

Answer with a JSON object only, no prose around it:

```json
{
  "lessons": [
    {
      "merge": ["L1", "L3"],
      "title": "one imperative line",
      "scope": "global | project | tool:<name>",
      "kind": "preference | workflow | pitfall | fact | communication",
      "trigger": "when this applies, concretely",
      "do": "the behaviour, one or two sentences",
      "why": "what went wrong without it, from the evidence",
      "check": "how an agent can tell it is following it (may be empty)",
      "themes": ["short", "topic", "words"],
      "refuted_by": [],
      "note": "one line for the changelog: what you changed and why"
    }
  ]
}
```

`merge` lists the lessons (by their L-number) that this output replaces; a lesson you only rewrite is a merge of
one. `refuted_by` lists evidence references (like `e4`) that contradict the lesson; leave it empty otherwise.

Lessons:

{{LESSONS}}
