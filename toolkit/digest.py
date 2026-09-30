"""对象摘要：RFC 8785 JCS 规范化 + SHA-256。

proposal 7.3 要求"固定、版本化的规范化规则"；本模块是该规则的唯一实现，
PRD/G5 决议选定 JCS + SHA-256。确认记录引用 CANONICALIZATION_ID 以便未来
规则升级时可区分摘要口径。
"""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785

CANONICALIZATION_ID = "jcs@1"


def canonicalize(obj: Any) -> bytes:
    """按 RFC 8785 规范化对象并返回 UTF-8 字节。NaN/Infinity 抛 ValueError。"""
    try:
        return rfc8785.dumps(obj)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"对象不可规范化（JCS）: {exc}") from exc


def digest(obj: Any) -> str:
    """对象内容摘要：JCS 规范化字节流的 SHA-256 十六进制（小写）。"""
    return hashlib.sha256(canonicalize(obj)).hexdigest()


def digest_bytes(data: bytes) -> str:
    """原始字节摘要（数据快照指纹用）。"""
    return hashlib.sha256(data).hexdigest()
