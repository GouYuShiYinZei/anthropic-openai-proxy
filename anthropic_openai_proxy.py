#!/usr/bin/env python3
"""Anthropic Messages API <-> OpenAI Chat Completions API proxy.

Protocol translator that lets you use any OpenAI-compatible API
to power Claude Code or other Anthropic API clients.

Usage:
    python anthropic_openai_proxy.py
        → prompts for upstream URL interactively

    python anthropic_openai_proxy.py --upstream https://your-api.com/v1/chat/completions
        → uses the given upstream URL

    python anthropic_openai_proxy.py --host 0.0.0.0 --port 8080 --model gpt-5

Setup the client (Claude Code, etc.) with:
    ANTHROPIC_BASE_URL = http://127.0.0.1:8899
    ANTHROPIC_AUTH_TOKEN = <your-upstream-api-key>

Dependencies: Python 3.7+ standard library only. No pip installs needed.
"""

import argparse
import http.server
import json
import os
import re
import sys
import uuid
import urllib.parse
import urllib.request


# ── Anthropic → OpenAI (request) ──────────────────────────────────────────

def _msg_content_to_oai(content, role):
    """Convert Anthropic content (str or list of blocks) to OpenAI message(s)."""
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    text_parts = []
    image_parts = []
    tool_results = []
    tool_uses = []

    for block in (content or []):
        if not isinstance(block, dict):
            continue
        bt = block.get("type", "")
        if bt == "text":
            text_parts.append(block.get("text", ""))
        elif bt == "image":
            src = block.get("source", {})
            image_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": "data:{};base64,{}".format(
                        src.get("media_type", "image/png"),
                        src.get("data", ""))
                }
            })
        elif bt == "tool_result":
            tool_results.append(block)
        elif bt == "tool_use":
            tool_uses.append(block)
        elif bt == "thinking":
            text_parts.append(block.get("thinking", ""))
        elif bt == "redacted_thinking":
            text_parts.append("[redacted thinking]")

    results = []

    # tool_result blocks → OpenAI "tool" role messages
    for tr in tool_results:
        tc = tr.get("content", "")
        if isinstance(tc, list):
            tc = "\n".join(b.get("text", "") for b in tc if isinstance(b, dict) and b.get("type") == "text")
        results.append({
            "role": "tool",
            "tool_call_id": tr.get("tool_use_id", ""),
            "content": tc if tc else "",
        })

    # assistant message with tool_use → OpenAI assistant with tool_calls
    if tool_uses and role == "assistant":
        oai_tool_calls = []
        for tu in tool_uses:
            oai_tool_calls.append({
                "id": tu.get("id", ""),
                "type": "function",
                "function": {
                    "name": tu.get("name", ""),
                    "arguments": json.dumps(tu.get("input", {}), ensure_ascii=False),
                }
            })
        results.append({
            "role": "assistant",
            "content": "\n".join(text_parts) if text_parts else "",
            "tool_calls": oai_tool_calls,
        })
        return results

    # Regular message
    text_content = "\n".join(text_parts) if text_parts else ""

    if image_parts:
        parts = []
        if text_content:
            parts.append({"type": "text", "text": text_content})
        parts.extend(image_parts)
        results.append({"role": role, "content": parts})
    else:
        results.append({"role": role, "content": text_content if text_content else ""})

    return results


