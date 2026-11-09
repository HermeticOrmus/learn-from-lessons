"""Shuffle every (case, condition) answer into blind judge files: the judge sees the case, the criterion and one
answer, never the condition or the notes block.   python3 -m eval.v3.judge_pack"""
import glob, hashlib, json, os, random
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "work", "eval", "v3")
JUDGES = 4

def main():
    cmap = json.load(open(os.path.join(OUT, "case-map.json")))
    items = {json.load(open(p))["id"]: json.load(open(p)) for p in glob.glob(os.path.join(OUT, "items", "*.json"))}
    rows, key = [], {}
    for path in glob.glob(os.path.join(OUT, "answers", "*", "slice-*.json")):
        cond = path.split(os.sep)[-2]
        for a in json.load(open(path)):
            item = items[cmap[a["case"]]]
            aid = "a" + hashlib.sha256(f"{cond}:{a['case']}".encode()).hexdigest()[:10]
            key[aid] = {"condition": cond, "item": item["id"]}
            rows.append({"answer_id": aid, "situation": item["situation"], "user_message": item["user_message"],
                         "criterion": item["criterion"], "actions": a.get("actions", []), "reply": a.get("reply", "")})
    random.Random(11).shuffle(rows)
    d = os.path.join(OUT, "judge")
    os.makedirs(d, exist_ok=True)
    for k in range(JUDGES):
        json.dump(rows[k::JUDGES], open(os.path.join(d, f"batch-{k}.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(key, open(os.path.join(OUT, "answer-key.json"), "w"), indent=1)
    print(len(rows), "answers ->", JUDGES, "judge batches")

if __name__ == "__main__":
    main()
