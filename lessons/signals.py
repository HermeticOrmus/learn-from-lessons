"""Moments worth learning from, found without a model: the person corrected, stopped or redirected the agent,
or a tool kept failing. Each moment keeps the turns before it, so a lesson can be checked against what happened."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from .model import Session, Turn

# A person's turn that pushes back. Chinese and English; matched at the start, where corrections usually sit.
CORRECTION = re.compile(
    r"^\s*(?:不是|不对|不要|别|错了|你(?:又|还是|怎么)|我(?:说|是说|的意思)|为什么(?:你|要|没)|停|算了|重新|还是不|"
    r"no\b|nope|wrong|that'?s not|not what|i said|i meant|why (?:did|are|would) you|stop|don'?t|undo|revert|again\??$|"
    r"you (?:didn'?t|forgot|missed|broke)|still (?:not|broken|wrong))",
    re.I,
)
FRUSTRATION = re.compile(r"(?:怎么做半天|还是不行|又来了|说了多少次|理想化|没用|废话|you keep|keeps? (?:failing|breaking)|ugh|wtf)", re.I)
INTERRUPT = re.compile(r"^\[Request interrupted by user")


@dataclass
class Moment:
    kind: str                    # correction | frustration | interrupt | repeated-error
    session: Session
    index: int                   # index of the triggering turn in session.turns
    context: list[Turn] = field(default_factory=list)
    followup: list[Turn] = field(default_factory=list)  # the person's next turns: an interrupt's reason is usually here

    @property
    def turn(self) -> Turn:
        return self.session.turns[self.index]


def _context(s: Session, i: int, before: int = 6) -> list[Turn]:
    return s.turns[max(0, i - before): i + 1]


def _followup(s: Session, i: int, n: int = 2) -> list[Turn]:
    return [t for t in s.turns[i + 1:i + 12] if t.role == "user"][:n]


def find(s: Session, seen: set | None = None) -> list[Moment]:
    """Moments in one session. `seen` (shared across sessions) drops turns already found elsewhere: resumed and
    forked sessions copy earlier turns under a new session id, and some files repeat turns."""
    out: list[Moment] = []
    seen = set() if seen is None else seen
    seen_agent = False
    for i, t in enumerate(s.turns):
        if t.role == "assistant":
            seen_agent = True
        if t.role != "user" or not seen_agent:
            continue  # a correction answers something the agent did
        text = t.text.strip()
        if INTERRUPT.match(text):
            kind = "interrupt"
        elif CORRECTION.match(text):
            kind = "correction"
        elif FRUSTRATION.search(text[:400]):
            kind = "frustration"
        else:
            continue
        key = (text[:300], t.ts[:19])
        if key in seen:
            continue
        seen.add(key)
        out.append(Moment(kind, s, i, _context(s, i), _followup(s, i)))
    # The same tool failing three times in a row: the agent was stuck.
    run = 0
    for i, t in enumerate(s.turns):
        if t.role == "tool":
            run = run + 1 if t.error else 0
            if run == 3:
                out.append(Moment("repeated-error", s, i, _context(s, i, 8)))
    return out
