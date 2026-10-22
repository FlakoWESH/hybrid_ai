"""复读 / 学舌检测（移植自官方 ai 插件的 AiMessageManager）。

保留两类行为：
- 重复消息：用户连续发同一句话超过 2 次，触发吐槽；超过 3 次有概率禁言。
- 学舌消息：用户复读机器人的回复，触发警告；超过 3 次有概率禁言。

与官方版本的差异：
1. 适配新版 zhenxun（BanConsole 替代 BanUser）。
2. 禁言仅在群聊生效（group_id 非空时）；私聊只返回吐槽文本，不调用禁言。
3. 禁言时长、开关、概率通过参数传入，不硬编码，便于配置化。
4. 消息窗口为内存态，10 分钟过期（与官方一致），不参与 LLM 上下文。
"""

import random
import time

from zhenxun.configs.config import BotConfig
from zhenxun.models.ban_console import BanConsole
from zhenxun.services.log import logger

# 消息/结果的有效窗口（秒）
_MESSAGE_TTL = 10 * 60


class AiMessageManager:
    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        nickname = BotConfig.self_nickname
        self._same_message = [
            "为什么要发一样的话？",
            "请不要再重复对我说一句话了，不然我就要生气了！",
            "别再发这句话了，我已经知道了...",
            "你是只会说这一句话吗？",
            "[*]，你发我也发！",
            "[uname]，[*]",
            f"救命！有笨蛋一直给{nickname}发一样的话！",
            "这句话你已经给我发了{}次了，再发就生气！",
        ]
        self._repeat_message = [
            f"请不要学{nickname}说话",
            f"为什么要一直学{nickname}说话？",
            "你再学！你再学我就生气了！",
            f"呜呜，你是想欺负{nickname}嘛..",
            "[uname]不要再学我说话了！",
            "再学我说话，我就把你拉进黑名单（生气",
            "你再学！[uname]是个笨蛋！",
            "你已经学我说话{}次了！别再学了！",
        ]

    def add_message(self, user_id: str, message: str) -> None:
        """记录一条用户消息。"""
        if not message:
            return
        record = self._data.setdefault(
            user_id,
            {"time": time.time(), "message": [], "result": [], "repeat_count": 0},
        )
        if time.time() - record["time"] > _MESSAGE_TTL:
            record["message"].clear()
        record["time"] = time.time()
        record["message"].append(message.strip())

    def add_result(self, user_id: str, message: str) -> None:
        """记录一条机器人回复（用于学舌检测）。"""
        if not message:
            return
        record = self._data.setdefault(
            user_id,
            {"time": time.time(), "message": [], "result": [], "repeat_count": 0},
        )
        if time.time() - record["time"] > _MESSAGE_TTL:
            record["result"].clear()
            record["repeat_count"] = 0
        record["time"] = time.time()
        record["result"].append(message.strip())

    async def get_result(
        self,
        user_id: str,
        nickname: str,
        *,
        group_id: str | None = None,
        ban_enabled: bool = True,
        ban_min: int = 60,
        ban_max: int = 300,
    ) -> str | None:
        """检测复读 / 学舌，返回特殊回复文本，未命中返回 None。

        参数:
            user_id: 用户 id
            nickname: 用户昵称（用于替换 [uname]）
            group_id: 群组 id；非 None 时才可能触发禁言，私聊传 None
            ban_enabled: 是否允许禁言
            ban_min / ban_max: 禁言时长区间（秒）
        """
        record = self._data.get(user_id)
        if not record or len(record["message"]) < 2:
            return None

        msg = await self._get_user_repeat_message_result(
            user_id, group_id, ban_enabled, ban_min, ban_max
        )
        if not msg:
            msg = await self._get_user_same_message_result(
                user_id, group_id, ban_enabled, ban_min, ban_max
            )
        if msg:
            if "[uname]" in msg:
                msg = msg.replace("[uname]", nickname)
            if not msg.startswith("生气了！你好烦，闭嘴！") and "[*]" in msg:
                msg = msg.replace("[*]", record["message"][-1])
        return msg

    async def _maybe_ban(
        self,
        user_id: str,
        group_id: str | None,
        ban_enabled: bool,
        ban_min: int,
        ban_max: int,
    ) -> tuple[bool, int]:
        """复读/学舌超过 3 次时，群聊中按 50% 概率禁言。

        返回:
            (是否触发禁言, 禁言秒数)
        """
        if not ban_enabled or not group_id:
            return False, 0
        if random.random() < 0.5:
            duration = random.randint(ban_min, ban_max)
            try:
                # BanConsole.ban duration 单位为秒（ban_time + duration 直接比对时间戳）
                await BanConsole.ban(user_id, group_id, 9, "复读刷屏", duration, None)
                return True, duration
            except Exception as e:
                logger.error("复读禁言失败", "hybrid_ai", e=e)
                return False, 0
        return False, 0

    async def _get_user_same_message_result(
        self,
        user_id: str,
        group_id: str | None,
        ban_enabled: bool,
        ban_min: int,
        ban_max: int,
    ) -> str | None:
        """重复发送同一句话检测。"""
        record = self._data[user_id]
        msg = record["message"][-1]
        cnt = 0
        for s in reversed(record["message"][:-1]):
            if s == msg:
                cnt += 1
            else:
                break
        if cnt <= 1:
            return None

        if cnt > 3:
            banned, duration = await self._maybe_ban(
                user_id, group_id, ban_enabled, ban_min, ban_max
            )
            if banned:
                record["message"].clear()
                return f"生气了！你好烦，闭嘴！给我老实安静{duration}秒"
        return random.choice(self._same_message).format(cnt)

    async def _get_user_repeat_message_result(
        self,
        user_id: str,
        group_id: str | None,
        ban_enabled: bool,
        ban_min: int,
        ban_max: int,
    ) -> str | None:
        """学舌机器人回复检测。"""
        record = self._data[user_id]
        msg = record["message"][-1]
        if not record["result"]:
            return None
        rst = record["result"][-1]
        if msg != rst:
            return None

        record["repeat_count"] += 1
        cnt = record["repeat_count"]
        if cnt <= 1:
            return None

        if cnt > 3:
            banned, duration = await self._maybe_ban(
                user_id, group_id, ban_enabled, ban_min, ban_max
            )
            if banned:
                record["result"].clear()
                record["repeat_count"] = 0
                return f"生气了！你好烦，闭嘴！给我老实安静{duration}秒"
        return random.choice(self._repeat_message).format(cnt)


ai_message_manager = AiMessageManager()
