# Genie v7.9.2 (Premium) 逆向分析

包名 `co.appnation.geniechat` · versionName 7.9.2 · 开发方 AppNation（`www.usegenie.ai` / `appnation.co`）

---

## 1. 结论摘要

| 项目 | 结论 | 证据等级 |
|------|------|----------|
| 应用形态 | Flutter AOT 单包，仅 `arm64-v8a`，无原生加壳 | 已验证（解包 + blutter 全量反编译） |
| 鉴权 | Firebase ID Token（`Authorization: Bearer`），匿名登录即可 | 已验证（真实请求 200） |
| 对话协议 | `POST /v2/chats/local/completions`，`text/event-stream` | 已验证（真实出字） |
| 可用模型 | 仅 `gpt-4o-mini`，其余模型名走兜底话术 | 已验证（11 个候选名逐一实测） |
| 免费额度 | 每账号 **7 次/日**（UTC 日切），按 uid 计 | 已验证（连发第 8 次触发限额） |
| 额度归属 | 按 Firebase uid，与 IP／设备无关 | 已验证（新 uid 同 IP 立刻可用） |
| 号池可行性 | 匿名注册无验证、无风控，可横向扩号 | 已验证（压力测试自动扩至 6 号，60/60 成功） |
| systemPrompt | 上游忽略 `parameters`，需网关层前置拼接 | 已验证（P1 实验 + 网关侧生效验证） |
| maxTokens / temperature | 上游忽略 | 已验证（maxTokens=10 输出 60+ token） |

**一句话**：Firebase 匿名登录 → 拿 idToken → 带三个白标 header 打 `/v2/chats/local/completions` → 读 SSE。每号 7 次/天，可无限发号。

---

## 2. APK 结构

```
lib/arm64-v8a/
  libapp.so            17.4 MB  Dart AOT 快照（业务逻辑全在此）
  libflutter.so        10.7 MB  Flutter 引擎（Dart 3.5.3 stable）
  libtoolChecker.so            anti-frida / 反调试探针
  librive_text.so              Rive 动画文本
  libmlkit_google_ocr_pipeline.so  OCR
AndroidManifest.xml
  包名 co.appnation.geniechat
  bin.mt.signature.KillerApplication（MT 管理器签名杀手，mod 版本特征）
  AdaptyPublicSdkKey（Adapty 订阅）
assets/flutter_assets/                 Rive 动画 / SVG / 引导视频
classes{1..4}.dex                      7.9 MB + 1.5 MB + 7.2 MB + 80 KB
```

`resources.arsc` 里直接躺着 Firebase 配置：

```
apiKey     AIzaSyBkanDK4DjniXPjw7TWA1dp9bUeOyHov6c
appId      1:467270152911:android:9e21622fbcb4a9cc673dba
projectId  chatgpt-a61db          ← 注意：项目名仍是 chatgpt，Genie 是它的白标换皮
```

---

## 3. 工具链与踩坑

| 步骤 | 工具 | 坑 |
|------|------|-----|
| 静态字符串 | `zipfile` + 正则扫 `libapp.so` | Dart AOT 字符串池乱序，只能靠上下文邻近猜，不能当结构用 |
| Dart 反编译 | **blutter**（Dart 3.5.3） | ① 需先 `vcvars64.bat` 注入 `LIB/INCLUDE`，否则 `LNK1104: 无法打开 kernel32.lib`；② APK 路径含中文（`逆向产物`）会被 GBK 误解码，改用已解压的 lib 目录作为输入；③ Windows 下 `.bat` 必须用完整路径调用 |
| 端点还原 | blutter `asm/` 反汇编字符串序列 | 按「函数内字符串引用顺序」读，比读汇编寄存器流快得多 |

审计脚本落盘在 `quota_probe.py` / `param_probe.py` / `verify_anon.py`，可直接复跑。

---

## 4. 协议还原

### 4.1 三层结构

```
① Firebase 认证          identitytoolkit.googleapis.com   （apiKey 硬编码）
② 业务 API               genie-production-yfvxbm4e6q-uc.a.run.app   （GCP Cloud Run + NestJS）
③ 流式响应               text/event-stream
```

另有 `management-api-production-vfywoevzkq-uc.a.run.app`（NestJS，承载 feedback / log-files / support），与本项目对话链路无关。

### 4.2 认证

```http
POST https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=<APIKEY>
Content-Type: application/json

{"returnSecureToken": true, "clientType": "CLIENT_TYPE_ANDROID"}
→ {"idToken": "...", "refreshToken": "...", "expiresIn": "3600", "localId": "<uid>"}
```

- 匿名登录**未关闭**，无邮箱、无验证、无滑块
- 邮箱密码注册同样开放（`accounts:signUp` 带 email），非必需
- `refreshToken` 走 `securetoken.googleapis.com/v1/token` 续期，1 小时一轮

### 4.3 业务请求

```http
POST https://genie-production-yfvxbm4e6q-uc.a.run.app/v2/chats/local/completions
Authorization: Bearer <firebase idToken>
X-App-Platform: android
X-App-Version: 7.9.2
X-App-Id: 1658377526
Content-Type: application/json

{
  "model": "gpt-4o-mini",
  "messages": [
    {"type": "prompt",   "content": "用户消息",           "fromUser": true},
    {"type": "response", "content": "助手历史消息",        "fromUser": false},
    {"type": "prompt",   "content": "新问题",             "fromUser": true}
  ],
  "regeneratedMessageId": null
}
```

