# 用途：用本地曲库给 dxrating 缺 id 的曲目补上本地号，按谱面族分别产出 Proofread-<族>.json
# 状态：现行
"""把 dxrating 曲库整理成完整可用的 Proofread 曲库（缺号用本地游戏数据补上），**按谱面族分开**。

【它做三件事】
  1. 读 dxrating_build 的产出（**按谱面族四份**：dx / std / utage / utage2p）当底子 ——
     这些记录带齐全字段（noteCounts / serverIds / level / releaseDate / 历史定数…）。
  2. 上游把 std 与 dx 谱**放在同一条歌曲记录里**，所以歌曲级元数据（曲名 / 艺术家）
     要跨族取一次；产出时**按族写四份文件，一份一族，绝不混装**。
  3. 缺内部号的曲目，去 localMusicData_build 的本地曲库里按 **title + category + version** 找到本地曲，
     把 (internalId, type, dataid) 填进歌曲级。

【配对判据】
  候选池 = 本地曲库里 (title, category) 相同的记录（本地有 85 组同名，只靠标题不行）。
  普通谱：title + category + version —— 本地 version（经 version_map.json 归一化）落在上游
          该曲的谱面 version 集合里，且只剩一个候选；**不用定数**（定数会被补丁调整）。
  宴谱：谱面族汉字（【協】【宴】…）+ 定数；同名多条上游记录时逐条分配，一条本地曲只配一次。
  收敛不到唯一就 needsReview + 不填号（宁缺勿错），条目照常留在产出里。

【输出结构（照用户 2026-10-16 的单数据组-fusion 样本）】
  歌曲级：internalId type dataid category title artist bpm version isNew isLocked
          sheets
  谱面级：chartid difficulty level dxratingLevelValue localLevelValue noteDesigner
          noteCounts serverIds isSpecial version releaseDate historyLevelValue?
  另：**上游的 serverOverrides 不要了**（用户 2026-10-16 明确删除）。
  `PatternsTag` / `DifficultyTag` / `EvaluationTag` 不在本步骤 —— 那是 **fusion 才加的谱面级字段**
  （数据来自 tag_catalog 抓的社区标签）。
  曲别名也不在本步骤：上游的 `searchAcronyms` 与顶层 `aliases` 一律不带，由
  tools/aliasesTag_catalog/ 融成 `aliasesTag`，同样是 **fusion 才加的歌曲级字段**（用户 2026-10-17 拍板）。

【用法】
    python tools/Proofread-build/proofread_build.py [选项]
      --dxrating-dir PATH   dxrating 产物目录（默认 data/dxrating_build/）
      --local-dir PATH      本地曲库目录（默认 data/localMusicData_build/）
      -o, --outdir PATH     输出目录（默认 data/Proofread-build/）
    退出码：0 成功；2 输入缺失/读不出/写盘失败。


依赖：仅 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"  # 批次号：run_chain 下发（手工跑记 manual），写进产物头部

sys.dont_write_bytecode = True

PROJECT_DIR = Path(__file__).resolve().parents[2]          # …/maimai-planner
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from lib.versions import canonical_version   # noqa: E402

try:
    import env
except Exception:
    env = None

SCRIPT_DIR_NAME = "Proofread-build"
SCRIPT_DIR = Path(__file__).resolve().parent
DXRATING_SCRIPT_DIR = "dxrating_build"
LOCAL_SCRIPT_DIR = "localMusicData_build"

VERSION_MAP_NAME = "version_map.json"                      # 版本名归一化（与本脚本同目录）
LOCAL_CONFIG_DIR = PROJECT_DIR / "tools" / "localMusicData_build"   # 版本顺序表在那边

# 谱面族：一份输入一族、一份产出一族，绝不混装
FAMILY_ORDER = ("dx", "std", "utage", "utage2p")
DXRATING_FILE = {"dx": "dxrating-dx.json", "std": "dxrating-std.json",
                 "utage": "dxrating-utage.json", "utage2p": "dxrating-utage2p.json"}
LOCAL_FILE = {"dx": "localMusicData-dx.json", "std": "localMusicData-std.json",
              "utage": "localMusicData-utage.json", "utage2p": "localMusicData-utage2p.json"}
OUT_FILE = {"dx": "Proofread-dx.json", "std": "Proofread-std.json",
            "utage": "Proofread-utage.json", "utage2p": "Proofread-utage2p.json"}
LEGACY_FILES = ("Proofread-normal.json",)                  # 上一版混装名，写新文件时清掉
MISSING_NAME = "dxrating-missing-id.json"
TZ_CN = timezone(timedelta(hours=8))

RULE = "-" * 70
BAR = "=" * 70


def data_root(script_name: str) -> Path:
    if env is not None and callable(getattr(env, "data_dir_for", None)):
        try:
            return Path(env.data_dir_for(script_name))
        except Exception:
            pass
    if env is not None and getattr(env, "DATA_DIR", None):
        return Path(env.DATA_DIR) / script_name
    return PROJECT_DIR / "data" / script_name


def read_json(path: Path, what: str):
    if not path.is_file():
        raise FileNotFoundError(f"{what}不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_canonical_map(path: Path) -> dict[str, str]:
    """产物里 version 字段的统一写法（version_map.json 的 canonical 段）。

    上游写 `maimaiでらっくす` / `maimaiでらっくす PLUS`，用户口径统一成 `DX` / `DX PLUS`；
    其余 DX 时代版本名照旧。这里只改**写法**，配对逻辑仍用上面的 map 段。
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in (doc.get("canonical") or {}).items()}


