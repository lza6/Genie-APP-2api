"""Vision reality check: is the model actually seeing the image, or guessing from the URL?"""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import bench  # noqa: E402

bench.ACCT = bench.Acct()
from bench import stream, join_text, BASE  # noqa: E402

CASES = [
    # 骰子图，URL 里没有任何内容提示
    ("dice-known",
     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/280px-PNG_transparency_demonstration_1.png",
     "这张图里有几个骰子？它们朝上的点数分别是多少？简短回答。"),
    # 随机图，URL 是 picsum 的随机种子，内容不可从 URL 推断
    ("picsum-random",
     "https://picsum.photos/seed/geniebench42/600/400",
     "描述这张图片的主要内容和主色调，两句以内。"),
    # 404 图，验证模型会不会凭空编造
    ("dead-url",
     "https://upload.wikimedia.org/wikipedia/commons/this-file-does-not-exist-xyz.jpg",
     "这张图片里是什么？如果看不到就直说看不到。"),
]


def ask(img_url, question):
    body = {"model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
            "messages": [{"type": "prompt", "content": f"{img_url}\n{question}", "fromUser": True}]}
    st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=180)
    return st, join_text(chunks)


def main():
    rows = []
    for name, url, q in CASES:
        st, ans = ask(url, q)
        print(f"{name:14s} st={st} → {ans[:160]!r}")
        rows.append({"case": name, "url": url, "status": st, "answer": ans[:400]})
    with open("docs/data/bench_vision_reality.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())