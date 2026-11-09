import json
import re
import tempfile
import unittest
from pathlib import Path

from lessons import frontmatter, view

FIX = Path(__file__).parent / "fixtures" / "lessons"
FIX1 = Path(__file__).parent / "fixtures" / "lessons-v1"


def page_data(html_text: str) -> dict:
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html_text, re.S)
    return json.loads(m.group(1))


class FrontmatterTest(unittest.TestCase):
    def test_scalars_lists_dicts(self):
        meta, body = frontmatter.load(
            "---\n"
            "id: a-b   # comment\n"
            "title: \"Say: \\\"hi\\\" # not a comment\"\n"
            "seen: 3\n"
            "themes: [x, 'y, z']\n"
            "tags:\n  - one\n  - \"two\"\n"
            "evidence:\n"
            "  - ref: /p.jsonl:4\n    source: codex\n    quote: 'it''s fine'\n"
            "  - ref: \"db#m1\"\n    date: 2026-01-02\n"
            "---\n\nBody\n"
        )
        self.assertEqual(meta["id"], "a-b")
        self.assertEqual(meta["title"], 'Say: "hi" # not a comment')
        self.assertEqual(meta["seen"], 3)
        self.assertEqual(meta["themes"], ["x", "y, z"])
        self.assertEqual(meta["tags"], ["one", "two"])
        self.assertEqual(meta["evidence"][0], {"ref": "/p.jsonl:4", "source": "codex", "quote": "it's fine"})
        self.assertEqual(meta["evidence"][1], {"ref": "db#m1", "date": "2026-01-02"})
        self.assertEqual(body.strip(), "Body")

    def test_no_frontmatter(self):
        self.assertEqual(frontmatter.load("just text"), ({}, "just text"))

    def test_fixtures_parse(self):
        lessons, warnings = view.load_lessons(FIX)
        self.assertEqual(len(lessons), 5)
        self.assertEqual(warnings, [])
        by = {l["id"]: l for l in lessons}
        v = by["verify-numbers-against-source"]
        self.assertEqual(len(v["evidence"]), 3)
        self.assertEqual(v["evidence"][2]["quote"], "that's not what the csv says, re-read it")
        self.assertEqual(v["sources"], ["claude-code", "codex"])
        self.assertIn("不对", v["evidence"][0]["quote"])
        x = by["escape-transcript-html"]
        self.assertEqual(x["trigger"], "Rendering user or tool text in a generated page")
        self.assertEqual(x["themes"], ["security", "html"])


