"""配置访问入口。

所有配置项在 __init__.py 的 PluginExtraData 中注册，
这里只做统一的读取封装，避免各处重复 Config.get("hybrid_ai")。

特别说明：HYBRID_AI_CHAT_MODEL 默认值为 None，表示"使用 zhenxun 全局
默认 chat 模型"（通过 zhenxun.services.ai.llm.chat(model=None) 联动），
而非禁用 LLM。如需禁用 LLM，在后台将其设为空字符串以外的无效值，或
直接在路由中移除 LLM 分支。
"""

from zhenxun.configs.config import Config

# 模块级配置对象，插件内统一从此读取
base_config = Config.get("hybrid_ai")

# 与 __init__.py 中 RegisterConfig 的 value/default_value 保持一致。
# 存在的意义：zhenxun 的 init_config 在完整启动时才会把插件配置写入
# ConfigsManager；若配置文件缺失或 init_config 未运行，Config.get()
# 会返回空 ConfigGroup，此时需要本地默认值兜底，避免各处拿到 None。
_DEFAULTS = {
    "HYBRID_AI_CHAT_MODEL": None,  # None = 联动 zhenxun 全局默认 chat 模型
    "SHORT_MSG_THRESHOLD": 6,
    "ANIME_PROBABILITY": 0.7,
    "QYK_PROBABILITY": 0.3,
    "QYK_RATE_LIMIT": 100,
    "QYK_WINDOW_SECONDS": 600,
    "QYK_TIMEOUT": 8,
    "LLM_TIMEOUT": 30,
    "QYK_BLOCK_KEYWORDS": [
        "笑话", "天气", "翻译", "歌词", "成语",
        "归属", "计算", "五笔", "拼音", "ip", "IP",
    ],
    "QYK_SWEAR_FILTER": [
        "滚", "去死", "笨蛋", "废物", "色", "屁", "猪", "垃圾",
    ],
    "TEXT_FILTER": ["鸡", "口交"],
    "REPEAT_BAN_ENABLED": True,
    "REPEAT_BAN_MIN": 60,
    "REPEAT_BAN_MAX": 300,
}


def get_config(key: str, default=None):
    """读取 hybrid_ai 配置项。

    优先级：显式传入的 default > zhenxun 配置文件中的值 > 本地 _DEFAULTS。

    封装一层是为了在配置尚未生成 / 类型异常时兜底，
    避免 None.get 之类的错误在运行时炸掉整个处理器。
    """
    try:
        val = base_config.get(key, default=None)
    except Exception:
        val = None
    if val is not None:
        return val
    # 配置文件中无此项（或值为 None），用本地默认值兜底
    if default is not None:
        return default
    return _DEFAULTS.get(key)
