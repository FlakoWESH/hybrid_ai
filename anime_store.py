"""语料库加载、迁移与匹配。

合并两份语料：
- anime.json：自带萌系互动回复（摸/抱/亲...），与官方 ai 共用
- qa.json：来自 yuzi-bot 的 15000+ 条问答语料（{name}/{me}/{segment} 占位符）

匹配算法（两阶段）：
1. 子串过滤：`key in text`，快速缩小候选
2. 相似度评分：在候选里用 difflib.quick_ratio() 取最高分那组
   （长 key 占 text 比例更高，倾向选更精确的匹配）

占位符替换：
- {me}      → bot 自称（BotConfig.self_nickname）
- {name}    → 用户昵称
- {segment} → 换行
- "你"      → 用户昵称（兼容旧 anime.json 语料）
"""

from pathlib import Path
import difflib
import random
import shutil

from zhenxun.configs.config import BotConfig
from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

ANIME_FILE = DATA_PATH / "anime.json"
QA_FILE = DATA_PATH / "qa.json"


def _json_load(file_obj):
    try:
        import ujson
        return ujson.load(file_obj)
    except Exception:
        import json
        return json.load(file_obj)


class QaStore:
    """合并语料库，带惰性加载与两阶段匹配。"""

    def __init__(self) -> None:
        self._data: dict[str, list[str]] | None = None

    def migrate_bundled(self, plugin_dir: Path) -> None:
        """首次启动时把插件目录内的语料复制到 DATA_PATH（仅当目标不存在）。"""
        for fname in ("anime.json", "qa.json"):
            src = plugin_dir / fname
            dst = DATA_PATH / fname
            if src.exists() and not dst.exists():
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dst))
                    self._data = None
                    logger.info(f"{fname} 已复制到 {dst}", "hybrid_ai")
                except Exception as e:
                    logger.error(f"{fname} 复制失败: {e}", "hybrid_ai", e=e)

    def _load(self) -> dict[str, list[str]]:
        """惰性加载并合并两份语料，anime.json 优先级高于 qa.json。"""
        if self._data is not None:
            return self._data

        merged: dict[str, list[str]] = {}

        # 先加载 qa.json（大语料，低优先级）
        if QA_FILE.exists():
            try:
                with QA_FILE.open("r", encoding="utf8") as f:
                    qa = _json_load(f)
                if isinstance(qa, dict):
                    merged.update(qa)
            except Exception as e:
                logger.error(f"加载 qa.json 失败: {e}", "hybrid_ai", e=e)

        # 再加载 anime.json（萌系互动，覆盖同 key）
        if ANIME_FILE.exists():
            try:
                with ANIME_FILE.open("r", encoding="utf8") as f:
                    anime = _json_load(f)
                if isinstance(anime, dict):
                    for k, v in anime.items():
                        if v:
                            merged[k] = v
            except Exception as e:
                logger.error(f"加载 anime.json 失败: {e}", "hybrid_ai", e=e)

        self._data = merged
        if merged:
            logger.info(
                f"语料库加载完成，共 {len(merged)} 个关键词、"
                f"{sum(len(v) for v in merged.values())} 条回复",
                "hybrid_ai",
            )
        else:
            logger.warning("语料库为空，短消息语料分支将跳过", "hybrid_ai")
        return self._data

    def _build_reply(self, reply: str, nickname: str) -> str:
        """替换占位符。"""
        me = BotConfig.self_nickname or "小真寻"
        text = reply
        text = text.replace("{me}", me)
        text = text.replace("{name}", nickname or "你")
        text = text.replace("{segment}", "\n")
        # 兼容旧 anime.json 里直接写"你"的语料
        if nickname:
            text = text.replace("你", nickname)
        return text

    def match(self, text: str, nickname: str) -> str | None:
        """两阶段匹配：子串过滤 + 相似度评分，命中则随机返回一条回复。

        参数:
            text: 用户消息纯文本
            nickname: 发送者昵称

        返回:
            命中返回替换占位符后的回复字符串，未命中返回 None
        """
        data = self._load()
        if not text or not data:
            return None

        # 清理：去掉 bot 自称后再算相似度（避免 bot 名干扰评分）
        me = BotConfig.self_nickname or ""
        cleaned = text.replace(me, "") if me else text

        # 阶段1：子串过滤出候选，阶段2：对候选算相似度
        best_score = -1.0
        best_replies: list[str] | None = None

        for key, replies in data.items():
            if not key or not replies or key not in text:
                continue
            score = difflib.SequenceMatcher(None, key, cleaned).quick_ratio()
            if score > best_score:
                best_score = score
                best_replies = replies
            elif score == best_score and best_replies is not None:
                # 同分合并候选
                best_replies = best_replies + replies

        if best_replies:
            reply = random.choice(best_replies)
            return self._build_reply(reply, nickname)
        return None


# 全局单例
qa_store = QaStore()
# 向后兼容别名
anime_store = qa_store
