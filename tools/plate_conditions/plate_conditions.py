# 用途：把「牌子映射表」+ 曲库，展开成牌子条件库（哪些牌子、要打哪些谱面、贴图叫什么）
# 状态：现行
"""把牌子的条件展开成一份可以直接查的静态库。

【只管展开，不管遍历】
  牌子 → 范围组/除外组/贴图 的**事实**由 `data/plate_inventory/plate_map.json` 提供 ——
  它是 `tools/plate_inventory/plate_inventory.py` 从本地游戏包生成、人工可核可改的一张显式表：
      {"plateId","plateNum","name","tier","kind","versionLabel","condition","difficultyRange",
       "normText","genre","releaseTagName","rangeGroups":[…],"excludedGroups":[…],
       "internalIds":[…],"nameplate":"ui_plate_006113.ab"}
  本脚本**不遍历** plate/ musicGroup/ 目录，也不按名字猜范围：只读这张表（缺了/格式不对直接报错）。

【展开成谱面】按用户口径：谱面号 = 库里的 `chartid`（0=BASIC 1=ADVANCED 2=EXPERT
  3=MASTER 4=Re:MASTER 10=宴谱），只取牌子难度范围（`BASIC～MASTER` → 0–3，
  `BASIC～Re:MASTER` → 0–4）内的谱面。表里的 internalId 在曲库里对不上就记进 `missing[]`，**不猜**。

【版本字段】`version` 要与**曲库那套 `version` 值**对得上（用户要求）：用牌子条件文本首段
  （去掉「までの全曲」/「全曲」）去匹配库里出现过的 version 值 —— 牌子写 `maimai ORANGE` /
  `maimai でらっくす Splash`，库里是 `ORANGE` / `Splash`；库里把 `でらっくす` 写作 `DX`
  （只有 `DX` / `DX PLUS` 这两个改了名，其余 DX 时代名照旧），所以牌子里的 `でらっくす`
  先换成 `DX` 再匹配。每步要求**唯一命中**，猜不到就原样写牌子的话。
  5 个舞牌条件首段就是「全曲」→ 按用户口径写 `maimai～FiNALE`（舞系列全曲 = maimai 到 FiNALE）。

【范围】108 个牌子 = 26 版本 × 四档（实际 103：`maimai PLUS までの全曲` 那代没有「将」）+ 5 个舞牌。

【贴图】表里给了 `ui_plate_<6位>.ab`。本脚本只列清单，解包交给 tools/unpack_ab/：
      python tools/unpack_ab/unpack_ab.py "<包>/A000/AssetBundleImages/nameplate" \\
          --only data/plate_conditions/nameplate-files.txt --obj-name-only -o data/image/nameplate
  （素材仅本地用，版权属 SEGA，不进仓库、不外发。）

【输出】data/plate_conditions/
  plates.json           每个牌子：plateId/plateNum/name/tier/version/condition/难度范围/
                        internalIds/sheets[]（internalId + chartid + difficulty + family）/
                        chartCount/贴图（文件名 + 是否存在）
  nameplate-files.txt   要解包的 .ab 文件名（一行一个）

【用法】
    python tools/plate_conditions/plate_conditions.py [-i <游戏包目录>] [选项]
      --plate-map PATH     牌子映射表（默认本脚本同目录的 plate_map.json）
      -i, --game-package   可选：给了就核对贴图 .ab 在不在（也可用环境变量 MAIMAI_STREAMING_ASSETS）
      --fusion-dir PATH    展开谱面用的曲库目录（默认 data/fusion/）
      -o, --outdir PATH    输出目录（默认 data/plate_conditions/）
    退出码：0 成功；2 映射表/曲库读不出，或写盘失败。不联网；不做校验/判定。

依赖：仅 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"
PKG_ENV = "MAIMAI_STREAMING_ASSETS"

sys.dont_write_bytecode = True

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    import env
except Exception:
    env = None

SCRIPT_DIR_NAME = "plate_conditions"
INVENTORY_SCRIPT_DIR = "plate_inventory"                 # 映射表由 plate_inventory 产出（文件 = 接口）
MAP_NAME = "plate_map.json"
FUSION_SCRIPT_DIR = "fusion"
OUT_NAME = "plates.json"
LIST_NAME = "nameplate-files.txt"

FAMILY_ORDER = ("dx", "std", "utage", "utage2p")
FUSION_FILE = "fusion-{family}.json"
DIFF_RANGE = {"BASIC～MASTER": [0, 1, 2, 3], "BASIC～Re:MASTER": [0, 1, 2, 3, 4]}
CHARTID_NAME = {0: "basic", 1: "advanced", 2: "expert", 3: "master", 4: "remaster", 10: "utage"}

TZ_CN = timezone(timedelta(hours=8))
RULE = "─" * 68
BAR = "=" * 68


def data_root(script_dir_name: str) -> Path:
    if env is not None and callable(getattr(env, "data_dir_for", None)):
        try:
            return Path(env.data_dir_for(script_dir_name))
        except Exception:
            pass
    return PROJECT_DIR / "data" / script_dir_name


def resolve_package(raw: str) -> Path | None:
    base = Path(raw).expanduser().resolve()
    if not base.is_dir():
        return None
    for cand in (base, base / "StreamingAssets", base / "Sinmai_Data" / "StreamingAssets"):
        if (cand / "A000").is_dir():
            return cand
    return None


# 曲库里两条写错的 version（上游/本地包原样带过来的），收集「已知版本」时先归一，
# 否则 `CiRCLE PLUS` 会同时命中 `CiRCLE PLUS` 和 `CiRCLEPLUS`，唯一性判定失败、牌子对不上版本。
from lib.versions import VERSION_FIX          # 统一配置：lib/versions.py


def load_fusion(fusion_dir: Path) -> tuple[dict, int, set]:
    """曲库按 internalId 索引；顺带把库里出现过的 version 值收集起来（牌子要对上它）。"""
    by_id, songs, versions = {}, 0, set()
    for family in FAMILY_ORDER:
        path = fusion_dir / FUSION_FILE.format(family=family)
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        for song in doc.get("songs") or []:
            internal_id = song.get("internalId")
            if internal_id is None:
                continue
            songs += 1
            if song.get("version"):
                versions.add(VERSION_FIX.get(song["version"], song["version"]))
            by_id[internal_id] = [(s.get("chartid"), s.get("difficulty"), family)
                                  for s in song.get("sheets") or []]
    return by_id, songs, versions


def _norm_version(text: str) -> str:
    return text.lower().replace(" ", "").replace("　", "")


def _unique(hits: list) -> str | None:
    return hits[0] if len(hits) == 1 else None


def match_version(label: str, known: set) -> str | None:
    """把牌子写的版本名对到**曲库里那套 version 值**上（对不上返回 None）。

    牌子和库里的写法不一致，按「最具体 → 最宽松」依次试：
      1. 原样 / 只去空格：`maimai PLUS` = `maimai PLUS`
      2. 去掉 `maimai`：牌子 `maimai ORANGE` → 库里 `ORANGE`
      3. 去掉 `maimai` / `でらっくす`(=DX) 再比尾巴，**并且优先同「带不带 DX」的候选**：
         牌子 `maimai でらっくす Splash` → 库里 `Splash`；
         牌子 `maimai でらっくす PLUS` → 库里 `DX PLUS`（不能落到 `maimai PLUS`）
    每一步都要求**唯一命中**，否则不猜（返回 None，外面原样写牌子的话）。
    """
    if not label:
        return None
    label = label.replace("でらっくす", "DX")             # 用户口径：库里写 DX
    if label in known:
        return label
    # 牌子「maimai でらっくす CiRCLE PLUS」→ 库里只写 "CiRCLE PLUS"：去掉 maimai/DX 前缀后**精确命中**才算
    for prefix in ("maimai DX ", "DX ", "maimai "):
        if label.startswith(prefix):
            tail = label[len(prefix):].strip()
            if tail and tail in known:
                return tail
    key = _norm_version(label)
    hit = _unique([v for v in known if _norm_version(v) == key])
    if hit:
        return hit
    hit = _unique([v for v in known if _norm_version(v) == key.replace("maimai", "")])
    if hit:
        return hit
    def _tail(text: str) -> str:
        return _norm_version(text).replace("maimai", "").replace("dx", "")
    tail = _tail(label)
    want_dx = "dx" in _norm_version(label)
    same_dx = [v for v in known if ("dx" in _norm_version(v)) == want_dx]
    for pool in (same_dx, list(known)):
        hit = _unique([v for v in pool if _tail(v) == tail])
        if hit:
            return hit
    return None


def version_of(row: dict, known: set) -> str:
    """这个牌子写哪个版本：条件文本首段（去掉「までの全曲」/「全曲」）→ 对到曲库的 version 值。

    ⚠ 5 个舞牌条件首段就是「全曲」—— 按用户口径「舞系列全曲 = maimai 到 FiNALE」写 `maimai～FiNALE`
      （实测舞组 572 首的 version 分布正是 maimai…FiNALE 那 12 个，无一首 DX 时代）。
    ⚠ 不能拿「组内歌的 version 众数」当版本 —— 实测 64/108 个牌子组内版本不统一
      （同一首曲两族在不同版本加入，也有老曲后来才进这个组）。
    """
    label = (row.get("versionLabel") or "").strip()
    for suffix in ("までの全曲", "全曲"):
        if label.endswith(suffix):
            label = label[: -len(suffix)].strip()
            break
    if not label:
        return "maimai～FiNALE"
    return match_version(label, known) or label


def expand(plate_map: dict, fusion_by_id: dict, known_versions: set,
           nameplate_dir: Path | None) -> dict:
    plates, missing = [], []
    version_unmatched: list[dict] = []
    tier_stats: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    for row in plate_map["plates"]:
        allowed = DIFF_RANGE.get(row.get("difficultyRange"), [0, 1, 2, 3])
        sheets, absent, chart_total = [], 0, 0
        for internal_id in row.get("internalIds") or []:
            charts = fusion_by_id.get(internal_id)
            if not charts:
                absent += 1
                missing.append({"plateId": row["plateId"], "internalId": internal_id,
                                "why": "曲库里没有这个 internalId"})
                continue
            chartids = sorted(c for c, _difficulty, _family in charts if c in allowed)
            sheets.append({"internalId": internal_id, "chartid": chartids})
            chart_total += len(chartids)
        ab = row.get("nameplate") or ""
        tier = row.get("tier") or row.get("name")
        version = version_of(row, known_versions)
        if version not in known_versions and version != "maimai～FiNALE":
            version_unmatched.append({"plateId": row["plateId"], "version": version,
                                      "versionLabel": row.get("versionLabel"),
                                      "why": "曲库里没有这个 version 值"})
        tier_stats[tier] = tier_stats.get(tier, 0) + 1
        kind_counts[row.get("kind") or "version"] = kind_counts.get(row.get("kind") or "version", 0) + 1
        plates.append({
            "plateId": row["plateId"], "plateNum": row.get("plateNum"), "name": row.get("name"),
            "condition": row.get("condition"), "version": version,
            "normText": row.get("normText"),
            "sheets": sheets, "chartCount": chart_total,
            "nameplate": ab.replace(".ab", ".png").replace("ui_plate_", "UI_Plate_") if ab else "",
        })
    plates.sort(key=lambda p: (p["plateNum"] or 0))
    found = (sum(1 for row in plate_map["plates"] if (nameplate_dir / row["nameplate"]).is_file())
             if nameplate_dir else None)
    counts = {
        "plates": len(plates),
        "versionPlates": kind_counts.get("version", 0),
        "dancePlates": kind_counts.get("dance", 0),
        "versions": len({p["version"] for p in plates if p["version"] != "maimai～FiNALE"}),
        "tiers": tier_stats,
        "sheets": sum(p["chartCount"] for p in plates),
        "internalIds": sum(len(p["sheets"]) for p in plates),
        "nameplateFound": found,
        "versionUnmatched": len(version_unmatched),
        "missing": len(missing),
    }
    return {"plates": plates, "missing": missing,
            "versionUnmatched": version_unmatched, "counts": counts}


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="plate_conditions.py",
        description="把牌子映射表 + 曲库展开成牌子条件库（108 个牌子）",
    )
    ap.add_argument("--plate-map", metavar="PATH",
                    help=f"牌子映射表（默认 data/{INVENTORY_SCRIPT_DIR}/{MAP_NAME}，由 tools/plate_inventory 生成）")
    ap.add_argument("-i", "--game-package", metavar="PATH",
                    help=f"可选：给了就核对贴图 .ab 在不在（也可用环境变量 {PKG_ENV}）")
    ap.add_argument("--fusion-dir", metavar="PATH", help=f"曲库目录（默认 data/{FUSION_SCRIPT_DIR}/）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help=f"输出目录（默认 data/{SCRIPT_DIR_NAME}/）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    map_path = (Path(args.plate_map).resolve() if args.plate_map
                else data_root(INVENTORY_SCRIPT_DIR) / MAP_NAME)
    raw = args.game_package or os.environ.get(PKG_ENV)
    package = resolve_package(raw) if raw else None
    if raw and package is None:
        print(f"[错误] 这个目录里没有 A000/：{raw}", file=sys.stderr)
        return 2
    nameplate_dir = package / "A000" / "AssetBundleImages" / "nameplate" if package else None
    fusion_dir = (Path(args.fusion_dir).resolve() if args.fusion_dir
                  else data_root(FUSION_SCRIPT_DIR))
    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)

    print(BAR)
    print("plate_conditions · 牌子条件库（读映射表展开）")
    print(f"  映射表   : {map_path}")
    print(f"  贴图目录 : {nameplate_dir or '（没给游戏包，跳过贴图核对）'}")
    print(f"  曲库     : {fusion_dir}")
    print(f"  输出     : {outdir}/{OUT_NAME}")
    print(RULE)

    try:
        plate_map = json.loads(map_path.read_text(encoding="utf-8"))
        fusion_by_id, fusion_songs, known_versions = load_fusion(fusion_dir)
    except (OSError, ValueError) as exc:
        print(f"[错误] 读输入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    built = expand(plate_map, fusion_by_id, known_versions, nameplate_dir)
    c = built["counts"]
    print(f"牌子 : {c['plates']} 个（版本牌 {c['versionPlates']} · 舞牌 {c['dancePlates']}，"
          f"覆盖 {c['versions']} 个版本）  档位 {c['tiers']}")
    print(f"曲目 : internalId {c['internalIds']:,} 个 → 谱面 {c['sheets']:,} 张"
          f"（曲库 {fusion_songs:,} 首，对不上的 {c['missing']} 处）")
    print(f"贴图 : " + (f"{c['nameplateFound']}/{c['plates']} 个牌子找到了 ui_plate_*.ab"
                       if c["nameplateFound"] is not None else "没给游戏包，未核对"))
    hard = [x for x in built["versionUnmatched"] if x["version"] != "maimai～FiNALE"]
    print(f"版本 : 曲库 {len(known_versions)} 种 version；26 个版本牌全部对上"
          + (f"；{sum(1 for p in built['plates'] if p['version'] == 'maimai～FiNALE')} 个舞牌按口径写"
             f"「maimai～FiNALE」" if any(p["version"] == "maimai～FiNALE" for p in built["plates"]) else "")
          if not hard else
          f"版本 : ★ {len(hard)} 个牌子的 version 在曲库里找不到（见 versionUnmatched[]）")

    print(RULE)
    print("抽查")
    samples = built["plates"][:2] + [p for p in built["plates"] if p["name"] == "覇者"][:1]
    for plate in samples:
        print(f"  {plate['plateId']} {plate['name']:<8} {plate['condition']:<13} "
              f"{plate['version']:<18} 曲目 {len(plate['sheets']):<4} 谱面 {plate['chartCount']:<5} "
              f"{plate['nameplate']}")
        for sheet in plate["sheets"][:3]:
            print(f"      internalId={sheet['internalId']:<7} chartid={sheet['chartid']}")

    payload = {
        "schema": 1, "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "kind": "plates",
        "sources": {"plateMap": f"data/{INVENTORY_SCRIPT_DIR}/{MAP_NAME}",
                    "nameplateDir": "A000/AssetBundleImages/nameplate" if package else None,
                    "fusionDir": f"data/{FUSION_SCRIPT_DIR}"},
        "counts": c, "chartidNames": {str(k): v for k, v in CHARTID_NAME.items()},
        "plates": built["plates"], "missing": built["missing"],
        "versionUnmatched": built["versionUnmatched"],
    }
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        tmp = outdir / (OUT_NAME + ".part")
        try:
            tmp.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            tmp.replace(outdir / OUT_NAME)
        finally:
            tmp.unlink(missing_ok=True)
        listing = [row["nameplate"] for row in plate_map["plates"] if row.get("nameplate")]
        list_tmp = outdir / (LIST_NAME + ".part")
        try:
            list_tmp.write_text("\n".join(listing) + "\n", encoding="utf-8")
            list_tmp.replace(outdir / LIST_NAME)
        finally:
            list_tmp.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[错误] 写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(RULE)
    print(f"完成 : {outdir / OUT_NAME}")
    print(f"       {outdir / LIST_NAME}（{len(listing)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
