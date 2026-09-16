# Claude Code + LiteLLM + Subscription OAuth Gateway
## System Architecture and Build Plan

**Target:** macOS, local-only gateway, one Claude Code installation, selectable Claude / GPT / Gemini models.

**Goal**

Use Claude Code as the only coding-agent interface:

```text
Claude Code
    │
    │ Anthropic Messages-compatible requests
    ▼
┌──────────────────────────────────────────────┐
│           Local LiteLLM Gateway              │
│              127.0.0.1:4000                  │
│                                              │
│  Model aliases                               │
│  ├─ Claude subscription → Anthropic bridge  │
│  ├─ GPT subscription    → OpenAI/Codex      │
│  └─ Gemini subscription → Google bridge     │
└──────────────┬──────────────┬───────────────┘
               │              │
               │              │
         subscription      subscription
            OAuth             OAuth
               │              │
      ┌────────┘       ┌──────┴─────────┐
      ▼                ▼                ▼
  Anthropic          OpenAI          Google
 Claude Pro/Max    ChatGPT Plus    Google AI Pro
```

The current implementation to base this on is the community **TokenGateway** project plus its LiteLLM wire bridge. It specifically targets Claude Pro/Max, ChatGPT Plus/Pro, and Google Gemini subscriptions, with OAuth and first-party CLI wire protocols rather than normal paid API keys.

> **Important:** This is not the same as official API billing. Consumer subscriptions are being accessed through first-party CLI/subscription surfaces. The TokenGateway project is community software and states that it is a personal/homelab tool, not production hardened. Treat account and terms risk separately from the technical architecture.

---

# 1. Final Architecture

## 1.1 Components

```text
                           ┌─────────────────────────┐
                           │       macOS Host        │
                           │                         │
                           │  Ghostty / Terminal     │
                           │          │              │
                           │          ▼              │
                           │    ┌─────────────┐       │
                           │    │ Claude Code │       │
                           │    └──────┬──────┘       │
                           │           │              │
                           │           │ HTTP         │
                           │           │ :4000        │
                           │           ▼              │
                           │    ┌─────────────┐       │
                           │    │   LiteLLM   │       │
                           │    │    Proxy    │       │
                           │    └──────┬──────┘       │
                           │           │              │
                           │     wire bridge          │
                           │   / OAuth adapter        │
                           │           │              │
                           │     ┌─────┼─────┐        │
                           │     │     │     │        │
                           │     ▼     ▼     ▼        │
                           │   Claude  GPT  Gemini    │
                           │    OAuth  OAuth  OAuth   │
                           │     │     │     │        │
                           │     └─────┼─────┘        │
                           │           │              │
                           └───────────┼──────────────┘
                                       │ HTTPS
                         ┌─────────────┼───────────────┐
                         ▼             ▼               ▼
                   api.anthropic  chatgpt.com    Google Cloud Code
```

## 1.2 Authentication architecture

There are three independent subscription credentials.

```text
Claude Pro
   │
   │ OAuth 2.0 / PKCE
   ▼
Claude access token + rotating refresh token
   │
   ▼
Local credential store
   │
   ▼
LiteLLM bridge
```

```text
ChatGPT Plus
   │
   │ Codex OAuth
   ▼
OpenAI/Codex credential
   │
   ▼
Local credential store
   │
   ▼
LiteLLM chatgpt provider / bridge
```

```text
Google AI Pro
   │
   │ Google OAuth
   ▼
Google/Cloud Code credential
   │
   ▼
Local credential store
   │
   ▼
LiteLLM Google bridge
```

Do **not** make Claude Code hold the OpenAI or Google credentials. Claude Code should know only about the local LiteLLM endpoint.

---

# 2. Why This Architecture

Normal LiteLLM provider configuration is not enough for your requirement.

```text
Normal LiteLLM
Claude Code
   │
   ▼
LiteLLM
   │
   ├── Anthropic API key
   ├── OpenAI API key
   └── Google API key
```

That uses API billing.

Your target is:

```text
Claude Code
   │
   ▼
LiteLLM
   │
   ├── Claude OAuth subscription
   ├── ChatGPT/Codex OAuth subscription
   └── Google subscription OAuth
```

The current TokenGateway implementation exists specifically for this model. It injects a `sitecustomize.py` wire bridge into LiteLLM and forwards requests to first-party subscription/CLI endpoints.

---

# 3. Provider Mapping

| Claude Code alias | Actual subscription backend | Protocol |
|---|---|---|
| `claude-opus-*` | Claude Pro/Max | Anthropic Messages |
| `claude-sonnet-*` | Claude Pro/Max | Anthropic Messages |
| `claude-haiku-*` | Claude Pro/Max | Anthropic Messages |
| `claude-codex-gpt-*` | ChatGPT Plus/Pro / Codex | OpenAI Responses |
| `claude-gemini-*` | Google AI Pro / Antigravity/Cloud Code | Google internal Cloud Code |

The `claude-` prefix on non-Claude aliases is intentional.

Claude Code model discovery filters the gateway model list. A Claude-looking alias lets GPT and Gemini models appear inside Claude Code's model picker.

---

# 4. Recommended Repository

Use:

```bash
git clone https://github.com/eduardopessin/tokengateway.git
cd tokengateway
```

The repository currently contains:

```text
tokengateway/
├── dashboard/
├── desktop/
├── deploy/
├── docs/
├── litellm-plugin/
├── docker-compose.yml
├── .env.example
└── README.md
```

The project currently provides:

- LiteLLM wire bridge
- OAuth token management
- Anthropic subscription support
- ChatGPT/Codex subscription support
- Google Antigravity / Cloud Code subscription support
- Docker Compose deployment
- local/desktop OAuth loopback handling

Source:

https://github.com/eduardopessin/tokengateway

---

# 5. Deployment Choice

## Use local Docker Compose

For your use case, do **not** start with Kubernetes.

```text
macOS
 │
 ├── Docker Desktop
 │     │
 │     ├── TokenGateway dashboard
 │     └── LiteLLM proxy
 │
 └── Claude Code
```

This keeps:

- credentials local
- gateway local
- no public network listener
- no cloud server
- no Kubernetes RBAC
- no remote credential synchronization

The upstream project documents:

```bash
cp .env.example .env
docker-compose up -d
```

with:

```text
Dashboard: http://localhost:3737
LiteLLM:   http://localhost:4000
```

---

# 6. Required Software

Install before starting:

```bash
# Homebrew
brew install git
```

Install Docker Desktop:

```text
https://www.docker.com/products/docker-desktop/
```

Verify:

```bash
docker --version
docker compose version
```

Install Claude Code using your existing installation method.

Verify:

```bash
claude --version
```

---

# 7. Clone and Initialize

```bash
mkdir -p ~/Developer/ai-gateway
cd ~/Developer/ai-gateway

git clone https://github.com/eduardopessin/tokengateway.git
cd tokengateway

cp .env.example .env
```

Generate a strong local gateway key:

```bash
openssl rand -hex 32
```

Put that value into:

