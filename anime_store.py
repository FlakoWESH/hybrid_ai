"""anime.json 动漫语录的加载、迁移与匹配。

anime.json 格式：{"关键词": ["回复1", "回复2", ...]}，
共 177 个关键词、1500+ 条萌系互动回复。

设计要点：
- 延迟加载：模块导入时不读文件，避免 anime.json 缺失导致整个插件加载失败
  （旧版 ai 插件在模块顶层 json.load，文件缺失即崩溃）。
- 容错：文件不存在 / JSON 解析失败时返回空字典，anime 分支静默跳过。
- 首次启动迁移：插件自带一份 anime.json，移动到 DATA_PATH 下与官方 ai 共用。
"""

import random
import shutil
from pathlib import Path

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

# 全局运行时数据文件位置（与官方 ai 插件保持一致）
ANIME_FILE = DATA_PATH / "anime.json"


class AnimeStore:
    """anime 语库，带惰性加载与缓存。"""

    def __init__(self) -> None:
        self._data: dict[str, list[str]] | None = None

    def migrate_bundled(self, plugin_dir: Path) -> None:
        """首次启动时把插件目录内自带的 anime.json 迁移到 DATA_PATH。

        仅当 DATA_PATH 下不存在时才移动，避免覆盖用户自定义语库。

        参数:
            plugin_dir: 插件根目录
        """
        src = plugin_dir / "anime.json"
        if src.exists() and not ANIME_FILE.exists():
            try:
                ANIME_FILE.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(ANIME_FILE))
                logger.info(f"anime.json 已移动到 {ANIME_FILE}", "hybrid_ai")
            except Exception as e:
                logger.error(f"anime.json 移动失败: {e}", "hybrid_ai", e=e)

    def _load(self) -> dict[str, list[str]]:
        """惰性加载并缓存 anime.json，失败返回空字典。"""
        if self._data is not None:
            return self._data

        if not ANIME_FILE.exists():
            logger.warning(f"anime.json 不存在于 {ANIME_FILE}，anime 语库将跳过", "hybrid_ai")
            self._data = {}
            return self._data

        try:
            with ANIME_FILE.open("r", encoding="utf8") as f:
                data = json_load(f)
            if not isinstance(data, dict):
                raise ValueError("anime.json 顶层结构不是对象")
            self._data = data
            logger.info(f"anime 语库加载完成，共 {len(data)} 个关键词", "hybrid_ai")
        except Exception as e:
            logger.error("加载 anime.json 失败，anime 语库将跳过", "hybrid_ai", e=e)
            self._data = {}
        return self._data

    def match(self, text: str, nickname: str) -> str | None:
        """按子串匹配关键词，命中则随机返回一条回复。

        与官方 ai 行为一致：text.find(key) != -1 即命中；
        回复中的"你"替换为用户昵称。

        参数:
            text: 用户消息纯文本
            nickname: 发送者昵称

        返回:
            命中返回回复字符串，未命中返回 None
        """
        data = self._load()
        if not text or not data:
            return None

        for key, replies in data.items():
            if key and key in text and replies:
                reply = random.choice(replies)
                if nickname:
                    reply = reply.replace("你", nickname)
                return reply
        return None


def json_load(file_obj):
    """读取 JSON，优先用 ujson 以保持与官方插件一致。"""
    try:
        import ujson

        return ujson.load(file_obj)
    except Exception:
        import json

        return json.load(file_obj)


# 全局单例
anime_store = AnimeStore()
