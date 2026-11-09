#!/usr/bin/env python3
# 用途：统计某个本地用户的成绩（对照曲库算覆盖率、RANK/FC/AP 分布、按版本聚合），写进他自己的目录
# 状态：现行
"""把「这个用户打得怎么样」算成一份统计，跟他的成绩库放在一起。

【谁调用】成绩页每次进入（或切用户、点刷新）时由服务端跑一次 —— 它就是「进页面时激活」的那个脚本。
  手工跑也可以：python tools/scores_stats/scores_stats.py --user <uuid>

【读什么】
  dataUser/<uuid>/scores.json      这个用户的成绩（8 列，一行一条）
  data/fusion/fusion-<族>.json               曲库（算分母、拿版本、拿族）

【算什么】
  * 总览：曲库谱数 / 已打谱数 / 覆盖率、100% 以上、FC 及以上、AP 及以上
  * RANK 分布（0–13，D…SSS+）
  * comboStatus 分布（没有 / FC / FC+ / AP / AP+）
  * 分族（std / dx / utage / utage2p）：谱数、已打、覆盖率
  * 按版本：曲库谱数、已打、覆盖率、100%+、AP+（版本取曲库那一列，不是别处推的）

【写什么】dataUser/<uuid>/stats.json（和成绩库同一个目录）
  纯统计，不含任何账号信息、不含本机路径。

【用法】
    python tools/scores_stats/scores_stats.py --user <uuid> [--scores-root PATH] [--fusion-dir PATH]
    退出码：0 成功；2 读不到成绩库/曲库，或写盘失败。不联网。
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

from lib.cache import is_fresh   # noqa: E402
from lib.versions import canonical_version   # noqa: E402

SCRIPT_DIR_NAME = "scores_stats"
SCORES_SCRIPT_DIR = "scores_import"
FUSION_SCRIPT_DIR = "fusion"
SCORES_NAME = "scores.json"
USER_FILE = "users.json"
STATS_NAME = "stats.json"
FAMILIES = ("std", "dx", "utage", "utage2p")
RANK_NAMES = ("D", "C", "B", "BB", "BBB", "A", "AA", "AAA",
              "S", "S+", "SS", "SS+", "SSS", "SSS+")
COMBO_NAMES = ("没有", "FC", "FC+", "AP", "AP+")
TZ_CN = timezone(timedelta(hours=8))
BAR = "=" * 68
RULE = "─" * 68


def now_stamp() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")


def user_data_root() -> Path:
    """用户数据根目录（默认 dataUser/）：<uuid>/scores.json、stats.json、users.json 都在这里。"""
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


def pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(prog="scores_stats.py",
                                 description="统计某个本地用户的成绩，写进他自己的目录（stats.json）")
    ap.add_argument("--user", metavar="UUID", required=True, help="本地用户 uuid")
    ap.add_argument("--scores-root", metavar="PATH",
                    help=f"成绩库根目录（默认 data/{SCORES_SCRIPT_DIR}/）")
    ap.add_argument("--fusion-dir", metavar="PATH", help=f"曲库目录（默认 data/{FUSION_SCRIPT_DIR}/）")
    ap.add_argument("-o", "--out", metavar="PATH", help="输出文件（默认 <成绩库根>/<uuid>/stats.json）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    scores_root = Path(args.scores_root).resolve() if args.scores_root else user_data_root()
    fusion_dir = Path(args.fusion_dir).resolve() if args.fusion_dir else data_root(FUSION_SCRIPT_DIR)
    user_dir = scores_root / args.user
    out_path = Path(args.out).resolve() if args.out else user_dir / STATS_NAME

    if is_fresh(out_path, [user_dir / SCORES_NAME, scores_root / USER_FILE,
                           *sorted(fusion_dir.glob("fusion-*.json")),
                           PROJECT_DIR / "lib" / "versions.py", Path(__file__)]):
        print(f"输入没变，跳过（复用 {out_path.name}）")
        return 0

    print(BAR)
    print("scores_stats · 这个用户打得怎么样")
    print(f"  用户目录 : {user_dir}/")
    print(f"  曲库     : {fusion_dir}/")
    print(RULE)

    try:
        scores_doc = read_json(user_dir / SCORES_NAME, "这个用户的成绩库")
        users = read_json(scores_root / USER_FILE, "本地用户表").get("users") or []
    except (OSError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    user = next((u for u in users if u.get("id") == args.user), {"id": args.user, "name": args.user})

    # 曲库：分母 + 版本 + 族
    lib = {}                                              # (internalId, chartid) -> (version, family)
    family_total = {f: 0 for f in FAMILIES}
    try:
        for family in FAMILIES:
            doc = read_json(fusion_dir / f"fusion-{family}.json", f"曲库（{family}）")
            for song in doc.get("songs") or []:
                for sheet in song.get("sheets") or []:
                    key = (song.get("internalId"), sheet.get("chartid"))
                    if key[0] is None:
                        continue
                    lib[key] = (canonical_version(sheet.get("version") or song.get("version")) or "?", family)
                    family_total[family] += 1
    except (OSError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    rows = scores_doc.get("scores") or []
    rank_hist = {name: 0 for name in RANK_NAMES}
    combo_hist = {name: 0 for name in COMBO_NAMES}
    family_played = {f: 0 for f in FAMILIES}
    versions: dict[str, dict] = {}
    played_keys = set()
    hundred = ap_plus = fc_up = 0
    achievement_sum = 0

    fdx = fdx_plus = 0
    dx_hist = {str(i): 0 for i in range(6)}
    dx_unknown = 0
    not_in_library = 0
    for row in rows:
        key = (row.get("internalId"), row.get("chartid"))
        achievement = int(row.get("achievement") or 0)
        achievement_sum += achievement
        bucket_dx = row.get("dxscore")
        if bucket_dx is None:
            dx_unknown += 1
        else:
            dx_hist[str(bucket_dx)] = dx_hist.get(str(bucket_dx), 0) + 1
        if key not in lib:                        # 用户要求：只为曲库里有的谱做统计
            not_in_library += 1
            continue
        played_keys.add(key)
        if achievement >= 1000000:
            hundred += 1
        combo = int(row.get("comboStatus") or 0)
        if 1 <= combo < len(COMBO_NAMES):
            combo_hist[COMBO_NAMES[combo]] += 1
            fc_up += 1
        else:
            combo_hist[COMBO_NAMES[0]] += 1
        if combo == 4:
            ap_plus += 1
        sync = int(row.get("syncStatus") or 0)
        if sync == 3:
            fdx += 1
        elif sync == 4:
            fdx_plus += 1
        rank = int(row.get("RANK") or 0)
        rank_hist[RANK_NAMES[rank]] = rank_hist.get(RANK_NAMES[rank], 0) + 1

        version, family = lib[key]
        if family:
            family_played[family] += 1
        bucket = versions.setdefault(version, {"sheets": 0, "played": 0, "hundred": 0, "ap": 0})
        bucket["played"] += 1
        if achievement >= 1000000:
            bucket["hundred"] += 1
        if combo == 4:
            bucket["ap"] += 1

    for version, _family in lib.values():                 # 分母：按版本铺满（没打过的版本也要出现）
        versions.setdefault(version, {"sheets": 0, "played": 0, "hundred": 0, "ap": 0})["sheets"] += 1

    version_rows = []
    for version, bucket in versions.items():
        total = bucket["sheets"]
        version_rows.append({
            "version": version,
            "sheets": total,
            "played": bucket["played"],
            "coverage": pct(bucket["played"], total),
            "hundred": bucket["hundred"],
            "ap": bucket["ap"],
        })
    version_rows.sort(key=lambda r: (-r["sheets"], r["version"]))

    total_sheets = sum(family_total.values())
    stats = {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": now_stamp(),
        "kind": "scores_stats",
        "user": {"id": user.get("id"), "name": user.get("name")},
        "dxRating": None,                          # 占位：之后写 rating 生成器再填
        "updatedAt": scores_doc.get("generatedAt"),  # 最近一次更新数据的时间
        "totals": {
            "sheetsInLibrary": total_sheets,
            "played": len(played_keys),             # 游玩谱面数（只算曲库里有的）
            "coverage": pct(len(played_keys), total_sheets),
            "hundred": hundred,
            "hundredPct": pct(hundred, len(played_keys)),
            "fc": combo_hist["FC"],
            "fcPlus": combo_hist["FC+"],
            "ap": combo_hist["AP"],
            "apPlus": combo_hist["AP+"],
            "fdx": fdx,
            "fdxPlus": fdx_plus,
            "fullCombo": combo_hist["FC"] + combo_hist["FC+"],
            "allPerfect": combo_hist["AP"] + combo_hist["AP+"],
            "fcUp": fc_up,
            "apPlus": ap_plus,
            "avgAchievement": round(achievement_sum / 10000 / len(rows), 4) if rows else 0.0,
            "notInLibrary": not_in_library,
        },
        "rank": rank_hist,
        "dxscore": dx_hist,
        "dxscoreUnknown": dx_unknown,
        "combo": combo_hist,
        "families": {f: {"sheets": family_total[f], "played": family_played[f],
                         "coverage": pct(family_played[f], family_total[f])} for f in FAMILIES},
        "versions": version_rows,
    }

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        tmp.write_bytes((json.dumps(stats, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        tmp.replace(out_path)
    except OSError as exc:
        print(f"[错误] 写统计失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    t = stats["totals"]
    print(f"用户 : {user.get('name')}   谱 {t['played']:,} / {t['sheetsInLibrary']:,}"
          f"（覆盖 {t['coverage']}%）  100%+ {t['hundred']:,}  FC+ {t['fcUp']:,}  AP+ {t['apPlus']:,}")
    print("分族 : " + "  ".join(f"{f} {v['played']:,}/{v['sheets']:,}" for f, v in stats["families"].items()))
    print(f"完成 : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
