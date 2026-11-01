"""青云客滑动窗口限流器。

青云客官方限制：同 IP 每 10 分钟不超过 200 次调用。
本插件默认更保守（100 次/10 分钟），并复用 zhenxun 内置的
``zhenxun.utils.limiters.RateLimiter`` 完成滑动窗口计数，这里只补充
「剩余配额查询」和「运行时热更新」两项：青云客按 IP 维度限流，无法按群/
按用户隔离，因此使用固定的全局 key。

触发限流后由路由层降级到 LLM / no_result。
"""

import time

from zhenxun.utils.limiters import RateLimiter

_KEY = "__hybrid_ai_qyk__"


class SlidingWindowRateLimiter(RateLimiter):
    """在 RateLimiter 基础上补充 remaining() 与 update_limits()。"""

    def __init__(self, max_calls: int, window_seconds: int):
        """
        参数:
            max_calls: 一个窗口内允许的最大调用次数
            window_seconds: 窗口长度（秒）
        """
        super().__init__(max(1, int(max_calls)), int(window_seconds))

    def acquire(self) -> bool:
        """尝试占用一次调用配额。

        返回:
            bool: True 表示本次允许调用（并已计数）；False 表示当前窗口已满。
        """
        return self.check(_KEY)

    def remaining(self) -> int:
        """当前窗口内剩余的调用配额（不消耗）。"""
        now = time.time()
        queue = self.requests[_KEY]
        while queue and queue[0] <= now - self.time_window:
            queue.popleft()
        return max(0, self.max_calls - len(queue))

    def update_limits(self, max_calls: int, window_seconds: int) -> None:
        """运行时更新限流参数（配置热更时使用）。"""
        self.max_calls = max(1, int(max_calls))
        self.time_window = int(window_seconds)
