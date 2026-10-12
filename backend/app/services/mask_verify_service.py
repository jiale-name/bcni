"""脱敏自检（CU-29，API-66，F-3.9 / 需求 §12.3 安全专项）。

为什么需要一个「自检」接口
==========================
CL-4 第 2 条约定是「出网前必过二次扫描」。但**扫描规则本身会不会漏**
是另一回事 —— 正则只覆盖手机号 / 邮箱 / 身份证，**人名无法用正则穷举**。
本接口让合规 / 运维把「已知会漏的形态」投进来，用**生产环境的同一套规则**
跑一遍，得到一份可复算的漏检清单（而不是靠人肉 review 代码猜）。

🔴 三条实现红线（缺一不可）
==========================
1. **不回显原文** —— 入参本身就是「可能含明文的文本」。出参若带原文，
   这个「查泄漏」的接口自己就成了泄漏源。故只给「序号 + 命中类型 + 脱敏后预览」。
2. **审计只记规模** —— 只记 ``{sample_count, leak_count}``，❌ 不记样例内容，
   否则等于把 PII 抄进了审计表（审计是长期留存、多人可读的表）。
3. **判定复用生产规则** —— 直接调 :func:`~app.core.masking.verify_outbound`。
   ❌ 绝不另写一套「自检专用规则」：那会得到「自检说通过、生产却漏了」
   这种最坏结论，比不做自检更危险（它会给出虚假的安全感）。
"""

from __future__ import annotations

from app.core.masking import PlaceholderTable, mask_text, verify_outbound
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.talent_master import (
    MASK_MIN_SAMPLES,
    MaskLeakOut,
    MaskVerifyOut,
    MaskVerifyRequest,
)
from app.services.audit_service import AuditService

#: 预览串长度上限（预览已是脱敏形态，但仍截断：控响应体，且减少残片被拼回的概率）
_PREVIEW_LIMIT = 120

#: 漏检类型 → 已登记的能力边界说明（把「已知会漏」与「新发现」分开）
_KNOWN_BOUNDARY: dict[str, str] = {
    "name": (
        "人名残留是**已登记的能力边界**（评审文档 §7.3 批次 15 的 G-01/G-02）："
        "姓名的形态（内插空格 / 拼音 / 昵称）无法用正则穷举，当前实现靠"
        "「已知人名精确匹配」兜底，故新形态必然漏检。"
        "修法方向是**先归一化再扫描**（去空格与大小写差异，只作用于判定、不改写原文），"
        "或在有算力时引入 NER —— 但 NER 自身仍有漏检率，不等于「零漏检」。"
    ),
    "phone": (
        "手机号漏检多为**书写变体**（``+86`` 前缀、``-`` 与空格分隔，见 G-03）："
        "正则要求 11 位连续数字且不被更长数字串包夹。"
        "修法同样是**先归一化再扫描**，❌ 不是把正则写松 ——"
        "写松会让「订单号 / 批次编号」这类长数字串被误判为手机号（T-40b 反向对照用例）。"
        # ⚠️ 这段文案本身也在响应体里：**禁止出现任何形如手机号 / 身份证的数字示例**，
        #    哪怕是「举例说明会被误判」的反例 —— 含 11 位连续数字的示例串
        #    其子串就会命中生产正则，等于把明文手机号写回响应体（T-52 实测抓到过）。
    ),
}


class MaskVerifyService:
    """API-66 脱敏自检（无状态：**不碰数据库**，只做确定性判定）。"""

    @classmethod
    def verify(cls, req: MaskVerifyRequest, *, tenant_id: int, user_id: int) -> MaskVerifyOut:
        samples = list(req.samples)
        known_names = [n for n in req.known_names if n and n.strip()]

        leaks: list[MaskLeakOut] = []
        leaked_types: set[str] = set()
        for index, sample in enumerate(samples):
            result = verify_outbound(sample, known_names=known_names or None)
            if result.passed:
                continue
            leaks.append(
                MaskLeakOut(
                    index=index,
                    residual=list(result.residual),
                    preview=cls._preview(sample, known_names),
                )
            )
            leaked_types.update(result.residual)

        meets_minimum = len(samples) >= MASK_MIN_SAMPLES

        # 🔴 审计：**只记规模**（把 PII 写进审计等于换个地方存一份明文）
        AuditService.record(
            AuditAction.MASK_VERIFY,
            tenant_id=int(tenant_id),
            user_id=int(user_id),
            target_type=AuditTarget.SESSION,  # 复用「会话/自检」这一非业务目标类别
            after={"sample_count": len(samples), "leak_count": len(leaks)},
        )

        return MaskVerifyOut(
            sample_count=len(samples),
            minimum_required=MASK_MIN_SAMPLES,
            meets_minimum=meets_minimum,
            passed=not leaks,
            leak_count=len(leaks),
            leaked_types=sorted(leaked_types),
            leaks=leaks,
            note=cls._build_note(meets_minimum, leaks, sorted(leaked_types)),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _preview(sample: str, known_names: list[str]) -> str:
        """生成**脱敏后**的预览（与生产同源：``mask_text`` 的规则）。"""
        table = PlaceholderTable()
        text = sample
        for name in known_names:
            text = text.replace(name, table.put("candidate", name) or name)
        text = mask_text(text, table)
        return text[:_PREVIEW_LIMIT] + ("…" if len(text) > _PREVIEW_LIMIT else "")

    @staticmethod
    def _build_note(meets_minimum: bool, leaks: list[MaskLeakOut], types: list[str]) -> str:
        """给出一句**可行动**的结论（而不是只报数字）。"""
        parts: list[str] = []
        if not meets_minimum:
            parts.append(
                f"⚠️ 样例数不足 {MASK_MIN_SAMPLES} 条（需求 §12.3 的下限），"
                "本次结论**不构成验收证据**，仅作开发期自检。"
            )
        if not leaks:
            parts.append("本次提交的样例未发现残留，通过。")
        else:
            parts.append(f"发现 {len(leaks)} 条漏检。")
            for kind in types:
                hint = _KNOWN_BOUNDARY.get(kind)
                if hint:
                    parts.append(f"[{kind}] {hint}")
                else:
                    parts.append(f"[{kind}] 未登记的漏检类型，请评估是否属新缺陷。")
        return " ".join(parts)


__all__ = ["MaskVerifyService"]
