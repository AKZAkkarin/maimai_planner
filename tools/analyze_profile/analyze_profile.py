#!/usr/bin/env python3
# 用途：按用户成绩 + 曲库社区标签，给每张谱打标签（熟练/推b35/推b15/AP推分/弱点练习/准度练习/有挑战/待开荒），写进用户目录
# 状态：现行（第一版，2026-10-20）
"""打歌助手的画像引擎：把「你打得怎么样」变成「这张谱对你是什么」。

【模型】设计见 docs/analysis-design.md（2026-10-20 讨论定稿）。
  1. 能力曲线：每个难度层把打过的谱按定数 0.5 一箱，取达成率中位数 → expected(定数)
  2. 标签修正：残差 = 实际 − expected；每个标签取残差中位数（按样本量收缩），
     再用曲线斜率折算成「定数单位」→ 有效定数 = 定数 + Σ 标签修正
  3. 分类（多标签）：熟练 / 推b35 / 推b15 / AP推分 / 弱点练习 / 准度练习 / 有挑战 / 待开荒

【读什么】
  dataUser/<uuid>/scores.json        这个用户的成绩
  dataUser/<uuid>/blacklist.json     黑名单（可选；没有就当空）
  data/fusion/fusion-<族>.json       曲库（定数 / 标签 / 物量）

【写什么】dataUser/<uuid>/analysis.json（纯派生数据，可随时重算）

【用法】
    python tools/analyze_profile/analyze_profile.py --user <uuid>
    可选：--scores-root PATH --fusion-dir PATH -o PATH
    退出码：0 成功；2 读不到成绩/曲库，或写盘失败。不联网。
"""

from __future__ import annotations

import argparse
import json
import os
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"

sys.dont_write_bytecode = True

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    import env
except Exception:                                          # noqa: BLE001
    env = None

from lib.blacklist import is_active as blacklist_active, load_entries
from lib.cache import is_fresh
from lib.versions import fix_version, new_song_versions
from lib.rating import COEF_MAX, coefficient, next_threshold   # noqa: E402  单曲 rating 唯一实现
from lib.rating import rating as song_rating                   # noqa: E402


def rating_coef(ach: float) -> float:
    """达成率 → 系数（只有系数，不要评级）。"""
    return coefficient(ach)[0]

SCRIPT_DIR_NAME = "analyze_profile"
FUSION_SCRIPT_DIR = "fusion"
SCORES_NAME = "scores.json"
BLACKLIST_NAME = "blacklist.json"
ANALYSIS_NAME = "analysis.json"
FAMILIES = ("std", "dx", "utage", "utage2p")

DIFF_ORDER = ("basic", "advanced", "expert", "master", "remaster")
DIFF_NAMES = {"basic": "BAS", "advanced": "ADV", "expert": "EXP",
              "master": "MAS", "remaster": "REM", "【協】": "UTG"}
TAG_GROUPS = ("PatternsTag", "DifficultyTag", "EvaluationTag")
TZ_CN = timezone(timedelta(hours=8))

# 默认阈值（都可被命令行覆盖；设计文档 §七）
PARAMS = {
    "fit_score_base": 0.2,        # 成绩修正基准值
    "fit_score_low": 0.06,        # 局数 <10 时的系数（0.2 × 0.3）
    "fit_cap": 0.3,               # 单谱修正上限
    "level_bin": 0.5,             # 能力曲线分箱宽度（定数）
    "bin_min_samples": 3,         # 每箱最少样本，少于它不采信
    "mastered_ach": 100.6,        # 熟练掌握：达成率下限（v2 用户定 100.6）
    "tag_count_mode": "linear",   # 标签数收缩：none / sqrt / linear
    "tag_count_alpha": 0.15,      # linear 模式：1 / (1 + α(k−1))
    "tag_balance_mode": "spread", # 均衡收缩：none / spread
    "tag_balance_ref": 0.25,      # spread 的参考值（标签 γ 极差）
    "tag_balance_power": 2.0,     # 均衡收缩曲线幂
    "tag_balance_floor": 0.15,    # 均衡收缩最低保留比例
}

# maimai rating 系数（达成率下限 → 系数）；推分 = 定数 × (下一档系数 − 当前档系数)
RATING_TABLE = (
    (100.5, 1.00), (100.0, 0.98), (99.5, 0.97), (99.0, 0.96), (98.0, 0.95),
    (97.0, 0.94), (96.0, 0.93), (95.0, 0.92), (94.0, 0.91), (93.0, 0.90),
    (92.0, 0.89), (91.0, 0.88), (90.0, 0.87), (88.0, 0.86), (86.0, 0.85),
    (84.0, 0.84), (82.0, 0.83), (80.0, 0.82), (78.0, 0.81), (76.0, 0.80),
    (74.0, 0.79), (72.0, 0.78), (70.0, 0.77), (68.0, 0.76), (66.0, 0.75),
    (64.0, 0.74), (62.0, 0.73), (60.0, 0.72), (58.0, 0.71), (56.0, 0.70),
    (54.0, 0.69), (52.0, 0.68), (50.0, 0.67), (0.0, 0.00),
)

BAR = "=" * 72
RULE = "─" * 72


# ---------------------------------------------------------------- 基础设施

def now_stamp() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")


def user_data_root() -> Path:
    if env is not None and callable(getattr(env, "user_data_dir", None)):
        try:
            return Path(env.user_data_dir())
        except Exception:                                  # noqa: BLE001
            pass
    return PROJECT_DIR / "dataUser"


def data_root(script_name: str) -> Path:
    if env is not None and callable(getattr(env, "data_dir_for", None)):
        return Path(env.data_dir_for(script_name))
    return PROJECT_DIR / "data" / script_name


def read_json(path: Path, what: str):
    if not path.is_file():
        raise ValueError(f"没有{what}：{path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict) -> None:
    # 并发分析时不能用固定 tmp 名：每个进程写自己的临时文件。
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        # Windows 上目标可能正被服务端读取，replace 会短暂报 WinError 5/32。
        # 用独立临时文件 + 短期重试覆盖这个窗口，避免把分析结果写成失败。
        last_error = None
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def quantile(values: list[float], q: float) -> float | None:
    """线性插值分位数（q ∈ [0,1]）。"""
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(data) - 1)
    frac = pos - lo
    return data[lo] * (1 - frac) + data[hi] * frac




# ---------------------------------------------------------------- 数据装配

