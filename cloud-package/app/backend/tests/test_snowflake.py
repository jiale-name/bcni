"""CU-02 · 雪花 ID 生成器单测。

覆盖：位宽分配、单调递增、并发唯一、时钟回拨（小回拨容错 / 大回拨报错）、配置边界。
"""

from __future__ import annotations

import threading

import pytest

from app.core.snowflake import (
    ClockBackwardsError,
    SnowflakeGenerator,
    next_id,
)

#: JS 安全整数上限（接口 §1.2 的「魔鬼数字」）
JS_MAX_SAFE_INTEGER = 2**53 - 1

#: 与 config.py 一致的纪元（2024-01-01T00:00:00Z）
_EPOCH_MS = 1_704_038_400_000


def _make(**kwargs) -> SnowflakeGenerator:
    params = {"worker_id": 1, "datacenter_id": 1, "epoch_ms": _EPOCH_MS}
    params.update(kwargs)
    return SnowflakeGenerator(**params)  # type: ignore[arg-type]


def test_id_is_positive_int64() -> None:
    value = _make().next_id()
    assert isinstance(value, int)
    assert 0 < value < 2**63  # 有符号 BIGINT 范围内


def test_id_is_monotonic_within_process() -> None:
    gen = _make()
    ids = [gen.next_id() for _ in range(1000)]
    assert ids == sorted(ids), "同进程雪花 ID 必须严格递增"
    assert len(set(ids)) == 1000, "同进程雪花 ID 必须唯一"


def test_sequence_rolls_over_within_same_millisecond() -> None:
    """冻结时钟：验证同毫秒内序列号推进（0~4095），而不是靠时间前进。"""
    frozen = [1_800_000_000.0]
    gen = _make(clock=lambda: frozen[0])
    first = gen.next_id()
    second = gen.next_id()
    assert second == first + 1, "同毫秒内应靠序列号递增"
    assert len({gen.next_id() for _ in range(100)}) == 100


def test_id_exceeds_js_safe_integer() -> None:
    """本项目 ID 会超出 JS 安全整数范围 —— 这是必须序列化为字符串的**根本原因**。"""
    value = _make().next_id()
    assert value > JS_MAX_SAFE_INTEGER, (
        f"当前配置下 ID={value} 未超 2**53-1（{JS_MAX_SAFE_INTEGER}）"
    )


def test_concurrent_generation_is_unique() -> None:
    """多线程并发下不得产生重复 ID（真实服务是多线程 + 多进程）。"""
    gen = _make(worker_id=3, datacenter_id=2)
    results: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        local = [gen.next_id() for _ in range(500)]
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4000
    assert len(set(results)) == 4000, "并发下出现重复 ID —— 序列号未加锁"


def test_different_workers_do_not_collide() -> None:
    a = _make(worker_id=1, datacenter_id=1)
    b = _make(worker_id=2, datacenter_id=1)
    # 同一时刻（冻结时钟）两个 worker 的 ID 必须不同（worker 位参与编码）
    frozen = [1_800_000_000.0]
    a._clock = lambda: frozen[0]  # noqa: SLF001 — 测试内直接替换时钟，避免再加参数
    b._clock = lambda: frozen[0]  # noqa: SLF001
    assert a.next_id() != b.next_id()


def test_worker_and_datacenter_range_validated() -> None:
    with pytest.raises(ValueError):
        _make(worker_id=32)  # 5 bit → 0~31
    with pytest.raises(ValueError):
        _make(worker_id=-1)
    with pytest.raises(ValueError):
        _make(datacenter_id=32)


def test_clock_backwards_small_drift_is_tolerated_and_monotonic() -> None:
    """小回拨（≤ 容忍窗口）应继续产号，且**严格递增**、不阻塞。"""
    now = [1_800_000_000_000]  # 毫秒
    gen = _make(clock=lambda: now[0] / 1000.0, max_backwards_ms=5)

    first = gen.next_id()
    now[0] -= 2  # 回拨 2ms
    second = gen.next_id()

    assert second > first, "小回拨后必须仍然递增（沿用上一毫秒 + 推进序列号）"


def test_clock_backwards_large_drift_raises() -> None:
    """大回拨（> 容忍窗口）必须显式报错，绝不静默产出可能重复的 ID。"""
    now = [1_800_000_000_000]
    gen = _make(clock=lambda: now[0] / 1000.0, max_backwards_ms=5)
    gen.next_id()
    now[0] -= 10_000  # 回拨 10s
    with pytest.raises(ClockBackwardsError):
        gen.next_id()


def test_module_level_next_id_uses_singleton() -> None:
    """``next_id`` 是 ORM ``default=`` 的入口，必须可直接调用且唯一。"""
    a, b = next_id(), next_id()
    assert isinstance(a, int)
    assert a != b
