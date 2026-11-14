"""
模型客户端模块。
定义了与模型交互的客户端，包括发送请求和处理响应。
如果模型返回错误，会自动重试最多两次。
"""

import os
import random
import time
from dataclasses import dataclass

from anthropic import APIStatusError, Anthropic

from agent.config import BASE_URL, FALLBACK_MODEL


if BASE_URL:
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

client = Anthropic(base_url=BASE_URL)

# 主循环单独管理重试次数，记忆、压缩和子代理仍使用原来的客户端。
lead_client = client.with_options(max_retries=0)


@dataclass
class RecoveryState:
    """记录主代理这一轮使用的模型和连续过载次数。"""

    current_model: str
    consecutive_529: int = 0


def retry_delay(attempt: int) -> float:
    """在随次数翻倍的基础等待时间上，增加最多 25% 的随机量。"""
    base = 2 ** attempt
    return base + random.uniform(0, base * 0.25)


def request_model(*, model: str, recovery: RecoveryState | None = None, **kwargs):
    """发送模型请求，遇到限流或过载时最多再试两次。"""
    if recovery is None:
        recovery = RecoveryState(current_model=model)

    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            response = lead_client.messages.create(model=recovery.current_model, **kwargs)
        except APIStatusError as error:
            status = error.status_code
            if status == 529:
                recovery.consecutive_529 += 1
            else:
                recovery.consecutive_529 = 0

            if status not in (429, 529) or attempt == max_attempts - 1:
                raise

            if (
                recovery.consecutive_529 >= 2
                and FALLBACK_MODEL
                and recovery.current_model != FALLBACK_MODEL
            ):
                # 下一次重试和本轮后续请求都从同一份状态读取模型名。
                recovery.current_model = FALLBACK_MODEL
                recovery.consecutive_529 = 0
                print(f"  [model] Switching to fallback model: {FALLBACK_MODEL}")

            # 错开不同客户端的重试时间，避免它们同时再次请求。
            delay = retry_delay(attempt)
            print(f"  [model] HTTP {status}, retry in {delay:.2f}s")
            time.sleep(delay)
        else:
            recovery.consecutive_529 = 0
            return response
