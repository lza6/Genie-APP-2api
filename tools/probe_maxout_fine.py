"""输出规模边界的精细阶梯。"""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import bench  # noqa: E402

bench.ACCT = bench.Acct()
from bench import stream, join_text, BASE  # noqa: E402


def run(target):
    body = {"model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
            "messages": [{"type": "prompt",
                          "content": f"写一篇约 {target} 字的中文说明文，主题是咖啡从埃塞俄比亚传到欧洲的历史。"
                                     "要求连续正文，不要标题、不要分点、不要解释你在做什么。",
                          "fromUser": True}]}
    st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=300)
    text = join_text(chunks)
    reason = next((d.get("reason") for _, ev, d in chunks if ev == "completion-done" and d), None)
    return st, len(text), reason, text[:60]


def main():
    rows = []
    for target in (2000, 2500, 2800):
        st, chars, reason, head = run(target)
        refused = "抱歉" in head or "无法" in head
        print(f"target={target} st={st} chars={chars} reason={reason} refused={refused} head={head!r}")
        rows.append({"target": target, "status": st, "chars": chars, "reason": reason,
                     "refused": refused, "head": head})
    with open("docs/data/bench_maxout_fine.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())