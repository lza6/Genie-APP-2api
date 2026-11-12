# Genie v7.9.2 (Premium) 逆向分析报告

> 目标：`co.appnation.geniechat` v7.9.2（Google Play 正式包，Premiun 档）→ 上游协议还原 → 本地 2API 网关。
> 方法：jadx 全量反编译 4 个 dex + blutter（Dart 3.5.3 AOT, arm64）还原 `libapp.so` + 线上探针复核。
> 实测时间：2026-11-12。全部结论均有命令输出或接口返回佐证，未验证项单列在文末。

---

## 1. TL;DR

| 项 | 结论 |
|---|---|
| 应用形态 | Flutter 3.22 / Dart 3.5.3 AOT，业务逻辑全部在 `libapp.so`，dex 只有壳与原生插件 |
| 加固 | 无壳、无反调试、无证书绑定；`libtoolChecker.so` 仅做环境自检，不影响协议 |
| 鉴权 | Firebase 匿名登录（`chatgpt-a61db`）换 idToken，`Authorization: Bearer`。apiKey 直接内嵌在 `resources.arsc` |
| 主通道 | `POST /v2/chats/local/completions`，恒为 SSE |
| 免费档模型 | 只有 `OPEN_AI_CHATGPT_4_0_O_MINI_MODEL`（= gpt-4o-mini），其余全部 `402 NEEDS_PRO` |
| 请求参数 | `parameters` 整块被上游忽略（含 `systemPrompt`），系统提示词必须网关侧拼接 |
| 额度 | 每个匿名 uid 每天 **8 次**；超限仍回 HTTP 200 + SSE 软错误文案，无 `completion-done` |
| 旁路资产 | `GET /v2/assistants/system` 返回 7 分类 **34 个官方助手**（含完整 prompt），可直接当人设用 |
| 生图 / 联网 | 协议完全打通（`?type=image_generation` → `agent-status text2image`），执行被 Pro 权益硬拦；9 种权益伪造全部无效 |
| 2API 状态 | 网关 + demo 页 + 压测全链路跑通；mock 160 请求 0 失败，real 18 请求 0 失败 |

---

## 2. APK 概览

| 项 | 值 |
|---|---|
| 包名 | `co.appnation.geniechat` |
| 版本 | 7.9.2，`X-App-Id: 1658377526` |
| 体积 | 91.8 MB，995 个条目 |
| dex | 4 个（7.98 MB + 1.57 MB + 7.25 MB + 80 KB），13 764 个类，jadx 反编译 1373 条警告（多为 Flutter 插件空壳） |
| 原生库 | `libapp.so` 17.4 MB（业务）、`libflutter.so` 10.7 MB、`libmlkit_google_ocr_pipeline.so` 11.1 MB、`librive_text.so`、`libagent-ndk.so`、`libtoolChecker.so` |
| Flutter 资源 | 32.6 MB（Rive 动画、34 个助手图标、14 种语言包 `packages/appnation_devkit/assets/langs/`） |
| 三方 SDK | Firebase（Auth/Firestore/Storage/RemoteConfig）、Adapty（订阅）、AppsFlyer、Amplitude、OneSignal、New Relic、ML Kit OCR、Rive |
| 厂商包 | `appnation_devkit` / `appnation_purchases` / `appnation_ui` / `appnation_utils` —— Appnation 自研白标框架，同一套代码复用到其产品矩阵 |

### 2.1 逆向路径

`libapp.so` 是 Dart AOT 快照，jadx 看不到任何业务方法。用 blutter 还原：

```
python blutter.py <apk>/lib/arm64-v8a <out>   # Dart 3.5.3, Snapshot 80a49c7111088100a233b2ae788e1f48
```

输出 183 个库目录（`asm/chatgpt/**` 为业务代码）、`objs.txt`、`pp.txt`、`blutter_frida.js`。

构建要点：Windows 下必须先 `vcvars64.bat` 再跑 blutter，否则 cmake 找不到 MSVC 工具链，Dart VM 编译直接失败。

---

## 3. 鉴权链

```
POST https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=AIzaSyBkanDK4DjniXPjw7TWA1dp9bUeOyHov6c
     {"returnSecureToken": true}
  -> { idToken, refreshToken, localId(uid), expiresIn: 3600 }

POST https://genie-production-yfvxbm4e6q-uc.a.run.app/v2/chats/local/completions
     Authorization: Bearer <idToken>
     X-App-Platform: android   X-App-Version: 7.9.2   X-App-Id: 1658377526
```

