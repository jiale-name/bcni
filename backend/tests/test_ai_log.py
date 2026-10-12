"""CU-12 · AI 调用日志与成本看板（API-88 / API-89，F-9.5，CL-7）。

分层
----
- **L1 契约层**（不依赖 MySQL / Redis）：区间解析（默认 7 天 / 上限 90 天）、
  ``_resolve_deployment`` 三态、筛选模型的空串与白名单、``MoneyStr`` 序列化、
  **``AiCallLogOut`` 结构上不含业务明文**、模型「只追加 + 复合主键」、端点在 OpenAPI 中的登记、
  **脱敏率公式与两条告警的组合逻辑**（把 DAO 打桩，不碰数据库）。
- **L2 集成层**（缺依赖时 skip 而非 fail）：写入 → 列表 → 汇总 → 三维度看板的端到端、
  租户隔离、权限矩阵、``deployment`` 口径真的落库并被读回。

🔴 本文件守护的两条合规口径
-----------------------
| 口径 | 破了会怎样 | 对应用例 |
| --- | --- | --- |
| 脱敏率**分母 = ``deployment='cloud'`` 的调用** | 把本地调用算进分母 → 指标长期 0.6~0.8 → 「低于 1.0 即告警」形同虚设（狼来了） | ``test_mask_rate_denominator_is_cloud_calls_only`` |
| ``scene_stage`` 取**本次调用实际部署位置** | 云端主路失败落本地备用时被打上「云端但未脱敏」的**假阳性**合规告警 | ``test_scene_stage_follows_deployment_not_scene`` |

每条「拒绝」用例都配一条**反向对照**（同依赖在应放行时确实放行）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.core.security import SecurityUtils
from app.dao import ai_dao
from app.exceptions import BizError, ErrorCode
from app.models.ai import AiCallLog, PromptTemplate
from app.models.enums import RoleDataScope
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.ai import (
    AI_CALL_DEFAULT_RANGE_DAYS,
    AI_CALL_MAX_RANGE_DAYS,
    AI_COST_DEFAULT_RANGE_DAYS,
    AiCallFilterRequest,
    AiCallLogOut,
    AiCallSummary,
    AiDeployment,
    AiScene,
    call_log_page,
)
from app.services.ai_log_service import AiLogService, _dec, _resolve_deployment, clip
from tests.conftest import db_available

PWD = "Admin@123456"

#: 接口 §2.2 里 API-88 / API-89 的权限码
LOG_PERM = "ai:log:view"
COST_PERM = "ai:cost:view"
AI_PERMS = (LOG_PERM, COST_PERM)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# L1 · 「出网事实」的三态解析
# ============================================================================
@pytest.mark.parametrize(
    ("scene", "declared", "expected"),
    [
        # 调用方声明永远优先 —— 它知道这一路到底走了哪个模型
        ("talent_ask", AiDeployment.LOCAL, AiDeployment.LOCAL),
        ("resume_parse", AiDeployment.CLOUD, AiDeployment.CLOUD),
        # 缺省按场景固有形态兜底（合理默认，对混合主备链上的偶发降级不准确）
        ("talent_ask", None, AiDeployment.CLOUD),
        ("resume_parse", None, AiDeployment.LOCAL),
        # 连场景都不认识 → 不猜（该记录不进脱敏率分母）
        ("unknown_scene", None, None),
    ],
)
def test_resolve_deployment(scene: str, declared: str | None, expected: str | None) -> None:
    """``deployment`` 的确定顺序：**调用方声明 > 场景固有形态 > ``None``**。

    🔴 为什么「场景固有形态」只能当兜底、不能当依据：
    混合主备链上（``talent_ask`` 云端主路 + 本地兜底）主路挂掉时这次调用
    **其实没有出网**，按场景猜就会得到一个「云端但未脱敏」的假阳性。
    """
    assert _resolve_deployment(scene, declared) == expected


def test_resolve_deployment_clips_to_column_width() -> None:
    """``deployment`` 列宽 16 —— 超长声明必须被截断（不能让 INSERT 报 1406）。"""
    assert len(_resolve_deployment("talent_ask", "cloud" * 10) or "") == 16


def test_ai_scene_stage_and_label_degrade_for_unknown_values() -> None:
    """历史日志里可能有**已废弃场景** —— 读取侧必须降级（返回 ``None``）而不是抛异常。

    日志表要能读十年前的数据；用 Enum 会在遇到废弃值时让整页列表反序列化失败。
    """
    assert AiScene.label_of("unknown_scene") is None
    assert AiScene.stage_of("unknown_scene") is None
    assert AiScene.label_of(None) is None
    assert AiScene.label_of("talent_ask") == "档案 RAG 问答"


# ============================================================================
# L1 · 区间解析（默认 7 天 / 上限 90 天）
# ============================================================================
def test_range_defaults_to_seven_days_from_request_time() -> None:
    """缺省区间 = **请求时刻**往前 7 天（API-88）/ 30 天（API-89）。

    ⚠️ 默认值刻意**不在 Schema 里**用 ``default_factory`` 算：
    模型若被缓存或跨日复用，区间会固定在「导入时刻的 N 天前」，
    表现为「今天的调用查不到」——非常隐蔽。故由服务层按请求时刻计算。
    """
    filters = AiCallFilterRequest()
    # 刻意把「现在」往前挪一点：不给边界留任何解释空间
    start, end = AiLogService.resolve_range(filters, default_days=AI_CALL_DEFAULT_RANGE_DAYS)
    assert (end - start).days == AI_CALL_DEFAULT_RANGE_DAYS
    assert abs((datetime.now() - end).total_seconds()) < 60, "end 应≈请求时刻"

    start2, end2 = AiLogService.resolve_range(filters, default_days=AI_COST_DEFAULT_RANGE_DAYS)
    assert (end2 - start2).days == AI_COST_DEFAULT_RANGE_DAYS
    assert AI_COST_DEFAULT_RANGE_DAYS > AI_CALL_DEFAULT_RANGE_DAYS, "看板区间应比日志区间长"


def test_range_rejects_span_over_90_days_with_document_shape() -> None:
    """🔴 跨度超 90 天 → **40001**，且 ``data`` 带 ``max_days`` / ``requested_days``。

    :data:`~app.schemas.ai.AI_CALL_MAX_RANGE_DAYS` 的理由是本表**按月分区**：
    跨月区间无法被分区裁剪，放开时间窗会让单次查询退化为全分区扫描。

    ⚠️ 校验刻意放在**服务层**而不是 Pydantic 校验器：接口 §3 要求失败响应带
    ``data.max_days``/``requested_days``，而 Pydantic 失败走全局处理器时
    ``data`` 里只有 ``fields[]`` —— 形状对不上文档。
    """
    end = datetime.now()
    filters = AiCallFilterRequest(
        start_time=end - timedelta(days=AI_CALL_MAX_RANGE_DAYS + 1), end_time=end
    )
    with pytest.raises(BizError) as exc:
        AiLogService.resolve_range(filters, default_days=AI_CALL_DEFAULT_RANGE_DAYS)

    assert exc.value.code == int(ErrorCode.PARAM_INVALID)
    assert exc.value.data["max_days"] == AI_CALL_MAX_RANGE_DAYS
    assert exc.value.data["requested_days"] == AI_CALL_MAX_RANGE_DAYS + 1


def test_range_accepts_exactly_90_days() -> None:
    """反向对照：恰好 90 天必须放行（边界不能「差一天就拒」）。"""
    end = datetime.now()
    start = end - timedelta(days=AI_CALL_MAX_RANGE_DAYS)
    resolved_start, resolved_end = AiLogService.resolve_range(
        AiCallFilterRequest(start_time=start, end_time=end),
        default_days=AI_CALL_DEFAULT_RANGE_DAYS,
    )
    assert resolved_start == start and resolved_end == end


def test_filter_model_rejects_reversed_range() -> None:
    """起止颠倒 → 校验失败（在契约层挡住，不进服务层）。"""
    now = datetime.now()
    with pytest.raises(ValidationError):
        AiCallFilterRequest(start_time=now, end_time=now - timedelta(days=1))


def test_filter_model_treats_blank_strings_as_absent() -> None:
    """``?scene=&model=`` （前端清空筛选后的常见形态）必须视为**未筛选**。

    否则会查出 0 条并让人以为「这段时间没有调用」——而真相是筛选条件里
    混进了一个空字符串。
    """
    filters = AiCallFilterRequest(scene="  ", model="", trace_id="   ")
    assert filters.scene is None and filters.model is None and filters.trace_id is None


def test_filter_model_rejects_unknown_scene() -> None:
    """场景码白名单校验（拼错 → 明确报错，而不是「查得到 0 条」）。"""
    with pytest.raises(ValidationError):
        AiCallFilterRequest(scene="talen_ask")
    assert AiCallFilterRequest(scene="talent_ask").scene == "talent_ask"


# ============================================================================
# L1 · 脱敏率公式与两条告警（把 DAO 打桩，不碰数据库）
# ============================================================================
def _stub_summary(
    monkeypatch: pytest.MonkeyPatch,
    *,
    calls: int,
    cost: str,
    cloud_calls: int,
    masked: int,
    failed: int = 0,
) -> None:
    """把 ``ai_dao`` 的两个聚合函数打桩 —— 本用例只验证**服务层的组合逻辑**。"""
    monkeypatch.setattr(
        ai_dao,
        "aggregate_call_summary",
        lambda *a, **k: {
            "calls": calls,
            "cost_total_yuan": Decimal(cost),
            "avg_latency_ms": 100,
            "cache_hit_rate": 0.5,
            "failed": failed,
        },
    )
    monkeypatch.setattr(
        ai_dao, "aggregate_outbound_mask", lambda *a, **k: (cloud_calls, masked)
    )


def test_mask_rate_denominator_is_cloud_calls_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 **本文件最重要的一条**：分母只含 ``deployment='cloud'`` 的调用。

    5 次调用里只有 4 次出网、其中 3 次脱敏 → 脱敏率 = 3/4 = 0.75，**不是** 3/5。
    把本地调用算进分母会让指标长期显著小于 1.0，于是
    「低于 1.0 即告警」这条合规监控天天报警，最终被运维学会忽略 —— 那才是真正的风险。
    """
    _stub_summary(monkeypatch, calls=5, cost="0.5", cloud_calls=4, masked=3)
    summary = AiLogService.summary(None, 1, start=datetime.now(), end=datetime.now())  # type: ignore[arg-type]

    assert summary.cloud_calls == 4, "分母必须显式下发（否则前端不知道 1.0 是在几个调用上算的）"
    assert summary.mask_applied_rate == 0.75
    assert summary.mask_alert is True, "0.75 < 1.0 → 存在未脱敏出网调用 = 合规事件"
    assert summary.calls == 5, "总数仍按全部调用统计（两个口径并存，各有各的用途）"


