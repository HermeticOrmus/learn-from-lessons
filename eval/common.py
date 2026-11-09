"""Shared helpers for the memory-scheme evaluation: paths, moments, rendering, cached model calls with cost log."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lessons.redact import redact  # noqa: E402
from lessons.dream import tokens_estimate, parse_json, shard_moments  # noqa: E402

WORK = os.path.join(ROOT, "work", "eval")
CACHE = os.path.join(WORK, "cache")
CALLS = os.path.join(WORK, "calls.jsonl")
os.makedirs(CACHE, exist_ok=True)
_lock = threading.Lock()
BUDGET_USD = float(os.environ.get("EVAL_BUDGET_USD", "25"))

LIMITS = {"user": 900, "assistant": 900, "tool": 300}


def load_moments() -> list[dict]:
    return shard_moments([os.path.join(ROOT, "work", "shards2", "*.jsonl")])


def mid(m: dict) -> str:
    t = m.get("trigger") or {}
    return hashlib.sha1(f"{m.get('session')}|{t.get('ref')}|{(t.get('text') or '')[:200]}".encode()).hexdigest()[:12]


def clip(text: str, n: int) -> str:
    text = redact(text or "").strip()
    return text if len(text) <= n else text[:n] + " …"


def render_context(turns: list[dict], n: int = 6) -> str:
    lines = []
    for t in turns[-n:]:
        role = t.get("role", "")
        label = "USER" if role == "user" else role.upper()
        if t.get("tool"):
            label += f" ({t['tool']})"
        if t.get("error"):
            label += " ERROR"
        lines.append(f"{label}: {clip(t.get('text', ''), LIMITS.get(role, 300)) or '(empty)'}")
    return "\n".join(lines)


def spent() -> float:
    if not os.path.exists(CALLS):
        return 0.0
    with open(CALLS) as fh:
        return sum(json.loads(l).get("cost_usd", 0) for l in fh if l.strip())


def call(prompt: str, *, system: str, model: str, tag: str, timeout: int = 300, effort: str | None = None) -> str:
    """One lean `claude -p` call (no tools, MCP, skills or default system prompt), cached by content hash.
    Every uncached call appends {tag, model, cost} to calls.jsonl."""
    key = hashlib.sha256(f"{model}\x1f{system}\x1f{prompt}".encode() + (f"\x1f{effort}".encode() if effort else b"")).hexdigest()[:24]
    path = os.path.join(CACHE, key + ".json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)["result"]
    if spent() >= BUDGET_USD:
        raise RuntimeError(f"budget ${BUDGET_USD} reached")
    cmd = ["claude", "-p", "--output-format", "json", "--no-session-persistence", "--tools", "",
           "--strict-mcp-config", "--disable-slash-commands", "--system-prompt", system, "--model", model] + (["--effort", effort] if effort else [])
    last = None
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix="lessons-eval-") as cwd:
            try:
                p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
                d = json.loads(p.stdout)
                if isinstance(d, list):
                    d = next((x for x in reversed(d) if isinstance(x, dict) and x.get("type") == "result"), {})
                if d.get("is_error"):
                    raise RuntimeError(str(d.get("result"))[:300])
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError(f"claude call failed: {last}")
    cost = float(d.get("total_cost_usd") or 0)
    u = d.get("usage") or {}
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, "model": model, "cost_usd": cost,
           "in": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
           + (u.get("cache_read_input_tokens") or 0), "out": u.get("output_tokens") or 0}
    with _lock:
        with open(CALLS, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        with open(path, "w") as fh:
            json.dump({"result": str(d.get("result", "")), "cost_usd": cost}, fh)
    return str(d.get("result", ""))


def jload(name: str):
    with open(os.path.join(WORK, name)) as fh:
        return json.load(fh)


def jsave(name: str, obj) -> None:
    with open(os.path.join(WORK, name), "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
