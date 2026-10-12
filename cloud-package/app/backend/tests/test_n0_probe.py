"""CU-27（N0 前置实测）的契约测试 —— 全部 L1：**不需要 MySQL / Redis / Milvus**。

测什么、不测什么
----------------
N0 探针本身**就是要连外部依赖的**，所以「探针连得上」不可能在 CI 里断言。
但**探针的骨架**恰恰是最容易悄悄坏掉、又最不该坏的部分，且完全可测：

- 注册表完整性（key 唯一、分组合法、出处非空）—— 少标一个出处，报告就失去可追溯性；
- 状态判定与退出码矩阵 —— 退出码写错，CI 会把「阻塞」当「通过」；
- 报告结构（小节齐全、脱敏、JSON 键稳定）；
- **报告绝不出现凭据** —— 这条一旦破了，危害是密钥进仓库。

按项目既有约定：依赖外部环境的用例才用 ``@requires_db`` 之类标注并 skip；
本文件**一条都不需要**，因此在任何机器上都必须全绿。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.config import settings
from scripts.n0 import report as report_mod
from scripts.n0 import targets as targets_mod
from scripts.n0.probes import GROUPS, KNOWN_KEYS, PROBE_SPECS, specs_by_key
from scripts.n0.result import (
    Probe,
    ProbePayload,
    ProbeSpec,
    ProbeStatus,
    exit_code,
    run_spec,
    summarize,
)
from scripts.n0_probe import main as n0_main


def _probe(key: str, status: ProbeStatus, blocking: bool = False, remediation: str = "") -> Probe:
    """构造一个探针结果（不触任何 I/O）。"""
    return Probe(
        key=key,
        title=f"标题-{key}",
        group="env",
        requirement_ref="需求 §12.1 N0 ③",
        blocking=blocking,
        status=status,
        detail=f"细节-{key}",
        remediation=remediation,
    )


# ============================================================================
# 注册表完整性
# ============================================================================
def test_registry_keys_unique_and_identifier() -> None:
    keys = [spec.key for spec in PROBE_SPECS]
    assert len(keys) == len(set(keys)), "探针 key 必须唯一"
    assert all(key.isidentifier() for key in keys)


def test_registry_declares_group_and_requirement_ref() -> None:
    """每个探针都必须标明分组与需求出处 —— 不允许「凭感觉加的检测项」。"""
    for spec in PROBE_SPECS:
        assert spec.group in GROUPS, f"{spec.key} 的分组 {spec.group!r} 未在 GROUPS 登记"
        assert spec.requirement_ref.strip(), f"{spec.key} 未标注需求出处"


def test_registry_functions_are_probe_functions() -> None:
    for spec in PROBE_SPECS:
        assert callable(spec.fn)
        assert spec.fn.__name__.startswith("probe_"), f"{spec.key} 的函数命名不符合约定"


def test_known_keys_matches_specs() -> None:
    assert KNOWN_KEYS == tuple(spec.key for spec in PROBE_SPECS)
    assert set(specs_by_key()) == set(KNOWN_KEYS)


def test_spec_rejects_empty_requirement_ref() -> None:
    """``ProbeSpec`` 自身就该拒绝「没有出处」的探针（构造期校验，不靠人自觉）。"""
    with pytest.raises(ValueError):
        ProbeSpec("x", "标题", "env", "", True, lambda: ProbePayload(ProbeStatus.OK, "d"))


def test_spec_rejects_illegal_key() -> None:
    with pytest.raises(ValueError):
        ProbeSpec("bad key", "标题", "env", "出处", True, lambda: ProbePayload(ProbeStatus.OK, "d"))


# ============================================================================
# 状态语义与退出码
# ============================================================================
def test_status_labels_and_rank_complete() -> None:
    for status in ProbeStatus:
        assert status.label
        assert isinstance(status.rank, int)
    # 严重度排序：failed 最该先看
    assert ProbeStatus.FAILED.rank > ProbeStatus.BLOCKED.rank > ProbeStatus.DEGRADED.rank > ProbeStatus.OK.rank


def test_healthy_and_needs_action() -> None:
    assert _probe("a", ProbeStatus.OK).healthy
    assert _probe("a", ProbeStatus.DEGRADED).healthy  # 降级仍「可用」
    assert _probe("a", ProbeStatus.BLOCKED).needs_action
    assert not _probe("a", ProbeStatus.OK).needs_action


def test_is_blocker_requires_both_flags() -> None:
    """阻塞 = 需求标为 blocking **且** 当前不可用。两者缺一都不算。"""
    assert _probe("a", ProbeStatus.BLOCKED, blocking=True).is_blocker
    assert not _probe("a", ProbeStatus.BLOCKED, blocking=False).is_blocker
    assert not _probe("a", ProbeStatus.OK, blocking=True).is_blocker
    assert not _probe("a", ProbeStatus.DEGRADED, blocking=True).is_blocker  # 降级不算阻塞
    assert not _probe("a", ProbeStatus.FAILED, blocking=False).is_blocker


def test_exit_code_matrix() -> None:
    assert exit_code([_probe("a", ProbeStatus.OK), _probe("b", ProbeStatus.DEGRADED)]) == 0
    assert exit_code([_probe("a", ProbeStatus.FAILED)]) == 2
    assert exit_code([_probe("a", ProbeStatus.BLOCKED, blocking=True)]) == 2
    # 非阻塞项的待就绪 **不该** 让退出码变 2（它不是缺陷，是 N0 要如实记录的现状）
    assert exit_code([_probe("a", ProbeStatus.BLOCKED, blocking=False)]) == 0


def test_summarize_counts_all_statuses() -> None:
    probes = [
        _probe("a", ProbeStatus.OK),
        _probe("b", ProbeStatus.OK),
        _probe("c", ProbeStatus.DEGRADED),
        _probe("d", ProbeStatus.BLOCKED),
        _probe("e", ProbeStatus.FAILED),
    ]
    assert summarize(probes) == {"ok": 2, "degraded": 1, "blocked": 1, "failed": 1}
    assert summarize([]) == {"ok": 0, "degraded": 0, "blocked": 0, "failed": 0}


def test_run_spec_swallows_probe_exception() -> None:
    """兜底红线：探针内部抛出的**预期外**异常必须变成 failed，而不是让整份报告消失。"""

    def _boom() -> ProbePayload:
        raise RuntimeError("boom-from-probe")

    probe = run_spec(ProbeSpec("boom", "故意抛错", "env", "测试用例", True, _boom))
    assert probe.status is ProbeStatus.FAILED
    assert "boom-from-probe" in probe.detail
    assert probe.remediation, "异常必须给出处置提示，否则拿到报告的人无从下手"


def test_run_spec_passes_through_payload() -> None:
    spec = ProbeSpec(
        "ok_probe",
        "正常探针",
        "env",
        "测试用例",
        False,
        lambda: ProbePayload(ProbeStatus.OK, "一切正常", {"v": 1}),
    )
    probe = run_spec(spec)
    assert probe.status is ProbeStatus.OK
    assert probe.metrics == {"v": 1}
    assert probe.title == "正常探针"


def test_probe_metric_path_lookup() -> None:
    probe = _probe("a", ProbeStatus.OK)
    probe.metrics = {"rtt_ms_avg": 12.5}
    assert probe.metric("rtt_ms_avg") == 12.5
    assert probe.metric("missing") is None
    assert probe.metric("missing", default="—") == "—"
    assert probe.metric() is None


# ============================================================================
# 指标基线台账
# ============================================================================
def test_metric_targets_declare_doc_ref_and_valid_scope() -> None:
    assert targets_mod.METRIC_TARGETS
    codes = [t.code for t in targets_mod.METRIC_TARGETS]
    assert len(codes) == len(set(codes)), "指标 code 必须唯一"
    for target in targets_mod.METRIC_TARGETS:
        assert target.doc_ref.strip(), f"{target.code} 未标注文档出处（不得凭空造阈值）"
        assert target.scope in ("probe", "load", "goldset")
        assert target.scope_label


def test_metric_target_probe_keys_point_to_real_probes() -> None:
    """挂了 ``probe_key`` 的指标，其探针必须真实存在于注册表（否则报告会谎称「未执行」）。"""
    for target in targets_mod.METRIC_TARGETS:
        if target.probe_key is None:
            continue
        assert target.probe_key in KNOWN_KEYS, f"{target.code} 指向不存在的探针 {target.probe_key}"
        assert target.metric_path, f"{target.code} 声明了探针却没给取值路径"


def test_metric_targets_for_filters_by_scope() -> None:
    probe_scope = targets_mod.targets_for("probe")
    assert probe_scope and all(t.scope == "probe" for t in probe_scope)
    goldset_scope = targets_mod.targets_for("goldset")
    assert {t.code for t in goldset_scope} == {"IDX-01", "IDX-02"}


def test_deliverables_owner_valid_and_summary_adds_up() -> None:
    owners = {"本脚本", "人工", "甲方"}
    for item in targets_mod.N0_DELIVERABLES:
        assert item.owner in owners, f"{item.name} 的责任方 {item.owner!r} 非法"
    counts = targets_mod.deliverable_summary()
    assert sum(counts.values()) == len(targets_mod.N0_DELIVERABLES)


# ============================================================================
# 报告渲染
# ============================================================================
def _ctx(probes: list[Probe]) -> report_mod.ReportContext:
    return report_mod.build_context(
        probes,
        generated_at=datetime(2026, 9, 22, 10, 30, 0),
        env={"MYSQL": "127.0.0.1:3306/talent_platform_v2", "AES_KEY": "已配置"},
    )


def test_markdown_has_all_required_sections() -> None:
    probes = [
        _probe("mysql", ProbeStatus.OK, blocking=True),
        _probe("milvus", ProbeStatus.BLOCKED, blocking=True, remediation="装 pymilvus"),
        _probe("redis", ProbeStatus.DEGRADED),
    ]
    md = report_mod.render_markdown(_ctx(probes))
    for section in (
        "## 零、结论摘要",
        "## 一、环境与模型就绪矩阵",
        "## 二、基线实测值",
        "## 三、需人工执行的处置清单",
        "## 四、N0 交付物对照",
        "## 五、口径与局限",
        "## 六、下一步",
    ):
        assert section in md, f"报告缺少小节：{section}"
    assert "milvus" in md
    assert "装 pymilvus" in md  # 处置清单必须带可执行动作
    assert "2026-09-22" in md


def test_markdown_says_no_blocker_when_clean() -> None:
    md = report_mod.render_markdown(_ctx([_probe("mysql", ProbeStatus.OK, blocking=True)]))
    assert "无阻塞项" in md


def test_markdown_lists_all_metric_targets() -> None:
    md = report_mod.render_markdown(_ctx([]))
    for target in targets_mod.METRIC_TARGETS:
        assert target.code in md, f"指标表漏了 {target.code}"


def test_json_structure_is_stable() -> None:
    probes = [
        _probe("mysql", ProbeStatus.OK, blocking=True),
        _probe("milvus", ProbeStatus.BLOCKED, blocking=True, remediation="装 pymilvus"),
    ]
    data = report_mod.render_json(_ctx(probes))
    assert {
        "script_version",
        "generated_at",
        "summary",
        "blockers",
        "env",
        "probes",
        "metrics",
        "deliverables",
    } <= set(data)
    assert data["blockers"] == ["milvus"]
    assert data["summary"]["ok"] == 1
    assert data["probes"][0]["status"] == "ok"
    assert data["probes"][0]["healthy"] is True
    assert len(data["metrics"]) == len(targets_mod.METRIC_TARGETS)


def test_json_is_serializable() -> None:
    """报告要落盘成 JSON，任何非序列化值都会在写文件时炸掉。"""
    import json

    payload = report_mod.render_json(_ctx([_probe("a", ProbeStatus.OK)]))
    json.dumps(payload, ensure_ascii=False)


def test_measured_value_reads_from_probe_metrics() -> None:
    probe = _probe("redis", ProbeStatus.OK)
    probe.metrics = {"ops_per_sec": 3752.0}
    target = next(t for t in targets_mod.METRIC_TARGETS if t.code == "§6.1②")
    rendered = report_mod._measured({"redis": probe}, target)
    assert "3,752" in rendered


def test_measured_value_is_dash_when_probe_absent() -> None:
    target = next(t for t in targets_mod.METRIC_TARGETS if t.code == "§6.1②")
    assert report_mod._measured({}, target).startswith("—")


def test_measured_value_is_dash_for_goldset_targets() -> None:
    """金标准集类指标不在探针注册表内，必须显示「仅登记口径」而非「未执行」。"""
    target = next(t for t in targets_mod.METRIC_TARGETS if t.code == "IDX-01")
    assert report_mod._measured({}, target) == "—"


def test_env_snapshot_never_contains_credentials() -> None:
    """🔴 报告会进仓库与邮件 —— 凭据只允许以「已配置 / 未配置」出现。"""
    env = report_mod.collect_env_snapshot()
    secrets = [
        settings.MYSQL_PASSWORD,
        settings.REDIS_PASSWORD,
        settings.DIFY_API_KEY,
        settings.AES_KEY,
        getattr(settings, "CONTACT_HASH_SALT", ""),
        settings.CLOUD_LLM_API_KEY,
    ]
    for value in env.values():
        for secret in secrets:
            if secret:
                assert secret not in value, "报告泄露了凭据！"


def test_default_report_path_is_under_docs() -> None:
    from scripts.n0_probe import default_report_path

    path = default_report_path()
    assert path.parent.name == "docs"
    assert path.suffix == ".md"
    assert "N0-基线实测报告" in path.name


# ============================================================================
# CLI（只测不触 I/O 的分支）
# ============================================================================
def test_cli_list_needs_no_environment(capsys: pytest.CaptureFixture[str]) -> None:
    assert n0_main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "N0 探针注册表" in out
    for key in KNOWN_KEYS:
        assert key in out


def test_cli_rejects_unknown_probe_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert n0_main(["--only", "mysql,not_a_probe"]) == 2
    assert "未知探针" in capsys.readouterr().out


def test_cli_rejects_bad_embedding_rounds(capsys: pytest.CaptureFixture[str]) -> None:
    assert n0_main(["--only", "embedding", "--embedding-rounds", "0"]) == 2
    assert "采样次数" in capsys.readouterr().out
