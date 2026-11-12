"""生图相关 action 通道全量探测：4 种 generationType + image_identifier + pdf_summary。"""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import bench  # noqa: E402

bench.ACCT = bench.Acct()
from bench import stream, join_text, BASE  # noqa: E402

CASES = [
    ("image_generation", "画一只橘猫坐在窗台上，水彩风格"),
    ("image_identifier", "这张图片里是什么？https://placehold.co/600x400/1e3a8a/f8fafc/png?text=ZX9"),
    ("pdf_summary", "总结这份文档的要点：季度营收同比增长 12%，成本下降 3%。"),
    ("tattoo", "设计一个极简线条风格的狼头纹身"),
    ("logo", "设计一个咖啡店的极简 logo"),
]


def main():
    rows = []
    for action, prompt in CASES:
        url = f"{BASE}/v2/chats/local/completions?type={action}"
        body = {"model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
                "messages": [{"type": "prompt", "content": prompt, "fromUser": True}]}
        st, chunks, raw = stream(url, body, timeout=180)
        text = join_text(chunks)
        svc = ""
        steps = []
        for _, ev, d in chunks:
            if d and d.get("service"):
                svc = d["service"]
            if ev == "agent-status" and d:
                steps.append(f"{d.get('status')}:{d.get('service')}")
        pro = "Pro" in text or "pro version" in text.lower()
        print(f"{action:16s} st={st} svc={svc or '-':12s} steps={steps} pro={pro} txt={text[:70]!r}")
        rows.append({"action": action, "status": st, "service": svc, "steps": steps,
                     "pro_gate": pro, "text": text[:300]})
    with open("docs/data/bench_actions.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())