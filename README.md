# learn-from-lessons

**Your coding agents keep making the mistakes you already corrected. This turns those corrections into lessons they read next time.**

`lessons` reads the transcripts that Claude Code, Codex and opencode already keep on your disk, finds the moments
where you pushed back ("no, I said…", an interrupt, the same tool failing three times), and turns them into short
lessons. Each lesson cites the transcript turns it came from and says whether it has held up across sessions. Any
agent can then use them: an always-loaded index in `CLAUDE.md` / `AGENTS.md`, a CLI search, or an MCP server.

Zero dependencies (Python ≥ 3.10 stdlib). Everything stays on your machine.

## Why

Each agent session starts from zero, and so does each tool. A correction you gave Claude Code on Monday is unknown
to Codex on Tuesday, and to Claude Code itself next week. Hand-written `CLAUDE.md` rules help, but they are written
from memory, go stale, and grow until nobody reads them.

This project follows Anthropic's
[components of agent memory](https://www.anthropic.com/research/components-of-agent-memory):

- **Transcripts are the ground truth.** Lessons are derived from them and always point back to them.
- **A lesson is a prediction, not a fact.** It is `candidate` until it holds in a second session, then `verified`;
  it can be `refuted`.
- **Consolidate, don't append.** `lessons dream` merges near-duplicates and rewrites lessons when new evidence
  arrives instead of piling up notes.
- **Keep the always-loaded layer small.** The injected index holds verified lessons only, one line each, at most
  200 lines; agents search the rest on demand.

## 30-second quickstart

```bash
# try it without installing (reads your local transcripts, prints counts only)
uvx --from git+https://github.com/HumanStudy-Hub/learn-from-lessons lessons scan

# or install it
pipx install git+https://github.com/HumanStudy-Hub/learn-from-lessons
lessons scan
```

`scan` output looks like this (format only; your counts will differ):

```
source        sessions  subagent   human  user turns   moments
claude-code        ...       ...     ...         ...   interrupt …, correction …, frustration …, repeated-error …
codex              ...
opencode           ...
```

Then:

```bash
lessons dream                         # extract + consolidate lessons, using your own agent CLI as the model
lessons search "deploy to vercel" --cwd
lessons inject --target CLAUDE.md     # shows a diff; add --apply to write it
lessons view                          # browse lessons and their evidence in a local HTML page
```

`dream` calls whichever agent CLI you have (`claude -p`, `codex exec` or `opencode run`), so it uses your existing
login and costs model calls. `lessons dream --dry-run` shows the prompts without calling anything;
`--max-batches N` caps a run.

## What a lesson looks like

One Markdown file per lesson under `~/.lessons/lessons/<scope>/` (full spec: [docs/LESSON_FORMAT.md](docs/LESSON_FORMAT.md)).
An illustrative example:

```markdown
---
id: verify-numbers-against-source
title: Check every number against the source table before reporting it
scope: global
kind: pitfall
trigger: writing up statistics, results or counts taken from a paper or a data file
status: verified
evidence:
  - ref: ~/.claude/projects/-Users-me-paper/3f1c….jsonl:412
    source: claude-code
    date: 2026-09-14
    quote: "no, Table 2 says 0.31 not 0.42, where did you get that"
  - ref: ~/.codex/sessions/2026/10/02/rollout-….jsonl:88
    source: codex
    date: 2026-10-02
    quote: "these numbers don't match the csv again"
seen: 2
first_seen: 2026-09-14
last_seen: 2026-10-02
---

**Do:** Before writing a number into prose, open the table or file it comes from and copy it from there.

**Why:** Twice the reported value was recalled rather than read, and the person had to catch it.

**Check:** Every number you wrote has a file and location you just looked at.
```

Scopes are `global`, `project:<path>` (only shown when you work inside that project) and `tool:<name>`.
Kinds are `preference`, `workflow`, `pitfall`, `fact` and `communication`.

## Plug it into your agents

**Claude Code** — the index plus a skill that tells the agent when to search:

```bash
lessons inject --target CLAUDE.md --apply              # or ~/.claude/CLAUDE.md for every project
mkdir -p ~/.claude/skills/learn-from-lessons && cp skill/SKILL.md ~/.claude/skills/learn-from-lessons/
claude mcp add lessons -- lessons mcp                  # optional: search_lessons / get_lesson tools
```

**Codex** — `lessons inject --target AGENTS.md --apply`, paste [integrations/AGENTS.md](integrations/AGENTS.md)
into your `AGENTS.md`, and optionally add the MCP server to `~/.codex/config.toml`:

```toml
[mcp_servers.lessons]
command = "lessons"
args = ["mcp"]
```

**opencode** — same `AGENTS.md` steps; MCP in `opencode.json`:

```json
{ "mcp": { "lessons": { "type": "local", "command": ["lessons", "mcp"] } } }
```

**Any MCP client** — `lessons mcp` is a stdio server (JSON-RPC 2.0, no SDK) with two tools:
`search_lessons(query, cwd?)` and `get_lesson(id)`.

`inject` only ever rewrites the text between `<!-- learn-from-lessons:start -->` and
`<!-- learn-from-lessons:end -->`; the rest of your file is untouched, and without `--apply` it only prints a diff.

## Privacy

- **Local only.** `scan`, `mine`, `index`, `inject`, `search`, `view` and `mcp` make no network calls.
  `dream` sends redacted transcript excerpts to the agent CLI you already use, which is the same place those
  transcripts came from.
- **Redaction.** API keys (`sk-…`), GitHub/Slack/AWS tokens, JWTs, private keys and `password=`-style values
  are replaced before an excerpt is exported or written into a lesson ([lessons/redact.py](lessons/redact.py)).
  It is pattern-based: review lessons before you share them.
- **Your files.** Lessons are plain Markdown in `~/.lessons` (override with `--dir` or `LESSONS_DIR`). Delete
  the folder and it is gone.

## How it works

```
 Layer 4  INDEX block in CLAUDE.md / AGENTS.md   always loaded · verified only · ≤ 200 lines
              ▲  lessons index / inject
 Layer 3  lesson files (~/.lessons/lessons)      searched on demand: lessons search · MCP · skill
              ▲  lessons dream  (extract → consolidate: merge, rewrite, refute; never just append)
 Layer 2  moments                                corrections, interrupts, frustration, repeated tool errors
              ▲  lessons scan / mine  (regex signals, no model)
 Layer 1  transcripts                            ~/.claude/projects · ~/.codex/sessions · opencode SQLite
                                                 ground truth; every lesson links back here
```

1. **Read** each tool's history into one shape (a session is a list of turns) — `lessons/sources/`.
2. **Find moments** without a model: a user turn that starts with a correction, an interrupt, frustration, or a
   tool failing three times in a row — `lessons/signals.py`. Only sessions with a person in them (not subagents,
   at least 3 user turns) count.
3. **Dream**: batches of moments with their preceding turns go to the model, which proposes lessons citing exact
   refs; lessons citing refs that were not in the batch are dropped, and dates and `seen` come from the transcripts,
   not the model. A second pass clusters similar lessons and merges or refutes them. Evidence is only ever added.
4. **Serve** the result: a small index for every session, search for the rest.

## Commands

| command | what it does |
|---|---|
| `lessons scan` | sessions, human sessions, user turns and moments per source (counts only) |
| `lessons mine --out work/shards` | export moments as redacted JSONL shards, grouped by project |
| `lessons dream` | extract and consolidate lessons (`--dry-run`, `--max-batches`, `--engine`) |
| `lessons index` | write `INDEX.md` in the lessons dir (verified, ≤ 200 lines) |
| `lessons inject --target FILE [--apply]` | put the index into `CLAUDE.md` / `AGENTS.md` between markers |
| `lessons search "<query>" [--cwd]` | BM25 over title, trigger, themes and body; verified and same-project first |
| `lessons view` | static HTML viewer for lessons and their evidence |
| `lessons mcp` | stdio MCP server |

## Roadmap

- Package on PyPI so `uvx learn-from-lessons scan` works directly.
- More sources (Cursor, Aider, Gemini CLI) behind the same `Session` / `Turn` shape.
- Verification by outcome: mark a lesson as holding when a later session meets its trigger without a correction.
- Hooks that run `lessons search` automatically at session start for the current project.
- Shareable, reviewed lesson packs for teams (opt-in, after redaction review).

## Development

```bash
python3 -m unittest discover tests
python3 -m lessons.cli scan
```

MIT licensed. Contributions welcome.
