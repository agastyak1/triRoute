#!/usr/bin/env python3
"""TriRoute headless OAuth (PKCE) helper.

Authenticates one subscription provider and merges the resulting tokens into
the shared credentials.json used by the LiteLLM wire bridge and the quota
dashboard. Safe to re-run: each invocation only touches its provider's entry.

Usage:
    python3 scripts/auth_helper.py <claude|openai|google> [path/to/credentials.json]

Flow details mirror the proven first-party desktop clients (Claude Code, Codex
CLI, Antigravity) — public clients, fixed loopback callback ports, S256 PKCE
except Google (whose shipped-secret client does not accept a challenge).
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import tempfile
import time
import uuid
import webbrowser
import urllib.error
import urllib.parse
import urllib.request

EXPIRY_SKEW_MS = 5 * 60_000          # store every expiry 5 minutes early
CALLBACK_TIMEOUT_S = 120             # hardened plan §5: hard listener timeout
TOKEN_TIMEOUT_S = 30
LOCK_TIMEOUT_S = 30
LOCK_STALE_S = 10 * 60

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CREDS = os.path.join(REPO_ROOT, "data", "credentials.json")


def _env_from_config_file(name: str) -> str:
    """Read LITELLM/config/.env style KEY=VALUE without a dependency on dotenv."""
    for candidate in (
        os.path.join(REPO_ROOT, "config", ".env"),
        os.path.join(REPO_ROOT, ".env"),
        os.environ.get("TRIROUTE_ENV_FILE", ""),
    ):
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith(f"{name}=") :
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return os.environ.get(name, "")


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.server.callback_path:
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        result = {
            "code": (params.get("code") or [None])[0],
            "state": (params.get("state") or [None])[0],
            "error": (params.get("error") or [None])[0],
        }
        if result["code"] and result["state"] != self.server.expected_state:
            result = {"code": None, "state": result["state"], "error": "state mismatch (possible CSRF)"}
            status = 400
        else:
            status = 200
        self.server.coordinator.result = result
        self.server.coordinator.done.set()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = status == 200 and bool(result["code"])
        self.wfile.write(
            b"<h1>Authentication %s</h1><p>You can close this window.</p>"
            % (b"succeeded" if ok else b"failed"))

    def log_message(self, *_args):
        return


class _IPv6HTTPServer(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True
    allow_reuse_address = True


class _CallbackState:
    def __init__(self, callback_path: str, expected_state: str):
        self.callback_path = callback_path
        self.expected_state = expected_state
        self.result = None
        self.done = threading.Event()
        self.servers = []
        self.threads = []


def _start_callback_listener(port: int, callback_path: str, expected_state: str):
    """Bind IPv4 and IPv6 loopback listeners before opening the browser."""
    state = _CallbackState(callback_path, expected_state)
    bind_errors = []
    for address, server_type in (
        (("127.0.0.1", port), http.server.ThreadingHTTPServer),
        (("::1", port), _IPv6HTTPServer),
    ):
        try:
            server = server_type(address, _CallbackHandler)
        except OSError as err:
            bind_errors.append(f"{address[0]}: {err}")
            continue
        server.callback_path = callback_path
        server.expected_state = expected_state
        server.coordinator = state
        state.servers.append(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state.threads.append(thread)

    if not state.servers:
        details = "; ".join(bind_errors)
        sys.exit(f"error: cannot bind loopback port {port} — is another login in progress? ({details})")
    return state


def _wait_for_code(state: _CallbackState):
    """Wait for exactly one validated callback, then close both listeners."""
    state.done.wait(CALLBACK_TIMEOUT_S)
    if not state.done.is_set():
        result = {"code": None, "state": None, "error": "callback timed out"}
    else:
        result = state.result or {"code": None, "state": None, "error": "empty callback"}

    for server in state.servers:
        server.shutdown()
        server.server_close()
    for thread in state.threads:
        thread.join(timeout=1)

    if result.get("error"):
        sys.exit(f"error: provider callback failed: {result['error']}")
    if not result.get("code"):
        sys.exit("error: callback did not contain an authorization code")
    return result["code"], result.get("state")


def _open_browser(url: str):
    print(f"Open this URL in your browser (launching now):\n\n  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _post(url: str, body: dict, headers: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TOKEN_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:500]
        sys.exit(f"error: token exchange failed ({err.code}): {detail}")


def _post_form(url: str, body: dict) -> dict:
    data = urllib.parse.urlencode(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TOKEN_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:500]
        sys.exit(f"error: token exchange failed ({err.code}): {detail}")


def _pkce():
    verifier = secrets.token_urlsafe(96)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _id_token_claims(id_token):
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def _expires_ms(expires_in):
    if expires_in is None:
        return None
    return int(time.time() * 1000) + int(expires_in) * 1000 - EXPIRY_SKEW_MS


# ── providers ────────────────────────────────────────────────────────────────

def auth_claude(creds_path: str):
    client_id = _env_from_config_file("ANTHROPIC_CLIENT_ID") or "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    verifier, challenge = _pkce()
    state = str(uuid.uuid4())
    redirect = "http://localhost:54545/callback"
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": ("org:create_api_key user:profile user:inference "
                  "user:sessions:claude_code user:mcp_servers user:file_upload"),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "code": "true",
    })
    listener = _start_callback_listener(54545, "/callback", state)
    _open_browser(f"https://claude.ai/oauth/authorize?{params}")
    code, returned_state = _wait_for_code(listener)
    res = _post(
        "https://api.anthropic.com/v1/oauth/token",
        {"grant_type": "authorization_code", "client_id": client_id, "code": code,
         "state": state, "redirect_uri": redirect, "code_verifier": verifier},
        {"Content-Type": "application/json", "accept": "application/json"},
    )
    account = res.get("account") if isinstance(res.get("account"), dict) else {}
    return "anthropic", {
        "access": res["access_token"],
        "refresh": res.get("refresh_token"),
        "expires": _expires_ms(res.get("expires_in")),
        "email": account.get("email_address"),
        "plan": res.get("plan_type") or account.get("plan_type") or "claude",
    }


def auth_openai(creds_path: str):
    client_id = _env_from_config_file("OPENAI_CODEX_CLIENT_ID") or "app_EMoamEEZ73f0CkXaXp7hrann"
    verifier, challenge = _pkce()
    state = str(uuid.uuid4())
    redirect = "http://localhost:1455/auth/callback"
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": "openid profile email offline_access api.connectors.read api.connectors.invoke",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "pi",
    })
    listener = _start_callback_listener(1455, "/auth/callback", state)
    _open_browser(f"https://auth.openai.com/oauth/authorize?{params}")
    code, returned_state = _wait_for_code(listener)
    res = _post_form("https://auth.openai.com/oauth/token", {
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "code_verifier": verifier, "redirect_uri": redirect,
    })
    claims = _id_token_claims(res.get("id_token", ""))
    auth = claims.get("https://api.openai.com/auth") or {}
    return "openai-codex", {
        "access": res["access_token"],
        "refresh": res.get("refresh_token"),
        "expires": _expires_ms(res.get("expires_in")),
        "email": claims.get("email"),
        "plan": auth.get("chatgpt_plan_type") or "chatgpt",
    }


def auth_google(creds_path: str):
    client_id = (_env_from_config_file("GOOGLE_CLIENT_ID")
                 or "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com")
    client_secret = _env_from_config_file("GOOGLE_CLIENT_SECRET")
    if not client_secret:
        sys.exit(
            "error: GOOGLE_CLIENT_SECRET is required for the Antigravity client exchange.\n"
            "Set it in config/.env (see .env.example) and retry.")
    state = str(uuid.uuid4())
    redirect = "http://localhost:51121/oauth-callback"
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": ("https://www.googleapis.com/auth/cloud-platform "
                  "https://www.googleapis.com/auth/userinfo.email "
                  "https://www.googleapis.com/auth/userinfo.profile "
                  "https://www.googleapis.com/auth/cclog "
                  "https://www.googleapis.com/auth/experimentsandconfigs"),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    })
    listener = _start_callback_listener(51121, "/oauth-callback", state)
    _open_browser(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
    code, returned_state = _wait_for_code(listener)
    res = _post_form("https://oauth2.googleapis.com/token", {
        "grant_type": "authorization_code", "client_id": client_id,
        "client_secret": client_secret, "code": code, "redirect_uri": redirect,
    })
    project_id = _discover_antigravity_project(res["access_token"])
    claims = _id_token_claims(res.get("id_token", ""))
    return "google-antigravity", {
        "access": res["access_token"],
        "refresh": res.get("refresh_token"),
        "expires": _expires_ms(res.get("expires_in")),
        "email": claims.get("email"),
        "plan": "antigravity",
        "projectId": project_id,
    }


def _discover_antigravity_project(access_token: str):
    req = urllib.request.Request(
        "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        data=json.dumps({"metadata": {"ideType": "ANTIGRAVITY"}}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)",
        })
    try:
        with urllib.request.urlopen(req, timeout=TOKEN_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        print(f"warning: project discovery failed ({err}); the bridge will report it on first Gemini call")
        return None
    for key in ("cloudaicompanionProject", "projectId", "project"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            return value["id"]
    return None


# ── persistence (atomic merge, dashboard-compatible schema) ─────────────────

_save_thread_lock = threading.Lock()


def _lock_credentials(creds_path: str):
    directory = os.path.dirname(os.path.abspath(creds_path)) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    lock_dir = creds_path + ".lockdir"
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            os.mkdir(lock_dir, 0o700)
            break
        except FileExistsError:
            try:
                if time.time() - os.stat(lock_dir).st_mtime > LOCK_STALE_S:
                    os.rmdir(lock_dir)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for the credentials writer lock")
            time.sleep(0.05)
    lock_path = creds_path + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock_path, 0o600)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
    except ImportError:
        pass
    return fd, lock_dir


def _unlock_credentials(lock):
    fd, lock_dir = lock
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except ImportError:
        pass
    os.close(fd)
    try:
        os.rmdir(lock_dir)
    except OSError:
        pass


def save(provider_key: str, credential: dict, creds_path: str):
    credential = {k: v for k, v in credential.items() if v not in (None, "")}
    credential["authorizedAt"] = int(time.time() * 1000)
    os.makedirs(os.path.dirname(creds_path) or ".", exist_ok=True)
    with _save_thread_lock:
        lock = _lock_credentials(creds_path)
        tmp = None
        tmp_fd = None
        try:
            if os.path.exists(creds_path):
                try:
                    with open(creds_path, encoding="utf-8") as fh:
                        existing = json.load(fh)
                except (OSError, json.JSONDecodeError) as err:
                    raise RuntimeError(
                        f"credentials file is unreadable; refusing to overwrite it: {err}"
                    ) from err
                if not isinstance(existing, dict):
                    raise RuntimeError("credentials file must contain a JSON object")
            else:
                existing = {}

            previous = existing.get(provider_key)
            merged = dict(previous) if isinstance(previous, dict) else {}
            merged.update(credential)
            existing[provider_key] = merged

            directory = os.path.dirname(os.path.abspath(creds_path)) or "."
            tmp_fd, tmp = tempfile.mkstemp(
                prefix=os.path.basename(creds_path) + ".", suffix=".tmp", dir=directory
            )
            os.fchmod(tmp_fd, 0o600)
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                tmp_fd = None
                json.dump(existing, fh, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, creds_path)
            tmp = None
            os.chmod(creds_path, 0o600)
            try:
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if tmp_fd is not None:
                os.close(tmp_fd)
            if tmp:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
            _unlock_credentials(lock)


PROVIDERS = {"claude": auth_claude, "openai": auth_openai, "google": auth_google}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PROVIDERS:
        sys.exit("usage: auth_helper.py <claude|openai|google> [credentials.json]")
    creds_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CREDS
    key, credential = PROVIDERS[sys.argv[1]](creds_path)
    save(key, credential, creds_path)
    who = credential.get("email") or "account"
    print(f"success: {sys.argv[1]} authenticated ({who}) -> {creds_path}")


if __name__ == "__main__":
    main()
