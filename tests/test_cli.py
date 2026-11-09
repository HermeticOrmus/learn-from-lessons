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

from lessons import cli, index, inject, mcp_server, scenario, store  # noqa: E402

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
        rc, out = self.run_cli("--dir", self.root, "search", "tests", "before", "done", "--json", "--all")
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
        self.assertEqual({t["name"] for t in out[1]["result"]["tools"]},
                         {"search_lessons", "get_lesson", "list_principles", "infer_scenario"})
        self.assertIn("verify-numbers", out[2]["result"]["content"][0]["text"])


# ---------------------------------------------------------------- v1: levels, scenarios, strength

V1 = """---
id: {id}
title: {title}
level: {level}
parent: {parent}
scope: {scope}
kind: workflow
scenarios: [{scenarios}]
strength: {strength}
trigger: {trigger}
status: {status}
seen: {seen}
themes: [{themes}]
---

**Do:** {body}
"""


def write_v1(root, scope_dir, **kw):
    d = dict(id="x", title="T", level="rule", parent="", scope="global", scenarios="any", strength="should",
             trigger="t", status="verified", seen=2, themes="x", body="b")
    d.update(kw)
    os.makedirs(os.path.join(root, scope_dir), exist_ok=True)
    with open(os.path.join(root, scope_dir, d["id"] + ".md"), "w") as fh:
        fh.write(V1.format(**d))


class V1Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "lessons")
        self.proj = os.path.join(self.tmp.name, "paperproj")
        os.makedirs(self.proj)
        w = lambda **kw: write_v1(self.root, kw.pop("dir", "global"), **kw)  # noqa: E731
        w(id="ask-first", level="principle", title="Ask before acting where the person holds the decision",
          scenarios="any", strength="must", status="verified", themes="ask, decision", seen=0)
        w(id="evidence-first", level="principle", title="Claims need shown evidence", scenarios="any",
          status="verified", themes="evidence", seen=0)
        w(id="no-push-without-go-ahead", parent="ask-first", title="Push or open a PR only after a go-ahead",
          scenarios="coding, release-deploy", strength="must", trigger="about to git push", themes="git, push",
          body="Wait for an explicit push.", seen=5)
        w(id="ask-before-spending", parent="ask-first", title="State the cost of a paid run and wait",
          scenarios="cost-resources, experiments", trigger="about to spend API credit", themes="cost, credit")
        w(id="paper-numbers-from-tables", parent="evidence-first", title="Quote paper numbers from the table",
          scenarios="writing-paper, reporting", trigger="writing results prose", themes="paper, numbers")
        w(id="prefer-short-captions", parent="evidence-first", title="Prefer short captions",
          scenarios="writing-paper", strength="prefer", status="candidate", themes="caption, paper")
        w(id="overleaf-pull-first", level="instance", parent="no-push-without-go-ahead", dir="project-paper",
          scope=f"project:{self.proj}", title="In the paper repo, pull from Overleaf before every edit",
          scenarios="writing-paper, release-deploy", trigger="editing the paper repo", themes="overleaf, git, push")
        w(id="lost-child", parent="does-not-exist", title="A rule whose parent is gone", themes="orphan")
        # a v0 lesson (no level / scenarios / strength) must still work
        write_lesson(self.root, "global", id="v0-verify", title="Verify numbers against the source table",
                     trigger="reporting statistics", themes="numbers, push", body="Re-read the table.")

    def tearDown(self):
        self.tmp.cleanup()

    def ids(self, hits):
        return [h["id"] for h in hits]