def test_mask_rate_is_one_when_no_cloud_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 没有出网调用 → 脱敏率记 **1.0**（「没有出网」= 没有违规），且**不告警**。

    若记 0.0 或 ''，全本地部署的租户会永久告警；若记 0.0 又配上「无出网不告警」，
    就会和「真的 0% 脱敏」在数值上无法区分。
    """
    _stub_summary(monkeypatch, calls=9, cost="0", cloud_calls=0, masked=0)
    summary = AiLogService.summary(None, 1, start=datetime.now(), end=datetime.now())  # type: ignore[arg-type]

    assert summary.mask_applied_rate == 1.0
    assert summary.mask_alert is False


def test_cost_alert_marks_but_does_not_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cost_alert``：超阈值只**标记**（接口 §3 备注「本接口只呈报不告警」）。

    阈值取配置（``AI_COST_ALERT_YUAN`` 默认 100 元）—— 断言两条方向相反的用例，
    否则一个恒为 ``True`` 或恒为 ``False`` 的实现也能过。
    """
    from app.config import settings

    over = str(float(settings.AI_COST_ALERT_YUAN) + 1)
    _stub_summary(monkeypatch, calls=1, cost=over, cloud_calls=0, masked=0)
    assert AiLogService.summary(None, 1, start=datetime.now(), end=datetime.now()).cost_alert is True  # type: ignore[arg-type]

    _stub_summary(monkeypatch, calls=1, cost="0.01", cloud_calls=0, masked=0)
    assert AiLogService.summary(None, 1, start=datetime.now(), end=datetime.now()).cost_alert is False  # type: ignore[arg-type]