```dotenv
LITELLM_MASTER_KEY=sk-<generated-random-value>
```

Do **not** use the repository's example key.

---

# 8. Environment Configuration

Start from:

```bash
cp .env.example .env
```

The current upstream template includes:

```dotenv
PORT=3737

LITELLM_PORT=4000
LITELLM_MASTER_KEY=sk-change-me-use-a-random-value

ANTHROPIC_CLIENT_ID=...
OPENAI_CODEX_CLIENT_ID=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

The current repository supplies default first-party client IDs for Anthropic and OpenAI, while Google credentials are represented in the environment template.

Keep `.env` out of Git.

Verify:

```bash
git status --short
```

The goal is for `.env` to remain untracked.

---

# 9. Start the Gateway

Run:

```bash
docker compose up -d
```

Check:

```bash
docker compose ps
```

Then inspect:

```bash
docker compose logs -f litellm
```

The expected architecture is:

```text
docker-compose
      │
      ├── dashboard :3737
      │
      └── litellm    :4000
```

The LiteLLM server must be reachable only from localhost.

Target:

```text
127.0.0.1:4000
```

Do not expose port 4000 through your router or public reverse proxy.

---

# 10. Install the LiteLLM Wire Bridge

The TokenGateway project uses:

```text
litellm-plugin/sitecustomize.py
```

The bridge is injected with:

```text
PYTHONPATH=/app/patch
```

Inside the container the architecture is:

```text
Python startup
     │
     ▼
sitecustomize.py
     │
     ├── intercept Anthropic model calls
     ├── intercept ChatGPT/Codex model calls
     └── intercept Google model calls
```

The upstream plugin currently documents these environment variables:

```dotenv
ANTHROPIC_OAUTH_TOKEN
ANTHROPIC_REFRESH_TOKEN

OPENAI_CODEX_OAUTH_TOKEN
OPENAI_CODEX_REFRESH_TOKEN

GOOGLE_ANTIGRAVITY_OAUTH_TOKEN
GOOGLE_ANTIGRAVITY_PROJECT_ID
```

Do not manually copy long-lived values into source files.

Use environment variables or the repository's credential management flow.

---

# 11. Anthropic Subscription Setup

## OAuth endpoint

The current TokenGateway documentation uses:

```text
https://api.anthropic.com/v1/oauth/token
```

The OAuth loopback callback is:

```text
http://localhost:54545/callback
```

The documented flow is:

```text
Claude account
    │
    ▼
Browser OAuth
    │
    ▼
localhost:54545/callback
    │
    ▼
access_token
refresh_token
    │
    ▼
TokenGateway credential store
    │
    ▼
LiteLLM
```

Anthropic access tokens are described by the project as short-lived, with a rotating single-use refresh token.

That means:

```text
OLD refresh_token
       │
       ▼
refresh operation
       │
       ├── new access_token
       └── NEW refresh_token
```

Never implement refresh by assuming the old refresh token remains valid forever.

This is a critical failure mode:

```text
Process A refreshes token
Process B uses OLD refresh token
                │
                ▼
         invalid_grant
```

The upstream bridge uses locking/atomic persistence to avoid concurrent refresh races.

---

# 12. OpenAI / ChatGPT Plus Setup

The current TokenGateway implementation routes ChatGPT subscription traffic through the Codex surface.

Current endpoint:

```text
https://chatgpt.com/backend-api/codex/responses
```

OAuth loopback:

```text
http://localhost:1455/auth/callback
```

Flow:

```text
ChatGPT Plus
      │
      ▼
Codex OAuth
      │
      ▼
localhost:1455/auth/callback
      │
      ▼
OpenAI/Codex credential
      │
      ▼
LiteLLM
      │
      ▼
Claude Code /model
```

The bridge uses the OpenAI Responses protocol:

```text
Anthropic-style Claude Code request
              │
              ▼
       LiteLLM adapter
              │
              ▼
 OpenAI Responses request
              │
              ▼
    SSE streaming response
              │
              ▼
       Claude Code stream
```

Tool calls must survive the translation.

Important objects include:

```text
input_text
output_text
function_call
function_call_output
```

The current community implementations specifically address these mappings.

---

# 13. Google AI Pro Setup

The current TokenGateway implementation uses Google's internal Cloud Code / Antigravity surface.

Endpoint:

```text
https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse
```

Loopback callback:

```text
http://localhost:51121/oauth-callback
```

Architecture:

```text
Google AI Pro
      │
      ▼
Google OAuth
      │
      ▼
localhost:51121/oauth-callback
      │
      ▼
Google credential
      │
      ▼
LiteLLM bridge
      │
      ▼
Google Cloud Code wire format
```

The bridge preserves Google-specific data such as:

```text
parametersJsonSchema
thoughtSignature
functionCall
functionResponse
```

This is important because a generic OpenAI-style translation can lose provider-specific reasoning/tool state.

---

# 14. OAuth Desktop Component

The TokenGateway project includes a Tauri desktop component because some first-party OAuth clients use fixed loopback ports.

The documented ports are:

```text
Anthropic → 54545
OpenAI    → 1455
Google    → 51121
```

Conceptually:

```text
                    Browser
                      │
            ┌─────────┼─────────┐
            │         │         │
            ▼         ▼         ▼
        :54545     :1455      :51121
        Claude     OpenAI      Google
            │         │         │
            └─────────┼─────────┘
                      ▼
               OAuth credentials
```

For a local macOS installation, use the project's desktop flow instead of inventing a new callback implementation.

---

# 15. LiteLLM Model Configuration

The clean model naming scheme should be:

```text
Claude:
  claude-opus-...
  claude-sonnet-...
  claude-haiku-...

OpenAI:
  claude-codex-gpt-...

Google:
  claude-gemini-...
```

Example conceptual configuration:

```yaml
model_list:

  # ──────────────────────────────────────
  # Claude subscription models
  # ──────────────────────────────────────

  - model_name: claude-opus
    litellm_params:
      model: anthropic/<current-opus-id>

  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/<current-sonnet-id>

  - model_name: claude-haiku
    litellm_params:
      model: anthropic/<current-haiku-id>


  # ──────────────────────────────────────
  # ChatGPT subscription models
  # ──────────────────────────────────────

  - model_name: claude-codex-gpt
    model_info:
      mode: responses

    litellm_params:
      model: chatgpt/<current-gpt-or-codex-id>
      reasoning:
        effort: medium


  # ──────────────────────────────────────
  # Google subscription models
  # ──────────────────────────────────────

  - model_name: claude-gemini
    litellm_params:
      model: <google-bridge-model-id>


general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY


litellm_settings:
  drop_params: true
```

### Why aliases are necessary

Claude Code's gateway discovery is easier to control when every exposed model starts with:

```text
claude-
```

A GPT model can therefore appear as:

```text
claude-codex-gpt
```

instead of:

```text
gpt-5.x
```

The alias is only the **Claude Code-facing identifier**.

---

# 16. Recommended Alias Catalog

Use stable human-facing aliases:

```text
claude-opus
claude-sonnet
claude-haiku

