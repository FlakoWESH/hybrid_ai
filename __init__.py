"""hybrid_ai —— 语库 + 青云客 + LLM 的轻量缝合 AI 插件（仅群聊生效）。

路由总览：
    复读检测 → 打招呼语库 → 图片走 LLM
    → 短消息[anime 语库 / 青云客 / LLM] → 长消息走 LLM

重要约束：
- 本插件仅在群聊中响应 @ 消息，私聊一律不回应（见 group_only rule）。
- 不要与官方 ai / bym_ai 同时启用，否则会因同 priority 的 to_me 事件冲突。
"""

from pathlib import Path

from nonebot import on_message
from nonebot.adapters import Bot, Event
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_alconna.uniseg import Image
from nonebot_plugin_uninfo import Uninfo
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.depends import UserName

from .anime_store import anime_store
from .data_source import route_message

__plugin_meta__ = PluginMetadata(
    name="hybrid_ai",
    description="语库+青云客+LLM缝合AI（仅群聊，短消息走语库，长消息走大模型）",
    usage=f"在群聊中 @ 机器人 即可触发；私聊不回应。",
    extra=PluginExtraData(
        author="you",
        version="0.1.0",
        ignore_prompt=True,
        menu_type="群聊",
        configs=[
            RegisterConfig(
                key="HYBRID_AI_CHAT_MODEL",
                value=None,
                help="LLM 模型名，留空则使用 zhenxun 全局默认 chat 模型（与 bym_ai 共用同一套 AI 配置）；填写则覆盖为指定模型",
                default_value=None,
            ),
            RegisterConfig(
                key="SHORT_MSG_THRESHOLD",
                value=6,
                help="短消息判定阈值（字符数），小于该值视为短消息",
                default_value=6,
                type=int,
            ),
            RegisterConfig(
                key="ANIME_PROBABILITY",
                value=0.7,
                help="短消息触发 anime 语库匹配的概率(0.0~1.0)",
                default_value=0.7,
                type=float,
            ),
            RegisterConfig(
                key="QYK_PROBABILITY",
                value=0.3,
                help="anime 未命中后走青云客的概率(0.0~1.0)",
                default_value=0.3,
                type=float,
            ),
            RegisterConfig(
                key="QYK_RATE_LIMIT",
                value=100,
                help="青云客每个时间窗口最大调用次数（官方上限200/10分钟，建议保守）",
                default_value=100,
                type=int,
            ),
            RegisterConfig(
                key="QYK_WINDOW_SECONDS",
                value=600,
                help="青云客限流窗口（秒），默认600秒=10分钟",
                default_value=600,
                type=int,
            ),
            RegisterConfig(
                key="QYK_TIMEOUT",
                value=8,
                help="青云客请求超时（秒）",
                default_value=8,
                type=int,
            ),
            RegisterConfig(
                key="LLM_TIMEOUT",
                value=30,
                help="LLM 请求超时（秒）",
                default_value=30,
                type=float,
            ),
            RegisterConfig(
                key="QYK_BLOCK_KEYWORDS",
                value=[
                    "笑话", "天气", "翻译", "歌词", "成语",
                    "归属", "计算", "五笔", "拼音", "ip", "IP",
                ],
                help="不交给青云客处理的关键词（这些功能实测质量差或报错）",
                default_value=[
                    "笑话", "天气", "翻译", "歌词", "成语",
                    "归属", "计算", "五笔", "拼音", "ip", "IP",
                ],
                type=list[str],
            ),
            RegisterConfig(
                key="QYK_SWEAR_FILTER",
                value=["滚", "去死", "笨蛋", "废物", "色", "屁", "猪", "垃圾"],
                help="青云客专属粗口词表，命中替换为*",
                default_value=["滚", "去死", "笨蛋", "废物", "色", "屁", "猪", "垃圾"],
                type=list[str],
            ),
            RegisterConfig(
                key="TEXT_FILTER",
                value=["鸡", "口交"],
                help="全局文本过滤器，将敏感词替换为*",
                default_value=["鸡", "口交"],
                type=list[str],
            ),
            RegisterConfig(
                key="REPEAT_BAN_ENABLED",
                value=True,
                help="复读/学舌超过3次时是否有概率触发禁言（仅群聊）",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                key="REPEAT_BAN_MIN",
                value=60,
                help="复读禁言最短时长（秒）",
                default_value=60,
                type=int,
            ),
            RegisterConfig(
                key="REPEAT_BAN_MAX",
                value=300,
                help="复读禁言最长时长（秒）",
                default_value=300,
                type=int,
            ),
        ],
    ).to_dict(),
)


async def group_only(session: Uninfo) -> bool:
    """群聊限定规则：只有在群组中才放行（私聊 session.group 为 None）。"""
    return session.group is not None


# 仅群聊 + 被 @；block=True 防止与其他同优先级 AI 插件重复响应
hybrid_ai = on_message(rule=to_me() & group_only, priority=998, block=True)


@hybrid_ai.handle()
async def _(
    bot: Bot,
    event: Event,
    message: UniMsg,
    session: Uninfo,
    uname: str = UserName(),
):
    # 双保险：私聊一律不回应
    if session.group is None:
        return

    text = message.extract_plain_text().strip()
    has_image = message.has(Image)

    # 拦截 XML 卡片等结构化消息（与官方 ai 行为一致）
    try:
        raw_str = str(event.get_message())
        if "CQ:xml" in raw_str or "<?xml" in raw_str:
            return
    except Exception:
        pass

    try:
        result = await route_message(
            text=text,
            has_image=has_image,
            message=message,
            bot=bot,
            event=event,
            user_id=session.user.id,
            nickname=uname,
            group_id=session.group.id,
        )
    except Exception as e:
        logger.error("hybrid_ai 处理消息时发生异常", "hybrid_ai", session=session, e=e)
        result = None

    if result is None:
        return

    try:
        await result.send(reply_to=True)
    except Exception as e:
        logger.error("hybrid_ai 发送回复失败", "hybrid_ai", session=session, e=e)


# 首次启动时把插件自带的 anime.json 迁移到 DATA_PATH
anime_store.migrate_bundled(Path(__file__).parent)