| 事实 | 证据 |
|---|---|
| apiKey 来自 `resources.arsc`（非 dex、非 so） | `AIzaSyBkanDK4DjniXPjw7TWA1dp9bUeOyHov6c`；Firebase project `chatgpt-a61db`（`iss` 字段） |
| Google App ID | `1:467270152911:android:9e21622fbcb4a9cc673d` |
| 匿名登录无需任何凭证 | 实测 200，uid 每次不同 |
| 无 `X-App-*` 头也能通 | 实测 `only-auth`（仅 Authorization）200 |
| 无 token / 坏 token | `401 {"success":false,"error":{"code":"UNAUTHORIZED"}}` |
| idToken 有效期 | 3600s，`securetoken.googleapis.com/v1/token` 可刷新 |

`auth_options.dart` 里 App 支持 Apple / Google / 密码less 登录，但对协议侧没有增量权限 —— 匿名 uid 与正式 uid 走同一条 completions 通道（服务端只按 uid 计额度，不校验 `provider_id`）。

---

## 4. 聊天协议

### 4.1 请求 DTO（`chat_request.dart::toJson` 全字段，blutter 还原）

```json
{
  "model": "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL",
  "messages": [
    {"type": "prompt",   "content": "...", "fromUser": true},
    {"type": "response", "content": "...", "fromUser": false}
  ],
  "parameters": {
    "maxTokens": 0, "temperature": 0.0, "topP": 0.0,
    "frequencyPenalty": 0.0, "presencePenalty": 0.0, "stop": [],
    "systemPrompt": "", "maxNewTokens": 0, "minNewTokens": 0,
    "topK": 0, "stopSequences": [], "seed": 0
  },
  "chatId": "...",
  "regeneratedMessageId": "..."
}
```

字段名顺序取自 `toJson` 汇编里 `AllocateArray(0x10)` 之后的字符串常量序列，无遗漏。

### 4.2 响应（恒 SSE，无 JSON 模式）

```
event: completion-started   data: {"model":"OPEN_AI_CHATGPT_4_0_O_MINI_MODEL","service":"text2text"}
event: completion           data: {"content":"你"}
event: completion           data: {"content":"好"}
event: completion-done      data: {"reason":"stop"}
```

- 实测单个事件可达 2 个 delta，说明服务端按 token 粒度推送.
- `Accept: application/json` 不改变行为，永远走 SSE.
- 软限流时的 SSE 只有 `completion-started`（`data: {}`）+ `completion`，**没有** `completion-done`.

### 4.3 模型矩阵（9 个模型名 × 独立账号，2026-11-12 实测）

| 请求 model | HTTP | 上游 model | 结果 |
|---|---|---|---|
| `gpt-4o-mini` | 200 | `OPEN_AI_CHATGPT_4_0_O_MINI_MODEL` | 正常答复 |
| `OPEN_AI_CHATGPT_4_0_O_MINI_MODEL` | 200 | 同上 | 正常答复 |
| `gpt-4o` | 402 | — | `NEEDS_PRO` |
| `gpt-4` | 402 | — | `NEEDS_PRO` |
| `gpt-3.5-turbo` | 402 | — | `NEEDS_PRO` |
| `gpt-4-vision` | 402 | — | `NEEDS_PRO` |
| `llama-2` | 402 | — | `NEEDS_PRO` |
| `OPEN_AI_CHATGPT_4_0_CHAT_MODEL` | 402 | — | `NEEDS_PRO` |
| `__garbage__` | 402 | — | `NEEDS_PRO` |

402 响应体：

```json
{"success":false,"error":{"code":"NEEDS_PRO",
 "message":"I would love to answer this, but it seems that you are not subscribed to the Pro version. Please upgrade to Pro for advanced chat models.",
 "details":{"message":"I would love to answer this, but it seems that you are not subscribed to the Pro version. ..."}}}
```

