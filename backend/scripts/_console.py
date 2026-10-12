"""``scripts`` 内各 CLI 的共用控制台工具（内部模块，非交付物）。

Windows 控制台默认代码页是 GBK，脚本一打印 emoji / 中文全角标点就会
``UnicodeEncodeError`` 并**中断整个命令**。而这些脚本恰恰是「跑了才知道结果」
的运维工具，中断的代价是几十秒到几分钟的探测白跑。

故统一在此提供 ``ensure_utf8_stdout()``，供各 CLI 在 ``main()`` 开头调用一次。
"""

from __future__ import annotations

import sys

__all__ = ["ensure_utf8_stdout"]


def ensure_utf8_stdout() -> None:
    """把 stdout / stderr 切到 UTF-8，并允许「编码不了就替换」。

    为什么要 ``errors="replace"`` 而不是让它抛错：报告已经落盘，
    **终端显示不该成为整个任务的单点故障**；实在映射不了的字符显示成 ``?``
    只是不好看，不影响结论。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — 被重定向/包装过的流可能不支持
                pass
