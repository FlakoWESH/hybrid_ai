"""核心路由与降级编排：语库 / 青云客 / LLM 三层混合。

路由优先级（从高到低）：
1. 复读 / 学舌检测（message_manager）
2. 打招呼语库（空文本 + 问候词）
3. 图片消息 → 多模态 LLM
4. 短消息（< 阈值）：
   - 含疑问词 → 直接 LLM
   - 否则 anime 语库（概率）→ 青云客（概率 × 限流）→ LLM
5. 长消息 → LLM
任何分支失败都有最终降级，最终兜底为 no_result() 语库。

本模块只产出结果（UniMessage / 文本），不关心消息收发与群聊限制，
群聊限制在 __init__.py 的 matcher rule 中完成。
"""

import os
import random

from nonebot_plugin_alconna.uniseg import Image
from zhenxun.configs.config import BotConfig
from zhenxun.configs.path_config import IMAGE_PATH
from zhenxun.services.ai.llm import LLMException, chat
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils

from .anime_store import anime_store
from .config import get_config
from .content_filter import apply_global_text_filter
from .qyk_client import qyk_client
from .message_manager import ai_message_manager
from .persona import build_persona

# 打招呼触发词（与官方 ai 一致）
GREETING_WORDS = (
    "你好啊",
    "你好",
    "在吗",
    "在不在",
    "您好",
    "您好啊",
    "在",
)

# 短消息中的疑问词：命中即视为真正提问，直接走 LLM，不进入 anime/青云客
QUESTION_WORDS = (
    "吗", "呢", "怎么", "什么", "为什么", "如何", "为何",
    "哪", "谁", "几", "多少", "咋", "是不是", "？", "?",
)

_HELLO_TEXTS = (
    "哦豁？！",
    "你好！Ov<",
    f"库库库，呼唤{BotConfig.self_nickname}做什么呢",
    "我在呢！",
    "呼呼，叫俺干嘛",
)

_NO_RESULT_TEXTS = (
    "你在说啥子？",
    f"纯洁的{BotConfig.self_nickname}没听懂",
    "下次再告诉你(下次一定)",
    "你觉得我听懂了吗？嗯？",
    "我！不！知！道！",
)


def _random_image(subdir: str):
    """安全读取 IMAGE_PATH 下某目录中的随机图片。

    资源目录缺失或为空时返回 None，避免 os.listdir 抛异常导致插件崩溃。
    """
    directory = IMAGE_PATH / subdir
    try:
        files = [f for f in os.listdir(directory) if f.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        )]
        if not files:
            return None
        return directory / random.choice(files)
    except Exception:
        return None


def hello():
    """打招呼语库：随机文案 + 随机"在"图片。"""
    text = random.choice(_HELLO_TEXTS)
    img = _random_image("zai")
    if img:
        return MessageUtils.build_message([img, text])
    return MessageUtils.build_message(text)


def no_result():
    """无回复语库：随机文案 + 随机"noresult"图片。"""
    text = random.choice(_NO_RESULT_TEXTS)
    img = _random_image("noresult")
    if img:
        return MessageUtils.build_message([text, img])
    return MessageUtils.build_message(text)


def _build_text(text: str):
    """动态文本的统一出口：先过全局 TEXT_FILTER，再构建消息。

    固定语库 hello()/no_result() 不含敏感词，不走此函数。
    """
    filtered = apply_global_text_filter(text, get_config("TEXT_FILTER", []) or [])
    return MessageUtils.build_message(filtered)


def contains_question(text: str) -> bool:
    """短消息是否包含疑问词（命中则直接交给 LLM）。"""
    return any(word in text for word in QUESTION_WORDS)


