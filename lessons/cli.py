"""lessons: turn your coding-agent transcripts into verified, evidence-linked lessons.

  lessons scan                      what's on this machine (counts only)
  lessons mine --out work/shards    export correction moments as redacted JSONL
  lessons dream                     extract / consolidate lessons with a model
  lessons index [--scenario S]      write INDEX.md: principles, then verified rules for S (<= 120 lines)
  lessons inject --target CLAUDE.md show the diff; add --apply to write it
  lessons search "<query>" --cwd    find lessons before a task (--context "<text>" infers the scenario)
  lessons tree                      principle -> rule -> instance hierarchy, orphans
  lessons scenarios                 lesson counts per scenario
  lessons view                      browse lessons
  lessons mcp                       stdio MCP server (search_lessons, get_lesson, list_principles, infer_scenario)

  Filters for index / inject / search: --scenario <id> (docs/SCENARIOS.md), --level principle|rule|instance
  (how specific to go; default rule), --strength must|should|prefer (minimum), --all (include candidates).
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

def _check_scenario(sc: str | None) -> None:
    from .scenario import SCENARIOS
    if sc and sc not in SCENARIOS:
        print(f"warning: scenario {sc!r} is not in docs/SCENARIOS.md ({', '.join(SCENARIOS)})", file=sys.stderr)


def _index_kw(a) -> dict:
    _check_scenario(a.scenario)
    return dict(scenario=a.scenario, level=a.level, strength_min=a.strength, include_candidates=a.all,
                tool=a.tool, max_lines=a.max_lines)


def cmd_index(a) -> int:
    from . import index
    kw = _index_kw(a)
    cwd = None if a.all_projects else (a.cwd or os.getcwd())
    if a.print:
        sys.stdout.write(index.build_index(root=a.dir, cwd=cwd, link_base=None, all_projects=a.all_projects, **kw))
        return 0
    out = index.write(root=a.dir, cwd=cwd, out=a.out, all_projects=a.all_projects, **kw)
    print(f"wrote {out}")
    return 0


def cmd_inject(a) -> int:
    from . import inject
    kw = _index_kw(a)
    target = os.path.abspath(a.target)
    try:
        d = inject.inject_index(target, root=a.dir, apply=a.apply, link_base=None, **kw)
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
    from .scenario import infer_scenario
    where = (a.at or os.getcwd()) if (a.cwd or a.at) else None
    scenario, inferred = a.scenario, None
    if not scenario and a.context:
        inferred = infer_scenario(a.context, where)
        scenario = inferred[0][0] if inferred else None
    _check_scenario(scenario)
    hits = store.search(" ".join(a.query), cwd=where, k=a.k, root=a.dir, scenario=scenario, level_max=a.level,
                        verified_only=not a.all, strength_min=a.strength)
    if a.json:
        print(json.dumps({"scenario": scenario, "inferred": inferred,
                          "hits": [{k: v for k, v in h.items() if k != "body"} for h in hits]}
                         if inferred is not None else
                         [{k: v for k, v in h.items() if k != "body"} for h in hits], ensure_ascii=False, indent=2))
        return 0
    if inferred is not None:
        print(f"scenario: {scenario or '(none inferred)'}"
              + (f"  (from --context: {', '.join(f'{s} {v}' for s, v in inferred)})" if inferred else ""),
              file=sys.stderr)
    if not hits:
        print("no matching lessons" + ("" if a.all else " (verified only; --all includes candidates)"))
        return 1
    for h in hits:
        trig = " ".join(str(h.get("trigger", "")).split())
        chain = " < ".join(p["id"] for p in h.get("chain", []))
        print(f"{h['score']:6.2f}  [{h['level']}, {h['strength']}, {h.get('status', '?')}] "
              f"{h.get('title', h.get('id'))}\n"
              f"        when: {trig}\n"
              + (f"        under: {chain}\n" if chain else "")
              + f"        {h['abspath']}")
    return 0


def _mark(d: dict) -> str:
    st = d.get("status", "?")
    tag = "" if st == "verified" else f" ({st})"
    sc = ",".join(d.get("scenarios") or [])
    return f"{d['id']}{tag}  [{d['strength']}; {sc}]"


def cmd_tree(a) -> int:
    ls = store.load(a.dir)
    if a.scenario:
        _check_scenario(a.scenario)
        ls = [d for d in ls if store.scenario_matches(d, a.scenario)]
    if not a.all:
        ls = [d for d in ls if d.get("status") != "refuted"]
    h = store.hierarchy(ls)
    by_id, kids = h["by_id"], h["children"]
    lines: list[str] = []

    def walk(i: str, depth: int, seen: set) -> None:
        if i in seen:
            return
        seen.add(i)
        d = by_id[i]
        lines.append("  " * depth + ("- " if depth else "") + _mark(d))
        if depth == 0 and not a.ids_only:
            lines.append("  " + " ".join(str(d.get("title", "")).split()))
        for c in kids.get(i, []):
            walk(c, depth + 1, seen)

    seen: set = set()
    for p in h["principles"]:
        walk(p, 0, seen)
        lines.append("")
    for label, ids in (("unparented (no parent set)", h["unparented"]),
                       ("orphans (parent id not found)", h["orphans"])):
        ids = [i for i in ids if i not in seen]
        if ids:
            lines.append(f"{label}: {len(ids)}")
            for i in ids:
                walk(i, 1, seen)
            lines.append("")
    if h["misleveled"]:
        lines.append(f"parent not one level up: {', '.join(h['misleveled'])}")
    n = {lv: sum(1 for d in ls if d["level"] == lv) for lv in store.LEVELS}
    lines.append(f"{n['principle']} principles, {n['rule']} rules, {n['instance']} instances; "
                 f"{len(h['orphans'])} orphans, {len(h['unparented'])} unparented")
    print("\n".join(lines))
    return 0


def cmd_scenarios(a) -> int:
    from .scenario import SCENARIOS
    ls = [d for d in store.load(a.dir) if d.get("status") != "refuted"]
    counts: dict[str, Counter] = defaultdict(Counter)
    for d in ls:
        for sc in d.get("scenarios") or ["any"]:
            counts[sc][d["level"]] += 1
            counts[sc]["verified" if d.get("status") == "verified" else "candidate"] += 1
    if a.json:
        print(json.dumps({k: dict(v) for k, v in sorted(counts.items())}, indent=2))
        return 0
    print(f"{'scenario':<22} {'principle':>9} {'rule':>6} {'instance':>8} {'verified':>8} {'candidate':>9}")
    order = [s for s in SCENARIOS if s in counts] + sorted(s for s in counts if s not in SCENARIOS)
    for sc in order:
        c = counts[sc]
        flag = "" if sc in SCENARIOS else "   (not in vocabulary)"
        print(f"{sc:<22} {c['principle']:>9} {c['rule']:>6} {c['instance']:>8} {c['verified']:>8} "
              f"{c['candidate']:>9}{flag}")
    print(f"\n{len(ls)} lessons; a lesson counts once per scenario it lists. Rules for a scenario also include "
          "those listing `any`.")
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

    def filters(s, default_lines: bool = True):
        from .store import LEVELS, STRENGTHS
        s.add_argument("--scenario", default=None, help="only lessons for this scenario (principles always)")
        s.add_argument("--level", choices=LEVELS, default="rule", help="most specific level to include")
        s.add_argument("--strength", choices=STRENGTHS, default=None, help="minimum strength")
        s.add_argument("--all", action="store_true", help="include candidates, not only verified lessons")
        if default_lines:
            s.add_argument("--tool", default=None, help="include tool:<name> instances for this tool")
            s.add_argument("--max-lines", type=int, default=120)

    s = sub.add_parser("index", help="write INDEX.md: principles + verified rules, <= 120 lines")
    s.add_argument("--out", default=None, help="default <lessons dir>/INDEX.md")
    s.add_argument("--cwd", default=None, help="project whose lessons to include (default: current directory)")
    s.add_argument("--all-projects", action="store_true", help="include verified lessons from every project")
    s.add_argument("--print", action="store_true", help="print instead of writing")
    filters(s)
    s.add_argument("--dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("inject", help="put the index into CLAUDE.md / AGENTS.md between markers")
    s.add_argument("--target", required=True, help="CLAUDE.md, AGENTS.md, or any path")
    s.add_argument("--apply", action="store_true", help="write the file (default: print a diff)")
    filters(s)
    s.add_argument("--dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_inject)

    s = sub.add_parser("search", help="rank lessons for a query")
    s.add_argument("query", nargs="+")
    s.add_argument("--cwd", action="store_true", help="scope to the current directory's project")
    s.add_argument("--at", default=None, help="scope to this directory's project")
    s.add_argument("-k", type=int, default=8)
    s.add_argument("--json", action="store_true")
    s.add_argument("--context", default=None, help="text of the current task; infers --scenario when not given")
    filters(s, default_lines=False)
    s.add_argument("--dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("tree", help="print the principle -> rule -> instance hierarchy")
    s.add_argument("--scenario", default=None)
    s.add_argument("--all", action="store_true", help="include refuted lessons")
    s.add_argument("--ids-only", action="store_true", help="omit principle titles")
    s.add_argument("--dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_tree)

    s = sub.add_parser("scenarios", help="count lessons per scenario and level")
    s.add_argument("--json", action="store_true")
    s.add_argument("--dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_scenarios)

    s = sub.add_parser("view", help="browse lessons", add_help=False)
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_view)

    s = sub.add_parser("mcp", help="run the stdio MCP server")
    s.add_argument("--dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
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
