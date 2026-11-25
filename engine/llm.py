"""Shared model transport. OAuth credentials are owned by openai-oauth, not Python."""
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "oauth/node_modules/openai-oauth/dist/cli.js"
BASE_URL = "http://127.0.0.1:10531/v1"
MODEL = "gpt-6-astra"
SETUP = "powershell -ExecutionPolicy Bypass -File oauth.ps1 setup"
LOGIN = "powershell -ExecutionPolicy Bypass -File oauth.ps1 login"


class ModelError(RuntimeError):
    """Safe, actionable error suitable for the panel log."""


def setting(env, key, default=None):
    return env.get(key) or os.environ.get(key) or default


def classify_error(status, body="", provider="openai_oauth"):
    # Inspect upstream messages for classification, never echo them (or credentials).
    text = str(body).lower()
    if provider == "ollama":
        if status in (401, 403):
            return ModelError("Ollama authentication failed. Check OLLAMA_API_KEY.")
        if status == 402:
            return ModelError("Ollama account has insufficient credit for this model.")
        if status == 404:
            return ModelError("Ollama model or endpoint unavailable. Check OLLAMA_MODEL and OLLAMA_BASE_URL.")
        return ModelError(f"Ollama service failed (HTTP {status}). Check account limits and connectivity.")
    if status in (401, 403) or any(s in text for s in (
        "invalid_grant", "refresh token", "refresh_token", "no auth file",
        "not authenticated", "sign in", "authentication", "token expired",
        "missing access token", "oauth credentials", "auth.json",
    )):
        return ModelError(f"ChatGPT login required or expired. Run: {LOGIN}")
    if status == 429 or "rate_limit" in text or "usage_limit" in text:
        return ModelError("ChatGPT usage limit reached. Wait for your allowance to reset and retry.")
    if status == 404 or "model_not_found" in text:
        return ModelError(f"Model {MODEL} is unavailable to this account.")
    return ModelError(f"Model service failed (HTTP {status}). Retry later or check OAuth status.")


def completed_text(response):
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise ModelError("Model response was incomplete; no result was applied.")
    parts = []
    output = response.get("output", [])
    if not isinstance(output, list):
        raise ModelError("Model returned an invalid output structure.")
    for item in output:
        if not isinstance(item, dict):
            raise ModelError("Model returned an invalid output item.")
        if item.get("type") == "message" and item.get("phase") != "commentary":
            content = item.get("content", [])
            if not isinstance(content, list) or any(not isinstance(p, dict) for p in content):
                raise ModelError("Model returned invalid message content.")
            for part in content:
                if part.get("type") == "refusal":
                    raise ModelError("Model declined this request; no result was applied.")
                if part.get("type") == "output_text":
                    text = part.get("text", "")
                    if not isinstance(text, str):
                        raise ModelError("Model returned invalid output text.")
                    parts.append(text)
    text = "".join(parts).strip()
    if not text:
        raise ModelError("Model returned no output text.")
    return text


def read_stream(response, deadline, first_line=None):
    """Use only the completed response, never partial deltas or reasoning text."""
    data = []
    items = {}
    while True:
        if time.monotonic() > deadline:
            raise ModelError("Model request timed out; no result was applied.")
        line = first_line if first_line is not None else response.readline()
        first_line = None
        if not line:
            raise ModelError("Model stream ended before completion; no result was applied.")
        line = line.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            payload, data = "\n".join(data), []
            if payload == "[DONE]":
                raise ModelError("Model stream ended before completion; no result was applied.")
            try:
                event = json.loads(payload)
            except ValueError:
                raise ModelError("Model stream contained invalid JSON.") from None
            if not isinstance(event, dict):
                raise ModelError("Model stream contained an invalid event.")
            kind = event.get("type")
            if kind == "response.output_item.done":
                index = event.get("output_index", len(items))
                if type(index) is not int or not isinstance(event.get("item"), dict):
                    raise ModelError("Model stream contained an invalid output item.")
                items[index] = event["item"]
            if kind == "response.completed":
                result = event.get("response")
                # Responses Lite omits output from the completion envelope.
                # Completed items remain provisional until this terminal event.
                if isinstance(result, dict) and not result.get("output"):
                    result = {**result, "output": [items[k] for k in sorted(items)]}
                return completed_text(result)
            if kind in ("error", "response.failed"):
                raise classify_error(502, json.dumps(event))
            if kind == "response.incomplete":
                raise ModelError("Model response was incomplete; no result was applied.")