def load_charts(fusion_dir: Path) -> tuple[list[dict], dict]:
    """曲库 → 每张谱一条扁平记录；顺带统计族/难度。"""
    charts, total = [], 0
    for family in FAMILIES:
        path = fusion_dir / f"fusion-{family}.json"
        if not path.is_file():
            raise ValueError(f"没有曲库（{family}）：{path}")
        for song in read_json(path, f"曲库（{family}）").get("songs") or []:
            iid = song.get("internalId")
            if iid is None:
                continue
            for sheet in song.get("sheets") or []:
                total += 1
                tags = []
                for group in TAG_GROUPS:
                    for tag in sheet.get(group) or []:
                        zh = (tag or {}).get("zh") if isinstance(tag, dict) else None
                        if zh:
                            tags.append({"name": zh, "group": group})
                diff_name = sheet.get("difficulty")
                tier = "utage" if family in ("utage", "utage2p") else diff_name
                charts.append({
                    "internalId": int(iid),
                    "chartid": sheet.get("chartid"),
                    "difficulty": diff_name,
                    "tier": tier,
                    "level": sheet.get("dxratingLevelValue") or sheet.get("localLevelValue"),
                    "localLevel": sheet.get("localLevelValue") or sheet.get("dxratingLevelValue"),
                    "version": fix_version(sheet.get("version")),
                    "family": family,
                    "title": song.get("title"),
                    "tags": tags,
                })
    return charts, {"sheets": total}


def load_scores(user_dir: Path) -> dict:
    doc = read_json(user_dir / SCORES_NAME, "这个用户的成绩库")
    out = {}
    for row in doc.get("scores") or []:
        key = (row.get("internalId"), row.get("chartid"))
        if key[0] is None or key[1] is None:
            continue
        out[(int(key[0]), key[1])] = {
            "achievement": (row.get("achievement") or 0) / 10000.0,
            "combo": row.get("comboStatus") or 0,
            "sync": row.get("syncStatus") or 0,
            "playCount": row.get("playCount") or 0,
            "rank": row.get("RANK"),
        }
    return out



def load_blacklist(user_dir: Path) -> set[tuple[int, int]]:
    """生效中的黑名单（过期的不算）。规则见 lib/blacklist.py。"""
    active = set()
    for entry in load_entries(user_dir / BLACKLIST_NAME):
        iid, cid = entry.get("internalId"), entry.get("chartid")
        if iid is None or cid is None or not blacklist_active(entry):
            continue
        active.add((int(iid), int(cid)))
    return active


# ---------------------------------------------------------------- 能力曲线

def build_curves(charts: list[dict], scores: dict, params: dict) -> tuple[dict, float]:
    """每个难度层：{bin 起点: (expected, n)}；返回（曲线, 全局斜率）。"""
    bin_w = params["level_bin"]
    samples: dict[str, dict[float, list[float]]] = {}
    for chart in charts:
        level = chart.get("level")
        score = scores.get((chart["internalId"], chart["chartid"]))
        if level is None or not score or score["achievement"] <= 0:
            continue
        # v2：难度层不分开 —— 定数（level）才是统一衡量标准，BAS/ADV/EXP 只是定数的不同段
        key = "*"
        start = round(level / bin_w) * bin_w
        samples.setdefault(key, {}).setdefault(round(start, 3), []).append(score["achievement"])

    curves = {}
    slopes = []
    for diff, bins in samples.items():
        pts = []
        for start, values in sorted(bins.items()):
            if len(values) >= params["bin_min_samples"]:
                pts.append({"level": start + bin_w / 2, "expected": round(statistics.median(values), 4),
                            "n": len(values)})
        if not pts:                                        # 样本太少：退化成整体中位数一个点
            allv = [v for values in bins.values() for v in values]
            pts = [{"level": round(statistics.median([c["level"] for c in charts
                                                      if c.get("tier") == diff and c.get("level") is not None] or [0]), 3),
                    "expected": round(statistics.median(allv), 4), "n": len(allv)}]
        # 强制单调：定数越高，期望达成率不应上升（分箱噪声会造成小反弹）
        prev = None
        for pt in pts:
            if prev is not None and pt["expected"] > prev:
                pt["expected"] = prev
            prev = pt["expected"]
        curves[diff] = pts
        # 该难度层的“达成率 / 定数”回归斜率（去掉层内常数项，只看层内下降速度）
        if len(pts) >= 3:
            xs = [p["level"] for p in pts]
            ys = [p["expected"] for p in pts]
            mx = statistics.fmean(xs)
            my = statistics.fmean(ys)
            den = sum((x - mx) ** 2 for x in xs)
            if den > 0:
                slopes.append(abs(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den))
    # 全局斜率 = 各层回归斜率的中位数（用中位数避免 REM 那种陡段带偏）
    slope = statistics.median(slopes) if slopes else 1.0
    return curves, max(slope, 0.3)


def inverse_level(curves: dict, achievement: float) -> float | None:
    """反解能力曲线：找到一个定数 L，使个人曲线在该定数的期望达成率 = achievement。

    比"残差 / 局部斜率"稳：曲线非线性、局部很平时也不会放大误差。找不到（超出曲线范围）返回 None。
    """
    pts = curves.get("*") or []
    if len(pts) < 2:
        return None
    ordered = sorted(pts, key=lambda p: p["level"])
    for a, b in zip(ordered, ordered[1:]):
        hi_a, hi_b = a["expected"], b["expected"]
        if hi_a == hi_b:
            continue
        lo, hi = min(hi_a, hi_b), max(hi_a, hi_b)
        if not (lo <= achievement <= hi):
            continue
        frac = (achievement - hi_a) / (hi_b - hi_a)     # 允许方向相反（越高定数期望越低）
        return a["level"] + frac * (b["level"] - a["level"])
    return None


def expected_at(curves: dict, diff: str, level: float) -> float | None:
    """在曲线上取期望（点之间线性插值，两端外推用最近点）。v2：只有一条曲线，diff 参数保留兼容。"""
    pts = curves.get("*") or curves.get(diff) or []
    if not pts:
        return None
    if level <= pts[0]["level"]:
        return pts[0]["expected"]
    if level >= pts[-1]["level"]:
        return pts[-1]["expected"]
    for a, b in zip(pts, pts[1:]):
        if a["level"] <= level <= b["level"]:
            if b["level"] == a["level"]:
                return b["expected"]
            t = (level - a["level"]) / (b["level"] - a["level"])
            return a["expected"] + (b["expected"] - a["expected"]) * t
    return pts[-1]["expected"]


