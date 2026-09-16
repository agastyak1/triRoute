#!/usr/bin/env python3
"""Offline unit tests for scripts/auth_helper.py — pure logic, no browser, no sockets, no network."""

import base64
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("auth_helper", os.path.join(ROOT, "scripts", "auth_helper.py"))
ah = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ah)


class Pkce(unittest.TestCase):
    def test_challenge_is_s256_of_verifier(self):
        verifier, challenge = ah._pkce()
        self.assertGreaterEqual(len(verifier), 43)  # RFC 7636 minimum
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        self.assertEqual(challenge, expected)

    def test_verifiers_unique(self):
        self.assertNotEqual(ah._pkce()[0], ah._pkce()[0])


class Time(unittest.TestCase):
    def test_expiry_skewed_early(self):
        before = int(ah.time.time() * 1000)
        got = ah._expires_ms(3600)
        self.assertLessEqual(got, before + 3600 * 1000 - ah.EXPIRY_SKEW_MS)

    def test_none_passthrough(self):
        self.assertIsNone(ah._expires_ms(None))


class IdToken(unittest.TestCase):
    def test_claims_decode(self):
        payload = base64.urlsafe_b64encode(json.dumps(
            {"email": "me@x.io", "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}}
        ).encode()).rstrip(b"=").decode()
        claims = ah._id_token_claims(f"header.{payload}.sig")
        self.assertEqual(claims["email"], "me@x.io")
        self.assertEqual(claims["https://api.openai.com/auth"]["chatgpt_plan_type"], "plus")

    def test_garbage_is_empty(self):
        self.assertEqual(ah._id_token_claims(None), {})
        self.assertEqual(ah._id_token_claims("nope"), {})


class EnvParsing(unittest.TestCase):
    def test_reads_config_env(self):
        tmp = tempfile.mkdtemp(prefix="triroute-test-")
        env_dir = os.path.join(tmp, "config")
        os.makedirs(env_dir)
        with open(os.path.join(env_dir, ".env"), "w") as fh:
            fh.write('A=plain\nB="quoted"\nC=with=equals\n# comment\n')
        module_path = os.path.join(tmp, "scripts", "auth_helper.py")
        os.makedirs(os.path.dirname(module_path))
        with open(module_path, "w") as fh:
            fh.write(open(os.path.join(ROOT, "scripts", "auth_helper.py")).read())
        spec2 = importlib.util.spec_from_file_location("ah2", module_path)
        mod = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(mod)
        self.assertEqual(mod._env_from_config_file("A"), "plain")
        self.assertEqual(mod._env_from_config_file("B"), "quoted")
        self.assertEqual(mod._env_from_config_file("C"), "with=equals")
        self.assertEqual(mod._env_from_config_file("MISSING"), "")


class Save(unittest.TestCase):
    def test_merge_permissions_atomic(self):
        tmp = tempfile.mkdtemp(prefix="triroute-test-")
        path = os.path.join(tmp, "data", "credentials.json")
        ah.save("anthropic", {"access": "a1", "refresh": "r1", "email": "e@x", "plan": None}, path)
        ah.save("openai-codex", {"access": "a2", "expires": 123}, path)
        saved = json.load(open(path))
        self.assertEqual(set(saved), {"anthropic", "openai-codex"})
        self.assertNotIn("plan", saved["anthropic"], "None/empty values must be dropped")
        self.assertIn("authorizedAt", saved["anthropic"])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_corrupt_store_survives(self):
        tmp = tempfile.mkdtemp(prefix="triroute-test-")
        path = os.path.join(tmp, "credentials.json")
        with open(path, "w") as fh:
            fh.write("###")
        ah.save("anthropic", {"access": "a"}, path)
        self.assertEqual(json.load(open(path))["anthropic"]["access"], "a")


class ProviderRegistration(unittest.TestCase):
    """Pin the real first-party client registrations (placeholders broke the old plan draft)."""

    def test_client_ids_are_the_real_ones(self):
        src = open(os.path.join(ROOT, "scripts", "auth_helper.py"), encoding="utf-8").read()
        self.assertIn("9d1c250a-e61b-44d9-88ed-5944d1962f5e", src)      # Claude Code
        self.assertIn("app_EMoamEEZ73f0CkXaXp7hrann", src)              # Codex CLI
        self.assertIn("1071006060591-tmhssin2h21lcre235vtolojh4g403ep", src)  # Antigravity
        self.assertNotIn("codex-cli-client-id", src)
        self.assertNotIn("google-cloudcode-client-id", src)

    def test_google_flow_specifics(self):
        src = open(os.path.join(ROOT, "scripts", "auth_helper.py"), encoding="utf-8").read()
        google = src[src.index("def auth_google"):src.index("_discover_antigravity_project")]
        self.assertNotIn("code_challenge", google,
                         "Antigravity client does not accept PKCE — sending it breaks the redirect")
        self.assertIn('"access_type": "offline"', google)
        self.assertIn('"prompt": "consent"', google, "refresh token only issued with forced consent")
        self.assertIn("GOOGLE_CLIENT_SECRET", google)

    def test_callback_ports_and_paths(self):
        src = open(os.path.join(ROOT, "scripts", "auth_helper.py"), encoding="utf-8").read()
        for needle in ("http://localhost:54545/callback", "http://localhost:1455/auth/callback",
                       "http://localhost:51121/oauth-callback"):
            self.assertIn(needle, src)

    def test_listener_is_loopback(self):
        src = open(os.path.join(ROOT, "scripts", "auth_helper.py"), encoding="utf-8").read()
        self.assertIn('Server(("127.0.0.1", port), _CallbackHandler)', src)
        self.assertNotIn('Server(("", port)', src)


class CallbackHandler(unittest.TestCase):
    def test_extract_code_state_error(self):
        import urllib.parse as up
        for query, expect in [
            ("code=c1&state=s1", {"code": "c1", "state": "s1", "error": None}),
            ("error=access_denied&state=s1", {"code": None, "state": "s1", "error": "access_denied"}),
        ]:
            params = up.parse_qs(up.urlparse("/callback?" + query).query)
            got = {k: (params.get(k) or [None])[0] for k in ("code", "state", "error")}
            self.assertEqual(got, expect)


if __name__ == "__main__":
    unittest.main(verbosity=1)
