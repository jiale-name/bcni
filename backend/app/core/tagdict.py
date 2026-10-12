"""受控标签词表校验器（TagDictValidator）—— 铁律 10 的确定性组件之一。

🔴 为什么标签合法性不能写在 Service 的 if/else 里
================================================
「这个标签能不能被引用」有三条规则，且**三条的执行点不同**：

1. **编码格式**（``大写 + 下划线 + 数字``，≤64 字符）—— 在**写入前**校验（API-51/54）；
2. **唯一性** —— 由数据库唯一键 ``uk_tenant_tag_code`` 兜底（防并发），
   服务层「先查后插」只给人看（40901 的 ``data.existing_tag_id``）;
3. **引用合法性**（``talent_tag_rel.tag_code`` 必须取自**启用**项）——
   在**打标**时校验（API-56 / AI 打标）。

三条分属三个调用点，写散了就会出现「建议审核过了但打标时被拒」这类
**规则之间的缝隙**。本类把「可判定的部分」收成一个纯函数入口。

🔴 与「AI 不得自由造词」的关系（SQ-8 / F-3.4.1）
==============================================
AI 的输出**永远只能是文本**。本模块不信任 AI 给出的 ``tag_code``：
- AI 选标签 → 选出来的 ``tag_name`` 必须命中词表的**启用项**（或其同义词），
  落库存的是词表里的 ``tag_code``，**不是 AI 的原文**（`normalize_to_dict`）；
- AI 建新词 → 只能走「建议」（``status=0``），经 API-54 人工审核后才成为词表项。
"""

from __future__ import annotations

import re
from typing import Final

#: 标签编码规则：大写字母开头，大写字母 / 数字 / 下划线，≤64 字符
TAG_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

#: 标签名长度（接口 §4.4：API-53 的 tag_name 2~32 字符）
TAG_NAME_MIN = 2
TAG_NAME_MAX = 32


class TagDictValidator:
    """受控词表的纯函数校验（无 IO；唯一性查库由 DAO 兜底）。"""

    @staticmethod
    def is_valid_code(tag_code: str) -> bool:
        """编码格式是否合法（大写 + 下划线 + 数字，字母开头）。"""
        return bool(TAG_CODE_PATTERN.match(tag_code or ""))

    @staticmethod
    def is_valid_name(tag_name: str) -> bool:
        """标签名长度 2~32（与 API-53 的约束一致）。"""
        name = (tag_name or "").strip()
        return TAG_NAME_MIN <= len(name) <= TAG_NAME_MAX

    @staticmethod
    def derive_code(tag_name: str) -> str:
        """从标签名**派生**默认编码（人工创建未传 tag_code 时）。

        - ASCII 名（如 ``LLM Ops``）→ ``LLM_OPS``；
        - 非 ASCII 名（中文）→ ``TAG_`` + 名字 SHA-256 前 8 位十六进制（大写）——
          ❌ 不做拼音转码（第三方依赖 + 多音字歧义），可读性由 ``tag_name`` 承担，
          编码只承担「稳定唯一」。
        """
        name = (tag_name or "").strip()
        ascii_part = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
        if ascii_part and ascii_part.isascii():
            return ascii_part[:64] if len(ascii_part) <= 64 else ascii_part[:64]
        import hashlib

        return f"TAG_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8].upper()}"

    @staticmethod
    def normalize_to_dict(
        wanted: str, enabled_entries: dict[str, dict[str, object]]
    ) -> str | None:
        """把「AI/检索给出的标签名或同义词」归一到**词表启用项**的 ``tag_code``。

        :param wanted: AI 给出的标签名（原文）
        :param enabled_entries: ``{tag_code: {"name": str, "synonyms": [...]}}``（仅启用项）
        :return: 归一后的 ``tag_code``；词表里没有 → ``None``（**绝不**返回 AI 原文，
            否则非启用词就绕过了审核闸门）

        匹配顺序：精确名 → 同义词 → 大小写/空白不敏感的宽松名。
        """
        wanted_stripped = (wanted or "").strip()
        if not wanted_stripped:
            return None
        folded = wanted_stripped.casefold()
        for code, entry in enabled_entries.items():
            if str(entry.get("name") or "").strip() == wanted_stripped:
                return code
        for code, entry in enabled_entries.items():
            for syn in entry.get("synonyms") or []:
                if str(syn).strip() == wanted_stripped:
                    return code
        for code, entry in enabled_entries.items():
            if str(entry.get("name") or "").strip().casefold() == folded:
                return code
            for syn in entry.get("synonyms") or []:
                if str(syn).strip().casefold() == folded:
                    return code
        return None
