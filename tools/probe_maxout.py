"""Max output length probe: ask for a long article, measure where it stops."""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import bench  # noqa: E402

bench.ACCT = bench.Acct()
from bench import stream, join_text, BASE  # noqa: E402


def main():
    rows = []
    for target in (1500,):
        body = {"model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
                "messages": [{"type": "prompt",
                              "content": f"写一篇约 {target} 字的中文说明文，主题是咖啡从埃塞俄比亚传到欧洲的历史。"
                                         "要求连续正文，不要标题、不要分点、不要解释你在做什么。",
                              "fromUser": True}]}
        st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=300)
        text = join_text(chunks)
        reason = next((d.get("reason") for _, ev, d in chunks if ev == "completion-done" and d), None)
        tails = text[-60:].replace("\n", " ")
        rows.append({"target": target, "status": st, "chars": len(text),
                     "est_tokens": round(len(text) / 1.7), "finish_reason": reason, "tail": tails})
        print(f"target={target} st={st} chars={len(text)} est_tok≈{round(len(text)/1.7)} "
              f"reason={reason} tail={tails!r}")
    with open("docs/data/bench_maxout.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())