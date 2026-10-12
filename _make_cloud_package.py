# -*- coding: utf-8 -*-
"""生成「云端部署打包目录」cloud-package/（纯复制：只读源目录，不删除、不改动任何源文件）。

设计原则
--------
1. **白名单**：只复制明确需要上服务器 / 需要交付的部分，不靠「排除法」猜；
2. **零删除**：全程只用 ``shutil.copy*``，不做任何 ``rm`` / ``rmtree``；
3. **敏感项硬排除**：真实 ``.env``（含密钥）、``backend/data/uploads``（含简历原件）、
   根 ``data/goldset``（含个人信息）一律不进包 —— 即使它们在白名单目录内。

用法：python _make_cloud_package.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "cloud-package"

# 任何层级都跳过的缓存 / 虚拟环境 / 编辑器产物
CACHE_DIRS = (
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".pytest_tmp",
    ".pytest_out",
    ".ruff_cache",
    ".mypy_cache",
    ".git",
    ".idea",
    ".vscode",
    ".vite",
    "htmlcov",
    "dist-ssr",
)
CACHE_FILES = ("*.pyc", "*.pyo", "*.log", ".DS_Store")

_IGNORE = shutil.ignore_patterns(*CACHE_DIRS, *CACHE_FILES)


def copy_dir(src: Path, dst: Path) -> None:
    """整目录复制（自动跳过缓存/虚拟环境）。"""
    if not src.is_dir():
        print(f"  [跳过] 源目录不存在：{src}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=_IGNORE, dirs_exist_ok=True)
    n = sum(1 for _ in dst.rglob("*") if _.is_file())
    print(f"  [复制] {src.relative_to(ROOT)} -> {dst.relative_to(PKG)}  ({n} 个文件)")


def copy_files(src_dir: Path, names: list[str], dst_dir: Path) -> None:
    """按文件名白名单复制单层文件。"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        s = src_dir / name
        if not s.is_file():
            print(f"  [跳过] 文件不存在：{s}")
            continue
        shutil.copy2(s, dst_dir / name)
        print(f"  [复制] {s.relative_to(ROOT)}")


def main() -> None:
    print("== 1/5 后端（源码 / 迁移 / 脚本 / 测试）==")
    be_src, be_dst = ROOT / "backend", PKG / "app" / "backend"
    for sub in ("app", "alembic", "scripts", "tests"):
        copy_dir(be_src / sub, be_dst / sub)
    copy_files(
        be_src,
        ["alembic.ini", "pyproject.toml", "requirements.txt", ".env.example", "README.md"],
        be_dst,
    )
    print("  [排除] backend/data/uploads（简历原件，敏感）、backend/.env（真实密钥）、")
    print("         backend/logs、backend/tmp、各 *.junit*.xml 测试报告")

    print("== 2/5 前端（构建产物 dist / 源码 src / 工程配置）==")
    fe_src, fe_dst = ROOT / "frontend", PKG / "app" / "frontend"
    for sub in ("src", "dist"):
        copy_dir(fe_src / sub, fe_dst / sub)
    copy_files(
        fe_src,
        [
            "index.html",
            "package.json",
            "package-lock.json",
            "tsconfig.json",
            "tsconfig.node.json",
            "vite.config.ts",
            ".env.example",
        ],
        fe_dst,
    )
    print("  [排除] frontend/node_modules（218MB，服务器上 npm ci 重建）")

    print("== 3/5 部署产物 deploy/ ==")
    copy_dir(ROOT / "deploy", PKG / "app" / "deploy")

    print("== 4/5 文档 docs/ 与设计资产 ==")
    copy_dir(ROOT / "docs", PKG / "docs")
    copy_dir(ROOT / "prototype", PKG / "design" / "prototype")
    copy_files(ROOT, ["AI数字化人才平台-架构设计.html"], PKG / "design")

    print("== 5/5 环境与依赖清单 env/ ==")
    # ⚠️ 后端与前端都有 .env.example / package*.json，必须带前缀改名后入包，
    #    否则同名文件互相覆盖（第一版脚本实测：env/ 只剩 4 个文件）。
    env_dst = PKG / "env"
    env_dst.mkdir(parents=True, exist_ok=True)
    pairs = [
        (be_src / "requirements.txt", env_dst / "backend-requirements.txt"),
        (be_src / ".env.example", env_dst / "backend-env.example"),
        (fe_src / "package.json", env_dst / "frontend-package.json"),
        (fe_src / "package-lock.json", env_dst / "frontend-package-lock.json"),
        (fe_src / ".env.example", env_dst / "frontend-env.example"),
    ]
    for s, d in pairs:
        if not s.is_file():
            print(f"  [跳过] 文件不存在：{s}")
            continue
        shutil.copy2(s, d)
        print(f"  [复制] {s.relative_to(ROOT)} -> env/{d.name}")

    # ---- 体积与文件数汇总 ----
    print("\n== 汇总 ==")
    total = 0
    for child in sorted(PKG.iterdir()):
        if not child.is_dir():
            continue
        size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
        cnt = sum(1 for f in child.rglob("*") if f.is_file())
        total += size
        print(f"  {child.name:<10} {cnt:>5} 文件  {size / 1024 / 1024:>8.2f} MB")
    print(f"  {'合计':<10} {'':>5}       {total / 1024 / 1024:>8.2f} MB")


if __name__ == "__main__":
    main()
