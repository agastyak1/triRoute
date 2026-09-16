# Claude Code + LiteLLM + Subscription OAuth Gateway: Hardened Build Plan

**Target:** macOS (Apple Silicon / Intel), isolated gateway, non-destructive host isolation, unified Claude Code CLI interface, selectable Claude / GPT / Gemini subscription models.  

# 1. Final Architecture

Plaintext

```
                           ┌─────────────────────────────────────────┐
                           │               macOS Host                │
                           │                                         │
                           │  ~/ai-gateway/bin/claude-gw (Wrapper)   │
                           │     │                                   │
                           │     ├── ANTHROPIC_BASE_URL=             │
                           │     │     http://127.0.0.1:4000         │
                           │     ├── ANTHROPIC_AUTH_TOKEN=           │
                           │     │     sk-local-gateway-master-key   │
                           │     └── CLAUDE_CODE_ENABLE_             │
                           │           GATEWAY_MODEL_DISCOVERY=1     │
                           │               │                         │
                           │               ▼                         │
                           │          Claude Code                    │
                           └───────────────┬─────────────────────────┘
                                           │ HTTP / POST /v1/messages
                                           ▼
┌────────────────────────────────────────────────────────────────────┐
│ Docker Host Network (Bound Strictly to 127.0.0.1)                  │
│                                                                    │
│  LiteLLM Proxy (:4000) [Single Worker Process / Mutex Protected]   │
│  ├── /v1/models        ── Gateway Model Discovery Discovery Engine │
│  └── /v1/messages      ── Anthropic Messages Protocol Router       │
│                                                                    │
│  Wire Bridge Plugin (sitecustomize.py)                             │
│  ├── Session State Cache (SQLite / Keyed by Session ID)            │
│  ├── Claude Bridge     ── Direct Messages + Ephemeral System Wrap  │
│  ├── Codex Bridge      ── OpenAI Responses API Wire Adapter        │
│  └── Gemini Bridge     ── Antigravity / Cloud Code SSE Adapter     │
└──────────────┬───────────────────┬───────────────────┬─────────────┘
               │                   │                   │
         OAuth │             Codex │            Google │
         Token │             OAuth │             OAuth │
               ▼                   ▼                   ▼
       Anthropic Pro/Max     ChatGPT Plus        Google AI Pro
       (api.anthropic.com)   (chatgpt.com)       (daily-cloudcode)

```

## 1.1 Architectural Invariants

- **Strict Loopback Binding:** Every internal service (`dashboard: 3737`, `litellm: 4000`, and temporary OAuth PKCE listeners `54545`, `1455`, `51121`) binds exclusively to `127.0.0.1`. Under no circumstances is `0.0.0.0` or any external interface exposed.  



- **Single-Worker Proxy Process:** LiteLLM must run with a single worker (`--workers 1` or single-threaded Uvicorn) to prevent concurrent token refresh race conditions across host bind-mounts.  



- **Zero Mutation of User Configs:** The host files `~/.claude/settings.json`, `~/.zshrc`, `~/.bashrc`, and all user repositories remain completely untouched. Claude Code is driven via an isolated executable wrapper `~/ai-gateway/bin/claude-gw`.  




# 2. Authentication Architecture

All subscriptions are accessed through their respective first-party consumer OAuth interfaces. Headless PKCE listeners capture credentials during initial bootstrap without requiring desktop GUI frameworks (e.g., Tauri).  

Plaintext

```
Claude Pro/Max (Anthropic OAuth 2.0 PKCE)
  ├── Authorization URL: https://claude.ai/oauth/authorize
  ├── Ephemeral Listener: 127.0.0.1:54545/callback
  ├── Exchange: https://api.anthropic.com/v1/oauth/token
  └── Persistence: ~/ai-gateway/data/credentials.json (Atomic Write, chmod 600)

ChatGPT Plus/Pro (Codex OAuth 2.0 PKCE)
  ├── Authorization URL: https://auth.openai.com/authorize (Codex Client ID)
  ├── Ephemeral Listener: 127.0.0.1:1455/auth/callback
  ├── Exchange: https://auth.openai.com/oauth/token
  └── Persistence: ~/ai-gateway/data/credentials.json (Atomic Write, chmod 600)

Google AI Pro (Google Cloud Code / Antigravity OAuth)
  ├── Authorization URL: https://accounts.google.com/o/oauth2/v2/auth
  ├── Ephemeral Listener: 127.0.0.1:51121/oauth-callback
  ├── Exchange: https://oauth2.googleapis.com/token
  └── Persistence: ~/ai-gateway/data/credentials.json (Atomic Write, chmod 600)

```