async def llm_chat(message, text: str, bot, event) -> str | None:
    """调用 LLM（固定帕秋莉×AnuNeko 人设）。

    模型配置联动：HYBRID_AI_CHAT_MODEL 留空（None）时，不传 model 参数，
    zhenxun.services.ai.llm.chat 会自动使用全局默认 chat 模型
    （get_default_model("chat")），与 bym_ai 共用同一套 AI 配置。

    参数:
        message: 原始 UniMessage（可能含图片）
        text: 纯文本
        bot / event: 用于多模态消息转换

    返回:
        LLM 回复文本；调用失败时返回 None
    """
    model = get_config("HYBRID_AI_CHAT_MODEL")
    persona = build_persona(BotConfig.self_nickname)
    timeout = get_config("LLM_TIMEOUT", 30)
    has_image = message.has(Image)

    # model 为 falsy 时不传 model，让 zhenxun 用全局默认模型
    chat_kwargs = {"instruction": persona, "timeout": timeout}
    if model:
        chat_kwargs["model"] = model

    try:
        if has_image:
            # 多模态：需要 MessageBuilder 下载并转换图片
            from zhenxun.services.ai.message_builder import MessageBuilder

            llm_input = await MessageBuilder.normalize_to_llm_messages(
                message, bot=bot, event=event
            )
            response = await chat(llm_input, **chat_kwargs)
        else:
            response = await chat(text, **chat_kwargs)
    except LLMException as e:
        logger.warning(f"LLM 调用异常：{e}", "hybrid_ai")
        return None
    except Exception as e:
        logger.error("LLM 调用发生未知错误", "hybrid_ai", e=e)
        return None

    reply = str(response.text).strip() if response else ""
    return reply or None


async def _qyk_fallback(text: str, nickname: str) -> str | None:
    """降级用青云客（受黑名单、限流、清洗约束），不做概率门控。"""
    return await qyk_client.chat(
        text,
        bot_nickname=BotConfig.self_nickname,
        user_nickname=nickname,
    )


async def llm_then_qyk(message, text: str, bot, event, nickname: str) -> str | None:
    """先 LLM，失败/未配置则降级青云客，再失败返回 None。"""
    reply = await llm_chat(message, text, bot, event)
    if reply:
        return reply
    return await _qyk_fallback(text, nickname)


def _record_result(user_id: str, result: str) -> None:
    """统一记录机器人回复，供学舌检测使用（hello/no_result 不记录）。"""
    if result:
        ai_message_manager.add_result(user_id, result)


async def route_message(
    *,
    text: str,
    has_image: bool,
    message,
    bot,
    event,
    user_id: str,
    nickname: str,
    group_id: str,
):
    """主路由：返回最终可发送的 UniMessage。

    仅在群聊中被调用（群聊限制由 __init__ 的 rule 保证）。
    """
    # 记录用户消息（用于复读检测，空文本不记录）
    ai_message_manager.add_message(user_id, text)

    # 1. 复读 / 学舌检测（最高优先级：反刷屏）
    special = await ai_message_manager.get_result(
        user_id,
        nickname,
        group_id=group_id,
        ban_enabled=get_config("REPEAT_BAN_ENABLED", True),
        ban_min=get_config("REPEAT_BAN_MIN", 60),
        ban_max=get_config("REPEAT_BAN_MAX", 300),
    )
    if special:
        _record_result(user_id, special)
        return _build_text(special)

    # 2. 打招呼语库（空文本 + 问候词；有图片时不视为打招呼）
    if not has_image:
        if not text or text in GREETING_WORDS:
            return hello()

    # 3. 图片消息 → 多模态 LLM（青云客不支持图片，不降级）
    if has_image:
        reply = await llm_chat(message, text, bot, event)
        if reply:
            _record_result(user_id, reply)
            return _build_text(reply)
        return no_result()

    threshold = get_config("SHORT_MSG_THRESHOLD", 6)

    # 4. 短消息分支
    if len(text) < threshold:
        # 含疑问词的短消息直接走 LLM（"人生意义"、"猫呢"等）
        if not contains_question(text):
            # 4.1 anime 语库概率门控
            anime_prob = get_config("ANIME_PROBABILITY", 0.7)
            if random.random() < anime_prob:
                anime_reply = anime_store.match(text, nickname)
                if anime_reply:
                    _record_result(user_id, anime_reply)
                    return _build_text(anime_reply)

            # 4.2 青云客概率门控（内部再受黑名单 + 限流约束）
            qyk_prob = get_config("QYK_PROBABILITY", 0.3)
            if random.random() < qyk_prob:
                qyk_reply = await qyk_client.chat(
                    text,
                    bot_nickname=BotConfig.self_nickname,
                    user_nickname=nickname,
                )
                if qyk_reply:
                    _record_result(user_id, qyk_reply)
                    return _build_text(qyk_reply)

    # 5. 长消息 / 短消息全部未命中 → LLM（失败降级青云客）
    reply = await llm_then_qyk(message, text, bot, event, nickname)
    if reply:
        _record_result(user_id, reply)
        return _build_text(reply)

    return no_result()