class MarkdownTest(unittest.TestCase):
    def test_subset(self):
        h = view.render_markdown("**Do:** use `a<b>` and [x](https://e.org).\n\n- one\n- two <i>")
        self.assertIn("<strong>Do:</strong>", h)
        self.assertIn("<code>a&lt;b&gt;</code>", h)
        self.assertIn('<a href="https://e.org">x</a>', h)
        self.assertIn("<ul><li>one</li><li>two &lt;i&gt;</li></ul>", h)

    def test_unsafe_link_dropped(self):
        h = view.render_markdown("[bad](javascript:alert(1)) <script>x</script>")
        self.assertNotIn("href", h)
        self.assertNotIn("<script>", h)


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        shard = Path(self.tmp.name) / "shard-00.jsonl"
        rows = [
            {"kind": "correction", "source": "claude-code", "trigger": {"ts": "2026-08-14T10:00:00Z"}},
            {"kind": "interrupt", "source": "claude-code", "trigger": {"ts": "2026-09-02T10:00:00Z"}},
            {"kind": "frustration", "source": "opencode", "trigger": {"ts": 1791937064194}},
            {"kind": "repeated-error", "source": "codex", "trigger": {"ts": "2026-10-21T10:00:00Z"}},
        ]
        shard.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        self.path = view.build_site(FIX, Path(self.tmp.name) / "site", str(Path(self.tmp.name) / "*.jsonl"))
        self.html = self.path.read_text(encoding="utf-8")
        self.data = page_data(self.html)

    def test_single_file_offline(self):
        self.assertEqual(self.path.name, "index.html")
        self.assertNotRegex(self.html, r'(?:src|href)="https?://')
        self.assertNotIn("{{", self.html)

    def test_counts(self):
        f = {k: dict(v) for k, v in self.data["facets"].items()}
        self.assertEqual(f["status"], {"verified": 2, "candidate": 2, "refuted": 1})
        self.assertEqual(f["kind"], {"pitfall": 2, "preference": 1, "workflow": 1, "communication": 1})
        self.assertEqual(f["scope"]["global"], 3)
        self.assertEqual(f["scope"]["project:tech-report"], 1)
        self.assertEqual(f["source"], {"claude-code": 4, "codex": 2, "opencode": 1})
        self.assertEqual(self.data["moments"]["total"], 4)
        self.assertEqual(self.data["lessons"][0]["status"], "verified")

    def test_escaping(self):
        # the quote contains a live <script> tag; it must never appear raw in the page
        self.assertEqual(self.html.count("<script"), 2)  # data block + page script only
        self.assertNotIn("<script>alert", self.html)
        self.assertNotIn("<img src=x", self.html)
        x = next(l for l in self.data["lessons"] if l["id"] == "escape-transcript-html")
        self.assertIn("<script>alert('x')</script>", x["evidence"][0]["quote"])  # survives as data
        self.assertIn("&lt;b&gt;this&lt;/b&gt;", x["body_html"])
        self.assertNotIn("javascript:", x["body_html"])

    def test_refs_and_chart(self):
        v = next(l for l in self.data["lessons"] if l["id"] == "verify-numbers-against-source")
        self.assertTrue(v["evidence"][0]["href"].startswith("file:///Users/"))
        self.assertNotIn(":412", v["evidence"][0]["href"])
        self.assertEqual(v["evidence"][2]["href"], "")  # db#id is not a file
        self.assertIn("<svg class=\"chart\"", self.html)
        self.assertEqual(v["spark"].count("<circle"), 3)

    def test_without_moments_and_empty(self):
        p = view.build_site(FIX, Path(self.tmp.name) / "plain")
        self.assertNotIn('<svg class="chart"', p.read_text())
        empty = Path(self.tmp.name) / "none"
        empty.mkdir()
        d = page_data(view.build_site(empty, Path(self.tmp.name) / "e").read_text())
        self.assertEqual(d["lessons"], [])

    def test_cli(self):
        out = Path(self.tmp.name) / "cli"
        self.assertEqual(view.main([str(FIX), "--out", str(out)]), 0)
        self.assertTrue((out / "index.html").exists())


class V1Test(unittest.TestCase):
    def setUp(self):
        self.lessons, self.warnings = view.load_lessons(FIX1)
        self.by = {l["id"]: l for l in self.lessons}

    def test_parse_v1_fields(self):
        self.assertEqual(len(self.lessons), 12)
        r = self.by["push-only-after-go-ahead"]
        self.assertEqual(r["level"], "rule")
        self.assertEqual(r["parent"], "ask-where-the-person-decides")
        self.assertEqual(r["scenarios"], ["coding", "release-deploy"])
        self.assertEqual(r["strength"], "must")
        self.assertIn("Stop after the local commit", r["do_html"])
        self.assertIn("reverted", r["why_html"])
        self.assertIn("push, ship or merge", r["check_html"])
        self.assertEqual(r["rest_html"], "")
        self.assertGreater(r["tokens"], 10)
        p = self.by["ask-where-the-person-decides"]
        self.assertEqual((p["level"], p["parent"], p["evidence"]), ("principle", "", []))

    def test_v0_defaults_to_rule(self):
        v0 = self.by["legacy-v0-lesson"]
        self.assertEqual((v0["level"], v0["parent"], v0["scenarios"], v0["strength"]), ("rule", "", [], ""))
        old, _ = view.load_lessons(FIX)
        self.assertTrue(all(l["level"] == "rule" and l["depth"] == 0 for l in old))

    def test_hierarchy(self):
        p = self.by["ask-where-the-person-decides"]
        self.assertEqual(sorted(p["children"]), ["estimate-cost-before-paid-run", "push-only-after-go-ahead"])
        self.assertEqual(p["children"][0], "estimate-cost-before-paid-run")  # must, seen 3 before must, seen 2
        i = self.by["overleaf-pull-first"]
        self.assertEqual(i["ancestors"], ["ask-where-the-person-decides", "push-only-after-go-ahead"])
        self.assertEqual((i["root"], i["depth"]), ("ask-where-the-person-decides", 2))
        self.assertEqual(self.by["push-only-after-go-ahead"]["children"], ["overleaf-pull-first"])
        orphan = self.by["hooks-run-in-repo-root"]
        self.assertEqual((orphan["parent"], orphan["depth"]), ("", 0))
        self.assertEqual(self.warnings, ["tool-claude-code/orphan-instance.md: parent 'a-rule-that-does-not-exist' not found"])

    def test_hierarchy_cycles_and_mismatch(self):
        def l(i, level, parent):
            return {"id": i, "level": level, "parent": parent, "path": i + ".md", "strength": "", "status": "verified",
                    "seen": 1, "title": i}
        ls = [l("a", "rule", "b"), l("b", "rule", "a"), l("c", "instance", "c"), l("d", "rule", "e"), l("e", "rule", "")]
        w = view.build_hierarchy(ls)
        by = {x["id"]: x for x in ls}
        self.assertTrue(any("cycle" in x for x in w))
        self.assertTrue(any("own parent" in x for x in w))
        self.assertTrue(any("rule under rule" in x for x in w))
        self.assertEqual(by["d"]["ancestors"], ["e"])  # mismatch is warned about, link kept
        self.assertTrue(all(len(x["ancestors"]) < 5 for x in ls))

    def test_related_edges(self):
        rel = view.related_edges(self.lessons)
        pairs = {(a, b) for a, b, _, _ in rel}
        for a, b, w, why in rel:
            self.assertGreaterEqual(w, 1)
            self.assertTrue(why)
            self.assertNotEqual(self.by[a]["parent"], b)
            self.assertNotEqual(self.by[b]["parent"], a)
        # share theme "papers" and scenario writing-paper, and are not parent/child
        self.assertIn(("numbers-from-the-table", "overleaf-pull-first"), pairs)

    def test_sections(self):
        sec = view.split_sections("**Do:** a\n\n**Why:** b\n- c\n\nNote at the end.\n\n**Check:** d")
        self.assertEqual((sec["do"], sec["why"], sec["check"]), ("a", "b\n- c", "d"))
        self.assertEqual(sec["rest"], "Note at the end.")
        self.assertEqual(view.split_sections("plain")["rest"], "plain")


