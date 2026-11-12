# -*- coding: utf-8 -*-
"""Verify OpenAI image_url content block reaches Genie vision through the gateway."""
import json
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8952"
IMG = "https://placehold.co/600x400/1e3a8a/f8fafc/png?text=ZX9"

body = {
    "model": "gpt-4o-mini",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "图里写了什么字？背景偏什么色系？只回答这两点。"},
            {"type": "image_url", "image_url": {"url": IMG}},
        ],
    }],
}

r = requests.post(f"{BASE}/v1/chat/completions", json=body, timeout=180)
print("status:", r.status_code)
j = r.json()
text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
print("answer:", text[:300])
ok = "ZX9" in text.upper() or "zx9" in text.lower()
print("[PASS]" if ok else "[FAIL]", "vision via gateway")
sys.exit(0 if ok else 1)