def load_version_map(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    table = doc.get("map") or {}
    return {re.sub(r"\s+", "", str(k)): str(v) for k, v in table.items()}


def load_version_order(path: Path) -> dict[str, int]:
    """版本名 → 顺序号（historyLevelValue 按这个排）。表不在就按名字排。"""
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in (doc.get("versionOrder") or {}).items()}


def canon_version(text, table: dict[str, str]) -> str:
    """本地形式 → 上游写法；已经是上游写法就原样返回（单向归一）。"""
    text = str(text or "")
    if text in set(table.values()):
        return text
    return table.get(re.sub(r"\s+", "", text), re.sub(r"\s+", "", text))


def kanji(text) -> str:
    matched = re.search(r"【(.+?)】", str(text or ""))
    return matched.group(1) if matched else ""


def load_by_family(directory: Path, names: dict[str, str]) -> dict[str, list[dict]]:
    """按谱面族读四份文件，返回 {族: 记录列表}。"""
    out: dict[str, list[dict]] = {f: [] for f in FAMILY_ORDER}
    for family in FAMILY_ORDER:
        path = directory / names[family]
        if path.is_file():
            out[family] = [r for r in (read_json(path, names[family]).get("songs") or [])
                           if isinstance(r, dict)]
    return out


def local_kanji(song: dict) -> set[str]:
    return {kanji(sh.get("difficulty")) for sh in song.get("sheets") or []}


def local_levels(song: dict) -> list[float]:
    return sorted(round(sh.get("internalLevelValue") or 0, 1) for sh in song.get("sheets") or [])


def gap_to(song: dict, target: float) -> float:
    return min((abs(lv - target) for lv in local_levels(song)), default=float("inf"))


def match_local(up_song: dict, candidates: list[dict], table: dict[str, str],
                used: set) -> tuple[dict | None, dict]:
    """给一条 dxrating 曲找对应的本地曲。返回 (本地曲 | None, match 说明)。"""
    free = [c for c in candidates if c.get("internalId") not in used]
    base = {"candidates": len(candidates), "needsReview": False}
    if not candidates:
        return None, dict(base, needsReview=True, reason="本地没有同名同分类的曲")
    if not free:
        return None, dict(base, needsReview=True, reason="同名的本地曲已被其他同名上游记录配走")

    up_versions = {canon_version(sh.get("version"), table) for sh in up_song.get("sheets") or []}
    up_k = {kanji(sh.get("difficulty")) for sh in up_song.get("sheets") or []} - {""}
    up_levels = sorted({round(sh.get("internalLevelValue") or 0, 1)
                        for sh in up_song.get("sheets") or []})
    hits = [c for c in free if canon_version(c.get("version"), table) in up_versions]

    if up_k:                                               # 宴谱：汉字 + 定数
        k_hits = [c for c in (hits or free) if local_kanji(c) & up_k]
        if len(k_hits) == 1:
            return k_hits[0], dict(base, method="utage_kanji", kanji=sorted(up_k))
        pool = k_hits or hits or free
        for target in up_levels:
            live = [c for c in pool if c.get("internalId") not in used]
            if not live:
                break
            best = min(live, key=lambda c: gap_to(c, target))
            gap = gap_to(best, target)
            if gap <= 1e-9:
                return best, dict(base, method="utage_kanji_levelvalue", kanji=sorted(up_k),
                                  localLevels=local_levels(best), matchedTarget=target)
            close = [c for c in live if gap_to(c, target) <= 1e-9 + 0.1]
            if len(close) == 1:
                return best, dict(base, method="utage_kanji_levelvalue_tolerant",
                                  kanji=sorted(up_k), localLevels=local_levels(best),
                                  matchedTarget=target, tolerance=0.1)
        return None, dict(base, needsReview=True, reason="宴谱汉字/定数收敛不到唯一候选",
                          candidateIds=sorted(c.get("internalId") for c in pool)[:8])

    if len(hits) == 1:                                     # 普通谱：title + category + version
        return hits[0], dict(base, method="title_category_version",
                             localVersion=hits[0].get("version"),
                             upstreamVersions=sorted(up_versions))
    pool = hits or free
    return None, dict(base, needsReview=True, reason="version 收敛不到唯一候选",
                      candidateIds=sorted(c.get("internalId") for c in pool)[:8],
                      upstreamVersions=sorted(up_versions))


