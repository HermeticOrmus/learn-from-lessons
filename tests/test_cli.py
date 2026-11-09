import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lessons import cli, index, inject, mcp_server, store  # noqa: E402

LESSON = """---
id: {id}
title: {title}
scope: {scope}
kind: {kind}
trigger: {trigger}
status: {status}
themes: [{themes}]
evidence:
  - ref: /x/session.jsonl:12
    source: claude-code
    date: 2026-10-01
    quote: "no, {quote}"
seen: {seen}
first_seen: 2026-10-01
last_seen: 2026-10-20
---

**Do:** {body}

**Why:** the person corrected it.
"""


def write_lesson(root, scope_dir, **kw):
    d = dict(id="x", title="T", scope="global", kind="pitfall", trigger="t", status="verified",
             themes="testing", quote="q", seen=2, body="b")
    d.update(kw)
    os.makedirs(os.path.join(root, scope_dir), exist_ok=True)
    p = os.path.join(root, scope_dir, d["id"] + ".md")
    with open(p, "w") as fh:
        fh.write(LESSON.format(**d))
    return p


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "lessons")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(os.path.join(self.proj, "sub"))
        write_lesson(self.root, "global", id="verify-numbers", title="Verify numbers against the source table",
                     trigger="reporting statistics from a paper", themes="numbers, papers",
                     body="Re-read the table before quoting any number.", seen=3)
        write_lesson(self.root, "global", id="run-tests-first", title="Run the test suite before saying done",
                     kind="workflow", trigger="finishing a code change", status="candidate", themes="tests",
                     body="Run pytest and paste the result.")
        write_lesson(self.root, "project-proj", id="never-git-root", title="Never run git at the workspace root",
                     scope=f"project:{self.proj}", kind="fact", trigger="about to run git in the root folder",
                     themes="git", body="The root is not a repository.")
        write_lesson(self.root, "global", id="old-idea", title="Use tabs for git commits", status="refuted",
                     themes="git", body="Refuted.")

    def tearDown(self):
        self.tmp.cleanup()


class StoreTest(Fixture):
    def test_load(self):
        ls = store.load(self.root)
        self.assertEqual(len(ls), 4)
        v = next(d for d in ls if d["id"] == "verify-numbers")
        self.assertEqual(v["status"], "verified")
        self.assertEqual(v["seen"], 3)
        self.assertEqual(v["evidence"][0]["source"], "claude-code")
        self.assertIn("Re-read the table", v["body"])
        self.assertEqual(v["path"], os.path.join("global", "verify-numbers.md"))

    def test_min_parser_matches(self):
        with open(os.path.join(self.root, "global", "verify-numbers.md")) as fh:
            meta, body = store._parse_min(fh.read())
        self.assertEqual(meta["themes"], ["numbers", "papers"])
        self.assertEqual(meta["evidence"][0]["quote"], "no, q")
        self.assertIn("**Do:**", body)

    def test_search_ranks_and_scopes(self):
        hits = store.search("verify the numbers in a paper", root=self.root)
        self.assertEqual(hits[0]["id"], "verify-numbers")
        # project lessons: hidden from another directory, shown (and boosted) inside the project
        self.assertFalse([h for h in store.search("git", scope=self.tmp.name, root=self.root)])
        inside = store.search("git", scope=os.path.join(self.proj, "sub"), root=self.root)
        self.assertEqual([h["id"] for h in inside], ["never-git-root"])  # refuted one is excluded
        self.assertEqual(store.search("zzzz", root=self.root), [])

    def test_get(self):
        self.assertEqual(store.get("run-tests-first", self.root)["kind"], "workflow")
        self.assertIsNone(store.get("nope", self.root))


