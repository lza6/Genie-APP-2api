#!/usr/bin/env python3
"""Genie (co.appnation.geniechat v7.9.2) 上游协议客户端 + 账号池。

协议事实(静态还原 + 实测):
  Firebase 项目 chatgpt-a61db, apiKey 硬编码于 resources.arsc。
  匿名注册即可用: POST identitytoolkit accounts:signUp  -> idToken / refreshToken
  聊天: POST {GENIE}/v2/chats/local/completions
        header Authorization: Bearer <firebase idToken>
               X-App-Platform: android / X-App-Version / X-App-Id: 1658377526
        body   {"model":"gpt-4o-mini","messages":[{"type":"prompt"|"response","content","fromUser"}]}
        响应   text/event-stream:
               event: completion-started  data:{"model","service"}
               event: completion          data:{"content":"片段"}
               event: completion-done     data:{"reason":"stop"}

  已实测的服务端行为:
    - 每账号每天 7 次免费额度, 第 8 次返回 HTTP 200 + {"error":{"key":"RATE_LIMIT_EXCEEDED"}}
    - 额度按 uid 计, 与 IP / 设备无关 -> 号池可横向扩展
    - parameters.{systemPrompt,maxTokens,temperature} 被服务端忽略
    - messages 里的 type 只有 "prompt"(用户) 与 "response"(助手) 会被采纳
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

FIREBASE_API_KEY = "AIzaSyBkanDK4DjniXPjw7TWA1dp9bUeOyHov6c"
FIREBASE_PROJECT = "chatgpt-a61db"
GENIE_BASE = os.environ.get(
    "GENIE_BASE", "https://genie-production-yfvxbm4e6q-uc.a.run.app"
)
CHAT_PATH = "/v2/chats/local/completions"

APP_ID = "1658377526"
APP_PACKAGE = "co.appnation.geniechat"
APP_VERSION = "7.9.2"

UPSTREAM_MODEL = "gpt-4o-mini"
DAILY_QUOTA = int(os.environ.get("GENIE_DAILY_QUOTA", "7"))
REQUEST_TIMEOUT = int(os.environ.get("GENIE_TIMEOUT", "120"))


class UpstreamQuotaExhausted(RuntimeError):
    """当前账号已触发日限额, 调用方应换号重试。"""


class UpstreamProtocolError(RuntimeError):
    """上游返回了无法识别的载荷。"""


def register_anonymous_account() -> dict:
    """用 Firebase 匿名登录发一个新号(无需邮箱/验证)。"""
    payload = json.dumps(
        {"returnSecureToken": True, "clientType": "CLIENT_TYPE_ANDROID"}
    ).encode()
    request = urllib.request.Request(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_API_KEY}",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Android-Package": APP_PACKAGE,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read())
    return {
        "uid": data["localId"],
        "refresh_token": data["refreshToken"],
        "id_token": data["idToken"],
        "token_expire_at": time.time() + int(data["expiresIn"]) - 60,
        "day_key": utc_day_key(),
        "used": 0,
        "exhausted": False,
    }


def refresh_id_token(refresh_token: str) -> tuple[str, int]:
    """用 refreshToken 换新的 idToken(1 小时有效)。"""
    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": refresh_token}
    ).encode()
    request = urllib.request.Request(
        f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}", data=body
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read())
    return data["id_token"], int(data["expires_in"])


def utc_day_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class AccountPool:
    """按 uid 记账的号池。额度用尽或过期时自动补号。"""

    def __init__(self, path: str = "accounts.json", max_accounts: int = 500):
        self.path = path
        self.max_accounts = max_accounts
        self._lock = threading.Lock()
        self.accounts: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self) -> None:
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.accounts, handle, indent=2)
        os.replace(tmp, self.path)

    def _roll_day(self, account: dict) -> None:
        today = utc_day_key()
        if account.get("day_key") != today:
            account["day_key"] = today
            account["used"] = 0
            account["exhausted"] = False

    def _usable(self, account: dict) -> bool:
        self._roll_day(account)
        return not account["exhausted"] and account["used"] < DAILY_QUOTA

    def acquire(self) -> dict:
        """取一个可用账号; 没有就现注册一个。"""
        with self._lock:
            for account in self.accounts:
                if self._usable(account):
                    return account
            if len(self.accounts) >= self.max_accounts:
                raise UpstreamQuotaExhausted(
                    f"号池已满({self.max_accounts}) 且全部耗尽, 请调大 GENIE_MAX_ACCOUNTS 或等待 UTC 日切"
                )
            account = register_anonymous_account()
            self.accounts.append(account)
            self._save()
            return account

    def mark_used(self, account: dict) -> None:
        with self._lock:
            account["used"] += 1
            if account["used"] >= DAILY_QUOTA:
                account["exhausted"] = True
            self._save()

    def mark_exhausted(self, account: dict) -> None:
        with self._lock:
            account["exhausted"] = True
            self._save()

    def token_for(self, account: dict) -> str:
        if account.get("id_token") and account.get("token_expire_at", 0) > time.time():
            return account["id_token"]
        id_token, expires_in = refresh_id_token(account["refresh_token"])
        with self._lock:
            account["id_token"] = id_token
            account["token_expire_at"] = time.time() + expires_in - 60
            self._save()
        return id_token

    def stats(self) -> dict:
        with self._lock:
            for account in self.accounts:
                self._roll_day(account)
            alive = sum(1 for a in self.accounts if self._usable(a))
            return {
                "accounts": len(self.accounts),
                "usable": alive,
                "used_today": sum(a["used"] for a in self.accounts),
                "capacity_left": max(0, alive * DAILY_QUOTA - sum(
                    a["used"] for a in self.accounts if not a["exhausted"]
                )),
                "daily_quota_per_account": DAILY_QUOTA,
                "day_key": utc_day_key(),
            }


def to_upstream_messages(messages: list[dict]) -> list[dict]:
    """OpenAI messages -> Genie messages。

    上游忽略 system, 因此把 system 文本前置拼进第一条用户消息, 保证指令不丢。
    上游只采纳 type=prompt(用户) / type=response(助手), 其余角色按用户处理。
    """
    system_text = "\n".join(
        (message.get("content") or "").strip()
        for message in messages
        if message.get("role") == "system"
    ).strip()

    converted: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        content = (content or "").strip()
        if not content or role == "system":
            continue
        is_assistant = role == "assistant"
        converted.append(
            {
                "type": "response" if is_assistant else "prompt",
                "content": content,
                "fromUser": not is_assistant,
            }
        )

    if system_text:
        if converted and converted[0]["type"] == "prompt":
            converted[0]["content"] = f"{system_text}\n\n{converted[0]['content']}"
        else:
            converted.insert(0, {"type": "prompt", "content": system_text, "fromUser": True})
    return converted


class GenieClient:
    def __init__(self, pool: AccountPool):
        self.pool = pool

    def _post(self, id_token: str, messages: list[dict]) -> urllib.response.addinfourl:
        body = json.dumps(
            {"model": UPSTREAM_MODEL, "messages": messages, "regeneratedMessageId": None}
        ).encode()
        request = urllib.request.Request(
            f"{GENIE_BASE}{CHAT_PATH}",
            data=body,
            headers={
                "Authorization": f"Bearer {id_token}",
                "X-App-Platform": "android",
                "X-App-Version": APP_VERSION,
                "X-App-Id": APP_ID,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": "Dart/3.5 (dart:io)",
                "X-Request-Id": uuid.uuid4().hex,
            },
        )
        return urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT)

    def stream(self, messages: list[dict], max_account_switches: int = 5):
        """产出上游 SSE 里累积的文本片段; 自动换号重试。"""
        upstream_messages = to_upstream_messages(messages)
        if not upstream_messages:
            raise ValueError("messages 为空, 至少需要一条非 system 文本消息")

        last_error: Exception | None = None
        for _ in range(max_account_switches):
            account = self.pool.acquire()
            id_token = self.pool.token_for(account)
            try:
                with self._post(id_token, upstream_messages) as response:
                    raw = response.read()
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:300]
                last_error = UpstreamProtocolError(f"HTTP {error.code}: {detail}")
                continue
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = UpstreamProtocolError(f"网络错误: {error}")
                continue

            text = raw.decode("utf-8", "replace")
            if '"RATE_LIMIT_EXCEEDED"' in text:
                self.pool.mark_exhausted(account)
                last_error = UpstreamQuotaExhausted("账号额度耗尽")
                continue
            if '"error"' in text and "completion-started" not in text:
                raise UpstreamProtocolError(text[:300])

            self.pool.mark_used(account)
            for chunk in self._parse_sse(text):
                yield chunk
            return
        raise UpstreamQuotaExhausted(f"连续 {max_account_switches} 个账号失败: {last_error}")

    @staticmethod
    def _parse_sse(text: str):
        for line in text.split("\n"):
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("content"):
                yield event["content"]

    def complete(self, messages: list[dict]) -> str:
        return "".join(self.stream(messages))