def test_summary_rounds_mask_rate_to_four_places(monkeypatch: pytest.MonkeyPatch) -> None:
    """比率量化到 4 位 —— 前端展示 ``0.6667``，而不是 ``0.6666666666666666``。"""
    _stub_summary(monkeypatch, calls=3, cost="0", cloud_calls=3, masked=2)
    summary = AiLogService.summary(None, 1, start=datetime.now(), end=datetime.now())  # type: ignore[arg-type]
    assert summary.mask_applied_rate == 0.6667


def test_aggregate_outbound_mask_requires_deployment_argument() -> None:
    """🔴 ``deployment`` 是**必填关键字参数** —— 让「口径」在调用点被写明。

    若给它一个默认值（如 ``'cloud'``），调用方就可以「不假思索地调用」，
    而哪天口径要改（或多口径并存）时，所有调用点都会**静默沿用旧口径**。
    必填等价于「每次调用都得回答：你说的出网指哪种」。

    顺带钉死分层：``deployment`` 是**字符串**而不是 ``AiDeployment`` 常量类 ——
    ``ai_dao`` 不得 import ``schemas``（dao 在 schemas 下层，反向依赖会破坏六层架构）。
    """
    import inspect

    params = inspect.signature(ai_dao.aggregate_outbound_mask).parameters
    assert "deployment" in params
    assert params["deployment"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["deployment"].default is inspect.Parameter.empty, "口径不能有默认值"
    assert params["deployment"].annotation in (str, "str")

    source = inspect.getsource(ai_dao)
    assert "from app.schemas" not in source, "dao 不得 import schemas（六层架构：依赖只能向下）"


# ============================================================================
# L1 · 契约形状：AIOPS 只能看元数据，看不到业务明文
# ============================================================================
def test_call_log_out_has_no_business_plaintext_fields() -> None:
    """🔴 ``AiCallLogOut`` **结构上**不含任何档案内容 / 问题原文 / Prompt 正文。

    接口 §3 备注：AIOPS 只能看元数据与日志，**不含业务数据明文**。
    「结构上不存在」比「记得别返回」可靠得多 —— 后者会在某次「顺手补个字段」时失守。
    """
    fields = set(AiCallLogOut.model_fields)
    assert fields, "契约定义丢了"
    forbidden = (
        "content", "prompt", "question", "answer", "text", "resume", "raw",
        "input", "output", "body",
    )
    offenders = [
        name
        for name in fields
        if any(word in name for word in forbidden)
        # ``prompt_id`` / ``prompt_version`` / ``prompt_code`` 是**元数据**（哪个模板的哪一版），
        # 不是正文 —— 「追溯得到模板」正是 F-9.2 要的，故显式豁免。
        and name not in {"prompt_id", "prompt_version", "prompt_code"}
    ]
    assert offenders == [], f"AiCallLogOut 出现了可能承载业务明文的字段：{offenders}"
    assert {"tokens_in", "tokens_out", "cost_yuan", "latency_ms", "mask_applied"} <= fields


def test_money_is_serialised_as_string_with_four_decimals() -> None:
    """金额下发为**字符串 + 4 位小数**（接口 §3 的 ``"0.0421"``）。

    用 float 会引入二进制误差，前端再累加就出现「合计对不上」；
    而 6 位小数会让界面上一堆无意义的尾数（DB 存 ``DECIMAL(12,6)`` 是为了累加精度）。
    """
    payload = AiCallLogOut(
        id="1",
        cost_yuan=Decimal("0.042100"),
        tokens_in=1,
        tokens_out=2,
        status=1,
        created_at=datetime.now(),
    ).model_dump(mode="json")
    assert payload["cost_yuan"] == "0.0421"
    assert isinstance(payload["cost_yuan"], str)
    assert payload["id"] == "1", "CR-5：ID 一律字符串"


def test_call_log_page_puts_summary_at_top_level() -> None:
    """🔴 ``summary`` 必须与 ``page`` **同级**，而 ``data`` 只放数组（接口 §1.1 / §3）。

    塞进 ``data`` 会让前端出现两套分页/汇总读取路径，而 ``extra="forbid"`` 的契约
    一旦放宽就再也收不回来（另外 94 个接口没有这个键）。
    """
    resp = call_log_page(
        [], 0, page=1, per_page=20, summary=AiCallSummary(calls=0, cost_total_yuan=Decimal("0"))
    ).model_dump()
    assert set(resp) >= {"code", "message", "data", "page", "summary", "trace_id"}
    assert resp["data"] == []
    assert resp["summary"]["calls"] == 0
    assert resp["page"]["total"] == 0


def test_call_log_model_is_append_only_with_composite_pk() -> None:
    """模型层保证「只追加 + 分区可行」：无软删/更新时间、复合主键、新增 ``deployment`` 列。

    - 无 ``is_deleted`` / ``updated_at``：误改误删在 ORM 层无从下手（与 TBL-28 同源）；
    - 复合主键 ``(id, created_at)``：MySQL 要求**分区列进入每个唯一键**；
    - ``deployment`` 是本批的**第 4 处偏离**（上游 DDL 没有它）：
      没有它就无法回答「这次调用到底出没出网」，只能事后按配置反推 ——
      而配置会变，历史合规结论会被追溯性改写。
    """
    cols = set(AiCallLog.__table__.c.keys())
    assert {"is_deleted", "updated_at", "deleted_at"} & cols == set()
    assert [c.name for c in AiCallLog.__table__.primary_key.columns] == ["id", "created_at"]
    assert "deployment" in cols
    assert {"prompt_tokens", "completion_tokens", "cost", "mask_applied", "fallback_used"} <= cols


def test_tools_clip_and_dec_are_defensive() -> None:
    """两个小工具的行为契约（写入侧的边界处理）。"""
    assert clip(None, 10) is None
    assert clip("abcdef", 3) == "abc"
    assert clip(12345, 3) == "123"
    assert clip("ok", 10) == "ok"
    assert _dec(None) == Decimal("0")
    assert _dec("1.25") == Decimal("1.25")
    assert _dec(Decimal("2")) == Decimal("2")


# ============================================================================
# L1 · 路由登记与权限矩阵
# ============================================================================
def test_call_log_endpoints_are_read_only(app) -> None:
    """API-88 / API-89 只有 ``GET``，且参数名是 ``start_time`` / ``end_time``。

    🔴 ``ai_call_log`` **不得有任何写/删入口** —— 它由调用点在真实调用时写入，
    写入口 = 「伪造 / 抹掉调用痕迹」的正门（与审计日志同源）。
    ⚠️ 时间参数名与接口 §3 的 ``filters.start/end`` **有意不同**（已登记评审文档）：
    与 API-28 审计日志保持同名，否则「前端按 A 写、后端按 B 收」而
    未知 query 参数会被**静默忽略** —— 现象是「筛了时间但结果没变」。
    """
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/ai/call-logs"]) >= {"get"}
    assert set(paths["/api/v1/ai/cost-dashboard"]) >= {"get"}
    for path in ("/api/v1/ai/call-logs", "/api/v1/ai/cost-dashboard"):
        writes = {m for m in paths[path] if m in {"post", "put", "delete", "patch"}}
        assert not writes, f"{path} 出现了写方法 {writes}（调用日志只可读）"

    params = {p["name"] for p in paths["/api/v1/ai/call-logs"]["get"]["parameters"]}
    assert {"start_time", "end_time", "scene", "model", "status", "cache_hit", "mask_applied"} <= params


def test_log_and_cost_perms_are_granted_to_the_right_roles() -> None:
    """权限矩阵（接口 §4）：

    - API-88 调用日志 = ADM / AIOPS / **AUDIT**；
    - API-89 成本看板 = ADM / EXEC / AIOPS。

    🔴 两者的**差集**正是设计意图：AUDIT 能看日志（追溯谁调用了什么），
    但不能看成本聚合（成本属经营数据）；EXEC 反过来 —— 只看聚合不看明细。
    任何一方被「顺手合并」都会破坏这条分界，故在此双向钉死。
    """
    from scripts.seed_dev import ROLE_GRANTS

    def granted(role: str, perm: str) -> bool:
        return any(perm.startswith(p) for p in ROLE_GRANTS[role])

    assert granted("AUDIT", LOG_PERM) and not granted("AUDIT", COST_PERM)
    assert granted("EXEC", COST_PERM) and not granted("EXEC", LOG_PERM)
    assert granted("AIOPS", LOG_PERM) and granted("AIOPS", COST_PERM)
    for role in ("HR", "MGR", "EMP"):
        assert not granted(role, LOG_PERM), f"{role} 不该拿到调用日志权限"
        assert not granted(role, COST_PERM), f"{role} 不该拿到成本看板权限"


def test_log_and_cost_perms_exist_in_permission_tree() -> None:
    """两个权限点必须被种子建出来，否则上面的授权是空转。"""
    from scripts.seed_dev import _PERM_TREE

    codes = {code for _menu, _name, children in _PERM_TREE for code, _cn in children}
    for perm in AI_PERMS:
        assert perm in codes, f"权限树缺少 {perm}"


def test_seeded_model_configs_keep_prices_and_deployment_consistent() -> None:
    """种子里的模型配置必须与「出网判定」自洽：**本地模型单价为 0**，出网模型单价 > 0。

    这条守的是成本看板的口径：本地模型若被写上单价，看板会凭空出现成本；
    反之出网模型单价为 0，成本看板就永远看不到真实的钱（而账单照样来）。
    """
    from scripts.seed_dev import _MODEL_CONFIGS

    for row in _MODEL_CONFIGS:
        (scene, _code, _name, _provider, deployment, _url, _model, _prio,
         _timeout, _retries, price_in, price_out, _status, _remark) = row
        if deployment == AiDeployment.LOCAL:
            assert Decimal(str(price_in)) == 0 and Decimal(str(price_out)) == 0, (
                f"{scene} 的本地模型不该有单价（本地算力不按 token 计费）"
            )
        else:
            assert Decimal(str(price_in)) > 0 or Decimal(str(price_out)) > 0, (
                f"{scene} 的出网模型必须有单价，否则成本看板永远看不到这笔钱"
            )


def test_seeded_model_configs_cover_every_scene() -> None:
    """每个 ``AiScene`` 都必须至少有一条启用中的模型配置 —— 否则该场景一调用就降级。

    ``ocr`` 是**刻意不种**的例外：OCR 后端选型属 CU-14，
    种一条 ``base_url`` 是占位符的配置会让 API-83 每次探测都失败
    （并呈现为「一个配错了的模型」这种假象）。故显式豁免并在此登记。
    """
    from scripts.seed_dev import _MODEL_CONFIGS

    seeded = {row[0] for row in _MODEL_CONFIGS}
    assert seeded | {"ocr"} == set(AiScene.LABELS), (
        f"场景覆盖不全：缺 {set(AiScene.LABELS) - seeded - {'ocr'}}"
    )
    assert "ocr" not in seeded


# ============================================================================
# L2 · 端到端夹具（独立租户，用例结束整体清理）
# ============================================================================
@pytest.fixture
def env(db: Session, client: TestClient) -> Iterator[dict]:
    """独立租户 + 4 个账号（ADM / 同时持有两权 / 只有日志权 / 只有看板权）。

    ⚠️ 必须用独立租户：要断言租户隔离与汇总口径。
    复用种子租户的话，``summary`` 会被别的测试留下的调用日志污染。
    """
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("ai_call_log"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0004_ai_agent）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu12_{suffix}", tenant_name=f"CU12测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    perms: dict[str, SysPermission] = {}
    for code in AI_PERMS:
        p = SysPermission(
            tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1
        )
        db.add(p)
        perms[code] = p
    db.flush()

    def _mk_role(code: str, grants: list[str]) -> SysRole:
        role = SysRole(
            tenant_id=tid, role_code=code, role_name=code,
            data_scope=int(RoleDataScope.ALL), is_builtin=1, status=1,
        )
        db.add(role)
        db.flush()
        for c in grants:
            db.add(SysRolePermission(tenant_id=tid, role_id=role.id, perm_id=perms[c].id))
        return role

    roles = {
        "adm": _mk_role("ADM", list(AI_PERMS)),
        "both": _mk_role("AIOPS", list(AI_PERMS)),
        "logonly": _mk_role("AUDIT", [LOG_PERM]),
        "costonly": _mk_role("EXEC", [COST_PERM]),
        "none": _mk_role("HR", []),
    }

    def _mk_user(name: str, role: SysRole) -> SysUser:
        user = SysUser(
            tenant_id=tid, username=name,
            password_hash=SecurityUtils.hash_password(PWD), real_name=name,
            status=1, login_fail_count=0,
        )
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=role.id))
        return user

    tokens: dict[str, str] = {}
    for who, role in roles.items():
        username = f"{who}_{suffix}"
        _mk_user(username, role)
        db.commit()
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["code"] == 0, payload
        tokens[who] = payload["data"]["access_token"]

    # ---- 一个 Prompt（用于验证 prompt_code 反查）+ 另一个租户（用于验证隔离）----
    prompt = PromptTemplate(
        tenant_id=tid, prompt_code="LOG_TEST_PROMPT", scene="ask",
        version=3, content="问题：{question}", status=2, created_by=None,  # 刻意「已停用」
    )
    db.add(prompt)
    db.flush()

    yield {
        "tenant_id": tid,
        "tokens": tokens,
        "prompt_id": int(prompt.id),
        "other_tenant_id": tid + 999_999,
        "suffix": suffix,
    }

    db.rollback()
    db.execute(delete(AiCallLog).where(AiCallLog.tenant_id.in_([tid, tid + 999_999])))
    db.execute(delete(PromptTemplate).where(PromptTemplate.tenant_id == tid))
    db.execute(
        delete(SysRolePermission).where(
            SysRolePermission.role_id.in_(select(SysRole.id).where(SysRole.tenant_id == tid))
        )
    )
    db.execute(delete(SysUserRole).where(SysUserRole.tenant_id == tid))
    db.execute(delete(SysUser).where(SysUser.tenant_id == tid))
    db.execute(delete(SysRole).where(SysRole.tenant_id == tid))
    db.execute(delete(SysPermission).where(SysPermission.tenant_id == tid))
    db.execute(delete(Tenant).where(Tenant.id == tid))
    db.commit()