def anthropic_to_openai(body, model_override=None):
    msgs = []
    for m in body.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content", "")
        msgs.extend(_msg_content_to_oai(content, role))

    system = body.get("system", "")
    if isinstance(system, list):
        text_parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        system = "\n".join(text_parts)

    if system:
        msgs.insert(0, {"role": "system", "content": system})

    oai = {
        "model": model_override or body.get("model", "gpt-4"),
        "messages": msgs,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 1.0),
        "stream": body.get("stream", False),
    }

    # Forward tools (Anthropic → OpenAI function format)
    tools = body.get("tools")
    if tools:
        oai["tools"] = []
        for t in tools:
            oai["tools"].append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                }
            })

    if body.get("top_p") is not None:
        oai["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        oai["stop"] = body["stop_sequences"]

    # Forward tool_choice (Anthropic → OpenAI)
    tc = body.get("tool_choice")
    if tc:
        tc_type = tc.get("type", "auto") if isinstance(tc, dict) else str(tc)
        if tc_type == "auto":
            oai["tool_choice"] = "auto"
        elif tc_type == "any":
            oai["tool_choice"] = "required"
        elif tc_type == "tool":
            oai["tool_choice"] = {
                "type": "function",
                "function": {"name": tc.get("name", "")}
            }

    return oai


# ── OpenAI → Anthropic (response) ─────────────────────────────────────────

def openai_to_anthropic(oai, model):
    choice = oai.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content", "")
    finish = choice.get("finish_reason", "stop")
    tool_calls = msg.get("tool_calls", [])

    content_blocks = []

    if content:
        content_blocks.append({"type": "text", "text": content})

    for tc in tool_calls:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "input": args,
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    stop_reason = stop_map.get(finish, "end_turn")

    return {
        "id": "msg_{}".format(oai.get("id", "proxy")),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": oai.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": oai.get("usage", {}).get("completion_tokens", 0),
        },
    }


def estimate_tokens(text):
    if not text:
        return 0
    cjk = sum(1 for c in text if '一' <= c <= '鿿')
    return max(1, (len(text) - cjk) // 4 + cjk // 2)


# ── Streaming SSE conversion ──────────────────────────────────────────────

def stream_convert(oai_body, upstream_url, auth, model, handler):
    """Send streaming request upstream, convert OpenAI SSE → Anthropic SSE."""
    req_data = json.dumps(oai_body).encode("utf-8")
    req = urllib.request.Request(upstream_url, data=req_data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer {}".format(auth))
    req.add_header("Accept", "text/event-stream")

    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print("[!!] HTTP {}: {}".format(e.code, err_body[:300]))
        handler._send_error(e.code, "Upstream error: {}".format(err_body[:200]))
        return
    except Exception as e:
        print("[!!] {}".format(e))
        handler._send_error(502, str(e))
        return

    msg_id = "msg_{}".format(uuid.uuid4().hex[:24])
    msg_started = False
    block_index = 0
    cur_type = None
    tool_id = None
    tool_name = ""
    tool_args = ""
    full_text = ""
    stream_ended = False

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.end_headers()

    def _sse(event, data):
        handler.wfile.write("event: {}\n".format(event).encode("utf-8"))
        handler.wfile.write("data: {}\n\n".format(json.dumps(data, ensure_ascii=False)).encode("utf-8"))
        handler.wfile.flush()

    def _start_block(bt, **extra):
        nonlocal cur_type, block_index
        _sse("content_block_start", {
            "type": "content_block_start",
            "index": block_index,
            "content_block": {"type": bt, **extra},
        })
        cur_type = bt

    def _stop_block():
        nonlocal cur_type, block_index
        _sse("content_block_stop", {
            "type": "content_block_stop",
            "index": block_index,
        })
        block_index += 1
        cur_type = None

    # Estimate input tokens for streaming message_start
    in_tokens = 0
    for m in oai_body.get("messages", []):
        content = m.get("content", "")
        if isinstance(content, str):
            in_tokens += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                for key in ("text",):
                    txt = block.get(key, "") if isinstance(block, dict) else ""
                    if txt:
                        in_tokens += estimate_tokens(txt)
    in_tokens = max(1, in_tokens)

    def _end_stream(finish, out_tokens):
        nonlocal stream_ended
        if stream_ended:
            return
        stream_ended = True
        stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
        _sse("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_map.get(finish, "end_turn"),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": out_tokens},
        })
        _sse("message_stop", {"type": "message_stop"})

    try:
        for line in resp:
            line = line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choice = chunk.get("choices", [{}])[0]
            delta = choice.get("delta", {})
            finish = choice.get("finish_reason")

            # message_start (first chunk only)
            if not msg_started:
                _sse("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": in_tokens, "output_tokens": 0},
                    }
                })
                msg_started = True

            # text content
            content_text = delta.get("content", "")
            if content_text:
                if cur_type != "text":
                    if cur_type is not None:
                        _stop_block()
                    _start_block("text", text="")
                full_text += content_text
                _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": content_text},
                })

            # tool_calls deltas
            tool_call_deltas = delta.get("tool_calls", [])
            for tcd in tool_call_deltas:
                if tcd.get("id"):
                    if cur_type is not None:
                        _stop_block()
                    tool_id = tcd["id"]
                    tool_name = tcd.get("function", {}).get("name", "")
                    tool_args = ""
                    _start_block("tool_use", id=tool_id, name=tool_name, input={})

                fn = tcd.get("function", {})
                args_delta = fn.get("arguments", "")
                if args_delta and cur_type == "tool_use":
                    tool_args += args_delta
                    _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "input_json_delta", "partial_json": args_delta},
                    })

            # finish
            if finish:
                if cur_type is not None:
                    _stop_block()
                output_tokens = estimate_tokens(full_text)
                if tool_args:
                    output_tokens += estimate_tokens(tool_args)
                _end_stream(finish, max(1, output_tokens))

        # Fallback: stream ended without explicit finish
        if not stream_ended:
            if cur_type is not None:
                _stop_block()
            if msg_started:
                _end_stream("stop", max(1, estimate_tokens(full_text)))

        print("[<- stream] {} chars, {} blocks".format(len(full_text), block_index))

    except Exception as e:
        print("[!! stream] {}".format(e))


