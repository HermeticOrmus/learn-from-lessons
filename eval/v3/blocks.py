"""Build the memory block each condition gives the agent, and the per-condition run files (no model calls).
    python3 -m eval.v3.blocks   ->  work/eval/v3/runs/<condition>/slice-<k>.json, work/eval/v3/matched.json"""
import glob, hashlib, json, os, random
from lessons import store
from lessons.scenario import infer_scenario

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
LIB = os.path.join(ROOT, "library-v2", "lessons")
OUT = os.path.join(ROOT, "work", "eval", "v3")
SLICES = 3


def anon(item_id: str) -> str:
    """Case ids the agent and judge see: they must not reveal the lesson or the kind (apply/control)."""
    return "c" + hashlib.sha256(("v3:" + item_id).encode()).hexdigest()[:8]


def line(l: dict) -> str:
    body = (l.get("body") or "")
    do = body.split("**Do:**", 1)[1].split("**Why:**", 1)[0].strip() if "**Do:**" in body else ""
    return f"- {l['title']}\n  When: {l.get('trigger','')}\n  Do: {do}".rstrip()


def main() -> None:
    lessons = store.load(LIB)
    byid = {l["id"]: l for l in lessons}
    principles = [l for l in lessons if l.get("level") == "principle"]
    rules = [l for l in lessons if l.get("level") == "rule" and l.get("status") == "verified"]
    head = "Notes from earlier sessions with this person (follow them unless the person says otherwise):\n"
    pblock = "\n".join(line(p) for p in principles)
    items = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(OUT, "items", "*.json")))]
    random.Random(7).shuffle(items)
    matched_log = {}
    blocks = {"none": {}, "all": {}, "matched": {}, "oracle": {}}
    for it in items:
        ctx = f"{it['situation']}\n{it['user_message']}"
        scen = infer_scenario(ctx)
        top = scen[0][0] if scen else None
        hits = store.search(ctx, k=5, lessons=lessons, scenario=top, level_max="rule")
        hit_ids = [h["id"] for h in hits if h.get("level") != "principle"][:5]
        matched_log[it["id"]] = {"scenario": top, "hits": hit_ids, "target_found": it["lesson_id"] in hit_ids}
        blocks["none"][it["id"]] = ""
        blocks["all"][it["id"]] = head + pblock + "\n" + "\n".join(line(r) for r in rules)
        blocks["matched"][it["id"]] = head + pblock + "\n" + "\n".join(line(byid[i]) for i in hit_ids if i in byid)
        blocks["oracle"][it["id"]] = head + pblock + "\n" + line(byid[it["lesson_id"]])
    for cond, mem in blocks.items():
        d = os.path.join(OUT, "runs", cond)
        os.makedirs(d, exist_ok=True)
        for k in range(SLICES):
            part = items[k::SLICES]
            cases = [{"case": anon(it["id"]), "situation": it["situation"], "tools": it["tools"],
                      "user_message": it["user_message"], "notes_block": mem[it["id"]]} for it in part]
            json.dump(cases, open(os.path.join(d, f"slice-{k}.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(matched_log, open(os.path.join(OUT, "matched.json"), "w"), indent=1)
    json.dump({anon(it["id"]): it["id"] for it in items}, open(os.path.join(OUT, "case-map.json"), "w"), indent=1)
    found = sum(v["target_found"] for v in matched_log.values() if "control" not in v and True)
    apply_ids = [i for i in matched_log if "--apply" in i]
    print(f"items {len(items)}; matched recall on apply items: {sum(matched_log[i]['target_found'] for i in apply_ids)}/{len(apply_ids)}")
    print("tokens≈ all", len(blocks["all"][items[0]["id"]]) // 4, "matched", sum(len(v) for v in blocks["matched"].values()) // 4 // max(1, len(items)),
          "oracle", sum(len(v) for v in blocks["oracle"].values()) // 4 // max(1, len(items)))


if __name__ == "__main__":
    main()
