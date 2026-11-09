# 用途：单曲 DX rating 的唯一实现（系数表 + 公式 + 下一档阈值）—— rating_calc / dxrating / analyze_profile 共用
# 状态：现行（2026-10-20 从 tools/rating_calc 抽出，AP/AP+ 额外 +1）
"""单曲 DX rating：

    rating = int(系数 × 定数 × min(100.5, 达成率) / 100) + (AP/AP+ ? 1 : 0)
"""

from __future__ import annotations

CAP = 100.5

# (下界达成率, 系数, 评级) —— 与 maimaidx-prober 逐行一致，含 99.9999 这类哨兵行
COEF_TABLE = (
    (0.0, 0.0, "D"),
    (10.0, 1.6, "D"),
    (20.0, 3.2, "D"),
    (30.0, 4.8, "D"),
    (40.0, 6.4, "D"),
    (50.0, 8.0, "C"),
    (60.0, 9.6, "B"),
    (70.0, 11.2, "BB"),
    (75.0, 12.0, "BBB"),
    (79.9999, 12.8, "BBB"),
    (80.0, 13.6, "A"),
    (90.0, 15.2, "AA"),
    (94.0, 16.8, "AAA"),
    (96.9999, 17.6, "AAA"),
    (97.0, 20.0, "S"),
    (98.0, 20.3, "S+"),
    (98.9999, 20.6, "S+"),
    (99.0, 20.8, "SS"),
    (99.5, 21.1, "SS+"),
    (99.9999, 21.4, "SS+"),
    (100.0, 21.6, "SSS"),
    (100.4999, 22.2, "SSS"),
    (100.5, 22.4, "SSS+"),
)


COEF_MAX = max(coef for _floor, coef, _rank in COEF_TABLE)   # 22.4：用来把系数归一化到 0..1


def coefficient(achievement: float) -> tuple[float, str]:
    """达成率 → (系数, 评级)。"""
    for i, (_, coef, rank) in enumerate(COEF_TABLE):
        if i == len(COEF_TABLE) - 1 or achievement < COEF_TABLE[i + 1][0]:
            return coef, rank
    return COEF_TABLE[-1][1], COEF_TABLE[-1][2]


def rating(ds: float, achievement: float, ap: bool = False) -> int:
    """单曲 rating；ap=True（AP / AP+）额外 +1。"""
    coef, _ = coefficient(achievement)
    value = int(coef * ds * min(CAP, achievement) / 100)
    return value + (1 if ap else 0)


def next_threshold(achievement: float) -> tuple[float, float]:
    """下一档系数的达成率下限与系数；已到顶返回 (ach, 当前系数)。"""
    cur, _ = coefficient(achievement)
    for floor, coef, _rank in COEF_TABLE:
        if floor > achievement + 1e-9:
            return floor, coef
    return achievement, cur
