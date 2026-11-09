# Contributing

Thanks for helping. This is a small, dependency-free Python project, so most changes need nothing more than a
clone and `python3`.

## Privacy rule (read this first)

**Never commit transcripts, moment shards, or your own lessons.** That includes excerpts in test fixtures,
issue text and screenshots. Your lessons quote your own words and paths, so they are personal data even after
redaction.

- `work/`, `library/`, `.lessons/` and `*.dream/` are gitignored. Keep personal output in those folders.
- Test fixtures (`tests/fixtures/`) must be **synthetic**: invented quotes, invented paths such as `/x/session.jsonl`.
- When you report a parsing bug, describe the shape of the record (keys, types) instead of pasting it. If you
  have to share a sample, write a minimal fake one by hand.
- Aggregate counts (moments per kind, lessons per level) are welcome in Discussions. Text is not.

## Dev setup

```bash
git clone https://github.com/assassin808/learn-from-lessons
cd learn-from-lessons
python3 -m lessons.cli scan            # runs from the checkout, no install needed
pip install -e .                       # optional: puts `lessons` on your PATH
```

You need Python ≥ 3.10. The package has no runtime dependencies, and new ones need a strong reason.
To try `dream` without spending model calls:

```bash
python3 -m lessons.dream --engine fake --max-batches 1 --out work/dream-smoke   # deterministic answers
lessons dream --dry-run                                                          # print prompts only
```

## Tests

```bash
python3 -m unittest discover tests
```

Tests use only synthetic fixtures and temporary directories, and must not read `~/.claude`, `~/.codex` or
opencode databases. Add a test with each change in behaviour.

## Adding a source reader

A reader turns one agent's on-disk history into the shared model in [`lessons/model.py`](lessons/model.py):

```python
@dataclass
class Turn:
    role: str            # "user" | "assistant" | "tool"
    text: str            # what was said, or the tool's output (truncated)
    ts: str = ""         # ISO time when known
    tool: str = ""       # tool name for tool calls / results
    error: bool = False  # a tool result that failed
    ref: str = ""        # where to find it again (path:line, db#message id)

@dataclass
class Session:
    source: str          # e.g. "cursor"
    id: str
    path: str            # file or database the session came from
    cwd: str = ""
    started: str = ""
    subagent: bool = False
    turns: list[Turn]
```

Steps:

1. Create `lessons/sources/<tool>.py` with a `sessions() -> Iterator[Session]` function. Use
   [`claude_code.py`](lessons/sources/claude_code.py) (JSONL files), [`codex.py`](lessons/sources/codex.py)
   (JSONL with typed records) or [`opencode.py`](lessons/sources/opencode.py) (SQLite, opened read-only) as
   templates.
2. Register it in `SOURCES` in [`lessons/sources/__init__.py`](lessons/sources/__init__.py), and add the name to
   the `--source` choices in `cli.py` and to `TOOLS` in `dream.py`.
3. Rules a reader must follow:
   - **Read-only.** Never write to or lock the agent's files. Open databases with `mode=ro`.
   - **Only what the person typed is a `user` turn.** Text the harness injects into the user role (system
     reminders, command output, "continued session" summaries) must be skipped, or it will look like a
     correction. See `HARNESS` in `claude_code.py`.
   - **Stable `ref`.** Every turn needs a ref that leads back to the exact record, because lessons cite it as
     evidence.
   - **Mark subagents** (`subagent=True`) so they are not counted as human sessions.
   - **Set `error=True`** on failed tool results, which drives the repeated-error signal.
   - **Fail soft.** Skip records you cannot parse instead of raising an exception.
4. Add a test with a small **hand-written** fixture in that tool's format, checking roles, refs, subagent
   detection and harness filtering.
5. Mention the default history location in the reader's docstring and in the README.

Readers we would especially like: Cursor, Aider, Gemini CLI, Cline.

## Other changes

- **Signals** (`lessons/signals.py`): new correction patterns need a test showing a true positive and a near
  miss that must not match.
- **Prompts** (`lessons/prompts/`): include a `--dry-run` sample of the changed prompt in the PR.
- **Lesson format**: changes to `docs/LESSON_FORMAT.md` need a version note and must keep v1 files readable.
- Style: short functions, standard library only, comments that say *why*.

## Pull requests

Keep PRs focused, describe what you ran (tests, `scan` on your own machine, counts only), and confirm that no
personal data is included. Discuss larger ideas first in
[GitHub Discussions](https://github.com/assassin808/learn-from-lessons/discussions).

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
