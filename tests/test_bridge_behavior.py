#!/usr/bin/env python3
"""TriRoute bridge behavior tests — no network, no Docker, host-safe.

Uses the same AST-extraction pattern as the upstream contract tests: the
forked bridge is parsed, the tested functions/classes are exec'd into a
controlled namespace with stubbed urllib + temp-file credential store, and
behavior is asserted directly. Guards the TriRoute-specific logic:
  - fail-closed master-key predicate (TG-004)
  - alias canonicalization (claude-gpt -> gpt-5.6-terra ...)
  - race-safe rotating refresh (adopt external rotation, no double-spend)
  - atomic merged credential persistence (dashboard-compatible schema)
  - loud not-authenticated failures (no silent fall-through to dummy keys)
  - thoughtSignature SQLite persistence
  - config/compose/launcher invariants
"""

import ast
import contextlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(ROOT, "litellm-plugin", "sitecustomize.py")

EXTRACT_FUNCS = {
    "_master_key_ok", "_canonical_model", "_is_codex_model", "_is_gemini_model",
    "_read_credentials_file", "_load_credentials_file", "_lock_credentials_file",
    "_unlock_credentials_file", "_persist_tokens_to_secret", "TokenManager",
    "_require_codex_token", "_require_google_token", "_fresh_anthropic_or_die",
    "_open_sig_db", "_remember_thought_signature",
}
EXTRACT_ASSIGNS = {
    "TRIROUTE_ALIAS_BACKEND", "_INSECURE_MASTER_KEYS", "ANTHROPIC_OAUTH_SENTINEL",
    "CREDENTIALS_FILE", "ANTHROPIC_CLIENT_ID", "CODEX_CLIENT_ID", "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET", "_ENV_TO_PROVIDER", "_CREDENTIAL_LOCK_TIMEOUT",
    "_CREDENTIAL_LOCK_STALE", "_sig_db_lock",
}


def build_namespace(fake_urlopen=None):
    """Exec the extracted bridge pieces into an isolated, stubbed namespace."""
    source = open(BRIDGE, encoding="utf-8").read()
    tree = ast.parse(source)
    wanted = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in EXTRACT_FUNCS:
            wanted.append(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in EXTRACT_ASSIGNS:
                    wanted.append(node)
    missing = EXTRACT_FUNCS - {n.name for n in wanted if hasattr(n, "name")}
    assert not missing, f"bridge symbols renamed/removed: {missing}"

    request_mod = types.SimpleNamespace(
        Request=urllib.request.Request,
        urlopen=fake_urlopen or (lambda *a, **k: (_ for _ in ()).throw(AssertionError("network disabled"))),
    )
    fake_urllib = types.SimpleNamespace(parse=urllib.parse, request=request_mod)

    class _OSProxy:
        """Real filesystem, scrubbed environment (no host ANTHROPIC_*/OPENAI_*/GOOGLE_* leakage)."""
        def __init__(self):
            self.environ = {k: v for k, v in os.environ.items()
                            if not k.startswith(("ANTHROPIC_", "OPENAI_", "GOOGLE_", "LITELLM_"))}

        def __getattr__(self, name):
            return getattr(os, name)

    ns = {
        "json": json, "os": _OSProxy(), "time": time, "threading": threading,
        "tempfile": tempfile,
        "urllib": fake_urllib, "sqlite3": __import__("sqlite3"),
        "sys": types.SimpleNamespace(stderr=io.StringIO(), exit=sys.exit, _exit=os._exit),
        "ANTHROPIC_OAUTH_SENTINEL": "sk-ant-oat-triroute-managed",
    }
    # Module-level singletons the extracted functions close over.
    try:
        import fcntl as _fcntl_mod
        ns["_fcntl"] = _fcntl_mod
    except ImportError:
        ns["_fcntl"] = None
    ns["_cred_write_lock"] = threading.Lock()
    ns["_cred_mtime"] = 0.0
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "bridge_extract", "exec"), ns)
    return ns


