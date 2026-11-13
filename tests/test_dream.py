import json, os, shutil, tempfile, unittest
from types import SimpleNamespace

from lessons import dream

os.environ["LESSONS_EXCLUDE_FILE"] = os.devnull  # never the person's own list
from lessons.state import State, moment_key


def moment(session, n, text, ts="2026-09-01T10:00:00Z", project="/p/proj"):
    path = f"/h/.claude/projects/x/{session}.jsonl"
    return {"kind": "correction", "source": "claude-code", "project": project, "cwd": project, "session": session,
            "trigger": {"role": "user", "tool": "", "error": False, "ts": ts, "ref": f"{path}:{n}", "text": text},
            "context": [{"role": "assistant", "tool": "Bash", "error": False, "ts": ts, "ref": f"{path}:{n - 1}",
                         "text": "{\"command\": \"git push --force\"}"}]}


def args(**kw):
    base = dict(shards=[], sources="", engine="fake", model="", home="", out="", add_candidates=[], max_batches=0,
                batch_tokens=12000, budget_usd=0.0, threshold=None, timeout=10, no_consolidate=False,
                consolidate_only=False, dry_run=False, abstraction="medium", max_principles=10, scenarios="")
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


def tool_moment(session, n, ts="2026-09-01T10:00:00Z"):
    m = moment(session, n, "no, stop retrying", ts=ts)
    m["kind"] = "repeated-error"
    m["followup"] = [{"role": "user", "tool": "", "error": False, "ts": ts, "ref": f"/h/{session}.jsonl:{n + 3}",
                      "text": "because the API key is reserved for batch work"}]
    return m


class TestV1Extract(Base):
    def test_v1_fields_scenarios_and_strength_validated(self):
        _, refs = dream.extract_prompt([moment("s1", 5, "no, don't force push to main")])
        raw = [{"title": "Ask before pushing", "do": "Ask.", "level": "principle", "strength": "must",
                "scenarios": ["release-deploy", "Coding", "made-up", "any"], "evidence": [{"ref": "r2"}]},
               {"title": "Only bogus", "do": "x", "level": "instance", "scenarios": ["nope"], "strength": "huge",
                "evidence": [{"ref": "r2"}]}]
        out, stats = dream.validate(raw, refs)
        self.assertEqual(out[0]["level"], "rule")  # principles only come from the abstraction pass
        self.assertEqual(out[0]["scenarios"], ["release-deploy", "coding"])
        self.assertEqual(out[0]["strength"], "must")
        self.assertEqual(out[0]["parent"], "")
        self.assertEqual(out[1]["level"], "instance")
        self.assertEqual(out[1]["scenarios"], ["any"])
        self.assertEqual(out[1]["strength"], "should")
        self.assertEqual(sorted(stats["scenarios_unknown"]), ["made-up", "nope"])

    def test_must_needs_the_persons_own_turn(self):
        _, refs = dream.extract_prompt([moment("s1", 5, "no")])
        out, stats = dream.validate([{"title": "T", "do": "D", "strength": "must", "evidence": [{"ref": "r1"}]}], refs)
        self.assertEqual(out[0]["strength"], "should")  # r1 is the agent's tool call
        self.assertEqual(stats["strength_downgraded"], 1)

    def test_followup_rendered_and_citable(self):
        prompt, refs = dream.extract_prompt([tool_moment("s1", 5)])
        self.assertIn("(next turn): because the API key", prompt)
        self.assertIn("`release-deploy`", prompt)
        h = next(h for h, v in refs.items() if v["text"].startswith("because"))
        out, _ = dream.validate([{"title": "T", "do": "D", "evidence": [{"ref": h}]}], refs)
        self.assertEqual(out[0]["evidence"][0]["ref"], "/h/s1.jsonl:8")

    def test_unknown_scenario_warned_in_run(self):
        self.write_shard([moment("s1", 5, "no, don't force push")])
        printed = []
        dream.run(args(shards=[self.shard], out=self.out, home=self.home), engine=dream.FakeEngine(), log=printed.append)
        self.assertTrue(any("unknown scenario" in p and "not-a-scenario" in p for p in printed))
        l = dream.read_lessons(self.out)[0]
        self.assertEqual((l["level"], l["scenarios"], l["strength"]), ("rule", ["coding"], "must"))

    def test_scenarios_restrict_extraction(self):
        self.write_shard([moment("s1", 5, "no, don't force push")])
        eng = dream.FakeEngine()
        r = dream.run(args(shards=[self.shard], out=self.out, home=self.home, scenarios="writing-paper"), engine=eng,
                      log=lambda *x: None)
        self.assertEqual(r["extract"]["dropped_scenario"], 1)
        self.assertNotIn("`coding`", eng.prompts[0])
        self.assertIn("`writing-paper`", eng.prompts[0])
        self.assertFalse(os.path.isdir(self.out) and dream.read_lessons(self.out))
        r = dream.run(args(shards=[self.shard], out=self.out + "2", home=self.home + "2", scenarios="coding"),
                      engine=dream.FakeEngine(), log=lambda *x: None)
        self.assertEqual(r["extract"]["kept"], 1)
        with self.assertRaises(dream.EngineError):
            dream.run(args(shards=[self.shard], out=self.out, home=self.home, scenarios="bogus"),
                      engine=dream.FakeEngine(), log=lambda *x: None)

    def test_round_trip_v1_and_v0_defaults(self):
        l = lesson("Ask first", "s1", 1)
        l.update(id="ask", level="instance", parent="p", scenarios=["coding", "release-deploy"], strength="prefer")
        dream.write_lesson(self.out, l)
        p = {"id": "p", "title": "Principle", "level": "principle", "parent": "", "scope": "global", "kind": "pitfall",
             "scenarios": ["any"], "strength": "should", "trigger": "", "status": "verified", "themes": [],
             "evidence": [], "seen": 3, "first_seen": "2026-09-01", "last_seen": "2026-09-03", "do": "d", "why": "w"}
        dream.write_lesson(self.out, p)
        with open(os.path.join(self.out, "global", "v0.md"), "w") as fh:
            fh.write('---\nid: v0\ntitle: "Old"\nscope: "global"\nkind: pitfall\nstatus: candidate\nevidence:\n'
                     '  - ref: "/h/x.jsonl:1"\n    quote: "q"\nseen: 1\n---\n\n**Do:** d\n')
        back = {x["id"]: x for x in dream.read_lessons(self.out)}
        self.assertEqual((back["ask"]["level"], back["ask"]["parent"], back["ask"]["strength"]), ("instance", "p", "prefer"))
        self.assertEqual(back["ask"]["scenarios"], ["coding", "release-deploy"])
        self.assertEqual((back["p"]["level"], back["p"]["seen"], back["p"]["status"], back["p"]["evidence"]),
                         ("principle", 3, "verified", []))
        self.assertEqual((back["v0"]["level"], back["v0"]["scenarios"], back["v0"]["strength"]), ("rule", ["any"], "should"))