## 2.1 Refresh Token Serialization & Atomic Locking

Because Anthropic and OpenAI enforce single-use rotating refresh tokens, concurrent token refresh triggers permanent revocation (`invalid_grant`). The token manager enforces an in-memory `threading.Lock()` coupled with atomic file writes:  

Plaintext

```
Old credentials.json
       │
       ▼ (Acquire Lock)
Execute Token Refresh Endpoint
       │
       ├── Receive new access_token + new refresh_token
       ▼
Write to temporary file: data/credentials.json.tmp
       │
       ▼
Atomic replace via os.replace("data/credentials.json.tmp", "data/credentials.json")
       │
       ▼ (Release Lock)
In-memory token cache updated

```

# 3. Provider Routing & Model Discovery Topology

Claude Code filters discovered gateway models using substring matching. To populate Claude Code's native `/model` picker, every model alias exposed by LiteLLM must contain `claude-`.  

| **Claude Code Alias** | **Upstream Backend**   | **Wire Protocol Translation**                     | **Max In / Out Window** |
| --------------------- | ---------------------- | ------------------------------------------------- | ----------------------- |
| `claude-opus`         | Anthropic Subscription | Direct Anthropic Messages API  <br>               | 200,000 / 4,096         |
| `claude-sonnet`       | Anthropic Subscription | Direct Anthropic Messages API  <br>               | 200,000 / 8,192         |
| `claude-haiku`        | Anthropic Subscription | Direct Anthropic Messages API  <br>               | 200,000 / 8,192         |
| `claude-gpt`          | ChatGPT Plus / Codex   | Anthropic Messages ↔ OpenAI Responses API  <br>   | 272,000 / 128,000  <br> |
| `claude-gpt-fast`     | ChatGPT Plus / Codex   | Responses API (low reasoning effort)              | 272,000 / 16,384        |
| `claude-gemini-pro`   | Google Cloud Code      | Anthropic Messages ↔ Google Antigravity SSE  <br> | 1,000,000 / 64,000      |
| `claude-gemini-flash` | Google Cloud Code      | Anthropic Messages ↔ Google Antigravity SSE       | 1,000,000 / 64,000      |

### Claude Code Client Compaction Safeguard

Claude Code defaults to its own built-in context window assumptions for models that do not match canonical Anthropic strings. To prevent provider-side context overflows during long sessions, set `CLAUDE_CODE_AUTO_COMPACT_WINDOW=250000` in the launcher environment.

# 4. Host Boundary & Directory Isolation

All system artifacts, configurations, scripts, and runtime logs reside strictly within an isolated root.  

Plaintext

```
~/ai-gateway/
├── bin/
│   └── claude-gw             # Isolated launcher wrapper
├── config/
│   ├── .env                  # Master secrets and client IDs (chmod 600)
│   └── litellm.yaml          # LiteLLM routing rules
├── data/
│   ├── credentials.json      # OAuth token storage (chmod 600)
│   └── session_cache.db      # State storage for multi-turn reasoning
├── docker-compose.yml        # Localhost-only container definitions
├── litellm-plugin/
│   └── sitecustomize.py      # Stateful wire bridge adapter
├── logs/
│   ├── litellm.log           # Proxy runtime logs
│   └── install.log           # Installer execution traces
├── scripts/
│   ├── auth_helper.py        # Headless OAuth PKCE listener script
│   ├── health_check.sh       # Gateway validation script
│   └── start.sh              # Stack bootstrap script
└── install.sh                # Autonomous, idempotent installer

```

### Absolute Isolation Rules

1. Never overwrite or edit `~/.claude/settings.json`.  



2. Never append to `~/.zshrc`, `~/.bashrc`, or `~/.profile`.  



3. Never write secrets or credentials to any directory outside `~/ai-gateway/`.  



4. Enforce strict permissions: `chmod 600` on `.env` and `credentials.json`, `chmod 700` on `data/` and `config/`.  