claude-gpt
claude-gpt-fast
claude-gpt-thinking

claude-gemini
claude-gemini-pro
claude-gemini-flash
```

Map those to exact upstream model IDs in one place.

Example:

```yaml
# Human-facing names:
claude-opus
claude-sonnet
claude-gpt
claude-gemini-pro

# Provider-facing names:
anthropic/<exact-current-model>
chatgpt/<exact-current-model>
<google-bridge>/<exact-current-model>
```

This makes model migrations easier.

Do not put provider version strings throughout your shell configuration.

---

# 17. Claude Code Configuration

Use:

```text
~/.claude/settings.json
```

The current community Claude Code + LiteLLM implementation uses:

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
    "ANTHROPIC_BASE_URL": "http://localhost:4000",
    "ANTHROPIC_MODEL": "claude-opus",
    "ANTHROPIC_CUSTOM_HEADERS": "x-litellm-api-key: Bearer <LITELLM_MASTER_KEY>"
  }
}
```

Do **not** hard-code the example placeholder.

The effective configuration is:

```text
Claude Code
  │
  ├── ANTHROPIC_BASE_URL
  │       ↓
  │   http://localhost:4000
  │
  ├── custom gateway authentication
  │
  └── selected model alias
          ↓
      LiteLLM
```

---

# 18. Remove Direct Anthropic Credentials From Claude Code

Before testing the proxy, make sure Claude Code does not bypass it.

Check:

```bash
env | grep '^ANTHROPIC_'
```

Especially check:

```text
ANTHROPIC_API_KEY
ANTHROPIC_AUTH_TOKEN
ANTHROPIC_BASE_URL
```

For the gateway architecture:

```text
ANTHROPIC_BASE_URL      = http://localhost:4000
ANTHROPIC_API_KEY       = unset
ANTHROPIC_AUTH_TOKEN    = unset
```

The exact final auth mechanism should match the current community gateway configuration.

Do not keep both:

```text
direct Anthropic subscription auth
```

and:

```text
LiteLLM gateway auth
```

active without verifying which one Claude Code selects.

---

# 19. Gateway Model Discovery

Enable:

```text
CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
```

Then Claude Code should request:

```text
GET /v1/models
```

and use the returned model list for selection.

Target behavior:

```text
/model

┌────────────────────────────────────────┐
│ Claude Opus                            │
│ Claude Sonnet                          │
│ Claude Haiku                           │
│ GPT via ChatGPT subscription           │
│ Gemini Pro via Google subscription     │
│ Gemini Flash via Google subscription   │
└────────────────────────────────────────┘
```

The exact display labels can be controlled with the Claude Code environment variables used by the current gateway implementation.

---

# 20. Desired User Experience

The final workflow should be:

```bash
claude
```

Then inside Claude Code:

```text
/model
```

Select:

```text
Claude Opus
Claude Sonnet
GPT
Gemini Pro
Gemini Flash
```

No second agent.

No separate OpenAI CLI.

No separate Gemini CLI.

No manual API key switching.

---

# 21. Optional Shell Shortcuts

Add to `~/.zshrc`:

```bash
alias cc='claude --model claude-sonnet'
alias cco='claude --model claude-opus'
alias ccg='claude --model claude-gpt'
alias ccgp='claude --model claude-gemini-pro'
```

Then:

```bash
cc
cco
ccg
ccgp
```

This is optional.

The `/model` picker should remain the primary interface.

---

# 22. Request Flow

## Claude request

```text
Claude Code
   │
   │ POST /v1/messages
   ▼
LiteLLM
   │
   │ model = claude-opus
   ▼
Anthropic adapter
   │
   │ subscription OAuth
   ▼
Anthropic
   │
   │ SSE
   ▼
LiteLLM
   │
   ▼
Claude Code
```

## GPT request

```text
Claude Code
   │
   │ POST /v1/messages
   │ model = claude-gpt
   ▼
LiteLLM
   │
   ▼
Anthropic → OpenAI Responses translator
   │
   │ subscription credential
   ▼
chatgpt.com/backend-api/codex/responses
   │
   │ SSE
   ▼
OpenAI → Anthropic translator
   │
   ▼
Claude Code
```

## Gemini request

```text
Claude Code
   │
   │ POST /v1/messages
   │ model = claude-gemini-pro
   ▼
LiteLLM
   │
   ▼
Google wire translator
   │
   │ OAuth subscription credential
   ▼
Cloud Code / Antigravity
   │
   │ SSE
   ▼
Google → Anthropic translator
   │
   ▼
Claude Code
```

---

# 23. Tool Calling Is the Critical Test

Text generation is not enough.

Every provider must pass:

```text
1. simple text
2. streaming
3. tool declaration
4. tool call
5. tool result
6. second model turn
7. reasoning/tool state
8. long context
```

Test sequence:

```text
Claude Code
   │
   ▼
"List the files in this repository."
   │
   ▼
Claude Code tool
   │
   ▼
model receives tool result
   │
   ▼
model responds
```

Repeat with:

```text
Claude
GPT
Gemini
```

---

# 24. Required Smoke Tests

## Test 1 — Gateway alive

```bash
curl -s http://127.0.0.1:4000/health
```

Expected:

```text
healthy / successful response
```

## Test 2 — Model discovery

```bash
curl -s \
  -H "Authorization: Bearer <LITELLM_MASTER_KEY>" \
  http://127.0.0.1:4000/v1/models
```

Verify your aliases appear.

## Test 3 — Claude

```bash
claude --model claude-opus
```

Run:

```text
Explain this repository architecture.
```

## Test 4 — GPT

```bash
claude --model claude-gpt
```

Run:

```text
Inspect the repository and propose the smallest safe refactor.
```

Require at least one tool call.

## Test 5 — Gemini

```bash
claude --model claude-gemini-pro
```

Run:

```text
Inspect the repository and find three likely failure points.
```

Require at least one tool call.

---

# 25. Streaming Test

Every model must stream.

Expected:

```text
request
  │
  ▼
provider
  │
  ├── chunk
  ├── chunk
  ├── tool event
  ├── chunk
  └── done
  │
  ▼
Claude Code
```

A model that only works as a completed JSON response is not acceptable for this architecture.

---

# 26. Tool Translation Test

For each provider:

```text
Claude Code
   │
   ▼
tool definition
   │
   ▼
provider wire format
   │
   ▼
function call
   │
   ▼
tool execution
   │
   ▼
tool result
   │
   ▼
provider
   │
   ▼
final answer
```

Check specifically:

```text
function name
arguments
tool call ID
tool result association
stream ordering
reasoning state
```

---

# 27. Reasoning / Thinking Compatibility

This is one of the hardest parts of the design.

Provider formats differ.

```text
Claude
  └── thinking blocks / signatures

OpenAI
  └── Responses reasoning items

Google
  └── thoughtSignature
```

The translator must preserve enough state for the next turn.

Do not assume:

```text
reasoning = ordinary text
```

It is often structured state.

The current community bridges explicitly handle OpenAI Responses reasoning and Google's `thoughtSignature`.