def _log(
    db: Session,
    tenant_id: int,
    *,
    scene: str = "resume_parse",
    deployment: str | None = "local",
    status: int = 1,
    mask_applied: bool = False,
    cost: str = "0",
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 100,
    model: str = "qwen2.5:3b",
    prompt_id: int | None = None,
    prompt_version: int | None = None,
    error_msg: str | None = None,
    trace_id: str | None = None,
    created_at: datetime | None = None,
    cache_hit: bool = False,
) -> AiCallLog:
    """直接插一条调用日志（**读路径用例需要精确控制 ``created_at``**，
    故不用 ``AiLogService.record`` —— 它不接受时间参数，只能写「现在」）。

    🔴 时间一律**砍到整秒**（``microsecond=0``）：``created_at`` 是 ``DATETIME``
    （无小数秒），MySQL 对亚秒值是**四舍五入**的（实测 ``…:00.5`` → ``…:01``）。
    若直接写 ``datetime.now()``，约一半概率这一行会落在「查询时刻的下一秒」，
    于是被默认区间（``end = now()``）排除 —— 表现是「刚插入就查不到」的假失败。
    这也是**生产上确实存在**的 1 秒级可见性延迟（已在 ``resolve_range`` 里记下来）。
    """
    row = AiCallLog(
        tenant_id=int(tenant_id),
        trace_id=trace_id,
        scene=scene,
        deployment=deployment,
        model=model,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        prompt_tokens=int(tokens_in),
        completion_tokens=int(tokens_out),
        cost=Decimal(cost),
        latency_ms=latency_ms,
        cache_hit=1 if cache_hit else 0,
        mask_applied=1 if mask_applied else 0,
        status=int(status),
        error_msg=error_msg,
        created_at=(created_at or datetime.now()).replace(microsecond=0),
    )
    ai_dao.insert_call_log(db, row)
    return row


