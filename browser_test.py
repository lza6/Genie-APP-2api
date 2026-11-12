# -*- coding: utf-8 -*-
"""Playwright E2E：demo 页真实路径（助手列表 / 流式对话 / 生图门禁）。

用法：python browser_test.py [base_url]
截图输出：shot_1_idle.png / shot_2_stream.png / shot_3_image.png
"""
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8952"


def main():
    errors = []
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        # 402 是生图门禁的预期状态码，浏览器会把它记进 console，不计为失败
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text}")
                if m.type == "error" and "402" not in m.text else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1200)

        n_assistants = page.locator("#assistantList .item").count()
        results.append(("assistant list", n_assistants >= 30, f"{n_assistants} items"))
        page.screenshot(path="shot_1_idle.png")

        # 对话
        page.fill("#input", "用一句话介绍你自己")
        page.click("#sendBtn")
        page.wait_for_function(
            "() => { const m = document.querySelector('.msg.assistant .meta'); return m && m.textContent.includes('ms'); }",
            timeout=90000,
        )
        reply = page.locator(".msg.assistant .body").first.inner_text()
        results.append(("chat stream", len(reply) > 0, reply[:60].replace("\n", " ")))
        page.screenshot(path="shot_2_stream.png")

        # 生图（免费档预期被 Pro 门禁拦）
        page.click('.tabs button[data-tab="image"]')
        page.fill("#imgPrompt", "a red apple on a wooden table")
        page.click("#imgBtn")
        page.wait_for_function(
            "() => document.querySelector('#imgOut').innerText.trim().length > 0 && "
            "!document.querySelector('#imgOut').innerText.includes('请求中')",
            timeout=120000,
        )
        page.wait_for_timeout(600)
        out = page.locator("#imgOut").inner_text()
        gated = "NEEDS_PRO" in out
        results.append(("image gate surfaced", gated, out[:90].replace("\n", " ")))
        page.screenshot(path="shot_3_image.png")

        # 工具调用（勾选开关，期望出现 toolcard 并拿到最终答复）
        page.click('.tabs button[data-tab="chat"]')
        page.check("#toolToggle")
        page.fill("#input", "北京现在天气怎么样？用工具查。")
        page.click("#sendBtn")
        page.wait_for_function(
            "() => document.querySelectorAll('.toolcard').length > 0",
            timeout=90000,
        )
        page.wait_for_function(
            "() => { const m = [...document.querySelectorAll('.msg.assistant .meta')].pop();"
            " return m && m.textContent.includes('tools'); }",
            timeout=120000,
        )
        final = page.locator(".msg.assistant .body").last.inner_text()
        has_tool = page.locator(".toolcard").count() > 0
        results.append(("tool calling UI", has_tool and ("21" in final or "晴" in final),
                        final[:70].replace("\n", " ")))
        page.uncheck("#toolToggle")
        page.screenshot(path="shot_4_tools.png")

        browser.close()

    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if errors:
        print("\nconsole errors:")
        for e in errors[:10]:
            print("  ", e)
    else:
        print("\nconsole errors: none")
    ok_all = all(ok for _, ok, _ in results) and not errors
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())