---

# 28. Context Window Handling

Do not advertise a larger context window than the subscription surface actually accepts.

The current Claude Code + Codex community configuration uses a conservative split for GPT-5.5/5.6 subscription routing:

```text
max_input_tokens  = 272000
max_output_tokens = 128000
total             = 400000
```

The important rule is:

```text
Claude Code prompt size
        ≤
actual subscription input limit
```

Otherwise a long tool session can fail even though the public API model documentation reports a larger total window.

Treat each subscription surface as its own capability profile.

---

# 29. Capability Matrix

Maintain a table like:

| Capability | Claude | GPT | Gemini |
|---|---:|---:|---:|
| Streaming | yes | yes | yes |
| Text | yes | yes | yes |
| Tool calls | yes | yes | yes |
| Multi-turn tools | verify | verify | verify |
| Reasoning | yes | yes | yes |
| Web/search tool | provider dependent | provider dependent | provider dependent |
| Long context | subscription-dependent | subscription-dependent | subscription-dependent |
| Native protocol | Anthropic | OpenAI Responses | Google Cloud Code |

Update this table when upstream implementations change.

---

# 30. Credential Storage

Recommended local structure:

```text
~/Developer/ai-gateway/tokengateway/
│
├── .env
├── data/
│   └── credentials.json
└── ...
```

Protect:

```bash
chmod 600 .env
chmod 600 data/credentials.json
```

Also protect Docker volume data.

Never commit:

```text
.env
credentials.json
auth.json
OAuth access tokens
OAuth refresh tokens
```

Add to `.gitignore`:

```gitignore
.env
data/
*.json
auth.json
credentials.json
```

Use narrower ignore rules if the repository contains legitimate JSON files. Do not blindly ignore every JSON file in a real project.

---

# 31. Security Boundary

The gateway should be local-only:

```text
                 Internet
                    X
                    │
                    │ NO PUBLIC ACCESS
                    │
              ┌─────▼─────┐
              │  macOS     │
              │ 127.0.0.1  │
              │    :4000    │
              └────────────┘
                    ▲
                    │
              Claude Code
```

Do not do:

```text
0.0.0.0:4000
```

unless you fully understand the security consequences.

The upstream TokenGateway repository explicitly lists unresolved security issues in its dashboard and credential synchronization components. For a single-user local deployment, avoid exposing those components beyond localhost.

---

# 32. Do Not Use the Remote/Kubernetes Architecture

The repository also supports:

```text
Desktop
   │
   ▼
Remote dashboard
   │
   ▼
Kubernetes
   │
   ▼
LiteLLM
```

Do not build this first.

Your required architecture is:

```text
Mac
 ├── OAuth desktop component
 ├── Docker Compose
 │    ├── dashboard
 │    └── LiteLLM
 └── Claude Code
```

This removes:

```text
Kubernetes
RBAC
remote credential sync
TLS termination
remote secrets
public ingress
```

from the initial system.

---

# 33. Process Supervision

The final local stack should survive terminal closure.

Use Docker Compose:

```bash
docker compose up -d
```

Then:

```bash
docker compose ps
```

Claude Code runs independently.

Desired process model:

```text
launchd / Docker Desktop
       │
       ▼
TokenGateway containers
       │
       ▼
LiteLLM :4000
       ▲
       │
Claude Code
```

Do not make Claude Code responsible for starting LiteLLM on every request.

---

# 34. Start/Stop Commands

Start:

```bash
cd ~/Developer/ai-gateway/tokengateway
docker compose up -d
```

Status:

```bash
docker compose ps
```

Logs:

```bash
docker compose logs -f litellm
```

Stop:

```bash
docker compose down
```

Restart:

```bash
docker compose restart
```

Rebuild after source/config changes:

```bash
docker compose up -d --build
```

---

# 35. Debugging Ladder

Always debug from the bottom upward.

```text
Layer 1 ─ Docker
   │
Layer 2 ─ LiteLLM
   │
Layer 3 ─ OAuth credential
   │
Layer 4 ─ Provider endpoint
   │
Layer 5 ─ model alias
   │
Layer 6 ─ Claude Code
   │
Layer 7 ─ tool calling
```

## Layer 1

```bash
docker compose ps
```

## Layer 2

```bash
curl http://127.0.0.1:4000/health
```

## Layer 3

Check the credential file or credential API state.

## Layer 4

Inspect LiteLLM logs.

## Layer 5

Check:

```bash
curl ... /v1/models
```

## Layer 6

Run:

```bash
claude --model <alias>
```

## Layer 7

Run a real tool call.

---

# 36. Most Important Failure Modes

## Failure A — Claude Code bypasses LiteLLM

Symptoms:

```text
Claude works
LiteLLM logs show nothing
```

Check:

```bash
ANTHROPIC_BASE_URL
ANTHROPIC_API_KEY
ANTHROPIC_AUTH_TOKEN
```

The goal is for the request to reach:

```text
127.0.0.1:4000
```

## Failure B — `invalid_grant`

Usually means an OAuth refresh-token race or stale token.

Do not manually run multiple refresh processes.

Restart the gateway and re-authenticate the provider if required.

## Failure C — GPT text works but tools fail

Inspect:

```text
function_call
function_call_output
reasoning
stream ordering
```

This indicates a protocol translation problem, not normal API authentication.

## Failure D — Gemini works but tool calls fail

Inspect:

```text
parametersJsonSchema
thoughtSignature
functionCall
functionResponse
```

## Failure E — model missing from `/model`

Check:

```text
model alias starts with claude-
gateway model discovery enabled
/v1/models contains the alias
```

---

# 37. Model Naming Strategy

Use a two-layer naming scheme.

## User-facing

```text
claude-opus
claude-sonnet
claude-gpt
claude-gemini-pro
```

## Backend-facing

```text
anthropic/<current-opus-id>
anthropic/<current-sonnet-id>

chatgpt/<current-openai-id>

<google-bridge>/<current-gemini-id>
```

This gives:

```text
Claude Code config
       │
       ▼
stable aliases
       │
       ▼
LiteLLM model mapping
       │
       ▼
provider-specific IDs
```

When a provider changes a model ID, edit only LiteLLM.

---

# 38. Recommended Directory Layout

```text
~/Developer/ai-gateway/
│
└── tokengateway/
    │
    ├── .env
    ├── docker-compose.yml
    │
    ├── config/
    │   └── litellm.yaml
    │
    ├── data/
    │   └── credentials.json
    │
    ├── litellm-plugin/
    │   └── sitecustomize.py
    │
    ├── scripts/
    │   ├── start.sh
    │   ├── stop.sh
    │   └── health.sh
    │
    └── docs/
        └── local-architecture.md
```

Do not modify upstream source until the stock configuration works.

---

# 39. Build Order

Follow this order exactly.