def request(base, route, body=None, headers=None, stream=False, read_timeout=120):
    url = urlsplit(base + route)
    cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    for attempt in range(3):
        conn = cls(url.hostname, url.port, timeout=3)
        retry = False
        try:
            conn.connect()
            conn.sock.settimeout(read_timeout)
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            conn.request("POST" if body is not None else "GET", url.path,
                         body=payload, headers={"Content-Type": "application/json", **(headers or {})})
            response = conn.getresponse()
            if response.status >= 400:
                error = classify_error(response.status, response.read(65536).decode("utf-8", "replace"),
                                       "openai_oauth" if base == BASE_URL else "ollama")
                retry = (response.status == 429 or response.status >= 500) and "login" not in str(error).lower()
                if not retry or attempt == 2:
                    raise error
            elif stream:
                # Proxy 2.0.0 can forward SSE with application/json headers.
                # Detect the body format rather than trusting Content-Type.
                first = response.readline()
                if not first.lstrip().startswith(b"{"):
                    return read_stream(response, time.monotonic() + 600, first)
                try:
                    result = json.loads(first + response.read())
                except ValueError:
                    raise ModelError("Model service returned invalid JSON.") from None
                return completed_text(result)
            else:
                try:
                    result = json.loads(response.read())
                except ValueError:
                    raise ModelError("Model service returned invalid JSON.") from None
                return completed_text(result) if stream else result
        except (TimeoutError, socket.timeout):
            raise ModelError("Model request timed out; no result was applied.") from None
        except (OSError, http.client.HTTPException):
            raise ModelError("Cannot connect to the model service. Check OAuth status and your connection.") from None
        finally:
            conn.close()
        if retry:
            time.sleep(2 ** attempt)


def proxy_running():
    """A listening but incompatible service is a conflict, not a startup opportunity."""
    conn = http.client.HTTPConnection("127.0.0.1", 10531, timeout=3)
    try:
        conn.connect()
    except ConnectionRefusedError:
        conn.close()
        return False
    except OSError:
        conn.close()
        raise ModelError("Cannot check OAuth port 10531; check local firewall permissions.") from None
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        data = json.loads(response.read())
        if response.status != 200 or data != {"ok": True, "replay_state": "stateless"}:
            raise ValueError()
        return True
    except (OSError, ValueError, http.client.HTTPException):
        raise ModelError("Port 10531 is occupied by an incompatible service. Free the port and retry.") from None
    finally:
        conn.close()


def node_command():
    node = shutil.which("node")
    if not node:
        node = str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs/node.exe")
    if not Path(node).is_file() or not CLI.is_file():
        raise ModelError(f"OAuth setup required (Node 20+ and local dependency). Run: {SETUP}")
    return [node, str(CLI)]


def runtime_env():
    # Pinned CLI 2.0.0 supports a separate runtime directory and authenticated stop.
    return {**os.environ, "OPENAI_OAUTH_INTERNAL_RUNTIME_DIR": str(ROOT / "cache/oauth")}


