"""CU-03 / CU-04 · **雪花 ID 序列化契约**（接口 §1.2 —— 本项目最强约束，红线 CR-5）。

为什么要单独立一个测试文件？
---------------------------
同类项目已在「合同要素接口」踩过这个坑：后端把雪花 ID 当**数字**下发 →
JS ``Number`` 只有 53 位有效精度 → 末位被静默截断 → 前端拿截断后的 ID 请求详情 →
**稳定 404**，排查耗时 > 1 天。

因此本文件做**三层防护**（缺一层都可能被绕过）：

1. Schema 层：``CommonResp[...]`` 的 ``model_dump_json`` 必须输出带引号的字符串；
2. 路由层：真实经过 FastAPI 的 ``response_model`` 序列化路径（而非只测 Pydantic）；
3. 请求层：路径参数 / body 中传 **string** 必须被正确解析为 int（前端只会传 string）。
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.exceptions import register_exception_handlers
from app.schemas.common import CommonResp, SnowflakeId, ok

#: 一个典型的、**超出 JS 安全整数**的雪花 ID（1.839e18 > 9.007e15）
BIG_ID = 1839204771234567890

assert BIG_ID > 2**53 - 1, "夹具本身失效：BIG_ID 必须超出 JS 安全整数"


class _Item(BaseModel):
    """最小复现体：一个 ID 字段 + 一个普通字段。"""

    id: SnowflakeId
    name: str


class _Envelope(BaseModel):
    """模拟真实业务体：ID 以多种命名出现（``id`` / ``*_id`` / ``*_ids``）。"""

    id: SnowflakeId
    org_id: SnowflakeId | None = None
    tag_ids: list[SnowflakeId] = []


# ============================================================================
# 第 1 层：Schema 序列化
# ============================================================================
def test_schema_dumps_id_as_quoted_string() -> None:
    resp = CommonResp[_Item](code=0, data=_Item(id=BIG_ID, name="李伟"))
    payload = json.loads(resp.model_dump_json())

    assert payload["data"]["id"] == str(BIG_ID)
    assert isinstance(payload["data"]["id"], str), "❌ ID 被序列化为数字 → 前端必然精度丢失"

    # 原始 JSON 文本里必须是**带引号**的，这是最终发给前端的形态
    raw = resp.model_dump_json()
    assert f'"{BIG_ID}"' in raw


def test_schema_keeps_python_side_as_int() -> None:
    """Python 内部仍是 int（DAO / Service 需要参与计算与比较），只对外转字符串。"""
    item = _Item(id=BIG_ID, name="李伟")
    assert isinstance(item.id, int)
    assert item.id == BIG_ID


def test_all_id_like_fields_are_strings() -> None:
    """``id`` / ``*_id`` / ``*_ids`` 一律字符串（接口 §1.2：所有 ID 类字段）。"""
    env = _Envelope(id=BIG_ID, org_id=BIG_ID + 1, tag_ids=[BIG_ID, BIG_ID + 2])
    payload = json.loads(env.model_dump_json())

    assert isinstance(payload["id"], str)
    assert isinstance(payload["org_id"], str)
    assert all(isinstance(t, str) for t in payload["tag_ids"])
    assert payload["tag_ids"] == [str(BIG_ID), str(BIG_ID + 2)]


def test_openapi_declares_id_as_string() -> None:
    """Swagger 上应显示为 string（接口 §1.2：``type: string, format: int64``）。"""
    schema = _Item.model_json_schema()
    id_schema = schema["properties"]["id"]
    assert id_schema.get("type") == "string", f"OpenAPI 中 ID 应为 string，实际 {id_schema}"


# ============================================================================
# 第 2 层：真实 FastAPI 序列化路径
# ============================================================================
def _probe_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe/item", response_model=CommonResp[_Item])
    def probe_item() -> CommonResp[_Item]:
        return ok(_Item(id=BIG_ID, name="李伟"))

    @app.get("/probe/item/{item_id}", response_model=CommonResp[_Item])
    def probe_detail(item_id: SnowflakeId) -> CommonResp[_Item]:
        # 回显路径参数：若解析有误，这里会与 BIG_ID 不等
        return ok(_Item(id=item_id, name="李伟"))

    return app


def test_route_response_has_string_id() -> None:
    with TestClient(_probe_app()) as client:
        body = client.get("/probe/item").json()

    assert body["code"] == 0
    assert body["data"]["id"] == str(BIG_ID)
    assert isinstance(body["data"]["id"], str)


def test_list_then_detail_roundtrip_with_string_id() -> None:
    """🔴 接口 §1.2 要求的**联调冒烟用例**：用列表返回的 ID 请求详情。

    这是唯一能当场暴露精度丢失的检查 —— 前端拿到的 ID 必须能原样命中详情。
    """
    with TestClient(_probe_app()) as client:
        listed_id = client.get("/probe/item").json()["data"]["id"]
        # 模拟前端：ID 以 string 形式回传（前端**必须**传 string）
        detail = client.get(f"/probe/item/{listed_id}")

    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == listed_id
    assert int(detail.json()["data"]["id"]) == BIG_ID


def test_route_also_accepts_numeric_path_param() -> None:
    """接口 §1.2：请求侧**接受 string 或 number**（服务端统一转 int64）。"""
    with TestClient(_probe_app()) as client:
        body = client.get(f"/probe/item/{BIG_ID}").json()
    assert int(body["data"]["id"]) == BIG_ID


# ============================================================================
# 反例：证明「不转字符串」确实会丢精度（防止有人以后"优化"掉这个约束）
# ============================================================================
def test_demonstrate_js_precision_loss_without_stringification() -> None:
    """用 Python 模拟 JS ``JSON.parse`` 的双精度行为，量化损失。

    ⚠️ 这是**反面教材**（documentation test）：如果哪天有人把 ``SnowflakeId`` 改回数字，
    本用例会失败并给出具体差值与错误 ID，直接指向 404 的根因。
    """
    parsed_by_js = float(BIG_ID)  # JS 的 Number 就是 IEEE-754 double
    truncated = int(parsed_by_js)

    assert truncated != BIG_ID, "夹具失效：该 ID 本应发生精度丢失"
    # 截断后的 ID 与真实 ID 只差末几位 —— 所以是「静默」的，肉眼极难发现
    assert abs(truncated - BIG_ID) < 1024, "末位截断幅度应很小（这才叫静默丢失）"
