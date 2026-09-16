#!/usr/bin/env python3
"""TriRoute end-to-end validation matrix (hardened plan §12 + original plan §24/§67).

For every authenticated provider: basic text, streaming SSE, single tool call,
and a multi-turn tool round-trip (tool_result -> final answer). Providers
without credentials are reported MANUAL (exit code keeps them non-fatal unless
--require is passed).

Usage: python3 tests/validate.py [--provider claude|gpt|gemini] [--require]
"""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:4000"

PROVIDER_MODELS = {
    "claude": ["claude-sonnet", "claude-opus"],
    "gpt": ["claude-gpt", "claude-gpt-fast"],
    "gemini": ["claude-gemini-pro", "claude-gemini-flash"],
}
CREDENTIAL_KEY = {"claude": "anthropic", "gpt": "openai-codex", "gemini": "google-antigravity"}

results = []


def record(name, status, detail=""):
    results.append((name, status))
    marker = {"PASS": "\033[1;32m[PASS]\033[0m", "FAIL": "\033[1;31m[FAIL]\033[0m",
              "MANUAL": "\033[1;33m[MANUAL]\033[0m"}[status]
    print(f"{marker} {name}" + (f" — {detail}" if detail else ""))


def master_key():
    env = os.path.join(ROOT, "config", ".env")
    with open(env, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("LITELLM_MASTER_KEY="):
                return line.strip().split("=", 1)[1]
    sys.exit("error: LITELLM_MASTER_KEY not found in config/.env")


KEY = None


def messages_request(model, body, stream=False):
    payload = dict(body, model=model)
    req = urllib.request.Request(
        f"{BASE}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {KEY}",
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "accept": "text/event-stream" if stream else "application/json",
        })
    return urllib.request.urlopen(req, timeout=300)


def basic_text(model):
    with messages_request(model, {
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "Reply with exactly: TRIROUTE-OK"}]}) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    assert data.get("role") == "assistant", f"bad role: {data.get('role')}"
    assert "TRIROUTE-OK" in text.upper() or text.strip(), f"empty/weird text: {text[:80]!r}"
    assert data.get("usage", {}).get("output_tokens", 0) > 0, "missing usage"


def streaming(model):
    with messages_request(
            model, {"max_tokens": 256, "stream": True,
                    "messages": [{"role": "user", "content": "Count from 1 to 5, one number per line."}]},
            stream=True) as resp:
        events, text_len = set(), 0
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            events.add(ev.get("type", ""))
            if ev.get("type") == "content_block_delta":
                text_len += len(ev.get("delta", {}).get("text", ""))
    assert "message_start" in events and "content_block_delta" in events and "message_stop" in events, \
        f"incomplete SSE event set: {sorted(events)}"
    assert text_len > 0, "stream produced no text deltas"


WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Return the weather for a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
}


def tool_call(model):
    with messages_request(model, {
            "max_tokens": 512,
            "tools": [WEATHER_TOOL],
            "tool_choice": {"type": "auto"},
            "messages": [{"role": "user",
                          "content": "What is the weather in Tokyo? You MUST call the get_weather tool."}]}) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    blocks = data.get("content", [])
    tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
    assert tool_uses, f"no tool_use block; stop_reason={data.get('stop_reason')} content={[b.get('type') for b in blocks]}"
    tu = tool_uses[0]
    assert tu.get("id"), "tool_use missing id"
    assert isinstance(tu.get("input"), dict), f"tool input not an object: {type(tu.get('input'))}"
    assert tu["input"].get("city", "").strip().lower().startswith("tokyo"), f"bad tool args: {tu['input']}"
    return tu


def multi_turn_tool(model, tool_use):
    with messages_request(model, {
            "max_tokens": 512,
            "tools": [WEATHER_TOOL],
            "messages": [
                {"role": "user",
                 "content": "What is the weather in Tokyo? You MUST call the get_weather tool."},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": tool_use["id"],
                     "name": tool_use["name"], "input": tool_use["input"]}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tool_use["id"],
                     "content": "17C, light rain, wind 12 km/h"}]},
            ]}) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    assert text.strip(), f"turn 2 produced no text: {data.get('content')}"


def provider_authenticated(name):
    creds = os.path.join(ROOT, "data", "credentials.json")
    try:
        with open(creds, encoding="utf-8") as fh:
            return CREDENTIAL_KEY[name] in json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def run_suite(name):
    if not provider_authenticated(name):
        for check in ("auth", "text", "stream", "tool", "multi-turn"):
            record(f"{name}/{check}", "MANUAL", "not authenticated — see docs/MANUAL_TESTS.md")
        return
    model = PROVIDER_MODELS[name][0]
    record(f"{name}/auth", "PASS")
    try:
        basic_text(model)
        record(f"{name}/text", "PASS", model)
    except Exception as err:
        record(f"{name}/text", "FAIL", str(err)[:200])
        return
    try:
        streaming(model)
        record(f"{name}/stream", "PASS", model)
    except Exception as err:
        record(f"{name}/stream", "FAIL", str(err)[:200])
    try:
        tu = tool_call(model)
        record(f"{name}/tool", "PASS", model)
        multi_turn_tool(model, tu)
        record(f"{name}/multi-turn", "PASS", model)
    except Exception as err:
        record(f"{name}/multi-turn", "FAIL", str(err)[:200])


def main():
    global KEY
    only = None
    if "--provider" in sys.argv:
        only = sys.argv[sys.argv.index("--provider") + 1]
    require = "--require" in sys.argv

    try:
        subprocess.run(["curl", "-sf", f"{BASE}/health/liveliness"], capture_output=True, check=True)
        record("gateway/health", "PASS")
    except Exception:
        record("gateway/health", "FAIL", f"{BASE} not reachable — is the stack running?")
        sys.exit(1)

    KEY = master_key()
    try:
        req = urllib.request.Request(f"{BASE}/v1/models",
                                     headers={"Authorization": f"Bearer {KEY}"})
        body = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        found = {m.get("id") for m in body.get("data", [])}
        missing = set(sum(PROVIDER_MODELS.values(), [])) - found
        assert not missing, f"missing aliases: {missing}"
        record("gateway/model-discovery", "PASS", f"{len(found)} aliases")
    except Exception as err:
        record("gateway/model-discovery", "FAIL", str(err)[:200])

    for name in PROVIDER_MODELS:
        if only and name != only:
            continue
        run_suite(name)

    fails = [r for r in results if r[1] == "FAIL"]
    manuals = [r for r in results if r[1] == "MANUAL"]
    print(f"\n{len(results) - len(fails) - len(manuals)} pass / {len(fails)} fail / {len(manuals)} manual")
    if fails or (require and manuals):
        sys.exit(1)


if __name__ == "__main__":
    main()
