"""青云客 API 客户端：限流、超时、黑名单、非 JSON 防护与内容清洗。

官方接口（2026-10 实测仍可用）：
    GET http://api.qingyunke.com/api.php?key=free&appid=0&msg=<urlencode>
    返回 {"result":0,"content":"..."}；频率限制 200 次/10 分钟。

本客户端在其上做了四层保护：
1. 关键词黑名单：笑话/天气/翻译等实测质量差或报错的功能直接不走青云客；
2. 滑动窗口限流：默认 100 次/10 分钟，比官方上限更保守；
3. 请求超时 + 非 JSON 防护：PHP Warning、超时、连接失败一律降级返回 None；
4. 内容清洗：粗口替换、广告判空、{br}/{face} 处理、超长截断、昵称替换。
"""

import json
import random
import time

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .config import get_config
from .content_filter import clean_qyk_content, contains_blocked_keyword
from .rate_limiter import SlidingWindowRateLimiter

_API_URL = "http://api.qingyunke.com/api.php"


class QykClient:
    def __init__(self) -> None:
        self._limiter: SlidingWindowRateLimiter | None = None

    def _get_limiter(self) -> SlidingWindowRateLimiter:
        """根据配置惰性构造限流器（配置可能在启动后才可用）。"""
        max_calls = get_config("QYK_RATE_LIMIT", 100)
        window = get_config("QYK_WINDOW_SECONDS", 600)
        if self._limiter is None:
            self._limiter = SlidingWindowRateLimiter(max_calls, window)
        else:
            self._limiter.update_limits(max_calls, window)
        return self._limiter

    def is_blocked(self, text: str) -> bool:
        """消息是否命中青云客黑名单关键词。"""
        keywords = get_config("QYK_BLOCK_KEYWORDS", [])
        return contains_blocked_keyword(text, keywords or [])

    def can_call(self) -> bool:
        """当前是否还有调用配额（不消耗）。"""
        return self._get_limiter().remaining() > 0

    async def chat(
        self,
        text: str,
        *,
        bot_nickname: str,
        user_nickname: str,
    ) -> str | None:
        """调用青云客并返回清洗后的回复。

        参数:
            text: 用户消息纯文本
            bot_nickname: 机器人自身昵称（替换"菲菲"）
            user_nickname: 发送者昵称（替换"小主人/小朋友"）

        返回:
            清洗后的回复文本；因任意原因不可用时返回 None
        """
        # 1. 黑名单：笑话/天气等不走青云客
        if self.is_blocked(text):
            logger.debug("消息命中青云客黑名单，跳过", "hybrid_ai")
            return None

        # 2. 限流
        limiter = self._get_limiter()
        if not limiter.acquire():
            logger.warning("青云客调用触发限流，降级", "hybrid_ai")
            return None

        # 3. 发请求（超时保护）
        timeout = get_config("QYK_TIMEOUT", 8)
        params = {"key": "free", "appid": 0, "msg": text, "_": int(time.time())}
        try:
            res = await AsyncHttpx.get(_API_URL, params=params, timeout=timeout)
            if res.status_code != 200:
                logger.warning(f"青云客 HTTP {res.status_code}", "hybrid_ai")
                return None
            raw = res.text
        except Exception as e:
            logger.warning(f"青云客请求异常（超时/网络）：{e}", "hybrid_ai")
            return None

        # 4. 非 JSON 防护：服务端可能返回 PHP Warning 前缀
        try:
            data = json.loads(raw)
        except Exception:
            logger.warning("青云客返回非 JSON（疑似服务端报错），降级", "hybrid_ai")
            return None
        if not isinstance(data, dict) or data.get("result") != 0:
            return None

        content = data.get("content") or ""

        # 5. 内容清洗
        swear_words = get_config("QYK_SWEAR_FILTER", []) or []
        cleaned = clean_qyk_content(
            content,
            bot_nickname=bot_nickname,
            swear_words=swear_words,
            max_length=200,
        )
        if not cleaned:
            return None

        # 6. 用户昵称装饰（与官方 ai 行为一致，仅对短昵称、按概率触发）
        cleaned = self._decorate_user_nickname(cleaned, user_nickname)
        return cleaned or None

    @staticmethod
    def _decorate_user_nickname(text: str, nickname: str) -> str:
        """将"小主人/小朋友"替换为用户昵称，并按概率加波浪线装饰。"""
        if not nickname:
            return text
        decorated = nickname
        if len(nickname) < 5 and random.random() < 0.5:
            decorated = "~".join(nickname) + "~"
            if random.random() < 0.2 and "大人" not in decorated:
                decorated += "大~人~"
        return (
            text.replace("小主人", decorated)
            .replace("小朋友", decorated)
        )


qyk_client = QykClient()
