import json, os, shutil, tempfile, unittest
from types import SimpleNamespace

from lessons import dream
from lessons.state import State, moment_key


def moment(session, n, text, ts="2026-09-01T10:00:00Z", project="/p/proj"):
    path = f"/h/.claude/projects/x/{session}.jsonl"
    return {"kind": "correction", "source": "claude-code", "project": project, "cwd": project, "session": session,
            "trigger": {"role": "user", "tool": "", "error": False, "ts": ts, "ref": f"{path}:{n}", "text": text},
            "context": [{"role": "assistant", "tool": "Bash", "error": False, "ts": ts, "ref": f"{path}:{n - 1}",
                         "text": "{\"command\": \"git push --force\"}"}]}


def args(**kw):
    base = dict(shards=[], sources="", engine="fake", model="", home="", out="", add_candidates=[], max_batches=0,
                batch_tokens=12000, budget_usd=0.0, threshold=0.3, timeout=10, no_consolidate=False,
                consolidate_only=False, dry_run=False)
    base.update(kw)
    return SimpleNamespace(**base)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.shard = os.path.join(self.tmp, "shard.jsonl")
        self.out = os.path.join(self.tmp, "lessons")
        self.home = os.path.join(self.tmp, "home")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_shard(self, moments):
        with open(self.shard, "w") as fh:
            for m in moments:
                fh.write(json.dumps(m) + "\n")


class TestBatching(Base):
    def test_budget_splits_batches_and_max_batches_limits(self):
        ms = [moment(f"s{i}", 10 + i, "no, never force push " + "x" * 400, ts=f"2026-09-0{i % 9 + 1}T00:00:00Z")
              for i in range(10)]
        one = dream.tokens_estimate(dream.render_batch([ms[0]])[0])
        budget = dream.tokens_estimate(dream.extract_prompt([])[0]) + one * 3
        batches = dream.make_batches(ms, budget_tokens=budget)
        self.assertGreater(len(batches), 1)
        self.assertEqual(sum(len(b) for b in batches), 10)
        for b in batches:
            self.assertLessEqual(dream.tokens_estimate(dream.extract_prompt(b)[0]), budget + 10)
        eng = dream.FakeEngine(lambda p: "[]")
        _, stats = dream.extract(ms, eng, state=None, run_id="t", budget_tokens=budget, max_batches=2,
                                    log=lambda *x: None)
        self.assertEqual(eng.calls, 2)
        self.assertEqual(stats["batches_run"], 2)

    def test_prompt_carries_handles_and_purpose(self):
        prompt, refs = dream.extract_prompt([moment("s1", 5, "no, don't force push")])
        self.assertTrue(prompt.startswith(dream.EXTRACT_MARK))
        self.assertIn("[r2] USER", prompt)
        self.assertEqual(refs["r2"]["ref"], "/h/.claude/projects/x/s1.jsonl:5")

    def test_dry_run_calls_nothing_writes_nothing(self):
        self.write_shard([moment("s1", 5, "no, don't force push")])
        printed = []
        dream.run(args(shards=[self.shard], out=self.out, home=self.home, dry_run=True), log=printed.append)
        self.assertTrue(any("extract batch 1/1" in p for p in printed))
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(self.home))