```text
[1] Install Docker
        │
        ▼
[2] Clone TokenGateway
        │
        ▼
[3] Start stock Docker Compose stack
        │
        ▼
[4] Confirm LiteLLM :4000
        │
        ▼
[5] Configure Anthropic OAuth
        │
        ▼
[6] Test Claude model
        │
        ▼
[7] Configure OpenAI/Codex OAuth
        │
        ▼
[8] Test GPT model
        │
        ▼
[9] Configure Google OAuth
        │
        ▼
[10] Test Gemini model
        │
        ▼
[11] Add model aliases
        │
        ▼
[12] Configure Claude Code gateway
        │
        ▼
[13] Enable gateway model discovery
        │
        ▼
[14] Test /model
        │
        ▼
[15] Test tool calls on all providers
        │
        ▼
[16] Lock down localhost-only access
        │
        ▼
[17] Add shell shortcuts
```

Do not change all three providers at once.

---

# 40. Acceptance Criteria

The build is complete only when all are true.

### Gateway

```text
[ ] LiteLLM running on 127.0.0.1:4000
[ ] Docker Compose survives terminal closure
[ ] No public ingress
```

### Claude

```text
[ ] Claude subscription OAuth works
[ ] Claude model appears in /model
[ ] Streaming works
[ ] Tool calls work
[ ] Multi-turn tool calls work
```

### OpenAI

```text
[ ] ChatGPT/Codex OAuth works
[ ] GPT alias appears in /model
[ ] Streaming works
[ ] Tool calls work
[ ] Multi-turn tool calls work
[ ] Reasoning state survives the next turn
```

### Google

```text
[ ] Google OAuth works
[ ] Gemini alias appears in /model
[ ] Streaming works
[ ] Tool calls work
[ ] Multi-turn tool calls work
[ ] thoughtSignature handling works
```

### Claude Code

```text
[ ] One Claude Code installation is used
[ ] /model changes providers
[ ] No API-key billing is required by the target subscription path
[ ] Claude Code traffic reaches localhost:4000
```

---

# 41. Operational Diagram

The finished system should look like this:

```text
                         ┌─────────────────────┐
                         │      YOU / CLI      │
                         │                     │
                         │      claude         │
                         │        │            │
                         │        ▼            │
                         │      /model         │
                         └────────┬────────────┘
                                  │
                                  │ Anthropic API
                                  ▼
                    ┌──────────────────────────────┐
                    │     LiteLLM @ 127.0.0.1     │
                    │             :4000            │
                    │                              │
                    │   ┌──────────────────────┐   │
                    │   │   MODEL ROUTER        │   │
                    │   │                      │   │
                    │   │ claude-opus          │   │
                    │   │ claude-sonnet        │   │
                    │   │ claude-gpt            │   │
                    │   │ claude-gemini-pro     │   │
                    │   └──────────┬───────────┘   │
                    │              │               │
                    │       wire translation       │
                    └──────────────┼───────────────┘
                                   │
                 ┌─────────────────┼─────────────────┐
                 │                 │                 │
                 ▼                 ▼                 ▼
          ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
          │  Anthropic  │   │   OpenAI    │   │   Google    │
          │ Subscription│   │ Subscription│   │ Subscription│
          │   OAuth     │   │    OAuth    │   │    OAuth    │
          └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
                 │                 │                 │
                 ▼                 ▼                 ▼
             Claude            Codex API        Cloud Code
```

---

# 42. OAuth Loopback Diagram

```text
             ┌───────────────┐
             │    Browser    │
             └───────┬───────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
   localhost      localhost      localhost
    :54545         :1455          :51121
        │            │            │
        │            │            │
     Anthropic      OpenAI        Google
        │            │            │
        └────────────┼────────────┘
                     ▼
              Credential store
                     │
                     ▼
                 LiteLLM
```

---

# 43. Subscription Billing Boundary

This is important.

```text
                 YOUR SUBSCRIPTIONS
                         │
           ┌─────────────┼─────────────┐
           │             │             │
           ▼             ▼             ▼
       Claude Pro    ChatGPT Plus   Google AI Pro
           │             │             │
           └─────────────┼─────────────┘
                         │
                         ▼
                  OAuth credentials
                         │
                         ▼
                  Local LiteLLM
                         │
                         ▼
                    Claude Code
```

The goal is **not**:

```text
Claude Code
    │
    ▼
API key
    │
    ▼
per-token billing
```

The target is the subscription/CLI surfaces implemented by the community bridges.

---

# 44. Important Current-State Caveats

The architecture is technically feasible, but there are three independent risks.

## 44.1 Provider-side changes

These are not stable public APIs.

The bridge depends on first-party CLI/subscription interfaces.

A provider can change:

```text
endpoint
OAuth flow
client ID
model IDs
request schema
quota behavior
```

without preserving compatibility.

## 44.2 Community software

TokenGateway is community software. Its README currently calls out several unresolved security issues.

Use it as a local single-user tool, not as a public multi-user service.

## 44.3 Subscription terms

Using subscription OAuth credentials through an intermediary is not the same thing as using a provider's normal developer API.

Review the current terms for each provider before relying on this as a long-term workflow.

---

# 45. Preferred Implementation Strategy

Do not write your own protocol translators initially.

Use:

```text
TokenGateway
      │
      ├── existing OAuth implementation
      ├── existing refresh-token handling
      ├── existing Anthropic translation
      ├── existing OpenAI Responses translation
      └── existing Google Cloud Code translation
```

Only write custom glue for:

```text
model aliases
Claude Code settings
local startup commands
security hardening
health checks
```

This minimizes maintenance.

---

# 46. Recommended Final Workflow

Daily use:

```bash
cd ~/project
claude
```

Inside Claude Code:

```text
/model
```

Select:

```text
Claude Opus
Claude Sonnet
GPT
Gemini Pro
Gemini Flash
```

To switch instantly from shell:

```bash
claude --model claude-opus
claude --model claude-gpt
claude --model claude-gemini-pro
```

Everything else remains unchanged:

```text
same terminal
same repository
same MCP tools
same Claude Code UI
same Claude Code agent behavior
different model backend
```

---

# 47. Reference Sources

Primary implementation:

- TokenGateway repository  
  https://github.com/eduardopessin/tokengateway

- TokenGateway LiteLLM plugin  
  https://github.com/eduardopessin/tokengateway/tree/main/litellm-plugin

- Anthropic subscription setup  
  https://github.com/eduardopessin/tokengateway/blob/main/docs/oauth-setup-anthropic.md

- OpenAI/ChatGPT subscription setup  
  https://github.com/eduardopessin/tokengateway/blob/main/docs/oauth-setup-openai.md

- Google subscription setup  
  https://github.com/eduardopessin/tokengateway/blob/main/docs/oauth-setup-google.md

Related Claude Code + LiteLLM implementation:

- https://github.com/papunoko/claude-litellm

Official Claude Code gateway documentation:

- https://docs.anthropic.com/en/docs/claude-code/llm-gateway

Official Gemini CLI authentication/quota documentation:

- https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.md
- https://github.com/google-gemini/gemini-cli/blob/main/docs/resources/quota-and-pricing.md

---

# 48. One-Page Build Checklist

