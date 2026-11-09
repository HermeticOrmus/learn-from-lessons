"""A minimal MCP server over stdio (newline-delimited JSON-RPC 2.0), no SDK.
Tools: search_lessons, get_lesson, list_principles, infer_scenario. Spec: https://modelcontextprotocol.io"""
from __future__ import annotations
import json, os, sys
from . import __version__, store
from .scenario import SCENARIOS, infer_scenario

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "search_lessons",
        "description": ("Search lessons learned from this person's past agent sessions (corrections, pitfalls, "
                        "preferences). Call with the current scenario before a risky or recurring step: pushing, "
                        "spending credit, writing paper prose, reporting results. Lessons are predictions to follow "
                        "unless the person says otherwise. Verified lessons only, unless include_candidates."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you are about to do, in a few words"},
                "scenario": {"type": "string", "enum": SCENARIOS,
                             "description": "Current scenario (see infer_scenario); principles always match"},
                "level": {"type": "string", "enum": store.LEVELS,
                          "description": "Most specific level to return (default rule; instance adds project facts)"},
                "cwd": {"type": "string", "description": "Working directory, to include that project's lessons"},
                "k": {"type": "integer", "description": "Max results (default 8)"},
                "include_candidates": {"type": "boolean", "description": "Also return unverified lessons"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_lesson",
        "description": ("Full text of one lesson (frontmatter + body + evidence) by id, with its parent chain "
                        "(rule -> principle) and children."),
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "list_principles",
        "description": "The few general principles (always apply), each with how many rules support it.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "infer_scenario",
        "description": "Guess the current scenario(s) from task text (English or Chinese), ranked.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "cwd": {"type": "string"}},
                        "required": ["text"]},
    },
]


def _fmt_hit(d: dict) -> str:
    chain = " < ".join(p["id"] for p in d.get("chain") or [])
    return (f"- {d.get('id')} [{d.get('level', 'rule')}, {d.get('strength', 'should')}, {d.get('status', '?')}, "
            f"{d.get('scope', '?')}] {d.get('title', '')}\n  when: {' '.join(str(d.get('trigger', '')).split())}"
            + (f"\n  under: {chain}" if chain else ""))


def call_tool(name: str, args: dict, root: str | None = None) -> tuple[str, bool]:
    if name == "search_lessons":
        q = str(args.get("query", ""))
        sc = args.get("scenario") or None
        hits = store.search(q, cwd=args.get("cwd") or None, k=int(args.get("k") or 8), root=root, scenario=sc,
                            level_max=args.get("level") or "rule",
                            verified_only=not bool(args.get("include_candidates")))
        if not hits:
            return f"No lessons match {q!r}" + (f" for scenario {sc}." if sc else "."), False
        return "\n".join(_fmt_hit(d) for d in hits) + "\n\nUse get_lesson(id) for the full lesson.", False
    if name == "get_lesson":
        ls = store.load(root)
        want = str(args.get("id", ""))
        d = next((x for x in ls if x["id"] == want or x["path"] == want or x["path"][:-3] == want), None)
        if not d:
            return f"No lesson with id {want!r}.", True
        h = store.hierarchy(ls)
        with open(d["abspath"], encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        chain = store.parent_chain(d, h["by_id"])
        kids = [h["by_id"][c] for c in h["children"].get(d["id"], [])]
        extra = []
        if chain:
            extra.append("Parent chain (nearest first):")
            extra += [f"- {p['id']} [{p['level']}] {p.get('title', '')}" for p in chain]
        elif d["parent"]:
            extra.append(f"Parent {d['parent']!r} not found (orphan).")
        if kids:
            extra.append("Children:")
            extra += [f"- {c['id']} [{c['level']}, {c.get('status', '?')}] {c.get('title', '')}" for c in kids]
        return text.rstrip() + ("\n\n---\n" + "\n".join(extra) if extra else "") + "\n", False
    if name == "list_principles":
        ls = [d for d in store.load(root) if d.get("status") != "refuted"]
        h = store.hierarchy(ls)
        ps = [h["by_id"][i] for i in h["principles"]]
        if not ps:
            return "No principles yet (all lessons are rules; v0 library).", False
        return "\n".join(f"- {p['id']}: {p.get('title', '')} ({len(h['children'].get(p['id'], []))} rules)"
                         for p in ps), False
    if name == "infer_scenario":
        r = infer_scenario(str(args.get("text", "")), args.get("cwd") or None)
        return json.dumps({"scenarios": [{"scenario": s, "score": v} for s, v in r],
                           "best": r[0][0] if r else None}), False
    raise KeyError(name)


def handle(msg: dict, root: str | None = None) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification, e.g. notifications/initialized
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({
            "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "learn-from-lessons", "version": __version__},
            "instructions": ("Lessons from the user's past agent sessions. Load list_principles once; call search_lessons "
                             "with the current scenario before risky or recurring steps."),
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        try:
            text, is_error = call_tool(params.get("name", ""), params.get("arguments") or {}, root)
        except KeyError:
            return err(-32602, f"Unknown tool: {params.get('name')}")
        except Exception as e:  # report tool failures to the model, not as protocol errors
            text, is_error = f"error: {e}", True
        return ok({"content": [{"type": "text", "text": text}], "isError": is_error})
    return err(-32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None, root: str | None = None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            msgs = msg if isinstance(msg, list) else [msg]
            out = [r for r in (handle(m, root) for m in msgs if isinstance(m, dict)) if r]
            resp = (out if isinstance(msg, list) else (out[0] if out else None))
        if resp:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv=None) -> None:
    serve(root=os.environ.get("LESSONS_DIR") or None)


if __name__ == "__main__":
    main()