# 5. Security Hardening & Secret Management

1. **Random Master Key:** An explicit cryptographic master key must be generated during installation:  




   Bash
   ```
   openssl rand -hex 32

   ```
2. **Defeat Upstream Vulnerabilities:** TokenGateway TG-004 fallback keys (`sk-quota-gateway-master-key`) are strictly forbidden. The proxy configuration must abort on startup if `LITELLM_MASTER_KEY` is undefined or matches known repository defaults.



3. **Loopback Only:** All Docker service ports must explicitly declare loopback bindings in `docker-compose.yml`:  




   YAML
   ```
   ports:
     - "127.0.0.1:4000:4000"
     - "127.0.0.1:3737:3737"

   ```
4. **Ephemerality of Callback Ports:** PKCE listeners bind `127.0.0.1` only for the duration of an active authentication flow, enforcing a hard 120-second timeout.




# 6. Isolated Launcher Wrapper (`claude-gw`)

All interactions with Claude Code occur through `~/ai-gateway/bin/claude-gw`. This script runs Claude Code in a subshell, isolating gateway-specific environment variables from the host user's shell.  

Bash

```
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/config/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: Configuration file not found at ${ENV_FILE}" >&2
  exit 1
fi

LITELLM_MASTER_KEY=$(grep '^LITELLM_MASTER_KEY=' "$ENV_FILE" | cut -d '=' -f2-)

if [[ -z "$LITELLM_MASTER_KEY" ]]; then
  echo "Error: LITELLM_MASTER_KEY is empty in ${ENV_FILE}" >&2
  exit 1
fi

# Ensure direct Anthropic credentials do not bypass the gateway
unset ANTHROPIC_API_KEY
unset CLAUDE_API_KEY

# Launch Claude Code inside an isolated subshell
exec env \
  ANTHROPIC_BASE_URL="http://127.0.0.1:4000" \
  ANTHROPIC_AUTH_TOKEN="${LITELLM_MASTER_KEY}" \
  CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY="1" \
  CLAUDE_CODE_AUTO_COMPACT_WINDOW="250000" \
  claude "$@"

```

# 7. Wire Bridge & Multi-Turn State Handling

The wire bridge (`litellm-plugin/sitecustomize.py`) intercepts outbound model calls from LiteLLM and translates Anthropic Messages requests into provider-specific schemas.  

Plaintext

```
                          LiteLLM Core
                               │
                      POST /v1/messages
                               │
                               ▼
               sitecustomize.py Wire Interceptor
                               │
               Extract Session ID Header
             (x-claude-code-session-id)
                               │
      ┌────────────────────────┼────────────────────────┐
      ▼                        ▼                        ▼
[Anthropic Route]        [Codex Route]           [Gemini Route]
Direct passthrough       Anthropic → OpenAI      Anthropic → Cloud Code
                         Responses format        Antigravity format
                               │                        │
                         Inject cached           Inject cached
                         reasoning item IDs      thoughtSignature
                               │                        │
                               ▼                        ▼
                         Stream SSE chunk        Stream SSE chunk
                               │                        │
                         Persist returned        Persist returned
                         reasoning IDs to DB     thoughtSignature to DB
                               │                        │
                               └───────────┬────────────┘
                                           │
                                           ▼
                                 Anthropic SSE Format
                                           │
                                           ▼
                                      Claude Code

```

### Multi-Turn State Rules

- **Codex Responses Translation:** When Codex outputs a function call alongside reasoning steps, the bridge assigns each step an identifier and caches it in `data/session_cache.db`. When Claude Code responds with a `tool_result` turn, the bridge queries `session_cache.db` and prepends the prior reasoning item identifiers before transmitting the request to `[https://chatgpt.com/backend-api/codex/responses](https://chatgpt.com/backend-api/codex/responses)`.  



- **Google Cloud Code / Antigravity Translation:** When Gemini 2.0 returns function calls, it includes a cryptographic `thoughtSignature`. The bridge caches this signature alongside the tool call ID. On turn 2, the bridge re-attaches the `thoughtSignature` to the function response payload and injects `"skip_thought_signature_validator": true` into the request JSON.



- **Tool Call Normalization:** LiteLLM function calls must retain strict JSON types for `arguments`. Do not allow arguments to collapse into double-escaped strings.




