# -*- coding: utf-8 -*-
"""本地网关冒烟：健康、模型表、非流式、流式、助手目录、统计。"""
import json
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8952"


def show(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {extra}")
    return ok


def main():
    passed = 0
    total = 0

    total += 1
    r = requests.get(f"{BASE}/health", timeout=10)
    passed += show("health", r.status_code == 200 and r.json().get("status") == "ok", r.text[:120])

    total += 1
    r = requests.get(f"{BASE}/v1/models", timeout=10)
    j = r.json()
    passed += show("models", r.status_code == 200 and any(m["id"] == "gpt-4o-mini" for m in j["data"]),
                   f"{len(j['data'])} models")

    total += 1
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "你好"}]}
    r = requests.post(f"{BASE}/v1/chat/completions", json=body, timeout=60)
    j = r.json()
    ok = r.status_code == 200 and bool(j.get("choices", [{}])[0].get("message", {}).get("content"))
    passed += show("chat non-stream", ok, repr(j.get("choices", [{}])[0].get("message", {}).get("content", ""))[:80])

    total += 1
    body["stream"] = True
    pieces, chunks = [], 0
    with requests.post(f"{BASE}/v1/chat/completions", json=body, stream=True, timeout=60) as resp:
        ok_status = resp.status_code == 200
        for line in resp.iter_lines(decode_unicode=False):
            if not line:
                continue
            text = line.decode("utf-8", "replace")
            if not text.startswith("data:"):
                continue
            payload = text[5:].strip()
            try:
                d = json.loads(payload)
            except json.JSONDecodeError:
                continue
            chunks += 1
            ch = d.get("choices", [{}])[0]
            if ch.get("delta", {}).get("content"):
                pieces.append(ch["delta"]["content"])
    joined = "".join(pieces)
    passed += show("chat stream", ok_status and chunks > 0 and bool(joined), f"{chunks} chunks, {len(joined)} chars")

    total += 1
    r = requests.get(f"{BASE}/v1/genie/assistants", timeout=120)
    j = r.json()
    n = len(j.get("data") or [])
    passed += show("assistants", r.status_code == 200, f"{n} assistants")

    total += 1
    r = requests.get(f"{BASE}/stats", timeout=10)
    j = r.json()
    ok = r.status_code == 200 and j.get("requests", 0) >= 2
    passed += show("stats", ok, f"requests={j.get('requests')} mode={j.get('mode')} pool={j.get('pool')}")

    total += 1
    r = requests.post(f"{BASE}/v1/images/generations", json={"prompt": "a red apple", "model": "genie-image"}, timeout=180)
    j = r.json()
    mode = requests.get(f"{BASE}/health", timeout=10).json().get("mode")
    if mode == "mock":
        ok = r.status_code == 200 and bool((j.get("data") or [{}])[0].get("url"))
        passed += show("image (mock)", ok, str(r.status_code))
    else:
        err = j.get("error") or {}
        steps = (j.get("genie") or {}).get("steps") or []
        ok = r.status_code == 402 and err.get("code") == "NEEDS_PRO" and any(s.get("service") == "text2image" for s in steps)
        passed += show("image gate", ok, f"{r.status_code} {err.get('code')} steps={[s.get('service') for s in steps]}")

    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())