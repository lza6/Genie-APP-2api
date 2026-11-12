# -*- coding: utf-8 -*-
"""伪函数调用端到端验证：工具定义 -> tool_calls -> 回灌结果 -> 最终答复。"""
import json
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8952"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市当前的天气",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，例如 北京"},
                "unit": {"type": "string", "description": "摄氏度或华氏度"},
            },
            "required": ["city"],
        },
    },
}]

TOOL_RESULT = "北京：晴，气温 21°C，西北风 2 级，湿度 38%"


def post(body):
    r = requests.post(f"{BASE}/v1/chat/completions", json=body, timeout=180)
    return r.status_code, r.json()


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main():
    passed = 0

    # 1) 非流式触发 tool_calls
    st, j = post({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "北京现在天气怎么样？请用工具查询。"}],
        "tools": TOOLS,
    })
    ch = (j.get("choices") or [{}])[0]
    calls = (ch.get("message") or {}).get("tool_calls") or []
    ok = st == 200 and ch.get("finish_reason") == "tool_calls" and calls and calls[0]["function"]["name"] == "get_weather"
    passed += check("non-stream tool_call", ok,
                    f"{st} finish={ch.get('finish_reason')} calls={[c['function']['name'] for c in calls]}")

    # 2) 回灌工具结果 -> 最终答复
    if calls:
        st2, j2 = post({
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "北京现在天气怎么样？请用工具查询。"},
                {"role": "assistant", "content": None, "tool_calls": calls},
                {"role": "tool", "tool_call_id": calls[0]["id"],
                 "content": TOOL_RESULT},
            ],
            "tools": TOOLS,
        })
        text = ((j2.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ok2 = st2 == 200 and ("21" in text or "晴" in text)
        passed += check("tool result round-trip", ok2, repr(text[:90]))

    # 3) 流式触发 tool_calls
    r = requests.post(f"{BASE}/v1/chat/completions", json={
        "model": "gpt-4o-mini", "stream": True,
        "messages": [{"role": "user", "content": "上海现在天气怎么样？用工具查。"}],
        "tools": TOOLS,
    }, stream=True, timeout=180)
    chunks, got_calls, finish = [], [], None
    for line in r.iter_lines(decode_unicode=False):
        if not line or not line.startswith(b"data:"):
            continue
        try:
            d = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        chunks.append(d)
        delta = (d.get("choices") or [{}])[0].get("delta") or {}
        if delta.get("tool_calls"):
            got_calls.extend(tc["function"]["name"] for tc in delta["tool_calls"])
        if (d.get("choices") or [{}])[0].get("finish_reason"):
            finish = d["choices"][0]["finish_reason"]
    ok3 = finish == "tool_calls" and "get_weather" in got_calls
    passed += check("stream tool_call", ok3, f"chunks={len(chunks)} calls={got_calls} finish={finish}")

    # 4) 不需要工具时不应乱调
    st4, j4 = post({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "1+1 等于几？直接回答。"}],
        "tools": TOOLS,
    })
    ch4 = (j4.get("choices") or [{}])[0]
    ok4 = st4 == 200 and ch4.get("finish_reason") == "stop" and not (ch4.get("message") or {}).get("tool_calls")
    passed += check("no spurious tool call", ok4, f"finish={ch4.get('finish_reason')}")

    # 5) tool_choice=required 时，即使能直接回答也必须调用
    st5, j5 = post({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "1+1 等于几？"}],
        "tools": TOOLS,
        "tool_choice": "required",
    })
    ch5 = (j5.get("choices") or [{}])[0]
    ok5 = st5 == 200 and ch5.get("finish_reason") == "tool_calls"
    passed += check("tool_choice=required", ok5, f"finish={ch5.get('finish_reason')}")

    print(f"\n{passed}/5 passed")
    return 0 if passed == 5 else 1


if __name__ == "__main__":
    sys.exit(main())