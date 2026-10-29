# Genie 2API · co.appnation.geniechat v7.9.2

把 Genie（AppNation，`usegenie.ai`）的移动端协议还原成 OpenAI 兼容接口。纯 Python，零 APK 依赖，账号自动注册。

> 逆向细节、端点目录、踩坑记录见 **[ANALYSIS.md](ANALYSIS.md)**。

---

## 事实表（全部实测，非推断）

| 项目 | 值 |
|------|-----|
| 可用模型 | `gpt-4o-mini`（唯一。其它模型名会返回假答复） |
| 协议 | `POST /v2/chats/local/completions`，`text/event-stream` |
| 鉴权 | Firebase 匿名 ID Token，`Authorization: Bearer` |
| 额度 | **7 次 / 账号 / UTC 日** |
| 额度归属 | 按 uid，与 IP／设备无关 → 号池可横向扩 |
| 号池 | 匿名注册零验证，用尽自动补号 |
| 参数 | `systemPrompt`/`maxTokens`/`temperature` 上游忽略；system 由网关前置拼接 |
| 多轮 | 支持（`prompt` / `response` 交替） |
| 视觉／生图／语音 | 无 |

---

## 快速开始

```bash
cd D:\apk-analysis\genie792
pip install flask requests
python demo_server.py --port 8952
# 打开 http://127.0.0.1:8952
```

首次请求会自动注册一个 Firebase 匿名账号（约 1 秒），之后按需扩号。

### OpenAI 兼容调用

```bash
curl http://127.0.0.1:8952/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"你好"}],"stream":true}'
```

非流式去掉 `"stream":true` 即可；`role:"system"` 会被网关拼进首条用户消息。

### 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/chat/completions` | OpenAI 兼容，支持 `stream` |
| GET | `/v1/models` | 返回 `gpt-4o-mini` |
| GET | `/api/stats` | 号池可用数 / 今日已用 / 剩余容量 |
| GET | `/` | demo 页 |

---

## 号池

`accounts.json` 持久化，字段 `refresh_token / used / exhausted / day_key`。

- 取号 → 额度满或已耗尽 → 换号；全满 → 现注册新号（`register_anonymous_account()`）
- idToken 1 小时过期，靠 `refreshToken` 自动续期，不重复注册
- UTC 日切自动清零
- 环境变量：`GENIE_DAILY_QUOTA`(默认 7)、`GENIE_MAX_ACCOUNTS`(默认 500)、`GENIE_TIMEOUT`

---

## 实测验证

### 端到端

```
$ python browser_test.py
标题: Genie 2API · 本地 Demo
号池状态: 号池 1/6 可用 · 剩余 3 次
状态栏: 完成
助手回复(81 字符): 'SSE（Streaming SIMD Extensions）是一种...'
截图: evidence_demo.png
PASS
```

真实 Chromium，点建议 chip → 等流式收完 → 断言非空 → 截图 `evidence_demo.png`。

### 压测（本机直连，非代理）

| 并发 | 请求 | 成功 | 吞吐 | P50 | P95 | 号池 |
|------|------|------|------|-----|-----|------|
| 3 | 12 | 12/12 | 0.72 req/s | 3.20s | 5.72s | 自动扩至 2 号 |
| 6 | 30 | 30/30 | 1.09 req/s | 5.18s | 8.52s | 自动扩至 6 号 |
| 10 | 40 | 40/40 | 1.48 req/s | 6.10s | 10.06s | 自动扩至 11 号 |

**82 次请求零失败**，换号对调用方完全透明。吞吐受上游单账号 7 次/日约束，理论上限 ≈ 号池容量 / 日。

复跑：

```bash
python stress_test.py --requests 40 --concurrency 10
```

---

## 文件

| 文件 | 作用 |
|------|------|
| `genie_client.py` | 协议客户端 + 账号池（Firebase 注册/续期、SSE 解析、换号重试） |
| `demo_server.py` | Flask OpenAI 兼容网关 |
| `demo.html` | 单文件聊天 demo |
| `stress_test.py` | 压测（并发/延迟/号池观测） |
| `browser_test.py` | Playwright 真实浏览器验证 |
| `ANALYSIS.md` | 逆向分析全记录 |
| `quota_probe.py` / `param_probe.py` / `verify_anon.py` | 额度／参数／匿名号探测脚本 |
| `sys_assistants.json` | 抓到的 34 个系统助手（含 systemPrompts） |
| `blutter_out/` | Dart 全量反编译产物（`asm/` + `pp.txt`） |

---

## 已知限制与风险

- **单账号 7 次/日**是上游硬约束。要走量必须扩号池；号的存活周期未做长观察（当前无任何风控反馈）。
- **底模能力偏弱**：知识陈旧、输出偏短、偶发跑题（把 SSE 认成 SIMD 指令集）。适合轻量问答，不适合当主力模型。
- 上游 `parameters` 全忽略 —— `max_tokens` / `temperature` 无法生效，长回答不可控。
- 逆向产物仅供协议研究与本地验证，请自行评估对上游服务条款的影响。

---

## 凭证说明（脱敏）

本仓库**不含任何个人凭证或账号数据**：

- `genie_client.py` 里的 Firebase `apiKey` 是目标 APK 内嵌的客户端公开常量（取自 `resources.arsc`），非个人密钥，复现所必需，故保留。它只能用于该项目自身的 Firebase 服务，无法访问任何私人数据。
- 账号池 `accounts.json`（含 `refreshToken`）与抓取的 `sys_assistants.json` 均已在 `.gitignore` 中，不随仓库分发。
- 所有 idToken 都在运行时通过匿名注册现铸，仓库内不存在任何可用凭证；`.secrets/` 目录被忽略。