```text
┌──────────────────────────────────────────────────────────────┐
│              CLAUDE CODE MULTI-SUBSCRIPTION                 │
│                       BUILD CHECKLIST                       │
├──────────────────────────────────────────────────────────────┤
│ [ ] Docker Desktop installed                                │
│ [ ] TokenGateway cloned                                      │
│ [ ] .env created                                             │
│ [ ] random LITELLM_MASTER_KEY generated                     │
│ [ ] docker compose up -d                                   │
│ [ ] LiteLLM health check passes                            │
│ [ ] Anthropic OAuth connected                              │
│ [ ] Claude model tested                                    │
│ [ ] OpenAI/Codex OAuth connected                           │
│ [ ] GPT model tested                                       │
│ [ ] Google OAuth connected                                 │
│ [ ] Gemini model tested                                    │
│ [ ] Stable Claude-facing aliases added                    │
│ [ ] Claude Code ANTHROPIC_BASE_URL = localhost:4000       │
│ [ ] gateway model discovery enabled                       │
│ [ ] /model shows all provider aliases                      │
│ [ ] Claude tool call passes                                │
│ [ ] GPT tool call passes                                   │
│ [ ] Gemini tool call passes                                │
│ [ ] multi-turn tool calls pass for all three              │
│ [ ] credentials chmod 600                                 │
│ [ ] secrets excluded from git                             │
│ [ ] gateway bound only to localhost                       │
│ [ ] daily startup workflow verified                       │
└──────────────────────────────────────────────────────────────┘
```

---

# 49. Bottom Line

The target architecture is:

```text
                    ┌───────────────────────┐
                    │      CLAUDE CODE      │
                    │       one CLI         │
                    └───────────┬───────────┘
                                │
                         localhost:4000
                                │
                                ▼
                    ┌───────────────────────┐
                    │       LiteLLM         │
                    │    model router       │
                    └──────┬─────┬─────┬────┘
                           │     │     │
                           ▼     ▼     ▼
                       Claude   GPT   Gemini
                       OAuth   OAuth  OAuth
                           │     │     │
                           ▼     ▼     ▼
                        Claude  ChatGPT Google
                        Pro/Max  Plus    AI Pro
```

**Do not start by building a custom LiteLLM provider. Start with TokenGateway's existing OAuth + wire bridge, run it locally with Docker Compose, validate one provider at a time, then expose stable `claude-*` aliases to Claude Code.**

This is the shortest path to the exact UX you want while keeping the gateway local and avoiding normal per-token API credentials.


---

# 50. Autonomous Agent Execution Contract

This section is an instruction set for the coding agent that will implement this system.

## 50.1 Primary objective

Build the complete system from start to finish.

The final product must be:

```text
ONE REPRODUCIBLE INSTALLER
        │
        ▼
   ./install.sh
        │
        ▼
Bubble Tea TUI
        │
        ▼
Preflight everything
        │
        ▼
Authenticate what is missing
        │
        ▼
Execute full installation
        │
        ▼
Run validation
        │
        ▼
READY
```

Do not stop at a partial manual setup.

Do not provide only instructions.

Do not leave required setup steps for the user.

The final deliverable must include a working script that performs the complete setup.

---

## 50.2 Exact implementation requirement

Follow this document to a T.

Handle every relevant case described in the plan, including:

```text
[ ] dependency checks
[ ] macOS checks
[ ] architecture checks
[ ] Git checks
[ ] Docker checks
[ ] Docker Compose checks
[ ] Claude Code installation checks
[ ] Claude subscription/auth checks
[ ] ChatGPT subscription/Codex auth checks
[ ] Google AI Pro/Google auth checks
[ ] missing-auth flows
[ ] OAuth browser flows
[ ] OAuth callback handling
[ ] credential persistence
[ ] refresh-token handling
[ ] model alias configuration
[ ] LiteLLM configuration
[ ] wire-bridge configuration
[ ] Claude Code gateway configuration
[ ] model discovery
[ ] streaming
[ ] tool calls
[ ] multi-turn tool calls
[ ] reasoning/tool state
[ ] health checks
[ ] failure detection
[ ] secure permissions
[ ] localhost-only binding
[ ] idempotent/re-runnable setup where practical
[ ] final end-to-end validation
[ ] final installer generation
```

Do not silently skip a case because it is inconvenient.

If an upstream implementation changed, adapt the implementation while preserving the architecture and end goal.

---

# 51. Fully Autonomous Agent Rules

The implementation agent must operate fully autonomously.

```text
DO NOT ASK THE USER QUESTIONS.
DO NOT REQUEST CONFIRMATION.
DO NOT STOP FOR NORMAL DECISIONS.
DO NOT WAIT FOR USER INPUT EXCEPT REQUIRED EXTERNAL OAUTH ACTION.
```

Make reasonable technical decisions from the current environment and this document.

The only unavoidable human action may be an external provider OAuth login when the provider requires browser authentication.

For that case:

```text
agent
  │
  ▼
launch browser
  │
  ▼
provider login / consent
  │
  ▼
OAuth callback
  │
  ▼
agent continues automatically
```

Do not ask the user which directory, port, framework, provider, or implementation strategy to use when this document already defines it.

Use the safest reasonable default.

---

# 52. Agent Response Style

The agent's text responses to the user must be extremely token efficient.

Use:

```text
caveman style
short
direct
status only
```

Examples:

```text
Docker: PASS
Claude: PASS
OpenAI: AUTH NEEDED
Google: PASS
Installing...
Testing...
DONE
```

Avoid:

```text
long explanations
progress essays
architecture recaps
repeated warnings
verbose commentary
```

The agent should communicate only what is necessary.

**This applies to the agent's responses to the user.**

It does **not** mean code comments, documentation, logs, or error messages should be malformed or unclear. Code and machine-readable output must remain technically precise.

---

# 53. Never Touch Existing User Stuff

This is a hard requirement.

The installer must not modify unrelated existing user state.

```text
┌─────────────────────────────────────────────┐
│              EXISTING USER STATE             │
├─────────────────────────────────────────────┤
│ existing projects             → UNTOUCHED   │
│ existing repositories         → UNTOUCHED   │
│ existing .zshrc               → UNTOUCHED   │
│ existing .bashrc              → UNTOUCHED   │
│ existing SSH config           → UNTOUCHED   │
│ existing Git config           → UNTOUCHED   │
│ existing Claude projects      → UNTOUCHED   │
│ existing Claude conversations → UNTOUCHED   │
│ existing editor config        → UNTOUCHED   │
│ existing MCP config           → UNTOUCHED   │
│ existing env files            → UNTOUCHED   │
└─────────────────────────────────────────────┘
```

The new system must live in an isolated root.

Recommended:

```text
~/ai-gateway/
```

or another dedicated directory chosen automatically.

Do not overwrite an existing directory without handling it safely.

Prefer:

```text
~/ai-gateway/
```

with explicit subdirectories.

---

# 54. Isolated Installation Boundary

The final installer must create a dedicated environment:

```text
~/ai-gateway/
│
├── tokengateway/
├── config/
├── credentials/
├── data/
├── logs/
├── scripts/
├── tests/
└── install.sh
```