def slope_near(curves: dict, level: float) -> float:
    """曲线在 level 附近的局部斜率（达成率每 1 定数下降多少，正数）。v2：标签换算用局部斜率。"""
    pts = curves.get("*") or []
    if len(pts) < 2:
        return 1.0
    lo, hi = pts[0], pts[-1]
    for a, b in zip(pts, pts[1:]):
        if a["level"] <= level <= b["level"]:
            lo, hi = a, b
            break
    drop = hi["expected"] - lo["expected"]
    span = hi["level"] - lo["level"]
    if span <= 0 or drop >= 0:
        return 1.0
    return max(0.4, min(12.0, abs(drop) / span))


# ---------------------------------------------------------------- 标签修正

PATTERN_GROUP = "PatternsTag"
EVAL_GROUP = "EvaluationTag"
DIFFICULTY_GROUP = "DifficultyTag"
FIT_NAME = "fit_diff.json"


def load_fit_diff() -> dict:
    """水鱼拟合定数：{"<iid>:<cid>": fitDiff}；读不到就是空表（全部走纯标签权重）。"""
    path = data_root("fit_diff") / FIT_NAME
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out = {}
    for key, item in (doc.get("charts") or {}).items():
        value = (item or {}).get("fitDiff")
        if value is not None:
            out[key] = float(value)
    return out


def load_fit_meta() -> dict:
    """水鱼拟合的完整记录：{"<iid>:<cid>": {fitDiff, avg, stdDev, cnt, diff}}。"""
    path = data_root("fit_diff") / FIT_NAME
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in (doc.get("charts") or {}).items() if isinstance(v, dict)}


def unreliable_fits(meta: dict, sigma: float = 1.5) -> set[str]:
    """同一档定数里 avg 明显离群的谱（外挂刷分 / 早期只有顶尖玩家打）：这些谱的 Δ 不可信。

    做法：按官标定数把谱分组，算该组 avg 的中位数与标准差，偏离超过 sigma 倍标准差的记为不可信。
    """
    by_level: dict[float, list[tuple[str, float]]] = {}
    for key, item in meta.items():
        avg, diff = item.get("avg"), item.get("diff")
        if avg is None or not diff:
            continue
        try:
            level = float(str(diff).rstrip("+?"))
        except ValueError:
            continue
        by_level.setdefault(level, []).append((key, float(avg)))
    bad: set[str] = set()
    for rows in by_level.values():
        if len(rows) < 3:
            continue
        avgs = [avg for _, avg in rows]
        med = statistics.median(avgs)
        sd = statistics.pstdev(avgs)
        if sd <= 0:
            continue
        for key, avg in rows:
            # 既要比同档离散度大（1.5σ），也要有实质差距（至少 3 个百分点），防止小样本误伤
            if abs(avg - med) > sigma * sd and abs(avg - med) >= 3.0:
                bad.add(key)
    return bad


def _solve_ols(rows: list[list[float]], ys: list[float]) -> list[float]:
    """最小二乘（正规方程 + 高斯消元，特征只有二十来个，不用 numpy）。"""
    n, p = len(rows), len(rows[0])
    ata = [[sum(rows[k][i] * rows[k][j] for k in range(n)) for j in range(p)] for i in range(p)]
    aty = [sum(rows[k][i] * ys[k] for k in range(n)) for i in range(p)]
    for i in range(p):
        ata[i][i] += 1e-6                                 # 轻微 ridge，防共线
    for col in range(p):                                  # 高斯消元
        pivot = max(range(col, p), key=lambda r: abs(ata[r][col]))
        if abs(ata[pivot][col]) < 1e-12:
            continue
        ata[col], ata[pivot] = ata[pivot], ata[col]
        aty[col], aty[pivot] = aty[pivot], aty[col]
        for r in range(col + 1, p):
            factor = ata[r][col] / ata[col][col]
            if factor == 0:
                continue
            for c in range(col, p):
                ata[r][c] -= factor * ata[col][c]
            aty[r] -= factor * aty[col]
    out = [0.0] * p
    for i in range(p - 1, -1, -1):
        if abs(ata[i][i]) < 1e-12:
            continue
        acc = aty[i] - sum(ata[i][j] * out[j] for j in range(i + 1, p))
        out[i] = acc / ata[i][i]
    return out


def classify(chart: dict, score: dict | None, effective: float,
             tag_stats: dict, params: dict, ability: tuple = (0.0, 99.0),
             eval_level: float | None = None, local_level: float | None = None,
             pool_cutoff: float | None = None, pool: str | None = None,
             in_conf: bool = False, in_b50: bool = False, conf_range: tuple = (0.0, 99.0),
             fit_median: float = 0.0, weak_tags: set | None = None,
             weak_pat: set | None = None, weak_eval: set | None = None) -> list[str]:
    """多标签判定（见设计文档 §四）；ability 是 v3 能力区间。"""
    labels = []
    ab_lo, ab_hi = ability
    conf_hi = conf_range[1]
    ability_mid = (ab_lo + ab_hi) / 2
    if score and score["achievement"] > 0:
        ach, combo = score["achievement"], score["combo"]
        # 熟练（v3 口径）：个人拟合在能力区间内 且 达成率 ≥ 100.6
        is_mastered = ach >= params["mastered_ach"] and ab_lo <= effective <= ab_hi
        # 推b35/推b15：① 个人拟合 < 置信上限 − 0.15；② 成绩 < 100.5（还有 rating 收益）；
        # ③ 已在对应 b50 池，或打到 100.0/100.5 的 rating 能进对应池
        local = local_level if local_level is not None else (chart.get("localLevel") or chart.get("level"))
        push_label = None
        if effective < conf_hi - 0.15 and local and ach < 100.5:
            reach = max(song_rating(local, 100.0), song_rating(local, 100.5))
            if in_b50 or reach > (pool_cutoff or 0):
                push_label = "pushable_b15" if pool == "new" else "pushable_b35"
        if is_mastered:
            labels.append("mastered")
        # 有挑战：个人拟合在 [能力区间上限, 置信区间上限]
        if ab_hi < effective <= conf_hi:
            labels.append("challenge")
        if push_label:
            labels.append(push_label)
        # AP推分：能力范围内、成绩 > 100.85、还没 AP，推到 AP 能提高 DX Rating（不一定是 +1，能加就行）
        combo_status = score.get("comboStatus") if score.get("comboStatus") is not None else combo
        if in_conf and ach > 100.85 and local and (combo_status or 0) < 3:
            ap_rating = song_rating(local, ach, True)
            if in_b50 or ap_rating > (pool_cutoff or 0):
                labels.append("ap")
        # 弱点练习：能力范围内 + ≥2 个弱项标签 + 成绩 < 100.6
        # 带 ≥2 个弱项配置标签，或 ≥1 个弱项评价标签
        weak_pat_hits = sum(1 for t in chart["tags"] if t["name"] in weak_pat)
        weak_eval_hits = sum(1 for t in chart["tags"] if t["name"] in weak_eval)
        if (in_conf and ach < 100.6 and ability_mid < effective < ab_hi
                and (weak_pat_hits >= 2 or weak_eval_hits >= 1)):
            labels.append("practice")
        # 准度练习：能力区间内中点以下，成绩 >100.7 且未 AP/AP+
        if (ab_lo <= effective <= ability_mid and ach > 100.7
                and (combo_status or 0) < 3):
            labels.append("accuracy")
        return labels
    # 没打过：高于能力区间上沿（仍在置信区间内）= 有挑战；
    #          能力区间中点以上、能力上界以下 = 待开荒；
    #          按 100.0/100.5 打上去能进对应 b50 的，也算推分（补池候选）
    if ab_hi < effective <= conf_hi:
        labels.append("challenge")
    elif ability_mid <= effective <= ab_hi:
        labels.append("unplayed")
    local = local_level if local_level is not None else (chart.get("localLevel") or chart.get("level"))
    if effective < conf_hi - 0.15 and local:
        reach = max(song_rating(local, 100.0), song_rating(local, 100.5))
        if reach > (pool_cutoff or 0):
            labels.append("pushable_b15" if pool == "new" else "pushable_b35")
    return labels


