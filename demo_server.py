# -*- coding: utf-8 -*-
"""Genie 7.9.2 -> OpenAI 兼容 2API 网关 (demo)

上游：https://genie-production-yfvxbm4e6q-uc.a.run.app
鉴权：Firebase 匿名账号 idToken（池化轮转，每 uid 每天 8 次）

端点
  POST /v1/chat/completions   OpenAI 兼容（stream=true/false）
  GET  /v1/models             可用模型（免费档只有 gpt-4o-mini）
  GET  /v1/genie/assistants   官方 34 个系统助手（含 prompt，可直接当人设）
  GET  /health /stats         健康与号池/统计
  GET  /                      demo 页

模式
  real（默认）：打真实上游
  mock：本地合成答复，用于不消耗账号额度的吞吐压测与网关逻辑验证
        GENIE_MODE=mock python demo_server.py

端口 8952
"""
import json
import os
import queue
import statistics
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from genie_client import (
    AccountPool, ChatResult, GenieClient, GenieError, FREE_MODEL, MODEL_ALIASES,
    openai_messages_to_genie,
)
from tool_calling import (
    convert_messages_for_tools, merge_tool_prompt, parse_tool_calls, to_openai_tool_calls,
)

PORT = int(os.environ.get("GENIE_PORT", 8952))
MODE = os.environ.get("GENIE_MODE", "real").lower()
HERE = os.path.dirname(os.path.abspath(__file__))

SERVED_MODELS = ["gpt-4o-mini", "genie-4o-mini", "gpt-4o", "gpt-4", "gpt-3.5-turbo", "gpt-4-vision"]
IMAGE_MODEL = "genie-image"      # 走 ?type=image_generation 的 agent 通道

app = Flask(__name__)
client = GenieClient(AccountPool())
stats_lock = threading.Lock()
STATS = {"requests": 0, "upstream_ok": 0, "upstream_err": 0, "soft_limited": 0,
         "pro_blocked": 0, "moderated": 0, "tool_calls": 0, "latency_ms": [], "errors": {}}
_assisants_cache = {"data": None, "at": 0.0}


def record(ok: bool, latency_ms: float, err_code: str = "", soft=False, moderated=False, pro=False):
    with stats_lock:
        STATS["requests"] += 1
        if ok:
            STATS["upstream_ok"] += 1
            STATS["latency_ms"].append(latency_ms)
            if len(STATS["latency_ms"]) > 2000:
                STATS["latency_ms"] = STATS["latency_ms"][-2000:]
        else:
            STATS["upstream_err"] += 1
            STATS["errors"][err_code or "UNKNOWN"] = STATS["errors"].get(err_code or "UNKNOWN", 0) + 1
        if soft:
            STATS["soft_limited"] += 1
        if moderated:
            STATS["moderated"] += 1
        if pro:
            STATS["pro_blocked"] += 1


def stats_payload():
    with stats_lock:
        lat = list(STATS["latency_ms"])
        base = {k: v for k, v in STATS.items() if k != "latency_ms"}
    if lat:
        base["latency_p50_ms"] = round(statistics.median(lat), 1)
        base["latency_p95_ms"] = round(sorted(lat)[int(len(lat) * 0.95) - 1], 1)
        base["latency_max_ms"] = round(max(lat), 1)
    base["mode"] = MODE
    base["pool"] = client.pool.stats()
    return base


# --------------------------------------------------------------------------- mock

def mock_stream(messages, model):
    last = messages[-1]["content"] if messages else ""
    words = ["这是", " mock", " 模式的", "合成答复", "：", last[:40], "。", " 上游未被调用。"]
    for w in words:
        yield w
        time.sleep(0.02)


# --------------------------------------------------------------------------- 系统提示词

def apply_system_prompt(messages: list[dict], system_prompt: str) -> list[dict]:
    """上游 parameters.systemPrompt 被忽略，只能把它拼进首条用户消息。"""
    if not system_prompt:
        return messages
    prefixed = [{"type": "prompt", "content": f"{system_prompt}\n\n{messages[0]['content']}", "fromUser": True}]
    return prefixed + messages[1:] if messages and messages[0]["type"] == "prompt" else \
        [{"type": "prompt", "content": system_prompt, "fromUser": True}] + messages


def assistant_prompts():
    """uniqueKey -> prompt / systemPrompts 映射，来自官方助手目录。"""
    now = time.time()
    if _assisants_cache["data"] is None or now - _assisants_cache["at"] > 3600:
        try:
            cats = client.system_assistants()
            flat = {}
            for c in cats:
                for a in c.get("assistants", []):
                    flat[a["uniqueKey"]] = a
            _assisants_cache["data"] = flat
            _assisants_cache["at"] = now
        except GenieError:
            return _assisants_cache["data"] or {}
    return _assisants_cache["data"] or {}


