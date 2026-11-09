"""Label each test moment: could the pushback have been anticipated from the context at all? -> predictable.json

    python3 eval/predictable.py
'avoidable' = the agent's last action went against something visible in the context or a recognisable general
preference; 'new-info' = the person brings a new direction, fact or preference nobody could infer; 'unclear'.
Used to report scores on the subset where anticipation is possible (a ceiling check, not a scheme input).
"""
from __future__ import annotations
import os, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import call, clip, jload, jsave, parse_json, render_context  # noqa: E402

SYSTEM = "You analyse conversations between a person and a coding agent. Answer only with JSON."
PROMPT = """Below are the last turns of a conversation between a person (USER) and their coding agent, then the person's next message, which pushes back or redirects.

## Last turns
{context}

## The person's next message
{pushback}

Could an attentive observer have anticipated this reaction before it happened, from the turns shown plus general knowledge of what careful people want from an agent?
- "avoidable": the agent's last action went against something visible above (an instruction, an earlier decision, the person's stated goal) or a recognisable general preference (don't act without approval, answer the question asked, don't invent facts, ...).
- "new-info": the person mainly brings a new direction, a new fact, or a personal preference that nothing above reveals.
- "unclear": the turns shown are not enough to tell.

Answer: {{"label": "avoidable" | "new-info" | "unclear", "why": "one short sentence"}}"""


def label(m):
    ans = parse_json(call(PROMPT.format(context=render_context(m["context"], 6), pushback=clip(m["pushback"], 1500)),
                          system=SYSTEM, model="sonnet", tag="predictable")) or {}
    lab = ans.get("label") if isinstance(ans, dict) else None
    return m["id"], lab if lab in ("avoidable", "new-info", "unclear") else "unclear"


if __name__ == "__main__":
    test = jload("split.json")["test"]
    with ThreadPoolExecutor(8) as ex:
        out = dict(ex.map(label, test))
    jsave("predictable.json", out)
    from collections import Counter
    print(Counter(out.values()))
