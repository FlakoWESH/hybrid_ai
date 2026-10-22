"""滑动窗口限流器。

青云客官方限制：同 IP 每 10 分钟不超过 200 次调用。
本插件默认更保守（100 次/10 分钟），并在进程内用滑动窗口计数，
避免固定窗口在边界处产生 2 倍突刺。

注意：青云客是按 IP 维度限流，无法按群/按用户隔离，
因此这里全局限流；触发限流后由路由层降级到 LLM / no_result。
"""

import time
from collections import deque


class SlidingWindowRateLimiter:
    """基于时间戳双端队列的滑动窗口限流器。"""

    def __init__(self, max_calls: int, window_seconds: int):
        """
        参数:
            max_calls: 一个窗口内允许的最大调用次数
            window_seconds: 窗口长度（秒）
        """
        self.max_calls = max(1, int(max_calls))
        self.window_seconds = int(window_seconds)
        self._timestamps: deque[float] = deque()

    def _evict_expired(self, now: float) -> None:
        """丢弃窗口外的过期时间戳。"""
        boundary = now - self.window_seconds
        while self._timestamps and self._timestamps[0] <= boundary:
            self._timestamps.popleft()

    def acquire(self) -> bool:
        """尝试占用一次调用配额。

        返回:
            bool: True 表示本次允许调用（并已计数）；False 表示当前窗口已满。
        """
        now = time.time()
        self._evict_expired(now)
        if len(self._timestamps) >= self.max_calls:
            return False
        self._timestamps.append(now)
        return True

    def remaining(self) -> int:
        """当前窗口内剩余的调用配额（不消耗）。"""
        now = time.time()
        self._evict_expired(now)
        return max(0, self.max_calls - len(self._timestamps))

    def update_limits(self, max_calls: int, window_seconds: int) -> None:
        """运行时更新限流参数（配置热更时使用）。"""
        self.max_calls = max(1, int(max_calls))
        self.window_seconds = int(window_seconds)
