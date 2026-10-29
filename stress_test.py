#!/usr/bin/env python3
"""Genie 2API 本地压测: 并发打本地网关, 验证号池自动换号与端到端延迟。

用法:
  python stress_test.py --requests 20 --concurrency 4
  python stress_test.py --requests 40 --concurrency 8 --mode nonstream
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request

PROMPTS = [
    "用一句话说明什么是 TCP 三次握手",
    "把 hello world 翻译成日语和法语",
    "写一个 Python 单行去重函数",
    "列举三个常见 HTTP 状态码及含义",
    "用 20 字概括相对论",
    "解释一下什么是幂等",
]


def one_request(base: str, stream: bool, index: int) -> dict:
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": PROMPTS[index % len(PROMPTS)]}],
        "stream": stream,
    }
    request = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8", "replace")
        elapsed = time.time() - started
        if stream:
            answer = ""
            for line in raw.split("\n\n"):
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    return {"ok": False, "elapsed": elapsed, "error": event["error"]["message"]}
                delta = (event.get("choices") or [{}])[0].get("delta") or {}
                answer += delta.get("content") or ""
            return {"ok": bool(answer), "elapsed": elapsed, "chars": len(answer)}
        data = json.loads(raw)
        if data.get("error"):
            return {"ok": False, "elapsed": elapsed, "error": data["error"]["message"]}
        answer = data["choices"][0]["message"]["content"]
        return {"ok": bool(answer), "elapsed": elapsed, "chars": len(answer)}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200]
        return {"ok": False, "elapsed": time.time() - started, "error": f"HTTP {error.code} {detail}"}
    except Exception as error:  # noqa: BLE001 - 压测需要记录任何失败
        return {"ok": False, "elapsed": time.time() - started, "error": str(error)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8952")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--mode", choices=["stream", "nonstream"], default="stream")
    args = parser.parse_args()

    stream = args.mode == "stream"
    results: list[dict] = []
    lock = threading.Lock()
    queue = list(range(args.requests))
    started = time.time()

    def worker() -> None:
        while True:
            with lock:
                if not queue:
                    return
                index = queue.pop()
            result = one_request(args.base, stream, index)
            with lock:
                results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(args.concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    total = time.time() - started

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    latencies = sorted(r["elapsed"] for r in ok)

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        k = min(len(latencies) - 1, int(round(p * (len(latencies) - 1))))
        return latencies[k]

    stats = json.loads(
        urllib.request.urlopen(f"{args.base}/api/stats", timeout=30).read().decode()
    )

    print("=" * 62)
    print(f"模式 {args.mode} | 并发 {args.concurrency} | 请求 {args.requests} | 耗时 {total:.1f}s")
    print(f"成功 {len(ok)} / 失败 {len(failed)} | 吞吐 {len(results)/total:.2f} req/s")
    if latencies:
        print(
            f"延迟 平均 {statistics.mean(latencies):.2f}s | P50 {pct(0.5):.2f}s | "
            f"P95 {pct(0.95):.2f}s | 最大 {latencies[-1]:.2f}s"
        )
    if ok:
        print(f"输出字符数 平均 {statistics.mean(r['chars'] for r in ok):.0f}")
    for failure in failed[:5]:
        print(f"  失败样本: {failure['error'][:120]}")
    print(f"号池: {stats['accounts']} 号 / {stats['usable']} 可用 / 今日已用 {stats['used_today']} 次")
    print("=" * 62)


if __name__ == "__main__":
    main()
