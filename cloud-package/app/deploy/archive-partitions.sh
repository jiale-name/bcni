#!/bin/bash
# ============================================================================
# 分区归档：**先转储，再删除**（CU-24，落地评审文档 §7.3 遗留 ⑥）
#
# 🔴 为什么不能直接用 `audit_partition.py --drop-before N --yes`
# -------------------------------------------------------------
# ``--drop-before`` 是**分区级删除**：快、不留碎片、正确，但**数据就此消失**。
# 若有合规留存要求（如「审计留存 ≥3 年」而在线只保 12 个月），
# 直接删等于**销毁证据**。故本脚本强制顺序：
#
#     ① 导出每个待删分区的**全部行**到冷文件（gzip 压缩）
#     ② **校验**（gzip 完整性 + 行数 > 0）—— 校验不过就**中止，一行都不删**
#     ③ 全部校验通过后，才执行 DROP PARTITION
#
# 「先删后导」或「边导边删」在单条 SQL 失败时会留下**无法回补的空洞**，
# 所以校验必须**全部完成后**才动删除。
#
# 用法：
#   ./archive-partitions.sh 12                 # 归档并删除超出 12 个月留存的分区
#   ./archive-partitions.sh 12 --dry-run       # 只导出+校验，不删（**建议先跑一次**）
#   TABLE=ai_call_log ./archive-partitions.sh 6
#
# ⚠️ 恢复方式（**不是「导入回原分区」那么简单，此处如实说明**）：
#    导出的是 TSV（``mysql --batch --raw``）。恢复需要先建好对应月份的分区，
#    再用 ``LOAD DATA LOCAL INFILE`` 载入；NULL 会以 ``\N`` 形式保留。
#    真要到「用得上归档」的那天，请按当时的合规要求做一次演练（别把恢复当理所当然）。
# ============================================================================
set -euo pipefail

RETENTION="${1:-12}"
DRY_RUN="false"
for arg in "$@"; do
    [ "$arg" = "--dry-run" ] && DRY_RUN="true"
done

TABLE="${TABLE:-all}"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${DEPLOY_DIR}/../backend" && pwd)"
ARCHIVE_DIR="${ARCHIVE_DIR:-${DEPLOY_DIR}/archive}"

if [ -x "${BACKEND_DIR}/.venv/Scripts/python.exe" ]; then
    PY="${BACKEND_DIR}/.venv/Scripts/python.exe"
elif [ -x "${BACKEND_DIR}/.venv/bin/python" ]; then
    PY="${BACKEND_DIR}/.venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi

mkdir -p "${ARCHIVE_DIR}"
STAMP="$(date '+%Y%m%d-%H%M%S')"
cd "${BACKEND_DIR}"

# DB 连接信息从 backend/.env 读（**不硬编码**，避免与 .env 漂移）
eval "$("${PY}" - <<'PYEOF'
from app.config import settings
print(f'DB_HOST="{settings.MYSQL_HOST}"')
print(f'DB_PORT="{settings.MYSQL_PORT}"')
print(f'DB_USER="{settings.MYSQL_USER}"')
print(f'DB_PASS="{settings.MYSQL_PASSWORD}"')
print(f'DB_NAME="{settings.MYSQL_DATABASE}"')
PYEOF
)"

if [ "${TABLE}" = "all" ]; then
    TABLES="audit_log ai_call_log"
else
    TABLES="${TABLE}"
fi

for tbl in ${TABLES}; do
    # 待删分区名由**已测试过的同一段逻辑**给出（不在 shell 里重写一遍月份算术）
    NAMES="$("${PY}" -c "
from scripts.audit_partition import droppable_partitions
print(' '.join(p.name for p in droppable_partitions(${RETENTION}, '${tbl}')))
")"
    if [ -z "${NAMES}" ]; then
        echo "[${tbl}] 没有超出 ${RETENTION} 个月留存期的分区，跳过。"
        continue
    fi

    echo "[${tbl}] 待归档分区：${NAMES}"
    declare -a VERIFIED=()
    FAILED="false"

    for name in ${NAMES}; do
        out="${ARCHIVE_DIR}/${tbl}_${name}_${STAMP}.tsv.gz"
        echo "  → 导出 ${name} → ${out}"

        # ``--batch --raw``：制表符分隔、不做字符转义（与 LOAD DATA 对称）
        # 用 ``PARTITION (name)`` 精确限定分区 —— 不带它整表导出会重复导出 12 个月
        if ! mysql -h"${DB_HOST}" -P"${DB_PORT}" -u"${DB_USER}" -p"${DB_PASS}" \
                --batch --raw "${DB_NAME}" \
                -e "SELECT * FROM \`${tbl}\` PARTITION (\`${name}\`)" \
                | gzip -c > "${out}"; then
            echo "  ✗ 导出失败，**中止整个流程（不删除任何分区）**"
            FAILED="true"
            break
        fi

        # 校验①：gzip 流完整（截断的归档是最危险的——看着有文件，实际读不完）
        if ! gzip -t "${out}"; then
            echo "  ✗ 归档文件损坏（gzip -t 失败），中止"
            FAILED="true"
            break
        fi
        # 校验②：行数 > 0（空文件通常意味着分区名写错或权限不足，
        #         此时删除会**静默丢数据**，是最不能容忍的一种失败）
        lines="$(gzip -dc "${out}" | wc -l)"
        if [ "${lines}" -lt 1 ]; then
            echo "  ✗ 归档内容为空（0 行），中止"
            FAILED="true"
            break
        fi
        echo "  ✓ ${name} 归档校验通过（${lines} 行）"
        VERIFIED+=("${name}")
    done

    if [ "${FAILED}" = "true" ]; then
        echo "[${tbl}] 🔴 有分区归档失败：**未执行任何删除**。已导出的文件保留在 ${ARCHIVE_DIR}（可重跑覆盖）。"
        exit 2
    fi

    if [ "${DRY_RUN}" = "true" ]; then
        echo "[${tbl}] （dry-run）已归档并校验 ${#VERIFIED[@]} 个分区，**未删除**。"
        echo "        确认无误后重跑（去掉 --dry-run）即可执行删除。"
        continue
    fi

    echo "[${tbl}] 归档完毕，执行 DROP PARTITION：${VERIFIED[*]}"
    "${PY}" -c "
from scripts.audit_partition import drop_partitions
drop_partitions('${VERIFIED[*]}'.split(), '${tbl}')
"
    echo "[${tbl}] ✓ 已删除 ${#VERIFIED[@]} 个分区，归档在 ${ARCHIVE_DIR}"
done

echo "全部完成。归档目录：${ARCHIVE_DIR}"
