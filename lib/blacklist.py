# 用途：黑名单的纯逻辑（时长→天数、过期判断）—— server 与 analyze_profile 共用
# 状态：现行（2026-10-20 抽出；规则：addedAt 只到日，含到期当天仍生效，forever 永久）

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

DURATION_DAYS = {"1d": 1, "3d": 3, "7d": 7, "30d": 30, "forever": None}
PERMANENT = ("forever", "perm", "永久")


def parse_duration(text: str | None) -> int | None:
    """'1d'/'3d'/'7d'/'30d' → 天数；永久 → None；认不出 → -1。"""
    t = (text or "").strip().lower()
    if t in PERMANENT:
        return None
    if t in DURATION_DAYS:
        return DURATION_DAYS[t]
    if t.endswith("d") and t[:-1].isdigit():
        return int(t[:-1])
    return -1


def added_date(entry: dict) -> date | None:
    raw = entry.get("addedAt")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except (TypeError, ValueError):
        return None


def expiry_of(entry: dict) -> date | None:
    """到期日（含当天）；永久返回 None；解析不了返回 False。"""
    days = parse_duration(entry.get("duration"))
    if days == -1:
        return False
    if days is None:
        return None
    added = added_date(entry)
    if added is None:
        return False
    return added + timedelta(days=days)


def is_active(entry: dict, today: date | None = None) -> bool:
    """黑名单条目当前是否生效。"""
    today = today or datetime.now().date()
    expires = expiry_of(entry)
    if expires is False:
        return False
    if expires is None:
        return True
    return today <= expires


def load_entries(path: Path) -> list[dict]:
    """读黑名单文件里的 entries（读不到就是空表）。"""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    entries = doc.get("entries")
    return entries if isinstance(entries, list) else []