The installer should know its own root:

```bash
INSTALL_ROOT="${HOME}/ai-gateway"
```

All generated files should resolve relative to this root.

Avoid writing to:

```text
~/.zshrc
~/.bashrc
~/.profile
~/Documents
~/Desktop
arbitrary project repositories
```

unless explicitly required by the isolated design.

---

# 55. Do Not Modify Existing Claude Code Configuration

The installer must not blindly overwrite:

```text
~/.claude/settings.json
```

or equivalent existing Claude configuration.

Instead, isolate the gateway configuration.

Preferred model:

```text
Existing Claude Code
       │
       │ untouched
       ▼

New gateway launcher
       │
       ▼
isolated environment variables
       │
       ▼
Claude Code
       │
       ▼
localhost LiteLLM
```

The final system may use a dedicated launcher/wrapper to inject:

```text
ANTHROPIC_BASE_URL
gateway authentication
model discovery settings
```

without rewriting unrelated global configuration.

If a Claude Code feature requires a config file, create an isolated config path or an isolated launch environment where supported.

---

# 56. Installation Flow

The installer is **not**:

```text
run shell commands immediately
```

It is:

```text
START
  │
  ▼
READ-ONLY PREFLIGHT
  │
  ▼
SHOW RESULTS IN BUBBLE TEA
  │
  ├── requirements missing
  │        │
  │        ▼
  │   repair/authenticate
  │        │
  │        ▼
  │     re-check
  │
  └── requirements pass
           │
           ▼
     install system
           │
           ▼
       test system
           │
           ▼
          DONE
```

The installer must perform checks before making installation changes.

---

# 57. Bubble Tea TUI

Use a Bubble Tea TUI for the installer front end.

Purpose:

```text
better status
clear preflight
OAuth state
errors
progress
final result
```

The TUI should remain simple.

Example:

```text
╭────────────────────────────────────────────────╮
│ Claude Code Multi-Model Gateway Installer      │
├────────────────────────────────────────────────┤
│                                                │
│ Preflight                                      │
│                                                │
│ ✓ macOS                                        │
│ ✓ Apple Silicon                                │
│ ✓ Git                                           │
│ ✓ Docker                                        │
│ ✓ Docker Compose                                │
│ ✓ Claude Code                                   │
│ ✓ Claude subscription                           │
│ ! ChatGPT subscription auth                     │
│ ✓ Google subscription auth                      │
│                                                │
│ Status: AUTH NEEDED                             │
│                                                │
│ [ Continue ]                                   │
╰────────────────────────────────────────────────╯
```

Do not make the TUI visually complex.

Keep it robust and readable.

---

# 58. Preflight: Check Everything First

The preflight phase must be read-only as much as practical.

Check:

```text
Operating system
CPU architecture
macOS version
Git
Docker binary
Docker daemon
Docker Compose
required ports
network connectivity
Claude Code executable
Claude Code version
Claude subscription auth
ChatGPT/Codex auth
Google AI Pro/Google auth
existing installation root
required permissions
required filesystem access
```

Do not assume a binary being installed means the service works.

Example:

```text
Docker binary exists
        ≠
Docker daemon ready
```

Check both.

---

# 59. Preflight State Machine

```text
                  ┌─────────────┐
                  │    START    │
                  └──────┬──────┘
                         │
                         ▼
                 ┌───────────────┐
                 │ Run preflight │
                 └───────┬───────┘
                         │
             ┌───────────┼────────────┐
             │            │            │
             ▼            ▼            ▼
           PASS        AUTH NEEDED   BLOCKED
             │            │            │
             │            ▼            │
             │       OAuth flow        │
             │            │            │
             │            ▼            │
             │       Re-check          │
             │            │            │
             └────────────┼────────────┘
                          │
                    all requirements
                       satisfied?
                     ┌────┴────┐
                    NO         YES
                    │           │
                    ▼           ▼
                  repair     install
                               │
                               ▼
                              test
                               │
                               ▼
                              done
```

---

# 60. Subscription/Auth Preflight

Check each provider separately.

```text
Claude Pro/Max
    │
    ├── credential present?
    ├── token usable?
    └── refresh usable?
```

```text
ChatGPT Plus / Codex
    │
    ├── credential present?
    ├── token usable?
    └── refresh usable?
```

```text
Google AI Pro
    │
    ├── credential present?
    ├── token usable?
    └── refresh usable?
```

Do not infer subscription status from an arbitrary browser cookie.

Use the authentication mechanism supported by the chosen bridge.

---

# 61. Missing Authentication Flow

When a provider is missing auth:

```text
TUI
 │
 ▼
"AUTH NEEDED"
 │
 ▼
Launch official provider OAuth
 │
 ▼
Browser
 │
 ▼
Callback listener
 │
 ▼
Persist credential
 │
 ▼
Re-check
 │
 ├── PASS → continue
 │
 └── FAIL → show concise error and retry/re-auth path
```

Example:

```text
╭────────────────────────────────────────────╮
│ OpenAI / ChatGPT                           │
├────────────────────────────────────────────┤
│                                            │
│ Subscription found.                       │
│ OAuth not configured.                     │
│                                            │
│ Opening browser...                        │
│ Waiting for callback...                   │
│                                            │
╰────────────────────────────────────────────╯
```

After callback:

```text
✓ OpenAI auth
```

Do not make the user manually paste tokens when the supported OAuth flow can run automatically.

---

# 62. Execution Stage

Only after preflight passes:

```text
START INSTALL
```

Then:

```text
[1] create isolated directories
[2] clone/update TokenGateway
[3] generate local secrets
[4] configure environment
[5] configure LiteLLM
[6] configure wire bridge
[7] configure provider credentials
[8] start Docker Compose
[9] wait for health
[10] configure isolated Claude Code gateway environment
[11] enable model discovery
[12] run smoke tests
[13] run tool tests
[14] run multi-turn tests
[15] run security checks
[16] generate final installer
[17] print result
```

---

# 63. Installer Must Be Reproducible

The agent should produce:

```text
install.sh
```

that performs the entire setup.

Target:

```bash
./install.sh
```

The script should:

```text
detect current state
authenticate if required
install missing dependencies where reasonable
configure the isolated system
start services
validate
```

Re-running the installer should not corrupt the installation.

Prefer idempotent operations:

```text
mkdir -p
safe config generation
docker compose up -d
existing credential reuse
existing repo update
version-aware migrations
```

Use backups for any unavoidable isolated-file replacement.

---

# 64. Installer Structure

Recommended:

```text
install.sh
   │
   ├── preflight
   │
   ├── auth
   │
   ├── install
   │
   ├── configure
   │
   ├── test
   │
   └── report
```

The Bubble Tea application may invoke separate deterministic scripts:

```text
installer/
├── tui
├── preflight.sh
├── auth.sh
├── install_core.sh
├── configure.sh
├── test.sh
└── install.sh
```

Final UX should still be:

```bash
./install.sh
```

---

# 65. Error Handling

Every major command must check its exit status.

Bad:

```bash
docker compose up -d
echo "done"
```

