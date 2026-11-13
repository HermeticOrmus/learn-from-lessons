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
        self.assertEqual(redact("-H 'Authorization: Bearer " + X + "42'"), "-H 'Authorization: Bearer [redacted]'")

    def test_env_style_names(self):
        self.assertEqual(redact("SUPABASE_SERVICE_ROLE_KEY=" + X), "SUPABASE_SERVICE_ROLE_KEY=[redacted]")
        self.assertEqual(redact("  DB_PASSWORD: s3cr3tv4lue"), "  DB_PASSWORD: [redacted]")  # YAML, line start

    def test_email_addresses(self):
        self.assertEqual(redact("send it to jane.doe+x@client-mail.example"), "send it to [email]")
        self.assertEqual(redact("git@github.com:owner/repo.git"), "git@github.com:owner/repo.git")

    def test_near_misses_stay(self):
        for text in ("see https://example.com/a/b", "the key point is speed", "TOKEN_COUNT is 12",
                     "sbp_short", "a skill-name-with-dashes", "Bearer of bad news"):
            self.assertEqual(redact(text), text)



class ReviewFindingsTest(unittest.TestCase):
    """Cases from the independent review of the redaction PR."""

    def test_package_pins_are_not_emails(self):
        for text in ("yarn add react@18.2.0 left-pad@1.3.0", "pip install foo==1.0 bar@2.0.1", "ssh u@10.0.0.5"):
            self.assertEqual(redact(text), text)
        self.assertEqual(redact("mail ops@example.co.uk now"), "mail [email] now")

    def test_url_passwords_without_a_user_or_with_an_at_sign(self):
        self.assertEqual(redact("redis://:hunter2hunter@cache:6379/0"), "redis://:[redacted]@cache:6379/0")
        self.assertEqual(redact("postgres://u:p@ss@host/db"), "postgres://u:[redacted]@host/db")
        self.assertEqual(redact("http://host:8080/a@b"), "http://host:8080/a@b")

    def test_json_and_quoted_values(self):
        self.assertEqual(redact('{"API_KEY": "abcdefghijk"}'), '{"API_KEY": "[redacted]"}')
        self.assertEqual(redact('{"token":"abcdefghijklmnop"}'), '{"token":"[redacted]"}')
        self.assertEqual(redact('password = "my secret pass"'), 'password = "[redacted]"')
        self.assertEqual(redact('{"tokens": 12, "max_tokens": "4096"}'), '{"tokens": 12, "max_tokens": "4096"}')

    def test_code_is_not_redacted(self):
        for text in ("ACCESS_TOKEN: Optional[str] = None", "KEY: string;", "MAX_TOKEN=100000",
                     "SSH_KEY=/home/u/.ssh/id_rsa", "API_KEY=$API_KEY", "GH_TOKEN=$(cat file)"):
            self.assertEqual(redact(text), text)
        self.assertEqual(redact("export DB_PASSWORD=" + X), "export DB_PASSWORD=[redacted]")

    def test_more_service_formats(self):
        cases = {
            "https://hooks.slack.com/services/T" + "0" * 8 + "/B" + "0" * 8 + "/" + X: "[slack-webhook]",
            "ya29." + X + X: "[google-token]",
            "GOCSPX-" + X: "[google-token]",
            "SG." + X[:22] + "." + X + X[:19]: "[sendgrid-key]",
            "hf_" + "A" * 34: "[huggingface-token]",
            "whsec_" + X + X: "[stripe-key]",
            "SK" + "0123456789abcdef" * 2: "[twilio-key]",
        }
        for secret, mark in cases.items():
            self.assertEqual(redact(f"use {secret} now"), f"use {mark} now", secret[:8])
        self.assertEqual(redact("Authorization: Basic " + "dXNlcjpwYXNzd29yZA=="), "Authorization: Basic [redacted]")

    def test_bearer_needs_a_token_shape(self):
        self.assertEqual(redact("Bearer authentication-middleware-handler"), "Bearer authentication-middleware-handler")

    def test_long_text_stays_fast(self):
        import time
        for text in ("a.a." * 10000, "A" * 40000, "x://:" + "a" * 40000):
            t = time.perf_counter()
            redact(text)
            self.assertLess(time.perf_counter() - t, 0.15, text[:6])

    def test_sk_inside_a_word_stays(self):
        for text in ("a risk-assessment-review-board note", "the disk-usage-report-generator"):
            self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
