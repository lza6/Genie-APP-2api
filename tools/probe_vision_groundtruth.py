"""Vision ground-truth test using a self-describing image."""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import bench  # noqa: E402

bench.ACCT = bench.Acct()
from bench import stream, join_text, BASE  # noqa: E402

# placehold.co 会渲染成图片，图片内容（文字+背景色）完全由 URL 参数决定，
# 但 URL 本身把答案写明了；为排除"从 URL 猜"的可能，同时测一张参数不透明的图。
CASES = [
    # URL 里只有文字内容和色值，不含"文字居中/字号/留白"这类版面信息，
    # 答对这些只能说明真看到了像素
    ("layout-detail", "https://placehold.co/600x400/1e3a8a/f8fafc/png?text=ZX9",
     "图中文字在图里的什么位置？文字是深色还是浅色？图的四条边有留白吗？简短回答。"),
]


def ask(url, q):
    body = {"model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
            "messages": [{"type": "prompt", "content": f"{url}\n{q}", "fromUser": True}]}
    st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=180)
    return st, join_text(chunks)


def main():
    rows = []
    for name, url, q in CASES:
        st, ans = ask(url, q)
        print(f"{name:14s} st={st} → {ans[:200]!r}")
        rows.append({"case": name, "url": url, "status": st, "answer": ans[:400]})
    with open("docs/data/bench_vision_groundtruth.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())