"""Locate the upstream input truncation point and its policy (head vs tail)."""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import bench  # noqa: E402

bench.ACCT = bench.Acct()
from bench import stream, join_text, BASE  # noqa: E402


def haystack(chars, needle, pos=0.6):
    unit = "The quick brown fox jumps over the lazy dog while the system records telemetry data. "
    block = unit * max(1, chars // len(unit))
    cut = int(len(block) * pos)
    return block[:cut] + f" IMPORTANT: the access code is {needle}. " + block[cut:]


def ask(text, question, model="OPEN_AI_CHATGPT_4_0_O_MINI_MODEL", timeout=240):
    body = {"model": model, "messages": [{"type": "prompt", "content": f"{text}\n\nQuestion: {question}", "fromUser": True}]}
    st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=timeout)
    return st, join_text(chunks), len(text) + len(question)


def main():
    print("== fine boundary ==")
    rows = []
    for size in [228_000, 232_000, 236_000]:
        text = haystack(size, f"B{size}")
        st, ans, total = ask(text, "What is the access code? Answer with the code only.")
        ok = f"B{size}" in ans
        print(f"  target={size:7d} total={total:7d} st={st} found={ok} ans={ans[:70]!r}")
        rows.append({"target": size, "total": total, "status": st, "found": ok, "answer": ans[:200]})

    print("\n== needle near head inside oversized body (256K) ==")
    text = haystack(256_000, "EARLY256", pos=0.08)
    st, ans, total = ask(text, "What is the access code? Answer with the code only.")
    hit = "EARLY256" in ans
    print(f"  needle@8% st={st} found={hit} ans={ans[:80]!r}")
    rows.append({"target": 256_000, "needle_pos": "8%", "status": st, "found": hit, "answer": ans[:200]})

    with open("docs/data/bench_ctx_boundary.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())