import json
import re
import tempfile
import unittest
from pathlib import Path

from lessons import frontmatter, view

FIX = Path(__file__).parent / "fixtures" / "lessons"


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


if __name__ == "__main__":
    unittest.main()
