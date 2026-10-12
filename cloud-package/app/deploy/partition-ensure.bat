@echo off
REM ============================================================================
REM 分区补足定时任务（Windows / CU-24）
REM
REM 为什么需要它：audit_log / ai_call_log 按月分区且末尾有 pmax 兜底，
REM 忘记补分区**不会报错**（数据进 pmax），但会**丧失归档能力** ——
REM 之后无法用 DROP PARTITION 回收，只能全表 DELETE。详见 partition-ensure.sh 头部。
REM
REM 注册为计划任务（每月 1 日 03:00），在**管理员** cmd 里执行一次：
REM   schtasks /Create /TN "TalentPartitionEnsure" /SC MONTHLY /D 1 /ST 03:00 ^
REM     /TR "D:\gan\16个人项目\deploy\partition-ensure.bat" /RL HIGHEST /F
REM
REM 查看 / 手动触发 / 删除：
REM   schtasks /Query /TN "TalentPartitionEnsure" /V /FO LIST
REM   schtasks /Run   /TN "TalentPartitionEnsure"
REM   schtasks /Delete /TN "TalentPartitionEnsure" /F
REM
REM ⚠️ 不要用「启动时触发 + 重复间隔」，那会在一天内反复 REORGANIZE pmax；
REM    只有 pmax 里有数据时补分区才是必要的，每月一次足够（默认预留 3 个月）。
REM ============================================================================
setlocal

set "DEPLOY_DIR=%~dp0"
set "BACKEND_DIR=%DEPLOY_DIR%..\backend"
set "PY=%BACKEND_DIR%\.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [FAIL] 未找到虚拟环境：%PY%
    echo        请先在 backend\ 下创建 .venv 并安装依赖。
    exit /b 1
)

echo [%DATE% %TIME%] 分区补足开始
pushd "%BACKEND_DIR%"
"%PY%" -m scripts.audit_partition --ensure-next --months 3 --table all
set "RC=%ERRORLEVEL%"
popd

if not "%RC%"=="0" (
    echo [FAIL] 分区补足失败（exit=%RC%）：常见原因 ① MySQL 未启动；② .env 密码未填。
    exit /b %RC%
)
echo [%DATE% %TIME%] 分区补足完成
endlocal