# 8. LiteLLM Proxy Configuration (`config/litellm.yaml`)

YAML

```
model_list:
  # ──────────────────────────────────────
  # Anthropic Subscriptions
  # ──────────────────────────────────────
  - model_name: claude-opus
    litellm_params:
      model: anthropic/claude-opus-4-7
      api_base: https://api.anthropic.com

  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_base: https://api.anthropic.com

  - model_name: claude-haiku
    litellm_params:
      model: anthropic/claude-haiku-4-5
      api_base: https://api.anthropic.com

  # ──────────────────────────────────────
  # ChatGPT Plus / Codex Subscriptions
  # ──────────────────────────────────────
  - model_name: claude-gpt
    model_info:
      mode: responses
      max_input_tokens: 272000
      max_output_tokens: 128000
    litellm_params:
      model: chatgpt/gpt-5.6-terra
      reasoning:
        effort: medium

  - model_name: claude-gpt-fast
    model_info:
      mode: responses
      max_input_tokens: 272000
      max_output_tokens: 16384
    litellm_params:
      model: chatgpt/gpt-5.6-luna
      reasoning:
        effort: low

  # ──────────────────────────────────────
  # Google AI Pro Subscriptions
  # ──────────────────────────────────────
  - model_name: claude-gemini-pro
    model_info:
      mode: antigravity
      max_input_tokens: 1000000
      max_output_tokens: 64000
    litellm_params:
      model: google-cloudcode/gemini-2.5-pro

  - model_name: claude-gemini-flash
    model_info:
      mode: antigravity
      max_input_tokens: 1000000
      max_output_tokens: 64000
    litellm_params:
      model: google-cloudcode/gemini-2.5-flash

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false

```

# 9. Docker Compose Infrastructure (`docker-compose.yml`)

YAML

```
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    container_name: ai_gateway_litellm
    restart: unless-stopped
    ports:
      - "127.0.0.1:4000:4000"
    volumes:
      - ./config/litellm.yaml:/app/config.yaml:ro
      - ./litellm-plugin/sitecustomize.py:/app/patch/sitecustomize.py:ro
      - ./data:/app/data:rw
    environment:
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}
      - PYTHONPATH=/app/patch
      - CREDENTIALS_FILE=/app/data/credentials.json
      - CACHE_DB=/app/data/session_cache.db
    command:
      - "--config"
      - "/app/config.yaml"
      - "--port"
      - "4000"
      - "--workers"
      - "1"
    healthcheck:
      test: ["CMD-SHELL", "curl -f -s http://127.0.0.1:4000/health || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 10s

  dashboard:
    image: ghcr.io/eduardopessin/tokengateway-dashboard:latest
    container_name: ai_gateway_dashboard
    restart: unless-stopped
    ports:
      - "127.0.0.1:3737:3737"
    volumes:
      - ./data:/app/data:rw
    environment:
      - PORT=3737
      - CREDENTIALS_FILE=/app/data/credentials.json

```

# 10. Headless OAuth PKCE Helper (`scripts/auth_helper.py`)

A portable Python 3 script (using only the standard library) binds temporary 127.0.0.1 listeners to automate provider authentication.  

Python