class TestValidation(Base):
    def test_invented_refs_dropped(self):
        prompt, refs = dream.extract_prompt([moment("s1", 5, "no, don't force push to main")])
        raw = [
            {"title": "Never force push", "do": "Ask first.", "kind": "pitfall", "scope": "global",
             "evidence": [{"ref": "r2", "quote": "don't force push"}, {"ref": "r99", "quote": "made up"}]},
            {"title": "Invented lesson", "do": "x", "evidence": [{"ref": "/some/other.jsonl:3", "quote": "hi"}]},
        ]
        out, stats = dream.validate(raw, refs)
        self.assertEqual(len(out), 1)
        self.assertEqual(stats["refs_invented"], 2)
        self.assertEqual(stats["dropped_no_evidence"], 1)
        ev = out[0]["evidence"]
        self.assertEqual([e["ref"] for e in ev], ["/h/.claude/projects/x/s1.jsonl:5"])
        self.assertEqual(out[0]["first_seen"], "2026-09-01")
        self.assertEqual(out[0]["seen"], 1)
        self.assertEqual(out[0]["status"], "candidate")

    def test_full_ref_accepted_and_fake_quote_replaced(self):
        _, refs = dream.extract_prompt([moment("s1", 5, "no, don't force push to main")])
        raw = [{"title": "T", "do": "D", "evidence": [{"ref": "/h/.claude/projects/x/s1.jsonl:5",
                                                        "quote": "words the person never said"}]}]
        out, stats = dream.validate(raw, refs)
        self.assertEqual(out[0]["evidence"][0]["quote"], "no, don't force push to main")
        self.assertEqual(stats["quotes_replaced"], 1)

    def test_project_scope_resolved_from_evidence(self):
        _, refs = dream.extract_prompt([moment("s1", 5, "no")])
        out, _ = dream.validate([{"title": "T", "do": "D", "scope": "project",
                                  "evidence": [{"ref": "r2", "quote": "no"}]}], refs)
        self.assertEqual(out[0]["scope"], "project:/p/proj")
        self.assertEqual(dream.scope_dir(out[0]["scope"]), "project-proj")

    def test_parse_json_fenced_and_bare(self):
        self.assertEqual(dream.parse_json("sure\n```json\n[1]\n```"), [1])
        self.assertEqual(dream.parse_json('here: {"lessons": []} done'), {"lessons": []})
        self.assertIsNone(dream.parse_json("nothing"))


def lesson(title, session, n, status="candidate", themes=("git",), date="2026-09-01"):
    ev = [{"ref": f"/h/{session}.jsonl:{n}", "source": "claude-code", "date": date, "quote": f"q{n}",
           "session": session}]
    return dream._finish({"id": "", "title": title, "scope": "global", "kind": "pitfall", "trigger": "pushing",
                          "status": status, "themes": list(themes), "evidence": ev, "do": "d", "why": "w",
                          "check": ""})


