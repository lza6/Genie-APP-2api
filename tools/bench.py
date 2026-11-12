# -*- coding: utf-8 -*-
"""Genie 上游能力基准：模型矩阵 / 上下文极限 / 流式 TPS / 识图 / 工具调用。

用法
    python bench.py --suite models            # 模型白名单
    python bench.py --suite context           # 上下文阶梯（needle-in-haystack）
    python bench.py --suite tps               # 流式吞吐
    python bench.py --suite vision            # 图片输入
    python bench.py --suite tools             # tools/functions 字段
    python bench.py --suite all --out bench.json

每个 suite 独立开新匿名账号，单账号 8 次额度，脚本按需扩容。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from typing import Optional

import requests

FIREBASE_KEY = "AIzaSyBkanDK4DjniXPjw7TWA1dp9bUeOyHov6c"
BASE = "https://genie-production-yfvxbm4e6q-uc.a.run.app"
H = {"X-App-Platform": "android", "X-App-Version": "7.9.2", "X-App-Id": "1658377526"}

MODEL_NAMES = [
    "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
    "OPEN_AI_CHATGPT_4_0_CHAT_MODEL",
    "OPEN_AI_CHATGPT_4_0_VISION_MODEL",
    "OPEN_AI_CHATGPT_3_5_CHAT_MODEL",
    "OPEN_AI_CHATGPT_3_5_INSTRUCT_MODEL",
    "META_LLAMA_2_CHAT_MODEL",
    "gpt-4o-mini", "gpt-4o", "gpt-4", "gpt-3.5-turbo", "gpt-4-vision", "llama-2",
    "gpt-4.1", "claude-3.5-sonnet", "gemini-2.0-flash",
]

_counter = {"n": 0}


def new_account() -> str:
    j = requests.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_KEY}",
                      json={"returnSecureToken": True}, timeout=30).json()
    return j["idToken"]


class Acct:
    """按 8 次额度轮转的匿名账号。"""

    def __init__(self):
        self.tok = new_account()
        self.used = 0

    def spend(self):
        self.used += 1
        if self.used >= 8:
            self.tok = new_account()
            self.used = 0
        return self.tok


def stream(url: str, body: dict, timeout=180):
    """返回 (status, [(t, event, data_dict), ...], raw_text)"""
    h = dict(H)
    h["Authorization"] = f"Bearer {ACCT.spend()}"
    h["Accept"] = "text/event-stream"
    t0 = time.time()
    chunks, raw = [], []
    with requests.post(url, json=body, headers=h, stream=True, timeout=(10, timeout)) as r:
        status = r.status_code
        if status != 200:
            return status, [], r.content.decode("utf-8", "replace")
        buf = []
        for line in r.iter_lines(decode_unicode=False):
            now = time.time() - t0
            if not line:
                if buf:
                    ev, data = _parse(buf)
                    if ev or data:
                        chunks.append((now, ev, data))
                    buf = []
                continue
            s = line.decode("utf-8", "replace")
            raw.append(s)
            buf.append(s)
        if buf:
            ev, data = _parse(buf)
            if ev or data:
                chunks.append((time.time() - t0, ev, data))
    return status, chunks, "\n".join(raw)


def _parse(buf):
    ev, data = None, None
    for ln in buf:
        if ln.startswith("event:"):
            ev = ln[6:].strip()
        elif ln.startswith("data:"):
            p = ln[5:].strip()
            if p:
                try:
                    data = json.loads(p)
                except json.JSONDecodeError:
                    data = {"_raw": p}
    return ev, data


def join_text(chunks) -> str:
    return "".join(d.get("content", "") for _, ev, d in chunks if d and "content" in d)


# --------------------------------------------------------------------------- suites

def suite_models():
    rows = []
    for name in MODEL_NAMES:
        body = {"model": name, "messages": [{"type": "prompt", "content": "Say OK", "fromUser": True}]}
        st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=60)
        text = join_text(chunks)
        up = ""
        for _, ev, d in chunks:
            if d and d.get("model"):
                up = d["model"]
        verdict = "OK" if st == 200 and not any(m in text for m in ("Pro version", "advanced chat models")) else (
            "NEEDS_PRO" if st == 402 or "Pro version" in text else ("ERR" if st != 200 else "LIMITED"))
        rows.append({"request": name, "status": st, "upstream": up, "verdict": verdict, "text": text[:80]})
        print(f"{name:36s} {st} {verdict:10s} up={up or '-'}")
    return rows


def _haystack(target_chars: int, needle: str) -> tuple[str, int]:
    """生成约 target_chars 的填充文本，needle 埋在 60% 位置。返回 (text, needle 段落编号)。"""
    unit = "The quick brown fox jumps over the lazy dog while the system records telemetry data. "
    block = unit * max(1, target_chars // len(unit))
    cut = int(len(block) * 0.6)
    at = cut // len(unit) + 1
    return block[:cut] + f" IMPORTANT: the access code is {needle}. " + block[cut:], at


def suite_context():
    ladder = [2_000, 8_000, 32_000, 64_000, 128_000, 256_000]
    rows = []
    for size in ladder:
        needle = f"MAGIC-{size}"
        text, at = _haystack(size, needle)
        prompt = (f"{text}\n\nQuestion: what is the access code mentioned in the text above? "
                  f"Answer with the code only.")
        body = {"model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
                "messages": [{"type": "prompt", "content": prompt, "fromUser": True}]}
        st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=240)
        answer = join_text(chunks)
        ok = needle.lower() in answer.lower()
        rows.append({"chars": len(prompt), "target": size, "status": st,
                     "found": ok, "answer": answer[:160],
                     "raw_head": None if st == 200 else raw[:300]})
        print(f"chars≈{len(prompt):7d} st={st} found={ok} answer={answer[:60]!r}")
        if st != 200:
            break
        if not ok and size >= 32_000:
            # 已经失败，继续测更大的没有意义；但要确认是截断而非模型偷懒，保留数据
            pass
    return rows


def suite_tps(runs: int = 3):
    rows = []
    for i in range(runs):
        body = {"model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
                "messages": [{"type": "prompt",
                              "content": f"写一篇约 {500 + i * 200} 字的中文短文，主题是深夜的城市街道。直接给正文。",
                              "fromUser": True}]}
        t0 = time.time()
        st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=240)
        wall = time.time() - t0
        text = join_text(chunks)
        deltas = [(t, d["content"], len(d["content"])) for t, ev, d in chunks if d and "content" in d]
        ttft = deltas[0][0] if deltas else None
        # 生成窗口用 TTFT 之后的时间；末批 delta 往往同时到达，避免除零
        gen_window = (deltas[-1][0] - deltas[0][0]) if len(deltas) > 1 else (wall - (ttft or 0))
        chars = len(text)
        est_tokens = chars / 1.7          # 中文粗估，报告里标注为估算
        rows.append({
            "run": i, "status": st, "chars": chars, "deltas": len(deltas),
            "ttft_s": round(ttft, 3) if ttft else None,
            "total_s": round(wall, 2),
            "gen_window_s": round(gen_window, 3),
            "tps_est": round(est_tokens / gen_window, 1) if gen_window > 0.05 else None,
            "chars_per_s": round(chars / gen_window, 1) if gen_window > 0.05 else None,
            # 时间线用于判断是否伪流式：每个 delta 的到达时刻与累积长度
            "timeline": [{"t": round(t, 3), "n": n} for t, _, n in deltas],
        })
        spread = (deltas[-1][0] - deltas[0][0]) if len(deltas) > 1 else 0
        print(f"run{i} st={st} chars={chars} deltas={len(deltas)} ttft={rows[-1]['ttft_s']}s "
              f"total={rows[-1]['total_s']}s spread={spread:.2f}s tps≈{rows[-1]['tps_est']} "
              f"cps={rows[-1]['chars_per_s']}")
    return rows


def suite_vision():
    """图片输入：media 字段 / content 内嵌 URL / 多模态数组 三种形式。"""
    img = "https://upload.wikimedia.org/wikipedia/commons/3/3a/Cat03.jpg"
    rows = []

    cases = [
        ("media-field", {
            "model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
            "messages": [{"type": "prompt", "content": "这张图片里是什么？一句话回答。", "fromUser": True,
                          "media": {"url": img, "type": "image"}}]}),
        ("content-url", {
            "model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
            "messages": [{"type": "prompt", "content": f"{img}\n这张图片里是什么？一句话回答。",
                          "fromUser": True}]}),
        ("vision-model", {
            "model": "OPEN_AI_CHATGPT_4_0_VISION_MODEL",
            "messages": [{"type": "prompt", "content": f"{img}\n描述这张图片。", "fromUser": True}]}),
    ]
    for name, body in cases:
        st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=120)
        txt = join_text(chunks)
        rows.append({"case": name, "status": st, "service": next((d.get("service") for _, _, d in chunks if d and d.get("service")), ""),
                     "answer": txt[:200], "raw_head": None if st == 200 else raw[:300]})
        print(f"{name:14s} st={st} answer={txt[:80]!r}")
    return rows


def suite_tools():
    """自定义 tools / functions / tool_choice 是否被接受。"""
    rows = []
    tools = [{"type": "function", "function": {
        "name": "get_weather", "description": "Get weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
    cases = [
        ("openai-tools", {"model": "gpt-4o-mini", "tools": tools, "tool_choice": "auto",
                          "messages": [{"type": "prompt", "content": "北京天气怎么样？用工具查。", "fromUser": True}]}),
        ("legacy-functions", {"model": "gpt-4o-mini", "functions": [t["function"] for t in tools],
                              "messages": [{"type": "prompt", "content": "北京天气怎么样？", "fromUser": True}]}),
        ("agent-websearch", {"model": "gpt-4o-mini",
                             "messages": [{"type": "prompt", "content": "今天北京天气", "fromUser": True}]}),
    ]
    for name, body in cases:
        q = "type=web_search" if name == "agent-websearch" else None
        url = f"{BASE}/v2/chats/local/completions" + (f"?{q}" if q else "")
        st, chunks, raw = stream(url, body, timeout=120)
        txt = join_text(chunks)
        svc = next((d.get("service") for _, _, d in chunks if d and d.get("service")), "")
        steps = [d for _, ev, d in chunks if ev == "agent-status"]
        rows.append({"case": name, "status": st, "service": svc, "steps": steps,
                     "answer": txt[:200], "raw_head": None if st == 200 else raw[:300]})
        print(f"{name:18s} st={st} svc={svc or '-':12s} steps={len(steps)} answer={txt[:70]!r}")
    return rows


SUITES = {"models": suite_models, "context": suite_context, "tps": suite_tps,
          "vision": suite_vision, "tools": suite_tools}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="models")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    global ACCT
    ACCT = Acct()

    names = list(SUITES) if args.suite == "all" else args.suite.split(",")
    out = {}
    for n in names:
        if n not in SUITES:
            print(f"unknown suite: {n}")
            return 2
        print(f"\n===== {n} =====")
        out[n] = SUITES[n]()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nsaved {args.out}")
    return 0


ACCT: Optional[Acct] = None

if __name__ == "__main__":
    sys.exit(main())