要点：**未知模型名也返回 402 而不是 400**，服务端对白名单内外一视同仁地拒绝。客户端枚举里的 6 个模型（`META_LLAMA_2_CHAT_MODEL`、`OPEN_AI_CHATGPT_3_5_CHAT_MODEL`、`OPEN_AI_CHATGPT_3_5_INSTRUCT_MODEL`、`OPEN_AI_CHATGPT_4_0_CHAT_MODEL`、`OPEN_AI_CHATGPT_4_0_O_MINI_MODEL`、`OPEN_AI_CHATGPT_4_0_VISION_MODEL`）只有 mini 对匿名账号开放。

### 4.4 `parameters` 被忽略（关键）

同一 prompt 三组对照，各自独立账号：

| 参数 | 期望效果 | 实测答复 |
|---|---|---|
| `systemPrompt: "You must answer only with the word BANANA."` | 答 BANANA | `2 + 2 = 4 😊` |
| `maxTokens: 64, temperature: 0.2` | 截断/低温 | `2 + 2 = 4 😊` |
| `seed: 42` | 可复现 | `2 + 2 = 4 😊` |

结论：`parameters` 整块在服务端被丢弃。App 里所有"人设/系统提示词"实际是客户端拼进 `messages` 第一条 prompt 的，不是走 `systemPrompt` 字段（`chatgpt/helper` 与 `chat_service.uploadMessages` 里能看到 `assistant`/`chat_history` 的拼装路径）。

对 2API 的影响：接 new-api 时 `system` 角色必须在网关侧前置拼进首条消息，否则静默失效。

### 4.5 多轮与历史

`type: response` 的历史条目有效。实测：

```
prompt:   "我的幸运色是青绿色，记住它。"
response: "好的，你的幸运色是青绿色。"
prompt:   "我的幸运色是什么？一个词。"
-> "Teal! 🎨"
```

服务端本身无状态（`chatId` 只是归属标记，实测带不带都返回 200 且内容不受影响），历史必须每次全量重放。

---

## 5. 额度模型

单账号连续请求（moderation 安全的 prompt，间隔 250ms）：

| 序号 | 状态 | 可见事件 | 正文 |
|---|---|---|---|
| #00–#07 | 200 | started / completion / done | 正常答复 |
| #08 | 200 | started / completion | `I would love to answer this, but it seems that you ...` |
| #09–#11 | 200 | 同上 | 同上 |

- **硬限 = 8 次/uid**，第 9 次开始软限流。
- **额度窗口不是 UTC 自然日**：UTC 00:05 用同一批 uid 复测，全部仍处于软限流，说明跨零点不重置（滚动 24h 或按首次使用时间计窗，未定）。
- 软限流不返回 4xx/429，而是 200 + 固定文案 + 缺 `completion-done` —— 客户端只能靠事件序列或文案识别。
- 额度按 **uid** 记：同一出口 IP 下新建的匿名 uid 首次请求全部正常（实测 3/3），不是 IP 级限流。
- 触发 moderation（`This request violates our content policy`）也计入额度。

网关侧因此做了两层：本地按 uid 记账（`used < 8` 才发请求），撞软限流的账号标记 `soft_limited` 并立即换号重试，两个机制同时兜底。

---

## 6. 旁路端点

| 端点 | 主机 | 鉴权 | 契约 |
|---|---|---|---|
| `GET /v2/assistants/system` | genie-production | Bearer | 7 分类 34 个助手；每项含 `uniqueKey / title(14 语言) / description / isPro / iconUrl / systemPrompts / prompt / questions / suggestions` |
| `POST /v2/chats/local/completions` | genie-production | Bearer | 主聊天 |
| `GET /v2/chats` | genie-production | Bearer | 会话列表（客户端 `chat_service` 引用） |
| `/v2/chats/{id}/messages/`、`/pin/`、`/summarize` | genie-production | Bearer | 消息上传/置顶/摘要（客户端路径拼接，未逐个实测） |
| `POST /v1/rate` | management-api | Bearer | NestJS 校验：`language must be a string` |
| `POST /v1/feedback` | management-api | Bearer | 同上 |
| `POST /v2/api-errors` | management-api | Bearer | 需 `environment`(对象) + `endpoint`(非空串) |

`/v2/assistants/system` 是本轮最有价值的旁路资产：34 个助手的 `prompt` 是完整的系统提示词，等于官方白送一套人设库。demo 页和网关都直接消费它（`genie_assistant` 参数 / `X-Genie-Assistant` 头）。