def ensure_proxy():
    if proxy_running():
        return
    command = node_command()
    print("[ai] starting local ChatGPT OAuth proxy", flush=True)
    # The CLI's atomic runtime lock coordinates simultaneous engine processes.
    deadline = time.monotonic() + 30
    try:
        result = subprocess.run(
            command + ["--detach", "--host", "127.0.0.1", "--port", "10531"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, env=runtime_env(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        raise ModelError("OAuth startup timed out after 30 seconds. Run oauth.ps1 status, then retry.") from None
    except OSError:
        raise ModelError(f"Could not start Node. Run: {SETUP}") from None
    if proxy_running():
        return
    if "already starting" in result.stderr.lower():
        while time.monotonic() < deadline:
            if proxy_running():
                return
            time.sleep(0.2)
        raise ModelError("OAuth startup timed out while another process was starting it.")
    if result.returncode:
        raise classify_error(502, result.stderr + result.stdout)
    raise ModelError("OAuth proxy did not become ready. Run oauth.ps1 status.")


def validate(data, feature):
    """Check the fields downstream code consumes before any cuts are produced."""
    def fail():
        raise ModelError(f"Model returned invalid {feature} JSON; required fields or values are missing.")

    def rows(value):
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            fail()
        return value

    def strings(row, keys):
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in keys):
            fail()

    def positive(value):
        if type(value) is not int or value < 1:
            fail()

    if not isinstance(data, dict):
        fail()
    if feature == "moments":
        for row in rows(data.get("moments")):
            strings(row, ("start_tc", "end_tc", "start_quote", "end_quote", "summary", "why", "visual"))
            if any(not re.fullmatch(r"\d+:\d{2}(?::\d{2})?", row[k]) for k in ("start_tc", "end_tc")):
                fail()
            for key in ("hook", "self_contained", "emotion", "clarity"):
                value = row.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10:
                    fail()
    elif feature == "picks":
        seen = set()
        for row in rows(data.get("picks")):
            positive(row.get("reel"))
            strings(row, ("song", "reason"))
            if row["reel"] in seen:
                fail()
            seen.add(row["reel"])
    elif feature == "trailer":
        candidates = rows(data.get("candidates"))
        orders = rows(data.get("orders"))
        if not candidates or not orders:
            fail()
        for row in candidates:
            positive(row.get("reel"))
            strings(row, ("start_quote", "end_quote", "summary", "why", "visual"))
        for row in orders:
            strings(row, ("name", "why"))
            clips = rows(row.get("clips"))
            if not clips:
                fail()
            for clip in clips:
                positive(clip.get("candidate"))
                strings(clip, ("summary", "why"))
                if clip.get("role") not in ("hook", "constant", "climax") or clip["candidate"] > len(candidates):
                    fail()
    else:
        raise ValueError(f"Unknown feature: {feature}")
    return data


class ModelClient:
    def __init__(self, env):
        self.env = env
        self.provider = setting(env, "AI_PROVIDER", "openai_oauth")
        if self.provider not in ("openai_oauth", "ollama"):
            raise ModelError("AI_PROVIDER must be openai_oauth or ollama.")
        self.ready = False

    def preflight(self):
        if self.ready:
            return
        if self.provider == "openai_oauth":
            ensure_proxy()
            models = request(BASE_URL, "/models", read_timeout=20)
            if not isinstance(models, dict) or not isinstance(models.get("data"), list):
                raise ModelError("OAuth model discovery returned an invalid response.")
            if not any(isinstance(m, dict) and m.get("id") == MODEL for m in models["data"]):
                raise ModelError(f"Model {MODEL} is not available to this ChatGPT account. No fallback was used.")
        elif not setting(self.env, "OLLAMA_API_KEY"):
            raise ModelError("Ollama selected but OLLAMA_API_KEY is missing.")
        self.ready = True

    def generate(self, prompt, feature, *, temperature=0.4, max_tokens=8000):
        self.preflight()
        if self.provider == "openai_oauth":
            raw = request(BASE_URL, "/responses", {
                "model": MODEL,
                "instructions": "Follow the user's selection task. Return only the requested JSON. Do not ask questions or use tools.",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                "reasoning": {"effort": "medium"}, "store": False, "stream": True,
            }, stream=True)
        else:
            response = request(setting(self.env, "OLLAMA_BASE_URL", "https://ollama.com/v1").rstrip("/"),
                               "/chat/completions", {
                                   "model": setting(self.env, "OLLAMA_MODEL", "gemma4:31b-cloud"),
                                   "messages": [{"role": "user", "content": prompt}],
                                   "temperature": temperature, "max_tokens": max_tokens,
                               }, headers={"Authorization": "Bearer " + setting(self.env, "OLLAMA_API_KEY")})
            try:
                raw = response["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise ModelError("Ollama returned no usable message.") from None
        if not isinstance(raw, str) or not raw.strip():
            raise ModelError("Model returned no output text.")
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        try:
            data = json.loads(raw)
        except ValueError:
            raise ModelError("Model returned malformed JSON; no result was applied.") from None
        return validate(data, feature)