# ── HTTP Handler ──────────────────────────────────────────────────────────

class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code, msg):
        self._send_json(code, {"type": "error", "error": {"type": "proxy_error", "message": msg}})

    def do_POST(self):
        print("[REQ] {} {}".format(self.command, self.path))
        path = urllib.parse.urlparse(self.path).path.rstrip("/")

        # POST /v1/messages/count_tokens
        if path in ("/v1/messages/count_tokens", "/messages/count_tokens"):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON")
                return

            tokens = 0
            for m in body.get("messages", []):
                content = m.get("content", "")
                if isinstance(content, str):
                    tokens += estimate_tokens(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            txt = block.get("text", "")
                            if txt:
                                tokens += estimate_tokens(txt)

            system = body.get("system", "")
            if isinstance(system, str):
                tokens += estimate_tokens(system)
            elif isinstance(system, list):
                for block in system:
                    if isinstance(block, dict):
                        txt = block.get("text", "")
                        if txt:
                            tokens += estimate_tokens(txt)

            tokens = max(1, tokens)
            print("[T] count_tokens: ~{} tokens".format(tokens))
            self._send_json(200, {"input_tokens": tokens})
            return

        # POST /v1/messages or /messages
        if path not in ("/v1/messages", "/messages"):
            self._send_error(404, "Not found: {}".format(self.path))
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            anthropic = json.loads(raw)
        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
            return

        auth = self.headers.get("x-api-key", "")
        if not auth:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                auth = auth_header[7:]
        if not auth:
            self._send_error(401, "Missing x-api-key or Authorization header")
            return

        streaming = anthropic.get("stream", False)
        oai = anthropic_to_openai(anthropic, model_override=self.server.upstream_model)
        oai["stream"] = streaming
        model = anthropic.get("model", "gpt-4")

        has_tools = bool(oai.get("tools"))
        print("[->] {} -> {}  ({} messages, stream={}, tools={})".format(
            model, self.server.upstream_model or model, len(oai["messages"]), streaming, has_tools))

        if streaming:
            stream_convert(oai, self.server.upstream_url, auth, model, self)
        else:
            req_data = json.dumps(oai).encode("utf-8")
            req = urllib.request.Request(self.server.upstream_url, data=req_data)
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer {}".format(auth))

            try:
                resp = urllib.request.urlopen(req, timeout=300)
                resp_body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                print("[!!] HTTP {}: {}".format(e.code, err_body[:300]))
                self._send_error(e.code, "Upstream error: {}".format(err_body[:200]))
                return
            except Exception as e:
                print("[!!] {}".format(e))
                self._send_error(502, str(e))
                return

            result = openai_to_anthropic(resp_body, model)
            n_blocks = len(result["content"])
            print("[<-] {} blocks, stop={}".format(n_blocks, result["stop_reason"]))
            self._send_json(200, result)

    def do_GET(self):
        print("[REQ] {} {}".format(self.command, self.path))
        path = urllib.parse.urlparse(self.path).path.rstrip("/")

        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if path in ("/v1/models", "/models"):
            self._send_json(200, {
                "data": [
                    {"id": self.server.upstream_model or "gpt-4",
                     "object": "model", "type": "model"}
                ]
            })
            return

        m = re.match(r"^(/v1)?/models/(.+)$", path)
        if m:
            model_id = m.group(2)
            print("[G] model detail: {}".format(model_id))
            self._send_json(200, {
                "id": model_id,
                "object": "model",
                "type": "model",
                "display_name": model_id,
                "created_at": "2025-01-01T00:00:00Z",
            })
            return

        self._send_error(404, "Not found: {}".format(self.path))

    def log_message(self, fmt, *args):
        pass


# ── Interactive upstream prompt ───────────────────────────────────────────

def prompt_upstream():
    print("=" * 55)
    print("  Anthropic -> OpenAI Protocol Proxy")
    print("=" * 55)
    print()
    print("  Enter the OpenAI-compatible API endpoint you want to proxy to.")
    print("  Examples:")
    print("    https://api.openai.com/v1/chat/completions")
    print("    https://your-custom-api.com/v1/chat/completions")
    print()

    while True:
        url = input("  Upstream URL: ").strip()
        if url:
            return url
        print("  [!] URL cannot be empty. Please try again.")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Anthropic -> OpenAI Protocol Proxy",
        epilog="Run without --upstream to enter the URL interactively."
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Listen address (default 127.0.0.1, use 0.0.0.0 for server)")
    parser.add_argument("--port", type=int, default=8899,
                        help="Listen port (default 8899)")
    parser.add_argument("--upstream", default=None,
                        help="Upstream OpenAI API URL (e.g. https://api.openai.com/v1/chat/completions)")
    parser.add_argument("--model", default=None,
                        help="Force upstream model name (default: use whatever the client sends)")
    args = parser.parse_args()

    host = os.environ.get("PROXY_HOST", args.host)
    port = int(os.environ.get("PROXY_PORT", args.port))
    upstream = os.environ.get("PROXY_UPSTREAM", args.upstream)
    upstream_model = os.environ.get("PROXY_MODEL", args.model) or None

    # Interactive prompt if no upstream URL provided
    if not upstream:
        upstream = prompt_upstream()
        print()

    server = http.server.HTTPServer((host, port), ProxyHandler)
    server.upstream_url = upstream
    server.upstream_model = upstream_model

    print("=" * 55)
    print("  Anthropic -> OpenAI Protocol Proxy")
    print("  Listen: http://{}:{}/v1/messages".format(host, port))
    print("  Upstream: {}".format(upstream))
    if upstream_model:
        print("  Model override: {}".format(upstream_model))
    print("  Streaming: SSE + tools + images")
    print("=" * 55)
    print("  Client config (Claude Code ~/.claude/settings.json):")
    print("    ANTHROPIC_BASE_URL = http://{}:{}".format(
        host if host != "0.0.0.0" else "<server-ip>", port))
    print("    ANTHROPIC_AUTH_TOKEN = <your-upstream-api-key>")
    print("=" * 55)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
