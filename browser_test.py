#!/usr/bin/env python3
"""真实浏览器验证 demo 页: 打开页面 -> 点建议 chip -> 等流式完成 -> 截图断言。

用法: python browser_test.py [--url http://127.0.0.1:8952/]
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8952/")
    parser.add_argument("--shot", default="evidence_demo.png")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        console_errors: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(args.url, wait_until="networkidle")
        assert page.title().strip(), "页面标题为空"
        print(f"标题: {page.title()}")
        print(f"号池状态: {page.inner_text('#stats')}")

        page.click(".chip >> nth=0")
        page.wait_for_selector(".msg.assistant .bubble", timeout=15_000)

        answer = ""
        for _ in range(90):
            page.wait_for_timeout(1000)
            answer = page.inner_text(".msg.assistant .bubble >> nth=-1").strip()
            if answer and "⚠️" in answer:
                break
            if answer and len(answer) > 20:
                break

        print(f"状态栏: {page.inner_text('#status')}")
        print(f"助手回复({len(answer)} 字符): {answer[:160]!r}")
        page.screenshot(path=args.shot, full_page=True)
        print(f"截图: {args.shot}")
        browser.close()

        if not answer or "⚠️" in answer:
            print("FAIL: 未拿到正常回复")
            return 1
        if console_errors:
            print(f"WARN: 控制台错误 {console_errors[:3]}")
        print("PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