def convert_sheets(up_sheets: list[dict], local_song: dict | None,
                   version_order: dict[str, int]) -> list[dict]:
    """上游谱面 → 产出谱面；本地有的按 chartid / difficulty 填本地号。"""
    local_by_chart = {}
    local_by_difficulty = {}
    for sh in (local_song or {}).get("sheets") or []:
        if sh.get("chartid") is not None:
            local_by_chart[sh["chartid"]] = sh
        local_by_difficulty[sh.get("difficulty")] = sh

    sheets = []
    for sh in up_sheets:
        chartid = sh.get("chartid")
        if chartid is None:
            chartid = sh.get("difficulty")            # 宴谱槽位就是难度名
        local_sheet = local_by_chart.get(sh.get("chartid")) or local_by_difficulty.get(sh.get("difficulty"))
        row = {
            "chartid": chartid,
            "difficulty": sh.get("difficulty"),
            "level": sh.get("level"),
            "dxratingLevelValue": sh.get("internalLevelValue"),
            "localLevelValue": (local_sheet or {}).get("internalLevelValue"),
            "noteDesigner": sh.get("noteDesigner"),
            "noteCounts": sh.get("noteCounts") or {},
            "serverIds": sh.get("serverIds") or [],
            "isSpecial": sh.get("isSpecial"),
            "version": canonical_version(sh.get("version")),
            "releaseDate": sh.get("releaseDate"),
        }
        if sh.get("comment") is not None:              # 宴谱吐槽
            row["comment"] = sh.get("comment")
        mv = sh.get("multiverInternalLevelValue") or sh.get("historyLevelValue")
        if mv:
            items = sorted(({"version": v, "value": val} for v, val in mv.items()),
                           key=lambda it: version_order.get(it["version"], len(version_order)))
            row["historyLevelValue"] = items
        sheets.append(row)
    return sheets


