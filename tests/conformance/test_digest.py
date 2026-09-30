"""摘要规范化契约测试（RFC 8785 JCS + SHA-256）。

期望哈希字面量由 shasum -a 256 独立计算，非实现自算（防同义反复）。
"""

import math

import pytest

from toolkit.digest import CANONICALIZATION_ID, canonicalize, digest


class TestCanonicalize:
    def test_keys_sorted_and_no_whitespace(self) -> None:
        assert canonicalize({"b": 1, "a": 2}) == b'{"a":2,"b":1}'

    def test_es6_number_normalization(self) -> None:
        # JCS 按 ECMAScript 数字序列化：1.0 → 1，0.5 保持
        assert canonicalize({"x": 1.0, "y": 0.5}) == b'{"x":1,"y":0.5}'

    def test_non_ascii_output_as_utf8(self) -> None:
        # JCS 不转义非 ASCII，输出 UTF-8 字节
        assert canonicalize({"é": 1}) == '{"é":1}'.encode()

    def test_nested_objects_canonicalized(self) -> None:
        assert canonicalize({"z": {"b": [1, 2], "a": {}}}) == b'{"z":{"a":{},"b":[1,2]}}'

    def test_rejects_nan_and_infinity(self) -> None:
        with pytest.raises(ValueError):
            canonicalize({"x": math.nan})
        with pytest.raises(ValueError):
            canonicalize({"x": math.inf})


class TestDigest:
    def test_digest_matches_independent_shell_hash(self) -> None:
        # 期望值来自：printf '{"a":2,"b":1}' | shasum -a 256
        expected = "d3626ac30a87e6f7a6428233b3c68299976865fa5508e4267c5415c76af7a772"
        assert digest({"b": 1, "a": 2}) == expected

    def test_value_equal_objects_share_digest(self) -> None:
        # 1 与 1.0 逻辑等价（JCS 序列化相同），键序无关
        assert digest({"a": 1, "b": 2}) == digest({"b": 2.0, "a": 1.0})

    def test_different_values_differ(self) -> None:
        assert digest({"a": 1}) != digest({"a": 2})

    def test_canonicalization_id_is_versioned(self) -> None:
        assert CANONICALIZATION_ID == "jcs@1"
