# Genie 2API → new-api 对接指南

自包含任务契约：任何没有上下文的会话/人可直接执行。

## 事实速查

1. 网关默认监听 `127.0.0.1:8952`，OpenAI 兼容：`POST /v1/chat/completions`（流式/非流式）、`GET /v1/models`、`GET /v1/genie/assistants`、`POST /v1/images/generations`。
2. **只有一个可用模型** `gpt-4o-mini`（上游 `OPEN_AI_CHATGPT_4_0_O_MINI_MODEL`）。其余 14 个模型名全部 `402 NEEDS_PRO`。
3. 上下文按**字符**算：236K 字符通过、240K 起静默截断（HTTP 仍是 200，末尾问题丢失）。配置时按 **200K 字符**留余量，不要按 token 配。
4. 单次输出约 1 200 中文字（≈800 tok）；请求目标 ≥2 800 字会被模型直接拒答。
5. 流式是**伪流式**：所有 delta 在生成完成后一次性到达，TTFT 6–9 秒，端到端 86–122 中文字符/秒。前端会看到长时间空白后整段出现。

## 路线 A：零代码 —— OpenAI 兼容渠道（推荐）

new-api 后台 → 渠道 → 新增：

| 字段 | 值 |
|---|---|
| 类型 | OpenAI |
| Base URL | `http://<网关主机>:8952`（**不要带 `/v1`**，new-api 会自己拼） |
| 密钥 | 网关未设鉴权时填任意非空串（如 `sk-genie`） |
| 模型 | `gpt-4o-mini` |

渠道「测试」按钮会打 `/v1/chat/completions`，本网关支持，能正常通过——不像纯图像网关那样必然失败。

客户端调用：

```bash
curl http://<new-api>/v1/chat/completions -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"你好"}]}'
```

### 模型倍率

`gpt-4o-mini` 在 new-api 内置 `ModelRatio` 表里有标准价，**不需要手工配**。如果想让用户看到独立的模型名，可在网关侧加别名（`genie-4o-mini`），但那样要在 new-api 里补一条倍率，否则按兜底倍率计费。

### 上下文与输出长度配置

new-api 的模型配置里：

| 项 | 建议值 | 依据 |
|---|---|---|
| 上下文 | 200 000（字符） | 实测 236K 通过 / 240K 截断，留 15% 余量 |
| 最大输出 | 1 200（中文 tok 估算） | 实测模型自然收尾在 1 000–1 400 字 |
| 最大请求超时 | 300 s | 长上下文场景单次可达 30 s 以上 |

## 能力边界（配渠道前必须知道）

| 能力 | 状态 | 对接影响 |
|---|---|---|
| 文本对话 | ✅ | 正常用 |
| 多轮上下文 | ✅ | 全量重放，网关按 messages 数组透传 |
| 识图 | ✅ | 客户端按 OpenAI `image_url` 内容块传图，网关自动还原成"URL + 问题"。URL 必须公网可直连（重定向/无扩展名会抓取失败） |
| 函数调用 | ⚠️ 网关伪调用 | 标准 `tools` 数组可用、`finish_reason=tool_calls` 正常、`role:tool` 回灌正常；但**不是上游原生**，靠提示词约束，偶发模型输出解释文字导致该轮不触发 |
| 流式 | ⚠️ 伪流式 | 不要承诺"逐字出现"的体验 |
| 生图 | ❌ | `/v1/images/generations` 会回 `402 NEEDS_PRO`，**不要给 new-api 配图像渠道** |
| 联网搜索 | ❌ | 同 Pro 门禁 |
| 温度/最大长度参数 | ❌ | 上游整块忽略，网关也不转发；不要依赖这些参数调行为 |

## 路线 B：原生 Go 渠道（不推荐）

本网关就是标准 OpenAI chat 语义，路线 A 足够。确需原生渠道时改动清单：

1. `constant/channel.go`：新增 `ChannelTypeGenie`
2. `constant/api_type.go`：iota 顺序追加 + `common/api_type.go` 的 `ChannelType2APIType` 映射
3. `relay/adaptor/genie/`：实现 `relay.Adaptor`，`GetRequestURL` = baseURL + `/v1/chat/completions`，`DoRequest` 直接转发（鉴权在网关内完成，new-api 不需要碰 Firebase）
4. `relay/adaptor.go` 的 `GetAdaptor`、前端 `web/src/features/channels/constants.ts` 两处同步
5. 计费走 `relay/text_handler.go` 原有路径，无需改

## 上游凭证失效时的恢复动作

网关返回 401 且日志含 `UNAUTHORIZED` 时：

1. 检查 `accounts.json` 是否存在且 uid 未被上游清理。
2. 网关会自己用 `refreshToken` 续期；续期失败会自动注册新匿名 uid。
3. 若 Firebase apiKey 失效（`SIGNUP_FAILED`），到 APK 的 `resources.arsc` 里重新提取 `AIza...`，替换 `genie_client.py` 的 `FIREBASE_KEY`。
4. 重启网关：`GENIE_MODE=real GENIE_PORT=8952 python demo_server.py`。

## 已知坑

| 坑 | 规避 |
|---|---|
| Base URL 带 `/v1` | 只填 `http://host:port` |
| 按 token 配上下文 | 上游按字符截断，必须按字符配（200K） |
| 长对话突然答非所问 | 累积超过 236K 字符被静默截断，网关侧要做历史裁剪 |
| 期望真流式 | 伪流式，别在 UI 上做"正在输入"的逐字动画假设 |
| 给 `tools` 传很多函数 | 伪调用靠提示词，工具越多越容易选错，建议 ≤5 个 |
| 指望生图渠道 | 免费档 0 张，需 Pro uid 才能过门禁 |
| 相信 `temperature=0` 可复现 | 参数被上游丢弃，同一 prompt 多次结果不同 |

## 验收清单

- [ ] `curl :8952/health` → `{"status":"ok","mode":"real",...}`
- [ ] `curl :8952/v1/models` → 6 条，`gpt-4o-mini` 标 `free: true`
- [ ] `curl :8952/v1/genie/assistants` → 34 个官方助手
- [ ] new-api 渠道测试通过（打的是 chat/completions）
- [ ] 真实对话一次（200 + 非空 content）
- [ ] 流式一次（SSE 有 delta，`finish_reason=stop`）
- [ ] 长上下文一次（≥100K 字符，needle 能命中）
- [ ] 函数调用一次（`finish_reason=tool_calls` + `role:tool` 回灌）
- [ ] 图像渠道未配置（确认没误配）

## 一句话

免费档就是一个 `gpt-4o-mini`：能长上下文（236K 字符）、能识图、能伪函数调用、伪流式、约 1 200 字输出。生图和联网没有，别配。