def _adm(env: dict) -> dict[str, str]:
    return _h(env["tokens"]["adm"])


# ============================================================================
# L2 · 写入侧（AiLogService.record）
# ============================================================================
def test_record_persists_deployment_and_cost(env: dict, db: Session) -> None:
    """``record`` 真的落库，且 ``deployment`` / 成本 / 脱敏标记都对。

    这条同时验证「价格单位是**元/千 token**」：1000 in × 0.001 + 500 out × 0.002 = 0.002 元。
    """
    row = AiLogService.record(
        tenant_id=env["tenant_id"],
        scene="talent_ask",
        deployment=AiDeployment.CLOUD,
        provider="cloud",
        model="cloud-llm-a",
        tokens_in=1000,
        tokens_out=500,
        price_in_per_1k=Decimal("0.001"),
        price_out_per_1k=Decimal("0.002"),
        mask_applied=True,
        db=db,
    )
    db.commit()
    assert row is not None

    stored = db.execute(select(AiCallLog).where(AiCallLog.id == int(row.id))).scalar_one()
    assert stored.deployment == "cloud", "出网事实必须落库（脱敏率分母的唯一依据）"
    assert stored.cost == Decimal("0.002000")
    assert int(stored.mask_applied) == 1
    assert stored.scene == "talent_ask"


