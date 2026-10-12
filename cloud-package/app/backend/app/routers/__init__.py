"""HTTP 路由层（架构 §3.3：``routers/`` 只做声明与绑定）。

``api_router`` 是**唯一**对外暴露的路由聚合点 —— :mod:`app.main` 只 include 它，
后续新增模块（``users_router`` / ``talents_router`` …）在此登记，main.py **不再改动**。

挂载路径：``/api/v1``（接口 §1.1 BaseURL），由 main.py 统一加前缀。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.agent_router import router as agent_router
from app.routers.assessment_router import router as assessment_router
from app.routers.ai_router import router as ai_router
from app.routers.audit_router import router as audit_router
from app.routers.auth_router import router as auth_router
from app.routers.change_router import router as change_router
from app.routers.dedup_router import router as dedup_router
from app.routers.dict_router import router as dict_router
from app.routers.import_router import router as import_router
from app.routers.org_router import router as org_router
from app.routers.privacy_router import router as privacy_router
from app.routers.rbac_router import router as rbac_router
from app.routers.review_router import router as review_router
from app.routers.search_router import router as search_router
from app.routers.tag_router import router as tag_router
from app.routers.talent_router import router as talent_router
from app.routers.user_router import router as user_router

api_router = APIRouter()

# ---------- A · 认证与会话（CU-04 ✅）----------
api_router.include_router(auth_router)

# ---------- C · 角色与权限（CU-05 ✅ API-18~22）----------
api_router.include_router(rbac_router)

# ---------- B · 组织与用户（CU-06 ✅ API-07~17）----------
api_router.include_router(org_router)
api_router.include_router(user_router)

# ---------- D · 数据字典与配置（CU-07 ✅ API-23~27）----------
api_router.include_router(dict_router)

# ---------- E · 审计日志（CU-08 ✅ API-28~29）----------
# ⚠️ 本模块**只有 GET 与 POST(export)**，没有任何写接口 ——
#    审计记录只由 Service 层在业务动作中写入（接口 §4：「AUDIT 全列只读」）。
api_router.include_router(audit_router)

# ---------- F / O · AI 中台（CU-10~12 ✅ API-81~89）----------
# ⚠️ 本模块**没有任何写「日志」或删「日志」的接口** ——
#    ai_call_log 只由真实调用点（AiLogService.record）写入，留存靠按月分区 DROP PARTITION。
#    HTTP 写入口 = 「伪造/抹掉调用痕迹」的正门，从架构上不开。
api_router.include_router(ai_router)

# ---------- P · Agent 与人工介入（CU-13 ✅ API-90~95）----------
# ⚠️ 只读接口 + 两个状态迁移接口（resolve / retry），**不提供创建任务**：
#    任务由业务动作（解析、问答、合并）在 Service 层创建，API-95 只能「重试已失败的任务」。
api_router.include_router(agent_router)

# ---------- Q · 人才测评任务（Batch 2A，API-119~124）----------
api_router.include_router(assessment_router)

# ---------- G · 简历导入（CU-14 ✅ API-36~43）----------
# ⚠️ API-39（SSE）是本模块唯一 async 端点（长连接不占线程池）；
#    其余端点保持统一同步驱动（def → 线程池）。
api_router.include_router(import_router)

# ---------- H · 分层复核（CU-16 ✅ API-44~49）----------
api_router.include_router(review_router)

# ---------- I · 受控标签（CU-17 ✅ API-50~56）----------
api_router.include_router(tag_router)

# ---------- J · 查重合并（CU-19 ✅ API-57~61）----------
api_router.include_router(dedup_router)

# ---------- K · 检索与问答（CU-20/21 ✅ API-62/63）----------
# ⚠️ API-63（SSE）是本模块唯一 async 端点（长连接不占线程池）；
#    其余端点保持统一同步驱动（def → 线程池）。
api_router.include_router(search_router)

# ---------- M · 变更留痕与版本回溯（CU-22 ✅ API-67~70）----------
# ⚠️ 本模块**只有 GET**：留痕由业务动作（解析 / 复核 / 合并 / 重解析 / 审批删除）
#    在 Service 层写入。HTTP 写入口 = 「伪造合规证据」的正门，从架构上不开。
api_router.include_router(change_router)

# ---------- N · 个人权利响应（CU-22 ✅ API-71~80）----------
# ⚠️ 本模块的「提交类」接口（72/75/78）只允许**本人**调用（EMP 权限点）；
#    「处理类」接口（74/77）在服务层**再补一次角色判定** —— 详见 privacy_router 模块 docstring。
api_router.include_router(privacy_router)

# ---------- F · 人才档案主线（CU-29 ✅ API-30~35 / 64~66）----------
# ⚠️ 本模块是**档案主线**（列表/详情/编辑/归档/画像/导出）＋三个跨域接口
#    （会话回看/清空、脱敏自检）。它补齐了 §6.1 **B-8 / B-9**：
#    API-30~35 与 API-64~66 此前**没有任何 CU 认领**，`openapi.json` 中实测不存在，
#    而前端（批次 14）已按契约实现 → 本模块落地点亮前端，无需改前端。
# ⚠️ API-31 / API-34 **刻意不声明权限点**，可见性全部交给数据域
#    （EMP 的「仅本人」由 DataScopeFilter 的 SELF 范围实现）——
#    理由见 talent_router 模块 docstring 的「说明 ①」。
api_router.include_router(talent_router)

# ---------- G ~ N · 后续单元在此登记 ----------
# ⚠️ CU-06 原计划含 API-18~22（角色/权限 CRUD）—— 该部分已由 CU-05 实现，
#    CU-06 只落了 API-07~17（组织 / 岗位 / 用户 CRUD），未重复实现。

__all__ = ["api_router"]
