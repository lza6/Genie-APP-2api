"""上下文上限是按字符还是按 token：用中文文本重复同一阶梯对比英文结果。"""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import bench  # noqa: E402

bench.ACCT = bench.Acct()
from bench import stream, join_text, BASE  # noqa: E402

# 带编号的中文句子，避免完全重复导致的极端 token 压缩
CJK_UNIT = "第{i}段记录：值班员在午夜巡查了东侧走廊，确认照明与通风设备运转正常，随后回到控制室填写巡检表。"


def cjk_haystack(target_chars, needle, pos=0.6):
    parts, total, i = [], 0, 0
    while total < target_chars:
        s = CJK_UNIT.format(i=i)
        parts.append(s)
        total += len(s)
        i += 1
    block = "".join(parts)
    cut = int(len(block) * pos)
    return block[:cut] + f" 重要：访问码是 {needle}。 " + block[cut:]


def ask(text, question):
    body = {"model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
            "messages": [{"type": "prompt", "content": f"{text}\n\n问题：{question}", "fromUser": True}]}
    st, chunks, raw = stream(f"{BASE}/v2/chats/local/completions", body, timeout=240)
    return st, join_text(chunks), len(text)


def main():
    rows = []
    # 英文侧实测 236K 字符通过；若按 token 截断，中文应在大约 236K/4*1.5 ≈ 88K 字符附近失败
    for target in (60_000, 88_000, 120_000, 180_000, 236_000):
        needle = f"CN{target}"
        text = cjk_haystack(target, needle)
        st, ans, total = ask(text, "文本里提到的访问码是什么？只回答访问码。")
        ok = needle in ans
        print(f"  中文 chars={total:7d} st={st} found={ok} ans={ans[:60]!r}")
        rows.append({"lang": "zh", "chars": total, "target": target, "status": st,
                     "found": ok, "answer": ans[:200]})
        if not ok:
            break
    with open("docs/data/bench_ctx_cjk.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())