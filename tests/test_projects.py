import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lessons import dream, projects  # noqa: E402
from lessons.model import Session, Turn  # noqa: E402


def session(cwd: str) -> Session:
    return Session(source="claude-code", id=cwd or "none", path="/x/s.jsonl", cwd=cwd,
                   turns=[Turn("user", "hi")])


class PatternsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "exclude-projects")
        with open(self.file, "w") as fh:
            fh.write("# client work\n\n/home/u/clients/acme\n*medco*\n  ~/private  \n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_skips_comments_and_blanks_and_expands_home(self):
        pats = projects.load(self.file)
        self.assertEqual(pats, ["/home/u/clients/acme", "*medco*", os.path.expanduser("~/private")])

    def test_missing_file_means_no_patterns(self):
        self.assertEqual(projects.load(os.path.join(self.tmp.name, "nope")), [])

    def test_a_plain_path_covers_its_subfolders_only(self):
        pats = ["/home/u/clients/acme"]
        self.assertTrue(projects.excluded("/home/u/clients/acme", pats))
        self.assertTrue(projects.excluded("/home/u/clients/acme/web/", pats))
        self.assertFalse(projects.excluded("/home/u/clients/acme-site", pats))  # near miss
        self.assertFalse(projects.excluded("/home/u/own", pats))

    def test_a_glob_matches_without_case(self):
        self.assertTrue(projects.excluded("/srv/MedCo-portal/app", ["*medco*"]))
        self.assertFalse(projects.excluded("/srv/medical", ["*medco*"]))

    def test_unknown_folder_is_excluded_only_when_there_are_patterns(self):
        self.assertTrue(projects.excluded("", ["*medco*"]))
        self.assertFalse(projects.excluded("", []))

    def test_filter_keeps_allowed_sessions_and_counts_the_rest(self):
        pats = ["/c/acme", "*medco*"]
        counts: dict = {}
        kept = list(projects.only_allowed(lambda: iter([session("/own/a"), session("/c/acme/x"),
                                                         session("/m/medco"), session("")]),
                                          pats, counts, "claude-code")())
        self.assertEqual([s.cwd for s in kept], ["/own/a"])
        self.assertEqual(counts, {"claude-code": 3})


class TextTermsTest(unittest.TestCase):
    def test_session_lines_skip_sessions_that_mention_the_word_anywhere(self):
        pats = ["session:MedCo", "/c/acme"]
        tool = Session(source="claude-code", id="t", path="/x", cwd="/home/u",
                       turns=[Turn("user", "fix the login"), Turn("tool", "rows from medco_patients: 3")])
        clean = Session(source="claude-code", id="c", path="/x", cwd="/home/u", turns=[Turn("user", "fix the medical form")])
        self.assertTrue(projects.excluded_session(tool, pats))
        self.assertFalse(projects.excluded_session(clean, pats))  # near miss: "medical" is not "medco"
        self.assertTrue(projects.excluded_session(session("/c/acme/web"), pats))

    def test_text_lines_keep_the_session(self):
        s = Session(source="claude-code", id="t", path="/x", cwd="/home/u", turns=[Turn("tool", "acme export")])
        self.assertFalse(projects.excluded_session(s, ["text:acme"]))

    def test_word_lines_are_not_folder_patterns(self):
        self.assertFalse(projects.excluded("/srv/text:medco", ["text:medco", "session:medco"]))
        self.assertTrue(projects.excluded("", ["text:medco"]))  # unknown folder, list set: still skipped

    def test_a_moment_is_checked_on_all_its_turns(self):
        m = {"cwd": "/home/u", "trigger": {"text": "no"}, "context": [{"text": "MEDCO export"}], "followup": []}
        self.assertEqual(dream.allowed_moments([m], ["text:medco"]), [])
        self.assertEqual(dream.allowed_moments([m], ["session:medco"]), [])  # shards: session words too
        self.assertEqual(len(dream.allowed_moments([m], ["text:acme"])), 1)


class LiveMomentsTest(unittest.TestCase):
    def test_live_moments_drop_moments_with_text_words_and_sessions_with_session_words(self):
        from lessons import sources

        def human(sid, cwd, tool_text):
            return Session(source="fake", id=sid, path="/x/s.jsonl", cwd=cwd, turns=[
                Turn("user", "add the export button", ref=f"/x/{sid}.jsonl:1"),
                Turn("assistant", "Done, pushed it.", ref=f"/x/{sid}.jsonl:2"),
                Turn("tool", tool_text, ref=f"/x/{sid}.jsonl:3"),
                Turn("user", f"no, don't push before I say so ({sid})", ref=f"/x/{sid}.jsonl:4"),
                Turn("assistant", "Understood.", ref=f"/x/{sid}.jsonl:5"),
                Turn("user", "ok now run the tests", ref=f"/x/{sid}.jsonl:6")])

        fake = lambda: iter([human("a", "/own", "3 rows"), human("b", "/own", "acme rows: 3"),
                             human("c", "/own", "medco_patients: 2")])
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "ex")
            with open(f, "w") as fh:
                fh.write("text:acme\nsession:medco\n")
            old_env, old_src = os.environ.get("LESSONS_EXCLUDE_FILE"), dict(sources.SOURCES)
            os.environ["LESSONS_EXCLUDE_FILE"] = f
            sources.SOURCES["fake"] = fake
            try:
                out, prints = dream.live_moments(None, ["fake"])
            finally:
                sources.SOURCES.clear()
                sources.SOURCES.update(old_src)
                os.environ["LESSONS_EXCLUDE_FILE"] = old_env or os.devnull
        self.assertEqual({m["session"] for m in out}, {"a"})
        self.assertNotIn("fake:b", prints)  # its moment held a text word: stays open
        self.assertNotIn("fake:c", prints)  # a skipped session is never read


