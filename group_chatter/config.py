"""group_chatter 配置读取入口。

所有配置项在 __init__.py 的 PluginExtraData 中注册，
这里只做统一读取封装和本地默认值兜底。
"""

from zhenxun.configs.config import Config

_MODULE = "group_chatter"

_DEFAULTS = {
    "CHATTER_ENABLE": False,       # 总开关，默认关闭
    "CHATTER_RATE": 0.01,          # 插话概率，0.01 = 平均每 100 条消息插一次
    "IGNORE_USER_IDS": [],         # 忽略的用户 ID（其他 bot 的 QQ 号）
    "IGNORE_KEYWORDS": [],         # 忽略的关键词（命令前缀、功能指令等）
    "CONTEXT_WINDOW": 15,          # 上下文缓冲条数
    "LLM_TIMEOUT": 30,             # LLM 调用超时（秒）
    "CHATTER_MODEL": None,         # LLM 模型，None = 用全局默认
    "JITTER_RATIO": 0.3,           # 触发间隔抖动比例
    "AUTO_IGNORE_COMMANDS": True,  # 自动检测并忽略已注册命令
}


def get_config(key: str, default=None):
    """读取配置，优先 zhenxun config.yml，其次本地默认值。"""
    try:
        val = Config.get(_MODULE).get(key, default=None)
    except Exception:
        val = None
    if val is not None:
        return val
    if default is not None:
        return default
    return _DEFAULTS.get(key)