class V1StoreTest(V1Fixture):
    def test_v0_compat(self):
        d = store.get("v0-verify", self.root)
        self.assertEqual((d["level"], d["scenarios"], d["strength"], d["version"]), ("rule", ["any"], "should", 0))
        self.assertEqual(store.get("ask-first", self.root)["version"], 1)
        # a v0 rule (scenarios any) shows under every scenario
        self.assertIn("v0-verify", self.ids(store.search("numbers", scenario="writing-paper", root=self.root)))

    def test_hierarchy(self):
        h = store.hierarchy(store.load(self.root))
        self.assertEqual(h["principles"], ["ask-first", "evidence-first"])
        self.assertEqual(h["children"]["ask-first"], ["ask-before-spending", "no-push-without-go-ahead"])
        self.assertEqual(h["children"]["no-push-without-go-ahead"], ["overleaf-pull-first"])
        self.assertEqual(h["orphans"], ["lost-child"])
        self.assertIn("v0-verify", h["unparented"])
        self.assertEqual(h["misleveled"], [])
        chain = store.parent_chain(h["by_id"]["overleaf-pull-first"], h["by_id"])
        self.assertEqual([p["id"] for p in chain], ["no-push-without-go-ahead", "ask-first"])

    def test_scenario_filter(self):
        paper = self.ids(store.search("git push branch", scenario="writing-paper", root=self.root))
        self.assertNotIn("no-push-without-go-ahead", paper)   # a git-push rule is not a writing-paper lesson
        dep = store.search("git push branch", scenario="release-deploy", root=self.root)
        self.assertEqual(dep[0]["id"], "no-push-without-go-ahead")
        self.assertEqual([p["id"] for p in dep[0]["chain"]], ["ask-first"])
        # principles are always eligible, whatever the scenario
        self.assertIn("ask-first", self.ids(store.search("ask decision", scenario="writing-paper", root=self.root)))

    def test_level_cap_verified_strength(self):
        q = "overleaf git push"
        self.assertNotIn("overleaf-pull-first", self.ids(store.search(q, root=self.root)))  # default cap: rule
        inst = store.search(q, level_max="instance", cwd=self.proj, root=self.root)
        self.assertIn("overleaf-pull-first", self.ids(inst))
        hit = next(h for h in inst if h["id"] == "overleaf-pull-first")
        self.assertEqual([p["id"] for p in hit["chain"]], ["no-push-without-go-ahead", "ask-first"])
        only_p = self.ids(store.search("", level_max="principle", root=self.root))
        self.assertEqual(only_p, ["ask-first", "evidence-first"])
        self.assertNotIn("prefer-short-captions", self.ids(store.search("caption", root=self.root)))  # candidate
        self.assertIn("prefer-short-captions", self.ids(store.search("caption", root=self.root, verified_only=False)))
        must = self.ids(store.search("", strength_min="must", root=self.root, k=50))
        self.assertEqual(sorted(must), ["ask-first", "no-push-without-go-ahead"])


class V1IndexTest(V1Fixture):
    def test_paper_index(self):
        out = index.build_index(root=self.root, scenario="writing-paper", cwd=self.tmp.name)
        self.assertLess(out.index("## Principles"), out.index("## Rules"))
        self.assertIn("Quote paper numbers from the table", out)
        self.assertNotIn("Push or open a PR", out)        # coding/release-deploy rule
        self.assertNotIn("State the cost", out)           # cost-resources rule
        self.assertNotIn("Prefer short captions", out)    # candidate
        self.assertNotIn("Overleaf", out)                 # instance: level cap
        self.assertIn("~", out.splitlines()[-1])
        self.assertTrue(out.splitlines()[-1].startswith("<!-- lessons index v1"))
        self.assertEqual(out, index.build_index(root=self.root, scenario="writing-paper", cwd=self.tmp.name))

    def test_instances_only_for_matching_project(self):
        kw = dict(root=self.root, scenario="writing-paper", level="instance")
        self.assertIn("pull from Overleaf", index.build_index(cwd=self.proj, **kw))
        self.assertNotIn("pull from Overleaf", index.build_index(cwd=self.tmp.name, **kw))
        p_only = index.build_index(root=self.root, level="principle")
        self.assertNotIn("## Rules", p_only)
        self.assertIn("Claims need shown evidence", p_only)

    def test_budget(self):
        for i in range(200):
            write_v1(self.root, "global", id=f"r{i:03d}", parent="ask-first", title=f"Rule {i}",
                     strength="must" if i == 199 else "should")
        out = index.build_index(root=self.root, max_lines=40)
        self.assertLessEqual(len(out.splitlines()), 40)
        self.assertIn("Rule 199", out)   # must survives the cut
        self.assertIn("omitted", out)


