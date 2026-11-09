# Lesson organisation schemes under comparison

All schemes start from the same evidence (moments in `work/shards2/`, the v0 library in `library/`, candidates in
`work/candidates/`) and write lesson files in the v1 format (`docs/LESSON_FORMAT.md`) plus scheme-specific fields.
Each writes only its own `library-<scheme>/` folder and creates `library-<scheme>/DONE` when finished, with
`SCHEME.md` describing the method and an `INDEX.md` (the injected layer, ≤ 80 lines). The evaluation
(`eval/`) compares them on held-out corrections.

| scheme | folder | idea |
|---|---|---|
| hierarchy | `library-v2/` | principle → rule → instance by judgement, scenarios, strength |
| cluster | `library-cluster/` | cluster *moments* first (bottom-up), one lesson per cluster prototype, hierarchy from the cluster tree |
| relations | `library-graph/` | lessons as condition → action rules with typed relations (refines, conflicts-with, requires, trade-off) and explicit conflict resolution |
| profile | `library-profile/` | a small model of the person (values/traits, 5–8) from which rules are derived; rules cite the trait they serve |
| playbook | `library-playbook/` | per-scenario playbooks: ordered checklists an agent runs at the start/end of a scenario |

Relation types (any scheme may use them): `refines` (narrower version of), `generalises`, `conflicts-with`
(with a `resolution`: which wins and when), `requires` (do A before B applies), `trade-off` (both matter; how to
balance). Stored as `relations: [{type, with, resolution?}]` in frontmatter.
