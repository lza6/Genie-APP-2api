# -*- coding: utf-8 -*-
"""伪函数调用的稳定性与多工具测试。"""
import json
import sys
import time

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8952"

TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather", "description": "查询指定城市当前的天气",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "城市名"}}, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "search_flights", "description": "查询两个城市之间的航班",
        "parameters": {"type": "object", "properties": {
            "from_city": {"type": "string"}, "to_city": {"type": "string"},
            "date": {"type": "string"}}, "required": ["from_city", "to_city"]}}},
]

PROMPTS = [
    "帮我查一下广州现在的天气。",
    "明天上海会下雨吗？用工具看看。",
    "查一下成都当前天气。",
    "我需要知道杭州的实时气温，调用工具。",
    "从北京飞深圳明天的航班有哪些？",
    "帮我找 12 月 3 日上海到东京的机票，用工具查。",
]


def call(prompt):
    t0 = time.time()
    r = requests.post(f"{BASE}/v1/chat/completions", json={
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "tools": TOOLS,
    }, timeout=180)
    dt = time.time() - t0
    j = r.json()
    ch = (j.get("choices") or [{}])[0]
    calls = (ch.get("message") or {}).get("tool_calls") or []
    return r.status_code, ch.get("finish_reason"), [c["function"]["name"] for c in calls], dt, \
        (ch.get("message") or {}).get("content") or ""


def main():
    ok = 0
    rows = []
    for i, p in enumerate(PROMPTS):
        st, finish, names, dt, text = call(p)
        hit = finish == "tool_calls" and names
        ok += bool(hit)
        rows.append({"prompt": p, "status": st, "finish": finish, "calls": names,
                     "latency": round(dt, 2), "content": text[:120]})
        print(f"{i} st={st} finish={finish} calls={names} {dt:.2f}s")
    print(f"\ntrigger rate: {ok}/{len(PROMPTS)}")
    with open("docs/data/bench_tool_stability.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0 if ok == len(PROMPTS) else 1


if __name__ == "__main__":
    sys.exit(main())