class ScenarioTest(unittest.TestCase):
    def best(self, text, cwd=None):
        return scenario.best(text, cwd)

    def test_english(self):
        self.assertEqual(self.best("tighten the rebuttal paragraph in main.tex"), "writing-paper")
        self.assertEqual(self.best("git push and open a PR"), "release-deploy")
        self.assertEqual(self.best("pytest fails with a traceback"), "debugging")
        self.assertEqual(self.best("redo the figure 2 plot colours"), "figures-visual")
        self.assertEqual(self.best("how much API credit will this batch cost?"), "cost-resources")
        self.assertEqual(self.best("why did we choose this, what is the plan"), "planning-discussion")

    def test_chinese(self):
        self.assertEqual(self.best("帮我改一下论文摘要"), "writing-paper")
        self.assertEqual(self.best("部署到线上然后推送"), "release-deploy")
        self.assertEqual(self.best("这个报错怎么调试"), "debugging")
        self.assertEqual(self.best("这次跑要花多少钱"), "cost-resources")
        self.assertEqual(self.best("我们讨论一下计划"), "planning-discussion")

    def test_ranked_and_cwd(self):
        r = scenario.infer_scenario("rerun the experiment seeds, what will the API cost be")
        self.assertEqual(r[0][0], "cost-resources")
        self.assertIn("experiments", [s for s, _ in r])
        self.assertEqual(r, sorted(r, key=lambda x: -x[1]))
        self.assertEqual(scenario.infer_scenario("hello there"), [])
        self.assertEqual(self.best("", "/home/x/tech-report/paper"), "writing-paper")
        self.assertTrue(all(s in scenario.SCENARIOS for s, _ in r))


class V1CliTest(V1Fixture):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(args))
        return rc, buf.getvalue()

    def test_search_context_infers(self):
        rc, out = self.run_cli("--dir", self.root, "search", "push", "--json",
                               "--context", "now polish the abstract of the paper")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["scenario"], "writing-paper")
        self.assertNotIn("no-push-without-go-ahead", [h["id"] for h in data["hits"]])
        rc, out = self.run_cli("--dir", self.root, "search", "push", "--json", "--context", "git push to main")
        self.assertEqual(json.loads(out)["hits"][0]["id"], "no-push-without-go-ahead")

    def test_index_and_inject_flags(self):
        rc, out = self.run_cli("--dir", self.root, "index", "--print", "--scenario", "writing-paper",
                               "--level", "instance", "--cwd", self.proj, "--all")
        self.assertIn("Prefer short captions", out)
        self.assertIn("pull from Overleaf", out)
        rc, out = self.run_cli("--dir", self.root, "index", "--print", "--strength", "must")
        self.assertIn("Push or open a PR", out)
        self.assertNotIn("Quote paper numbers", out)
        t = os.path.join(self.proj, "CLAUDE.md")
        rc, out = self.run_cli("--dir", self.root, "inject", "--target", t, "--scenario", "writing-paper")
        self.assertIn("+" + inject.START, out)
        self.assertNotIn("Push or open a PR", out)
        self.assertFalse(os.path.exists(t))
        self.run_cli("--dir", self.root, "inject", "--target", t, "--scenario", "writing-paper", "--apply")
        rc, out = self.run_cli("--dir", self.root, "inject", "--target", t, "--scenario", "writing-paper")
        self.assertIn("already up to date", out)

    def test_tree_and_scenarios(self):
        rc, out = self.run_cli("--dir", self.root, "tree")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        i = lines.index("ask-first  [must; any]")
        self.assertTrue(any(l.startswith("  - no-push-without-go-ahead") for l in lines[i:]))
        self.assertTrue(any(l.startswith("    - overleaf-pull-first") for l in lines))
        self.assertIn("orphans (parent id not found): 1", out)
        rc, out = self.run_cli("--dir", self.root, "scenarios", "--json")
        c = json.loads(out)
        self.assertEqual(c["writing-paper"]["rule"], 2)
        self.assertEqual(c["any"]["principle"], 2)

    def test_mcp_v1_tools(self):
        def call(name, **args):
            r = mcp_server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                   "params": {"name": name, "arguments": args}}, self.root)
            return r["result"]["content"][0]["text"]
        t = call("search_lessons", query="git push", scenario="writing-paper")
        self.assertNotIn("no-push-without-go-ahead", t)
        t = call("search_lessons", query="git push overleaf", scenario="release-deploy", level="instance",
                 cwd=self.proj)
        self.assertIn("overleaf-pull-first", t)
        self.assertIn("under: no-push-without-go-ahead < ask-first", t)
        t = call("get_lesson", id="no-push-without-go-ahead")
        self.assertIn("Parent chain", t)
        self.assertIn("- ask-first [principle]", t)
        self.assertIn("- overleaf-pull-first [instance", t)
        self.assertIn("ask-first", call("list_principles"))
        self.assertEqual(json.loads(call("infer_scenario", text="git push the release"))["best"], "release-deploy")


if __name__ == "__main__":
    unittest.main()
