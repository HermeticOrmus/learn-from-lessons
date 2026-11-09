"""A minimal MCP server over stdio (newline-delimited JSON-RPC 2.0), no SDK.
Tools: search_lessons(query, cwd?) and get_lesson(id). Spec: https://modelcontextprotocol.io"""
from __future__ import annotations
import json, os, sys
from . import __version__, store

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "search_lessons",
        "description": ("Search lessons learned from this person's past coding-agent sessions (corrections, pitfalls, "
                        "preferences). Call before risky, recurring or unfamiliar tasks. Verified lessons rank first."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you are about to do, in a few words"},
                "cwd": {"type": "string", "description": "Working directory, to include that project's lessons"},
                "k": {"type": "integer", "description": "Max results (default 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_lesson",
        "description": "Full text of one lesson (frontmatter + body + evidence) by id, as returned by search_lessons.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
]


def _fmt_hit(d: dict) -> str:
    return (f"- {d.get('id')} [{d.get('status', '?')}, {d.get('kind', '?')}, {d.get('scope', '?')}] "
            f"{d.get('title', '')}\n  when: {' '.join(str(d.get('trigger', '')).split())}")


def call_tool(name: str, args: dict, root: str | None = None) -> tuple[str, bool]:
    if name == "search_lessons":
        q = str(args.get("query", ""))
        hits = store.search(q, scope=args.get("cwd") or None, k=int(args.get("k") or 8), root=root)
        if not hits:
            return f"No lessons match {q!r}.", False
        return "\n".join(_fmt_hit(d) for d in hits) + "\n\nUse get_lesson(id) for the full lesson.", False
    if name == "get_lesson":
        d = store.get(str(args.get("id", "")), root=root)
        if not d:
            return f"No lesson with id {args.get('id')!r}.", True
        with open(d["abspath"], encoding="utf-8", errors="replace") as fh:
            return fh.read(), False
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
            "instructions": "Lessons from the user's past agent sessions. search_lessons before risky or recurring tasks.",
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