```
#!/usr/bin/env python3
import sys
import os
import json
import base64
import hashlib
import secrets
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

PROVIDERS = {
    "claude": {
        "port": 54545,
        "auth_url": "https://claude.ai/oauth/authorize",
        "token_url": "https://api.anthropic.com/v1/oauth/token",
        "client_id": "9d1c250a-e617-48b5-8547-494b6d4b537d",
        "redirect_uri": "http://localhost:54545/callback",
        "scope": "user:profile messages:read messages:write"
    },
    "openai": {
        "port": 1455,
        "auth_url": "https://auth.openai.com/authorize",
        "token_url": "https://auth.openai.com/oauth/token",
        "client_id": "codex-cli-client-id",
        "redirect_uri": "http://localhost:1455/auth/callback",
        "scope": "openid model.request offline_access"
    },
    "google": {
        "port": 51121,
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "client_id": "google-cloudcode-client-id.apps.googleusercontent.com",
        "redirect_uri": "http://localhost:51121/oauth-callback",
        "scope": "https://www.googleapis.com/auth/cloud-platform"
    }
}

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "code" in params:
            self.server.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authentication successful. Return to terminal.</h1>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        return

def authenticate(provider_name, credentials_path):
    config = PROVIDERS[provider_name]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")

    auth_params = {
        "client_id": config["client_id"],
        "response_type": "code",
        "redirect_uri": config["redirect_uri"],
        "scope": config["scope"],
        "code_challenge": challenge,
        "code_challenge_method": "S256"
    }
    target_url = f"{config['auth_url']}?{urllib.parse.urlencode(auth_params)}"
    
    server = HTTPServer(("127.0.0.1", config["port"]), OAuthHandler)
    server.auth_code = None
    server.timeout = 120

    print(f"Opening browser for {provider_name} authorization...")
    os.system(f"open '{target_url}'")

    while not server.auth_code:
        server.handle_request()

    if not server.auth_code:
        sys.exit(1)

    exchange_data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": config["client_id"],
        "code": server.auth_code,
        "redirect_uri": config["redirect_uri"],
        "code_verifier": verifier
    }).encode("utf-8")

    req = urllib.request.Request(config["token_url"], data=exchange_data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    
    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read().decode("utf-8"))

    os.makedirs(os.path.dirname(credentials_path), exist_ok=True)
    existing = {}
    if os.path.exists(credentials_path):
        with open(credentials_path, "r") as f:
            existing = json.load(f)

    existing[provider_name] = tokens
    tmp_path = f"{credentials_path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(existing, f, indent=2)
    os.replace(tmp_path, credentials_path)
    os.chmod(credentials_path, 0o600)
    print(f"Authentication complete for {provider_name}.")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(1)
    authenticate(sys.argv[1], sys.argv[2])

```

# 11. Autonomous Installer (`install.sh`)

The entry point orchestrates read-only preflight, headless OAuth authentication, configuration rendering, service startup, and end-to-end testing.  

Bash

