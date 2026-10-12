#!/bin/bash
# ============================================================================
# 分区补足定时任务包装（CU-24，落地评审文档 §7.3 遗留 ①）
#
# 🔴 为什么必须是「定时任务」而不是「记得手动跑」
# ----------------------------------------------
# ``audit_log`` / ``ai_call_log`` 都是**按月 RANGE 分区**且末尾有 ``pmax`` 兜底。
# 兜底分区让「忘了补分区」**不会报错** —— 数据照常写入 ``pmax``。
# 等到想按留存期回收时才发现在 ``pmax`` 里删不掉（``DROP PARTITION`` 只能整块丢），
# 只能退化成 ``DELETE``（全表扫 + 写 undo，亿级表上等同事故）。
# **静默降级到「丧失归档能力」** 正是这个任务存在的理由。
#
# 建议（第 1 日执行，留出 3 个月缓冲）：
#   Linux crontab:  0 3 1 * *  /path/to/deploy/partition-ensure.sh  >> /var/log/talent-partition.log 2>&1
#   Windows 计划任务: 见 partition-ensure.bat 头部说明
#
# 用法：
#   ./partition-ensure.sh              # 两张表都补（默认 3 个月）
#   ./partition-ensure.sh 6            # 补 6 个月
# ============================================================================
set -euo pipefail

MONTHS="${1:-3}"
# 脚本位于 deploy/，后端在其同级目录 backend/
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${DEPLOY_DIR}/../backend" && pwd)"

# 优先用项目虚拟环境（依赖版本与开发环境一致；裸 python 可能缺 pymysql）
if [ -x "${BACKEND_DIR}/.venv/Scripts/python.exe" ]; then
    PY="${BACKEND_DIR}/.venv/Scripts/python.exe"     # Windows(Git Bash)
elif [ -x "${BACKEND_DIR}/.venv/bin/python" ]; then
    PY="${BACKEND_DIR}/.venv/bin/python"             # Linux/macOS
else
    PY="$(command -v python3 || command -v python)"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 分区补足开始（months=${MONTHS}）"
cd "${BACKEND_DIR}"

# ``--table all`` 一次覆盖 TBL-28 + TBL-24。
# ⚠️ 幂等：已存在的月份会跳过且不发任何 SQL（不会无谓 REORGANIZE 重建 pmax）。
"${PY}" -m scripts.audit_partition --ensure-next --months "${MONTHS}" --table all

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 分区补足完成"