class SourcesUseTheFileTest(unittest.TestCase):
    def test_env_points_at_the_file_and_sources_are_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "ex")
            with open(f, "w") as fh:
                fh.write("/c/acme\n")
            old = os.environ.get("LESSONS_EXCLUDE_FILE")
            os.environ["LESSONS_EXCLUDE_FILE"] = f
            try:
                self.assertEqual(projects.default_file(), f)
                srcs = projects.sources({"t": lambda: iter([session("/c/acme"), session("/own")])})
                self.assertEqual([s.cwd for s in srcs["t"]()], ["/own"])
            finally:
                if old is None:
                    os.environ.pop("LESSONS_EXCLUDE_FILE")
                else:
                    os.environ["LESSONS_EXCLUDE_FILE"] = old

    def test_shard_moments_from_excluded_folders_are_dropped(self):
        moments = [{"cwd": "/c/acme/x", "session": "1"}, {"cwd": "/own", "session": "2"}, {"session": "3"}]
        self.assertEqual([m["session"] for m in dream.allowed_moments(moments, ["/c/acme"])], ["2"])
        self.assertEqual(len(dream.allowed_moments(moments, [])), 3)


if __name__ == "__main__":
    unittest.main()


class ReviewFindingsTest(unittest.TestCase):
    """Cases from the independent review of the exclude PR."""

    def test_tool_names_and_the_project_header_are_checked(self):
        m = {"cwd": "/own", "project": "/own", "trigger": {"text": "no"},
             "context": [{"text": "ok", "tool": "mcp__acme__search"}], "followup": []}
        self.assertEqual(dream.allowed_moments([m], ["text:acme"]), [])
        m2 = dict(m, cwd="/srv/acme-site", project="/srv/acme-site", context=[])
        self.assertEqual(dream.allowed_moments([m2], ["text:acme"]), [])

    def test_a_glob_covers_the_folders_below(self):
        self.assertTrue(projects.excluded("/home/u/secret/sub", ["/home/*/secret"]))
        self.assertFalse(projects.excluded("/home/u/secrets", ["/home/*/secret"]))

    def test_a_path_with_brackets_that_exists_is_a_plain_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "[work]")
            os.makedirs(os.path.join(d, "app"))
            self.assertTrue(projects.excluded(os.path.join(d, "app"), [d]))

    def test_a_symlinked_folder_is_matched_by_its_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "clients", "acme")
            os.makedirs(real)
            link = os.path.join(tmp, "a")
            os.symlink(real, link)
            self.assertTrue(projects.excluded(link, [os.path.join(tmp, "clients")]))

    def test_lessons_that_match_are_found_by_evidence_and_words(self):
        clean = {"id": "a", "title": "Ask before push", "scope": "global",
                 "evidence": [{"ref": "/x:1", "project": "/own", "quote": "no, wait"}]}
        by_folder = dict(clean, id="b", evidence=[{"ref": "/x:2", "project": "/c/acme/web", "quote": "no"}])
        by_word = dict(clean, id="c", why="the Acme export broke")
        by_scope = dict(clean, id="d", scope="project:/c/acme")
        got = [l["id"] for l in projects.matching_lessons([clean, by_folder, by_word, by_scope],
                                                          ["/c/acme", "text:acme"])]
        self.assertEqual(got, ["b", "c", "d"])


