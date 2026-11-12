# -*- coding: utf-8 -*-
"""Inspect raw /v1/images/generations payload."""
import json
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8952"

r = requests.post(f"{BASE}/v1/images/generations",
                  json={"prompt": "a red apple on a wooden table", "model": "genie-image"}, timeout=180)
print("status:", r.status_code)
print(json.dumps(r.json(), ensure_ascii=False, indent=1)[:1600])