def test_record_falls_back_to_scene_stage_when_deployment_missing(env: dict, db: Session) -> None:
    """调用方没声明 ``deployment`` 时按**场景固有形态**兜底（合理默认，已在文档里标注不精确）。"""
    row = AiLogService.record(
        tenant_id=env["tenant_id"], scene="talent_ask", tokens_in=1, db=db
    )
    db.commit()
    assert row is not None and row.deployment == AiDeployment.CLOUD

    local = AiLogService.record(tenant_id=env["tenant_id"], scene="resume_parse", db=db)
    db.commit()
    assert local is not None and local.deployment == AiDeployment.LOCAL


def test_record_never_raises_when_insert_fails(
    env: dict, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 **fail-open**：日志写不进去**绝不**让业务失败。

    一次 AI 调用已经成功（模型回了内容、业务已经用上了），此时因为
    「日志表写不进去」而把整个请求变成 500，是纯粹的伤害 ——
    用户丢了一次成功的结果，运维丢了一条审计线索。
    """
    def _boom(*_a, **_k):
        raise RuntimeError("表被锁了")

    monkeypatch.setattr(ai_dao, "insert_call_log", _boom)
    assert AiLogService.record(tenant_id=env["tenant_id"], scene="judge", db=db) is None


def test_record_survives_bad_tokens_with_zero_cost(env: dict, db: Session) -> None:
    """负 token（上游统计错了）→ 成本记 0、计数归零，**但仍然记录这次调用**。

    丢弃整条记录会让「调用发生过」这件事消失（token 已经烧掉了）；
    而取绝对值会让成本看板长期偏低且**看不出有问题**。故：成本 0 + 计数下限 0。
    """
    row = AiLogService.record(
        tenant_id=env["tenant_id"],
        scene="judge",
        tokens_in=-5,
        tokens_out=-1,
        price_in_per_1k=Decimal("0.001"),
        price_out_per_1k=Decimal("0.001"),
        db=db,
    )
    db.commit()
    assert row is not None
    assert row.cost == Decimal("0")
    assert int(row.prompt_tokens) == 0 and int(row.completion_tokens) == 0


def test_record_truncates_long_error_message(env: dict, db: Session) -> None:
    """``error_msg`` 截断到 512（列宽）—— 超长会让 INSERT 报 1406 并**丢掉整条日志**。"""
    row = AiLogService.record(
        tenant_id=env["tenant_id"], scene="judge", status=0, error_msg="x" * 2000, db=db
    )
    db.commit()
    assert row is not None and len(str(row.error_msg)) == 512


# ============================================================================
# L2 · API-88 列表与汇总
# ============================================================================
def test_api_88_lists_metadata_and_resolves_prompt_code(env: dict, client: TestClient, db: Session) -> None:
    """列表返回元数据 + ``prompt_code`` 反查（**模板已停用也要能显示**）。

    反查时不过滤 ``status`` / ``is_deleted``：老日志指向的 Prompt 可能早已停用，
    但仍必须显示它当时的编码 —— 否则「这条日志用的是哪个 Prompt」在停用后永久丢失。
    """
    _log(
        db, env["tenant_id"], scene="talent_ask", deployment="cloud", mask_applied=True,
        tokens_in=10, tokens_out=5, cost="0.010000",
        prompt_id=env["prompt_id"], prompt_version=3, trace_id="trace-abc",
    )
    db.commit()

    body = client.get("/api/v1/ai/call-logs", headers=_adm(env)).json()
    assert body["code"] == 0
    assert body["page"]["total"] == 1
    item = body["data"][0]
    assert isinstance(item["id"], str), "CR-5"
    assert item["prompt_code"] == "LOG_TEST_PROMPT", "已停用（status=2）的模板也要能反查到"
    assert item["prompt_version"] == 3
    assert item["scene_name"] == "档案 RAG 问答"
    assert item["cost_yuan"] == "0.0100", "金额按 4 位小数字符串下发"
    assert item["status_name"] == "成功"
    assert body["summary"]["calls"] == 1


def test_scene_stage_follows_deployment_not_scene(env: dict, client: TestClient, db: Session) -> None:
    """🔴 ``scene_stage`` 取**本次调用实际的部署位置**，不是场景的固有形态。

    构造：``talent_ask``（固有 = cloud）但 ``deployment=local``（云端主路挂掉、
    落到本地兜底的那一次）。期望 ``scene_stage=local``。

    若按场景猜，这次调用会被打上「云端」徽标，而它的 ``mask_applied`` 是 0
    （本地不出网、无需脱敏）→ 合规核查者看到一个**「云端但未脱敏」的假阳性**。
    """
    _log(db, env["tenant_id"], scene="talent_ask", deployment="local", mask_applied=False)
    db.commit()

    item = client.get("/api/v1/ai/call-logs", headers=_adm(env)).json()["data"][0]
    assert item["scene"] == "talent_ask"
    assert item["scene_stage"] == "local", "必须跟着 deployment 走"


def test_mask_rate_denominator_is_cloud_calls_only_end_to_end(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 端到端复现合规口径：本地调用**不进分母**。

    构造 5 条：3 条出网（2 条已脱敏、1 条未脱敏）+ 2 条本地（未脱敏，但**不该被算作违规**）。
    → ``cloud_calls=3``、``mask_applied_rate=0.6667``、``mask_alert=true``。
    若分母是 5，比率会变成 0.4 —— 一个凭空偏低的合规指标。
    """
    for masked in (True, True, False):
        _log(db, env["tenant_id"], scene="talent_ask", deployment="cloud", mask_applied=masked)
    for _ in range(2):
        _log(db, env["tenant_id"], scene="resume_parse", deployment="local", mask_applied=False)
    db.commit()

    summary = client.get("/api/v1/ai/call-logs", headers=_adm(env)).json()["summary"]
    assert summary["calls"] == 5
    assert summary["cloud_calls"] == 3, "分母只含出网调用"
    assert summary["mask_applied_rate"] == 0.6667
    assert summary["mask_alert"] is True

    # 反向对照：把那条未脱敏的出网调用改成已脱敏 → 不再告警
    row = db.execute(
        select(AiCallLog).where(
            AiCallLog.tenant_id == env["tenant_id"], AiCallLog.mask_applied == 0,
            AiCallLog.deployment == "cloud",
        )
    ).scalars().first()
    assert row is not None
    row.mask_applied = 1
    db.commit()
    summary = client.get("/api/v1/ai/call-logs", headers=_adm(env)).json()["summary"]
    assert summary["mask_applied_rate"] == 1.0
    assert summary["mask_alert"] is False


def test_no_cloud_call_means_no_alert(env: dict, client: TestClient, db: Session) -> None:
    """全本地部署：脱敏率 1.0、不告警（「没有出网 = 没有违规」）。"""
    _log(db, env["tenant_id"], deployment="local")
    db.commit()
    summary = client.get("/api/v1/ai/call-logs", headers=_adm(env)).json()["summary"]
    assert summary["cloud_calls"] == 0
    assert summary["mask_applied_rate"] == 1.0
    assert summary["mask_alert"] is False


def test_api_88_filters(env: dict, client: TestClient, db: Session) -> None:
    """筛选：``scene`` / ``status`` / ``mask_applied`` / ``trace_id`` / ``model`` 各一条断言。

    每条都配反向对照（换一个不匹配的值应为 0 条）——
    否则一个「忽略所有筛选」的实现也能全过。
    """
    _log(db, env["tenant_id"], scene="resume_parse", deployment="local", status=0,
         model="qwen2.5:3b", trace_id="t-1")
    _log(db, env["tenant_id"], scene="talent_ask", deployment="cloud", status=1,
         mask_applied=True, model="cloud-llm-a", trace_id="t-2")
    db.commit()

    def total(query: str) -> int:
        return client.get(f"/api/v1/ai/call-logs?{query}", headers=_adm(env)).json()["page"]["total"]

    assert total("scene=resume_parse") == 1
    assert total("scene=judge") == 0
    assert total("status=0") == 1
    assert total("status=1") == 1
    assert total("mask_applied=true") == 1
    assert total("mask_applied=false") == 1
    assert total("trace_id=t-2") == 1
    assert total("trace_id=nope") == 0
    assert total("model=cloud-llm-a") == 1
    assert total("cache_hit=false") == 2


def test_api_88_default_range_excludes_old_rows(env: dict, client: TestClient, db: Session) -> None:
    """默认区间「最近 7 天」真的在裁剪：10 天前那条不得出现在默认查询里。"""
    _log(db, env["tenant_id"], created_at=datetime.now() - timedelta(days=10))
    _log(db, env["tenant_id"])
    db.commit()

    assert client.get("/api/v1/ai/call-logs", headers=_adm(env)).json()["page"]["total"] == 1

    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    widened = client.get(
        f"/api/v1/ai/call-logs?start_time={start}&end_time={end}", headers=_adm(env)
    ).json()
    assert widened["page"]["total"] == 2, "显式放宽区间后应能看到两条"


def test_api_88_span_over_limit_is_40001_with_max_days(env: dict, client: TestClient) -> None:
    """跨度超 90 天 → 40001，且 ``data.max_days`` / ``data.requested_days`` 按文档形状下发。"""
    start = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
    resp = client.get(f"/api/v1/ai/call-logs?start_time={start}", headers=_adm(env))
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == int(ErrorCode.PARAM_INVALID)
    assert body["data"]["max_days"] == AI_CALL_MAX_RANGE_DAYS
    assert body["data"]["requested_days"] > AI_CALL_MAX_RANGE_DAYS


def test_api_88_unknown_scene_is_40001(env: dict, client: TestClient) -> None:
    """``scene`` 拼错 → 40001（明确提示），而不是「查得到 0 条」。"""
    resp = client.get("/api/v1/ai/call-logs?scene=talen_ask", headers=_adm(env))
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)
    assert resp.json()["data"]["fields"][0]["field"] == "scene"