# ---------------------------------------------------------------- 主流程

WATER_TAGS = ("水", "诈称谱")
DELTA_MIN = -0.2      # 水鱼 Δ 的下限：比官标简单超过 0.2 的，只认 0.2
# 吃体力的标签：在高位（超出能力区间上沿）权重放大，在低位权重缩小
STAMINA_EVAL = {"体力谱", "键盘谱", "高物量"}
STAMINA_PAT = {"爆发", "交互", "大位移", "转圈", "散打", "纵连"}


def _score_weight(ach: float) -> float:
    """成绩权重：以 100.5 为 0 点，低分正、高分负（线性插值）。"""
    pts = [(0.0, 1.5), (98.0, 1.5), (99.0, 1.0), (100.0, 0.5),
           (100.4, 0.0), (100.6, 0.0), (101.0, -1.0), (102.0, -1.0)]
    if ach <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= ach <= x1:
            if x1 == x0:
                return y1
            return y0 + (ach - x0) * (y1 - y0) / (x1 - x0)
    return pts[-1][1]


def dynamic_top_n(official_level: float | None) -> int:
    """v3.2：按 b35 末位官方定数取能力样本数。"""
    if official_level is None:
        return 50
    x = float(official_level)
    if x <= 12.5:
        return 50
    if x >= 14.0:
        return 200
    if x <= 13.0:
        return int(round(50 + 72 * (x - 12.5)))
    if x <= 13.5:
        return int(round(86 + 84 * (x - 13.0)))
    return int(round(128 + 144 * (x - 13.5)))


def _fit_logit(X: list[list[float]], y: list[float], lam: float = 5.0,
               iters: int = 30) -> list[float]:
    """L2 正则 logistic 回归（IRLS/牛顿法）；截距不惩罚。"""
    n, p = len(X), len(X[0])
    beta = [0.0] * p
    for _ in range(iters):
        eta = [sum(X[i][j] * beta[j] for j in range(p)) for i in range(n)]
        mu = [1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, e)))) for e in eta]
        w = [max(m * (1.0 - m), 1e-6) for m in mu]
        g = [sum(X[i][j] * (y[i] - mu[i]) for i in range(n)) for j in range(p)]
        H = [[sum(X[i][j] * w[i] * X[i][k] for i in range(n)) + (lam if (j == k and j > 0) else (1e-9 if j == k else 0.0))
              for k in range(p)] for j in range(p)]
        # 解 H·delta = g（高斯消元）
        A = [row[:] + [g[j]] for j, row in enumerate(H)]
        for c in range(p):
            piv = max(range(c, p), key=lambda r: abs(A[r][c]))
            if abs(A[piv][c]) < 1e-12:
                continue
            A[c], A[piv] = A[piv], A[c]
            for r in range(c + 1, p):
                f = A[r][c] / A[c][c]
                for c2 in range(c, p + 1):
                    A[r][c2] -= f * A[c][c2]
        delta = [0.0] * p
        for i in range(p - 1, -1, -1):
            if abs(A[i][i]) < 1e-12:
                continue
            delta[i] = (A[i][p] - sum(A[i][j] * delta[j] for j in range(i + 1, p))) / A[i][i]
        beta = [beta[j] + delta[j] for j in range(p)]
        if max(abs(d) for d in delta) < 1e-6:
            break
    return beta


