"""群聊上下文缓冲：按群维护最近 N 条消息的 deque。

被忽略的用户/关键词消息不进缓冲，
插话时把缓冲内容拼成纯文本喂给 LLM 作为群聊上下文。
"""

from collections import deque

# group_id -> deque[str]
_BUFFERS: dict[str, deque] = {}


def add_message(group_id: str, nickname: str, text: str, window: int) -> None:
    """把一条群消息加入缓冲。

    格式: "[昵称]: 消息文本"，让 LLM 能区分是谁在说话。
    """
    if group_id not in _BUFFERS:
        _BUFFERS[group_id] = deque(maxlen=window)
    _BUFFERS[group_id].append(f"{nickname}: {text}")


def get_context(group_id: str) -> str:
    """获取群聊上下文纯文本，按时间顺序拼接。"""
    buf = _BUFFERS.get(group_id)
    if not buf:
        return ""
    return "\n".join(buf)


def clear(group_id: str) -> None:
    """清空指定群的缓冲（管理员手动重置时用）。"""
    _BUFFERS.pop(group_id, None)