# --------------------------------------------------------------------------- 路由

@app.get("/")
def index():
    return send_from_directory(HERE, "demo.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "mode": MODE, "pool": client.pool.stats()})


@app.get("/stats")
def stats():
    return jsonify(stats_payload())


@app.get("/v1/models")
def models():
    data = []
    for m in SERVED_MODELS:
        free = MODEL_ALIASES.get(m) == FREE_MODEL
        data.append({
            "id": m,
            "object": "model",
            "created": 1735000000,
            "owned_by": "genie",
            "upstream": MODEL_ALIASES.get(m, m),
            "free": free,
            "capabilities": {
                "text": True,
                "vision": free,              # 只有 mini 免费档实测能吃图片直链
                "function_call": "pseudo",   # 网关侧伪函数调用，上游无原生 tools 通道
                "native_function_call": False,
                "agent_tools": False,        # text2image / websearch 需 Pro
                "streaming": "pseudo",       # 生成完才一次性推
            },
            "limits": {"context_chars": 236000, "max_output_tokens_est": 1000},
        })
    return jsonify({"object": "list", "data": data})


@app.get("/v1/genie/assistants")
def assistants():
    flat = assistant_prompts()
    out = []
    for key, a in flat.items():
        out.append({
            "uniqueKey": key,
            "title": a.get("title", {}),
            "description": a.get("description", {}),
            "isPro": a.get("isPro", False),
            "systemPrompts": a.get("systemPrompts"),
            "prompt": a.get("prompt"),
            "questions": a.get("questions", []),
        })
    return jsonify({"object": "list", "data": out})


@app.post("/v1/images/generations")
def images_generations():
    """OpenAI 图像协议 -> 上游 agent 的 text2image 工具。

    链路本身是通的（上游回 agent-status tool_calling service=text2image），
    但工具执行前有 Pro 校验：免费匿名账号拿到需要订阅的文案。
    """
    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt") or ""
    if not prompt:
        return jsonify({"error": {"message": "prompt is required", "type": "invalid_request_error"}}), 400

    created = int(time.time())
    t0 = time.time()
    if MODE == "mock":
        record(True, (time.time() - t0) * 1000)
        return jsonify({
            "created": created,
            "data": [{"url": "https://example.invalid/mock-genie-image.png", "revised_prompt": prompt}],
            "genie": {"mode": "mock"},
        })

    try:
        result = client.generate_image(prompt)
    except GenieError as e:
        pro = e.code == "NEEDS_PRO"
        record(False, (time.time() - t0) * 1000, e.code, pro=pro)
        return jsonify({
            "error": {
                "message": e.message,
                "type": "pro_required" if pro else "upstream_error",
                "code": e.code,
            },
            "genie": {"service": "text2image", "steps": (e.payload or {}).get("steps", [])},
        }), (402 if pro else (e.status or 502))

    record(True, result.latency * 1000)
    data = [{"url": m.url, "revised_prompt": prompt} for m in result.media]
    if not data:
        data = [{"url": "", "revised_prompt": prompt, "note": result.text}]
    return jsonify({
        "created": created,
        "data": data,
        "genie": {"service": result.service, "steps": result.agent_steps, "events": result.events},
    })


@app.post("/v1/chat/completions")
def chat_completions():
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    if not messages:
        return jsonify({"error": {"message": "messages is required", "type": "invalid_request_error"}}), 400

    model = body.get("model") or "gpt-4o-mini"
    if model not in MODEL_ALIASES:
        return jsonify({"error": {"message": f"model '{model}' is not served; see /v1/models",
                                  "type": "invalid_request_error", "code": "model_not_found"}}), 400

    assistant_key = body.get("genie_assistant") or (request.headers.get("X-Genie-Assistant") or "")
    system_prompt = body.get("system_prompt") or ""
    if assistant_key:
        a = assistant_prompts().get(assistant_key)
        if a:
            system_prompt = a.get("prompt") or (a.get("systemPrompts") or [{}])[0].get("prompt", "") or system_prompt

    tools = body.get("tools") or []
    tool_choice = body.get("tool_choice")
    genie_msgs = convert_messages_for_tools(messages) if tools else openai_messages_to_genie(messages)
    genie_msgs = apply_system_prompt(genie_msgs, system_prompt)
    genie_msgs = merge_tool_prompt(genie_msgs, tools, tool_choice)

    stream = bool(body.get("stream"))
    created = int(time.time())
    cid = f"chatcmpl-genie-{created}"

    if stream:
        def generate():
            t0 = time.time()
            if MODE == "mock":
                try:
                    for piece in mock_stream(genie_msgs, model):
                        yield _chunk(cid, created, model, piece, None)
                    yield _chunk(cid, created, model, "", "stop")
                    record(True, (time.time() - t0) * 1000)
                except Exception as e:  # noqa: BLE001
                    record(False, (time.time() - t0) * 1000, "INTERNAL")
                    yield _chunk(cid, created, model, "", "error", error=GenieError("INTERNAL", str(e)))
                return

            if tools:
                # 伪函数调用必须拿到完整输出才能解析，先收齐再按 SSE 下发
                try:
                    result = client.chat(model, genie_msgs)
                    calls = parse_tool_calls(result.text)
                    if calls:
                        oa = to_openai_tool_calls(calls)
                        with stats_lock:
                            STATS["tool_calls"] += len(oa)
                        yield _tool_chunk(cid, created, model, oa)
                        yield _finish_chunk(cid, created, model, "tool_calls")
                    else:
                        for piece in _split_text(result.text):
                            yield _chunk(cid, created, model, piece, None)
                        yield _chunk(cid, created, model, "", "stop")
                    record(True, result.latency * 1000, soft=result.soft_limited,
                           moderated=result.moderated)
                except GenieError as e:
                    record(False, (time.time() - t0) * 1000, e.code, pro=e.code == "NEEDS_PRO")
                    yield _chunk(cid, created, model, "", "error", error=e)
                return

            q: queue.Queue = queue.Queue()
            box = {}

            def worker():
                try:
                    box["result"] = client.chat(model, genie_msgs, on_delta=lambda p: q.put(("delta", p)))
                except GenieError as e:
                    box["error"] = e
                except Exception as e:  # noqa: BLE001
                    box["error"] = GenieError("INTERNAL", str(e))
                finally:
                    q.put(("end", None))

            threading.Thread(target=worker, daemon=True).start()
            while True:
                kind, value = q.get()
                if kind == "delta":
                    yield _chunk(cid, created, model, value, None)
                elif kind == "end":
                    break
            err = box.get("error")
            result = box.get("result")
            if err is not None:
                record(False, (time.time() - t0) * 1000, err.code, pro=err.code == "NEEDS_PRO")
                yield _chunk(cid, created, model, "", "error", error=err)
            else:
                yield _chunk(cid, created, model, "", "stop")
                record(True, result.latency * 1000, soft=result.soft_limited, moderated=result.moderated)

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    t0 = time.time()
    try:
        if MODE == "mock":
            text = "".join(mock_stream(genie_msgs, model))
            record(True, (time.time() - t0) * 1000)
            return jsonify(_completion(cid, created, model, text))
        result = client.chat(model, genie_msgs)
        calls = parse_tool_calls(result.text) if tools else []
        if calls:
            with stats_lock:
                STATS["tool_calls"] += len(calls)
            record(True, result.latency * 1000)
            return jsonify(_tool_completion(cid, created, model, calls))
        record(True, result.latency * 1000, soft=result.soft_limited, moderated=result.moderated)
        return jsonify(_completion(cid, created, model, result.text))
    except GenieError as e:
        record(False, (time.time() - t0) * 1000, e.code, pro=e.code == "NEEDS_PRO")
        payload = {"error": {"message": e.message, "type": "upstream_error", "code": e.code}}
        return jsonify(payload), (e.status or 502)


def _chunk(cid, created, model, delta, finish, error=None):
    if error is not None:
        payload = {"error": {"message": error.message, "code": error.code, "type": "upstream_error"}}
    else:
        payload = {
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": ({"content": delta} if delta else {}),
                         "finish_reason": finish}],
        }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _tool_completion(cid, created, model, calls):
    return {
        "id": cid, "object": "chat.completion", "created": created, "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": None,
                        "tool_calls": to_openai_tool_calls(calls)},
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "genie": {"mode": "pseudo_tool_call"},
    }


def _tool_chunk(cid, created, model, openai_calls):
    payload = {
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "tool_calls": [
                {"index": i, **c} for i, c in enumerate(openai_calls)
            ]},
            "finish_reason": None,
        }],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _finish_chunk(cid, created, model, reason):
    payload = {
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _split_text(text, size=24):
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def _completion(cid, created, model, text):
    return {
        "id": cid, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "genie": {"upstream_model": MODEL_ALIASES.get(model, model)},
    }


if __name__ == "__main__":
    print(f"Genie 2API demo on http://127.0.0.1:{PORT}  mode={MODE}")
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)