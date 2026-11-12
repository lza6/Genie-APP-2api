# -*- coding: utf-8 -*-
"""Genie 2API 网关压测。

用法
    python stress_test.py                          # mock 网关默认档位
    python stress_test.py --levels 1,5,10,20 --n 40
    python stress_test.py --base http://127.0.0.1:8952 --stream
    python stress_test.py --real --n 16 --levels 2,4   # 注意：真打上游会消耗匿名号池额度

指标：成功/失败、p50/p95/max、RPS、错误码分布、号池消耗。
"""
import argparse
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_BASE = "http://127.0.0.1:8952"
lock = threading.Lock()


def one_call(base, prompt, stream):
    t0 = time.time()
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    if stream:
        payload["stream"] = True
    try:
        if stream:
            chars, chunks = 0, 0
            with requests.post(f"{base}/v1/chat/completions", json=payload, stream=True, timeout=180) as r:
                if r.status_code != 200:
                    return {"ok": False, "status": r.status_code, "error": r.text[:160],
                            "ms": (time.time() - t0) * 1000}
                for line in r.iter_lines(decode_unicode=False):
                    if not line or not line.startswith(b"data:"):
                        continue
                    body = line[5:].strip()
                    try:
                        d = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    if "error" in d:
                        return {"ok": False, "status": 200, "error": d["error"].get("code", "ERR"),
                                "ms": (time.time() - t0) * 1000}
                    ch = d.get("choices", [{}])[0]
                    content = ch.get("delta", {}).get("content")
                    if content:
                        chars += len(content)
                        chunks += 1
            return {"ok": chars > 0, "status": 200, "ms": (time.time() - t0) * 1000,
                    "chars": chars, "chunks": chunks,
                    "error": None if chars else "empty_stream"}
        r = requests.post(f"{base}/v1/chat/completions", json=payload, timeout=180)
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "error": r.text[:160], "ms": (time.time() - t0) * 1000}
        j = r.json()
        text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"ok": bool(text), "status": 200, "ms": (time.time() - t0) * 1000, "chars": len(text),
                "error": None if text else "empty_reply"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "error": f"{type(e).__name__}: {e}", "ms": (time.time() - t0) * 1000}


def run_level(base, concurrency, total, stream, tag):
    results = []
    counter = {"i": 0}

    def worker(_):
        while True:
            with lock:
                if counter["i"] >= total:
                    return
                idx = counter["i"]
                counter["i"] += 1
            results.append(one_call(base, f"压力测试请求 #{idx}，请简短回应。", stream))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(worker, range(concurrency)))
    wall = time.time() - t0

    ok = [r for r in results if r["ok"]]
    ms = [r["ms"] for r in ok]
    errs = {}
    for r in results:
        if not r["ok"]:
            key = str(r.get("error"))[:60]
            errs[key] = errs.get(key, 0) + 1
    out = {
        "tag": tag, "concurrency": concurrency, "total": total, "wall_s": round(wall, 2),
        "rps": round(total / wall, 2) if wall else 0,
        "ok": len(ok), "fail": total - len(ok),
        "fail_rate": round((total - len(ok)) / total * 100, 1) if total else 0,
        "p50_ms": round(statistics.median(ms), 1) if ms else None,
        "p95_ms": round(sorted(ms)[int(len(ms) * 0.95) - 1], 1) if len(ms) > 1 else (round(ms[0], 1) if ms else None),
        "max_ms": round(max(ms), 1) if ms else None,
        "errors": errs,
    }
    print(json.dumps(out, ensure_ascii=False))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--levels", default="1,3,6,10")
    ap.add_argument("--n", type=int, default=20, help="每档请求数")
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--real", action="store_true", help="标记真打上游（只影响提示，不改行为）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    try:
        h = requests.get(f"{args.base}/health", timeout=10).json()
    except Exception as e:  # noqa: BLE001
        print(f"gateway unreachable: {e}")
        return 1
    print(f"gateway mode={h.get('mode')} pool={h.get('pool')}")
    if args.real and h.get("mode") != "real":
        print("warning: --real 指定但网关不是 real 模式")

    rows = []
    for c in levels:
        rows.append(run_level(args.base, c, args.n, args.stream, f"{'real' if args.real else 'auto'}-c{c}"))

    try:
        s = requests.get(f"{args.base}/stats", timeout=10).json()
        print(f"\nfinal stats: requests={s.get('requests')} ok={s.get('upstream_ok')} "
              f"err={s.get('upstream_err')} soft={s.get('soft_limited')} pool={s.get('pool')}")
    except Exception:  # noqa: BLE001
        pass

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())