class DreamStopsOnExcludedLessonsTest(unittest.TestCase):
    def test_consolidate_refuses_when_an_existing_lesson_matches(self):
        from lessons.dream import EngineError
        lesson = ("---\nid: x\ntitle: Acme export first\nscope: global\nkind: preference\ntrigger: t\n"
                  "status: candidate\nevidence:\n  - ref: /x/s.jsonl:3\n    source: claude-code\n"
                  "    date: 2026-10-01\n    quote: \"no\"\n    project: /c/acme\n---\n**Do:** d\n")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "lessons")
            os.makedirs(os.path.join(out, "global"))
            with open(os.path.join(out, "global", "x.md"), "w") as fh:
                fh.write(lesson)
            cand = os.path.join(tmp, "cand", "global")
            os.makedirs(cand)
            with open(os.path.join(cand, "y.md"), "w") as fh:
                fh.write(lesson.replace("id: x", "id: y").replace("Acme export first", "Ask first")
                         .replace("project: /c/acme", "project: /own"))
            f = os.path.join(tmp, "ex")
            with open(f, "w") as fh:
                fh.write("/c/acme\n")
            old = os.environ.get("LESSONS_EXCLUDE_FILE")
            os.environ["LESSONS_EXCLUDE_FILE"] = f
            try:
                from types import SimpleNamespace
                a = SimpleNamespace(shards=[], sources="", engine="fake", model="", home=os.path.join(tmp, "h"),
                                    out=out, add_candidates=[os.path.join(tmp, "cand")], max_batches=0, batch_tokens=12000, budget_usd=0.0,
                                    threshold=None, abstraction="medium", max_principles=10, scenarios="",
                                    timeout=10, no_consolidate=False, consolidate_only=True, dry_run=False)
                with self.assertRaises(EngineError) as cm:
                    dream.run(a, engine=dream.FakeEngine(), log=lambda *x: None)
                self.assertIn("x", str(cm.exception))
                self.assertTrue(os.path.exists(os.path.join(out, "global", "x.md")))  # nothing deleted
            finally:
                os.environ["LESSONS_EXCLUDE_FILE"] = old or os.devnull

    def test_add_candidates_that_match_are_dropped(self):
        cands = [{"id": "", "title": "fine", "evidence": [{"ref": "/x:1", "project": "/own"}]},
                 {"id": "", "title": "Acme one", "evidence": [{"ref": "/x:2", "project": "/own"}]}]
        self.assertEqual([c["title"] for c in projects.allowed_lessons(cands, ["text:acme"])], ["fine"])


class StateKeepsSessionsWithDroppedMomentsOpenTest(unittest.TestCase):
    def test_a_session_with_a_dropped_moment_is_not_marked_done(self):
        from lessons import sources

        def human(sid, tool_text):
            return Session(source="fake", id=sid, path=f"/x/{sid}.jsonl", cwd="/own", turns=[
                Turn("user", "add the button", ref=f"/x/{sid}.jsonl:1"),
                Turn("assistant", "Done, pushed it.", ref=f"/x/{sid}.jsonl:1"),
                Turn("tool", tool_text, ref=f"/x/{sid}.jsonl:2"),
                Turn("user", f"no, don't push yet ({sid})", ref=f"/x/{sid}.jsonl:3"),
                Turn("user", "ok go on", ref=f"/x/{sid}.jsonl:4")])

        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "ex")
            with open(f, "w") as fh:
                fh.write("text:acme\n")
            old_env, old_src = os.environ.get("LESSONS_EXCLUDE_FILE"), dict(sources.SOURCES)
            os.environ["LESSONS_EXCLUDE_FILE"] = f
            sources.SOURCES["fake"] = lambda: iter([human("a", "3 rows"), human("b", "acme rows")])
            try:
                out, prints = dream.live_moments(None, ["fake"])
            finally:
                sources.SOURCES.clear()
                sources.SOURCES.update(old_src)
                os.environ["LESSONS_EXCLUDE_FILE"] = old_env or os.devnull
        self.assertIn("fake:a", prints)
        self.assertNotIn("fake:b", prints)  # re-read once the word is removed from the file
