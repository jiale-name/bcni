"""向量索引全量重建脚本（CU-18 —— 数据自有、可全量重建的落点）。

为什么这个脚本存在
====================
向量库（Milvus）存的是 MySQL 权威数据的**派生索引**。派生索引有个重要性质：
**任何时刻丢了都能重建**。于是「向量和主库对不上」这类事故，
处置方式不是「查差异、补数据」，而是「直接全量重建」—— 简单、可证、不依赖人工对账。

本脚本就是那条重建路径：从 ``talent_base`` 读出全部**有效**档案 → 组装脱敏画像文本
→ （可选）经模型网关算向量 → 写入检索提供方。

🔴 CU-18 状态与缺口
====================
CU-18 在评审文档中为「🟡 正在设计」，缺口是 **Embedding 吞吐未实测（H-19）**。
故本脚本的「真实向量计算 + 吞吐标定」部分是**占位骨架**：

- 脱敏画像文本组装：✅ 已实现（复用 ``core/crypto`` 的脱敏，明文身份不入向量库）；
- 向量计算（经网关 ``scene=embedding``）：⚠️ **占位** —— 吞吐未标定前，
  默认**不**批量算向量（``--no-vector`` 缺省开），只把脱敏文本交给 Provider
  （关键词降级实现直接可用，Milvus 实现会因缺向量而跳过入库）；
- 批大小 / 并发 / 失败重试：待 H-19 标定后补齐。

用法::

    python -m scripts.rebuild_index --dry-run            # 只预览，不写
    python -m scripts.rebuild_index --tenant-id 1001     # 全量重建某租户
    python -m scripts.rebuild_index --limit 100          # 只处理前 100 条（冒烟）
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.dao.session import SessionLocal
from app.models.enums import TalentDataStatus
from app.models.talent import TalentBase
from app.retrieval import get_retrieval_provider
from app.retrieval.profile import build_profile_text
from scripts._console import ensure_utf8_stdout


def rebuild(
    *,
    tenant_id: int | None = None,
    limit: int | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """全量重建。返回 ``{"total": 处理条数, "ingested": 成功入库条数}``。

    :param tenant_id: 只重建该租户；``None`` = 全部租户
    :param limit: 只处理前 N 条（冒烟 / 灰度）
    :param dry_run: 只预览不写（默认 True，**破坏性弱**但仍是全量扫描，慎用于生产）
    """
    provider = get_retrieval_provider()
    conditions = [
        TalentBase.is_deleted == 0,
        # 只索引「有效」档案（待复核 / 已合并 / 已删除不进检索）
        TalentBase.data_status == int(TalentDataStatus.ACTIVE),
    ]
    if tenant_id is not None:
        conditions.append(TalentBase.tenant_id == int(tenant_id))
    stmt = select(TalentBase).where(*conditions).order_by(TalentBase.id)
    if limit is not None:
        stmt = stmt.limit(int(limit))

    total = 0
    ingested = 0
    db = SessionLocal()
    try:
        for talent in db.execute(stmt).scalars():
            total += 1
            text = build_profile_text(talent)
            if not text:
                continue  # 无任何可索引字段的档案：跳过
            if dry_run:
                ingested += 1  # 预览：计入「将入库」条数
                continue
            pk = provider.ingest(
                db,
                talent_id=int(talent.id),
                tenant_id=int(talent.tenant_id),
                org_id=int(talent.org_id) if talent.org_id is not None else None,
                field_name="profile",
                chunk_text=text,
                # 🔴 vector 留空：真实 embedding 待 H-19 吞吐标定后接入（CU-18 缺口）
                vector=None,
            )
            if pk:
                ingested += 1
    finally:
        db.close()
    return {"total": total, "ingested": ingested}


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="向量索引全量重建（CU-18）")
    parser.add_argument("--tenant-id", type=int, default=None, help="只重建指定租户（默认全部）")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条（冒烟用）")
    parser.add_argument("--dry-run", action="store_true", default=True, help="只预览不写（默认开）")
    parser.add_argument("--yes", action="store_true", help="确认执行写入（关闭预览）")
    args = parser.parse_args(argv)

    dry_run = not args.yes
    result = rebuild(tenant_id=args.tenant_id, limit=args.limit, dry_run=dry_run)
    mode = "预览（dry-run，未写入）" if dry_run else "已写入"
    print(f"\n[{mode}] 待处理/已处理 {result['total']} 条，将入库/已入库 {result['ingested']} 条。")
    if dry_run:
        print("（确认无误后加 --yes 真正写入。⚠️ 真实向量计算待 H-19 吞吐标定后接入。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