---

## 7. 生图与联网：agent 工具通道

### 7.1 触发方式

`chatgpt/data/enum/assistant_action_type.dart` 的枚举值（pp.txt 实例）：

| 枚举 | wire 值 |
|---|---|
| `none` | `none` |
| `imageGeneration` | `image_generation` |
| `voiceChat` | `voice_chat` |
| `webSearch` | `web_search` |

`AssistantAction` 还有 `generationType` 和 `mediaType` 两个字段，官方助手目录里的实际取值：

| 助手 | action.type | generationType / mediaType | isPro |
|---|---|---|---|
| `imageGenerator`、`designer` | `image_generation` | `image` | ✓ |
| `tattooArtist` | `image_generation` | `tattoo` | ✓ |
| `signatureCreator` | `image_generation` | `signature` | ✓ |
| `logoCreator` | `image_generation` | `logo` | ✓ |
| `askWeb`、`webSearch` | `web_search` | `web_search` / `image` | ✓ |
| `askPDF` | `pdf_summary` | mediaType `document` | ✓ |
| `botanist`、`imageAnalyzer` | `image_identifier` | `plantIdentifier` / `image` | ✓ |

`generationType` 只出现在 `assistant_helper.dart` 的页面路由参数和 `assistant_mapper` 里，**不进 HTTP 请求**。所以 tattoo / logo / signature 在协议层与普通生图无区别 —— 实测把 `?type` 换成 `tattoo` / `logo`，服务端照样走 `text2image`，因为它是按 prompt 意图分类的。

另外两个 action 的实测结果：`?type=image_identifier` 不触发任何工具，退化成普通文本；`pdf_summary` 也不需要专用通道，普通对话就能摘要。

`chat_service._sendMessage` 的反汇编里，URL 拼接处对 `imageGeneration` 与 `webSearch` 两个分支做了 `?type=` 追加：

```asm
cmp w1, Obj!AssistantActionType@f385a1   ; imageGeneration
b.eq 0xa92c40                            ; -> 拼 "?type=" + action.value
cmp w1, Obj!AssistantActionType@f385e1   ; webSearch
b.ne 0xa92ca0
```

所以生图请求就是普通 completions 加一个 query：

```
POST /v2/chats/local/completions?type=image_generation
{model:"OPEN_AI_CHATGPT_4_0_O_MINI_MODEL", messages:[{type:"prompt",content:"a red apple",fromUser:true}]}
```

### 7.2 服务端自己识别意图（`?type` 不是开关）

七组对照实测，结论是 `type` 只影响不到门禁：

| 请求 | 上游判定 | 结果 |
|---|---|---|
| `?type=image_generation` | `text2image` | Pro 门禁 |
| `?type=text2image` | `text2image` | Pro 门禁 |
| `?type=IMAGE_GENERATION` | `text2image` | Pro 门禁 |
| `?type=garbage` | `text2image` | Pro 门禁 |
| **不带 type**，prompt 写"画一只橘猫" | `text2image` | Pro 门禁 |
| `?type=image_generation` + body 塞 `parameters.systemPrompt` | `text2image` | Pro 门禁 |
| `?type=websearch`（小写，非枚举值） | 不触发工具 | 普通文本答复 |

把 type 换成任意字符串、甚至完全省略，只要 prompt 是生图意图，服务端都会走 `text2image` 工具。所以这是一个服务端 agent 意图分类器，`?type=` 只是客户端提示。

### 7.3 事件流

```
event: completion-started   data: {"model":"OPEN_AI_CHATGPT_4_0_O_MINI_MODEL","service":"text2text"}
event: agent-status         data: {"status":"tool_calling","service":"text2image","step":0}
event: completion           data: {"content":"..."}          # 工具调用前的中间文案
event: agent-status         data: {"status":"tool_done","service":"text2image","step":0}
event: completion           data: {"content":"..."}          # 工具结束后的最终文案
event: completion-done      data: {"reason":"stop"}
```

同一段文案会在 `tool_done` 前后各推一遍，网关按 `agent-status` 切段取最后一段（`genie_client._stream` 的 `segments`）。

