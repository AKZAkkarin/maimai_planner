# 用途：把本地定数并进 Proofread 曲库，产出 fusion-normal / fusion-utage
# 状态：现行
"""把本地游戏数据并进 Proofread 曲库（结构照用户 2026-10-16 的单数据组-fusion 样本）。

【合什么】
  底子是 Proofread-build 的产出（dxrating 全字段 + 已补好的本地号）。本脚本只补这四样：
    * `localLevelValue`  本地定数 —— 从 localMusicData_build 的本地曲库里取
      （本地曲按 **(dataid, type)** 配，谱面按 chartid / 难度名配）
    * `serverIds`        该谱面本地有的话追加 `"local"`
    * `PatternsTag` / `DifficultyTag` / `EvaluationTag`   三段社区标签（**谱面级**，来自 tag_catalog）
    * `aliasesTag`       曲别名（**歌曲级**）—— aliasesTag_catalog 的（曲名写法 + dxdata 玩家别名）在前，
      再追加 aliasesTag_chiffon 的（Chiffonmai 第三方别名），并集去重、保序；这张表不在就只挂前者
  其它字段一律保持 Proofread 原值，**不改结构、不改名**（上游的 searchAcronyms 已不在链路里）。

【为什么不能自己算】
  `localLevelValue` 只能来自本地游戏包（Music.xml 的 <level>+<levelDecimal>）；
  dxdata 的 `internalLevelValue` 是**上游定数**，改名 `dxratingLevelValue` 由 Proofread 侧负责，
  两者不合并、不互相覆盖（用户 2026-10-16 拍板：dxrating 与本地分开计）。

【用法】
    python tools/fusion/fusion.py [选项]
      --proofread-dir PATH  Proofread 产物目录（默认 data/Proofread-build/）
      --local-dir PATH      本地曲库目录（默认 data/localMusicData_build/）
      -o, --outdir PATH     输出目录（默认 data/fusion/）
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

SCRIPT_DIR_NAME = "fusion"
PROOFREAD_SCRIPT_DIR = "Proofread-build"
LOCAL_SCRIPT_DIR = "localMusicData_build"

# 谱面族：一份输入一族、一份产出一族，绝不混装
FAMILY_ORDER = ("dx", "std", "utage", "utage2p")
PROOFREAD_FILE = {"dx": "Proofread-dx.json", "std": "Proofread-std.json",
                  "utage": "Proofread-utage.json", "utage2p": "Proofread-utage2p.json"}
LOCAL_FILE = {"dx": "localMusicData-dx.json", "std": "localMusicData-std.json",
              "utage": "localMusicData-utage.json", "utage2p": "localMusicData-utage2p.json"}
OUT_FILE = {"dx": "fusion-dx.json", "std": "fusion-std.json",
            "utage": "fusion-utage.json", "utage2p": "fusion-utage2p.json"}
LEGACY_FILES = ("fusion-normal.json",)                     # 上一版混装名，写新文件时清掉

# 谱面/本地数据里的版本写法 → 曲库 version 口径（只这几条对不上；其余原样）
from lib.versions import VERSION_FIX

LOCAL_SERVER_TAG = "local"                                 # 本地有这张谱就追加这个
TAG_SCRIPT_DIR = "tag_catalog"                             # 社区标签表（tag_catalog 抓的）
TAG_NAME = "tags.json"
TAG_FIELDS = ("PatternsTag", "DifficultyTag", "EvaluationTag")
ALIASES_SCRIPT_DIR = "aliasesTag_catalog"                  # 曲别名表（aliasesTag_catalog 融的）
ALIASES_NAME = "aliasesTag.json"
ALIASES_CHIFFON_SCRIPT_DIR = "aliasesTag_chiffon"          # 第三方别名表（Chiffonmai；文件不在就跳过）
ALIASES_CHIFFON_NAME = "aliasesTag-chiffon.json"
ALIASES_LXNS_SCRIPT_DIR = "aliasesTag_lxns"                # 第三方别名表（落雪 lxns；文件不在就跳过）
ALIASES_LXNS_NAME = "aliasesTag-lxns.json"
TZ_CN = timezone(timedelta(hours=8))
DATAID_MAX_DEFAULT = 5000          # 自制谱：dataid > 5000 的整首不进入 fusion

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


def load_by_family(directory: Path, names: dict[str, str]) -> dict[str, list[dict]]:
    """按谱面族读四份文件，返回 {族: 记录列表}。"""
    out: dict[str, list[dict]] = {f: [] for f in FAMILY_ORDER}
    for family in FAMILY_ORDER:
        path = directory / names[family]
        if path.is_file():
            out[family] = [r for r in (read_json(path, names[family]).get("songs") or [])
                           if isinstance(r, dict)]
    return out


def index_local(local: list[dict]) -> dict[int, dict]:
    """本地曲库按 **internalId** 索引。

    Proofread 侧的 internalId 就是本地游戏内部号，实测 1882/1882 全部能接上，
    所以直接按号取，不用靠 (dataid, type) 猜 —— 同名宴谱多版本的 (dataid, type) 会撞，
    连定数都可能一样（Garakuta Doll Play 5 个版本都是 227/utage/14.0），只有内部号能分开。
    """
    return {song["internalId"]: song for song in local if song.get("internalId") is not None}


def local_sheet_map(local_song: dict) -> dict:
    """本地谱面索引：按 chartid，另备一份按难度名。

    同一首歌内部 chartid 不会重复（常规 0~4、宴谱 10），所以按它索引安全。
    """
    by_chart: dict = {}
    by_difficulty: dict = {}
    for sheet in (local_song or {}).get("sheets") or []:
        if sheet.get("chartid") is not None:
            by_chart.setdefault(sheet["chartid"], sheet)
        by_difficulty.setdefault(sheet.get("difficulty"), sheet)
    return {"byChart": by_chart, "byDifficulty": by_difficulty}


def index_tags(tags_doc: dict) -> dict[tuple, dict]:
    """标签表按 (内部号, 谱面族, 难度) 索引 —— 一张谱一个条目。"""
    index: dict[tuple, dict] = {}
    for row in tags_doc.get("sheets") or []:
        key = (row.get("internalId"), row.get("type"), row.get("difficulty"))
        index[key] = row
    return index


def merge_aliases(base: dict, extra: dict) -> dict:
    """把第三方别名**追加**到现有别名后面：并集去重、保序（现有在前、chiffon 在后）。"""
    merged = {key: list(tags) for key, tags in base.items()}
    for internal_id, tags in extra.items():
        current = merged.setdefault(internal_id, [])
        seen = set(current)
        for tag in tags:
            if tag not in seen:
                seen.add(tag)
                current.append(tag)
    return merged


def index_aliases(aliases_doc: dict) -> dict[int, list[str]]:
    """别名表按 **内部号** 索引 —— 一份别名挂在这首曲的每一条记录上（std / dx 各一条）。"""
    index: dict[int, list[str]] = {}
    for row in aliases_doc.get("songs") or []:
        if isinstance(row, dict) and row.get("internalId") is not None:
            index[row["internalId"]] = list(row.get("aliasesTag") or [])
    return index


def level_text(value, fallback):
    """定数文本：游戏里 x.6 及以上显示成 "x+"（本地包的 level 字段有时只写 "13"，小数在 levelDecimal 里）。"""
    if value is None:
        return fallback
    base = int(value)
    frac = round(float(value) - base, 3)
    return f"{base}+" if frac >= 0.6 else str(base)


def fuse_sheet(sheet: dict, local_maps: dict, tag_maps: dict) -> dict:
    """一张 Proofread 谱面 → 补上本地定数 / 本地等级 / serverIds 的 local 标记 / 三段社区标签。"""
    local_sheet = (local_maps["byChart"].get(sheet.get("chartid"))
                   or local_maps["byDifficulty"].get(sheet.get("difficulty")))

    row = dict(sheet)
    if local_sheet is not None:
        row["localLevelValue"] = local_sheet.get("internalLevelValue")
        server_ids = list(sheet.get("serverIds") or [])
        if LOCAL_SERVER_TAG not in server_ids:
            server_ids.append(LOCAL_SERVER_TAG)
        row["serverIds"] = server_ids
    else:
        row["localLevelValue"] = None                     # 本地没有这张谱
    for field in TAG_FIELDS:
        row[field] = tag_maps.get(field) or []
    # 定数文本以数值为准（本地 13.7 但文本写 "13" 的，补成 "13+"）
    value = row.get("localLevelValue")
    if value is None:
        value = row.get("dxratingLevelValue")
    row["level"] = level_text(value, row.get("level"))
    return row


def synth_song_from_local(local_song: dict) -> dict:
    """本地包里有、上游曲库没有的歌 —— 用本地记录补一条形状对齐 Proofread 的曲记录。

    上游没有这些歌，所以 dxratingLevelValue 留空（None）—— 本地定数只走 localLevelValue
    （fuse_sheet 会从本地补），两者是不同的数据组，不互相顶替。
    """
    sheets = []
    for s in local_song.get("sheets") or []:
        sheets.append({
            "chartid": s.get("chartid"),
            "difficulty": s.get("difficulty"),
            "level": level_text(s.get("internalLevelValue"), s.get("level")),
            "dxratingLevelValue": None,
            "localLevelValue": None,
            "noteDesigner": s.get("noteDesigner"),
            "noteCounts": s.get("noteCounts") or {},
            "serverIds": [],
            "isSpecial": False,
            "version": VERSION_FIX.get(local_song.get("version") or "", local_song.get("version")),
            "releaseDate": None,
        })
    return {
        "internalId": local_song.get("internalId"),
        "type": local_song.get("type"),
        "dataid": local_song.get("dataid"),
        "category": local_song.get("category"),
        "title": local_song.get("title"),
        "artist": local_song.get("artist"),
        "bpm": local_song.get("bpm"),
        "version": VERSION_FIX.get(local_song.get("version") or "", local_song.get("version")),
        "isNew": False,
        "isLocked": False,
        "sheets": sheets,
    }


def fuse_song(song: dict, local_song: dict | None, tags_by_sheet: dict,
              aliases_by_id: dict) -> dict:
    record = dict(song)
    maps = local_sheet_map(local_song) if local_song else {"byChart": {}, "byDifficulty": {}}
    internal_id = song.get("internalId")
    record["aliasesTag"] = list(aliases_by_id.get(internal_id) or [])
    sheets = []
    for sheet in song.get("sheets") or []:
        tag_maps = tags_by_sheet.get((internal_id, song.get("type"), sheet.get("difficulty"))) or {}
        sheets.append(fuse_sheet(sheet, maps, tag_maps))
    record.pop("sheets", None)      # 键序：先摘再挂，让 sheets 排在 aliasesTag 后面（record 派生自上游曲记录，sheets 本来就在里面，直接赋值只会改值、位置不动）
    record["sheets"] = sheets
    # 曲目版本 = 这条记录里谱面的添加版本（所有谱面一致时）。上游 song.version 对「老歌后来补 DX 谱」
    # 会写成原曲版本（如 怪盗Rのテーマ dx 写成 maimai），跟谱面版本（CiRCLE）对不上 —— 用谱面版本。
    sheet_versions = {sh.get("version") for sh in sheets if sh.get("version")}
    if len(sheet_versions) == 1:
        v = next(iter(sheet_versions))
        record["version"] = VERSION_FIX.get(v, v)
    return record


def build_doc(records: list, kind: str, stamped: str, sources: dict, matched: int,
              tagged: int, chiffon_records: int = 0, local_only: int = 0,
              dataid_max: int = DATAID_MAX_DEFAULT, skipped_by_dataid: int = 0) -> dict:
    return {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": stamped,
        "kind": kind,
        "sources": sources,
        "counts": {
            "songs": len(records),
            "songsWithoutInternalId": sum(1 for r in records if r.get("internalId") is None),
            "sheets": sum(len(r["sheets"]) for r in records),
            "songsWithLocal": sum(1 for r in records
                                  if any(s.get("localLevelValue") is not None for s in r["sheets"])),
            "sheetsWithLocalLevel": sum(1 for r in records for s in r["sheets"]
                                        if s.get("localLevelValue") is not None),
            "sheetsTaggedLocal": sum(1 for r in records for s in r["sheets"]
                                     if LOCAL_SERVER_TAG in (s.get("serverIds") or [])),
            "sheetsWithCommunityTag": sum(1 for r in records for s in r["sheets"]
                                          if any(s.get(f) for f in TAG_FIELDS)),
            "songsWithAliasesTag": sum(1 for r in records if r.get("aliasesTag")),
            "chiffonRecords": chiffon_records,
            "songsLocalOnly": local_only,
            "dataidMax": dataid_max,
            "skippedByDataid": skipped_by_dataid,
        },
        "songs": records,
    }


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="fusion.py",
        description="把本地定数与社区标签并进 Proofread 曲库，按谱面族产出 fusion-<族>.json",
    )
    ap.add_argument("--proofread-dir", metavar="PATH", help="Proofread 产物目录（默认 data/Proofread-build/）")
    ap.add_argument("--local-dir", metavar="PATH", help="本地曲库目录（默认 data/localMusicData_build/）")
    ap.add_argument("--tags", metavar="PATH", help=f"社区标签表（默认 data/{TAG_SCRIPT_DIR}/{TAG_NAME}）")
    ap.add_argument("--aliases-tag", metavar="PATH",
                    help=f"曲别名表（默认 data/{ALIASES_SCRIPT_DIR}/{ALIASES_NAME}）")
    ap.add_argument("--aliases-chiffon", metavar="PATH",
                    help=f"第三方别名表（默认 data/{ALIASES_CHIFFON_SCRIPT_DIR}/{ALIASES_CHIFFON_NAME}；不在就跳过）")
    ap.add_argument("--aliases-lxns", metavar="PATH",
                    help=f"落雪别名表（默认 data/{ALIASES_LXNS_SCRIPT_DIR}/{ALIASES_LXNS_NAME}；不在就跳过）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="输出目录（默认 data/fusion/）")
    ap.add_argument("--dataid-max", type=int, default=DATAID_MAX_DEFAULT,
                    help="dataid 大于该值的整首跳过（默认 5000；自制谱截断）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    proofread_dir = Path(args.proofread_dir).resolve() if args.proofread_dir else data_root(PROOFREAD_SCRIPT_DIR)
    local_dir = Path(args.local_dir).resolve() if args.local_dir else data_root(LOCAL_SCRIPT_DIR)
    tags_path = Path(args.tags).resolve() if args.tags else data_root(TAG_SCRIPT_DIR) / TAG_NAME
    aliases_path = (Path(args.aliases_tag).resolve() if args.aliases_tag
                    else data_root(ALIASES_SCRIPT_DIR) / ALIASES_NAME)
    aliases_chiffon_path = (Path(args.aliases_chiffon).resolve() if args.aliases_chiffon
                            else data_root(ALIASES_CHIFFON_SCRIPT_DIR) / ALIASES_CHIFFON_NAME)
    aliases_lxns_path = (Path(args.aliases_lxns).resolve() if args.aliases_lxns
                         else data_root(ALIASES_LXNS_SCRIPT_DIR) / ALIASES_LXNS_NAME)
    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)
    dataid_max = int(args.dataid_max)


    print(BAR)
    print("fusion · 本地定数与社区标签并进 Proofread 曲库（按谱面族分开）")
    print(f"  本地曲库  : {local_dir}/")
    print(f"  社区标签  : {tags_path}")
    print(f"  曲别名    : {aliases_path}")
    print(f"  第三方别名: {aliases_chiffon_path}"
          + ("" if aliases_chiffon_path.is_file() else "（这张表不在，跳过）"))
    print(f"  落雪别名  : {aliases_lxns_path}"
          + ("" if aliases_lxns_path.is_file() else "（这张表不在，跳过）"))
    print(f"  dataid 截断: > {dataid_max} 的自制谱整首跳过")
    for family in FAMILY_ORDER:
        print(f"  {family:<8} {PROOFREAD_FILE[family]:<24} → {OUT_FILE[family]}")
    print(RULE)

    try:
        proofread_by_family = load_by_family(proofread_dir, PROOFREAD_FILE)
        local_by_family = load_by_family(local_dir, LOCAL_FILE)
        local_by_id = index_local([r for f in FAMILY_ORDER for r in local_by_family[f]])
        tags_by_sheet = index_tags(read_json(tags_path, "社区标签表"))
        aliases_by_id = index_aliases(read_json(aliases_path, "曲别名表"))
        chiffon_by_id = {}
        if aliases_chiffon_path.is_file():                 # 第三方别名：有就追加在后，没有就跳过
            chiffon_by_id = index_aliases(read_json(aliases_chiffon_path, "第三方别名表"))
            aliases_by_id = merge_aliases(aliases_by_id, chiffon_by_id)
        if aliases_lxns_path.is_file():                    # 落雪别名：追加在最后
            aliases_by_id = merge_aliases(aliases_by_id, index_aliases(read_json(aliases_lxns_path, "落雪别名表")))
    except (OSError, ValueError) as exc:
        print(f"[错误] 读输入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    stamped = datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")
    sources = {"proofread": [f"data/{PROOFREAD_SCRIPT_DIR}/{PROOFREAD_FILE[f]}" for f in FAMILY_ORDER],
               "local": [f"data/{LOCAL_SCRIPT_DIR}/{LOCAL_FILE[f]}" for f in FAMILY_ORDER],
               "communityTags": f"data/{TAG_SCRIPT_DIR}/{TAG_NAME}",
               "aliasesTag": f"data/{ALIASES_SCRIPT_DIR}/{ALIASES_NAME}"}
    if chiffon_by_id:
        sources["aliasesTagChiffon"] = f"data/{ALIASES_CHIFFON_SCRIPT_DIR}/{ALIASES_CHIFFON_NAME}"
    if aliases_lxns_path.is_file():
        sources["aliasesTagLxns"] = f"data/{ALIASES_LXNS_SCRIPT_DIR}/{ALIASES_LXNS_NAME}"

    out_by_family: dict[str, list] = {f: [] for f in FAMILY_ORDER}
    with_local = tagged = 0
    skipped_by_dataid = 0
    local_only_by_family: dict[str, int] = {}
    for family in FAMILY_ORDER:
        seen = set()
        for song in proofread_by_family[family]:
            try:
                if int(song.get("dataid") or 0) > dataid_max:
                    skipped_by_dataid += 1
                    continue
            except (TypeError, ValueError):
                pass
            local_song = local_by_id.get(song.get("internalId"))
            if local_song is not None:
                with_local += 1
            record = fuse_song(song, local_song, tags_by_sheet, aliases_by_id)
            tagged += sum(1 for s in record["sheets"] if any(s.get(f) for f in TAG_FIELDS))
            out_by_family[family].append(record)
            if song.get("internalId") is not None:
                seen.add(song["internalId"])

        # 本地有、上游没有的歌也要进曲库（用户要求）—— 用本地记录合成，排在族尾
        extra = sorted((s for s in local_by_family[family]
                        if s.get("internalId") is not None and s["internalId"] not in seen),
                       key=lambda s: s["internalId"])
        local_only_count = 0
        for local_song in extra:
            try:
                if int(local_song.get("dataid") or 0) > dataid_max:
                    skipped_by_dataid += 1
                    continue
            except (TypeError, ValueError):
                pass
            out_by_family[family].append(
                fuse_song(synth_song_from_local(local_song), local_song, tags_by_sheet, aliases_by_id))
            local_only_count += 1
        local_only_by_family[family] = local_only_count

    try:
        outdir.mkdir(parents=True, exist_ok=True)
        for family in FAMILY_ORDER:
            name, recs = OUT_FILE[family], out_by_family[family]
            payload = json.dumps(build_doc(recs, family, stamped, sources, with_local, tagged,
                                           len(chiffon_by_id), local_only_by_family[family],
                                           dataid_max, skipped_by_dataid),
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

    total = sum(len(out_by_family[f]) for f in FAMILY_ORDER)
    print(f"输入 : Proofread {total:,} 首   本地曲库 {len(local_by_id):,} 首   "
          f"配上本地 {with_local:,} 首   标签表 {len(tags_by_sheet):,} 张谱   "
          f"别名表 {len(aliases_by_id):,} 首（其中第三方 {len(chiffon_by_id):,} 首）")
    if skipped_by_dataid:
        print(f"自制谱: 按 dataid > {dataid_max} 跳过 {skipped_by_dataid:,} 首")
    print(RULE)
    extra_line = "  ".join(f"{f} {n}" for f, n in local_only_by_family.items() if n)
    if extra_line:
        print(f"本地独有（上游没有，已补进曲库）: {extra_line}")
    for family in FAMILY_ORDER:
        recs = out_by_family[family]
        filled = sum(1 for r in recs for s in r["sheets"] if s.get("localLevelValue") is not None)
        sheets = sum(len(r["sheets"]) for r in recs)
        tagged_here = sum(1 for r in recs for s in r["sheets"] if any(s.get(f) for f in TAG_FIELDS))
        print(f"  {OUT_FILE[family]:<22} 曲 {len(recs):,}   谱面 {sheets:,}   "
              f"本地定数填上 {filled:,}   带社区标签 {tagged_here:,}")
    orphan = [(r["internalId"], r["dataid"], r["type"], r["title"], s["chartid"], s["difficulty"])
              for f in FAMILY_ORDER for r in out_by_family[f] for s in r["sheets"]
              if s.get("localLevelValue") is None]
    if orphan:
        print(RULE)
        print(f"本地没配上的谱面 {len(orphan)} 张（本地包缺这些谱）：")
        for o in orphan[:6]:
            print("   internalId=%s dataid=%s type=%s chartid=%s %s  %s" % o)
    print(RULE)
    print("抽查")
    first = next((out_by_family[f][0] for f in FAMILY_ORDER if out_by_family[f]), None)
    if first is not None:
        print(f"  internalId={first['internalId']} type={first['type']} "
              f"dataid={first['dataid']} {first['title']}")
        for sh in first["sheets"][:3]:
            print(f"     {sh['chartid']} {str(sh['difficulty']):9} level={sh['level']:4} "
                  f"dxrating={sh['dxratingLevelValue']} local={sh['localLevelValue']} "
                  f"serverIds={sh['serverIds']}")
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