def rule(title, session, n, themes, status="candidate", sessions=None):
    l = lesson(title, session, n, themes=themes)
    for i, s2 in enumerate(sessions or []):
        l["evidence"].append({"ref": f"/h/{s2}.jsonl:{n + i + 1}", "source": "claude-code", "date": "2026-09-02",
                              "quote": "q", "session": s2})
    l["trigger"] = title.lower()
    return dream._finish(l)


def scripted(abstract_answer):
    def respond(prompt):
        if prompt.startswith(dream.ABSTRACT_MARK):
            return json.dumps(abstract_answer(prompt) if callable(abstract_answer) else abstract_answer)
        return dream.FakeEngine()(prompt)
    return respond


class TestAbstraction(Base):
    def setUp(self):
        super().setUp()
        os.makedirs(self.out)
        self.push = rule("Never force push to main", "s1", 1, ("git",))
        self.fig = rule("Render figures with embedded fonts", "s2", 2, ("figures",))
        self.cost = rule("Ask before launching paid evaluation runs", "s3", 3, ("cost",))

    def go(self, existing, cands, eng, **kw):
        return dream.consolidate(existing, cands, eng, out_dir=self.out, run_id=kw.pop("run_id", "r1"),
                                 log=lambda *x: None, **kw)

    def by_level(self):
        out = {}
        for l in dream.read_lessons(self.out):
            out.setdefault(l["level"], []).append(l)
        return out

    def test_principle_created_over_two_rules(self):
        eng = dream.FakeEngine()
        stats = self.go([], [self.push, self.fig], eng)
        self.assertEqual(eng.calls, 1)  # no merge call (different topics), one abstraction call
        lv = self.by_level()
        self.assertEqual(len(lv["principle"]), 1)
        p = lv["principle"][0]
        self.assertEqual({r["parent"] for r in lv["rule"]}, {p["id"]})
        self.assertEqual(p["evidence"], [])
        self.assertEqual((p["seen"], p["status"]), (2, "candidate"))
        self.assertEqual(stats["principles"], 1)
        with open(os.path.join(self.out, "CHANGELOG.md")) as fh:
            self.assertIn("- principle: Principle:", fh.read())

    def test_principle_verified_iff_two_verified_children(self):
        a = rule("Never force push to main", "s1", 1, ("git",), sessions=["s9"])
        b = rule("Render figures with embedded fonts", "s2", 2, ("figures",), sessions=["s8"])
        self.go([], [a, b, self.cost], dream.FakeEngine())
        p = self.by_level()["principle"][0]
        self.assertEqual(p["status"], "verified")
        a2 = rule("Never force push to main", "s1", 1, ("git",), sessions=["s9"])
        shutil.rmtree(self.out)
        os.makedirs(self.out)
        self.go([], [a2, self.fig, self.cost], dream.FakeEngine())
        self.assertEqual(self.by_level()["principle"][0]["status"], "candidate")

    def test_single_rule_never_gets_a_principle(self):
        eng = dream.FakeEngine()
        self.go([], [self.push], eng)
        self.assertEqual(eng.calls, 0)
        self.assertNotIn("principle", self.by_level())
        # even when the model proposes one over a single rule
        _, rl, pl = dream.abstract_prompt([self.push, self.fig], [], [self.push, self.fig], abstraction="medium",
                                         max_new=10)
        new, _, st = dream.apply_abstraction({"principles": [{"rules": ["R1"], "title": "P", "do": "d"}]}, rl, pl,
                                             [self.push, self.fig], max_new=10, taken=set())
        self.assertEqual((new, st["principles_rejected"]), ([], 1))
        self.assertEqual(self.push.get("parent"), "")

    def test_narrow_rules_become_instances_only_with_two_siblings(self):
        broad = rule("Pull before editing a synced repo", "s1", 1, ("git",))
        n1 = rule("Pull Overleaf before editing the paper", "s2", 2, ("git",))
        n2 = rule("Pull the jobs branch before editing state", "s3", 3, ("git",))
        for i, l in enumerate((broad, n1, n2)):
            l["id"] = f"x{i}"
        both = {"instances": [{"rule": "R2", "under": "R1"}, {"rule": "R3", "under": "R1"}]}
        _, rl, pl = dream.abstract_prompt([broad, n1, n2], [], [broad, n1, n2], abstraction="medium", max_new=10)
        _, log, st = dream.apply_abstraction(both, rl, pl, [broad, n1, n2], max_new=10, taken=set())
        self.assertEqual(st["filed_as_instance"], 2)
        self.assertEqual([(l["level"], l["parent"]) for l in (n1, n2)], [("instance", "x0"), ("instance", "x0")])
        self.assertTrue(any("rule → instance" in x for x in log))
        a, b = rule("Pull before editing a synced repo", "s1", 1, ("git",)), rule("Pull Overleaf first", "s2", 2, ("git",))
        a["id"], b["id"] = "a", "b"
        _, rl, pl = dream.abstract_prompt([a, b], [], [a, b], abstraction="medium", max_new=10)
        _, _, st = dream.apply_abstraction({"instances": [{"rule": "R2", "under": "R1"}]}, rl, pl, [a, b],
                                           max_new=10, taken=set())
        self.assertEqual((st["filed_as_instance"], b["level"]), (0, "rule"))

    def test_max_principles_caps_and_prefers_larger(self):
        d = rule("Report numbers from the source table", "s4", 4, ("reporting",))
        e = rule("Keep status replies short", "s5", 5, ("brevity",))
        ans = {"principles": [{"rules": ["R1", "R2"], "title": "Small one", "do": "d"},
                              {"rules": ["R3", "R4", "R5"], "title": "Big one", "do": "d"}]}
        eng = dream.FakeEngine(scripted(ans))
        stats = self.go([], [self.push, self.fig, self.cost, d, e], eng, max_principles=1)
        ps = self.by_level()["principle"]
        self.assertEqual([p["title"] for p in ps], ["Big one"])
        self.assertEqual(stats["principles_rejected"], 1)

    def test_incremental_attach_to_existing_and_dissolve_keeps_evidence(self):
        self.go([], [self.push, self.fig], dream.FakeEngine())
        before = dream.read_lessons(self.out)
        pid = next(l["id"] for l in before if l["level"] == "principle")
        seen_prompts = []

        def attach(prompt):
            seen_prompts.append(prompt)
            return {"principles": [{"use": "P1", "rules": ["R1"]}]}
        eng = dream.FakeEngine(scripted(attach))
        self.go(before, [self.cost], eng, run_id="r2")
        self.assertIn("### P1: Principle:", seen_prompts[0])
        self.assertNotIn("Never force push", seen_prompts[0].split("Rules without a principle yet:")[1])
        after = dream.read_lessons(self.out)
        self.assertEqual(sum(l["level"] == "principle" for l in after), 1)
        self.assertEqual(sum(l.get("parent") == pid for l in after), 3)
        # nothing new: no abstraction call
        eng3 = dream.FakeEngine()
        self.go(after, [], eng3, run_id="r3")
        self.assertEqual(eng3.calls, 0)
        # two of three rules refuted -> the principle dissolves, its rules keep all evidence
        lessons = dream.read_lessons(self.out)
        for l in lessons:
            if l["title"] in ("Never force push to main", "Render figures with embedded fonts"):
                l["status"] = "refuted"
        n_ev = sum(len(l["evidence"]) for l in lessons)
        cand = rule("Write the abstract last", "s7", 7, ("writing",))
        self.go(lessons, [cand], dream.FakeEngine(scripted({"principles": []})), run_id="r4")
        final = dream.read_lessons(self.out)
        self.assertFalse(any(l["level"] == "principle" for l in final))
        self.assertFalse(any(l.get("parent") for l in final))
        self.assertEqual(sum(len(l["evidence"]) for l in final), n_ev + 1)
        with open(os.path.join(self.out, "CHANGELOG.md")) as fh:
            self.assertIn("dissolved principle", fh.read())

    def test_merge_relinks_children(self):
        a, b = rule("Never force push to main", "s1", 1, ("git",)), rule("Never force push to main", "s2", 2, ("git",))
        a["id"], b["id"] = "push-a", "push-b"
        inst = rule("In the paper repo pull Overleaf first", "s3", 3, ("overleaf",))
        inst.update(id="inst", level="instance", parent="push-b")
        b["_new"] = True
        self.go([a, inst], [b], dream.FakeEngine(scripted({"principles": []})))
        back = {l["id"]: l for l in dream.read_lessons(self.out)}
        self.assertEqual(back["inst"]["parent"], "push-a")


