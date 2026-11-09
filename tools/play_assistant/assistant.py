#!/usr/bin/env python3
# 用途：打歌助手的纯逻辑（牌桌持久化、加权抽歌、日志清理、全局设置）
# 状态：现行（v1 骨架）
"""打歌助手纯逻辑，不直接读曲库/成绩；候选由 server 构造后传进来。"""

from __future__ import annotations

import json
import random
import statistics
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
USER_DIR = PROJECT_DIR / "dataUser"
GLOBAL_SETTINGS = USER_DIR / "assistant-settings.json"
DEFAULT_SETTINGS = {"host": "", "modPort": 8790, "callbackPort": 20010}
DEFAULT_POOLS = ("mastered", "pushable_b35", "pushable_b15", "ap", "practice",
                 "accuracy", "challenge", "unplayed", "favorite")


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0800")


def user_dir(user_id: str) -> Path:
    return USER_DIR / str(user_id)


def state_path(user_id: str) -> Path:
    return user_dir(user_id) / "assistant.json"


def load_state(user_id: str) -> dict:
    path = state_path(user_id)
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        doc = {}
    return normalize_state(doc)


def save_state(user_id: str, state: dict) -> None:
    path = state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(normalize_state(state), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def normalize_state(doc: dict) -> dict:
    cards = doc.get("cards")
    if not isinstance(cards, list):
        cards = []
    cards = [c for c in cards if isinstance(c, dict) and c.get("pool") in DEFAULT_POOLS + ("underdog",)][:12]
    preview = [p for p in (doc.get("preview") or []) if isinstance(p, dict)][:3]
    recent = [p for p in (doc.get("recent") or []) if isinstance(p, dict)][:6]
    return {
        "schema": 1,
        "cards": cards,
        "preview": preview,
        "recent": recent,
        "nextCard": int(doc.get("nextCard") or 0),
        "updatedAt": doc.get("updatedAt") or now_stamp(),
    }


def load_settings() -> dict:
    try:
        doc = json.loads(GLOBAL_SETTINGS.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        doc = {}
    return {**DEFAULT_SETTINGS, **{k: doc[k] for k in DEFAULT_SETTINGS if k in doc}}


def save_settings(doc: dict) -> dict:
    out = {**DEFAULT_SETTINGS, **{k: doc.get(k, DEFAULT_SETTINGS[k]) for k in DEFAULT_SETTINGS}}
    USER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = GLOBAL_SETTINGS.with_suffix(".json.part")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(GLOBAL_SETTINGS)
    return out


def central_median(values: list[float]) -> float:
    """排序后 45%-55% 区间的中位数；样本太小时直接用全部。"""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return 0.0
    if len(vals) < 8:
        m = len(vals) // 2
        return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2
    lo = int(len(vals) * 0.45)
    hi = max(lo + 1, int(len(vals) * 0.55))
    return statistics.median(vals[lo:hi])


def _candidate_key(c: dict) -> str:
    return f"{c.get('internalId')}:{c.get('chartid')}"


def _priority_weights(candidates: list[dict], card: dict) -> list[float]:
    """返回与 candidates 等长的权重；高优先项 ×3，两个都满足按 6。"""
    settings = card.get("settings") or {}
    if card.get("pool") == "underdog":
        mode = settings.get("fitPriority") or "none"
        if mode not in ("high", "low"):
            return [1.0] * len(candidates)
        levels = []
        for c in candidates:
            try:
                levels.append((c, float(c.get("level"))))
            except (TypeError, ValueError):
                continue
        if not levels:
            return [1.0] * len(candidates)
        try:
            lo = float(settings.get("levelMin"))
            hi = float(settings.get("levelMax"))
            mid = (lo + hi) / 2.0
        except (TypeError, ValueError):
            # 只填了一端时，用候选官方定数的中位数兜底
            mid = central_median([lv for _, lv in levels])
        out = []
        for c in candidates:
            try:
                level = float(c.get("level"))
            except (TypeError, ValueError):
                out.append(1.0)
                continue
            hit = level <= mid if mode == "low" else level > mid
            out.append(3.0 if hit else 1.0)
        return out
    fit_mode = settings.get("fitPriority") or "none"
    ach_mode = settings.get("achPriority") or "none"
    fit_mid = central_median([c.get("fit") for c in candidates if c.get("fit") is not None]) \
        if fit_mode != "none" else None
    ach_mid = central_median([c.get("ach") for c in candidates if c.get("ach") is not None]) \
        if ach_mode != "none" else None
    out = []
    for c in candidates:
        fit_hit = False
        ach_hit = False
        fit = c.get("fit")
        ach = c.get("ach")
        if fit_mode == "high" and fit is not None and fit_mid is not None:
            fit_hit = fit > fit_mid
        elif fit_mode == "low" and fit is not None and fit_mid is not None:
            fit_hit = fit < fit_mid
        if ach_mode == "high" and ach is not None and ach_mid is not None:
            ach_hit = ach > ach_mid
        elif ach_mode == "low" and ach is not None and ach_mid is not None:
            ach_hit = ach < ach_mid
        w = 1
        if fit_hit:
            w = 3
        if ach_hit:
            w = 6 if w == 3 else 3
        out.append(float(w))
    return out


def draw_candidate(candidates: list[dict], card: dict, recent: list[dict],
                   rng: random.Random | None = None) -> dict | None:
    """从候选里抽一首；最近 6 首优先排除，池内全被排除时允许重复。"""
    if not candidates:
        return None
    rng = rng or random
    recent_keys = {_candidate_key(x) for x in recent if isinstance(x, dict)}
    pool = [c for c in candidates if _candidate_key(c) not in recent_keys]
    if not pool:
        pool = candidates
    weights = _priority_weights(pool, card)
    if all(w <= 0 for w in weights):
        return rng.choice(pool)
    return rng.choices(pool, weights=weights, k=1)[0]


def touch_recent(state: dict, picked: dict) -> None:
    recent = [r for r in state.get("recent") or [] if _candidate_key(r) != _candidate_key(picked)]
    recent.append({"internalId": picked.get("internalId"), "chartid": picked.get("chartid")})
    state["recent"] = recent[-6:]


def log_dir(user_id: str) -> Path:
    return user_dir(user_id) / "assistant-logs"


def list_logs(user_id: str) -> list[dict]:
    root = log_dir(user_id)
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.log"), reverse=True):
        try:
            out.append({"name": p.name, "size": p.stat().st_size,
                        "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
        except OSError:
            pass
    return out


def prune_logs(user_id: str, keep: int = 3) -> None:
    root = log_dir(user_id)
    if not root.is_dir():
        return
    files = sorted(root.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        try:
            p.unlink()
        except OSError:
            pass
