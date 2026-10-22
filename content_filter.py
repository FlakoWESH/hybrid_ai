"""青云客回复内容清洗管道。

青云客免费接口返回的内容存在诸多问题（实测）：
- 自称"菲菲"，需要换成机器人自身昵称；
- 用 {br} 表示换行、{face:数字} 表示表情（无法直接渲染）；
- 部分回复夹带"公众号""淘宝"等广告/引导；
- 部分功能（如"笑话"）返回超长低俗文本；
- 日常语气偶有粗口（"死一边去""哈个屁"等）。

本模块提供纯文本清洗函数，不做任何网络请求，方便单测。
"""

import re

# {face:123} 表情标记（非贪婪匹配，兼容旧版 {face:xxx}）
_FACE_PATTERN = re.compile(r"\{face:.*?\}", re.IGNORECASE)

# 命中以下任意子串的回复直接判空（广告 / 引流）
_BLACKLIST_SUBSTRINGS = ("公众号", "淘宝", "taobao.com")

# 到"提示"为止的截断标记（青云客常在正文后附带"提示：……"）
_CUTOFF_MARKERS = ("提示",)


def clean_qyk_content(
    content: str,
    *,
    bot_nickname: str = "",
    swear_words: list[str] | None = None,
    max_length: int = 200,
) -> str:
    """清洗青云客原始回复文本。

    参数:
        content: 青云客 content 字段原始文本
        bot_nickname: 机器人自身昵称，用于替换"菲菲"
        swear_words: 本地粗口词表，命中替换为 *
        max_length: 回复最大长度，超过则截断（主要防超长笑话刷屏）

    返回:
        清洗后的文本；若内容被判定为广告/垃圾，返回空字符串。
    """
    if not content or not isinstance(content, str):
        return ""

    text = content.strip()

    # 1. 广告 / 引流直接判空
    for bad in _BLACKLIST_SUBSTRINGS:
        if bad in text:
            return ""

    # 2. 截断"提示"等引导语
    for marker in _CUTOFF_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]

    # 3. 换行 / 表情标记处理
    text = text.replace("{br}", "\n")
    text = _FACE_PATTERN.sub("", text)

    # 4. 自称替换
    if bot_nickname:
        text = text.replace("菲菲", bot_nickname)
        text = text.replace("艳儿", bot_nickname)

    # 5. 粗口词替换为 *（整词长度数量的星号，保留可读性）
    for word in swear_words or []:
        if word and word in text:
            text = text.replace(word, "*" * len(word))

    # 6. 长度限制：超长截断，防止低俗长笑话刷屏
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "……"

    return text


def contains_blocked_keyword(text: str, keywords: list[str]) -> bool:
    """判断消息是否命中青云客黑名单关键词（笑话/天气/翻译等）。

    命中时说明该功能实测质量差或服务端报错，应跳过青云客，交给 LLM。

    参数:
        text: 用户消息纯文本
        keywords: 黑名单关键词列表

    返回:
        bool: 是否命中
    """
    if not text:
        return False
    return any(kw and kw in text for kw in keywords)


def apply_global_text_filter(text: str, filter_words: list[str]) -> str:
    """全局文本过滤器（所有来源回复发送前统一执行）。

    与官方 ai 插件的 TEXT_FILTER 行为保持一致：命中替换为 *。

    参数:
        text: 待发送文本
        filter_words: 过滤词表

    返回:
        过滤后的文本
    """
    if not text:
        return text
    for word in filter_words or []:
        if word:
            text = text.replace(word, "*")
    return text
