#!/bin/bash
# ============================================================================
# 审计 / AI 日志专用库账号（CU-24，「只追加」的**第三层**保证）
#
# 由 MySQL 官方镜像的 docker-entrypoint 在**首次初始化**数据卷时自动执行
# （``/docker-entrypoint-initdb.d/*.sh``）。此时 ``MYSQL_DATABASE`` 已建好。
#
# 🔴 为什么需要这一层
# -------------------
# 「审计日志只追加」在本项目里由三层共同保证：
#   ① 模型层：``AuditLog`` 没有 ``is_deleted`` / ``updated_at`` 列；
#   ② DAO 层：``audit_dao.__all__`` 只导出 insert / list / count；
#   ③ **库层（本脚本）**：写入用的账号**没有** ``UPDATE`` / ``DELETE`` 权限。
# ①② 挡的是**应用代码**——有人直接 ``mysql -e "DELETE FROM audit_log"`` 就全破了。
# ③ 让「改审计」在数据库权限层面就不成立。
#
# ⚠️ 这个账号**不能**用于分区维护：``ALTER TABLE ... DROP PARTITION`` 需要 ALTER 权限，
#    分区补足 / 回收由运维用**主账号**执行 ``scripts/audit_partition.py``（README 有 cron 说明）。
#
# 手工执行（数据卷已存在时，initdb 不会再跑）：
#   docker exec -i talent-mysql bash -s < deploy/mysql-init/01-audit-account.sh
# ============================================================================
set -euo pipefail

AUDIT_USER="${AUDIT_DB_USER:-audit_writer}"
AUDIT_PASS="${AUDIT_DB_PASSWORD:-}"
DB="${MYSQL_DATABASE:-talent_platform_v2}"

if [ -z "$AUDIT_PASS" ]; then
    echo "[01-audit-account] 未提供 AUDIT_DB_PASSWORD，跳过审计专用账号的创建。"
    echo "                   ⇒ 后端将退回主账号（功能正常，但缺少库级「只追加」保证）。"
    exit 0
fi

echo "[01-audit-account] 创建审计专用账号 '${AUDIT_USER}'（仅 SELECT + INSERT）..."

# ``--protocol=socket``：initdb 阶段用本地 socket 最稳（TCP 可能还没就绪）
mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
-- 幂等：重复执行不报错
CREATE USER IF NOT EXISTS '${AUDIT_USER}'@'%' IDENTIFIED BY '${AUDIT_PASS}';

-- 🔴 **只授 SELECT + INSERT，一个字母都不多**：
--    没有 UPDATE  ⇒ 改不动已写的审计记录（合规要求的「防篡改」）；
--    没有 DELETE  ⇒ 删不掉（留存期内不可能通过应用链路抹痕迹）；
--    没有 ALTER   ⇒ 动不了分区结构（分区维护必须显式用主账号，留下运维动作痕迹）。
--    没有 DROP    ⇒ 更不可能整表清空。
GRANT SELECT, INSERT ON \`${DB}\`.\`audit_log\`    TO '${AUDIT_USER}'@'%';
GRANT SELECT, INSERT ON \`${DB}\`.\`ai_call_log\`  TO '${AUDIT_USER}'@'%';

FLUSH PRIVILEGES;

-- 自检：把实际生效的授权打出来（人工核对时不用再连一次库）
SHOW GRANTS FOR '${AUDIT_USER}'@'%';
SQL

echo "[01-audit-account] 完成。后端 .env 需设置："
echo "                   AUDIT_DB_USER=${AUDIT_USER}"
echo "                   AUDIT_DB_PASSWORD=<同上>"