def fit_v3(charts: list[dict], scores: dict, fit_map: dict, unreliable: set | None,
           params: dict, curves: dict | None = None) -> dict:
    """三步法：评价定数 → 能力区间 → 成绩/标签修正（详见 docs/fit-v3-design.md）。"""
    from lib.rating import rating as song_rating

    items = {}
    for chart in charts:
        if chart.get("family") in ("utage", "utage2p"):
            continue
        level = chart.get("level")
        if level is None:
            continue
        ckey = f"{chart['internalId']}:{chart['chartid']}"
        score = scores.get((chart["internalId"], chart["chartid"]))
        delta = fit_map.get(ckey)
        if delta is not None and unreliable and ckey in unreliable:
            delta = None
        d = None if delta is None else delta - level
        if d is not None:
            d = max(d, DELTA_MIN)      # 水鱼说"太简单"的过度乐观截断；说"更难"保持原样
        items[ckey] = {"level": level,
                       "localLevel": chart.get("localLevel") or level,
                       "version": chart.get("version") or "",
                       "score": score, "names": {t["name"] for t in chart["tags"]},
                       "delta": d}

    # ① 评价定数：水鱼 > 档位 Δ 先验 > 官标
    #    水 / 诈称谱 / 两者都有 是三种不同先验，不能合并平均
    water_groups = {"water": {}, "fake": {}, "both": {}}
    for it in items.values():
        if it["delta"] is None:
            continue
        has_water = "水" in it["names"]
        has_fake = "诈称谱" in it["names"]
        if not (has_water or has_fake):
            continue
        kind = "both" if (has_water and has_fake) else ("water" if has_water else "fake")
        water_groups[kind].setdefault(int(it["level"] // 1), []).append(it["delta"])

    def _water_mean(vals: list[float]) -> float:
        return statistics.fmean(vals) if vals else 0.0

    band_delta = {}
    for kind, groups in water_groups.items():
        all_vals = [d for vals in groups.values() for d in vals]
        band_delta[kind] = {
            "global": round(_water_mean(all_vals), 4),
            "bands": {str(b): round(_water_mean(v), 4) for b, v in groups.items() if v},
        }

    def water_prior(kind: str, band: int) -> float:
        item = band_delta.get(kind) or {}
        bands = item.get("bands") or {}
        return float(bands.get(str(band)) or 0.0)

    for it in items.values():
        if it["delta"] is not None:
            it["eval_level"] = it["level"] + it["delta"]
            it["fit_base"] = "water"
        else:
            has_water = "水" in it["names"]
            has_fake = "诈称谱" in it["names"]
            if has_water or has_fake:
                kind = "both" if (has_water and has_fake) else ("water" if has_water else "fake")
                prior = water_prior(kind, int(it["level"] // 1))
                it["eval_level"] = it["level"] + prior
                it["fit_base"] = "band-prior"
                it["water_prior"] = round(prior, 4)
                it["water_kind"] = kind
            else:
                it["eval_level"] = it["level"]
                it["fit_base"] = "official"

    # ② 能力区间：b35 末位官定 → 动态样本数
    rated = []
    for ckey, it in items.items():
        score = it["score"]
        if not score or score["achievement"] <= 0:
            continue
        ap = (score.get("comboStatus") or 0) >= 3
        r = song_rating(it["localLevel"], score["achievement"], ap)      # 单曲 rating 一律用本地定数
        rated.append((r, ckey))
    rated.sort(key=lambda x: -x[0])

    versions = [it["version"] for it in items.values()]
    _, _new_versions_n = new_song_versions(versions)
    old_for_n = sorted(((r, k) for r, k in rated if items[k]["version"] not in _new_versions_n),
                       key=lambda x: -x[0])
    new_for_n = sorted(((r, k) for r, k in rated if items[k]["version"] in _new_versions_n),
                       key=lambda x: -x[0])

    def _anchor_level(rows: list, idx: int):
        return items[rows[idx][1]]["level"] if len(rows) > idx else None

    if len(old_for_n) >= 35:
        anchor = _anchor_level(old_for_n, 34)
    elif old_for_n:
        anchor = _anchor_level(old_for_n, len(old_for_n) - 1)
    elif len(new_for_n) >= 15:
        anchor = _anchor_level(new_for_n, 14)
    elif new_for_n:
        anchor = _anchor_level(new_for_n, len(new_for_n) - 1)
    else:
        anchor = None
    top_n = dynamic_top_n(anchor)
    top = rated[:top_n]
    if top:
        evs = sorted(items[k]["eval_level"] for _, k in top)

        def _quantile(vals: list[float], q: float) -> float:
            if not vals:
                return 0.0
            pos = q * (len(vals) - 1)
            lo_i = int(pos)
            hi_i = min(lo_i + 1, len(vals) - 1)
            frac = pos - lo_i
            return vals[lo_i] * (1.0 - frac) + vals[hi_i] * frac

        # 用分位而不是极值：单张谱不决定边界
        ab_lo = _quantile(evs, 0.10)      # 能力区间下界（p10）
        ab_hi = _quantile(evs, 0.95)      # 能力区间上界（p95）
    else:
        ab_lo, ab_hi = 0.0, 99.0
    lo, hi = ab_lo - 0.3, ab_hi + 0.3                            # 置信区间

    # ③ 标签效应：直接回归到「定数」单位（方案 B，不需要 k 换算）
    #    obs = (成绩 − 该评价定数应打的成绩) / 斜率   ← 已换算成定数
    #    obs ≈ Σ γ_t·标签 + Σ δ_b·定数档
    tag_names = sorted({n for it in items.values() for n in it["names"]} - set(WATER_TAGS))
    obs_rows = []
    for k, it in items.items():
        sc = it["score"]
        if not sc or sc["achievement"] <= 0 or curves is None:
            continue
        if not (ab_lo <= it["eval_level"] <= ab_hi):
            continue
        exp = expected_at(curves, "*", it["eval_level"])
        if exp is None:
            continue
        slope = max(1.0, min(3.0, slope_near(curves, it["eval_level"]) or 1.5))
        raw = (sc["achievement"] - exp) / slope
        it["obs_shift"] = max(-1.5, min(1.5, raw))      # 去掉极端成绩的杠杆
        obs_rows.append(k)
    tag_gamma = {}
    tag_counts = {}
    for it in items.values():
        if not (ab_lo <= it["eval_level"] <= ab_hi):
            continue
        sc = it["score"]
        if not sc or sc["achievement"] <= 0:
            continue
        for n in it["names"]:
            if n not in WATER_TAGS:
                tag_counts[n] = tag_counts.get(n, 0) + 1
    tag_sample_factor = 0.5 if len(obs_rows) < 300 else 1.0
    if obs_rows and tag_names:
        band_of = lambda v: round(v * 2) / 2
        band_count = {}
        for k in obs_rows:
            band_count[band_of(items[k]["eval_level"])] = band_count.get(band_of(items[k]["eval_level"]), 0) + 1
        bands = sorted(b for b, c in band_count.items() if c >= 10)
        X, y = [], []
        for k in obs_rows:
            it = items[k]
            row = [1.0] + [1.0 if band_of(it["eval_level"]) == b else 0.0 for b in bands]
            row += [1.0 if n in it["names"] else 0.0 for n in tag_names]
            X.append(row)
            y.append(it["obs_shift"])
        off = 1 + len(bands)
        m = len(X)
        sds = []
        for j in range(off, len(X[0])):
            col = [X[i][j] for i in range(m)]
            mu = sum(col) / m
            sd = (sum((v - mu) ** 2 for v in col) / m) ** 0.5
            sds.append(sd)
            if sd > 1e-9:
                for i in range(m):
                    X[i][j] = (X[i][j] - mu) / sd
        # 岭回归（正规方程 + 高斯消元；截距与定数档不惩罚）
        p_dim = len(X[0])
        lam = float(params.get("tag_ridge_lambda", 200.0))
        ata = [[sum(X[r][i] * X[r][j] for r in range(m)) for j in range(p_dim)] for i in range(p_dim)]
        aty = [sum(X[r][i] * y[r] for r in range(m)) for i in range(p_dim)]
        for i in range(off, p_dim):
            ata[i][i] += lam
        for c in range(p_dim):
            piv = max(range(c, p_dim), key=lambda r: abs(ata[r][c]))
            if abs(ata[piv][c]) < 1e-12:
                continue
            ata[c], ata[piv] = ata[piv], ata[c]
            aty[c], aty[piv] = aty[piv], aty[c]
            for r in range(c + 1, p_dim):
                f = ata[r][c] / ata[c][c]
                for c2 in range(c, p_dim):
                    ata[r][c2] -= f * ata[c][c2]
                aty[r] -= f * aty[c]
        coef = [0.0] * p_dim
        for i in range(p_dim - 1, -1, -1):
            if abs(ata[i][i]) < 1e-12:
                continue
            coef[i] = (aty[i] - sum(ata[i][j] * coef[j] for j in range(i + 1, p_dim))) / ata[i][i]
        for idx, n in enumerate(tag_names):
            sd = sds[idx]
            if sd <= 1e-9:
                tag_gamma[n] = 0.0
                continue
            g = coef[off + idx] / sd              # 换回 0/1 尺度，单位＝定数
            a = tag_counts.get(n, 0)
            g *= a / (a + 20.0)                    # 样本越少越向 0 收缩
            tag_gamma[n] = max(-0.25, min(0.25, g))
    tag_u = tag_gamma                              # 对外仍用 tagU 字段，含义改为「定数」
    pat_names, eval_names = set(), set()
    for chart in charts:
        for t in chart["tags"]:
            if t["name"] in WATER_TAGS:
                continue
            if t.get("group") == PATTERN_GROUP:
                pat_names.add(t["name"])
            elif t.get("group") == EVAL_GROUP:
                eval_names.add(t["name"])
    weak_tags = {n for n, g in tag_gamma.items() if g <= -0.05}
    weak_pat = weak_tags & pat_names
    weak_eval = weak_tags & eval_names
    top_tags = {}

    tag_spread = 0.0
    tag_balance_factor = 1.0
    if tag_gamma:
        tag_spread = max(tag_gamma.values()) - min(tag_gamma.values())
        if params.get("tag_balance_mode") == "spread":
            ref = max(1e-6, float(params.get("tag_balance_ref", 0.25)))
            power = float(params.get("tag_balance_power", 2.0))
            floor = float(params.get("tag_balance_floor", 0.15))
            ratio = tag_spread / ref
            tag_balance_factor = max(floor, min(1.0, ratio ** power if ratio > 0 else 0.0))
    tag_count_mode = params.get("tag_count_mode", "none")
    tag_count_alpha = max(0.0, float(params.get("tag_count_alpha", 0.15)))

    # ④ 逐谱：区间内才修正
    out = {}
    for ckey, it in items.items():
        tag_corr = 0.0
        tag_raw = 0.0
        tag_k = 0
        tag_factor = 1.0
        tag_scope = 0.0
        fit = it["eval_level"]
        mode = it["fit_base"]
        in_range = lo <= it["eval_level"] <= hi
        if in_range:
            score = it["score"]
            score_corr = 0.0
            if score and score["achievement"] > 0:
                plays = score.get("playCount") or 0
                coef = 0.2 if plays >= 10 else 0.06           # 10 局及以上用 0.2；不足 10 局用 0.06
                score_corr = coef * _score_weight(score["achievement"])
            E = it["eval_level"]
            if ab_lo <= E <= ab_hi:
                tag_scope = 1.0
            elif lo <= E < ab_lo:
                tag_scope = 0.8
            elif ab_hi < E <= hi:
                tag_scope = 0.8
            else:
                tag_scope = 0.0
            if ab_lo + 0.1 <= E <= ab_hi - 0.1:
                tier, mult = 1, 1.0
            elif ab_hi - 0.1 < E <= hi:
                tier, mult = 2, 1.5        # 能力区间上沿之外：体力需求放大
            elif lo <= E < ab_lo + 0.1:
                tier, mult = 3, 0.5        # 能力区间下沿之外：体力需求缩小
            else:
                tier, mult = 1, 1.0
            # 标签效应直接是「定数」：γ>0 = 对你更容易
            # 档2/档3 不再限定固定体力标签，所有标签按 γ 分档
            def tag_mult(name: str) -> float:
                g = tag_gamma.get(name, 0.0)
                if tier == 2:
                    if g <= -0.05:
                        return 1.5
                    if g < 0:
                        return 1.2
                    return 1.0
                if tier == 3:
                    return 0.5 if g < 0 else 1.0
                return 1.0
            names = [n for n in it["names"] if n not in WATER_TAGS and n in tag_gamma] if tag_scope > 0 else []
            tag_k = len(names)
            if tag_count_mode == "sqrt":
                tag_factor = 1.0 / math.sqrt(max(1, tag_k))
            elif tag_count_mode == "linear":
                tag_factor = 1.0 / (1.0 + tag_count_alpha * max(0, tag_k - 1))
            tag_raw = sum(tag_gamma.get(n, 0.0) * tag_mult(n) for n in names)
            tag_base = max(-0.3, min(0.3, -tag_balance_factor * tag_factor * tag_raw * tag_scope))
            tag_corr = tag_base * tag_sample_factor
            corr = max(-0.3, min(0.3, score_corr + tag_corr))
            fit += corr
        score = it["score"]
        cur_rating = None
        if score and score["achievement"] > 0:
            cur_rating = song_rating(it["localLevel"], score["achievement"],
                                     (score.get("comboStatus") or 0) >= 3)
        out[ckey] = {"fit": round(fit, 3), "mode": mode, "inRange": in_range,
                     "inConf": lo <= fit <= hi,        # 能力范围内歌曲：个人拟合落在置信区间内
                     "tier": tier if in_range else 0,
                     "evalLevel": round(it["eval_level"], 3),
                     "localLevel": it["localLevel"], "rating": cur_rating,
                     "tagCorr": round(tag_corr, 4), "tagRaw": round(tag_raw, 4),
                     "tagK": tag_k, "tagFactor": round(tag_factor, 4),
                     "tagBalance": round(tag_balance_factor, 4),
                     "tagScope": round(tag_scope, 2), "tagSample": round(tag_sample_factor, 2),
                     "waterKind": it.get("water_kind"), "waterPrior": it.get("water_prior")}

    in_conf_fits = sorted(rec["fit"] for rec in out.values() if rec.get("inConf"))
    fit_median = in_conf_fits[len(in_conf_fits) // 2] if in_conf_fits else 0.0
    tiers = {1: 0, 2: 0, 3: 0}
    for rec in out.values():
        if rec.get("inRange"):
            tiers[rec.get("tier") or 1] += 1
    # b50 = 旧曲 35 + 新曲 15；两池分开算截断值
    versions = [it["version"] for it in items.values()]
    current, new_versions = new_song_versions(versions)
    old_rows = sorted(((rec["rating"], k) for k, rec in out.items()
                       if rec.get("rating") and items[k]["version"] not in new_versions), reverse=True)
    new_rows = sorted(((rec["rating"], k) for k, rec in out.items()
                       if rec.get("rating") and items[k]["version"] in new_versions), reverse=True)
    old_rated = [r for r, _ in old_rows]
    new_rated = [r for r, _ in new_rows]
    # 池子没满（不足 35 / 15 张）时门槛取 0：名额没竞争，池内全部算推分
    b35 = old_rated[34] if len(old_rated) >= 35 else 0
    b15 = new_rated[14] if len(new_rated) >= 15 else 0
    in_b35 = {k for _, k in old_rows[:35]}
    in_b15 = {k for _, k in new_rows[:15]}
    for k, rec in out.items():
        is_new = items[k]["version"] in new_versions
        rec["pool"] = "new" if is_new else "old"
        rec["cutoff"] = b15 if is_new else b35
        rec["inB50"] = k in (in_b15 if is_new else in_b35)
    return {"byKey": out, "lo": round(lo, 3), "hi": round(hi, 3),
            "b50": {"b35": b35, "b15": b15, "current": current,
                    "newVersions": sorted(new_versions),
                    "oldCount": len(old_rated), "newCount": len(new_rated)},
            "ability": [round(ab_lo, 3), round(ab_hi, 3)], "tiers": tiers,
            "topN": len(top), "topNRequested": top_n,
            "topNAnchor": round(float(anchor), 3) if anchor is not None else None,
            "topNMode": "dynamic-b35",
            "tagSample": {"n": len(obs_rows), "factor": round(tag_sample_factor, 2)},
            "fitMedian": round(fit_median, 3),
            "weakTags": sorted(weak_tags),
            "weakPattern": sorted(weak_pat), "weakEval": sorted(weak_eval),
            "profileStatus": ("balanced" if tag_gamma and all(abs(g) < 0.05 for g in tag_gamma.values())
                               else "distinct"),
            "tagBalance": {"mode": params.get("tag_balance_mode", "none"),
                           "countMode": params.get("tag_count_mode", "none"),
                           "spread": round(tag_spread, 4),
                           "factor": round(tag_balance_factor, 4)},
            "bandDelta": band_delta,
            "tagU": {k: round(v, 3) for k, v in tag_u.items()},
            "tagUPattern": {k: round(v, 3) for k, v in tag_u.items() if k in pat_names},
            "tagUEval": {k: round(v, 3) for k, v in tag_u.items() if k in eval_names},
            "topTags": dict(sorted(top_tags.items(), key=lambda x: -x[1])[:12]),
            "samples": len(items)}


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(prog="analyze_profile.py",
                                 description="按成绩 + 标签给每张谱打标签，写 analysis.json")
    ap.add_argument("--user", metavar="UUID", required=True, help="本地用户 uuid")
    ap.add_argument("--scores-root", metavar="PATH", help="用户数据根目录（默认 dataUser/）")
    ap.add_argument("--fusion-dir", metavar="PATH", help="曲库目录（默认 data/fusion/）")
    ap.add_argument("-o", "--out", metavar="PATH", help="输出文件（默认 <用户目录>/analysis.json）")
    ap.add_argument("--mastered-ach", type=float, default=PARAMS["mastered_ach"])
    ap.add_argument("--tag-count-mode", choices=("none", "sqrt", "linear"),
                    default=PARAMS["tag_count_mode"])
    ap.add_argument("--tag-count-alpha", type=float, default=PARAMS["tag_count_alpha"])
    ap.add_argument("--tag-balance-mode", choices=("none", "spread"),
                    default=PARAMS["tag_balance_mode"])
    ap.add_argument("--tag-balance-ref", type=float, default=PARAMS["tag_balance_ref"])
    ap.add_argument("--tag-balance-power", type=float, default=PARAMS["tag_balance_power"])
    ap.add_argument("--tag-balance-floor", type=float, default=PARAMS["tag_balance_floor"])
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    params = dict(PARAMS)
    params["mastered_ach"] = args.mastered_ach
    for key in ("tag_count_mode", "tag_count_alpha", "tag_balance_mode",
                "tag_balance_ref", "tag_balance_power", "tag_balance_floor"):
        params[key] = getattr(args, key)

    root = Path(args.scores_root).resolve() if args.scores_root else user_data_root()
    fusion_dir = Path(args.fusion_dir).resolve() if args.fusion_dir else data_root(FUSION_SCRIPT_DIR)
    user_dir = root / args.user
    out_path = Path(args.out).resolve() if args.out else user_dir / ANALYSIS_NAME

    print(BAR)
    print("analyze_profile · 这张谱对你是什么")
    print(f"  用户目录 : {user_dir}/")
    print(f"  曲库     : {fusion_dir}/")
    print(RULE)

    # 输入没变就跳过：成绩 / 黑名单 / 曲库 都比 analysis.json 旧、参数也没变 → 直接用现有结果
    if is_fresh(out_path, [user_dir / SCORES_NAME, user_dir / BLACKLIST_NAME,
                           *sorted(fusion_dir.glob("fusion-*.json")),
                           PROJECT_DIR / "lib" / "versions.py",
                           data_root("fit_diff") / FIT_NAME, Path(__file__)], expected=params):
        print(f"输入没变，跳过（复用 {ANALYSIS_NAME}）")
        return 0

    try:
        charts, meta = load_charts(fusion_dir)
        scores = load_scores(user_dir)
        blacklist = load_blacklist(user_dir)
    except (OSError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    played = sum(1 for c in charts if (scores.get((c["internalId"], c["chartid"])) or {}).get("achievement", 0) > 0)
    curves, _fitted_slope = build_curves(charts, scores, params)
    fit_map = load_fit_diff()
    unreliable = unreliable_fits(load_fit_meta())
    v3 = fit_v3(charts, scores, fit_map, unreliable, params, curves)
    tag_stats = {}
    print(f"拟合 v3   : 能力区间 [{v3['ability'][0]:.2f}, {v3['ability'][1]:.2f}]，"
          f"置信区间 [{v3['lo']:.2f}, {v3['hi']:.2f}]，三档 {v3['tiers']}，"
          f"档位Δ先验 {sum(len(v.get('bands') or {}) for v in v3['bandDelta'].values())} 个，样本 {v3['samples']}")

    rows = []
    counts = {"mastered": 0, "pushable_b35": 0, "pushable_b15": 0, "ap": 0,
              "practice": 0, "accuracy": 0, "challenge": 0, "unplayed": 0, "blacklisted": 0}
    for chart in charts:
        level = chart.get("level")
        if level is None:
            continue
        score = scores.get((chart["internalId"], chart["chartid"]))
        if chart.get("family") in ("utage", "utage2p"):
            continue                                          # 宴谱不是常规谱，不参与分类
        ckey = f"{chart['internalId']}:{chart['chartid']}"
        delta = fit_map.get(ckey)
        delta_unreliable = delta is not None and ckey in unreliable
        if delta_unreliable:
            delta = None
        delta = None if delta is None else delta - level
        names = {t["name"] for t in chart["tags"]}
        band_adj = 0.0
        names = {t["name"] for t in chart["tags"]}
        rec = v3["byKey"].get(ckey) or {}
        if not rec:
            effective = None
            fit_mode = "none"
            eff_for_class = level
        else:
            effective = rec["fit"]
            fit_mode = rec["mode"] + ("" if rec.get("inRange") else "-out")
            eff_for_class = effective
        predicted = expected_at(curves, "*", eff_for_class) or 0.0
        labels = classify(chart, score, eff_for_class, tag_stats, params,
                          v3.get("ability") or (0.0, 99.0), rec.get("evalLevel") if rec else None,
                          rec.get("localLevel") if rec else None, rec.get("cutoff") if rec else None,
                          rec.get("pool") if rec else None,
                          bool(rec and rec.get("inConf")), bool(rec and rec.get("inB50")),
                          (v3["lo"], v3["hi"]), v3.get("fitMedian") or 0.0,
                          set(v3.get("weakTags") or []),
                          set(v3.get("weakPattern") or []), set(v3.get("weakEval") or []))
        blocked = (chart["internalId"], chart["chartid"]) in blacklist
        if blocked:
            counts["blacklisted"] += 1
            labels = []
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        rows.append({
            "internalId": chart["internalId"],
            "chartid": chart["chartid"],
            "difficulty": chart.get("difficulty"),
            "tier": chart.get("tier"),
            "family": chart["family"],
            "level": level,
            "effectiveLevel": effective,
            "fitEvalLevel": rec.get("evalLevel") if rec else None,
            "fitInConf": rec.get("inConf") if rec else None,
            "fitTier": rec.get("tier") if rec else None,
            "tagCorr": rec.get("tagCorr") if rec else None,
            "tagRaw": rec.get("tagRaw") if rec else None,
            "tagK": rec.get("tagK") if rec else None,
            "tagFactor": rec.get("tagFactor") if rec else None,
            "tagBalance": rec.get("tagBalance") if rec else None,
            "tagScope": rec.get("tagScope") if rec else None,
            "tagSample": rec.get("tagSample") if rec else None,
            "waterKind": rec.get("waterKind") if rec else None,
            "waterPrior": rec.get("waterPrior") if rec else None,
            "fitMode": fit_mode,
            "deltaUnreliable": delta_unreliable,
            "delta": None if delta is None else round(delta, 3),
            "tags": [t["name"] for t in chart["tags"]],
            "achievement": round(score["achievement"], 4) if score else None,
            "combo": score["combo"] if score else None,
            "sync": score["sync"] if score else None,
            "playCount": score["playCount"] if score else 0,
            "expected": round(expected_at(curves, chart.get("tier"), level) or 0, 4),
            "residual": round(score["achievement"] - (expected_at(curves, chart.get("tier"), level) or 0), 4)
            if score and score["achievement"] > 0 else None,
            "labels": labels,
            "predicted": predicted,
            "blacklisted": blocked,
        })

    payload = {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": now_stamp(),
        "params": params,
        "fitModel": {"model": "v3.2",
                     "range": [v3["lo"], v3["hi"]],
                     "abilityRange": v3.get("ability"),
                     "fitMedian": v3.get("fitMedian"),
                     "weakTags": v3.get("weakTags"),
                     "weakPattern": v3.get("weakPattern"), "weakEval": v3.get("weakEval"),
                     "profileStatus": v3.get("profileStatus"),
                     "tagBalance": v3.get("tagBalance"),
                     "tiers": v3.get("tiers"),
                     "b50": v3.get("b50"),
                     "stamina": {"eval": sorted(STAMINA_EVAL), "pat": sorted(STAMINA_PAT),
                                 "mult": {"top": 1.5, "topPos": 1.0, "bottom": 0.5}},
                     "topN": v3["topN"],
                     "topNRequested": v3.get("topNRequested"),
                     "topNMode": v3.get("topNMode"),
                     "topNAnchor": v3.get("topNAnchor"),
                     "tagSample": v3.get("tagSample"),
                     "bandDelta": v3["bandDelta"],
                     "tagU": v3["tagU"],
                     "tagUPattern": v3.get("tagUPattern"),
                     "tagUEval": v3.get("tagUEval"),
                     "topTags": v3["topTags"],
                     "tagCalib": {"pattern": 0.093, "eval": 0.191},
                     "scoreWeight": {"base": 0.2, "lowPlays": 0.06, "table": [[98, 1.5], [99, 1.0], [100, 0.5], [100.5, 0.0], [101, -1.0]], "cap": 0.3},
                     "samples": v3["samples"]},
        "counts": {"charts": len(rows), "played": played, "blacklisted": counts["blacklisted"],
                   "labels": {k: v for k, v in counts.items() if k != "blacklisted"}},
        "abilityCurve": {diff: pts for diff, pts in sorted(curves.items())},
        "tagStats": sorted(({"tag": name, **info} for name, info in tag_stats.items()),
                           key=lambda x: x["levelAdj"], reverse=True),
        "charts": rows,
    }
    try:
        write_json(out_path, payload)
    except OSError as exc:
        print(f"[错误] 写盘失败：{exc}", file=sys.stderr)
        return 2

    print(f"曲库谱面 {meta['sheets']}，有成绩 {played}，黑名单生效 {counts['blacklisted']}")
    print("标签修正（正 = 对你更难）：")
    for item in payload["tagStats"][:12]:
        print(f"  {item['tag']:<8} {item['levelAdj']:+.2f} 定数   n={item['n']:<4} 配对差 {(item.get('rawResidual') or 0):+.2f}")
    print("分类： " + "  ".join(f"{DIFF_NAMES.get(k, k)}={v}" for k, v in payload["counts"]["labels"].items()))
    print(RULE)
    print("抽查（每个标签 3 个例子）：")
    for label in ("mastered", "pushable_b35", "pushable_b15", "ap", "practice", "accuracy", "challenge", "unplayed"):
        picks = [r for r in rows if label in r["labels"]][:3]
        if not picks:
            continue
        print(f"  {label}:")
        for r in picks:
            print(f"    #{r['internalId']}-{r['chartid']} {DIFF_NAMES.get(r['difficulty'], r['difficulty'])} "
                  f"定数{r['level']}→{r['effectiveLevel']} 分{r['achievement']} FC{r['combo']} 标签{','.join(r['tags']) or '-'}")
    print(RULE)
    print(f"已写入 {out_path}")
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