def test_api_88_tenant_isolation(env: dict, client: TestClient, db: Session) -> None:
    """🔴 租户隔离：别的租户的日志既不出现在列表里，也**不进汇总**。

    汇总也要隔离这一点最容易漏 —— 列表隔离了、``summary`` 忘了加租户条件的话，
    现象是「列表 1 条、汇总 5 条」。
    """
    _log(db, env["tenant_id"])
    for _ in range(4):
        _log(db, env["other_tenant_id"], scene="talent_ask", deployment="cloud", cost="9")
    db.commit()

    body = client.get("/api/v1/ai/call-logs", headers=_adm(env)).json()
    assert body["page"]["total"] == 1
    assert body["summary"]["calls"] == 1, "汇总必须同口径隔离"
    assert body["summary"]["cost_total_yuan"] == "0.0000"


# ============================================================================
# L2 · API-89 成本看板
# ============================================================================
def test_api_89_three_dimensions_sum_to_total(env: dict, client: TestClient, db: Session) -> None:
    """🔴 **三个维度的成本之和 = 总额**（同一区间、同一租户）。

    三者由同一段 SQL 骨架（``ai_dao._aggregate``）产出，不是三份手写查询 ——
    否则「场景维度 100 元、模型维度 98 元」这类差异会先被使用者发现，
    而那时已经无从判断哪个对。
    """
    _log(db, env["tenant_id"], scene="talent_ask", deployment="cloud", cost="0.100000",
         model="cloud-llm-a", tokens_in=100, tokens_out=50)
    _log(db, env["tenant_id"], scene="resume_parse", deployment="local", cost="0.020000",
         model="qwen2.5:3b", tokens_in=10, tokens_out=5)
    _log(db, env["tenant_id"], scene="talent_ask", deployment="cloud", cost="0.300000",
         model="cloud-llm-a", tokens_in=200, tokens_out=80)
    db.commit()

    body = client.get("/api/v1/ai/cost-dashboard", headers=_adm(env)).json()
    assert body["code"] == 0
    data = body["data"]
    total = Decimal(data["total"]["cost_total_yuan"])
    assert total == Decimal("0.420000")

    for dim in ("by_scene", "by_model", "by_time"):
        got = sum(Decimal(b["cost_yuan"]) for b in data[dim])
        assert got == total, f"{dim} 的合计 {got} 与总额 {total} 不一致"

    by_scene = {b["key"]: b for b in data["by_scene"]}
    assert by_scene["talent_ask"]["cost_yuan"] == "0.4000"
    assert by_scene["talent_ask"]["label"] == "档案 RAG 问答", "场景维度的 label 要翻成中文"
    assert by_scene["talent_ask"]["calls"] == 2
    assert by_scene["resume_parse"]["label"] == "简历字段抽取"

    by_model = {b["key"]: b for b in data["by_model"]}
    assert by_model["cloud-llm-a"]["label"] == "cloud-llm-a", "模型维度的 label 即原值"
    assert by_model["cloud-llm-a"]["tokens_in"] == 300

    by_time = [b["key"] for b in data["by_time"]]
    assert by_time == [datetime.now().strftime("%Y-%m-%d")], "by_time 按天聚合，key 是 YYYY-MM-DD"


