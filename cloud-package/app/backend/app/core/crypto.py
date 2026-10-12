"""敏感字段加密与脱敏（数据库 §0 原则 10 / 接口 §1.7）。

职责
----
1. **加密**：手机号 / 邮箱 / 身份证等 PII 以 **AES-256-GCM** 加密后存 ``VARBINARY``
   —— 数据库里**看不到明文**，运维 dump 出来的也是一堆密文；
2. **脱敏**：响应侧把明文转成 ``138****8888`` / ``a***@example.com``，
   让「能看到」与「能看到多少」分成两件事（接口 §1.7 的字段级裁剪）。

为什么用 GCM 而不是 CBC
-----------------------
GCM 是**认证加密**（AEAD）：密文被篡改时解密会抛 ``InvalidTag``，而 CBC 不会 ——
CBC 下篡改一段密文往往只会解出乱码，**静默**产生一条错误的手机号，
运维根本发现不了。PII 字段宁可直接失败也不要静默出错。

存储格式
--------
``nonce(12B) || ciphertext(含 16B tag)`` —— 一段自包含的二进制，直接塞进 ``VARBINARY(256)``。
nonce 每次加密**随机生成**（12 字节随机数在 2^32 次加密内碰撞概率可忽略），
❌ 绝不复用固定 nonce（GCM 下 nonce 复用会直接泄露异或明文与认证密钥）。

🔴 两条不可退让的底线
--------------------
1. **无密钥时拒绝加密，绝不静默降级为明文** —— 加密函数拿不到 ``AES_KEY`` 时抛错，
   而不是「存明文算了」。PII 一旦以明文落库，后续任何补丁都无法回收已泄露的数据。
2. **解密失败不阻断读接口** —— 密钥轮换 / 历史脏数据导致的解密失败只让**该字段显示为空**，
   不让整个用户列表 500。安全性靠「显示为空」保住，可用性不被单条脏数据拖垮。

⚠️ ``AES_KEY`` 为 **64 位 hex（32 字节）**，生成方式见 ``.env.example``：
``python -c "import secrets; print(secrets.token_hex(32))"``
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: GCM nonce 长度（字节）。12 是 GCM 的推荐值，与 96-bit IV 直接对应
NONCE_BYTES = 12
#: AES-256 密钥长度（字节）
KEY_BYTES = 32
#: 密钥的 hex 字符数（64 hex = 32 字节）
KEY_HEX_CHARS = KEY_BYTES * 2


class SensitiveFieldError(RuntimeError):
    """敏感字段加解密的前置条件不满足（缺密钥 / 密钥格式非法 / 密文损坏）。

    ⚠️ 故意**不继承** ``BizError`` —— 这是**部署配置问题**（运维事故），
    不是用户输入问题，不应被包装成 40001 让用户去改表单。
    """


@lru_cache(maxsize=1)
def _load_key() -> bytes:
    """读取并校验 ``settings.AES_KEY``（进程内缓存）。

    :raises SensitiveFieldError: 未配置 / 非 hex / 长度不等于 32 字节
    """
    raw = (settings.AES_KEY or "").strip()
    if not raw:
        raise SensitiveFieldError(
            "AES_KEY 未配置：敏感字段（手机号/邮箱）无法加密。"
            "请在 .env 中填入 64 位 hex 密钥（生成：python -c "
            '"import secrets; print(secrets.token_hex(32))"）'
        )
    if len(raw) != KEY_HEX_CHARS:
        raise SensitiveFieldError(
            f"AES_KEY 长度非法：应为 {KEY_HEX_CHARS} 个 hex 字符（{KEY_BYTES} 字节），"
            f"实际 {len(raw)} 个"
        )
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise SensitiveFieldError("AES_KEY 不是合法的十六进制字符串") from exc
    if len(key) != KEY_BYTES:
        raise SensitiveFieldError(f"AES_KEY 解码后应为 {KEY_BYTES} 字节，实际 {len(key)} 字节")
    return key


def reset_key_cache() -> None:
    """清空密钥缓存（**仅供测试**：用 ``monkeypatch`` 改 ``settings.AES_KEY`` 后调用）。"""
    _load_key.cache_clear()


def is_configured() -> bool:
    """密钥是否已正确配置（供 ``/health`` 之类的自检使用，不抛异常）。"""
    try:
        _load_key()
    except SensitiveFieldError:
        return False
    return True


# ============================================================================
# 加解密
# ============================================================================
def encrypt_sensitive(plain: str | None) -> bytes | None:
    """明文 → 密文（``nonce || ciphertext+tag``）。

    :param plain: 明文；``None`` 或空串返回 ``None``（表示「未填」，与「填了空」区分）
    :return: 可直接写入 ``VARBINARY`` 的字节串
    :raises SensitiveFieldError: ``AES_KEY`` 缺失或非法 —— **绝不静默存明文**
    """
    if plain is None:
        return None
    text = plain.strip()
    if not text:
        return None
    key = _load_key()  # 先取密钥：拿不到就抛错，不进入「要不要存明文」的分支
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, text.encode("utf-8"), None)


def decrypt_sensitive(blob: bytes | None) -> str | None:
    """密文 → 明文。

    解密失败（密钥轮换、历史脏数据、密文被截断）时**返回 ``None`` 并告警**，
    而不是抛异常 —— 单条脏数据不应让整个列表接口 500。
    """
    if blob is None:
        return None
    data = bytes(blob)
    if len(data) <= NONCE_BYTES:
        logger.warning("敏感字段密文长度异常，按空值处理 len=%s", len(data))
        return None
    try:
        key = _load_key()
    except SensitiveFieldError as exc:
        logger.error("敏感字段解密失败：%s", exc)
        return None
    nonce, payload = data[:NONCE_BYTES], data[NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, payload, None).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as exc:
        # 🔴 绝不把异常上抛：否则一条改过密钥的旧数据会让「用户列表」整体不可用
        logger.warning("敏感字段解密失败（按空值下发）err=%s", type(exc).__name__)
        return None


# ============================================================================
# 脱敏
# ============================================================================
#: 手机号保留「前 3 位 + 后 4 位」，中间固定 4 个星（接口 §1.7 示例：``138****8888``）
PHONE_MASK = "****"


def mask_phone(plain: str | None) -> str | None:
    """手机号脱敏：``13812348888`` → ``138****8888``。

    过短（< 7 位）或非预期格式时**整体打星**，绝不因为「格式奇怪」就把原值透出去。
    """
    if not plain:
        return None
    text = plain.strip()
    if len(text) < 7:
        return "*" * len(text) if text else None
    return f"{text[:3]}{PHONE_MASK}{text[-4:]}"


def mask_email(plain: str | None) -> str | None:
    """邮箱脱敏：``zhangsan@example.com`` → ``z***@example.com``。

    只保留首字符 + 域名（域名本身不敏感，且前端需要它来显示「哪家邮箱」）。
    """
    if not plain:
        return None
    text = plain.strip()
    if "@" not in text:
        # 不是邮箱格式：不猜测，整体打星（长度也一并隐藏）
        return "*" * min(len(text), 8)
    local, _, domain = text.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def mask_encrypted_phone(blob: bytes | None) -> str | None:
    """密文 → 脱敏手机号（列表接口的常用组合）。"""
    return mask_phone(decrypt_sensitive(blob))


def mask_id_card(plain: str | None) -> str | None:
    """身份证脱敏：``110101199003071234`` → ``110101********1234``。

    保留**前 6 位（行政区划）+ 后 4 位**：区划码是「核对是不是本人所在地」的最低需要，
    出生日期段与顺序码整体打星 —— 中间那 8 位恰好是最有识别价值的部分。

    ⚠️ 与 :func:`mask_phone` 的取舍**刻意相反**（手机号留前 3 后 4、这里留前 6 后 4）：
    身份证的前 6 位不含个人身份信息（是地区代码），多留不增加暴露面；
    手机号的前 3 位是运营商号段，留 3 位已足够。两处都按「最小必要」逐字段判定，
    ❌ 不套用同一个比例（那是看起来一致、实际多余的暴露）。

    过短或非预期格式时整体打星（同 :func:`mask_phone` 的 fail-safe）。
    """
    if not plain:
        return None
    text = plain.strip()
    if len(text) < 10:
        return "*" * len(text) if text else None
    return f"{text[:6]}{'*' * (len(text) - 10)}{text[-4:]}"


def mask_encrypted_id_card(blob: bytes | None) -> str | None:
    """密文 → 脱敏身份证（本人视角查阅接口的常用组合，CU-22 API-71）。"""
    return mask_id_card(decrypt_sensitive(blob))


def mask_encrypted_email(blob: bytes | None) -> str | None:
    """密文 → 脱敏邮箱（列表接口的常用组合）。"""
    return mask_email(decrypt_sensitive(blob))


# ============================================================================
# 不可逆比对哈希（TBL-11 的 phone_hash / id_card_hash）
# ============================================================================
ContactKind = Literal["phone", "email", "id_card"]

_PHONE_DIGITS = re.compile(r"\D")


class ContactHashSaltError(SensitiveFieldError):
    """``CONTACT_HASH_SALT`` 未配置（比对哈希的前置条件不满足）。"""


def normalize_contact(plain: str, kind: ContactKind) -> str:
    """把联系方式归一到「同一实体只有一个形式」，否则哈希比对会假性不相等。

    - ``phone``：去掉空格 / 横线 / 括号等非数字字符，并剥离 ``+86`` / ``86`` 国际前缀
      —— ``+86 138-1234-8888`` 与 ``13812348888`` 必须是同一个哈希；
    - ``email``：去空白 + **转小写**（``Zhang@X.com`` 与 ``zhang@x.com`` 是同一个邮箱）；
    - ``id_card``：去空白 + 转大写（``x`` 结尾的校验位大小写混用常见）。
    """
    text = (plain or "").strip()
    if kind == "phone":
        digits = _PHONE_DIGITS.sub("", text)
        if len(digits) > 11 and digits.startswith("86"):
            digits = digits[2:]
        return digits
    if kind == "email":
        return text.lower()
    return text.upper()


def contact_hash_salt_configured() -> bool:
    """盐是否已配置（供自检 / 金标准集脚本判断能否比对联系方式）。"""
    return bool((settings.CONTACT_HASH_SALT or "").strip())


def hash_contact(plain: str | None, kind: ContactKind) -> str | None:
    """联系方式 → ``char(64)`` 的**不可逆比对哈希**（小写 hex）。

    为什么**必须加盐**（而不是裸 SHA256）
    -----------------------------------
    手机号只有 10^11 个可能值、身份证号的校验位更少 —— 裸 SHA256 在 GPU 上
    **秒级就能穷举**，等于「加密存了，但等于没存」。加一个进程外的盐后，
    彩虹表与批量穷举直接失效，而「精确去重」这个用途完全不受影响
    （同一盐、同一归一化 → 同一哈希，正是判重需要的性质）。

    🔴 三条约束
    -----------
    1. **盐必须稳定**：它一旦变更，全部历史哈希失效 —— 属密钥级资产，
       ❌ 不得随版本发布随意更换（换盐等价于换密钥，需配套数据重刷方案）。
    2. **盐必须与 ``AES_KEY`` 不同**：一个用于不可逆比对、一个用于可逆解密，
       混用会让「泄露一个」等于「弱化另一个」。
    3. **未配置盐时报错，不静默退化为不加盐**（对齐 ``encrypt_sensitive`` 的 fail-loud）：
       静默降级会悄悄产出一批弱哈希，且没人会发现。

    :returns: 64 位小写 hex；``None`` / 空串 / 归一化后为空 → ``None``（表示「未填」）
    :raises ContactHashSaltError: ``CONTACT_HASH_SALT`` 未配置
    """
    if plain is None:
        return None
    normalized = normalize_contact(plain, kind)
    if not normalized:
        return None
    salt = (settings.CONTACT_HASH_SALT or "").strip()
    if not salt:
        raise ContactHashSaltError(
            "CONTACT_HASH_SALT 未配置：无法生成联系方式比对哈希（phone_hash / id_card_hash）。"
            "请在 .env 中填入一个随机盐（生成：python -c "
            '"import secrets; print(secrets.token_hex(16))"）；'
            "⚠️ 该盐一经使用即不可更换，否则已入库的哈希全部失效。"
        )
    return hashlib.sha256(f"{salt}:{kind}:{normalized}".encode()).hexdigest()


__all__ = [
    "KEY_BYTES",
    "NONCE_BYTES",
    "ContactHashSaltError",
    "ContactKind",
    "SensitiveFieldError",
    "contact_hash_salt_configured",
    "decrypt_sensitive",
    "encrypt_sensitive",
    "hash_contact",
    "is_configured",
    "mask_email",
    "mask_encrypted_email",
    "mask_encrypted_id_card",
    "mask_encrypted_phone",
    "mask_id_card",
    "mask_phone",
    "normalize_contact",
    "reset_key_cache",
]
