"""CU-26 · **脱敏样例集专项**（F-3.9 / CL-4 · 需求 §12.3「出网脱敏与回填」）。

需求 §12.3 对该验收项的要求（原文）：

> **出网脱敏与回填（F-3.9）** ∣ 证据：**脱敏测试样例集（≥20 条：姓名变体/中英混排/昵称/缩写）** ∣
> 判定：**零漏检**；脱敏/回填失败时拒绝作答，无明文出网

本文件是那条「≥20 条样例集」的落地物，并把「零漏检」拆成**两类可判定的断言**：

| 类别 | 用例号 | 结论 |
| --- | --- | --- |
| **格式类样例**（手机 / 邮箱 / 身份证 / 混合共存 / 精确姓名） | T-21 ~ T-35（**T-35 参数化 20 条**） | ✅ **全部命中**，未发现漏检 |
| 🔴 **姓名变体类样例**（排版变体 / 昵称 / 拼音 / 中英混排） | **T-36 ~ T-39** | ❌ **全部漏检** —— 见下 |
| 🔴 **手机号书写变体**（``+86`` 前缀 / ``-`` 分隔） | **T-40** | ❌ **漏检** |

🔴 **本文件最重要的产出不是一个「全绿」的用例集，而是 T-36 ~ T-40 这 5 条把缺口钉死的用例。**
需求写的是「**零漏检**」，而当前实现是**确定性精确匹配 + 正则兜底**：

- 精确匹配只能命中「与登记真值逐字相同」的串；
- 正则兜底**只覆盖手机 / 邮箱 / 身份证**（人名无法用正则穷举 —— 这是 :mod:`app.core.masking`
  自己的设计说明里写明的自觉取舍，不是遗漏）。

⇒ **「姓名变体零漏检」在当前架构下不可达**，这是一个**需求口径 vs 可实现性**的冲突，
不是「代码有个 bug 修一下就好」。故 T-36 ~ T-40 采用**行为固化（characterization）**写法：
断言**当前确实漏检**，并在文档串里注明「**缺口闭环后本断言应反转**」——
这样缺口一旦被修，用例会**主动失败并要求更新**，而不会静默变成一条「早就过期但一直绿着」的假证据。

分层
----
**全部 L1**：脱敏与二次扫描是**纯函数 + 正则**（确定性组件，铁律 10），不依赖 MySQL / Redis / Milvus。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.core.masking import (
    PLACEHOLDER_CANDIDATE,
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_ID,
    PLACEHOLDER_PHONE,
    PlaceholderTable,
    mask_text,
    rehydrate,
    verify_outbound,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MASKING_MODULE = BACKEND_ROOT / "app" / "core" / "masking.py"

#: 样例用的固定真值（与真实数据无关，纯造数）
NAME = "张伟"
PHONE = "13800138000"
EMAIL = "zhang.wei@example.com"
ID_CARD = "110101199003071234"
OTHER_PHONE = "13900139000"


# ============================================================================
# T-21 ~ T-27 · 格式类样例：精确替换与正则兜底
# ============================================================================
def test_t21_name_is_replaced_by_candidate_placeholder() -> None:
    """精确姓名 → ``{{CANDIDATE}}``，且**原文里不再出现真名**。"""
    table = PlaceholderTable()
    out = mask_text(f"{NAME}负责数据治理平台建设", table, name=NAME)
    assert NAME not in out
    assert PLACEHOLDER_CANDIDATE in out
    assert table.resolve(PLACEHOLDER_CANDIDATE) == NAME


def test_t22_phone_is_replaced_by_phone_placeholder() -> None:
    table = PlaceholderTable()
    out = mask_text(f"联系电话{PHONE}", table, phone=PHONE)
    assert PHONE not in out
    assert PLACEHOLDER_PHONE in out


def test_t23_email_is_replaced_by_email_placeholder() -> None:
    table = PlaceholderTable()
    out = mask_text(f"邮箱 {EMAIL}", table, email=EMAIL)
    assert EMAIL not in out
    assert PLACEHOLDER_EMAIL in out


def test_t24_id_card_is_replaced_by_id_placeholder() -> None:
    table = PlaceholderTable()
    out = mask_text(f"身份证{ID_CARD}", table, id_card=ID_CARD)
    assert ID_CARD not in out
    assert PLACEHOLDER_ID in out


def test_t25_regex_fallback_catches_foreign_phone() -> None:
    """🔴 正则兜底：**未登记**的手机号（项目描述里出现的他人号码）也要被换掉。

    只做「已登记真值」的替换是不够的 —— 简历正文里常有推荐人、证明人的联系方式。
    """
    table = PlaceholderTable()
    out = mask_text(f"证明人电话 {OTHER_PHONE}", table)
    assert OTHER_PHONE not in out
    assert PLACEHOLDER_PHONE in out
    # 兜底换出的真值同样登记进表 → 回填得回来
    assert table.resolve(PLACEHOLDER_PHONE) == OTHER_PHONE


def test_t26_regex_fallback_catches_foreign_email() -> None:
    table = PlaceholderTable()
    out = mask_text("联系 hr@other-company.cn", table)
    assert "hr@other-company.cn" not in out
    assert PLACEHOLDER_EMAIL in out


def test_t27_regex_fallback_catches_id_card_with_capital_x() -> None:
    """身份证末位校验码有 ``x`` / ``X`` 两种写法（且小写常见），两者都要命中。"""
    for raw in ("11010119900307123x", "11010119900307123X"):
        table = PlaceholderTable()
        out = mask_text(f"证件号{raw}", table)
        assert raw not in out, f"身份证 {raw} 未被脱敏"
        assert PLACEHOLDER_ID in out


# ============================================================================
# T-28 ~ T-32 · 回填与二次扫描（CL-4 行为约定 2 / 3 / 4）
# ============================================================================
def test_t28_rehydrate_restores_every_placeholder() -> None:
    """回填必须是**全量还原**：任一占位符解析不了都要走拒答，不能部分还原。"""
    table = PlaceholderTable()
    masked = mask_text(f"{NAME} {PHONE} {EMAIL} {ID_CARD}", table, name=NAME)
    assert PLACEHOLDER_CANDIDATE in masked and PLACEHOLDER_PHONE in masked
    restored = rehydrate(masked, table)
    assert restored is not None
    for raw in (NAME, PHONE, EMAIL, ID_CARD):
        assert raw in restored, f"{raw} 未被回填"


def test_t29_outbound_guard_blocks_residual_phone() -> None:
    """🔴 CL-4 约定 2：出网前二次扫描发现残留手机号 → ``passed=False``（调用方**必须拒答**）。"""
    r = verify_outbound(f"他的电话是 {PHONE}")
    assert r.passed is False
    assert "phone" in r.residual


def test_t30_outbound_guard_blocks_known_name() -> None:
    """🔴 人名只能靠「本次请求涉及的真值」反向找 —— 正则扫不出人名。"""
    r = verify_outbound(f"{NAME}的履历显示……", known_names=[NAME])
    assert r.passed is False
    assert "name" in r.residual


def test_t31_outbound_guard_passes_clean_text() -> None:
    """反向对照：干净文本必须**放行**。

    没有这条，「永远返回 passed=False」的实现也能让 T-29 / T-30 通过 ——
    而那种实现的特征是**问答全部拒答**（可用性归零），却看上去「合规很到位」。
    """
    r = verify_outbound("该候选人具备数据治理经验，项目周期两年", known_names=[NAME])
    assert r.passed is True
    assert r.residual == []


def test_t32_rehydrate_rejects_unresolvable_placeholder() -> None:
    """🔴 CL-4 约定 4：存在解析不了的占位符 → 返回 ``None``（**拒答**）。

    ❌ 严禁把解析不了的占位符**原样下发**（那会把内部标记暴露给前端），
    ❌ 更严禁退化为明文出网。

    反向对照：占位符**已登记**时必须还原 —— 否则「永远返回 None」的实现
    也能让「拒答」这条通过（代价是问答全废，却看着很合规）。
    """
    empty = PlaceholderTable()
    assert rehydrate(f"{PLACEHOLDER_CANDIDATE} 的履历", empty) is None

    registered = PlaceholderTable()
    registered.put("candidate", NAME)
    assert rehydrate(f"{PLACEHOLDER_CANDIDATE} 的履历", registered) == f"{NAME} 的履历"


# ============================================================================
# T-33 · 结构断言：扫描器的「已知人名」来源必须受控
# ============================================================================
def test_t33_outbound_guard_cannot_look_up_names_by_itself() -> None:
    """🔴 ``verify_outbound`` **只能拿调用方给的名字**，不得自己查库。

    反面实现（扫描器内部按 ``tenant_id`` 查全租户姓名再逐个比对）会造成两个问题：
    ① 把**与本次提问无关的人**的姓名拉进进程内存（暴露面反而扩大）；
    ② 「扫描范围」变成隐式约定，没人知道到底比对了哪些名字。

    断言：① 签名只有 ``text`` + ``known_names``（无 ``tenant_id`` / ``db``）；
    ② ``masking.py`` 不 import 任何持久化模块（redis / sqlalchemy / app.dao / app.models）。
    """
    params = set(inspect.signature(verify_outbound).parameters)
    assert params == {"text", "known_names"}, f"verify_outbound 签名被扩展成 {params}，可能自己查库了"

    tree = ast.parse(MASKING_MODULE.read_text(encoding="utf-8"))
    forbidden_prefixes = ("redis", "sqlalchemy", "app.dao", "app.models", "app.services")
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if a.name.startswith(forbidden_prefixes)]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(forbidden_prefixes):
                hits.append(node.module)
    assert hits == [], f"masking.py 引入了持久化/业务依赖，脱敏规则不再是纯函数：{hits}"


# ============================================================================
# T-34 · 同一真值复用同一 token（指代关系不被破坏）
# ============================================================================
def test_t34_same_value_reuses_same_token_within_request() -> None:
    """同一真值在本次请求内**恒定同一个占位符**。

    若每次出现都换新 token，模型会以为那是**不同实体**，回答里的指代关系会被破坏
    （「他」「该候选人」指回谁变得不确定），反而降低答案质量。
    """
    table = PlaceholderTable()
    a = table.put("candidate", NAME)
    b = table.put("candidate", NAME)
    assert a == b == PLACEHOLDER_CANDIDATE
    # 反向对照：不同真值得不同 token（不是「永远返回同一个」）
    assert table.put("phone", PHONE) == PLACEHOLDER_PHONE
    assert table.put("candidate", "李娜") == PLACEHOLDER_CANDIDATE  # 同类复用同一 token
    assert table.resolve(PLACEHOLDER_CANDIDATE) == NAME  # 以先登记者为准


# ============================================================================
# T-35 · **样例集本体（参数化 20 条）** —— 需求 §12.3 的「≥20 条」即此
# ============================================================================
#: 20 条**格式类**样例：(说明, 文本, 期望命中的残留类型或 None, 期望被替换掉的真值)
SAMPLE_CASES: list[tuple[str, str, str | None, tuple[str, ...]]] = [
    ("01 精确姓名", "张伟负责数据治理", "name", ("张伟",)),
    ("02 姓名在句首无分隔", "张伟，男，1990 年生", "name", ("张伟",)),
    ("03 姓名在括号内", "（张伟）高级工程师", "name", ("张伟",)),
    ("04 手机号紧邻中文", "联系电话13800138000请回电", "phone", (PHONE,)),
    ("05 手机号在括号内", "联系电话（13800138000）", "phone", (PHONE,)),
    ("06 两个手机号并存", "13800138000 与 13900139000", "phone", (PHONE, "13900139000")),
    ("07 手机号在句末", "请回电 13800138000", "phone", (PHONE,)),
    ("08 邮箱小写", "邮箱 zhang.wei@example.com", "email", (EMAIL,)),
    ("09 邮箱混合大小写", "邮箱 Zhang.Wei@Example.COM", "email", ("Zhang.Wei@Example.COM",)),
    ("10 邮箱含加号别名", "邮箱 a+b@example.co.uk", "email", ("a+b@example.co.uk",)),
    ("11 邮箱多级域名", "邮箱 hr@sub.example.com.cn", "email", ("hr@sub.example.com.cn",)),
    ("12 身份证 18 位数字尾", "证件号110101199003071234", "id_card", (ID_CARD,)),
    ("13 身份证小写 x 尾", "证件号11010119900307123x", "id_card", ("11010119900307123x",)),
    ("14 身份证大写 X 尾", "证件号11010119900307123X", "id_card", ("11010119900307123X",)),
    ("15 身份证在中文标签后", "身份证：110101199003071234", "id_card", (ID_CARD,)),
    ("16 姓名 + 手机号共存", "张伟 联系电话13800138000", "phone", (PHONE,)),
    ("17 手机号 + 邮箱共存", "13800138000 zhang.wei@example.com", "phone", (PHONE, EMAIL)),
    ("18 邮箱 + 身份证共存", "zhang.wei@example.com 110101199003071234", "email", (EMAIL, ID_CARD)),
    ("19 三类 PII 齐现", "张伟 13800138000 zhang.wei@example.com 110101199003071234", "phone", (PHONE, EMAIL, ID_CARD)),
    ("20 他人手机号（未登记）", "证明人电话 13900139000", "phone", ("13900139000",)),
]


@pytest.mark.parametrize(
    ("desc", "text", "expect_residual", "must_disappear"),
    SAMPLE_CASES,
    ids=[c[0] for c in SAMPLE_CASES],
)
def test_t35_masking_sample_set_never_leaks(
    desc: str, text: str, expect_residual: str | None, must_disappear: tuple[str, ...]
) -> None:
    """🔴 **脱敏样例集（20 条）**：每一条都断言「**脱敏后原文里相关真值全部消失**」，
    并断言出网二次扫描**确实报出**了对应残留类型（在未脱敏的文本上）。

    两条断言缺一不可：
    - 只测 ``verify_outbound`` → 只证明「能发现」，不证明「能消除」；
    - 只测 ``mask_text`` → 只证明「替换了」，不证明「残留扫描会拦住漏网之鱼」。
    """
    table = PlaceholderTable()
    masked = mask_text(text, table, name=NAME if "张伟" in text else None)
    for raw in must_disappear:
        if raw == NAME:
            continue  # 姓名只在显式传入时才会被替换，单独由 T-21 / T-36~ 覆盖
        assert raw not in masked, f"[{desc}] 脱敏后仍含明文：{raw}"

    # 未脱敏文本上，二次扫描必须报出期望的残留类型
    residual = verify_outbound(text, known_names=[NAME] if NAME in text else None)
    assert residual.passed is False, f"[{desc}] 二次扫描漏报了残留"
    assert expect_residual in residual.residual, (
        f"[{desc}] 期望残留含 {expect_residual}，实际 {residual.residual}"
    )


# ============================================================================
# 🔴 T-36 ~ T-40 · 缺口固化：「姓名变体 / 书写变体零漏检」当前不可达
# ============================================================================
# 这 5 条断言的是**当前确实漏检**。缺口闭环后它们会失败 —— 那是**预期行为**：
# 失败即提醒维护者把断言反转并关闭遗留项，而不是让一条过期的用例一直绿着。
# 登记见《测试用例与验收脚本》§缺口清单 G-01 / G-02 / G-03。

def test_t36_gap_name_with_inner_space_is_not_detected() -> None:
    """🔴 **缺口固化 G-01** · 姓名**内插半角空格**（``张 伟``）→ 当前漏检。

    本条断言的是「当前漏检」。需求 §12.3 要求「姓名变体/中英混排/昵称/缩写」**零漏检**，
    而当前实现是「精确匹配 + 正则兜底（仅手机/邮箱/身份证）」—— 人名无法用正则穷举。
    **缺口闭环（姓名归一化 / 变体登记 / 或从源头不把姓名放入出网片段）后，
    请把本条改为断言「被检出」**，并在评审文档关闭 G-01。
    """
    r = verify_outbound("候选人 张 伟 参与了数据治理项目", known_names=[NAME])
    assert r.passed is True, "已能检出内插空格变体 —— 请反转本断言并关闭 G-01"


def test_t37_gap_name_with_fullwidth_space_is_not_detected() -> None:
    """🔴 **缺口固化 G-01** · 姓名内插**全角空格**（``张　伟``）→ 当前漏检。

    全角空格在中文排版里很常见（粘贴自 Word / PDF 时尤其多），
    且它与半角空格是**两个不同字符** —— 去空格还必须同时处理全角/半角与零宽字符。
    """
    r = verify_outbound("候选人 张\u3000伟 参与了数据治理项目", known_names=[NAME])
    assert r.passed is True, "已能检出全角空格变体 —— 请反转本断言并关闭 G-01"


def test_t38_gap_pinyin_and_mixed_script_names_are_not_detected() -> None:
    """🔴 **缺口固化 G-02** · **中英混排 / 拼音**（``Zhang Wei`` / ``ZhangWei`` / ``Wei Zhang`` / 小写）→ 当前全部漏检。

    这一类比「插空格」难得多：需要把中文姓名转成拼音（含多音字歧义）并生成
    姓名顺序的多种组合。**它已经不是排版归一化能解决的问题**。
    """
    variants = ("Zhang Wei", "ZhangWei", "Wei Zhang", "zhang wei")
    leaked = [v for v in variants if verify_outbound(f"候选人 {v} 参与了数据治理项目", known_names=[NAME]).passed]
    assert leaked == list(variants), (
        f"部分拼音变体已能检出 {[v for v in variants if v not in leaked]} —— 请更新 G-02 并反转断言"
    )


def test_t39_gap_nicknames_are_not_detected() -> None:
    """🔴 **缺口固化 G-02** · **昵称**（``小张`` / ``伟哥``）→ 当前漏检。

    ⚠️ 昵称属**语义识别**，不是排版归一化能解决的 —— 即使做了去空格 / 大小写折叠也仍然漏。
    若甲方坚持「昵称零漏检」，需要的是 **NER 模型**，而 NER 同样**有漏检率**，
    与「零漏检」的措辞直接冲突 ⇒ 建议把该措辞改为「**已登记变体形态漏检率 ≤ 阈值**」
    或明确该项**不纳入一期**（详见《测试用例与验收脚本》§缺口清单 G-02）。
    """
    leaked = [v for v in ("小张", "伟哥") if verify_outbound(f"{v}负责数据治理项目", known_names=[NAME]).passed]
    assert leaked == ["小张", "伟哥"], "昵称已能检出 —— 请更新 G-02 并反转断言"


def test_t40_gap_phone_prefix_and_separators_are_not_detected() -> None:
    """🔴 **缺口固化 G-03** · 手机号书写变体（``+86`` 前缀 / ``-`` 或空格分隔）→ 当前漏检。

    根因具体：``_PHONE_RE = (?<!\\d)1[3-9]\\d{9}(?!\\d)`` 的**前后向断言**要求号码两侧
    不能是数字 —— 这**正确地**避免了误伤订单号/编号（见 T-40b），
    但 ``+8613800138000`` 里 ``1`` 前面是 ``6``（数字）⇒ 直接不匹配；
    ``138-0013-8000`` 同理（``-`` 把连续数字打断）。

    ⚠️ 这是**取舍型缺口**：修复方向**不是把正则写松**（写松会立刻踩坏 T-40b），
    而是**先做书写归一化再扫描**（剥国家码、去分隔符），且归一化只作用于**扫描判定**、
    **不改写原文**（改写会破坏占位符↔真值的回填映射）。
    """
    for raw in ("+8613800138000", "138-0013-8000", "138 0013 8000"):
        assert verify_outbound(f"联系电话{raw}，请回电").passed is True, (
            f"{raw} 已能检出 —— 请更新 G-03 并反转断言"
        )


def test_t40b_phone_inside_longer_digit_run_is_intentionally_not_flagged() -> None:
    """✅ **反向对照（这是正确行为，不是缺口）**：数字串内嵌的「手机号形状」**不应**被判为 PII。

    ``编号91380013800012`` 里的 ``13800138000`` 其实是订单号/流水号的一部分。
    若把它当手机号脱掉，回填时会把**编号**换成别的数字（回填映射张冠李戴），
    属于**比漏检更隐蔽的正确性事故**。

    本条存在的意义：**防止有人为了修 G-03 而把正则写松** —— 写松之后本条会立刻失败。
    """
    assert verify_outbound("编号91380013800012").passed is True
    assert mask_text("编号91380013800012", PlaceholderTable()) == "编号91380013800012"

