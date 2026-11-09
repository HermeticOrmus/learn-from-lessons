"""opencode: a SQLite database (session, message, part tables), e.g. ~/.local/share/opencode/opencode.db.
Apps that embed opencode keep their own database (found by searching for opencode.db)."""
from __future__ import annotations
import glob, json, os, sqlite3
from typing import Iterator
from ..model import Session, Turn

DEFAULT = [os.path.expanduser("~/.local/share/opencode/opencode.db")]


def databases() -> list[str]:
    found = [p for p in DEFAULT if os.path.exists(p)]
    for pattern in ("~/Library/Application Support/*/kernel/data/opencode/opencode.db", "~/.local/share/*/opencode/opencode.db"):
        found += glob.glob(os.path.expanduser(pattern))
    return sorted(set(found))


def read_db(path: str) -> Iterator[Session]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("pragma table_info(session)")} if _has(con, "session") else set()
        if "data" in cols:  # older layout: one JSON column
            rows = [(sid, (json.loads(d) if d else {})) for sid, d in con.execute("select id, data from session")]
            rows = [(sid, i.get("directory", ""), i.get("parentID")) for sid, i in rows]
        elif cols:
            rows = con.execute("select id, directory, parent_id from session").fetchall()
        else:
            rows = []
        for sid, directory, parent in rows:
            sess = Session(source="opencode", id=sid, path=path, cwd=directory or "", subagent=bool(parent))
            msgs = con.execute("select id, time_created, data from message where session_id=? order by time_created, id", (sid,)).fetchall()
            for mid, created, mdata in msgs:
                role = (json.loads(mdata) or {}).get("role", "")
                sess.started = sess.started or str(created)
                for (pdata,) in con.execute("select data from part where message_id=? order by id", (mid,)):
                    p = json.loads(pdata)
                    ref = f"{path}#{mid}"
                    if p.get("type") == "text" and p.get("text", "").strip() and not p.get("synthetic"):
                        sess.turns.append(Turn(role, p["text"], str(created), ref=ref))
                    elif p.get("type") == "tool":
                        st = p.get("state") or {}
                        sess.turns.append(Turn("assistant", json.dumps(st.get("input", {}), ensure_ascii=False)[:600], str(created), tool=p.get("tool", ""), ref=ref))
                        out = st.get("output") if st.get("status") == "completed" else st.get("error")
                        if out:
                            sess.turns.append(Turn("tool", str(out)[:2000], str(created), tool=p.get("tool", ""), error=st.get("status") == "error", ref=ref))
            if sess.turns:
                yield sess
    finally:
        con.close()


def _has(con, table: str) -> bool:
    return bool(con.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone())


def sessions() -> Iterator[Session]:
    for db in databases():
        try:
            yield from read_db(db)
        except sqlite3.Error:
            continue