def test_api_89_default_range_is_thirty_days(env: dict, client: TestClient, db: Session) -> None:
    """看板默认 30 天（比日志的 7 天长 —— 看板关心趋势，不看单次）。"""
    _log(db, env["tenant_id"], created_at=datetime.now() - timedelta(days=20))
    _log(db, env["tenant_id"], created_at=datetime.now() - timedelta(days=60))
    db.commit()

    data = client.get("/api/v1/ai/cost-dashboard", headers=_adm(env)).json()["data"]
    assert data["total"]["calls"] == 1, "60 天前那条应在默认区间外"
    span = datetime.strptime(data["end_time"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(
        data["start_time"], "%Y-%m-%d %H:%M:%S"
    )
    assert span.days == AI_COST_DEFAULT_RANGE_DAYS


def test_api_89_empty_result_is_zero_not_error(env: dict, client: TestClient) -> None:
    """没有数据 → 全 0 的**正常响应**（不是 404 / 500）。

    看板在「刚部署、还没有调用」时必须能打开 —— 报错会让人以为功能坏了。
    """
    body = client.get("/api/v1/ai/cost-dashboard", headers=_adm(env)).json()
    assert body["code"] == 0
    assert body["data"]["total"]["calls"] == 0
    assert body["data"]["by_scene"] == [] and body["data"]["by_time"] == []


def test_api_89_unknown_scene_is_40001_like_api_88(env: dict, client: TestClient) -> None:
    """看板与日志必须**报同一种错**：``scene`` 拼错在两个接口上都是 40001。

    这条防的是「日志接口拦得住、看板接口拦不住」的不对称 ——
    使用者会以为是数据缺失，而不是自己拼错了。
    """
    resp = client.get("/api/v1/ai/cost-dashboard?scene=nope", headers=_adm(env))
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


# ============================================================================
# L2 · 权限矩阵（含「差集」的双向验证）
# ============================================================================
def test_log_only_role_cannot_read_cost_dashboard(env: dict, client: TestClient) -> None:
    """🔴 AUDIT（只有 ``ai:log:view``）→ 看板 **40301**；但日志列表 200。

    这条是「AUDIT 与 EXEC 的权限差集」的验收点：若把 API-89 挂成 ``ai:log:view``，
    审计角色就能看到全租户的开销 —— 成本属经营数据，不在审计职责内。
    """
    assert client.get("/api/v1/ai/call-logs", headers=_h(env["tokens"]["logonly"])).status_code == 200
    denied = client.get("/api/v1/ai/cost-dashboard", headers=_h(env["tokens"]["logonly"]))
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == int(ErrorCode.NO_PERMISSION)
    assert "cost" not in str(denied.json().get("data")), "40301 不得泄露任何资源字段"


def test_cost_only_role_cannot_read_call_logs(env: dict, client: TestClient) -> None:
    """🔴 EXEC（只有 ``ai:cost:view``）→ 日志明细 **40301**；但看板 200。

    与上一条互为反向对照：EXEC 是「只看聚合不看明细」的角色（``data_scope=4``）。
    """
    assert client.get("/api/v1/ai/cost-dashboard", headers=_h(env["tokens"]["costonly"])).status_code == 200
    denied = client.get("/api/v1/ai/call-logs", headers=_h(env["tokens"]["costonly"]))
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == int(ErrorCode.NO_PERMISSION)


def test_role_without_ai_perms_is_40301(env: dict, client: TestClient) -> None:
    """业务角色（HR）→ 两个接口都 40301（fail-closed）。"""
    for path in ("/api/v1/ai/call-logs", "/api/v1/ai/cost-dashboard"):
        resp = client.get(path, headers=_h(env["tokens"]["none"]))
        assert resp.status_code == 403, f"{path}: {resp.text}"
        assert resp.json()["code"] == int(ErrorCode.NO_PERMISSION)


def test_unauthenticated_is_40101(env: dict, client: TestClient) -> None:
    """无 Token → 40101（认证层先于权限层）。"""
    assert client.get("/api/v1/ai/call-logs").json()["code"] == int(ErrorCode.UNAUTHENTICATED)


def test_both_perms_role_can_read_everything(env: dict, client: TestClient) -> None:
    """反向对照：同时持有两权的角色（AIOPS）两个接口都能读。"""
    headers = _h(env["tokens"]["both"])
    for path in ("/api/v1/ai/call-logs", "/api/v1/ai/cost-dashboard"):
        assert client.get(path, headers=headers).status_code == 200, path