class V1BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.html = view.build_site(FIX1, Path(self.tmp.name) / "site").read_text(encoding="utf-8")
        self.data = page_data(self.html)

    def test_graph_and_facets_embedded(self):
        d = self.data
        ids = {l["id"] for l in d["lessons"]}
        self.assertEqual(len(ids), 12)
        for a, b, w, why in d["graph"]["related"]:
            self.assertIn(a, ids)
            self.assertIn(b, ids)
        for l in d["lessons"]:
            for c in l["children"]:
                self.assertIn(c, ids)
            if l["parent"]:
                self.assertIn(l["id"], next(x for x in d["lessons"] if x["id"] == l["parent"])["children"])
        f = {k: dict(v) for k, v in d["facets"].items()}
        self.assertEqual(f["level"], {"principle": 3, "rule": 6, "instance": 3})
        self.assertEqual(list(f["strength"]), ["must", "should", "prefer"])
        self.assertEqual(f["scenario"]["writing-paper"], 3)
        self.assertEqual(d["facets"]["scenario"][0][0], "any")  # SCENARIOS.md order

    def test_escaping_v1(self):
        self.assertEqual(self.html.count("<script"), 2)
        self.assertNotIn("<script>alert", self.html)
        r = next(l for l in self.data["lessons"] if l["id"] == "rerun-tests-before-done")
        self.assertIn("<script>alert('x')</script>", r["evidence"][0]["quote"])
        self.assertIn("&lt;b&gt;this&lt;/b&gt;", r["why_html"])
        self.assertNotIn("javascript:", r["why_html"])
        self.assertNotRegex(self.html, r'(?:src|href)="https?://')
        self.assertNotIn("{{", self.html)

    def test_json_breakout_is_escaped(self):
        tmp = Path(self.tmp.name) / "evil" / "global"
        tmp.mkdir(parents=True)
        (tmp / "x.md").write_text("---\nid: x\ntitle: \"</script><script>alert(1)</script>\"\nlevel: rule\n"
                                  "scenarios: [\"</script>\"]\nstatus: candidate\nkind: fact\ntrigger: t\n---\n"
                                  "**Do:** \u2028 & <i>\n", encoding="utf-8")
        h = view.build_site(tmp.parent, Path(self.tmp.name) / "evil-site").read_text(encoding="utf-8")
        self.assertEqual(h.count("<script"), 2)
        self.assertEqual(h.count("</script>"), 2)
        d = page_data(h)
        self.assertEqual(d["lessons"][0]["title"], "</script><script>alert(1)</script>")
        self.assertEqual(d["lessons"][0]["scenarios"], ["</script>"])


if __name__ == "__main__":
    unittest.main()
