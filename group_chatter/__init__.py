"""group_chatter —— 群聊随机插话插件（独立于 hybrid_ai）。

职责：
- 旁听群聊，按概率随机插一句话（不被 @ 也能主动发言）
- 被 @ 时不回复（交给 hybrid_ai），只把消息计入上下文
- 支持配置忽略用户（其他 bot）和忽略关键词（命令/功能指令），
  这些消息既不触发插话也不计入上下文

与 hybrid_ai 的关系：
- hybrid_ai：to_me() 响应型，被 @ 才回
- group_chatter：全局监听型，随机插话，block=False 不挡其他插件
"""

from nonebot import on_message
from nonebot.adapters import Bot, Event
from nonebot.plugin import PluginMetadata
from nonebot.rule import TrieRule
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.depends import UserName
from zhenxun.utils.message import MessageUtils

from .config import get_config
from .context_buffer import add_message, get_context
from .data_source import generate_chatter, should_chatter

__plugin_meta__ = PluginMetadata(
    name="group_chatter",
    description="群聊随机插话：旁听群聊，按概率主动接话，支持忽略用户/关键词过滤",
    usage="无需指令，在群聊中按配置概率自动插话；被 @ 时由 hybrid_ai 处理。",
    extra=PluginExtraData(
        author="you",
        version="0.1.0",
        ignore_prompt=True,
        menu_type="其他",
        configs=[
            RegisterConfig(
                key="CHATTER_ENABLE",
                value=False,
                help="随机插话总开关，默认关闭",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                key="CHATTER_RATE",
                value=0.01,
                help="插话概率(0.0~1.0)，0.01 表示平均每 100 条群消息插一次",
                default_value=0.01,
                type=float,
            ),
            RegisterConfig(
                key="IGNORE_USER_IDS",
                value=[],
                help="忽略的用户 ID 列表（填其他 bot 的 QQ 号），这些用户的消息不触发插话、不计入上下文",
                default_value=[],
                type=list[str],
            ),
            RegisterConfig(
                key="IGNORE_KEYWORDS",
                value=[],
                help="忽略的关键词列表，消息含任意关键词则不触发插话、不计入上下文（可填命令前缀、功能指令等）",
                default_value=[],
                type=list[str],
            ),
            RegisterConfig(
                key="CONTEXT_WINDOW",
                value=15,
                help="群聊上下文缓冲条数，插话时把最近 N 条消息喂给 LLM",
                default_value=15,
                type=int,
            ),
            RegisterConfig(
                key="LLM_TIMEOUT",
                value=30,
                help="LLM 调用超时（秒）",
                default_value=30,
                type=int,
            ),
            RegisterConfig(
                key="CHATTER_MODEL",
                value=None,
                help="LLM 模型名，留空则使用 zhenxun 全局默认 chat 模型",
                default_value=None,
            ),
            RegisterConfig(
                key="JITTER_RATIO",
                value=0.3,
                help="触发间隔抖动比例，避免插话间隔机械固定",
                default_value=0.3,
                type=float,
            ),
            RegisterConfig(
                key="AUTO_IGNORE_COMMANDS",
                value=True,
                help=(
                    "自动检测并忽略已注册命令（on_command 走命令前缀树，on_alconna 走命令名枚举），"
                    "无需手动填写；on_message 型无命令前缀的被动功能仍需在 IGNORE_KEYWORDS 手动配置"
                ),
                default_value=True,
                type=bool,
            ),
        ],
    ).to_dict(),
)


async def chatter_rule(event: Event, session: Uninfo) -> bool:
    """自定义 rule：只放行群聊中的有效消息。

    过滤条件：
    - 非群聊 → False
    - 发送者在忽略用户列表 → False
    - 消息含任意忽略关键词 → False
    - 自动检测开启时，命中已注册命令（on_command / on_alconna）→ False
    """
    # 仅群聊
    if not session.group:
        return False

    # 忽略用户（统一转字符串比较，兼容配置里填 int 或 str）
    ignore_users = get_config("IGNORE_USER_IDS", []) or []
    user_id = str(session.user.id)
    if user_id in [str(u) for u in ignore_users]:
        return False

    text = event.get_plaintext()

    # 忽略关键词（手动配置）
    ignore_kw = get_config("IGNORE_KEYWORDS", []) or []
    if ignore_kw and any(kw in text for kw in ignore_kw):
        return False

    # 自动检测：命中已注册命令 → 拦截
    # 覆盖两类命令系统：
    #   - on_command：走 nonebot 全局前缀树 TrieRule
    #   - on_alconna：走 alconna command_manager 枚举命令名
    if get_config("AUTO_IGNORE_COMMANDS"):
        stripped = text.lstrip()
        if TrieRule.prefix.longest_prefix(stripped):
            return False
        if _is_alc_command(stripped):
            return False

    return True


# priority=999 排在 hybrid_ai(998) 之后，block=False 不挡其他插件
_matcher = on_message(priority=999, block=False, rule=chatter_rule)

# alconna 命令名缓存（命令在插件加载后基本不变，避免每条消息都枚举）
_alc_cmd_names: list[str] | None = None


def _get_alc_cmd_names() -> list[str]:
    """枚举 alconna 已注册命令名，带缓存。alconna 未安装时返回空列表。"""
    global _alc_cmd_names
    if _alc_cmd_names is not None:
        return _alc_cmd_names
    try:
        from arclet.alconna import command_manager

        _alc_cmd_names = [
            c.command for c in command_manager.get_commands() if c.command
        ]
    except Exception:
        _alc_cmd_names = []
    return _alc_cmd_names


def _is_alc_command(text: str) -> bool:
    """判断消息是否以某个 alconna 命令名开头（含词边界，避免 banana 误命中 ban）。"""
    s = text.lstrip()
    for name in _get_alc_cmd_names():
        if s == name:
            return True
        if s.startswith(name):
            # 命令名后必须是词边界（空白/标点/结束），否则不算
            nxt = s[len(name)]
            if nxt in " \t\n，。！？、,.!?":
                return True
    return False


@_matcher.handle()
async def _(
    bot: Bot,
    event: Event,
    session: Uninfo,
    uname: str = UserName(),
):
    user_msg = UniMessage.of(event.get_message(), bot=bot)
    text = user_msg.extract_plain_text().strip()
    if not text:
        # 纯图片/表情等无文本消息不进上下文
        return

    group_id = str(session.group.id)
    window = int(get_config("CONTEXT_WINDOW", 15))

    # 1. 所有有效消息都计入上下文
    add_message(group_id, uname, text, window)

    # 2. 被 @ 时不回复，交给 hybrid_ai 处理
    if event.is_tome():
        return

    # 3. 没被 @：计数器推进，判断是否插话
    if not should_chatter(group_id):
        return

    # 4. 触发插话，取上下文调 LLM
    context = get_context(group_id)
    reply = await generate_chatter(context)
    if not reply:
        return

    logger.info(
        f"group_chatter 随机插话 | 上下文 {len(context)} 字 | 回复: {reply}",
        "group_chatter",
        session=session,
    )
    await MessageUtils.build_message(reply).send()
