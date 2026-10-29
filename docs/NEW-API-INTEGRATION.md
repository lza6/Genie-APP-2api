# new-api 对接指南 — Genie v7.9.2（co.appnation.geniechat）

读完这篇不用再问就能把渠道接上。

- 上游：`https://genie-production-yfvxbm4e6q-uc.a.run.app`
- 凭证：Firebase 匿名 idToken（**自动铸造，无需 App、无需真机、无需邮箱**）
- 本地网关：`demo_server.py`（OpenAI 兼容，`:8952`），已含号池与额度自愈
- 能力：**单模型 `gpt-4o-mini`**，SSE 流式文本。无生图、无语音、无视觉。

---

## 0. 先做选择：两种接入形态

| | 形态 A：Sidecar（推荐先做） | 形态 B：原生 Go 渠道 |
|---|---|---|
| 做法 | 把 `demo_server.py` 跑在 new-api 同机，new-api 用**现有 OpenAI 渠道类型**指向它 | 新增 `type` 渠道 + `relay/channel/genie` 适配器 |
| 代码量 | **0 行 Go** | 约 400–600 行 Go + 单测 |
| 优点 | 10 分钟通；号池/换号/额度记账已在 Python 侧实现并压测过 | 少一个进程；可复用 new-api 计费/限流/日志/渠道监控 |
| 缺点 | 多一个进程；new-api 看不到上游原始错误 | 得把号池和额度逻辑搬进 Go，且要处理「HTTP 200 + JSON 错误体」这种非标行为 |
| 适用 | 先验证价值、快速上线 | 长期生产 |

**建议**：先 A。号池策略稳定后再考虑 B，协议细节全在 `ANALYSIS.md` §4，照抄即可。

---

## 1. 形态 A：Sidecar（0 行 Go）

### 1.1 起 sidecar

```bash
pip install flask requests
cd /opt/genie-2api
python demo_server.py --host 0.0.0.0 --port 8952
```

生产建议用进程守护（systemd / supervisor / pm2），并把 `accounts.json` 放在持久盘上——号池就是这个文件。

### 1.2 在 new-api 里加渠道

| 字段 | 值 |
|------|-----|
| 类型 | **OpenAI**（`type = 1`） |
| Base URL | `http://127.0.0.1:8952` |
| 密钥 | 任意非空字符串（如 `genie-local`，网关不校验） |
| 模型 | `gpt-4o-mini` |

Base URL 不要带 `/v1`——new-api 的 OpenAI 渠道会自己拼 `/v1/chat/completions`，网关正好暴露该路径。

### 1.3 模型映射

只有 `gpt-4o-mini` 真能出字。**不要**把 `gpt-4o` / `gpt-4` / `gpt-3.5-turbo` 直接指过来当高配卖：上游对未知模型名不报错，而是返回 HTTP 200 + 一段
`"I would love to answer this, but it seems that..."` 的假答复，new-api 侧完全看不出来。

如果业务需要多个模型名，在渠道「模型重定向」里全部映射到 `gpt-4o-mini`，并在计费上按 mini 定价。

---

## 2. 形态 B：原生 Go 渠道要点

若要在 new-api 里写适配器（`relay/channel/genie`），以下是必须照做的点：

1. **上游请求体不是 OpenAI 格式**
   ```json
   {"model":"gpt-4o-mini","messages":[{"type":"prompt","content":"...","fromUser":true}]}
   ```
   `role` → `type`：`user` → `prompt`，`assistant` → `response`。`system` 上游忽略，必须前置拼进首条用户消息。
2. **响应是 SSE，但事件名非 OpenAI**
   只有 `event: completion` 的 `data:{"content":"..."}` 是正文；`completion-started` / `completion-done` 丢弃。要在适配器里转成 `dto.StreamResponse`。
3. **错误判定不能只看 HTTP 状态码**
   额度耗尽返回 **HTTP 200** + `{"error":{"key":"RATE_LIMIT_EXCEEDED",...}}`。必须在 body 层识别并映射成 new-api 的限流错误，否则会被计费成成功。
4. **请求头三个白标字段缺一不可**
   ```
   Authorization: Bearer <firebase idToken>
   X-App-Platform: android
   X-App-Version: 7.9.2
   X-App-Id: 1658377526
   ```
5. **401 不代表鉴权失败**
   后端对不存在的路由一律回 401。调试时若怀疑鉴权，先打 `/v2/chats/local/completions` 看是否返回 400 业务错误。
6. **`parameters` 全部无效**，不要按 OpenAI 语义传 `max_tokens` / `temperature`（传了也是静默忽略，容易误判）。
7. **上游不支持非流式**。new-api 拿到 `stream=false` 的客户端请求时，只能由适配器把 SSE 聚合后一次性返回。

---

## 3. 额度与限流策略（关键）

上游硬约束：**每账号 7 次 / UTC 日**，按 Firebase uid 计，与 IP、设备无关。

因此 new-api 侧的容量规划只有一条公式：

```
日可用请求数 ≈ 号池规模 × 7
```

建议：

- 在 new-api 里给这个渠道设置**很低的 RPM/TPM**，或者干脆放在「低优先级分组」——它扛不住主力流量。
- 网关层已经把换号做成对调用方透明：一个请求最多轮换 5 个号，全部失败才返回 429。
- 号池打满（默认 `GENIE_MAX_ACCOUNTS=500`，即 3500 次/日）会直接报错，需要扩上限或加多机。
- **不建议**把它作为付费分组的主模型；适合免费额度、试用、轻量问答。

---

## 4. 对接后验证清单

```bash
# 1) 网关自身健康
curl -s http://127.0.0.1:8952/api/stats
# {"accounts":11,"usable":1,"capacity_left":...,"daily_quota_per_account":7}

# 2) 直连网关（绕过 new-api）
curl -s http://127.0.0.1:8952/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}],"stream":false}'

# 3) 经 new-api 中转
curl -s http://127.0.0.1:3000/v1/chat/completions \
  -H "Authorization: Bearer <new-api token>" -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'
```

判定标准：第 3 步拿到非空 `choices[0].message.content`，且 new-api 日志里该请求计费正常、无 5xx。

---

## 5. 容易踩的坑

| 现象 | 原因 | 处理 |
|------|------|------|
| 返回「I would love to answer this...」 | 模型名不是 `gpt-4o-mini` | 模型重定向到 `gpt-4o-mini` |
| 400 `No messages provided` | `messages[].type` 不是 `prompt`/`response` | 检查请求体转换 |
| 全部 401 | 路径写错（后端对未知路由回 401，不是 404） | 核对 `/v2/chats/local/completions` |
| 偶发 429 | 号池打满 | 调大 `GENIE_MAX_ACCOUNTS` 或多机分号池 |
| 回答被截断/风格单一 | 上游忽略参数且输出偏短 | 无解，属模型本身特性 |
