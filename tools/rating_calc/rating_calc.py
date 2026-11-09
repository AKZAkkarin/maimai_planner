#!/usr/bin/env python3
# 用途：单曲 DX rating 计算器（定数 × 达成率 → rating / 评级 / 系数）
# 状态：现行
"""算一张谱的 DX rating。

【算法（抄自 maimaidx-prober 的 database/models/maimai.py）】
    ra = int(c * ds * min(100.5, achievement) / 100)
  ds = 谱面定数（13.9 这种，带小数）；achievement = 达成率百分比（100.5 = 100.5%）；
  c = 达成率对应的系数，见下面 COEF_TABLE（分段查表，取"下界 ≤ 达成率"的那一段）。
  达成率超过 100.5% 时按 100.5 算（上限），所以 101% 和 100.5% 的 rating 一样。

【用法】
    python tools/rating_calc/rating_calc.py --level 13.9 --achievement 100.5
    python tools/rating_calc/rating_calc.py --level 13.9 --achievement 100.5 --ap --json
  退出码：0 正常；2 参数不对。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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

BAR = "=" * 56

# 系数表 / 公式 / AP 加成都在 lib/rating.py（rating_calc、dxrating、analyze_profile 共用）
from lib.rating import CAP, COEF_TABLE, coefficient, rating   # noqa: E402




def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()
    ap = argparse.ArgumentParser(prog="rating_calc.py", description="单曲 DX rating 计算器")
    ap.add_argument("--level", type=float, required=True, help="谱面定数，如 13.9")
    ap.add_argument("--achievement", type=float, required=True, help="达成率百分比，如 100.5")
    ap.add_argument("--ap", action="store_true", help="AP / AP+：rating 额外 +1")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.level <= 0 or args.achievement <= 0:
        print("[错误] 定数和达成率都要大于 0", file=sys.stderr)
        return 2

    coef, rank = coefficient(args.achievement)
    base = rating(args.level, args.achievement)
    value = rating(args.level, args.achievement, args.ap)
    capped = args.achievement > CAP
    result = {
        "runId": RUN_ID,
        "level": args.level,
        "achievement": args.achievement,
        "effectiveAchievement": min(CAP, args.achievement),
        "coefficient": coef,
        "rank": rank,
        "ap": bool(args.ap),
        "baseRating": base,
        "rating": value,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    print(BAR)
    print("单曲 DX rating")
    print(f"  定数        {args.level:g}")
    print(f"  达成率      {args.achievement:g}%" + (f"（超过 {CAP:g}%，按 {CAP:g}% 算）" if capped else ""))
    print(f"  评级        {rank}")
    print(f"  系数        {coef:g}")
    print("-" * 56)
    print(f"  AP 加成     {'+1' if args.ap else '无'}")
    print(f"  rating      {value}   = int({coef:g} × {args.level:g} × {min(CAP, args.achievement):g} / 100)"
          + (" + 1" if args.ap else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