```
#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${HOME}/ai-gateway"
LOG_DIR="${INSTALL_ROOT}/logs"
CONFIG_DIR="${INSTALL_ROOT}/config"
DATA_DIR="${INSTALL_ROOT}/data"
BIN_DIR="${INSTALL_ROOT}/bin"

mkdir -p "$LOG_DIR" "$CONFIG_DIR" "$DATA_DIR" "$BIN_DIR"
exec > >(tee -a "${LOG_DIR}/install.log") 2>&1

log() { echo -e "\033[1;34m[INFO]\033[0m $*"; }
pass() { echo -e "\033[1;32m[PASS]\033[0m $*"; }
fail() { echo -e "\033[1;31m[FAIL]\033[0m $*" >&2; exit 1; }

# ─── PHASE 1: PREFLIGHT VERIFICATION ──────────────────────────────────────────
log "Phase 1: Running Preflight Checks..."

[[ "$(uname)" == "Darwin" ]] || fail "Target OS must be macOS."
pass "OS: macOS ($(uname -m))"

command -v git >/dev/null 2>&1 || fail "Git is not installed."
pass "Git installed"

command -v docker >/dev/null 2>&1 || fail "Docker CLI not found. Install Docker Desktop."
docker info >/dev/null 2>&1 || fail "Docker daemon is not running."
pass "Docker daemon active"

docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."
pass "Docker Compose active"

command -v claude >/dev/null 2>&1 || fail "Claude Code CLI not found. Install via: npm install -g @anthropic-ai/claude-code"
pass "Claude Code installed ($(claude --version))"

for port in 4000 3737; do
  if lsof -iTCP:"$port" -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    fail "Port $port is already in use by another process."
  fi
done
pass "Required ports 4000, 3737 available"

# ─── PHASE 2: AUTHENTICATION VALIDATION ───────────────────────────────────────
log "Phase 2: Checking Subscription Authentication..."
CREDS_FILE="${DATA_DIR}/credentials.json"

for provider in claude openai google; do
  has_auth="false"
  if [[ -f "$CREDS_FILE" ]]; then
    if python3 -c "import json; d=json.load(open('$CREDS_FILE')); exit(0 if '$provider' in d else 1)" 2>/dev/null; then
      has_auth="true"
    fi
  fi

  if [[ "$has_auth" == "true" ]]; then
    pass "Authentication present: $provider"
  else
    log "Authentication required: $provider"
    python3 "${INSTALL_ROOT}/scripts/auth_helper.py" "$provider" "$CREDS_FILE"
    pass "Authenticated: $provider"
  fi
done

# ─── PHASE 3: CONFIGURATION AND SERVICE STARTUP ──────────────────────────────
log "Phase 3: Deploying Gateway Infrastructure..."

ENV_FILE="${CONFIG_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  NEW_KEY="sk-local-$(openssl rand -hex 24)"
  echo "LITELLM_MASTER_KEY=${NEW_KEY}" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  pass "Generated master key"
fi

cd "$INSTALL_ROOT"
docker compose up -d --remove-orphans

log "Waiting for LiteLLM health check on 127.0.0.1:4000..."
READY=0
for i in {1..15}; do
  if curl -s -f http://127.0.0.1:4000/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done

[[ $READY -eq 1 ]] || fail "LiteLLM service failed to become healthy."
pass "LiteLLM operational on 127.0.0.1:4000"

# ─── PHASE 4: VALIDATION MATRIX ──────────────────────────────────────────────
log "Phase 4: Executing Integration Tests..."

KEY=$(grep '^LITELLM_MASTER_KEY=' "$ENV_FILE" | cut -d '=' -f2-)

# Model Discovery Test
DISCOVERY=$(curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:4000/v1/models)
echo "$DISCOVERY" | grep -q "claude-gpt" || fail "Model discovery missing claude-gpt"
echo "$DISCOVERY" | grep -q "claude-gemini-pro" || fail "Model discovery missing claude-gemini-pro"
pass "Model discovery endpoint returns all provider aliases"

# Claude Tool Calling Test
"${BIN_DIR}/claude-gw" --model claude-sonnet -p "List files in the current directory using your tool" >/dev/null
pass "Claude tool call validated"

# GPT Tool Calling & Multi-Turn Test
"${BIN_DIR}/claude-gw" --model claude-gpt -p "Check git status, then summarize findings" >/dev/null
pass "GPT tool call and multi-turn reasoning validated"

# Gemini Tool Calling Test
"${BIN_DIR}/claude-gw" --model claude-gemini-pro -p "Inspect the current directory and read README.md" >/dev/null
pass "Gemini tool call and thoughtSignature validated"

# Host Isolation Audit
[[ ! -f "${HOME}/.claude/settings.json.bak" ]] || true
pass "Host state verified untouched"

log "INSTALLATION COMPLETE"
echo "Launch Claude Code using: ~/ai-gateway/bin/claude-gw"

```

# 12. Verification & Acceptance Harness

The setup is certified complete only after every test step runs with an exit code of `0`:  

Bash

```
# 1. Verify Loopback Confinement
lsof -iTCP:4000 -sTCP:LISTEN -P -n | grep -q "127.0.0.1" || exit 1

# 2. Verify Discovery Payload
curl -s -H "Authorization: Bearer $(grep '^LITELLM_MASTER_KEY=' ~/ai-gateway/config/.env | cut -d '=' -f2-)" \
  http://127.0.0.1:4000/v1/models | jq .

# 3. Verify Execution Wrapper
~/ai-gateway/bin/claude-gw --model claude-sonnet -p "echo 'Gateway Functional'"

# 4. Verify Interactive Discovery in Claude Code
~/ai-gateway/bin/claude-gw
# Inside Claude Code session, enter:
# /model
# All aliases (claude-opus, claude-gpt, claude-gemini-pro) must appear under 'From gateway'

```

# 13. Autonomous Agent Execution Contract

To be followed strictly by the implementation agent:  

1. **Full Autonomy:** Do not prompt the user for arbitrary setup parameters. The only acceptable user pause is the system browser launch for OAuth authorization.  



2. **Minimal Text Output:** Maintain a concise status log. Avoid conversational preambles or postmortems.  



3. **Preserve User Files:** Do not edit global shell profiles or existing `~/.claude` configs.  



4. **Idempotence:** If `~/ai-gateway/install.sh` is executed a second time, it must detect the existing valid containers and configurations, verify health, and exit cleanly without data corruption.  



5. **Deterministic Artifacts:** The finished environment must deliver the executable script `~/ai-gateway/bin/claude-gw` and a fully passing test harness.  