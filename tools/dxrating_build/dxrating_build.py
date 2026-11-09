# 用途：把上游 dxdata.json 按谱面族整成 dx/std/utage/utage2p 四份曲库——舞萌「打歌计划器」
# 状态：现行
"""把 DXRating 上游原文（data/catalog_dxdata/dxdata.json）整成**按谱面族分开**的曲库。

【规则（用户 2026-10-16 拍板）】
  1. **按谱面族分四份，一份一族，绝不混装**：
        data/dxrating_build/dxrating-dx.json        type = dx
        data/dxrating_build/dxrating-std.json       type = std
        data/dxrating_build/dxrating-utage.json     type = utage
        data/dxrating_build/dxrating-utage2p.json   type = utage2p
     ⚠ 上游把 std 与 dx 谱**放在同一条歌曲记录里**（同名难度出现两次），所以必须按族拆开。
      记录里只留本族谱面；`type` 在歌曲级，谱面子项不带 type。
     另出一份缺号清单（上游没给 internalId、算不出曲绘号的）：
        data/dxrating_build/dxrating-missing-id.json
        标准 JSON 数组，每条只记 title / category / artist / version，**不合并**，
        等本地游戏数据出来再对号填。
  2. 四份库只保留 updatedAt 与 songs —— 上游头部那些定义表（categories / versions /
     types / difficulties / servers / tagGroups / tags / tagSongs / aliases）与
     schemaVersion 一律不要（用户 2026-10-16 要求删掉）。
  3. 宴谱（type = utage / utage2p）在歌曲级多加一个字段 `total` —— 取自 noteCounts.total。
  4. `dataid` —— 用于在本地游戏包里找曲绘，**取 internalId 的后 4 位，去掉前导 0**。
        10181 -> "0181" -> 181      （dx 谱）
          360 -> "0360" -> 360       （std 谱）
       161852 -> "1852" -> 1852      （宴谱）
     不用「dx 减 10000」那种算法：本地实测有 dx 谱 internalId 本身就 < 10000
     （けーね 360、Burn My Soul 1639）会算出负数。
  5. 临时覆盖：`dxdata_overrides.json` 可在 build 前修正上游错误 ID；
     规则独立、可删除，并与本地 title+version 的 dataid 做校验。
  6. `internalId` 在歌曲级，按族算（与本地游戏完全一致）：
        std = dataid、dx = dataid + 10000、宴谱 = 上游原值。
     上游没给号的留 null（缺号清单里会列出来）。
  6. 谱面的 `id`（上游哈希串）删除，换成 `chartid` —— 只为读的时候快速判断难度：
        basic 0 / advanced 1 / expert 2 / master 3 / remaster 4
     都不是时看 `category`：宴会场记 10；既不是常规难度、category 又不是宴会场 → 记 null，
     **整条记录搬去 data/dxrating_build/dxrating-dataerror.json，库里不留**（没有就不生成该文件）。
  7. 产出里**只保留用户样本点到的键**，其余上游字段（id / imageName / dataQuality /
     谱面 internalId、细分宴谱难度号等）一律删掉。键序照样本。

【别名不在这里（用户 2026-10-17 拍板）】
  曲名写法（searchAcronyms）与玩家别名（aliases）本脚本**不管** —— 上游这两份别名都是歌曲级，
  由 tools/aliasesTag_catalog/ 融成一张 `aliasesTag` 表，等 fusion 时按内部号挂到歌曲级。

【已知脏数据（不猜，照规则算）】
  * 「Link」上游并成一条记录（internalId 131 + 383）。本地实测 music000131(maimaiPLUS) 与
    music000383(ORANGE) 是**两首不同的歌**，只是标题一样 —— 本脚本按 internalId 拆成两条。
  * 「物凄い勢いでけーねが物凄いうた」上游并成一条（dx 谱 internalId 360 + std 谱 360）。
    本地实测是两首（music000360 / music010360），但两者共用曲绘号 360，所以 dataid 仍正确。
  * 「Burn My Soul」上游 internalId 1639，本地是 music011639 —— 上游掉了首位 1，
    但后 4 位仍是 1639，曲绘号照样对。

【用法】
    python tools/dxrating_build/dxrating_build.py [选项]
      -i, --input PATH   上游原文（默认 data/catalog_dxdata/dxdata.json）
      -o, --outdir PATH  输出目录（默认 data/dxrating_build/）
    退出码：0 成功；2 输入缺失/读不出/写盘失败。



依赖：仅 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"  # 批次号：run_chain 下发（手工跑记 manual），写进产物头部

sys.dont_write_bytecode = True

PROJECT_DIR = Path(__file__).resolve().parents[2]          # …/maimai-planner
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    import env
except Exception:                                          # env.py 缺失也不致命
    env = None

SCRIPT_DIR_NAME = "dxrating_build"
SOURCE_SCRIPT_DIR = "catalog_dxdata"
SOURCE_NAME = "dxdata.json"
OVERRIDE_NAME = "dxdata_overrides.json"   # 上游 ID 临时修正表（可删）
OVERRIDE_PATH = Path(__file__).resolve().parent / OVERRIDE_NAME
# 谱面族：一份文件一族，绝不混装（上游把 std/dx 混在同一条记录里，所以产出必须按族拆开）
FAMILY_ORDER = ("dx", "std", "utage", "utage2p")
FAMILY_FILE = {"dx": "dxrating-dx.json", "std": "dxrating-std.json",
               "utage": "dxrating-utage.json", "utage2p": "dxrating-utage2p.json"}
LEGACY_FILES = ("dxrating-normal.json",)                   # 上一版混装名，写新文件时清掉
OUT_MISSING = "dxrating-missing-id.json"                   # 缺号的清单
OUT_DATAERROR = "dxrating-dataerror.json"                  # chartid 算不出的记录，整条搬来

UTAGE_TYPES = ("utage", "utage2p")
# 产出顶层只留这两样（上游头部定义表全删）
TOP_KEYS_KEEP = ("updatedAt",)

# ── 产出里保留的键与顺序（照用户给的样本，多的一律删）─────────────────
SONG_KEYS = ("internalId", "type", "dataid", "category", "title", "artist", "bpm",
             "version", "isNew", "isLocked", "sheets")
SHEET_KEYS = ("chartid", "difficulty", "level", "internalLevelValue", "noteDesigner",
              "noteCounts", "serverIds", "serverOverrides", "isSpecial", "version",
              "releaseDate", "multiverInternalLevelValue")

# chartid：常规谱按难度槽位 0~4；宴谱（宴会场）记 10
CHART_SLOT = {"basic": 0, "advanced": 1, "expert": 2, "master": 3, "remaster": 4}
UTAGE_SLOT = 10
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


def dataid_of(internal_id: int | None) -> int | None:
    """规则 2：取后 4 位、去掉前导 0。"""
    if not isinstance(internal_id, int) or isinstance(internal_id, bool):
        return None
    return int(str(internal_id)[-4:])


def song_internal_id(dataid: int | None, is_utage: bool, raw_ids: list[int],
                     kind: str | None = None) -> int | None:
    """歌曲级内部号（与本地曲库同一套算法，实测两边 628 例全部对得上）。

        std   → dataid 本身（本地 True Love Song std 是 8，不是 10008）
        dx    → dataid + 10000
        宴谱  → 上游原值

    ⚠ 曾经把 std 也写成 dataid + 10000（本地 8 写成 10008），导致 std / dx 两条记录
      撞同一个内部号，fusion 按号配对时全错位。**std 不加**。
    """
    if is_utage:
        return min(raw_ids) if raw_ids else None
    if dataid is None:
        return None
    return dataid + 10000 if kind == "dx" else dataid


def load_local_dataids(local_dir: Path) -> dict[tuple[str, str, str], int]:
    """本地曲库 (family,title,version) → dataid；给 dxdata 缺 internalId 的新曲兜底。"""
    out = {}
    for family in FAMILY_ORDER:
        path = local_dir / f"localMusicData-{family}.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for song in doc.get("songs") or []:
            dataid = song.get("dataid")
            if dataid is not None:
                out[(family, song.get("title"), song.get("version"))] = int(dataid)
    return out


def transform(song: dict, fallback_dataid: dict[str, int] | None = None) -> list[dict]:
    """一条上游歌曲记录 → 若干条产出记录，**每条只属于一个谱面族**。

    fallback_dataid：新曲在 dxdata 里可能没有 internalId，用本地曲库按
    title + version 找到的 dataid 兜底。
    """
    sheets = song.get("sheets") or []

    groups: dict[tuple[str, int | None], list[dict]] = {}
    for sh in sheets:
        stype = sh.get("type")
        if stype not in FAMILY_ORDER:
            continue
        iid = sh.get("internalId")
        dataid = dataid_of(iid) if isinstance(iid, int) else (fallback_dataid or {}).get(stype)
        key = (stype, dataid)
        groups.setdefault(key, []).append(sh)

    out = []
    for (stype, dataid) in sorted(groups, key=lambda k: (k[1] is None, k[1] or 0, k[0])):
        group = groups[(stype, dataid)]
        raw_ids = sorted({sh["internalId"] for sh in group if isinstance(sh.get("internalId"), int)})

        # 歌曲级：按 SONG_KEYS 的顺序重建，其余上游字段（id/imageName/dataQuality…）删掉
        item = {
            "internalId": song_internal_id(dataid, stype in UTAGE_TYPES, raw_ids, stype),
            "type": stype,
            "dataid": dataid,
            "category": song.get("category"),
            "title": song.get("title"),
            "artist": song.get("artist"),
            "bpm": song.get("bpm"),
            "version": song.get("version"),
            "isNew": song.get("isNew"),
            "isLocked": song.get("isLocked"),
        }
        if stype in UTAGE_TYPES:                       # 宴谱在歌曲级加 total
            counts = (group[0].get("noteCounts") or {}) if group else {}
            item["total"] = counts.get("total")
        # 谱面级：chartid = 难度槽位；不是 5 个常规难度就看 category，宴会场记 10，否则 null
        ok_sheets, err_sheets = [], []
        for sh in group:
            difficulty = sh.get("difficulty")
            chartid = chartid_of(difficulty, song.get("category"))
            row = {"chartid": chartid, "difficulty": difficulty,
                   "level": sh.get("level"), "internalLevelValue": sh.get("internalLevelValue"),
                   "noteDesigner": sh.get("noteDesigner"), "noteCounts": sh.get("noteCounts") or {},
                   "serverIds": sh.get("serverIds") or []}
            if sh.get("serverOverrides"):          # 没有就整个不写（照样本）
                row["serverOverrides"] = sh["serverOverrides"]
            row["isSpecial"] = sh.get("isSpecial")
            row["version"] = sh.get("version")
            row["releaseDate"] = sh.get("releaseDate")
            if sh.get("multiverInternalLevelValue"):
                row["multiverInternalLevelValue"] = sh["multiverInternalLevelValue"]
            (err_sheets if chartid is None else ok_sheets).append(row)
        if ok_sheets:
            item = dict(item, sheets=sorted(ok_sheets, key=lambda r: r["chartid"]))
            if err_sheets:
                item["_errSheets"] = err_sheets          # 后面整条搬去 dxrating-dataerror.json
            out.append(item)
        elif err_sheets:
            item = dict(item, sheets=err_sheets)
            item["_errWhole"] = "noChartidInRecord"
            item["_kind"] = stype
            out.append(item)
    return out


def chartid_of(difficulty, category) -> int | None:
    """chartid 只用来读的时候快速判断难度，不分细分：
        basic 0 / advanced 1 / expert 2 / master 3 / remaster 4；
        都不是 → 看 category，宴会场记 10，其余记 null（会被移进 dxrating-dataerror.json）。
    """
    slot = CHART_SLOT.get(difficulty)
    if slot is not None:
        return slot
    return UTAGE_SLOT if category == "宴会場" else None


def load_overrides() -> list[dict]:
    if not OVERRIDE_PATH.is_file():
        return []
    try:
        doc = json.loads(OVERRIDE_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"读不出临时覆盖表 {OVERRIDE_PATH}：{exc}")
    items = doc.get("overrides") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise RuntimeError(f"临时覆盖表格式不对：{OVERRIDE_PATH}")
    return [x for x in items if isinstance(x, dict)]


def apply_overrides(doc: dict, overrides: list[dict],
                    local_dataids: dict[tuple[str, str, str], int]) -> tuple[int, int]:
    """只改内存里的上游 dxdata；返回 (实际改动谱面数, 已修复条数)。"""
    applied = 0
    already = 0
    for ov in overrides:
        if str(ov.get("source") or "dxdata") != "dxdata":
            continue
        song_id = str(ov.get("songId") or "")
        title = str(ov.get("title") or "")
        version = str(ov.get("version") or "")
        family = str(ov.get("family") or "std")
        try:
            old_id = int(ov.get("fromInternalId"))
            new_id = int(ov.get("toInternalId"))
        except (TypeError, ValueError):
            raise RuntimeError(f"覆盖表缺 fromInternalId/toInternalId：{ov}")
        target = local_dataids.get((family, title, version))
        if target is not None and dataid_of(new_id) != target:
            raise RuntimeError(
                f"覆盖目标与本地曲库不一致：{title} / {version} "
                f"覆盖 {new_id}，本地 dataid {target}")
        matched_song = False
        fixed_song = False
        for song in doc.get("songs") or []:
            if song_id and str(song.get("id") or "") != song_id:
                continue
            if title and str(song.get("title") or "") != title:
                continue
            if version and str(song.get("version") or "") != version:
                continue
            matched_song = True
            for sh in song.get("sheets") or []:
                if str(sh.get("type") or "") != family:
                    continue
                if sh.get("internalId") == old_id:
                    sh["internalId"] = new_id
                    applied += 1
                elif sh.get("internalId") == new_id:
                    fixed_song = True
        if not matched_song:
            raise RuntimeError(f"覆盖表找不到歌曲：{title} / {version} / {song_id}")
        if fixed_song and not applied:
            already += 1
            print(f"[override] 已修复：{title} / {version} 已是 internalId {new_id}")
        elif applied:
            print(f"[override] {title} / {version}: internalId {old_id} → {new_id}"
                  f"（{applied} 张谱面）")
        else:
            print(f"[override] 未命中：{title} / {version} 上游没有 internalId {old_id}")
    return applied, already


def split_records(doc: dict, local_dataids: dict[tuple[str, str, str], int] | None = None) -> dict[str, list]:
    """按谱面族拆成 {族名: 记录列表}。一份一族，绝不混装。

    产出里谱面改名 chartid（只剩难度槽位），没法再按谱面 id 对账，所以直接在 main() 里
    对总数：产出谱面数必须等于上游谱面数。
    """
    by_family: dict[str, list] = {name: [] for name in FAMILY_ORDER}
    local_dataids = local_dataids or {}
    for song in doc.get("songs") or []:
        fallback = {family: local_dataids[(family, song.get("title"), song.get("version"))]
                    for family in FAMILY_ORDER
                    if (family, song.get("title"), song.get("version")) in local_dataids}
        for item in transform(song, fallback):
            by_family[item["type"]].append(item)
    return by_family


def build_doc(doc: dict, records: list, kind: str) -> dict:
    """产出顶层只留 updatedAt + songs（规则 2：上游头部定义表全删）。"""
    head = {k: doc.get(k) for k in TOP_KEYS_KEEP if k in doc}
    records = sorted(records, key=lambda r: (r.get("dataid") is None, r.get("dataid") or 0))
    return {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "kind": kind,
        "source": {"file": f"data/{SOURCE_SCRIPT_DIR}/{SOURCE_NAME}",
                   "updatedAt": doc.get("updatedAt")},
        **head,
        "songs": records,
    }


def facts(records: list) -> dict:
    return {
        "songs": len(records),
        "sheets": sum(len(r["sheets"]) for r in records),
        "types": {t: sum(1 for r in records if r["type"] == t)
                  for t in sorted({r["type"] for r in records})},
        "no_dataid": sum(1 for r in records if r.get("dataid") is None),
        "multi_sheet_songs": sum(1 for r in records if len(r["sheets"]) > 1),
    }


def print_facts(label: str, recs: list) -> None:
    f = facts(recs)
    print(f"  {label}")
    print(f"    歌曲 {f['songs']:,}   谱面 {f['sheets']:,}   "
          f"type {'  '.join(f'{k}={v}' for k, v in f['types'].items())}")
    if f["no_dataid"]:
        print(f"    dataid 缺失 {f['no_dataid']}")


def duplicate_dataids(records: list) -> dict:
    seen: dict = {}
    for r in records:
        d = r.get("dataid")
        if d is None:
            continue
        seen.setdefault(d, []).append(r.get("title"))
    return {k: v for k, v in seen.items() if len(v) > 1}


def missing_id_rows(records: list) -> list[dict]:
    """挑出没有 dataid 的记录，写成「给本地校对用」的完整记录（用户 2026-10-16 要求）。

    除了 title / category / artist / version 四项，**把校对判据要用的上游谱面数据也提出来**，
    这样 localProofreadDxrating 只读这一份 + 本地曲库就能配对，不必自己去翻 dxdata.json：

        sheets[]            本曲全部谱面：
                              version / type / difficulty / internalLevelValue /
                              noteCountsTotal / noteDesigner
        sheetVersions[]     谱面 version 去重排序（与本地 <AddVersion><id> 对齐用）
        sheetTypes[]        谱面族去重排序（dx / std / utage / utage2p）
        sheetDifficulties[] 难度名去重排序（宴谱是【協】这种）
        sheetLevelValues[]  定数去重排序
    一条记录一行，**不合并**：这些是各自独立的谱面记录，合并了就没法逐条回填 id。
    """
    rows = []
    for r in records:
        if r.get("dataid") is not None:
            continue
        sheets = [{
            "version": sh.get("version"),
            "type": sh.get("type"),
            "difficulty": sh.get("difficulty"),
            "internalLevelValue": sh.get("internalLevelValue"),
            "noteCountsTotal": (sh.get("noteCounts") or {}).get("total"),
            "noteDesigner": sh.get("noteDesigner"),
        } for sh in r.get("sheets") or []]
        row = {k: r.get(k) for k in ("title", "category", "artist", "version")}
        row["sheets"] = sheets
        row["sheetVersions"] = sorted({s["version"] for s in sheets if s["version"] is not None})
        row["sheetTypes"] = sorted({s["type"] for s in sheets if s["type"] is not None})
        row["sheetDifficulties"] = sorted({s["difficulty"] for s in sheets if s["difficulty"] is not None})
        row["sheetLevelValues"] = sorted({s["internalLevelValue"] for s in sheets
                                          if s["internalLevelValue"] is not None})
        rows.append(row)
    rows.sort(key=lambda r: (str(r["version"]), str(r["title"])))
    return rows


def write_missing_id(records: list, outdir: Path) -> int:
    """写出 dxrating-missing-id.json（标准 JSON，一条一个对象，不去重）。返回条数。"""
    rows = missing_id_rows(records)
    text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    path = outdir / OUT_MISSING
    tmp = outdir / (OUT_MISSING + ".part")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return len(rows)


def collect_data_errors(by_family: dict[str, list]) -> tuple[dict, list, int, int]:
    """把 chartid 算不出、不能进库的记录整条挑出来，按族返回。

    返回 ({族: 干净记录}, 报错记录, 搬走的谱面数, 整条搬走的记录数)。
    """
    clean_by_family: dict[str, list] = {name: [] for name in FAMILY_ORDER}
    errs = []
    moved_sheets = moved_records = 0
    for family, clean in ((name, clean_by_family[name]) for name in FAMILY_ORDER):
        src = by_family.get(family) or []
        for item in src:
            if "_errWhole" in item:
                item.pop("_kind", None)
                item["reason"] = item.pop("_errWhole")
                errs.append(item)
                moved_sheets += len(item["sheets"])
                moved_records += 1
                continue
            err = item.pop("_errSheets", None)
            if err:
                errs.append(dict(item, sheets=err, reason="sheetWithoutChartid"))
                moved_sheets += len(err)
                item = dict(item, sheets=[r for r in item["sheets"] if r["chartid"] is not None])
            clean.append(item)
    errs.sort(key=lambda r: (str(r.get("category")), str(r.get("title"))))
    return clean_by_family, errs, moved_sheets, moved_records


def write_or_clear(path: Path, text: str | None) -> None:
    """有内容就原子写；没有就删掉旧文件（不留空壳）。"""
    if text is None:
        path.unlink(missing_ok=True)
        for leftover in path.parent.glob(path.name + ".*"):
            leftover.unlink(missing_ok=True)
        return
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="dxrating_build.py",
        description="把上游 dxdata.json 按谱面族整成 dx/std/utage/utage2p 四份",
    )
    ap.add_argument("-i", "--input", metavar="PATH", help="上游原文（默认 data/catalog_dxdata/dxdata.json）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="输出目录（默认 data/dxrating_build/）")
    ap.add_argument("--local-dir", metavar="PATH", help="本地曲库目录（默认 data/localMusicData_build/；新曲兜底）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    in_path = Path(args.input).resolve() if args.input else data_root(SOURCE_SCRIPT_DIR) / SOURCE_NAME
    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)
    local_dir = (Path(args.local_dir).resolve() if args.local_dir
                 else data_root("localMusicData_build"))
    local_dataids = load_local_dataids(local_dir)

    print(BAR)
    print("曲库构建 · dxrating_build.py")
    print(f"  输入 : {in_path}")
    print(f"  新曲兜底 : {local_dir}（{len(local_dataids)} 条）")
    for family in FAMILY_ORDER:
        print(f"  输出 : {outdir}/{FAMILY_FILE[family]}（{family}）")
    print(RULE)

    try:
        doc = json.loads(in_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[错误] 上游原文不存在：{in_path}", file=sys.stderr)
        print("       先跑 tools/catalog_dxdata/catalog_dxdata.py 拉数据。", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"[错误] 读不出上游原文：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        overrides = load_overrides()
        if overrides:
            apply_overrides(doc, overrides, local_dataids)
    except RuntimeError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    by_family = split_records(doc, local_dataids)
    by_family, errs, moved_sheets, moved_records = collect_data_errors(by_family)
    all_records = [r for f in FAMILY_ORDER for r in by_family[f]]

    src_sheets = sum(len(s.get("sheets") or []) for s in doc.get("songs") or [])
    out_sheets = sum(len(r["sheets"]) for r in all_records) + moved_sheets
    if src_sheets != out_sheets:
        print(f"[错误] 谱面数不一致：上游 {src_sheets} vs 产出 {out_sheets}（含 dataerror）", file=sys.stderr)
        return 2

    try:
        outdir.mkdir(parents=True, exist_ok=True)
        for family in FAMILY_ORDER:
            name, recs = FAMILY_FILE[family], by_family[family]
            payload = json.dumps(build_doc(doc, recs, family), ensure_ascii=False, indent=2) + "\n"
            tmp = outdir / (name + ".part")
            try:
                tmp.write_bytes(payload.encode("utf-8"))
                tmp.replace(outdir / name)
            finally:
                tmp.unlink(missing_ok=True)
        for legacy in LEGACY_FILES:                        # 上一版混装的旧文件清掉，不留空壳
            (outdir / legacy).unlink(missing_ok=True)
        # dataerror：有就写，没有就把旧文件删掉，不留空壳
        if errs:
            payload = json.dumps(build_doc(doc, errs, "dataerror"), ensure_ascii=False, indent=2) + "\n"
            write_or_clear(outdir / OUT_DATAERROR, payload)
        else:
            write_or_clear(outdir / OUT_DATAERROR, None)
        miss_lines = write_missing_id(all_records, outdir)
    except OSError as exc:
        print(f"[错误] 写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(RULE)
    print(f"上游  歌曲 {len(doc.get('songs') or []):,}   谱面 {src_sheets:,}")
    print(RULE)
    for family in FAMILY_ORDER:
        print_facts(f"{FAMILY_FILE[family]}（{family}）", by_family[family])
    print(RULE)
    print("抽查")
    for family in FAMILY_ORDER:
        recs = by_family[family]
        if recs:
            r = recs[0]
            print(f"  {family:<8} 首条 : dataid={r.get('dataid')} internalId={r.get('internalId')} "
                  f"{str(r.get('title'))[:24]} 谱面{len(r['sheets'])}张")
    link = [r for r in all_records if r.get("dataid") in (131, 383)]
    if link:
        print("  131 / 383 : " + " / ".join(
            f"dataid={r.get('dataid')} internalId={r.get('internalId')} {r.get('type')} {len(r['sheets'])}张"
            for r in link))
    for family in FAMILY_ORDER:
        nid = [r for r in by_family[family] if r.get("dataid") is None]
        if nid:
            print(f"  {family} 无 dataid : {len(nid)} 首（上游没给 internalId，如 {str(nid[0].get('title'))[:20]}）")
    print(RULE)
    print(f"缺号清单 : {outdir / OUT_MISSING}")
    print(f"  title / category / artist / version   共 {miss_lines} 条（不去重）")
    if errs:
        print(f"数据错误 : {outdir / OUT_DATAERROR}")
        print(f"  整条搬走 {moved_records} 条记录、{moved_sheets} 张谱面（chartid 算不出），库里已不留")
    else:
        print(f"数据错误 : 无（没有算不出 chartid 的记录，不生成 {OUT_DATAERROR}）")
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
