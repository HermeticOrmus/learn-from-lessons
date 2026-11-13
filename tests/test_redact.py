import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lessons.redact import redact  # noqa: E402

# Fake secrets are built from parts so no token-shaped string sits in the file.
X = "x" * 24
HEX = "a1" * 20


class KeptPatternsTest(unittest.TestCase):
    def test_existing_shapes(self):
        cases = {
            "sk-" + "ant-" + X: "[key]",
            "ghp_" + X: "[github-token]",
            "xox" + "b-" + "1234567890-" + X: "[slack-token]",
            "AKIA" + "ABCDEFGHIJKLMNOP": "[aws-key]",
            "eyJ" + X + "." + X + "." + X: "[jwt]",
        }
        for secret, mark in cases.items():
            self.assertEqual(redact(f"use {secret} here"), f"use {mark} here")
        self.assertEqual(redact("password = hunter2hunter2"), "password = [redacted]")


class NewPatternsTest(unittest.TestCase):
    def test_password_in_a_url(self):
        self.assertEqual(redact("postgres://app:S3cr3tPass@db.example.com:5432/x"),
                         "postgres://app:[redacted]@db.example.com:5432/x")

    def test_service_tokens(self):
        cases = {
            "sbp_" + HEX: "[supabase-token]",
            "sb_" + "secret_" + X: "[supabase-token]",
            "sk_" + "live_" + X: "[stripe-key]",
            "rk_" + "test_" + X: "[stripe-key]",
            "AIza" + "B" * 35: "[google-key]",
            "glpat-" + X: "[gitlab-token]",
            "xai-" + X + X: "[key]",
            "npm_" + "A" * 36: "[npm-token]",
            "tskey-" + "auth-" + X: "[tailscale-key]",
        }
        for secret, mark in cases.items():
            self.assertEqual(redact(f"token {secret} ok"), f"token {mark} ok", secret[:8])

    def test_bearer_header(self):
        self.assertEqual(redact("-H 'Authorization: Bearer " + X + "'"), "-H 'Authorization: Bearer [redacted]'")

    def test_env_style_names(self):
        self.assertEqual(redact("SUPABASE_SERVICE_ROLE_KEY=" + X), "SUPABASE_SERVICE_ROLE_KEY=[redacted]")
        self.assertEqual(redact("export DB_PASSWORD: " + X), "export DB_PASSWORD: [redacted]")

    def test_email_addresses(self):
        self.assertEqual(redact("send it to jane.doe+x@client-mail.example"), "send it to [email]")
        self.assertEqual(redact("git@github.com:owner/repo.git"), "git@github.com:owner/repo.git")

    def test_near_misses_stay(self):
        for text in ("see https://example.com/a/b", "the key point is speed", "TOKEN_COUNT is 12",
                     "sbp_short", "a skill-name-with-dashes", "Bearer of bad news"):
            self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