class TestMerge(Base):
    def test_merge_unions_evidence_and_verifies_on_two_sessions(self):
        a, b = lesson("Never force push to main", "s1", 1), lesson("Never force push to main branch", "s2", 2)
        out, log = dream.apply_merge([a, b], {"lessons": [{"merge": ["L1", "L2"], "title": "Ask before force push"}]}, {})
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["evidence"]), 2)
        self.assertEqual(out[0]["seen"], 2)
        self.assertEqual(out[0]["status"], "verified")
        self.assertIn("merged", log[0])

    def test_same_session_stays_candidate(self):
        a, b = lesson("Never force push", "s1", 1), lesson("Never force push", "s1", 2)
        out, _ = dream.apply_merge([a, b], {"lessons": [{"merge": ["L1", "L2"], "title": "x"}]}, {})
        self.assertEqual(out[0]["seen"], 1)
        self.assertEqual(out[0]["status"], "candidate")

    def test_uncovered_members_kept_and_refuted(self):
        a, b, c = lesson("A", "s1", 1), lesson("B", "s2", 2), lesson("C", "s3", 3)
        _, eh = dream.consolidate_prompt([a, b, c])
        out, _ = dream.apply_merge([a, b, c], {"lessons": [{"merge": ["L1"], "title": "A2", "refuted_by": ["e2"]},
                                                           {"merge": ["L9"], "title": "ghost"}]}, eh)
        titles = {l["title"]: l for l in out}
        self.assertEqual(set(titles), {"A2", "B", "C"})  # nothing lost, nothing invented
        self.assertEqual(titles["A2"]["status"], "refuted")
        self.assertEqual(titles["A2"]["refuted_by"], ["/h/s2.jsonl:2"])

    def test_cluster_groups_similar(self):
        a = lesson("Never force push to main", "s1", 1)
        b = lesson("Never force push to main without asking", "s2", 2)
        c = lesson("Render figures with embedded fonts", "s3", 3, themes=("figures",))
        groups = dream.cluster([a, b, c])
        self.assertEqual(sorted(len(g) for g in groups), [1, 2])

    def test_consolidate_writes_files_and_changelog(self):
        a, b = lesson("Never force push to main", "s1", 1), lesson("Never force push to main", "s2", 2)
        os.makedirs(self.out)
        stats = dream.consolidate([], [a, b], dream.FakeEngine(), out_dir=self.out, run_id="r1", log=lambda *x: None)
        self.assertEqual(stats["lessons"], 1)
        files = [f for f in os.listdir(os.path.join(self.out, "global"))]
        self.assertEqual(len(files), 1)
        back = dream.read_lessons(self.out)
        self.assertEqual(back[0]["status"], "verified")
        self.assertEqual(len(back[0]["evidence"]), 2)
        with open(os.path.join(self.out, "CHANGELOG.md")) as fh:
            self.assertIn("## r1", fh.read())
        # a later candidate merges into the existing file, which keeps its id and path
        c = lesson("Never force push to main", "s3", 3)
        dream.consolidate(back, [c], dream.FakeEngine(), out_dir=self.out, run_id="r2", log=lambda *x: None)
        again = dream.read_lessons(self.out)
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["id"], back[0]["id"])
        self.assertEqual(again[0]["seen"], 3)

    def test_round_trip(self):
        l = lesson('Quote "this" and: that', "s1", 1)
        l["id"] = "x"
        l["evidence"][0]["quote"] = '不要 "push" — 先问我'
        path = dream.write_lesson(self.out, l)
        back = dream.read_lessons(self.out)[0]
        self.assertEqual(back["title"], l["title"])
        self.assertEqual(back["evidence"][0]["quote"], l["evidence"][0]["quote"])
        self.assertEqual(back["do"], "d")
        self.assertTrue(path.endswith("global/x.md"))


class TestIncremental(Base):
    def test_second_run_processes_nothing_new(self):
        self.write_shard([moment("s1", 5, "no, don't force push to main"),
                          moment("s2", 7, "no, don't force push to main again", ts="2026-09-02T00:00:00Z")])
        a = args(shards=[self.shard], out=self.out, home=self.home)
        eng = dream.FakeEngine()
        r1 = dream.run(a, engine=eng, log=lambda *x: None)
        self.assertEqual(r1["extract"]["moments_new"], 2)
        self.assertTrue(os.path.exists(os.path.join(self.home, "state.json")))
        lessons1 = dream.read_lessons(self.out)
        self.assertTrue(lessons1)
        eng2 = dream.FakeEngine()
        r2 = dream.run(a, engine=eng2, log=lambda *x: None)
        self.assertEqual(r2["extract"]["moments_new"], 0)
        self.assertEqual(eng2.calls, 0)
        self.assertEqual(len(dream.read_lessons(self.out)), len(lessons1))
        st = State(os.path.join(self.home, "state.json"))
        self.assertTrue(st.seen(moment_key(moment("s1", 5, "no, don't force push to main"))))

    def test_failed_batch_not_marked(self):
        self.write_shard([moment("s1", 5, "no")])
        a = args(shards=[self.shard], out=self.out, home=self.home)
        dream.run(a, engine=dream.FakeEngine(lambda p: "I refuse"), log=lambda *x: None)
        r = dream.run(a, engine=dream.FakeEngine(), log=lambda *x: None)
        self.assertEqual(r["extract"]["moments_new"], 1)

    def test_default_home_beside_out(self):
        self.write_shard([moment("s1", 5, "no")])
        r = dream.run(args(shards=[self.shard], out=self.out), engine=dream.FakeEngine(), log=lambda *x: None)
        self.assertEqual(r["home"], self.out + ".dream")


if __name__ == "__main__":
    unittest.main()
