#!/usr/bin/env python3
"""Genie v7.9.2 (co.appnation.geniechat) -> OpenAI 兼容网关 + demo 页。

唯一上游能力: gpt-4o-mini 文本补全(SSE 流式), 每账号每天 7 次。
上游忽略 systemPrompt / maxTokens / temperature, 网关层把 system 前置拼进首条用户消息。

用法:
  python demo_server.py --port 8952
  curl localhost:8952/v1/chat/completions -H 'Content-Type: application/json' \
       -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"stream":true}'
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

from flask import Flask, Response, jsonify, request, send_file

from genie_client import AccountPool, GenieClient, UpstreamProtocolError, UpstreamQuotaExhausted

app = Flask(__name__)
pool = AccountPool()
client = GenieClient(pool)

DEFAULT_MODEL = "gpt-4o-mini"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def openai_error(message: str, status: int, code: str) -> tuple[Response, int]:
    return (
        jsonify({"error": {"message": message, "type": code, "code": code}}),
        status,
    )


@app.get("/v1/models")
def list_models():
    return jsonify(
        {
            "object": "list",
            "data": [
                {
                    "id": DEFAULT_MODEL,
                    "object": "model",
                    "created": 0,
                    "owned_by": "genie-appnation",
                }
            ],
        }
    )


@app.get("/api/stats")
def stats():
    return jsonify(pool.stats())


@app.post("/v1/chat/completions")
def chat_completions():
    payload = request.get_json(silent=True) or {}
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return openai_error("messages must be a non-empty array", 400, "invalid_request_error")

    model = payload.get("model") or DEFAULT_MODEL
    stream = bool(payload.get("stream"))
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if not stream:
        try:
            answer = client.complete(messages)
        except UpstreamQuotaExhausted as error:
            return openai_error(str(error), 429, "rate_limit_exceeded")
        except (UpstreamProtocolError, ValueError) as error:
            return openai_error(str(error), 502, "upstream_error")
        prompt_tokens = sum(estimate_tokens(m.get("content") or "") for m in messages)
        completion_tokens = estimate_tokens(answer)
        return jsonify(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        )

    def sse() -> Response:
        def chunk(delta: dict, finish_reason: str | None = None) -> str:
            event = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        try:
            yield chunk({"role": "assistant", "content": ""})
            for piece in client.stream(messages):
                yield chunk({"content": piece})
            yield chunk({}, "stop")
        except (UpstreamQuotaExhausted, UpstreamProtocolError, ValueError) as error:
            yield f"data: {json.dumps({'error': {'message': str(error), 'type': 'upstream_error'}}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(sse(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return send_file("demo.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8952)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"Genie 2API demo: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
