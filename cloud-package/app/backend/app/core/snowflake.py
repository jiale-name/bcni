"""雪花 ID 生成器（数据库设计 §0.1：应用生成，有符号 BIGINT）。

位分配（共 63 bit，最高位恒 0 以保证有符号 BIGINT 为正）::

    | 1 bit 保留 | 41 bit 毫秒时间戳 | 5 bit 数据中心 | 5 bit 机器 | 12 bit 序列号 |
          0            (相对 epoch)       0~31          0~31        0~4095

设计要点
--------
1. **单机一期**：``SNOWFLAKE_DATACENTER_ID`` / ``SNOWFLAKE_WORKER_ID`` 默认 1/1；
   多实例部署时按实例改写，否则会撞号。
2. **时钟回拨保护**（分级处理，❌ 绝不"将就生成"）：
   - 回拨 ≤ ``max_backwards_ms``（默认 5ms）：**沿用上一毫秒时间戳并推进序列号**，
     既保证单调递增，也不会因自旋等待而卡住（时钟被 NTP 小幅校正很常见）；
   - 回拨 > 容忍窗口：抛 :class:`ClockBackwardsError` —— 宁可不服务，
     也不产出可能重复的 ID（唯一索引冲突时极难定位）。
3. 🔴 **精度坑（数据库 §0.1 / 接口 §1.2）**：ID 为 int64，可超 ``Number.MAX_SAFE_INTEGER``
   （9.007e15）。**对外序列化必须转字符串**，见 :mod:`app.schemas.common` 的 ``SnowflakeId``。
4. **可测性**：``clock`` 可注入（默认 ``time.time``），使时钟回拨分支能在单测中稳定复现，
   无需 ``sleep`` 或改系统时间。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.config import settings

#: 各段位宽
_WORKER_BITS = 5
_DATACENTER_BITS = 5
_SEQUENCE_BITS = 12

_MAX_WORKER_ID = (1 << _WORKER_BITS) - 1  # 31
_MAX_DATACENTER_ID = (1 << _DATACENTER_BITS) - 1  # 31
_MAX_SEQUENCE = (1 << _SEQUENCE_BITS) - 1  # 4095

_WORKER_SHIFT = _SEQUENCE_BITS  # 12
_DATACENTER_SHIFT = _SEQUENCE_BITS + _WORKER_BITS  # 17
_TIMESTAMP_SHIFT = _SEQUENCE_BITS + _WORKER_BITS + _DATACENTER_BITS  # 22

#: 默认时钟回拨容忍窗口（毫秒）
DEFAULT_MAX_BACKWARDS_MS = 5


class ClockBackwardsError(RuntimeError):
    """系统时钟发生较大回拨，拒绝生成 ID。"""


class SnowflakeGenerator:
    """线程安全的雪花 ID 生成器。"""

    def __init__(
        self,
        worker_id: int,
        datacenter_id: int,
        epoch_ms: int,
        *,
        clock: Callable[[], float] = time.time,
        max_backwards_ms: int = DEFAULT_MAX_BACKWARDS_MS,
    ) -> None:
        """
        :param worker_id: 机器号 0~31
        :param datacenter_id: 数据中心号 0~31
        :param epoch_ms: 起始纪元（毫秒）。41 bit 时间戳以此为基准，
            决定了 ID 的绝对大小 —— 纪元越早，ID 越大（越容易超 JS 安全整数）
        :param clock: 时钟函数（返回秒，可为 float）。注入用于测试回拨分支
        :param max_backwards_ms: 时钟回拨容忍窗口（毫秒），超出即抛错
        """
        if not 0 <= worker_id <= _MAX_WORKER_ID:
            raise ValueError(f"worker_id 必须落在 0~{_MAX_WORKER_ID}，当前 {worker_id}")
        if not 0 <= datacenter_id <= _MAX_DATACENTER_ID:
            raise ValueError(
                f"datacenter_id 必须落在 0~{_MAX_DATACENTER_ID}，当前 {datacenter_id}"
            )
        if max_backwards_ms < 0:
            raise ValueError("max_backwards_ms 不能为负")

        self._worker_id = worker_id
        self._datacenter_id = datacenter_id
        self._epoch_ms = epoch_ms
        self._clock = clock
        self._max_backwards_ms = max_backwards_ms
        self._sequence = 0
        self._last_timestamp_ms = -1
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    def _wait_next_ms(self, last_ms: int) -> int:
        """自旋等待到下一毫秒（仅在「同毫秒序列号耗尽」时使用）。"""
        ts = self._now_ms()
        while ts <= last_ms:
            ts = self._now_ms()
        return ts

    # ------------------------------------------------------------------
    def next_id(self) -> int:
        """生成下一个雪花 ID（严格单调递增）。"""
        with self._lock:
            timestamp = self._now_ms()

            if timestamp < self._last_timestamp_ms:
                offset = self._last_timestamp_ms - timestamp
                if offset > self._max_backwards_ms:
                    raise ClockBackwardsError(
                        f"检测到系统时钟回拨 {offset}ms（超过容忍窗口 "
                        f"{self._max_backwards_ms}ms），拒绝生成 ID 以免撞号"
                    )
                # 小幅回拨：**不等待**，沿用上一毫秒并靠序列号区分 —— 保证单调且不阻塞
                timestamp = self._last_timestamp_ms

            if timestamp == self._last_timestamp_ms:
                self._sequence += 1
                if self._sequence > _MAX_SEQUENCE:
                    # 同毫秒 4096 个序列号耗尽 → 等下一毫秒并重置序列
                    timestamp = self._wait_next_ms(self._last_timestamp_ms)
                    self._sequence = 0
            else:
                self._sequence = 0

            self._last_timestamp_ms = timestamp

            return (
                ((timestamp - self._epoch_ms) << _TIMESTAMP_SHIFT)
                | (self._datacenter_id << _DATACENTER_SHIFT)
                | (self._worker_id << _WORKER_SHIFT)
                | self._sequence
            )

    def next_ids(self, count: int) -> list[int]:
        """批量生成（一次加锁内顺序生成，避免逐次竞争）。"""
        return [self.next_id() for _ in range(count)]


_generator: SnowflakeGenerator | None = None
_generator_lock = threading.Lock()


def get_generator() -> SnowflakeGenerator:
    """进程内单例。"""
    global _generator
    if _generator is None:
        with _generator_lock:
            if _generator is None:
                _generator = SnowflakeGenerator(
                    worker_id=settings.SNOWFLAKE_WORKER_ID,
                    datacenter_id=settings.SNOWFLAKE_DATACENTER_ID,
                    epoch_ms=settings.SNOWFLAKE_EPOCH_MS,
                )
    return _generator


def next_id() -> int:
    """便捷函数：生成一个雪花 ID（供 ORM ``default=`` 使用）。"""
    return get_generator().next_id()
