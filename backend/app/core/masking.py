"""出网脱敏与本地回填（CU-21，F-3.9，CL-4）—— 全项目**风险最高**的一段代码。

本模块回答两个问题
==================
1. **明文在哪一步消失**：:func:`mask_text` 在片段送出网关**之前**把明文身份
   替换为占位符（``{{CANDIDATE}}`` / ``{{PHONE}}`` / ``{{EMAIL}}`` / ``{{ID}}``）；
2. **真值在哪一步回来**：:func:`rehydrate` 在生成文本返回**之后**、下发前端**之前**，
   用**本次请求内存里**的 :class:`PlaceholderTable` 把占位符换回真值 ——
   回填**不经任何外部服务**（CL-4 行为约定 3）。

四条不可动摇的行为约定（CL-4，逐条落点）
========================================
1. **先脱敏再出网** —— 本模块是 :func:`~app.services.ask_service.AskService` 组装
   Prompt 时的**强制调用点**；未脱敏的片段进网关即违规。
2. **出网前必过二次扫描** —— :func:`verify_outbound` 用**正则规则**（确定性，铁律 10）
   扫残留明文身份；发现残留 → 拒答（``NER_RESIDUAL``），**绝不降级为明文出网**。
3. **回填只发生在服务端** —— :class:`PlaceholderTable` 是**本次请求的临时映射**，
   ❌ 不落库、❌ 不进 Redis：落库等于把「明文-占位符字典」也存下来，反而扩大暴露面。
4. **任一步失败即拒答** —— 回填后发现占位符解析不了（``MASK_REQUIRED``），
   拒绝下发，宁可少答一次。

为什么是正则而不是模型
====================
「这段文字里有没有手机号 / 身份证 / 已知人名」是**合规判定**，不是文本生成。
让 LLM 自己判断「有没有敏感信息」既不可复算也不可审计 —— 与
:class:`app.core.threshold.ThresholdEngine` 同源的道理：确定性结论必须来自确定性组件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ============================================================================
# 占位符与正则规则
# ============================================================================
#: 占位符模板（接口 API-63 备注 2 的四种）
PLACEHOLDER_CANDIDATE = "{{CANDIDATE}}"
PLACEHOLDER_PHONE = "{{PHONE}}"
PLACEHOLDER_EMAIL = "{{EMAIL}}"
PLACEHOLDER_ID = "{{ID}}"

#: 手机号（与 parse_pipeline 的抽取规则同源 —— 同一份正则两处用，口径不漂移）
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
#: 邮箱
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#: 身份证（18 位，末位可为 X）
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
#: 占位符本身（rehydrate 后残留检测用）
_PLACEHOLDER_RE = re.compile(r"\{\{(CANDIDATE|PHONE|EMAIL|ID)\}\}")


@dataclass(slots=True)
class VerifyResult:
    """出网前二次扫描的结果（CL-4 ``VerifyResult``）。"""

    passed: bool
    #: 残留的敏感类型（``phone`` / ``email`` / ``id_card`` / ``name``），供日志与拒答原因
    residual: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "residual": list(self.residual)}


class PlaceholderTable:
    """占位符 ↔ 真值映射（**仅存活于本次请求内存**，CL-4 ``PlaceholderTable``）。

    🔴 同一真值在本次请求内**复用同一个 token**：模型的上下文里「李伟」永远渲染成
    同一个 ``{{CANDIDATE}}`` —— 若每次出现都换新 token，模型会以为那是不同的实体，
    回答里的指代关系会被破坏。
    """

    def __init__(self) -> None:
        self._by_value: dict[tuple[str, str], str] = {}  # (类型, 真值) -> 占位符
        self._by_token: dict[str, str] = {}  # 占位符 -> 真值

    def put(self, pii_type: str, value: str | None) -> str | None:
        """登记一个真值，返回其占位符（空值原样返回 ``None``）。"""
        if not value:
            return None
        key = (pii_type, value)
        token = self._by_value.get(key)
        if token is None:
            token = {
                "candidate": PLACEHOLDER_CANDIDATE,
                "phone": PLACEHOLDER_PHONE,
                "email": PLACEHOLDER_EMAIL,
                "id": PLACEHOLDER_ID,
            }.get(pii_type, "{{UNKNOWN}}")
            self._by_value[key] = token
            # 同一占位符只映射最近登记的真值 —— 同类多实体时以先登记者为准
            self._by_token.setdefault(token, value)
        return token

    def resolve(self, token: str) -> str | None:
        """占位符 → 真值；未知 token 返回 ``None``（调用方据此拒答）。"""
        return self._by_token.get(token)

    def tokens(self) -> list[str]:
        return list(self._by_token)

    def values_of_type(self, pii_type: str) -> list[str]:
        """某类型的全部已登记真值（二次扫描「已知人名」用）。"""
        return [v for (t, v) in self._by_value if t == pii_type]

    def to_local_map(self) -> dict[str, str]:
        return dict(self._by_token)


# ============================================================================
# 脱敏 / 回填 / 二次扫描
# ============================================================================
def mask_text(
    text: str | None,
    table: PlaceholderTable,
    *,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    id_card: str | None = None,
) -> str:
    """把 ``text`` 中的明文身份替换为占位符（返回**新字符串**，不改输入）。

    :param table: 本次请求的占位符表（真值登记在这里，回填时用它取回）
    :param name: 该片段所属档案的姓名（→ ``{{CANDIDATE}}``）
    :param phone/email/id_card: 该档案的解密值；拿不到解密值（如 AES 未配置）就传
        ``None`` —— 此时该类 PII **本来就以脱敏形态存储**，正则扫描兜底。

    替换顺序：先精确（已知姓名/手机/邮箱/证件），后兜底正则（扫出残余 PII 也换掉）——
    兜底换出的真值同样登记进 ``table``，回填时能还原。
    """
    if not text:
        return ""
    out = text
    if name:
        out = out.replace(name, table.put("candidate", name) or name)
    if phone:
        out = out.replace(phone, table.put("phone", phone) or phone)
    if email:
        out = out.replace(email, table.put("email", email) or email)
    if id_card:
        out = out.replace(id_card, table.put("id", id_card) or id_card)

    # 兜底：精确替换后残余的 PII（其他人的手机号出现在项目描述里等）
    for m in _PHONE_RE.finditer(out):
        raw = m.group(0)
        out = out.replace(raw, table.put("phone", raw) or raw)
    for m in _EMAIL_RE.finditer(out):
        raw = m.group(0)
        out = out.replace(raw, table.put("email", raw) or raw)
    for m in _ID_CARD_RE.finditer(out):
        raw = m.group(0)
        out = out.replace(raw, table.put("id", raw) or raw)
    return out


def verify_outbound(
    text: str | None,
    *,
    known_names: list[str] | None = None,
) -> VerifyResult:
    """出网前二次扫描（CL-4 ``OutboundGuard.check``，**确定性**）。

    检查四类残留：手机号 / 邮箱 / 身份证（正则）+ **已知人名**（提问范围内档案的
    真实姓名 —— 正则扫不出人名，只能拿「本次请求涉及的真值」反向找）。

    :return: ``passed=False`` 时 ``residual`` 给出命中的类型 —— 调用方**必须拒答**
        （``refusal reason=NER_RESIDUAL``），绝不降级为明文出网。
    """
    residual: list[str] = []
    if not text:
        return VerifyResult(passed=True)
    if _PHONE_RE.search(text):
        residual.append("phone")
    if _EMAIL_RE.search(text):
        residual.append("email")
    if _ID_CARD_RE.search(text):
        residual.append("id_card")
    for name in known_names or []:
        if name and name in text:
            residual.append("name")
            break
    return VerifyResult(passed=not residual, residual=residual)


def rehydrate(text: str | None, table: PlaceholderTable) -> str | None:
    """占位符 → 真值（**服务端本地回填**，不经任何外部服务）。

    :return: 回填后的文本；存在**无法解析的占位符**时返回 ``None`` ——
        调用方必须拒答（``MASK_REQUIRED``），❌ 严禁把「解析不了」的占位符
        原样下发（那会把内部标记暴露给前端），更严禁降级为明文出网。
    """
    if not text:
        return text
    out = text
    for token, value in table.to_local_map().items():
        out = out.replace(token, value)
    if _PLACEHOLDER_RE.search(out):
        return None  # 还有解析不了的占位符 → 回填失败
    return out
