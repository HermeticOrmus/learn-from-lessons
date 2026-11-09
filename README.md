<p align="center">
  <img src="docs/assets/logo.svg" alt="Learn from Lessons" width="460">
  <br>
  <b>Every time you correct your coding agent, it should learn. This makes it.</b>
  <br><br>
  <a href="#install">Install</a> •
  <a href="#usage">Usage</a> •
  <a href="#how-it-works">How it works</a> •
  <a href="docs/GUIDE.md">Guide</a> •
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <img src="docs/assets/idea.svg" alt="transcripts → corrections → lessons → agents" width="760">
</p>

You tell Claude Code "don't push yet", "I asked why, not to change it", "that number isn't from the table" —
and next session it does it again. **Learn from Lessons** reads the transcripts already on your machine (Claude
Code, Codex, opencode), finds the moments you corrected the agent, and turns them into short lessons with the
exact quote they came from. Your agents load them next time.

<p align="center">
  <img src="docs/assets/demo.gif" alt="lessons scan, then browsing lessons by scenario and as a graph" width="820">
</p>

## Install

```bash
pipx install git+https://github.com/assassin808/learn-from-lessons
```

Python 3.10+, no other dependencies. Everything runs locally.

## Usage

```bash
lessons scan          # what's on this machine: sessions, corrections
lessons dream         # turn corrections into lessons (uses your own claude / codex CLI)
lessons view          # browse them: list, graph, evidence
lessons inject --target CLAUDE.md --apply   # let your agent load them
```

Writing a paper doesn't need your git-push lessons: filter by scenario.

```bash
lessons index --scenario writing-paper
lessons search "about to launch the eval runs" --context "$(git log -1)"
```

Also ships as a [Claude Code skill](skill/SKILL.md), an [AGENTS.md block](integrations/AGENTS.md) for Codex and
opencode, and an MCP server (`lessons mcp`).

## How it works

A lesson is a small prediction: *when this situation comes up, doing X avoids a correction.* Lessons sit at
three levels — a **principle** ("ask where the person holds the decision"), the **rules** under it ("push only
after an explicit go-ahead"), and narrow **cases** ("in this repo, Overleaf is the source of truth"). Each one
cites the conversations it came from and is marked verified once it has been seen twice.

<p align="center"><img src="docs/assets/graph.png" alt="Lesson graph" width="720"></p>

The design follows Anthropic's [*The Components of Agent Memory*](https://www.anthropic.com/research/components-of-agent-memory):
transcripts are the ground truth, lessons are checked against them, and only a small index is loaded by default.
Details: [docs/GUIDE.md](docs/GUIDE.md) · [docs/LESSON_FORMAT.md](docs/LESSON_FORMAT.md).

## Does it work?

On 47 new situations — different projects from the ones the lessons came from — an agent followed the person's
rule 85% of the time without lessons and 98–100% with them, without becoming over-cautious elsewhere.
One person's data so far; the test is in [`eval/`](eval/).

## Contributing

Looking for collaborators: readers for more agents (Cursor, Aider, Gemini CLI, Cline), people willing to run the
evaluation on their own history, and researchers interested in agent memory. Open an issue or a discussion.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Citation

```bibtex
@software{learn_from_lessons_2026,
  author = {{learn-from-lessons contributors}},
  title  = {Learn from Lessons: Turning Coding-Agent Corrections into Evidence-Linked Lessons},
  year   = {2026},
  url    = {https://github.com/assassin808/learn-from-lessons}
}
```

Built on ideas from Anthropic, *The Components of Agent Memory* (2026). MIT License.
