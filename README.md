# Genie-APP-2api

Genie `co.appnation.geniechat` v7.9.2 (Premium) → 上游协议还原 → OpenAI 兼容 2API 网关。
仅用于安全研究与协议学习。

## TL;DR

上游 `https://genie-production-yfvxbm4e6q-uc.a.run.app`，鉴权是 **Firebase 匿名登录换 idToken**（apiKey 内嵌在 `resources.arsc`），无设备绑定、无证书固定。

**免费档只有一个模型 `gpt-4o-mini`，但它能识图、能跑 236K 字符上下文。** 生图与联网走 agent 工具，执行前被 Pro 权益硬拦。

```bash
pip install flask requests
python demo_server.py            # http://127.0.0.1:8952
GENIE_MODE=mock python demo_server.py   # 不消耗账号额度的本地模式

curl http://127.0.0.1:8952/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"你好"}]}'
```

浏览器打开 `http://127.0.0.1:8952/` 是 demo 页：左侧 34 个官方助手，中间对话/生图双 tab，右侧号池与延迟。

## 能力速览

| 能力 | 结论 |
|---|---|
| 可用模型 | **1 个**：`gpt-4o-mini`（其余 14 个名字全 402 `NEEDS_PRO`） |
| 上下文 | 236K **字符**通过 / 240K 起静默截断（按字符不按 token，中英一致） |
| 单次输出 | 约 1 200 中文字；请求 ≥2 800 字直接拒答 |
| 识图 | ✅ 图片直链放 content 第一行即可，实测能读图内文字与版面 |
| 函数调用 | 上游无 tools 通道；**网关侧伪调用已实现**，5/5 + 6/6 |
| 生图 | ❌ 协议通（`?type=image_generation` → `agent-status text2image`），Pro 拦 |
| 联网搜索 | ❌ 同 Pro 门禁 |
| 流式 | 恒 SSE，但**伪流式**：生成完一次性推，TTFT 6–9 s |
| 吞吐 | 86–122 中文字符/秒（≈51–72 tok/s） |
| `parameters` | 整块被上游丢弃（temperature/maxTokens/systemPrompt/seed 全无效） |

完整数据见 [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md)，协议细节见 [`docs/REVERSE-REPORT.md`](docs/REVERSE-REPORT.md)，对接 new-api 见 [`docs/NEW-API-INTEGRATION.md`](docs/NEW-API-INTEGRATION.md)。

## 协议（最小可用）

```
POST https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=<FIREBASE_KEY>
     {"returnSecureToken": true}          -> idToken / refreshToken / localId

POST https://genie-production-yfvxbm4e6q-uc.a.run.app/v2/chats/local/completions[?type=<action>]
     Authorization: Bearer <idToken>
     {model, messages:[{type:"prompt"|"response", content, fromUser}], parameters, chatId?}

<- SSE:
   completion-started  {"model":"OPEN_AI_CHATGPT_4_0_O_MINI_MODEL","service":"text2text"}
   agent-status        {"status":"tool_calling"|"tool_done","service":"text2image","step":0}
   completion          {"content":"..."} / {"content":"...","media":{"url":"...","blurhash":"..."}}
   completion-done     {"reason":"stop"}
```

- `X-App-*` 头实测非必需，只有 `Authorization` 是硬要求。
- 额度按 uid 计：**8 次/天**，超限仍是 HTTP 200 + 固定文案，且缺 `completion-done`。
- 生图/联网：URL 加 `?type=image_generation` / `?type=web_search` 进 agent 通道；服务端按 prompt 意图分类，`type` 值本身不决定行为。

## 目录

```
├── genie_client.py        协议封装 + 匿名账号池（文本 / 视觉 / agent 三条通道）
├── demo_server.py         OpenAI 兼容网关（chat / images / 伪函数调用 / mock 双模）
├── tool_calling.py        伪函数调用：工具协议注入、tool_call 解析、OpenAI 结构转换
├── demo.html              demo 页（对话 / 生图双 tab + 工具调用开关 + 号池面板）
├── stress_test.py         并发梯度压测
├── browser_test.py        Playwright E2E（助手列表 / 流式 / 生图门禁 / 工具调用）
├── verify_anon.py         匿名账号可用性单点验证
├── tools/
│   ├── smoke.py               网关冒烟（7 项）
│   ├── bench.py               能力基准：模型矩阵 / 上下文阶梯 / TPS / 视觉 / 工具
│   ├── check_tool_calling.py  伪函数调用 E2E（5 项）
│   ├── check_vision.py        视觉透传验证
│   ├── check_image.py         生图端点原始响应
│   ├── bench_tool_stability.py 工具触发稳定性
│   ├── probe_ctx_boundary.py  上下文边界定位
│   ├── probe_ctx_cjk.py       中文上下文对照
│   ├── probe_maxout*.py       输出规模边界
│   └── probe_vision_*.py      视觉真实性验证
└── docs/
    ├── CAPABILITIES.md        能力基准（模型清单、上限、TPS 原始数据）
    ├── REVERSE-REPORT.md      逆向报告（DTO、SSE、agent 通道、额度语义）
    ├── NEW-API-INTEGRATION.md new-api 对接指南
    ├── ANALYSIS-R1.md         第一轮分析（历史存档）
    └── data/                  各基准的原始 JSON
```

## 实测记录（2026-11-12）

| 项 | 结果 |
|---|---|
| 网关冒烟 | 7/7（health / models / 非流式 / 流式 / 助手 / 统计 / 生图门禁） |
| 伪函数调用 | 5/5 功能项 + 6/6 触发率，工具选择正确，延迟 1.87–2.80 s |
| 视觉透传 | OpenAI `image_url` → 正确读出图内文字、色值、版面 |
| mock 压测 1→20 并发 | 160 请求 0 失败，RPS 5.87 → 101.97 |
| real 压测 1/2/4 并发 | 18 请求 0 失败，p50 2311 / 2243 / 4053 ms |
| 浏览器 E2E | 4/4，console 零报错 |
| 生图权益伪造 | 9 种 header/body/平台伪造全部无效 |

## 限制

- 生图 / 联网需要真实 Pro 订阅 uid，免费档一张都出不了。
- 成功响应的 `media{url, blurhash}` 格式按 `server_sent_event.dart` 解析器反推，**没有真实成功样本验证**。
- 语音实时链路（`voice_chat_realtime` + `librive_text.so`）未还原。
- `media` 字段（客户端图片附件通道）在裸协议下不被识别，正确格式未破解。
- 额度窗口不是 UTC 自然日，跨零点验证不重置；具体窗口长度未定。

## 声明

本项目仅用于安全研究与协议学习，不对因使用造成的任何后果负责。请遵守目标服务的使用条款。
