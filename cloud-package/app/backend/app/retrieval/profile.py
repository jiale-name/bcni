"""画像文本组装（脱敏）—— CU-18 向量化的输入侧。

从 ``talent_base`` 组装一段**脱敏后**的画像文本，供 Embedding 流水线向量化。
放在 ``retrieval/`` 下（而非 ``scripts/``）：重建脚本与查重合并的重索引**共用**同一份，
避免「脚本一套、服务一套」导致两边脱敏口径漂移（明文身份一旦进向量库就收不回）。

🔴 脱敏在**入库前**（数据库 §7 / B-8）：``phone`` / ``email`` 只取脱敏形态，
身份证**绝不**进向量库（连脱敏形态都不放 —— 证件号不是检索信号，出现即风险）。
"""

from __future__ import annotations

from typing import Any

from app.core.crypto import mask_encrypted_email, mask_encrypted_phone

#: 参与画像文本的字段（与检索侧可见字段一致）
_PROFILE_FIELDS: tuple[str, ...] = ("name", "school", "major", "education_level", "work_years")


def build_profile_text(talent: Any) -> str:
    """组装脱敏画像文本。

    :param talent: ``TalentBase`` 实例（只需 ``name``/``school``/``major``/
        ``education_level``/``work_years``/``phone_enc``/``email_enc`` 属性）
    """
    parts: list[str] = []
    for field in _PROFILE_FIELDS:
        value = getattr(talent, field, None)
        if value not in (None, ""):
            parts.append(f"{field}:{value}")
    phone = mask_encrypted_phone(getattr(talent, "phone_enc", None))
    if phone:
        parts.append(f"phone:{phone}")
    email = mask_encrypted_email(getattr(talent, "email_enc", None))
    if email:
        parts.append(f"email:{email}")
    return " ".join(parts)