def build_doc(records: list, family: str, stamped: str, sources: dict) -> dict:
    return {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": stamped,
        "kind": family,
        "sources": sources,
        "counts": {
            "songs": len(records),
            "sheets": sum(len(r["sheets"]) for r in records),
            "withLocalId": sum(1 for r in records if r.get("internalId") is not None),
            "needsReview": sum(1 for r in records if r.get("match", {}).get("needsReview")),
        },
        "songs": records,
    }


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="proofread_build.py",
        description="用本地曲库给 dxrating 缺 id 的曲目补号，按谱面族产出 Proofread-<族>.json",
    )
    ap.add_argument("--dxrating-dir", metavar="PATH", help="dxrating 产物目录（默认 data/dxrating_build/）")
    ap.add_argument("--local-dir", metavar="PATH", help="本地曲库目录（默认 data/localMusicData_build/）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="输出目录（默认 data/Proofread-build/）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    dxrating_dir = Path(args.dxrating_dir).resolve() if args.dxrating_dir else data_root(DXRATING_SCRIPT_DIR)
    local_dir = Path(args.local_dir).resolve() if args.local_dir else data_root(LOCAL_SCRIPT_DIR)
    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)

    print(BAR)
    print("Proofread · 用本地给 dxrating 缺号补 id（按谱面族分开）")
    for family in FAMILY_ORDER:
        print(f"  {family:<8} 上游 {DXRATING_FILE[family]:<24} → {OUT_FILE[family]}")
    print(RULE)

    try:
        table = load_version_map(SCRIPT_DIR / VERSION_MAP_NAME)
        canonical = load_canonical_map(SCRIPT_DIR / VERSION_MAP_NAME)
        version_order = load_version_order(LOCAL_CONFIG_DIR / "version_ids.json")
        upstream_by_family = load_by_family(dxrating_dir, DXRATING_FILE)
        local_by_family = load_by_family(local_dir, LOCAL_FILE)
    except (OSError, ValueError) as exc:
        print(f"[错误] 读输入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    local = [r for f in FAMILY_ORDER for r in local_by_family[f]]
    pool: dict[tuple, list[dict]] = {}
    for song in local:
        pool.setdefault((song.get("title"), song.get("category")), []).append(song)

    stamped = datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")
    sources = {"dxrating": [f"data/{DXRATING_SCRIPT_DIR}/{DXRATING_FILE[f]}" for f in FAMILY_ORDER],
               "local": [f"data/{LOCAL_SCRIPT_DIR}/{LOCAL_FILE[f]}" for f in FAMILY_ORDER],
               "versionMap": VERSION_MAP_NAME}

    # 上游已按谱面族拆好，一族一条记录 —— 逐条处理，不再按名字合并
    #（同名多版本是合法的：[宴]Wonderland Wars オープニング 本地 5 条，名字完全相同）
    used: set = set()
    out_by_family: dict[str, list] = {f: [] for f in FAMILY_ORDER}
    matched = 0
    for family in FAMILY_ORDER:
        for src in upstream_by_family[family]:
            sheets = src.get("sheets") or []
            if src.get("internalId") is not None:
                local_song = None                # 号已由上游给出，本地只在 fusion 里接
                local_id, local_dataid = src["internalId"], src.get("dataid")
                match = None
            else:
                candidates = pool.get((src.get("title"), src.get("category")), [])
                local_song, match = match_local(src, candidates, table, used)
                if local_song is not None:
                    used.add(local_song["internalId"])
                    matched += 1
                    local_id, local_dataid = local_song["internalId"], local_song.get("dataid")
                else:
                    local_id, local_dataid = None, src.get("dataid")

            record = {
                "internalId": local_id,
                "type": family,
                "dataid": local_dataid,
                "category": src.get("category"),
                "title": src.get("title"),
                "artist": src.get("artist"),
                "bpm": src.get("bpm"),
                "version": canonical.get(src.get("version"), src.get("version")),
                "isNew": src.get("isNew"),
                "isLocked": src.get("isLocked"),
                "sheets": convert_sheets(sheets, local_song, version_order),
            }
            if match is not None:
                record["match"] = match
            out_by_family[family].append(record)

    for family in FAMILY_ORDER:
        out_by_family[family].sort(key=lambda r: (r["internalId"] is None, r["internalId"] or 0))

    try:
        outdir.mkdir(parents=True, exist_ok=True)
        for family in FAMILY_ORDER:
            name = OUT_FILE[family]
            payload = json.dumps(build_doc(out_by_family[family], family, stamped, sources),
                                 ensure_ascii=False, indent=2) + "\n"
            tmp = outdir / (name + ".part")
            try:
                tmp.write_bytes(payload.encode("utf-8"))
                tmp.replace(outdir / name)
            finally:
                tmp.unlink(missing_ok=True)
        for legacy in LEGACY_FILES:                        # 上一版混装的旧文件清掉
            (outdir / legacy).unlink(missing_ok=True)
    except OSError as exc:
        print(f"[错误] 写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    up_n = sum(len(upstream_by_family[f]) for f in FAMILY_ORDER)
    print(f"输入 : 上游 {up_n:,} 首   本地 {len(local):,} 首   版本映射 {len(table)} 组"
          f"   统一写法 {len(canonical)} 条")
    print(RULE)
    for family in FAMILY_ORDER:
        recs = out_by_family[family]
        missing = sum(1 for r in recs if r["internalId"] is None)
        print(f"  {OUT_FILE[family]:<26} 曲 {len(recs):,}  谱面 {sum(len(r['sheets']) for r in recs):,}"
              + (f"   号仍为空 {missing}" if missing else ""))
    print(f"  合计补号 {matched} 首")
    print(RULE)
    print("抽查")
    for family in FAMILY_ORDER:
        if out_by_family[family]:
            r = out_by_family[family][0]
            print(f"  {family:<8} internalId={r['internalId']} dataid={r['dataid']} {str(r['title'])[:24]}")
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
