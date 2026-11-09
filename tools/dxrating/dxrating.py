#!/usr/bin/env python3
# 用途：按版本算用户的总 DX rating（B35 + B15 分池取前 35 / 前 15）
# 状态：现行
"""按"用户选的版本"算 DX rating。

【池子怎么划】旧框（FiNALE 及以前）不能作为“当前版本”去选，但旧曲仍可进入 B35。
DX 时代每个版本：B15 = 本版本 + 上一个版本的谱（CiRCLE 起双版本）；B35 = 再往前的全部版本，
包括 maimai～FiNALE 与更早的 DX 版本。
用户给的三条已按原话写进 VERSION_POOLS，其余 DX 版本按同一套规则推出。

【单曲 rating】tools/rating_calc 里那套：int(系数 × 定数 × min(100.5, 达成率) / 100)，
  定数优先取曲库的 dxratingLevelValue，空的话退回 localLevelValue；两者都没有就跳过并计数。

【用法】
    python tools/dxrating/dxrating.py --user <uuid> --version CiRCLE
    python tools/dxrating/dxrating.py --user <uuid> --version CiRCLE --json
    python tools/dxrating/dxrating.py --versions          # 列出可选版本
  退出码：0 正常；2 参数/数据不足。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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

sys.path.insert(0, str(PROJECT_DIR / "tools" / "rating_calc"))

from lib.cache import is_fresh   # noqa: E402  （PROJECT_DIR 进 sys.path 之后才能 import lib）
from lib.rating import coefficient, rating as chart_rating   # noqa: E402
from lib.versions import (B15_DOUBLE_FROM, RATING_VERSIONS, VERSION_ORDER,  # noqa: E402
                          canonical_version)

TZ_CN = timezone(timedelta(hours=8))
BAR = "=" * 64

# 版本主表来自 lib/versions.py；这里只保留 rating 的池规则。
B15_COUNT, B35_COUNT = 15, 35

def _pools(version: str) -> tuple[tuple, tuple]:
    """→ (B15 版本集合, B35 版本集合)。B35 永远是 B15 范围之前的全部版本。"""
    i = VERSION_ORDER.index(version)
    span = 2 if i >= VERSION_ORDER.index(B15_DOUBLE_FROM) else 1
    b15 = VERSION_ORDER[max(0, i - span + 1):i + 1]
    b35 = VERSION_ORDER[:max(0, i - span + 1)]
    return b15, b35


def data_dir(script: str) -> Path:
    if env is not None and callable(getattr(env, "data_dir_for", None)):
        return Path(env.data_dir_for(script))
    return PROJECT_DIR / "data" / script


def user_data_root() -> Path:
    """用户数据根目录（默认 dataUser/）：<uuid>/scores.json、dxrating.json 都在这里。"""
    if env is not None and callable(getattr(env, "user_data_dir", None)):
        try:
            return Path(env.user_data_dir())
        except Exception:                                  # noqa: BLE001
            pass
    return PROJECT_DIR / "dataUser"


def load_library(fusion_dir: Path) -> dict:
    """(internalId, chartid) → {"version", "level"}；level 优先 dxratingLevelValue。"""
    table = {}
    for family in ("std", "dx", "utage", "utage2p"):
        path = fusion_dir / f"fusion-{family}.json"
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        for song in doc.get("songs") or []:
            for sheet in song.get("sheets") or []:
                level = sheet.get("dxratingLevelValue")
                if level is None:
                    level = sheet.get("localLevelValue")
                table[(song.get("internalId"), sheet.get("chartid"))] = {
                    "version": canonical_version(song.get("version")),
                    "level": level,
                }
    return table


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(prog="dxrating.py", description="按版本算 DX rating（B35+B15）")
    ap.add_argument("--user", help="本地用户 uuid")
    ap.add_argument("--version", help="版本名，见 --versions")
    ap.add_argument("--scores-dir", help="成绩目录（默认 dataUser/）")
    ap.add_argument("--fusion-dir", help="曲库目录（默认 data/fusion/）")
    ap.add_argument("--versions", action="store_true", help="列出可选版本后退出")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.versions:
        print("\n".join(RATING_VERSIONS))
        return 0
    if not args.user or not args.version:
        print("[错误] 要给 --user 和 --version（--versions 看可选值）", file=sys.stderr)
        return 2
    args.version = canonical_version(args.version)
    if args.version not in RATING_VERSIONS:
        print(f"[错误] 版本不在 DX 时代列表里：{args.version}", file=sys.stderr)
        return 2

    scores_dir = Path(args.scores_dir).resolve() if args.scores_dir else user_data_root()
    fusion_dir = Path(args.fusion_dir).resolve() if args.fusion_dir else data_dir("fusion")
    scores_path = scores_dir / args.user / "scores.json"
    if not scores_path.is_file():
        print(f"[错误] 这个用户还没有成绩：{scores_path}", file=sys.stderr)
        return 2

    out_path = (Path(args.scores_dir).resolve() if args.scores_dir else user_data_root()) / args.user / "dxrating.json"
    rating_dir = data_dir("dxrating_build") if hasattr(globals(), "data_dir") else PROJECT_DIR / "data" / "dxrating_build"
    if is_fresh(out_path, [scores_path,
                           *sorted((PROJECT_DIR / "data" / "fusion").glob("fusion-*.json")),
                           *sorted(rating_dir.glob("dxrating-*.json")),
                           PROJECT_DIR / "lib" / "versions.py", Path(__file__)],
                expected=args.version, key_field="version"):
        print(f"输入没变，跳过（复用 dxrating.json）")
        if args.json:
            print(json.dumps(json.loads(out_path.read_text(encoding="utf-8-sig")), ensure_ascii=False))
        return 0


    library = load_library(fusion_dir)
    scores = json.loads(scores_path.read_text(encoding="utf-8-sig")).get("scores") or []
    b15_versions, b35_versions = _pools(args.version)

    pools = {"b15": [], "b35": []}
    skipped_no_level = skipped_no_version = 0
    for row in scores:
        key = (row.get("internalId"), row.get("chartid"))
        entry = library.get(key)
        if not entry or not entry.get("level"):
            skipped_no_level += 1
            continue
        version = entry.get("version")
        achievement = int(row.get("achievement") or 0) / 10000
        if achievement <= 0:
            continue
        ap = int(row.get("comboStatus") or 0) in (3, 4)          # AP / AP+
        value = chart_rating(float(entry["level"]), achievement, ap)
        coef, rank = coefficient(achievement)
        item = {
            "internalId": key[0], "chartid": key[1], "version": version,
            "level": float(entry["level"]), "achievement": round(achievement, 4),
            "coefficient": coef, "rank": rank, "ap": ap, "rating": value,
        }
        if version in b15_versions:
            pools["b15"].append(item)
        elif version in b35_versions:
            pools["b35"].append(item)
        else:
            skipped_no_version += 1

    b15 = sorted(pools["b15"], key=lambda x: -x["rating"])[:B15_COUNT]
    b35 = sorted(pools["b35"], key=lambda x: -x["rating"])[:B35_COUNT]
    total = sum(x["rating"] for x in b15) + sum(x["rating"] for x in b35)

    result = {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "kind": "dxrating",
        "user": args.user,
        "version": args.version,
        "b15Versions": list(b15_versions),
        "b35Versions": list(b35_versions),
        "dxRating": total,
        "b15": b15, "b35": b35,
        "counts": {
            "b15": len(b15), "b35": len(b35),
            "candidatesB15": len(pools["b15"]), "candidatesB35": len(pools["b35"]),
            "skippedNoLevel": skipped_no_level, "skippedOtherVersion": skipped_no_version,
        },
    }

    out = scores_dir / args.user / "dxrating.json"
    try:
        tmp = out.with_suffix(".json.part")
        tmp.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        tmp.replace(out)
    except OSError as exc:
        print(f"[错误] 写结果失败：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({k: result[k] for k in ("version", "dxRating", "counts", "b15Versions", "b35Versions")},
                         ensure_ascii=False))
        return 0

    print(BAR)
    print(f"DX rating · {args.version}")
    print(f"  B15 来自: {' + '.join(b15_versions)}")
    print(f"  B35 来自: {b35_versions[-1]} 及以前（共 {len(b35_versions)} 个版本）" if b35_versions else "  B35 来自: —")
    print("-" * 64)
    for label, items, cap in (("B15", b15, B15_COUNT), ("B35", b35, B35_COUNT)):
        print(f"  {label} 取前 {cap}（候选 {len(pools[label.lower()])} 条）")
        for it in items[:5]:
            print(f"    {it['rating']:>4}  {it['rank']:<4} 定数 {it['level']:<4g} {it['achievement']:g}%  v={it['version']}")
        if len(items) > 5:
            print(f"    … 其余 {len(items) - 5} 条")
    print("-" * 64)
    print(f"  合计 DX rating  {total}")
    print(f"  写盘 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