成功出图时的媒体字段来自 `server_sent_event.dart` 的解析：`completion` 事件的 data 同时读 `content` 与 `media`，`ChatMessage.mediaUrl / mediaBlurhash` 落到 `CompletionMedia{url, blurhash}`。**该分支未被验证**——免费账号拿不到成功响应，格式是根据解析器反推的。

### 7.4 Pro 门禁（本次核心结论）

免费匿名 uid 的生图/联网请求：

```
HTTP 200  text/event-stream
event: agent-status  {"status":"tool_calling","service":"text2image","step":0}
event: completion    {"content":"I would love to answer this, but it seems that you are
                      not subscribed to the Pro version. Please upgrade to Pro for image generation."}
event: agent-status  {"status":"tool_done","service":"text2image","step":0}
event: completion-done {"reason":"stop"}
```

- **状态码是 200，不是 402**。文本模型走 HTTP 402 `NEEDS_PRO`，agent 工具走 200 + 文案，两套语义。
- 文案与软限流共用 "I would love to answer this..." 开头，只能靠尾句区分：
  - `Please upgrade to Pro for advanced chat models.` → 当日额度用尽（软限流）
  - `Please upgrade to Pro for image generation.` → 生图权益不足
  - `Please upgrade to Pro to get web search access.` → 联网权益不足

### 7.5 权益伪造测试（全部失败）

| 尝试 | 结果 |
|---|---|
| `X-Is-Pro: true` | 门禁不变 |
| `X-Premium-Status: active` | 门禁不变 |
| `premiumStatus: active`（header） | 门禁不变 |
| `X-Premium: 1` | 门禁不变 |
| body `{"isPro": true}` | 门禁不变 |
| body `{"premiumStatus": "active"}` | 门禁不变 |
| body `{"isPremium": true, "user": {"isPro": true}}` | 门禁不变 |
| `X-App-Platform: ios` | 门禁不变 |
| 去掉所有 `X-App-*` 头 | 门禁不变 |

客户端侧也没有可改的开关：`isPro` 只出现在 `assistant.dart` / `chat_suggestion.dart` / `seasonal_event.dart` 的展示字段；`premiumStatus` / `premiumProducts` 只在 `ANEnvironmentInfo` 里作为**上报字段**发给 management-api；`genie_wishes` 是 `shared_preferences` 本地键，`test_reset_credits` 是本地调试方法名。权益是服务端按 uid 查的，与客户端无关。

**结论：协议层已完全打通，执行层被账号权益硬拦。** 要出图只有一条路——拿真实 Pro 订阅账号，把它的 Firebase uid 导入号池。

---

## 8. 客户端结构（blutter 还原的业务分层）

```
chatgpt/
├── bloc/        auth, chat, conversation, create_assistant, dan_mode, explore,
│                gpt4_usage, pro_usage, tts, voice_chat, voice_chat_realtime, wish
├── data/
│   ├── model/   chat_request, chat_model, chat_message, chat_conversation, assistant,
│   │            genie_request_parameter, gpt_request_parameter, gpt4_message_limit,
│   │            credit_system_options, completion_media, custom_paywall_ids
│   ├── repository/  chat_repository, firebase_repository
│   └── service/ chat_service, assistants_service, feedback_service, firestore_service,
│                moderation_service, remote_config_service, seasonal_event_service
└── presentation/  page/chat, page_voice_chat_rt, page_onboarding, ...
```

值得单独说的三块：

- **`dan_mode` bloc**：App 自带 "Do Anything Now" 开关。提示词不在二进制里（`grep "do anything now"` 零命中），来自远程配置，属于官方自带越狱通道。
- **`voice_chat_realtime` + `librive_text.so`**：实时语音走另一套实时链路（WebRTC 类），不在 `/v2/chats` 上，本轮未展开。
- **`credit_system` / `wish`**：积分与"每日愿望"系统，`CreditSystemFeatures` 只有 `isEnabled / features[] / showPaywallDelayInSeconds / isDailyWishCarryOverEnabled / canUseWishSystem`，与聊天额度是两套独立额度。

付费墙：`NEEDS_PRO` 由服务端判定；客户端侧 `chat_model.dart` 有 `alwaysPro` / `paywallId` / `mediaNeedsPremium` 字段，Adapty 负责 SKU 与回执。逆向包里没有可伪造的本地开关 —— 402 是服务端硬判，改客户端常量无效。

---

## 9. 2API 网关

