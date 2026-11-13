"""Remove secrets before anything leaves the machine or is written to a lesson."""
import re

PATTERNS = [
    (re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-|or-|proj-)?[A-Za-z0-9_\-]{16,}"), "[key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "[github-token]"),
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+"), "[slack-webhook]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[slack-token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws-key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[jwt]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[private-key]"),
    (re.compile(r"\b(?:sbp_[a-f0-9]{40}|sb_secret_[A-Za-z0-9_-]{20,})"), "[supabase-token]"),
    (re.compile(r"\b(?:[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|whsec_[A-Za-z0-9]{24,})"), "[stripe-key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "[google-key]"),
    (re.compile(r"\b(?:ya29\.|GOCSPX-)[A-Za-z0-9_-]{20,}"), "[google-token]"),
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"), "[sendgrid-key]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}"), "[huggingface-token]"),
    (re.compile(r"\bSK[0-9a-f]{32}\b"), "[twilio-key]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "[gitlab-token]"),
    (re.compile(r"\bxai-[A-Za-z0-9]{40,}"), "[key]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), "[npm-token]"),
    (re.compile(r"\btskey-[a-z]+-[A-Za-z0-9-]{10,}"), "[tailscale-key]"),
    # the password runs to the last @ before the path, so a password with @ in it is covered
    (re.compile(r"\b([a-z][a-z0-9+.-]{0,30}://[^:/\s@]*:)[^\s/]*@"), r"\1[redacted]@"),
    (re.compile(r"(?i)\b(Authorization:\s*Basic\s+)[A-Za-z0-9+/]{8,}={0,2}"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(Bearer\s+)(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{20,}"), r"\1[redacted]"),
    (re.compile(r"(?i)([\"'])([A-Za-z0-9_-]*(?:api[_-]?key|secret|token|password|passwd))\1(\s*:\s*)([\"'])[^\"'\n]{6,}\4"),
     r"\1\2\1\3\4[redacted]\4"),
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key))(\s*[:=]\s*)([\"'])[^\"'\n]{6,}\3"),
     r"\1\2\3[redacted]\3"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b(\s*[:=]\s*)(?![\"'\[$/(])(\S{6,})"), r"\1\2[redacted]"),
    # NAME_KEY=value; skip what is code, a path, a variable or a number, not a secret
    (re.compile(r"\b([A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD))(\s*=\s*)(?![/$\[(\"'])(?!\d+\b)[^\s\"']{6,}"),
     r"\1\2[redacted]"),
    (re.compile(r"(?m)^([ \t]*(?:-[ \t]*)?[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD):[ \t]+)(?=[^\s\[\]();=,]*\d)"
                r"[^\s\[\]();=,]{6,}$"), r"\1[redacted]"),
    (re.compile(r"(?<![\w.+-])(?!git@)[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), "[email]"),
]


def redact(text: str) -> str:
    for pattern, repl in PATTERNS:
        text = pattern.sub(repl, text)
    return text
