# -*- coding: utf-8 -*-
"""Genie (co.appnation.geniechat) 7.9.2 上游协议客户端 + 匿名账号池。

协议来源：libapp.so (Dart 3.5.3 AOT) blutter 反编译 + 线上探针复核（2026-11-12）。

鉴权链
    Firebase 匿名 signUp(apiKey 内嵌 resources.arsc) -> idToken
    Authorization: Bearer <idToken>
    X-App-Platform / X-App-Version / X-App-Id（实测非必需，客户端照发）

聊天
    POST {CHAT_BASE}/v2/chats/local/completions[?type=<action>]
    body: {model, messages:[{type:"prompt"|"response", content, fromUser, media?}],
           parameters:{maxTokens, temperature, topP, ..., systemPrompt, seed},
           chatId?, regeneratedMessageId?}
    响应恒为 SSE，事件四类：
        completion-started  {"model":"OPEN_AI_...","service":"text2text"}
        agent-status        {"status":"tool_calling"|"tool_done","service":"text2image"|"websearch","step":0}
        completion          {"content":"<增量>"} / {"content":"...","media":{"url":"...","blurhash":"..."}}
        completion-done     {"reason":"stop"}

已确认的上游行为
    - parameters 全字段被忽略（systemPrompt/temperature/maxTokens/seed 均无效果）
    - 免费档只有 OPEN_AI_CHATGPT_4_0_O_MINI_MODEL（gpt-4o-mini），其余模型 HTTP 402 NEEDS_PRO
    - 每匿名 uid 每天 8 次；超限仍 HTTP 200 + SSE 软错误文案，且没有 completion-done
    - 生图/联网走 agent tool：服务端自己识别意图（不认 ?type 的值，乱填或省略都照样触发），
      工具执行前做 Pro 校验，免费账号拿到 200 + "upgrade to Pro for image generation"
    - 伪造 premium 的 header/body 字段（X-Is-Pro / premiumStatus / isPro / isPremium /
      X-App-Platform:ios）全部无效，权益是服务端按 uid 查的
    - GET /v2/assistants/system 需要 Bearer，返回 7 分类 34 个官方助手（含 action 与 prompt）
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Optional

import requests

FIREBASE_KEY = "AIzaSyBkanDK4DjniXPjw7TWA1dp9bUeOyHov6c"
CHAT_BASE = "https://genie-production-yfvxbm4e6q-uc.a.run.app"
MGMT_BASE = "https://management-api-production-vfywoevzkq-uc.a.run.app"

APP_HEADERS = {
    "X-App-Platform": "android",
    "X-App-Version": "7.9.2",
    "X-App-Id": "1658377526",
}

FREE_MODEL = "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL"
MODEL_ALIASES = {
    "gpt-4o-mini": FREE_MODEL,
    "OPEN_AI_CHATGPT_4_0_O_MINI_MODEL": FREE_MODEL,
    "gpt-4o": "OPEN_AI_CHATGPT_4_0_CHAT_MODEL",
    "gpt-4": "OPEN_AI_CHATGPT_4_0_CHAT_MODEL",
    "OPEN_AI_CHATGPT_4_0_CHAT_MODEL": "OPEN_AI_CHATGPT_4_0_CHAT_MODEL",
    "gpt-4-vision": "OPEN_AI_CHATGPT_4_0_VISION_MODEL",
    "gpt-3.5-turbo": "OPEN_AI_CHATGPT_3_5_CHAT_MODEL",
    "gpt-3.5-instruct": "OPEN_AI_CHATGPT_3_5_INSTRUCT_MODEL",
    "llama-2": "META_LLAMA_2_CHAT_MODEL",
}
PRO_MODELS = {v for k, v in MODEL_ALIASES.items() if v != FREE_MODEL}

# 客户端 AssistantActionType 枚举：none / imageGeneration / voiceChat / webSearch / ...
ACTIONS = {"image_generation", "web_search", "voice_chat"}

QUOTA_PER_ACCOUNT_DAY = 8          # 同一 uid 连续第 9 次开始软限流
# 软限流与 Pro 门禁共用同一套 "I would love to answer this..." 文案头，
# 只能靠尾句区分：advanced chat models = 当日额度用尽；image generation / web search = 权益不足
QUOTA_SOFT_MARKERS = ("advanced chat models",)
PRO_MARKERS = ("for image generation", "web search access")
MODERATION_MARKERS = ("violates our content policy", "content policy")

ACCOUNTS_FILE = os.environ.get("GENIE_ACCOUNTS", os.path.join(os.path.dirname(__file__), "accounts.json"))


class GenieError(Exception):
    def __init__(self, code: str, message: str, status: int = 0, payload=None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.payload = payload or {}


def _post(url: str, body, headers: Optional[dict] = None, timeout: float = 90):
    if body is None:
        r = requests.get(url, headers=headers, timeout=timeout)
    else:
        r = requests.post(url, json=body, headers=headers, timeout=timeout)
    return r.status_code, r.content, r.headers


# --------------------------------------------------------------------------- 账号池

@dataclass
class Account:
    uid: str
    id_token: str
    refresh_token: str
    expires_at: float
    day: str = ""
    used: int = 0
    soft_limited: bool = False
    failures: int = 0

    @property
    def usable(self) -> bool:
        return not self.soft_limited and self.used < QUOTA_PER_ACCOUNT_DAY


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class AccountPool:
    """Firebase 匿名账号池：按需扩容，按 uid 记账，额度内轮转。"""

    def __init__(self, path: str = ACCOUNTS_FILE, quota: int = QUOTA_PER_ACCOUNT_DAY):
        self.path = path
        self.quota = quota
        self.lock = threading.RLock()
        self.accounts: list[Account] = []
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self.accounts = [Account(**a) for a in raw]
        except Exception:
            self.accounts = []

    def save(self):
        with self.lock:
            data = [asdict(a) for a in self.accounts]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def _roll_day(self, acc: Account):
        today = _today()
        if acc.day != today:
            acc.day = today
            acc.used = 0
            acc.soft_limited = False

    def _signup(self) -> Account:
        st, raw, _ = _post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_KEY}",
            {"returnSecureToken": True},
        )
        if st != 200:
            raise GenieError("SIGNUP_FAILED", raw[:300].decode("utf-8", "replace"), st)
        j = json.loads(raw)
        return Account(
            uid=j["localId"],
            id_token=j["idToken"],
            refresh_token=j["refreshToken"],
            expires_at=time.time() + int(j.get("expiresIn", 3600)) - 60,
            day=_today(),
        )

    def _refresh(self, acc: Account) -> bool:
        st, raw, _ = _post(
            f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_KEY}",
            {"grant_type": "refresh_token", "refresh_token": acc.refresh_token},
        )
        if st != 200:
            return False
        j = json.loads(raw)
        acc.id_token = j["id_token"]
        acc.refresh_token = j.get("refresh_token", acc.refresh_token)
        acc.expires_at = time.time() + int(j.get("expires_in", 3600)) - 60
        return True

    def _ensure_token(self, acc: Account):
        if acc.expires_at > time.time():
            return
        if not self._refresh(acc):
            raise GenieError("TOKEN_REFRESH_FAILED", acc.uid, 401)

    def acquire(self, wait: float = 60.0) -> Account:
        deadline = time.time() + wait
        while True:
            with self.lock:
                for acc in self.accounts:
                    self._roll_day(acc)
                    if acc.usable:
                        self._ensure_token(acc)
                        return acc
                try:
                    acc = self._signup()
                except GenieError:
                    acc = None
                if acc:
                    self.accounts.append(acc)
                    self.save()
                    return acc
            if time.time() > deadline:
                raise GenieError("POOL_EXHAUSTED", "no account with remaining quota")
            time.sleep(1.0)

    def charge(self, acc: Account, soft_limited: bool = False):
        with self.lock:
            acc.used += 1
            if soft_limited:
                acc.soft_limited = True
            self.save()

    def stats(self) -> dict:
        with self.lock:
            today = _today()
            live = [a for a in self.accounts if a.day == today]
            return {
                "accounts": len(self.accounts),
                "today_accounts": len(live),
                "remaining_quota": sum(max(0, self.quota - a.used) for a in live if not a.soft_limited),
                "used_today": sum(a.used for a in live),
                "soft_limited": sum(1 for a in live if a.soft_limited),
            }


# --------------------------------------------------------------------------- 客户端

@dataclass
class Media:
    url: str = ""
    blurhash: str = ""


@dataclass
class ChatResult:
    text: str
    model: str
    media: list[Media] = field(default_factory=list)
    service: str = "text2text"
    events: list[str] = field(default_factory=list)
    agent_steps: list[dict] = field(default_factory=list)
    soft_limited: bool = False
    pro_required: bool = False
    moderated: bool = False
    latency: float = 0.0


def _parse_sse_block(block: str) -> tuple[Optional[str], Optional[dict]]:
    event, data = None, None
    for line in block.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {"_raw": payload}
    return event, data


class GenieClient:
    def __init__(self, pool: Optional[AccountPool] = None, timeout: float = 120):
        self.pool = pool or AccountPool()
        self.timeout = timeout

    def _stream(self, acc: Account, body: dict, action: Optional[str] = None,
                on_delta: Optional[Callable[[str], None]] = None,
                timeout: Optional[float] = None) -> ChatResult:
        headers = dict(APP_HEADERS)
        headers["Authorization"] = f"Bearer {acc.id_token}"
        headers["Accept"] = "text/event-stream"

        url = f"{CHAT_BASE}/v2/chats/local/completions"
        if action:
            url += f"?type={action}"

        t0 = time.time()
        segments: list[str] = [""]      # agent 通道会在 tool_calling / tool_done 前后各推一遍文案
        events: list[str] = []
        steps: list[dict] = []
        media: list[Media] = []
        state = {"model": "", "service": "text2text", "done": False}
        try:
            resp = requests.post(url, json=body, headers=headers, stream=True,
                                 timeout=(10, timeout or self.timeout))
        except requests.RequestException as e:
            raise GenieError("UPSTREAM_NETWORK", str(e))

        with resp:
            if resp.status_code != 200:
                raw = resp.content.decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"_raw": raw}
                err = payload.get("error") or {}
                code = err.get("code") if isinstance(err, dict) else None
                if resp.status_code == 402 or code == "NEEDS_PRO":
                    raise GenieError("NEEDS_PRO", err.get("message", "pro required"), resp.status_code, payload)
                if resp.status_code == 401:
                    raise GenieError("UNAUTHORIZED", "token rejected", resp.status_code, payload)
                raise GenieError("UPSTREAM_HTTP", raw[:300], resp.status_code, payload)

            buf: list[str] = []

            def flush():
                if not buf:
                    return
                ev, d = _parse_sse_block("\n".join(buf))
                buf.clear()
                if ev:
                    events.append(ev)
                if not d:
                    return
                if "model" in d and d["model"]:
                    state["model"] = d["model"]
                if v := d.get("service"):
                    state["service"] = v
                if ev == "agent-status" and "status" in d:
                    steps.append(d)
                    if d.get("status") == "tool_done":
                        segments.append("")
                if "content" in d:
                    segments[-1] += d["content"]
                    if on_delta:
                        on_delta(d["content"])
                if isinstance(d.get("media"), dict) and d["media"].get("url"):
                    media.append(Media(url=d["media"]["url"], blurhash=d["media"].get("blurhash", "")))
                if ev == "completion-done":
                    state["done"] = True

            for raw_line in resp.iter_lines(decode_unicode=False):
                line = raw_line.decode("utf-8", "replace").rstrip("\r")
                if line == "":
                    flush()
                else:
                    buf.append(line)
            flush()

        text = segments[-1] if len(segments) > 1 and segments[-1].strip() else "".join(segments)
        pro_required = any(m in text for m in PRO_MARKERS)
        soft_limited = (not pro_required) and any(m in text for m in QUOTA_SOFT_MARKERS)
        moderated = (not soft_limited and not pro_required) and any(
            m in text.lower() for m in MODERATION_MARKERS)
        return ChatResult(
            text=text,
            model=state["model"] or body.get("model", ""),
            media=media,
            service=state["service"],
            events=events,
            agent_steps=steps,
            soft_limited=soft_limited,
            pro_required=pro_required,
            moderated=moderated,
            latency=round(time.time() - t0, 3),
        )

    def chat(self, model: str, messages: Iterable[dict], chat_id: Optional[str] = None,
             regenerated_message_id: Optional[str] = None,
             action: Optional[str] = None,
             on_delta: Optional[Callable[[str], None]] = None,
             parameters: Optional[dict] = None) -> ChatResult:
        resolved = MODEL_ALIASES.get(model, model)
        if resolved in PRO_MODELS:
            raise GenieError("NEEDS_PRO", f"{resolved} requires an active Pro subscription", 402,
                             {"success": False, "error": {"code": "NEEDS_PRO", "message": "Pro required"}})

        body = {"model": resolved, "messages": list(messages)}
        if parameters:
            body["parameters"] = parameters
        if chat_id:
            body["chatId"] = chat_id
        if regenerated_message_id:
            body["regeneratedMessageId"] = regenerated_message_id

        for attempt in range(2):
            acc = self.pool.acquire()
            try:
                result = self._stream(acc, body, action=action, on_delta=on_delta)
            except GenieError as e:
                if e.code == "UNAUTHORIZED" and attempt == 0:
                    acc.expires_at = 0
                    self.pool._refresh(acc)
                    continue
                raise
            self.pool.charge(acc, soft_limited=result.soft_limited)
            if result.soft_limited and attempt == 0:
                continue
            if result.pro_required:
                raise GenieError("NEEDS_PRO", result.text or "Pro subscription required", 402,
                                 {"service": result.service, "steps": result.agent_steps})
            return result
        raise GenieError("RATE_LIMITED", "all anonymous accounts hit the daily quota", 429)

    def chat_text(self, model: str, prompt: str, **kwargs) -> str:
        return self.chat(model, [{"type": "prompt", "content": prompt, "fromUser": True}], **kwargs).text

    def generate_image(self, prompt: str, model: str = "gpt-4o-mini", **kwargs) -> ChatResult:
        """生图：action=image_generation -> 上游 agent 调 text2image 工具。

        免费账号会在工具执行前被 Pro 门禁拦下，客户端抛 NEEDS_PRO（带 agent 步骤）。
        """
        return self.chat(model, [{"type": "prompt", "content": prompt, "fromUser": True}],
                         action="image_generation", **kwargs)

    def web_search(self, prompt: str, model: str = "gpt-4o-mini", **kwargs) -> ChatResult:
        return self.chat(model, [{"type": "prompt", "content": prompt, "fromUser": True}],
                         action="web_search", **kwargs)

    def system_assistants(self) -> list:
        acc = self.pool.acquire()
        self.pool._ensure_token(acc)
        headers = dict(APP_HEADERS)
        headers["Authorization"] = f"Bearer {acc.id_token}"
        st, raw, _ = _post(f"{CHAT_BASE}/v2/assistants/system", None, headers)
        if st != 200:
            raise GenieError("ASSISTANTS_FAILED", raw[:200].decode("utf-8", "replace"), st)
        return json.loads(raw)


def openai_messages_to_genie(messages: list[dict]) -> list[dict]:
    """OpenAI 消息 -> Genie 的 {type, content, fromUser}。

    - system 角色没有独立通道（上游 parameters.systemPrompt 被忽略），由网关把它当首条 prompt。
    - 图片：上游不认 OpenAI 的 image_url 结构，但会把 content 文本里的图片直链自己抓下来做
      视觉理解（实测可读出图内文字与版面），所以这里把 image_url 还原成一行 URL。
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):
            texts, urls = [], []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" or "text" in part:
                    texts.append(part.get("text") or "")
                elif part.get("type") in ("image_url", "image"):
                    img = part.get("image_url") or part.get("image") or {}
                    url = img.get("url") if isinstance(img, dict) else img
                    if url:
                        urls.append(url)
            # 图片直链必须排在文本前面：上游按首行抓图，URL 放在问题后面时不会被识别
            content = "\n".join([u for u in urls if u] + [t for t in texts if t])
        if role == "assistant":
            out.append({"type": "response", "content": content or "", "fromUser": False})
        else:
            out.append({"type": "prompt", "content": content or "", "fromUser": True})
    return out


if __name__ == "__main__":
    c = GenieClient()
    print(c.chat_text("gpt-4o-mini", "用一句话说明你是谁"))
    print(c.pool.stats())