"""lessons: turn your coding-agent transcripts into verified, evidence-linked lessons.

  lessons scan                      what's on this machine (counts only)
  lessons mine --out work/shards    export correction moments as redacted JSONL
  lessons dream                     extract / consolidate lessons with a model
  lessons index                     write INDEX.md (verified lessons, <= 200 lines)
  lessons inject --target CLAUDE.md show the diff; add --apply to write it
  lessons search "<query>" --cwd    find lessons before a task
  lessons view                      browse lessons
  lessons mcp                       stdio MCP server (search_lessons, get_lesson)
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import Counter, defaultdict

from . import __version__, store

HUMAN_MIN_TURNS = 3
LIMITS = {"user": 1500, "assistant": 600, "tool": 1200}


def _sources(only: list[str] | None = None):
    from .sources import SOURCES
    return {k: v for k, v in SOURCES.items() if not only or k in only}


def _project(cwd: str, cache: dict) -> str:
    """The git root above cwd (so subdirectories share a project), else cwd itself."""
    if not cwd:
        return ""
    if cwd not in cache:
        p = cwd
        while True:
            if os.path.exists(os.path.join(p, ".git")):
                cache[cwd] = p
                break
            parent = os.path.dirname(p)
            if parent == p:
                cache[cwd] = cwd
                break
            p = parent
    return cache[cwd]


def _is_human(s) -> bool:
    return not s.subagent and len(s.user_turns) >= HUMAN_MIN_TURNS


# ---------------------------------------------------------------- scan

def cmd_scan(a) -> int:
    from . import signals
    rows, total = {}, Counter()
    for name, sessions in _sources(a.source).items():
        c, kinds = Counter(), Counter()
        try:
            for s in sessions():
                c["sessions"] += 1
                if s.subagent:
                    c["subagent"] += 1
                    continue
                c["user turns"] += len(s.user_turns)
                if _is_human(s):
                    c["human sessions"] += 1
                    for m in signals.find(s):
                        kinds[m.kind] += 1
        except Exception as e:  # one broken source should not hide the others
            print(f"  {name}: error reading ({type(e).__name__}: {e})", file=sys.stderr)
        rows[name] = (c, kinds)
        total.update(c)
        total.update({"moments:" + k: v for k, v in kinds.items()})
    if a.json:
        print(json.dumps({n: {**c, "moments": dict(k)} for n, (c, k) in rows.items()}, indent=2))
        return 0
    print(f"{'source':<12} {'sessions':>9} {'subagent':>9} {'human':>7} {'user turns':>11}   moments")
    for name, (c, kinds) in rows.items():
        km = ", ".join(f"{k} {v}" for k, v in kinds.most_common()) or "-"
        print(f"{name:<12} {c['sessions']:>9} {c['subagent']:>9} {c['human sessions']:>7} {c['user turns']:>11}   {km}")
    mom = sum(v for k, v in total.items() if k.startswith("moments:"))
    print(f"\nhuman session = not a subagent and >= {HUMAN_MIN_TURNS} user turns. {mom} moments to learn from.")
    if mom:
        print("Next: `lessons dream` to turn them into lessons (or `lessons mine --out work/shards` to export).")
    return 0


# ---------------------------------------------------------------- mine

def _turn(t, limit: int | None = None) -> dict:
    from .redact import redact
    n = limit or LIMITS.get(t.role, 1200)
    return {"role": t.role, "tool": t.tool, "error": t.error, "ts": t.ts, "ref": t.ref, "text": redact(t.text[:n])}


def moments(only: list[str] | None = None):
    """Every moment from human sessions, as the JSON records the shards hold."""
    from . import signals
    cache: dict = {}
    seen: set = set()  # turns already found in another (forked or resumed) session
    for name, sessions in _sources(only).items():
        for s in sessions():
            if not _is_human(s):
                continue
            for m in signals.find(s, seen):
                yield {"kind": m.kind, "source": s.source, "project": _project(s.cwd, cache), "cwd": s.cwd,
                       "session": s.id, "trigger": _turn(m.turn, 2000), "context": [_turn(t) for t in m.context[:-1]],
                       "followup": [_turn(t, 1200) for t in m.followup]}


def cmd_mine(a) -> int:
    recs = list(moments(a.source))
    by_proj: dict[str, list] = defaultdict(list)
    for r in recs:
        by_proj[r["project"]].append(r)
    # Pack whole projects into shards of about --size moments, so one worker sees one project's history.
    shards: list[list] = [[]]
    for _, rs in sorted(by_proj.items(), key=lambda kv: -len(kv[1])):
        for i in range(0, len(rs), a.size):
            chunk = rs[i:i + a.size]
            if shards[-1] and len(shards[-1]) + len(chunk) > a.size:
                shards.append([])
            shards[-1].extend(chunk)
    os.makedirs(a.out, exist_ok=True)
    n = 0
    for i, sh in enumerate(s for s in shards if s):
        with open(os.path.join(a.out, f"shard-{i:02d}.jsonl"), "w", encoding="utf-8") as fh:
            for r in sh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        n += 1
    kinds = Counter(r["kind"] for r in recs)
    print(f"{len(recs)} moments from {len(by_proj)} projects -> {n} shards in {a.out}  "
          f"({', '.join(f'{k} {v}' for k, v in kinds.most_common())})")
    return 0


# ---------------------------------------------------------------- delegated

def _delegate(module: str, argv: list[str], what: str) -> int:
    import importlib
    try:
        mod = importlib.import_module(f"lessons.{module}")
    except ImportError as e:
        if f"lessons.{module}" not in str(e) and module not in str(e):
            raise
        print(f"`lessons {module}` is not installed in this build ({what}).", file=sys.stderr)
        return 2
    rc = mod.main(argv)
    return rc if isinstance(rc, int) else 0


def cmd_dream(a) -> int:
    return _delegate("dream", a.rest, "model-driven extraction and consolidation")


def cmd_view(a) -> int:
    rest = list(a.rest)
    if not rest or rest[0].startswith("-"):
        rest.insert(0, a.dir or store.DEFAULT_DIR)  # view takes the lessons dir first
    return _delegate("view", rest, "the lesson browser")


# ---------------------------------------------------------------- index / inject / search / mcp

def cmd_index(a) -> int:
    from . import index
    cwd = None if a.all_projects else (a.cwd or os.getcwd())
    if a.print:
        sys.stdout.write(index.build(root=a.dir, cwd=cwd, link_base=None))
        return 0
    out = index.write(root=a.dir, cwd=cwd, out=a.out)
    print(f"wrote {out}")
    return 0


def cmd_inject(a) -> int:
    from . import index, inject
    target = os.path.abspath(a.target)
    body = index.build(root=a.dir, cwd=os.path.dirname(target), link_base=None)
    try:
        d = inject.inject(target, body, apply=a.apply)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not d:
        print(f"{a.target}: already up to date")
    elif a.apply:
        print(f"updated {a.target}")
    else:
        sys.stdout.write(d)
        print(f"\n(dry run: re-run with --apply to write {a.target})", file=sys.stderr)
    return 0


def cmd_search(a) -> int:
    scope = (a.at or os.getcwd()) if (a.cwd or a.at) else None
    hits = store.search(" ".join(a.query), scope=scope, k=a.k, root=a.dir)
    if a.json:
        print(json.dumps([{k: v for k, v in h.items() if k != "body"} for h in hits], ensure_ascii=False, indent=2))
        return 0
    if not hits:
        print("no matching lessons")
        return 1
    for h in hits:
        trig = " ".join(str(h.get("trigger", "")).split())
        print(f"{h['score']:6.2f}  [{h.get('status', '?')}] {h.get('title', h.get('id'))}\n"
              f"        when: {trig}\n        {h['abspath']}")
    return 0


def cmd_mcp(a) -> int:
    from . import mcp_server
    mcp_server.serve(root=a.dir)
    return 0


# ---------------------------------------------------------------- main

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lessons", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"learn-from-lessons {__version__}")
    p.add_argument("--dir", default=None, help=f"lessons directory (default {store.DEFAULT_DIR}; env LESSONS_DIR)")
    sub = p.add_subparsers(dest="cmd", metavar="command")
    src = ["claude-code", "codex", "opencode"]

    s = sub.add_parser("scan", help="count sessions, turns and moments per source")
    s.add_argument("--source", action="append", choices=src)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("mine", help="export moments as redacted JSONL shards")
    s.add_argument("--out", default="work/shards")
    s.add_argument("--size", type=int, default=100, help="moments per shard (approx.)")
    s.add_argument("--source", action="append", choices=src)
    s.set_defaults(fn=cmd_mine)

    s = sub.add_parser("dream", help="extract and consolidate lessons (model-driven)", add_help=False)
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_dream)

    s = sub.add_parser("index", help="write INDEX.md: verified lessons, <= 200 lines")
    s.add_argument("--out", default=None, help="default <lessons dir>/INDEX.md")
    s.add_argument("--cwd", default=None, help="project whose lessons to include (default: current directory)")
    s.add_argument("--all-projects", action="store_true", help="include verified lessons from every project")
    s.add_argument("--print", action="store_true", help="print instead of writing")
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("inject", help="put the index into CLAUDE.md / AGENTS.md between markers")
    s.add_argument("--target", required=True, help="CLAUDE.md, AGENTS.md, or any path")
    s.add_argument("--apply", action="store_true", help="write the file (default: print a diff)")
    s.set_defaults(fn=cmd_inject)

    s = sub.add_parser("search", help="rank lessons for a query")
    s.add_argument("query", nargs="+")
    s.add_argument("--cwd", action="store_true", help="scope to the current directory's project")
    s.add_argument("--at", default=None, help="scope to this directory's project")
    s.add_argument("-k", type=int, default=8)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("view", help="browse lessons", add_help=False)
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_view)

    s = sub.add_parser("mcp", help="run the stdio MCP server")
    s.set_defaults(fn=cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # dream/view own their flags (including --help): hand everything after the word over untouched.
    rest = None
    for i, w in enumerate(argv):
        if w in ("dream", "view") and (i == 0 or argv[i - 1] != "--dir"):
            argv, rest = argv[:i + 1], argv[i + 1:]
            break
        if not w.startswith("-") and (i == 0 or argv[i - 1] != "--dir"):
            break  # some other subcommand
    a = p.parse_args(argv)
    if rest is not None:
        a.rest = rest
    if not getattr(a, "fn", None):
        p.print_help()
        return 0
    if a.dir:
        a.dir = os.path.expanduser(a.dir)
    try:
        return a.fn(a) or 0
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
