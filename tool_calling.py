# -*- coding: utf-8 -*-
"""伪函数调用（pseudo function calling）。

Genie 上游没有 tools / tool_choice / function_call 任何通道（`parameters` 整块被丢弃），
所以函数调用只能在网关侧模拟：

    1. 把 OpenAI tools 定义渲染成协议说明，拼进首条 prompt
    2. 模型需要调用时只输出 {"tool_call": {...}} 或 {"tool_calls": [...]}
    3. 网关解析成标准 OpenAI tool_calls，finish_reason=tool_calls
    4. 调用方回传 role=tool 消息时，网关把它转成 "[工具结果] ..." 继续对话

这套做法与 NoTrackAI 的能力结论一致：原生 function calling 不存在，伪调用可稳定工作。
"""
from __future__ import annotations

import json
import re
from typing import Optional

TOOL_PROTOCOL_HEADER = "[TOOL PROTOCOL]"


def _render_tool(tool: dict) -> str:
    fn = tool.get("function") if isinstance(tool.get("type"), str) else tool
    fn = fn or {}
    name = fn.get("name", "unnamed")
    desc = (fn.get("description") or "").strip().replace("\n", " ")
    params = fn.get("parameters") or {}
    props = params.get("properties") or {}
    required = set(params.get("required") or [])

    parts = []
    for pname, pspec in props.items():
        pspec = pspec or {}
        seg = f'"{pname}"({pspec.get("type", "string")}'
        if pspec.get("description"):
            seg += f': {pspec["description"]}'
        seg += "，必填" if pname in required else "，可选"
        seg += ")"
        parts.append(seg)
    sig = ", ".join(parts) if parts else "无参数"
    return f"- {name}: {desc or '无描述'} | 参数: {sig}"


def build_tool_prompt(tools: list[dict], tool_choice=None) -> str:
    lines = [
        TOOL_PROTOCOL_HEADER,
        "你可以调用下列工具：",
    ]
    lines.extend(_render_tool(t) for t in tools if t)
    lines += [
        "",
        "规则：",
        "1. 能直接回答就直接回答，不要为了用工具而用工具。",
        "2. 需要调用工具时，只输出一个 JSON，不要输出任何其他文字、解释或代码块标记：",
        '{"tool_call": {"name": "<工具名>", "arguments": {<参数>}}}',
        "3. 需要同时调用多个工具时，输出：",
        '{"tool_calls": [{"name": "<工具名>", "arguments": {<参数>}}, ...]}',
        "4. 严禁编造工具的执行结果；结果会由调用方在后续消息里提供。",
    ]

    # tool_choice 在网关侧只能靠提示词近似
    forced = None
    if isinstance(tool_choice, dict):
        forced = (tool_choice.get("function") or {}).get("name")
    if tool_choice == "required":
        lines.append("5. 本次必须调用至少一个工具，即使用户的问题看起来可以直接回答。")
    elif forced:
        lines.append(f'5. 本次必须调用工具 "{forced}"，不要直接回答。')

    lines.append("")
    return "\n".join(lines)


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _as_call(obj: dict) -> Optional[dict]:
    if not isinstance(obj, dict):
        return None
    if "tool_call" in obj and isinstance(obj["tool_call"], dict):
        obj = obj["tool_call"]
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    args = obj.get("arguments", obj.get("args", obj.get("parameters")))
    if not name:
        return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return {"name": name, "arguments": args}


def parse_tool_calls(text: str) -> list[dict]:
    """从模型输出里抽出工具调用；抽不到返回空列表。"""
    if not text:
        return []
    candidate = _strip_fence(text)

    # 先按整体 JSON 解析
    for payload in (candidate, _JSON_OBJ_RE.search(candidate).group(0) if _JSON_OBJ_RE.search(candidate) else None):
        if not payload:
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
            out = [c for c in (_as_call(o) for o in obj["tool_calls"]) if c]
            if out:
                return out
        single = _as_call(obj)
        if single:
            return [single]

    # 兜底：正文里出现 {"tool_call": ...} 片段
    match = re.search(r'\{"tool_calls?"\s*:\s*(\[.*?\]|\{.*?\})\s*\}', candidate, re.S)
    if match:
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        if isinstance(obj.get("tool_calls"), list):
            return [c for c in (_as_call(o) for o in obj["tool_calls"]) if c]
        return [c for c in [_as_call(obj)] if c]
    return []


def to_openai_tool_calls(calls: list[dict], call_id_prefix: str = "call_genie") -> list[dict]:
    out = []
    for i, c in enumerate(calls):
        out.append({
            "id": f"{call_id_prefix}_{i}_{abs(hash(c['name'])) % 100000}",
            "type": "function",
            "function": {
                "name": c["name"],
                "arguments": json.dumps(c["arguments"], ensure_ascii=False),
            },
        })
    return out


def merge_tool_prompt(genie_msgs: list[dict], tools: list[dict], tool_choice=None) -> list[dict]:
    """把工具协议放到对话最前面（上游没有 system 通道，只能当首条 prompt）。"""
    if not tools:
        return genie_msgs
    proto = build_tool_prompt(tools, tool_choice)
    if genie_msgs and genie_msgs[0]["type"] == "prompt":
        merged = dict(genie_msgs[0])
        merged["content"] = f"{proto}\n{merged['content']}"
        return [merged] + genie_msgs[1:]
    return [{"type": "prompt", "content": proto, "fromUser": True}] + genie_msgs


def convert_messages_for_tools(messages: list[dict]) -> list[dict]:
    """OpenAI 消息（含 tool / assistant.tool_calls）-> Genie 的 {type,content,fromUser}。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):
            texts, urls = [], []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" or "text" in part:
                    texts.append(part.get("text") or "")
                elif part.get("type") in ("image_url", "image"):
                    img = part.get("image_url") or part.get("image") or {}
                    url = img.get("url") if isinstance(img, dict) else img
                    if url:
                        urls.append(url)
            content = "\n".join([u for u in urls if u] + [t for t in texts if t])

        if role == "tool":
            name = m.get("name") or m.get("tool_call_id") or "tool"
            out.append({"type": "prompt",
                        "content": f"[工具结果 {name}]\n{content or ''}",
                        "fromUser": True})
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            if calls:
                rendered = "; ".join(
                    f"{c.get('function', {}).get('name', '')}({c.get('function', {}).get('arguments', '')})"
                    for c in calls
                )
                out.append({"type": "response", "content": f"[调用工具] {rendered}",
                            "fromUser": False})
            else:
                out.append({"type": "response", "content": content or "", "fromUser": False})
        else:
            out.append({"type": "prompt", "content": content or "", "fromUser": True})
    return out