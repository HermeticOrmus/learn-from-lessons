"""One shape for every agent's history: a session is a list of turns."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Turn:
    role: str            # "user" | "assistant" | "tool"
    text: str            # what was said, or the tool's output (truncated)
    ts: str = ""         # ISO time when known
    tool: str = ""       # tool name for tool calls / results
    error: bool = False  # a tool result that failed
    ref: str = ""        # where to find it again (line number, message id)


@dataclass
class Session:
    source: str          # "claude-code" | "codex" | "opencode"
    id: str
    path: str            # file or database the session came from
    cwd: str = ""
    started: str = ""
    subagent: bool = False
    turns: list[Turn] = field(default_factory=list)

    @property
    def user_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.role == "user"]