class IndexTest(Fixture):
    def test_verified_only_and_scoped(self):
        out = index.build(root=self.root, cwd=self.tmp.name)
        self.assertIn("Verify numbers against the source table", out)
        self.assertIn("— reporting statistics from a paper", out)
        self.assertNotIn("Run the test suite", out)   # candidate
        self.assertNotIn("Never run git", out)        # other project
        self.assertNotIn("tabs", out)                 # refuted
        here = index.build(root=self.root, cwd=self.proj)
        self.assertIn("Never run git at the workspace root", here)
        self.assertLess(here.index("Project"), here.index("## Global"))

    def test_line_budget(self):
        for i in range(300):
            write_lesson(self.root, "global", id=f"l{i:03d}", title=f"Lesson {i}", kind=["pitfall", "fact"][i % 2])
        out = index.build(root=self.root, cwd=None)
        self.assertLessEqual(len(out.splitlines()), index.MAX_LINES)
        self.assertIn("omitted", out)

    def test_write_relative_links(self):
        p = index.write(root=self.root, cwd=self.tmp.name)
        with open(p) as fh:
            self.assertIn("](global/verify-numbers.md)", fh.read())
        self.assertNotIn("INDEX", [d["id"] for d in store.load(self.root)])


class InjectTest(unittest.TestCase):
    def test_splice_preserves_outside(self):
        old = "# Mine\n\nkeep me\n"
        new = inject.splice(old, "A")
        self.assertTrue(new.startswith(old))
        self.assertIn(inject.START + "\nA\n" + inject.END, new)
        again = inject.splice(new + "after\n", "B")
        self.assertTrue(again.startswith(old))
        self.assertTrue(again.endswith(inject.END + "\nafter\n"))
        self.assertNotIn("\nA\n", again)
        self.assertEqual(inject.splice(again, "B"), again)

    def test_one_marker_refuses(self):
        with self.assertRaises(ValueError):
            inject.splice("x\n" + inject.START + "\n", "A")

    def test_dry_run_then_apply(self):
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "CLAUDE.md")
            with open(t, "w") as fh:
                fh.write("hello\n")
            diff = inject.inject(t, "body")
            self.assertIn("+" + inject.START, diff)
            with open(t) as fh:
                self.assertEqual(fh.read(), "hello\n")   # dry run wrote nothing
            inject.inject(t, "body", apply=True)
            with open(t) as fh:
                self.assertIn("body", fh.read())
            self.assertEqual(inject.inject(t, "body"), "")


class CliTest(Fixture):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(args))
        return rc, buf.getvalue()

    def test_search_json(self):
        rc, out = self.run_cli("--dir", self.root, "search", "tests", "before", "done", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)[0]["id"], "run-tests-first")

    def test_inject_cli(self):
        t = os.path.join(self.proj, "AGENTS.md")
        rc, out = self.run_cli("--dir", self.root, "inject", "--target", t)
        self.assertEqual(rc, 0)
        self.assertIn("Never run git at the workspace root", out)
        self.assertFalse(os.path.exists(t))
        self.run_cli("--dir", self.root, "inject", "--target", t, "--apply")
        with open(t) as fh:
            self.assertIn(inject.END, fh.read())

    def test_mcp_call_in_process(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "get_lesson", "arguments": {"id": "verify-numbers"}}}, self.root)
        self.assertFalse(r["result"]["isError"])
        self.assertIn("Re-read the table", r["result"]["content"][0]["text"])
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                               "params": {"name": "nope", "arguments": {}}}, self.root)
        self.assertEqual(r["error"]["code"], -32602)


class McpSubprocessTest(Fixture):
    def test_round_trip(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_lessons", "arguments": {"query": "numbers paper"}}},
        ]
        env = dict(os.environ, PYTHONPATH=ROOT)
        p = subprocess.run([sys.executable, "-m", "lessons.cli", "--dir", self.root, "mcp"],
                           input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True,
                           text=True, timeout=30, env=env, cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual([o["id"] for o in out], [1, 2, 3])   # the notification gets no reply
        init = out[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        self.assertEqual(init["serverInfo"]["name"], "learn-from-lessons")
        self.assertEqual({t["name"] for t in out[1]["result"]["tools"]}, {"search_lessons", "get_lesson"})
        self.assertIn("verify-numbers", out[2]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