- `X-App-Id` 取自 `AppConfig.appDetails().field_7`，静态值 `1658377526`
- `messages[].type` 只有 **`prompt`（用户）/ `response`（助手）** 会被采纳，其它值（`text`/`user`/`TEXT`…）会让服务端判定「没有消息」
- `parameters`（`systemPrompt` / `maxTokens` / `temperature` / `topP`）字段存在但服务端不读

### 4.4 流式响应

```
event: completion-started
id: e8dbba84-...
data: {"model":"OPEN_AI_CHATGPT_4_0_O_MINI_MODEL","service":"text2text"}

event: completion
id: e8dbba84-...
data: {"content":"Hello"}

event: completion-done
id: e8dbba84-...
data: {"reason":"stop"}
```

额度耗尽时**不是** 4xx，而是 HTTP 200 + JSON 正文：

```json
{"error":{"key":"RATE_LIMIT_EXCEEDED","message":"The daily usage limit for this model has been reached. After 23:57:36 hours the limits will be renewed."}}
```

网关必须靠正文内容判错，不能只看状态码。

---

## 5. 端点目录（从 `chat_service.dart` 反汇编还原）

| 方法 | 路径 | 用途 | 实测 |
|------|------|------|------|
| GET | `/v2/assistants/system` | 34 个系统助手（含 systemPrompts、isPro、多语言文案） | 200，无需额度 |
| POST | `/v2/chats/local/completions` | 无会话直接补全（**本项目唯一需要的端点**） | 200 SSE |
| POST | `/v2/chats` | 建会话 `{messages, explore, assistant, firstMessage, isPinned}` | 未使用 |
| POST | `/v2/chats/{id}/messages?chatModel=` | 会话内发消息 | 未使用 |
| POST | `/v2/chats/{id}/summarize` | 摘要 | 未使用 |
| POST | `/v2/chats/{id}/completions/` | 临时会话补全 | 未使用 |
| POST | `/v2/chats/{id}/pin/{bool}` | 置顶 | 未使用 |
| POST | `/auth/login` · `/auth/logout` | 后端账号同步（body `{"email":...}`） | 未使用 |
| POST | `/v2/api-errors` · `/v1/feedback` · `/v1/moderations` · `/v1/log-files` | 遥测/反馈 | 未使用 |

`GET /v2/assistants/system` 返回 250 KB 结构化数据（`sys_assistants.json`），包含全部助手系统提示词，可直接作为资产复用。

---

## 6. 关键踩坑（每条都踩过）

1. **`401 UNAUTHORIZED` 不等于鉴权失败。** 后端对**不存在的路由**统一回 401，只有真正匹配的路由才会返回 200/400。
   判别方法：故意打 `/zzz/qqq` 得到同样的 401 —— 于是所有 401 都不能作为「token 无效」的证据。最终是靠 `/v2/chats/local/completions` 返回 **400 业务错误**（而不是 401）才确认了正确路径。
2. **`messages[].type` 必须是 `prompt`。** 用 `role/content`、`type/text`、`fromUser` 组合都会得到 `"No messages provided"`——服务端按 `type` 过滤，`content` 不是过滤条件。这个值是靠枚举 9 个枚举值 × 4 种字段名组合试出来的。
3. **额度按 uid 不按 IP。** 初期以为是 IP 限流，实测换新 uid 同 IP 立刻可用，号池方案才成立。
4. **限流返回 HTTP 200。** 见 4.4。
5. **`parameters` 全部无效。** 服务端收下不读，systemPrompt 必须在网关层拼进首条用户消息。
6. **只有 `gpt-4o-mini` 真有效。** `gpt-4o` / `gpt-4` / `gpt-3.5-turbo` / `claude-3-5-sonnet` / `gemini-1.5-pro` / `llama-2` 等全部返回
   `data: {}` + 兜底文案「I would love to answer this, but it seems that...」——看着像成功，其实是假答复。
7. **`X-App-Id` 用包名无效。** 从 `an_http_client.dart` 反汇编确认取值来自 `ANAppDetails.field_7`，静态是数字 `1658377526`。

---

## 7. 能力与限制

**有**
- 文本对话，SSE 流式，中文流畅
- 多轮上下文（`prompt` / `response` 交替）
- 34 个系统助手 prompt 资产

**无**
- 生图 / 语音 / 视觉（`OPEN_AI_CHATGPT_4_0_VISION_MODEL` 常量存在但无对应可用接口）
- 非流式接口（服务端只有 SSE）
- 参数控制（temperature / maxTokens / stop 全被忽略）
- 单账号超过 7 次/天

**质量观察**
- 底模能力偏弱：会把 "SSE" 理解成 Streaming SIMD Extensions 这类型号旧、知识面窄的迹象
- 输出偏短，偏向营销式口吻（"😊 How can I assist you today?"）

---

## 8. 未验证项

- 付费（Adapty / Play Billing）解锁后的额度与 GPT-4 权限：**未验证**，需要真实订阅凭证，不在本次授权范围
- `/v2/chats` 完整会话链（建会话 → 发消息 → 摘要）未实测，本地补全端点已满足需求
- 图片输入（vision）协议字段未还原
- 长期封号策略未知：号池目前无任何风控反馈，但无 24h 以上观察数据
