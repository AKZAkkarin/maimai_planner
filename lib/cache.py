# 用途：分析/统计脚本的「输入没变就跳过」判断（输出比所有输入都新、且参数一致）
# 状态：现行（2026-10-20）

from __future__ import annotations

import json
from pathlib import Path


def mtime_ns(path) -> int:
    try:
        return Path(path).stat().st_mtime_ns
    except OSError:
        return 0


def is_fresh(out_path, inputs, expected=None, key_field: str = "params") -> bool:
    """输出存在、比所有输入都新、且（给了 expected 时）输出里的 params 与 expected 一致 → 可以跳过。"""
    out = Path(out_path)
    if not out.is_file() or out.stat().st_size == 0:
        return False
    out_m = mtime_ns(out)
    for src in inputs:
        if src and mtime_ns(src) > out_m:
            return False
    if expected is not None:
        try:
            doc = json.loads(out.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
        if doc.get(key_field) != expected:
            return False
    return True
