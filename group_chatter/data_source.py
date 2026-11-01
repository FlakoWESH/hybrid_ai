"""插话触发计数器与 LLM 插话生成。

计数器机制仿照 bym_ai：
- 每个群维护 count/target，每来一条有效消息 count+1
- target = 1/rate，加 ±30% 抖动，避免机械间隔
- count 达到 target 就触发插话，然后清零重新抽 target
"""

import random

from zhenxun.services.ai.llm import LLMException, chat
from zhenxun.services.log import logger

from .config import get_config

# group_id -> {"count": int, "target": int}
TRIGGER_STATE: dict[str, dict[str, int]] = {}


def _make_target(rate: float, jitter_ratio: float) -> int:
    """根据概率和抖动比计算下一次插话的目标消息数。"""
    expected = max(1, int(1.0 / rate))
    jitter = max(1, int(expected * jitter_ratio))
    return max(1, random.randint(expected - jitter, expected + jitter))


def should_chatter(group_id: str) -> bool:
    """计数器推进，返回本次是否该插话。

    总开关关闭时直接返回 False，不推进计数。
    """
    if not get_config("CHATTER_ENABLE"):
        return False

    rate = float(get_config("CHATTER_RATE", 0.01))
    if rate <= 0:
        return False

    jitter_ratio = float(get_config("JITTER_RATIO", 0.3))

    if group_id not in TRIGGER_STATE:
        TRIGGER_STATE[group_id] = {
            "count": 0,
            "target": _make_target(rate, jitter_ratio),
        }

    state = TRIGGER_STATE[group_id]
    state["count"] += 1

    if state["count"] >= state["target"]:
        # 触发插话，重置计数器并抽下一次 target
        state["count"] = 0
        state["target"] = _make_target(rate, jitter_ratio)
        return True
    return False


# 插话专用 instruction：让 LLM 知道自己在旁听群聊、偶尔插嘴
_CHATTER_INSTRUCTION = (
    "你正在一个群聊里旁听大家的对话。现在你偶尔插一句话。"
    "要求：\n"
    "1. 回复必须简短自然，像真人在群里随口接话，1-2 句话即可，不要长篇大论。\n"
    "2. 结合上下文内容，接群里正在聊的话题，不要说无关的话。\n"
    "3. 语气可以带点吐槽、调侃或接梗，但不要生硬地自报家门。\n"
    "4. 不要解释你为什么说话，不要说'我来插一句'之类的话，直接说内容。\n"
    "5. 如果上下文没有值得接的话题，可以发一句简短的感叹或吐槽。"
)


async def generate_chatter(context: str) -> str | None:
    """调 LLM 基于群聊上下文生成一句插话。

    参数:
        context: 最近群聊消息纯文本

    返回:
        插话文本；失败返回 None
    """
    model = get_config("CHATTER_MODEL")
    timeout = get_config("LLM_TIMEOUT", 30)

    chat_kwargs = {"instruction": _CHATTER_INSTRUCTION, "timeout": timeout}
    if model:
        chat_kwargs["model"] = model

    # 上下文为空时给一个兜底输入，避免 LLM 收到空消息
    user_text = context if context else "群里很安静，你可以随口说点什么。"

    try:
        response = await chat(user_text, **chat_kwargs)
    except LLMException as e:
        logger.warning(f"group_chatter LLM 调用异常：{e}", "group_chatter")
        return None
    except Exception as e:
        logger.error("group_chatter LLM 未知错误", "group_chatter", e=e)
        return None

    reply = str(response.text).strip() if response else ""
    return reply or None