`2api/demo_server.py`（Flask，:8952）：

| 端点 | 行为 |
|---|---|
| `POST /v1/chat/completions` | OpenAI 兼容，`stream=true/false` 都支持；真流式（worker 线程 + queue 转发 `on_delta`） |
| 伪函数调用 | 上游无 tools 通道（`parameters` 整块被丢），改由网关 `tool_calling.py` 注入工具协议 → 解析 `{"tool_call":…}` → 标准 `tool_calls`；`role:tool` 回灌转 `[工具结果]`；`tool_choice=required` 用提示词强制 |
| `POST /v1/images/generations` | 转 `?type=image_generation`，透传上游 agent 步骤；Pro 门禁映射成 402 |
| `GET /v1/models` | 6 个模型条目，标注 `free` / `upstream` |
| `GET /v1/genie/assistants` | 34 个官方助手（含 prompt），1 小时缓存 |
| `GET /health` `/stats` | 号池余量、p50/p95、错误码分布 |
| `GET /` | demo 页 |

两个模式：`GENIE_MODE=real`（默认）打上游，`GENIE_MODE=mock` 本地合成答复（把网关逻辑验证与上游额度解耦）。

### 9.1 压测结果（2026-11-12 本机）

**mock 模式**（验证网关自身吞吐，不消耗账号）：

| 并发 | 请求 | 失败 | RPS | p50 | p95 |
|---|---|---|---|---|---|
| 1 | 40 | 0 | 5.87 | 168 ms | 182 ms |
| 5 | 40 | 0 | 28.56 | 169 ms | 179 ms |
| 10 | 40 | 0 | 57.19 | 171 ms | 176 ms |
| 20 | 40 | 0 | 101.97 | 186 ms | 196 ms |

p50 ≈ 180 ms 是 mock 内部 `0.02s × 9 片段` 的合成时延，不是网关开销。

**real 模式**（真打上游，号池自动扩容）：

| 并发 | 请求 | 失败 | wall | p50 | p95 | 说明 |
|---|---|---|---|---|---|---|
| 1 | 6 | 0 | 15.9 s | 2311 ms | 2353 ms | 期间触发一次账号注册 |
| 2 | 6 | 0 | 7.5 s | 2243 ms | 2460 ms | 换号正常 |
| 4 | 6 | 0 | 8.1 s | 4053 ms | 5019 ms | 并发 4 起上游明显变慢 |

压测结束时号池 3 个账号、`used_today=24`、`soft_limited=1` —— 软限流被正确识别并换号，没有把失败透传给客户端。

### 9.2 浏览器验证

demo 页在 mock（:8953）与 real（:8952）两端都用真实浏览器跑过：助手目录渲染 34 项、流式增量渲染、延迟标注、号池面板实时刷新，console 无报错。截图见 `shot_1_idle.png` / `shot_2_stream.png` / `shot_3_real.png`。

---

## 10. 限制与未验证项

| 项 | 状态 |
|---|---|
| 生图 / 联网（agent 工具通道） | 协议已打通并实测到 `agent-status text2image/websearch`；执行被 Pro 权益拦截，成功响应的 `media{url,blurhash}` 格式按解析器反推，未经真实成功样本验证 |
| 真实 Pro 账号 | 未获取。有订阅 uid 即可把 `image_generation` 打通到出图，代码路径已就绪（`genie_client.generate_image`） |
| 额度窗口长度 | 跨 UTC 零点不重置已实测；是滚动 24h 还是按首次使用计窗，需要隔日定点采样 |
| 语音实时链路 | `voice_chat_realtime` + `librive_text.so`，未抓包，协议未知 |
| `/v2/chats/{id}/messages/`、`/summarize` | 路径已从 `chat_service` 汇编提取，未逐个实测 |
| `dan_mode` 提示词 | 远程配置下发，未取到（需要 Frida hook `remote_config_service`） |
| 长期账号存活 | 匿名 uid 是否被上游批量清理，未做 7 天观测 |
| rate/feedback/api-errors | 只验证了 DTO 校验层，未验证业务语义 |

真实结论：**这个渠道的价值在 `gpt-4o-mini` + 34 个官方助手人设，不在"破解 Premium"**。402 是服务端硬判，任何客户端 patch 都拿不到 GPT-4o。