class TestAbstractionParams(unittest.TestCase):
    def rules(self, n=20):
        topics = ["git push", "figure fonts", "paid runs", "status replies", "table numbers"]
        return [rule(f"{topics[i % 5]} rule {i}", f"s{i}", i, (topics[i % 5].split()[0],)) for i in range(n)]

    def test_higher_abstraction_fewer_bigger_groups(self):
        rs = self.rules()
        n = {lv: len(dream.abstraction_groups(rs, lv)) for lv in ("low", "medium", "high")}
        self.assertGreater(n["low"], n["medium"])
        self.assertGreaterEqual(n["medium"], n["high"])
        self.assertEqual(n["high"], 1)
        self.assertEqual(sum(len(g) for g in dream.abstraction_groups(rs, "low")), 20)

    def test_guidance_and_merge_threshold_follow_level(self):
        rs = self.rules(3)
        lo = dream.abstract_prompt(rs, [], rs, abstraction="low", max_new=4)[0]
        hi = dream.abstract_prompt(rs, [], rs, abstraction="high", max_new=4)[0]
        self.assertIn("Abstraction: low", lo)
        self.assertIn("Abstraction: high", hi)
        self.assertIn("at most 4 new principle", hi)
        self.assertLess(dream.ABSTRACTION["high"]["merge"], dream.ABSTRACTION["low"]["merge"])
        # a pair that only high abstraction sends to the merge pass
        a = rule("Never force push to main", "s1", 1, ("git", "push"))
        b = rule("Never push without asking", "s2", 2, ("git", "remote"))
        sim = dream.similarity(a, b)
        self.assertTrue(dream.ABSTRACTION["high"]["merge"] <= sim < dream.ABSTRACTION["low"]["merge"], sim)

    def test_cli_parses_new_flags(self):
        a = dream.build_parser().parse_args(["--abstraction", "high", "--max-principles", "3", "--scenarios", "coding"])
        self.assertEqual((a.abstraction, a.max_principles, a.scenarios, a.threshold), ("high", 3, "coding", None))


class TestIncrementalHierarchy(Base):
    def test_runs_build_tree_then_cost_nothing(self):
        self.write_shard([moment("s1", 5, "no, don't force push to main")])
        a = args(shards=[self.shard], out=self.out, home=self.home)
        dream.run(a, engine=dream.FakeEngine(), log=lambda *x: None)
        self.assertFalse(any(l["level"] == "principle" for l in dream.read_lessons(self.out)))
        with open(self.shard, "a") as fh:
            fh.write(json.dumps(moment("s2", 9, "stop, render the figures with embedded fonts",
                                       ts="2026-09-03T00:00:00Z")) + "\n")
        eng = dream.FakeEngine()
        r = dream.run(a, engine=eng, log=lambda *x: None)
        self.assertEqual(r["extract"]["moments_new"], 1)
        self.assertEqual(r["consolidate"]["principles"], 1)
        eng3 = dream.FakeEngine()
        dream.run(a, engine=eng3, log=lambda *x: None)
        self.assertEqual(eng3.calls, 0)
        st = State(os.path.join(self.home, "state.json"))
        self.assertEqual(st.data["runs"][-1]["abstraction"], "medium")


if __name__ == "__main__":
    unittest.main()
