"""CU-01 · 配置齐全性与 .env 可解析性（**本文件把「.env 是否齐全」变成自动化检查**）。

为什么需要它
------------
「配置漏了」在本项目有两类高发事故，且都不会在开发时立刻暴露：

1. **字段缺失** → 走 `Settings` 的默认值，本地能跑，上测试/生产环境才因默认值不当而失败
   （例如漏了 `MYSQL_PASSWORD`、`JWT_SECRET_KEY` 仍是占位值）。
2. **`.env` 里有拼错的键名** → `extra="ignore"` 会**静默忽略**，使用者以为生效了其实没有。
   （真实案例：把另一个项目的 `.env` 拷进来，19 个键全被静默忽略，62 个键缺失。）

因此本文件做四件事：
- 校验 `.env.example` **覆盖全部 Settings 字段**（新增配置项必须同步模板 —— 架构 §5 要求）；
- 校验 `.env.example` **本身可被解析**（模板写错等于新人一上手就踩坑）；
- 校验**真实 `.env`**（若存在）同样齐全，并**逐键比对**出多余/缺失（值一律不打印）；
- 钉死一类**只在有 .env 时才暴露**的解析 bug：列表型配置（`CORS_ORIGINS`）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ENV_EXAMPLE = _BACKEND_DIR / ".env.example"
_ENV_REAL = _BACKEND_DIR / ".env"

#: `Settings` 中需要外部提供的配置项（大写字段）
_NEEDED_KEYS: set[str] = {name for name in Settings.model_fields if name.isupper()}

#: 允许「不在 .env.example 中显式写出」的键（都有安全默认值，且写出来反而更容易被误改）
_OPTIONAL_IN_TEMPLATE: set[str] = set()


def _parse_env_keys(path: Path) -> list[str]:
    """取出 .env 文件的键名列表（**不读取值**，避免密钥进入测试输出）。"""
    # 用 utf-8 宽读：.env 允许中文注释
    text = path.read_text(encoding="utf-8", errors="replace")
    return [m.group(1) for m in re.finditer(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=", text, re.M)]


# ============================================================================
# .env.example：模板守卫
# ============================================================================
def test_env_example_covers_every_settings_field() -> None:
    """🔴 新增配置项必须同步写入 `.env.example`（架构 §5：禁止硬编码、配置集中）。"""
    keys = set(_parse_env_keys(_ENV_EXAMPLE))
    missing = sorted(_NEEDED_KEYS - keys - _OPTIONAL_IN_TEMPLATE)
    assert not missing, (
        f"`.env.example` 缺少 {len(missing)} 个配置项，请补齐（新增配置项必须同步模板）：{missing}"
    )


def test_env_example_has_no_unknown_keys() -> None:
    """模板里不得出现 `Settings` 不认的键 —— 否则使用者以为配了、实际被静默忽略。"""
    keys = set(_parse_env_keys(_ENV_EXAMPLE))
    unknown = sorted(keys - _NEEDED_KEYS)
    assert not unknown, f"`.env.example` 含 Settings 不识别的键（拼写错误？）：{unknown}"


def test_env_example_has_no_duplicate_keys() -> None:
    keys = _parse_env_keys(_ENV_EXAMPLE)
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, f"`.env.example` 存在重复键（后者覆盖前者，极易误读）：{duplicates}"


def test_env_example_is_parseable() -> None:
    """模板本身必须能被 pydantic-settings 解析 —— 否则新人复制即踩坑。"""
    loaded = Settings(_env_file=str(_ENV_EXAMPLE))
    assert loaded.APP_PORT == 8002
    assert loaded.API_V1_PREFIX == "/api/v1"
    assert loaded.REDIS_PROTOCOL == 2, "REDIS_PROTOCOL 必须为 2（RESP2，兼容旧版 Redis）"
    assert loaded.JWT_SECRET_KEY


# ============================================================================
# 真实 .env：齐全性检查（存在才检查；缺文件时 skip）
# ============================================================================
@pytest.mark.skipif(not _ENV_REAL.exists(), reason="尚无 backend/.env（复制 .env.example 即可）")
def test_real_env_covers_every_settings_field() -> None:
    """真实 `.env` 必须覆盖全部配置项 —— 缺项会静默走默认值，是最隐蔽的事故源。"""
    keys = set(_parse_env_keys(_ENV_REAL))
    missing = sorted(_NEEDED_KEYS - keys)
    assert not missing, (
        f"`backend/.env` 缺少 {len(missing)} 个配置项（会静默使用默认值）：{missing}\n"
        "修复：以 .env.example 为准补齐，或直接重新生成。"
    )


@pytest.mark.skipif(not _ENV_REAL.exists(), reason="尚无 backend/.env")
def test_real_env_has_no_foreign_keys() -> None:
    """真实 `.env` 不得含 Settings 不识别的键。

    ⚠️ 这条专门防「把别的项目的 .env 拷进来」——`extra="ignore"` 会静默忽略，
    不报错但也不生效，排查成本极高。
    """
    keys = set(_parse_env_keys(_ENV_REAL))
    foreign = sorted(keys - _NEEDED_KEYS)
    assert not foreign, (
        f"`backend/.env` 含 {len(foreign)} 个非本项目配置键（疑为其他项目的 .env 误拷贝）：{foreign}"
    )


@pytest.mark.skipif(not _ENV_REAL.exists(), reason="尚无 backend/.env")
def test_real_env_has_no_duplicate_keys() -> None:
    keys = _parse_env_keys(_ENV_REAL)
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, f"`backend/.env` 存在重复键：{duplicates}"


@pytest.mark.skipif(not _ENV_REAL.exists(), reason="尚无 backend/.env")
def test_real_env_critical_values_are_sane() -> None:
    """真实 `.env` 的**结构性**检查（只验证形态，不打印值）。"""
    loaded = Settings(_env_file=str(_ENV_REAL))
    assert "/" not in loaded.MYSQL_DATABASE, "MYSQL_DATABASE 只写库名，不要带 schema/路径"
    assert 0 < loaded.MYSQL_PORT < 65536
    assert 0 < loaded.REDIS_PORT < 65536
    assert loaded.API_V1_PREFIX.startswith("/"), "API_V1_PREFIX 必须以 / 开头"
    assert loaded.CORS_ORIGINS, "CORS_ORIGINS 不得为空，否则前端跨域全被拦"
    assert len(loaded.JWT_SECRET_KEY.encode()) >= 32, "HS256 密钥应 ≥32 字节（RFC 7518）"
    assert loaded.REDIS_PROTOCOL in (2, 3)


# ============================================================================
# 回归：列表型配置的解析（只在「存在 .env」时才暴露的启动级 bug）
# ============================================================================
def test_cors_origins_accepts_comma_separated(tmp_path: Path) -> None:
    """🔴 回归用例：`CORS_ORIGINS=a,b` 必须可解析。

    历史 bug：字段声明为 `list[str]` 时，pydantic-settings 会在**字段校验器之前**
    先做 `json.loads`，于是逗号分隔写法直接抛
    `SettingsError: error parsing value for field "CORS_ORIGINS"` ——
    且发生于导入 `settings` 时，表现为**服务完全起不来**。
    修复：字段加 `NoDecode`，由 `_split_cors_origins` 负责切分。
    """
    env = tmp_path / ".env"
    env.write_text("CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173\n", encoding="utf-8")
    loaded = Settings(_env_file=str(env))
    assert loaded.CORS_ORIGINS == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_cors_origins_accepts_json_array(tmp_path: Path) -> None:
    """JSON 数组写法同样要支持（避免使用者两种写法二选一踩坑）。"""
    env = tmp_path / ".env"
    env.write_text('CORS_ORIGINS=["http://a.test","http://b.test"]\n', encoding="utf-8")
    loaded = Settings(_env_file=str(env))
    assert loaded.CORS_ORIGINS == ["http://a.test", "http://b.test"]


def test_cors_origins_single_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("CORS_ORIGINS=http://only.test\n", encoding="utf-8")
    assert Settings(_env_file=str(env)).CORS_ORIGINS == ["http://only.test"]


def test_cors_origins_trims_whitespace(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("CORS_ORIGINS= http://a.test , http://b.test ,\n", encoding="utf-8")
    assert Settings(_env_file=str(env)).CORS_ORIGINS == ["http://a.test", "http://b.test"]


def test_cors_origins_falls_back_to_default_when_absent(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("APP_PORT=8002\n", encoding="utf-8")
    assert Settings(_env_file=str(env)).CORS_ORIGINS == ["http://localhost:5173"]


# ============================================================================
# 回归：类型转换（.env 里的值都是字符串，必须被正确强制为 int/bool）
# ============================================================================
def test_numeric_and_bool_values_are_coerced(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "APP_PORT=8003",
                "REDIS_PROTOCOL=2",
                "RAG_RECALL_TOP_K=20",
                "RAG_NO_ANSWER_THRESHOLD=0.35",
                "DEBUG=true",
                "RATE_LIMIT_ENABLED=false",
                "SNOWFLAKE_EPOCH_MS=1704038400000",
            ]
        ),
        encoding="utf-8",
    )
    loaded = Settings(_env_file=str(env))
    assert loaded.APP_PORT == 8003 and isinstance(loaded.APP_PORT, int)
    assert loaded.REDIS_PROTOCOL == 2
    assert loaded.RAG_RECALL_TOP_K == 20
    assert loaded.RAG_NO_ANSWER_THRESHOLD == pytest.approx(0.35)
    assert loaded.DEBUG is True
    assert loaded.RATE_LIMIT_ENABLED is False
    assert loaded.SNOWFLAKE_EPOCH_MS == 1704038400000