class Calls:
    """Records fake-urlopen traffic and serves canned token responses."""
    def __init__(self, responses=None):
        self.requests = []
        self.responses = responses if responses is not None else {}

    def __call__(self, req, timeout=None, context=None):
        self.requests.append({
            "url": req.full_url,
            "body": req.data.decode() if req.data else "",
            "headers": {k.lower(): v for k, v in req.headers.items()},
        })
        payload = self.responses.get(req.full_url, {"access_token": "new-access", "expires_in": 3600})
        return _Resp(json.dumps(payload).encode())


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fresh_manager(ns, tmp, responses=None):
    calls = Calls(responses)
    ns["urllib"].request.urlopen = calls
    ns["CREDENTIALS_FILE"] = os.path.join(tmp, "credentials.json")
    ns["GOOGLE_CLIENT_SECRET"] = "test-google-secret"
    return calls


class MasterKeyGuard(unittest.TestCase):
    def test_rejects_known_defaults(self):
        ns = build_namespace()
        ok = ns["_master_key_ok"]
        for bad in ["", "sk-quota-gateway-master-key", "sk-change-me-use-a-random-value", "short1",
                    "sk-local-" + "a" * 20, "sk-local-has space-" + "a" * 24]:
            self.assertFalse(ok(bad), bad)
        self.assertTrue(ok("sk-local-" + "a" * 32))
        self.assertTrue(ok("sk-local-" + "a" * 48))


class Canonicalization(unittest.TestCase):
    def test_alias_mapping(self):
        ns = build_namespace()
        self.assertEqual(ns["_canonical_model"]("claude-gpt"), "gpt-5.6-terra")
        self.assertEqual(ns["_canonical_model"]("claude-gpt-fast"), "gpt-5.6-luna")
        self.assertEqual(ns["_canonical_model"]("claude-gemini-pro"), "gemini-2.5-pro")
        self.assertEqual(ns["_canonical_model"]("claude-gemini-flash"), "gemini-2.5-flash")
        self.assertEqual(ns["_canonical_model"]("openai/gpt-5.6-terra"), "openai/gpt-5.6-terra")
        self.assertEqual(ns["_canonical_model"]("claude-sonnet"), "claude-sonnet")

    def test_matchers_see_canonicalized_backends(self):
        ns = build_namespace()
        self.assertTrue(ns["_is_codex_model"](ns["_canonical_model"]("claude-gpt")))
        self.assertTrue(ns["_is_codex_model"](ns["_canonical_model"]("claude-gpt-fast")))
        self.assertTrue(ns["_is_gemini_model"](ns["_canonical_model"]("claude-gemini-pro")))
        self.assertFalse(ns["_is_codex_model"](ns["_canonical_model"]("claude-opus")))
        self.assertFalse(ns["_is_gemini_model"](ns["_canonical_model"]("claude-haiku")))


class CredentialPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="triroute-test-")
        self.ns = build_namespace()
        self.ns["CREDENTIALS_FILE"] = os.path.join(self.tmp, "sub", "credentials.json")

    def test_merge_preserves_others_atomic_and_600(self):
        ns = self.ns
        path = ns["CREDENTIALS_FILE"]
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            json.dump({"openai-codex": {"access": "keep-me", "refresh": "r0"}}, fh)
        ns["_persist_tokens_to_secret"]({
            "ANTHROPIC_OAUTH_TOKEN": "a1", "ANTHROPIC_REFRESH_TOKEN": "r1",
            "ANTHROPIC_EXPIRES_MS": int(time.time() * 1000) + 1000, "EMPTY": "",
        })
        saved = json.loads(open(path).read())
        self.assertEqual(saved["openai-codex"]["access"], "keep-me")
        self.assertEqual(saved["anthropic"]["access"], "a1")
        self.assertEqual(saved["anthropic"]["refresh"], "r1")
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_unreadable_file_treated_as_empty(self):
        ns = self.ns
        path = ns["CREDENTIALS_FILE"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(ns["_load_credentials_file"](), {})


class TokenManagerBehavior(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="triroute-test-")
        self.ns = build_namespace()
        self.cred_path = os.path.join(self.tmp, "credentials.json")
        self.ns["CREDENTIALS_FILE"] = self.cred_path

    def seed(self, data):
        with open(self.cred_path, "w") as fh:
            json.dump(data, fh)
        os.utime(self.cred_path, None)
        self.ns["_cred_mtime"] = 0.0  # force mtime resync

    def test_no_credentials_no_network(self):
        calls = fresh_manager(self.ns, self.tmp)
        tm = self.ns["TokenManager"]()
        self.assertEqual(tm.get_anthropic_token(), "")
        self.assertEqual(tm.get_codex_token(), "")
        self.assertEqual(tm.get_google_token(), "")
        self.assertEqual(calls.requests, [])

    def test_expired_anthropic_rotates_and_persists(self):
        self.seed({
            "anthropic": {"access": "old", "refresh": "rot-1", "expires": int(time.time() * 1000)},
            "openai-codex": {"access": "untouched", "refresh": "codex-r"},
        })
        calls = fresh_manager(self.ns, self.tmp, responses={
            "https://api.anthropic.com/v1/oauth/token": {
                "access_token": "new-access", "refresh_token": "rot-2", "expires_in": 3600}})
        tm = self.ns["TokenManager"]()
        self.assertEqual(tm.get_anthropic_token(), "new-access")
        self.assertEqual(len(calls.requests), 1)
        req = calls.requests[0]
        body = json.loads(req["body"])
        self.assertEqual(body["grant_type"], "refresh_token")
        self.assertEqual(body["refresh_token"], "rot-1")
        self.assertEqual(req["headers"].get("content-type"), "application/json")
        self.assertIn("oauth-2025-04-20", req["headers"].get("anthropic-beta", ""))
        saved = json.loads(open(self.cred_path).read())
        self.assertEqual(saved["anthropic"]["access"], "new-access")
        self.assertEqual(saved["anthropic"]["refresh"], "rot-2")  # rotated grant persisted
        self.assertGreater(saved["anthropic"]["expires"], time.time() * 1000)
        self.assertEqual(saved["openai-codex"]["access"], "untouched")
        # second call: fresh expiry -> no additional network spend
        self.assertEqual(tm.get_anthropic_token(), "new-access")
        self.assertEqual(len(calls.requests), 1)

    def test_adopts_external_rotation_without_double_spend(self):
        """The invalid_grant failure mode: another process rotated first."""
        self.seed({"anthropic": {"access": "old", "refresh": "rot-1", "expires": int(time.time() * 1000)}})
        calls = fresh_manager(self.ns, self.tmp)
        tm = self.ns["TokenManager"]()
        # dashboard/auth_helper writes the rotated pair concurrently
        external = {"anthropic": {"access": "external-new", "refresh": "rot-2",
                                  "expires": int(time.time() * 1000 + 3_000_000)}}
        tmp = self.cred_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(external, fh)
        future = time.time() + 1  # guarantee mtime moves even on coarse clocks
        os.utime(tmp, (future, future))
        os.replace(tmp, self.cred_path)
        self.assertEqual(tm.get_anthropic_token(), "external-new")
        self.assertEqual(calls.requests, [], "stale refresh token must not be spent")

    def test_codex_and_google_use_form_encoding(self):
        self.seed({
            "openai-codex": {"access": "old", "refresh": "cr", "expires": int(time.time() * 1000)},
            "google-antigravity": {"access": "old", "refresh": "gr", "expires": int(time.time() * 1000),
                                   "projectId": "proj-1"},
        })
        calls = fresh_manager(self.ns, self.tmp)
        tm = self.ns["TokenManager"]()
        tm.get_codex_token()
        tm.get_google_token()
        self.assertEqual(len(calls.requests), 2)
        self.assertEqual(calls.requests[0]["url"], "https://auth.openai.com/oauth/token")
        self.assertEqual(calls.requests[1]["url"], "https://oauth2.googleapis.com/token")
        for req in calls.requests:
            self.assertEqual(req["headers"].get("content-type"), "application/x-www-form-urlencoded")
        google_form = urllib.parse.parse_qs(calls.requests[1]["body"])
        self.assertEqual(google_form["client_secret"], ["test-google-secret"])
        codex_form = urllib.parse.parse_qs(calls.requests[0]["body"])
        self.assertEqual(codex_form["refresh_token"], ["cr"])
        self.assertEqual(tm.get_google_project_id(), "proj-1")

    def test_transient_failure_keeps_memory_usable(self):
        self.seed({"anthropic": {"access": "still-valid", "refresh": "rot-1",
                                 "expires": int(time.time() * 1000 + 30_000)}})
        calls = fresh_manager(self.ns, self.tmp)
        calls.responses = None
        tm = self.ns["TokenManager"]()
        tm._expires_at["anthropic"] = time.time()  # force refresh decision
        def boom(req, timeout=None):
            raise OSError("network blip")
        self.ns["urllib"].request.urlopen = boom
        self.assertEqual(tm.get_anthropic_token(), "still-valid")


class RequireHelpers(unittest.TestCase):
    def test_loud_unauthenticated(self):
        tmp = tempfile.mkdtemp(prefix="triroute-test-")
        ns = build_namespace()
        ns["CREDENTIALS_FILE"] = os.path.join(tmp, "credentials.json")
        ns["_token_manager"] = ns["TokenManager"]()
        for fn, needle in ((ns["_require_codex_token"], "ChatGPT/Codex"),
                           (ns["_require_google_token"], "Google AI Pro"),
                           (ns["_fresh_anthropic_or_die"], "Anthropic")):
            with self.assertRaises(RuntimeError) as ctx:
                fn()
            self.assertIn(needle, str(ctx.exception))
            self.assertIn("3737", str(ctx.exception))

    def test_google_requires_project_id(self):
        tmp = tempfile.mkdtemp(prefix="triroute-test-")
        ns = build_namespace()
        cred = os.path.join(tmp, "credentials.json")
        with open(cred, "w") as fh:
            json.dump({"google-antigravity": {"access": "a", "expires": int(time.time() * 1000 + 9_000_000)}}, fh)
        ns["CREDENTIALS_FILE"] = cred
        os.environ.pop("GOOGLE_ANTIGRAVITY_PROJECT_ID", None)
        ns["_token_manager"] = ns["TokenManager"]()
        with self.assertRaises(RuntimeError) as ctx:
            ns["_require_google_token"]()
        self.assertIn("project", str(ctx.exception).lower())


class ThoughtSignatures(unittest.TestCase):
    def test_sqlite_roundtrip(self):
        tmp = tempfile.mkdtemp(prefix="triroute-test-")
        ns = build_namespace()
        ns["_thought_signatures"] = {}
        ns["_CACHE_DB"] = os.path.join(tmp, "session_cache.db")
        db = ns["_open_sig_db"]()
        ns["_sig_db"] = db
        ns["_remember_thought_signature"]("call-1", "SIG-A")
        self.assertEqual(ns["_thought_signatures"]["call-1"], "SIG-A")
        rows = list(db.execute("SELECT signature FROM thought_signatures WHERE call_id='call-1'"))
        self.assertEqual(rows[0][0], "SIG-A")
        ns["_remember_thought_signature"]("call-1", "SIG-B")
        rows = list(db.execute("SELECT signature FROM thought_signatures WHERE call_id='call-1'"))
        self.assertEqual(rows[0][0], "SIG-B")  # INSERT OR REPLACE
        # reopen simulates proxy restart
        db2 = ns["_open_sig_db"]()
        seen = dict(db2.execute("SELECT call_id, signature FROM thought_signatures"))
        self.assertEqual(seen.get("call-1"), "SIG-B")

    def test_no_db_degrades(self):
        ns = build_namespace()
        ns["_sig_db"] = None
        ns["_thought_signatures"] = {}
        ns["_remember_thought_signature"]("x", "s")
        self.assertEqual(ns["_thought_signatures"]["x"], "s")


class InterceptionWiring(unittest.TestCase):
    """Source-level regression guards on the monkey-patch section."""

    def setUp(self):
        self.src = open(BRIDGE, encoding="utf-8").read()

    def test_no_silent_token_fallthrough(self):
        self.assertNotIn("get_codex_token() or _token_manager.get_codex_token", self.src)
        self.assertNotIn("raise Exception(\"OpenAI Codex OAuth token unavailable", self.src)

    def test_every_wrapper_canonicalizes(self):
        defs = [(m.group(1), m.start()) for m in re.finditer(r"def (_wrapped_\w+)", self.src)]
        checked = set()
        for i, (name, start) in enumerate(defs):
            end = defs[i + 1][1] if i + 1 < len(defs) else len(self.src)
            body = self.src[start:end]
            self.assertIn("_canonical_model(", body, f"{name} misses alias canonicalization")
            checked.add(name)
        self.assertEqual(checked, {
            "_wrapped_acompletion", "_wrapped_completion",
            "_wrapped_router_acompletion", "_wrapped_router_completion",
            "_wrapped_route_request"})

    def test_pre_call_hook_order(self):
        hook = self.src[self.src.index("async def async_pre_call_hook"):]
        hook = hook[:hook.index("return data")]
        self.assertLess(hook.index("_is_codex_model"), hook.index('"claude" in model'),
                        "claude-* aliases must not hit the Anthropic injector")

    def test_guard_and_swap_present(self):
        self.assertIn("os._exit(78)", self.src)
        self.assertIn("optionally_handle_anthropic_oauth", self.src)
        self.assertIn("AnthropicModelInfo.get_anthropic_headers", self.src)


class RepoInvariants(unittest.TestCase):
    def test_compose_loopback_and_single_worker(self):
        compose = open(os.path.join(ROOT, "compose.yaml"), encoding="utf-8").read()
        self.assertIn('"127.0.0.1:4000:4000"', compose)
        self.assertIn('"127.0.0.1:3737:3737"', compose)
        self.assertIn('"--num_workers"', compose)
        self.assertRegex(compose, r'(?s)"--num_workers".{0,40}"1"')
        self.assertNotIn("0.0.0.0", compose)

    def test_yaml_aliases_match_bridge_map(self):
        yml = open(os.path.join(ROOT, "config", "litellm.yaml"), encoding="utf-8").read()
        blocks = re.findall(r"- model_name: (\S+)\n\s+litellm_params:\n\s+model: (\S+)", yml)
        mapping = dict(blocks)
        expected = {
            "claude-gpt": "openai/gpt-5.6-terra",
            "claude-gpt-fast": "openai/gpt-5.6-luna",
            "claude-gemini-pro": "openai/gemini-2.5-pro",
            "claude-gemini-flash": "openai/gemini-2.5-flash",
        }
        ns = build_namespace()
        for alias, backend in expected.items():
            self.assertIn(alias, mapping, alias)
            self.assertEqual(mapping[alias], backend)
            # drift guard: bridge alias table suffix == yaml backend suffix
            self.assertEqual(mapping[alias].split("/")[-1], ns["TRIROUTE_ALIAS_BACKEND"][alias])
        for alias in ("claude-opus", "claude-sonnet", "claude-haiku"):
            self.assertTrue(mapping.get(alias, "").startswith("anthropic/"), alias)

    def test_validate_models_are_declared(self):
        yml = open(os.path.join(ROOT, "config", "litellm.yaml"), encoding="utf-8").read()
        declared = set(re.findall(r"- model_name: (\S+)", yml))
        src = open(os.path.join(ROOT, "tests", "validate.py"), encoding="utf-8").read()
        used = set(re.findall(r'"(claude-[a-z0-9-]+)"', src))
        self.assertTrue(used - {"claude-code"}, f"validate.py uses undeclared aliases: {used - declared - {'claude-code'}}")
        self.assertFalse(used - declared - {"claude-code"})

    def test_launcher_never_touches_host_config(self):
        gw = open(os.path.join(ROOT, "bin", "claude-gw"), encoding="utf-8").read()
        self.assertIn("unset ANTHROPIC_API_KEY", gw)
        self.assertIn("http://127.0.0.1:4000", gw)
        self.assertIn("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", gw)
        self.assertNotIn("settings.json", gw)
        self.assertNotIn("$HOME/.claude/", gw)
        self.assertNotIn(".zshrc", gw)

    def test_launcher_preserves_gateway_key(self):
        gw = open(os.path.join(ROOT, "bin", "claude-gw"), encoding="utf-8").read()
        self.assertIn("GATEWAY_MASTER_KEY", gw)
        self.assertIn('ANTHROPIC_AUTH_TOKEN="${GATEWAY_MASTER_KEY}"', gw)
        # The ambient LITELLM_MASTER_KEY must be cleared without clearing the
        # file-backed gateway key used for the child process.
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN="${LITELLM_MASTER_KEY}"', gw)

    def test_gitignore_protects_secrets(self):
        gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
        for entry in ("config/.env", "data/", "credentials.json", "logs/"):
            self.assertIn(entry, gi)

    def test_installer_preserves_live_secrets(self):
        sh = open(os.path.join(ROOT, "install.sh"), encoding="utf-8").read()
        self.assertIn("--exclude 'config/.env'", sh)
        self.assertIn("--exclude 'data/'", sh)
        self.assertNotIn("zshrc", sh)
        self.assertNotIn("settings.json", sh)


if __name__ == "__main__":
    unittest.main(verbosity=2)