Good:

```bash
if ! docker compose up -d; then
    fail "Docker Compose failed"
fi
```

The installer must stop on unrecoverable errors.

It must not print:

```text
SUCCESS
```

after a failed provider setup.

---

# 66. Failure Recovery

For recoverable failures:

```text
detect
  │
  ▼
classify
  │
  ├── missing dependency → install/retry
  ├── missing auth       → OAuth/retry
  ├── expired token      → refresh/re-auth
  ├── service not ready  → wait/retry
  └── real failure       → stop + concise error
```

Do not loop forever.

Use bounded retry counts and timeouts.

---

# 67. Final Validation Matrix

The installer must test:

```text
                 Claude   GPT   Gemini
─────────────────────────────────────────
health             ✓       ✓      ✓
basic request      ✓       ✓      ✓
streaming          ✓       ✓      ✓
tool call          ✓       ✓      ✓
multi-turn tool    ✓       ✓      ✓
reasoning state    ✓       ✓      ✓
model discovery    ✓       ✓      ✓
```

Do not declare success until the required matrix passes.

---

# 68. Final System Verification

The installer should prove:

```text
Claude Code
    │
    ▼
127.0.0.1:4000
    │
    ▼
correct provider
```

Do not rely only on the model returning text.

Verify gateway logs/network behavior where practical.

Desired:

```text
claude-opus
    → Anthropic bridge

claude-gpt
    → OpenAI/Codex bridge

claude-gemini-pro
    → Google bridge
```

---

# 69. Security Verification

Before final success:

```text
[ ] gateway bound to localhost
[ ] no unintended public bind
[ ] credentials not in Git
[ ] secrets have restrictive permissions
[ ] no tokens printed in logs
[ ] no tokens printed by TUI
[ ] user projects untouched
[ ] global dotfiles untouched
[ ] existing Claude config untouched
[ ] isolated directory used
```

If any security check fails, do not print a clean success result.

---

# 70. Final TUI Result

Success should be compact:

```text
╭────────────────────────────────────────────╮
│ Installation complete                     │
├────────────────────────────────────────────┤
│ ✓ LiteLLM                                  │
│ ✓ Claude                                   │
│ ✓ GPT                                      │
│ ✓ Gemini                                   │
│ ✓ Streaming                                │
│ ✓ Tools                                    │
│ ✓ Multi-turn                               │
│ ✓ Local-only gateway                       │
│                                            │
│ Root: ~/ai-gateway                         │
╰────────────────────────────────────────────╯
```

Failure should be compact:

```text
╭────────────────────────────────────────────╮
│ Installation failed                       │
├────────────────────────────────────────────┤
│ ✗ Gemini tool test                        │
│                                            │
│ Log: ~/ai-gateway/logs/install.log        │
╰────────────────────────────────────────────╯
```

---

# 71. Final Deliverable

The coding agent must leave the machine with:

```text
~/ai-gateway/
│
├── install.sh                 ← FINAL ENTRY POINT
├── uninstall.sh               ← optional but recommended
├── tokengateway/
├── config/
├── credentials/
├── data/
├── logs/
├── scripts/
├── tests/
└── docs/
```

The most important artifact is:

```text
install.sh
```

A fresh compatible machine should be able to run:

```bash
./install.sh
```

and have the complete system configured.

---

# 72. Proposed Complete Install Flow

```text
                         ./install.sh
                              │
                              ▼
                 ┌────────────────────────┐
                 │      Bubble Tea TUI    │
                 │        START           │
                 └───────────┬────────────┘
                             │
                             ▼
                 ┌────────────────────────┐
                 │   READ-ONLY PREFLIGHT  │
                 └───────────┬────────────┘
                             │
          ┌──────────────────┼───────────────────┐
          │                  │                   │
          ▼                  ▼                   ▼
     Claude Code          Docker/Git        OS/System
       check                check             checks
          │                  │                   │
          └──────────────────┼───────────────────┘
                             │
                             ▼
                 ┌────────────────────────┐
                 │   SUBSCRIPTION CHECK   │
                 ├────────────────────────┤
                 │ Claude Pro/Max         │
                 │ ChatGPT Plus/Codex     │
                 │ Google AI Pro          │
                 └───────────┬────────────┘
                             │
                ┌────────────┼────────────┐
                │            │            │
                ▼            ▼            ▼
              PASS        AUTH NEEDED   BLOCKED
                │            │            │
                │            ▼            │
                │       OAuth browser     │
                │            │            │
                │            ▼            │
                │        Re-check         │
                └────────────┼────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ ALL PREFLIGHT = PASS   │
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ CREATE ISOLATED ROOT    │
                │ ~/ai-gateway/           │
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ INSTALL / CONFIGURE     │
                │ TokenGateway             │
                │ LiteLLM                  │
                │ wire bridges             │
                │ aliases                  │
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ START LOCAL SERVICES     │
                │ 127.0.0.1:4000         │
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ CONFIGURE CLAUDE CODE   │
                │ isolated launcher/env   │
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ END-TO-END TEST MATRIX  │
                │ Claude / GPT / Gemini   │
                │ stream / tools / turns  │
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ SECURITY + INTEGRITY    │
                │ checks                  │
                └────────────┬────────────┘
                             │
                             ▼
                ┌─────────────────────────┐
                │ FINAL RESULT             │
                │                          │
                │ install.sh ready        │
                │ system ready            │
                └─────────────────────────┘
```

---

# 73. Hard Rules Summary

```text
1. FOLLOW THE ENTIRE PLAN.
2. WORK AUTONOMOUSLY.
3. DO NOT ASK THE USER QUESTIONS.
4. KEEP USER-FACING TEXT CAVEMAN-SHORT.
5. DO NOT TOUCH EXISTING USER PROJECTS.
6. DO NOT MODIFY UNRELATED DOTFILES.
7. DO NOT OVERWRITE EXISTING CLAUDE CONFIG.
8. USE AN ISOLATED INSTALL ROOT.
9. CHECK EVERYTHING BEFORE INSTALLING.
10. USE BUBBLE TEA FOR PREFLIGHT/AUTH/STATUS.
11. AUTHENTICATE ONLY WHAT IS MISSING.
12. BUILD THE WHOLE SYSTEM.
13. TEST ALL THREE PROVIDERS.
14. TEST STREAMING.
15. TEST TOOLS.
16. TEST MULTI-TURN.
17. TEST MODEL DISCOVERY.
18. TEST SECURITY.
19. HANDLE FAILURES.
20. LEAVE A SINGLE REPRODUCIBLE install.sh.
```

---

# 74. Agent Completion Condition

Do not finish with:

```text
"here is how you could install it"
```

Finish with:

```text
"the isolated installation works and install.sh reproduces it"
```

The agent's final response should be minimal:

```text
DONE

Installer:
~/ai-gateway/install.sh

Tests:
Claude ✓
GPT ✓
Gemini ✓
Tools ✓
Streaming ✓

Existing user stuff untouched.
```

If a required test fails, report only the relevant failure and do not claim completion.
