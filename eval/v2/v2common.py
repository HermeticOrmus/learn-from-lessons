"""v2 glue: reuse eval/common.py (redaction, cached `claude -p` calls) with a separate v2 cost log and budget."""
from __future__ import annotations
import difflib, json, os, re, sys

V1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V1)
os.environ.setdefault("EVAL_BUDGET_USD", "15")
import common  # noqa: E402

V2WORK = os.path.join(common.WORK, "v2")
os.makedirs(V2WORK, exist_ok=True)
common.CALLS = os.path.join(V2WORK, "calls.jsonl")  # v2 cost log; cache dir stays shared (hits are free)
common.BUDGET_USD = float(os.environ["EVAL_BUDGET_USD"])
from common import call, clip, parse_json, render_context, spent, tokens_estimate, mid  # noqa: E402,F401

C1, C2 = "2026-09-28", "2026-09-04"


def load(name):
    with open(os.path.join(V2WORK, name)) as fh:
        return json.load(fh)


def save(name, obj):
    with open(os.path.join(V2WORK, name), "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def split_context(m):
    """(prefix shown to the agent, original agent action). The action = last assistant entry and anything after it,
    unless the context ends with a user entry (then the agent sees everything and there is no shown action)."""
    ctx = m.get("context") or []
    if ctx and ctx[-1].get("role") == "user":
        return ctx, []
    idx = max((i for i, t in enumerate(ctx) if t.get("role") == "assistant"), default=None)
    if idx is None:
        return ctx, []
    return ctx[:idx], ctx[idx:]


REJECT = re.compile(r"(doesn't want to proceed with this tool use|tool use was rejected|Request interrupted by user|"
                    r"user (has )?(rejected|denied)|\[Request interrupted)", re.I)
NONHUMAN = re.compile(r"(approval assessment|Codex agent history|^\s*\[\d+\] tool |<(system|environment)[-_ ]|"
                      r"^\s*(\{|\[)\s*\")", re.I)


def _norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def leak_reasons(m) -> list[str]:
    """Automatic leak check (REVIEW-v1 §1): the shown context must not contain the pushback / follow-up text or a
    tool-rejection marker, and the trigger must be a human message."""
    shown, action = split_context(m)
    why = []
    texts = [_norm(t.get("text")) for t in (m.get("context") or [])]
    probes = [m.get("pushback", "")] + [t.get("text", "") for t in m.get("followup") or [] if t.get("role") == "user"]
    for p in probes:
        p = _norm(p)
        if len(p) >= 8 and any(p[:40] in t or (len(t) >= 8 and t in p and len(t) >= 0.5 * len(p))
                               or difflib.SequenceMatcher(None, p[:200], t[:200]).ratio() >= 0.7 for t in texts):
            why.append("pushback-in-context")
            break
    pbn = _norm(m.get("pushback"))
    users = [_norm(t.get("text")) for t in (m.get("context") or []) if t.get("role") == "user"]
    if any(len(u) >= 3 and pbn.startswith(u) for u in users) and "pushback-in-context" not in why:
        why.append("queued-prefix-in-context")
    if any(NONHUMAN.search(t.get("text") or "") for t in (m.get("context") or []) if t.get("role") == "user"):
        why.append("non-human-context")
    if any(REJECT.search(t.get("text") or "") for t in m.get("context") or []):
        why.append("tool-rejection-marker")
    pb = m.get("pushback") or ""
    if NONHUMAN.search(pb[:300]) or len(pb) > 3000:
        why.append("non-human-trigger")
    return why
