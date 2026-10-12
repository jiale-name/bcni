"""成本计算（F-9.5）—— **确定性组件**，铁律 10 的落地之一。

为什么要有这个文件（而不是在 service 里写两行乘法）
================================================
铁律 10：「**确定性结论必须来自确定性组件**；LLM 只产出文本，不得让其决定数值结论」。
成本是一个**要进财务报表的数字**：它必须可复算、可审计、
换一次实现不改结果。两行乘法写在调用点里，第一个月就够用，
第六个月会出现「列表页的成本与看板的总和对不上」——
因为有人只在其中一处改了取整方式。

本模块只做一件事：**用定点数（``Decimal``）算钱**。
❌ 不用 ``float`` —— ``0.1 + 0.2 != 0.3``，几千次调用累加后会出现分位误差，
而运维会拿它跟云厂商账单对账。

单价口径
========
``price_in_per_1k`` / ``price_out_per_1k`` 单位是 **元 / 千 token**
（云厂商普遍这么报）。本地模型填 0 —— 是**默认值**而不是「忘了配」，
本地只是耗算力不花钱，故本地调用成本恒为 0 是正确的。

结果精度
========
落库列是 ``DECIMAL(12,6)``（数据库 §4.6），故统一量化到 **6 位小数**
（``ROUND_HALF_UP``，与财务习惯一致；Python 默认的 ``ROUND_HALF_EVEN``
会让 ``0.0000005`` 变成 ``0.000000``，对账时解释不清）。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: 落库精度（与 ``ai_call_log.cost`` 的 ``DECIMAL(12,6)`` 一致）
COST_QUANT = Decimal("0.000001")

#: 千 token 的分母
_PER_1K = Decimal("1000")


class CostCalculator:
    """Token 成本计算器（纯函数，无状态、无 IO）。"""

    @staticmethod
    def compute(
        *,
        tokens_in: int,
        tokens_out: int,
        price_in_per_1k: Decimal | float | int | None,
        price_out_per_1k: Decimal | float | int | None,
    ) -> Decimal:
        """按 token 数与单价算出成本（元），量化到 6 位小数。

        .. code-block:: python

            CostCalculator.compute(tokens_in=1832, tokens_out=412,
                                   price_in_per_1k=Decimal("0.001"),
                                   price_out_per_1k=Decimal("0.002"))
            # Decimal('0.002656')

        :raises ValueError: token 数为负（**不静默取绝对值** ——
            负 token 一定是上游统计错了，掩盖它会让成本看板长期偏低）
        """
        if int(tokens_in) < 0 or int(tokens_out) < 0:
            raise ValueError(f"token 数不可为负：in={tokens_in} out={tokens_out}")

        p_in = CostCalculator._to_decimal(price_in_per_1k)
        p_out = CostCalculator._to_decimal(price_out_per_1k)
        total = (
            Decimal(int(tokens_in)) * p_in + Decimal(int(tokens_out)) * p_out
        ) / _PER_1K
        return total.quantize(COST_QUANT, rounding=ROUND_HALF_UP)

    @staticmethod
    def estimate(
        *,
        prompt_chars: int,
        max_output_tokens: int,
        price_in_per_1k: Decimal | float | int | None,
        price_out_per_1k: Decimal | float | int | None,
    ) -> Decimal:
        """**预估**成本上限（供「高成本查询确认」待办 ``cost_confirm`` 使用，F-A0.3）。

        ⚠️ 这是**上界**而不是精确值，两个输入都是估算：

        1. 输入 token 用「字符数 / 1.5」粗估（中英混排的经验值，**不是 tokenizer**）；
        2. 输出按 ``max_output_tokens`` 顶格算。

        接口 §3 的备注要求关键决策点（含**高成本查询**）走人工确认 ——
        人工确认需要的是「最坏情况要花多少」，而不是「估计花多少」，
        故这里刻意**顶格估**：宁可把阈值判得保守一点，也不要事后超支。

        ⚠️ 因此本函数的返回值**只用于拦截判定，不得写入 ``ai_call_log.cost``**
        （那必须由 :meth:`compute` 按真实 token 算）。
        """
        est_in = max(1, int(prompt_chars) * 2 // 3)  # 约 1.5 字符 / token
        return CostCalculator.compute(
            tokens_in=est_in,
            tokens_out=max(0, int(max_output_tokens)),
            price_in_per_1k=price_in_per_1k,
            price_out_per_1k=price_out_per_1k,
        )

    @staticmethod
    def _to_decimal(value: Decimal | float | int | None) -> Decimal:
        """把单价转成 ``Decimal``。

        ⚠️ ``float`` 必须**先转 str 再转 Decimal**：``Decimal(0.001)`` 会得到
        ``0.001000000000000000020816681711721685...`` —— 这是二进制浮点的真实值，
        它会让「1 千 token × 0.001 元」算成 ``0.001000`` 而不是恰好 ``0.001000``
        （本例四舍五入后看不出来，但单价是 ``0.0001`` 量级时就会显现）。
        """
        if value is None:
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        if isinstance(value, float):
            return Decimal(str(value))
        return Decimal(int(value))
