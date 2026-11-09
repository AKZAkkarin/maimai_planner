# 用途：把本地游戏包的音乐 XML 整理成本地曲库（data/localMusicData.json）——舞萌「打歌计划器」
# 状态：现行
"""读本地游戏包的 Music.xml，产出「本地曲库」。

【数据源来自哪 · 重要】
  游戏包路径**绝不写死**（不写盘符、不写用户名）：本地游戏文件的读取是一次性的，
  由网页前端让用户当场选文件夹 → 后端把选中的路径传给本脚本。命令行用法：

      python tools/localMusicData_build/localMusicData_build.py -i <游戏包目录>

  也可以先走一遍探测再跑：-i 指到包目录（里面有 music/），或直接指到 music/ 都认。
  没给 -i 时会退回环境变量 MAIMAI_STREAMING_ASSETS（同样不落配置文件）。

【一条记录 = 一个 musicNNNNNN 目录】字段来源照用户 2026-10-16 的样本：
    internalId    按文件夹名出 —— 文件夹名的数字就是游戏内部号
    type          按文件夹名出 —— <10000 = std；>=10000 = dx；
                  >=100000 = 宴谱，再按文件夹里的 .ma2 分：
                      只有一张            → utage
                      两张 _L / _R 成对    → utage2p（双人谱；如 110281_00_L.ma2 + _R.ma2）
    dataid        按文件夹名出 —— 内部号的后 4 位（去掉前导 0）
    category      读 xml <genreName><str>，再查 category_map.json 归一化
    title         读 xml <name><str>
    artist        读 xml <artistName><str>
    bpm           读 xml <bpm>
    version       读 xml <AddVersion><str>
    sheets        读 xml <notesData> 里 isEnable=true 的难度：
        difficulty          槽位 0~4 → basic/advanced/expert/master/remaster；
                            宴谱取 <utageKanjiName>，写成【×】
        level               读 xml <level>
        internalLevelValue  读 xml <level><levelDecimal> 合成（5 + 0/10 = 5.0）
        noteDesigner        读 xml <notesDesigner><str>
        noteCounts.total    读 xml <maxNotes>
        comment             读 xml <comment>，**只有宴谱且非空才写**

【category 归一化表】
    与脚本同目录的 category_map.json（跟着脚本一起搬，不写死任何路径）：
        key = 本地 xml 的 <genreName><id>，值 = 项目统一写法（取 dxrating 那套，带全角 ＆）
        "101" → "POPS＆アニメ"、"104" → "ゲーム＆バラエティ" …
    **按 id 查而不按名字**：实测 8 首的 <genreName><str> 是坏的（3 首是 "\n    "、
    5 首是少字的 "バラエティ"），但它们的 <id> 都是对的。id 取值见
    A000/musicGenre/MusicGenreSort.xml。
    改归一化结果只改这张表，不动脚本。表里查不到的 id 会原样输出并[警告]列出。

【已知写法差异（照实转，不猜）】
  * 上游/本地号段不统一：同一首歌的 std 与 dx 是**两条独立记录**
      music000181（std, 内部号 181） / music010181（dx, 内部号 10181）
  * 宴谱标题里的中括号是**半角** `[協]Love You`，写法用全角 `【協】` 与 dxdata 对齐
  * category 用 xml 原文：`POPSアニメ`（没有 `＆`）；dxrating 那边写 `POPS＆アニメ`

【用法】
    python tools/localMusicData_build/localMusicData_build.py -i <包目录> [选项]
      -i, --input PATH   游戏包目录（含 music/）；不给则用环境变量 MAIMAI_STREAMING_ASSETS
      -o, --outdir PATH  输出目录（默认 data/localMusicData_build/）
      --limit N          只处理前 N 首（抽查用）
    退出码：0 成功；1 校验不通过；2 输入有问题或写盘失败。

依赖：仅 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"  # 批次号：run_chain 下发（手工跑记 manual），写进产物头部

sys.dont_write_bytecode = True

PROJECT_DIR = Path(__file__).resolve().parents[2]          # …/maimai-planner
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    import env
except Exception:                                          # env.py 缺失也不致命
    env = None

SCRIPT_DIR_NAME = "localMusicData_build"
SCRIPT_DIR = Path(__file__).resolve().parent             # 归一化表与本脚本同处，一起搬
# 谱面族：一份文件一族，绝不混装（与 dxrating_build 同一套族名）
LEGACY_FILES = ("localMusicData-normal.json",)             # 上一版混装名，写新文件时清掉
FAMILY_ORDER = ("dx", "std", "utage", "utage2p")
FAMILY_FILE = {"dx": "localMusicData-dx.json", "std": "localMusicData-std.json",
               "utage": "localMusicData-utage.json", "utage2p": "localMusicData-utage2p.json"}
CATEGORY_MAP_NAME = "category_map.json"                    # 与本脚本同目录，category 归一化表
VERSION_IDS_NAME = "version_ids.json"                      # 与本脚本同目录，版本 id → 版本名
INPUT_ENV_VAR = "MAIMAI_STREAMING_ASSETS"                  # 环境变量兜底，不落配置文件

NORMAL_TYPES = ("std", "dx")
UTAGE_TYPES = ("utage", "utage2p")

DIR_PATTERN = re.compile(r"^music(\d{4,8})$")
SLOT_DIFFICULTY = ("basic", "advanced", "expert", "master", "remaster")
# chartid：与 dxrating 侧同一套号 —— 常规难度 0~4，宴谱（宴会场）10
CHART_SLOT = {"basic": 0, "advanced": 1, "expert": 2, "master": 3, "remaster": 4}
UTAGE_SLOT = 10
TZ_CN = timezone(timedelta(hours=8))

RULE = "-" * 70
BAR = "=" * 70


def load_category_map(path: Path) -> dict[str, str]:
    """读 category 归一化表。缺文件/写坏就报错，绝不静默输出原文。"""
    if not path.is_file():
        raise FileNotFoundError(f"归一化表不存在：{path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    table = doc.get("map")
    if not isinstance(table, dict) or not table:
        raise ValueError(f"归一化表里没有可用的 map：{path}")
    return {str(k): str(v) for k, v in table.items()}


def load_version_id_map(path: Path) -> dict[int, str]:
    """读「版本 id → 版本名」表（取自 MusicVersion.xml，见文件头）。"""
    if not path.is_file():
        raise FileNotFoundError(f"版本 id 表不存在：{path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    table = doc.get("byId")
    if not isinstance(table, dict) or not table:
        raise ValueError(f"版本 id 表里没有可用的 byId：{path}")
    return {int(k): str(v) for k, v in table.items()}


def data_root(script_name: str) -> Path:
    if env is not None and callable(getattr(env, "data_dir_for", None)):
        try:
            return Path(env.data_dir_for(script_name))
        except Exception:
            pass
    if env is not None and getattr(env, "DATA_DIR", None):
        return Path(env.DATA_DIR) / script_name
    return PROJECT_DIR / "data" / script_name


def type_of(internal_id: int, ma2_names: list[str] | None = None) -> tuple[str, str | None]:
    """type 按文件夹名 + 谱面文件出，返回 (type, 异常说明)。

    内部号 <10000 → std；>=10000 → dx；
    >=100000 → 宴谱，再看文件夹里的 .ma2：
        * 只有一张           → utage
        * 两张 _L / _R 成对   → utage2p（双人谱，两个 .ma2）
    两种都不像时按 utage 记，并回一条异常说明（不静默）。
    """
    if internal_id < 100000:
        return ("dx" if internal_id >= 10000 else "std"), None

    names = ma2_names or []
    left = [n for n in names if "_L.ma2" in n]
    right = [n for n in names if "_R.ma2" in n]
    if len(names) == 2 and left and right:
        return "utage2p", None
    if len(names) == 1:
        return "utage", None
    return "utage", f"宴谱 .ma2 形态不符合两类（{len(names)} 个：{sorted(names)[:4]}），按 utage 记"


def dataid_of(internal_id: int) -> int:
    """dataid 按文件夹名出：内部号的后 4 位，去掉前导 0。"""
    return internal_id % 10000


def text_of(node, path: str, default=None):
    found = node.find(path)
    if found is None:
        return default
    return found.text if found.text is not None else default


def levels_of(notes) -> tuple[str, float]:
    """<level>5</level> + <levelDecimal>0</levelDecimal> → ("5", 5.0)。"""
    raw = (notes.findtext("level") or "0").strip() or "0"
    dec = (notes.findtext("levelDecimal") or "0").strip() or "0"
    return raw, int(raw) + int(dec) / 10


def parse_music(path: Path, internal_id: int, kind: str,
                category_map: dict[str, str], version_ids: dict[int, str]) -> dict:
    """一个 Music.xml → 一条记录。kind 是文件夹名判出的 type（含宴谱细分）。"""
    root = ET.parse(path).getroot()
    is_utage = internal_id >= 100000
    difficulty_text = (root.findtext("utageKanjiName") or "").strip()
    comment = (root.findtext("comment") or "").strip()
    raw_category = (root.findtext("genreName/id") or "").strip()
    category = category_map.get(raw_category, raw_category)

    # 版本按 <AddVersion><id> 查表；str 会坏（实测 4 首是空白），只当兜底
    raw_version_id = (root.findtext("AddVersion/id") or "").strip()
    version = version_ids.get(int(raw_version_id)) if raw_version_id.isdigit() else None
    if version is None:
        fallback = (root.findtext("AddVersion/str") or "").strip()
        version = fallback or None

    sheets = []
    for slot, notes in enumerate(root.findall("notesData/Notes")):
        if (notes.findtext("isEnable") or "").strip().lower() != "true":
            continue
        if is_utage:
            difficulty = f"【{difficulty_text}】" if difficulty_text else SLOT_DIFFICULTY[slot]
        else:
            difficulty = SLOT_DIFFICULTY[slot] if slot < len(SLOT_DIFFICULTY) else None
        level, value = levels_of(notes)
        sheet = {
            "chartid": CHART_SLOT.get(difficulty, UTAGE_SLOT),
            "difficulty": difficulty,
            "level": level,
            "internalLevelValue": value,
            "noteDesigner": text_of(notes, "notesDesigner/str"),
            "noteCounts": {"total": int(notes.findtext("maxNotes") or 0)},
        }
        if is_utage and comment:
            sheet["comment"] = comment
        sheets.append(sheet)

    return {
        "internalId": internal_id,
        "type": kind,
        "dataid": dataid_of(internal_id),
        "category": category,
        "title": root.findtext("name/str"),
        "artist": root.findtext("artistName/str"),
        "bpm": float(root.findtext("bpm") or 0),
        "version": version,
        "sheets": sheets,
    }


def load_packages(base: Path) -> list[tuple[str, list[tuple[int, Path]]]]:
    """确定要读哪些包，返回 [(包名, [(内部号, Music.xml 路径)…])…]，**已按包名升序**。

    两种情况：
      * base 自己是包目录（下面直接有 music/）→ 只读它一个
      * base 是 StreamingAssets（下面是一堆包目录）→ 按包名升序读**每个含 music/ 的子目录**，
        并跳过 Table 这种没有 music/ 的目录
    升序很关键：游戏按这个顺序加载，**包名大的后加载、覆盖小的**（见 merge_packages）。
    """
    if (base / "music").is_dir():
        return [(base.name, find_music_dirs(base))]

    packages = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        found = find_music_dirs(entry)
        if found:
            packages.append((entry.name, found))
    return packages


def find_music_dirs(base: Path) -> list[tuple[int, Path]]:
    """在给定目录里找 musicNNNNNN（base 本身是包目录或 music/ 都认）。"""
    music_dir = base / "music" if (base / "music").is_dir() else base
    found = []
    for entry in sorted(music_dir.iterdir()):
        if not entry.is_dir():
            continue
        matched = DIR_PATTERN.match(entry.name)
        if not matched:
            continue
        path = entry / "Music.xml"
        if path.is_file():
            found.append((int(matched.group(1)), path))
    return found


def merge_packages(packages: list[tuple[str, list[tuple[int, Path]]]]
                   ) -> tuple[dict[int, tuple[str, Path]], list[tuple[int, str, str]]]:
    """按包名升序合并：**后读的包覆盖先读的**（包名大的覆盖小的）。

    返回 (内部号 → (包名, Music.xml), 覆盖记录 [(内部号, 赢的包, 被覆盖的包)…])。
    """
    merged: dict[int, tuple[str, Path]] = {}
    overrides: list[tuple[int, str, str]] = []
    for pkg, entries in packages:                 # load_packages 已按包名升序
        for internal_id, path in entries:
            previous = merged.get(internal_id)
            if previous is not None:
                overrides.append((internal_id, pkg, previous[0]))
            merged[internal_id] = (pkg, path)
    return merged, overrides


def facts(records: list) -> dict:
    types: dict[str, int] = {}
    for r in records:
        types[r["type"]] = types.get(r["type"], 0) + 1
    return {
        "songs": len(records),
        "sheets": sum(len(r["sheets"]) for r in records),
        "types": types,
        "with_comment": sum(1 for r in records for s in r["sheets"] if "comment" in s),
    }


def print_facts(label: str, records: list) -> None:
    f = facts(records)
    prefix = f"  {label}" if label else "   "
    print(prefix + f"曲 {f['songs']:,}   谱面 {f['sheets']:,}   "
          f"type " + "  ".join(f"{k}={v}" for k, v in sorted(f["types"].items())))
    if f["with_comment"]:
        print(f"     带 comment 的宴谱难度 {f['with_comment']}")


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def build_doc(records: list, kind: str, source: dict) -> dict:
    records = sorted(records, key=lambda r: r["internalId"])
    return {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "kind": kind,
        "source": source,
        "songs": records,
    }


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="localMusicData_build.py",
        description="读本地游戏包的 Music.xml，产出 data/localMusicData.json",
    )
    ap.add_argument("-i", "--input", metavar="PATH",
                    help=f"游戏包目录（含 music/）或 music/ 本身；不给则用环境变量 {INPUT_ENV_VAR}")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="输出目录（默认 data/localMusicData_build/）")
    ap.add_argument("--limit", type=int, help="只处理前 N 首（抽查用）")
    ap.add_argument("--merge", action="store_true",
                    help="数据小更新：只把手头这（些）包的新内容合进现有曲库（同 internalId 覆盖、新的追加），"
                         "不是重建整库")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)

    raw_input = args.input or os.environ.get(INPUT_ENV_VAR)
    if not raw_input:
        print(f"[错误] 没给游戏包路径。用 -i <包目录>，或设环境变量 {INPUT_ENV_VAR}。", file=sys.stderr)
        print("       本脚本不写死任何本机路径（约定见 maimai-planner/env.py）。", file=sys.stderr)
        return 2
    base = Path(raw_input).expanduser()
    if not base.is_dir():
        print(f"[错误] 目录不存在：{base}", file=sys.stderr)
        return 2

    print(BAR)
    print("本地曲库构建 · localMusicData_build.py")
    print(f"  输入 : {base}")
    for family in FAMILY_ORDER:
        print(f"  输出 : {outdir / FAMILY_FILE[family]}（{family}）")
    print(f"  规则 : 文件夹名出 internalId/type/dataid；其余读 Music.xml；"
          f"category 查 {CATEGORY_MAP_NAME}；版本按 {VERSION_IDS_NAME} 的 id 查")
    print(RULE)

    try:
        category_map = load_category_map(SCRIPT_DIR / CATEGORY_MAP_NAME)
        version_ids = load_version_id_map(SCRIPT_DIR / VERSION_IDS_NAME)
    except (OSError, ValueError) as exc:
        print(f"[错误] 读不出归一化表：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    packages = load_packages(base)
    if not packages:
        print(f"[错误] {base} 里没找到 musicNNNNNN/Music.xml —— 这个路径是不是包目录或 StreamingAssets？",
              file=sys.stderr)
        return 2

    # 规则同游戏加载：包名升序读，包名大的覆盖小的
    merged, overrides = merge_packages(packages)
    picked = sorted(merged.items())
    if args.limit:
        picked = picked[:args.limit]

    records, failed, type_odd = [], [], []
    for internal_id, (pkg, path) in picked:
        try:
            ma2 = sorted(p.name for p in path.parent.glob("*.ma2"))
            kind, note = type_of(internal_id, ma2)
            if note:
                type_odd.append((path.parent.name, note))
            records.append(parse_music(path, internal_id, kind, category_map, version_ids))
        except (ET.ParseError, OSError, ValueError) as exc:
            failed.append((path.parent.name, f"{type(exc).__name__}: {exc}"))

    print(f"包   : " + "  ".join(f"{pkg}({len(entries)})" for pkg, entries in packages)
          + "   ← 按包名升序读，包名大的覆盖小的")
    print(f"目录 : 共 {sum(len(e) for _, e in packages)} 个 Music.xml，"
          f"合并去重后 {len(merged)} 首" + (f"，本次处理 {len(picked)} 首（--limit）" if args.limit else ""))
    if overrides:
        print(f"[覆盖] {len(overrides)} 首重复，按规则取包名大的那个：")
        for internal_id, winner, loser in overrides:
            print(f"        {internal_id}  {winner} 覆盖 {loser}")
    if failed:
        print(f"[警告] {len(failed)} 个解析失败（已跳过）：{failed[:3]}")
    if type_odd:
        print(f"[警告] {len(type_odd)} 个宴谱的 .ma2 形态异常（已按 utage 记）：{type_odd[:3]}")
    # category 若在归一化表里查不到，脚本会原样输出 —— 这里主动报出来，不静默
    unmapped = sorted({r["category"] for r in records} - set(category_map.values()))
    if unmapped:
        print(f"[警告] {len(unmapped)} 个 category 不在归一化表的取值里：{unmapped}")
        print(f"        请在 {CATEGORY_MAP_NAME} 里补齐，否则会原样输出。")

    # --merge（数据小更新）：新包进来时只把它合进现有曲库 —— 同 internalId 覆盖、新的追加，
    # 其余各族的记录原样保留，免得重建整库。覆盖方向与游戏加载一致（后来读到的包更大）。
    added = replaced = kept = 0
    if args.merge:
        merged_records: dict[tuple[str, int], dict] = {}
        for family in FAMILY_ORDER:
            path = outdir / FAMILY_FILE[family]
            if not path.is_file():
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"[错误] 读不出现有曲库 {path.name}：{type(exc).__name__}: {exc}", file=sys.stderr)
                return 2
            for rec in doc.get("songs") or []:
                merged_records[(rec["type"], rec["internalId"])] = rec
        kept = len(merged_records)
        for rec in records:
            key = (rec["type"], rec["internalId"])
            if key in merged_records:
                replaced += 1
            else:
                added += 1
            merged_records[key] = rec
        records = [r for _, r in sorted(merged_records.items(), key=lambda kv: kv[0][1])]
        print(f"[合并] 现有曲库 {kept:,} 首 → 本次新增 {added:,} 首 / 覆盖 {replaced:,} 首"
              f" → 合并后 {len(records):,} 首")

    by_family = {family: [r for r in records if r["type"] == family] for family in FAMILY_ORDER}
    source = {
        "input": base.name,
        "packages": [{"name": pkg, "dirs": len(entries)} for pkg, entries in packages],
        "overrides": len(overrides),
        "typeOdd": len(type_odd),
    }

    try:
        outdir.mkdir(parents=True, exist_ok=True)
        for family in FAMILY_ORDER:
            write_json(outdir / FAMILY_FILE[family],
                       build_doc(by_family[family], family, source))
        for legacy in LEGACY_FILES:                    # 上一版混装的旧文件清掉，不留空壳
            (outdir / legacy).unlink(missing_ok=True)
    except OSError as exc:
        print(f"[错误] 写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(RULE)
    for family in FAMILY_ORDER:
        print(f"  {FAMILY_FILE[family]}（{family}）")
        print_facts("", by_family[family])
    print(RULE)
    print("抽查")
    for label, recs in ((f, by_family[f]) for f in FAMILY_ORDER):
        for r in recs[:1]:
            print(f"  {label} internalId={r['internalId']} type={r['type']} dataid={r['dataid']} "
                  f"{str(r['title'])[:22]}")
            for s in r["sheets"]:
                extra = f"  comment={str(s.get('comment'))[:16]}…" if "comment" in s else ""
                print(f"     {str(s['difficulty']):12} level={s['level']:4} "
                      f"定数={s['internalLevelValue']:5} 物量={s['noteCounts']['total']:5} "
                      f"designer={str(s['noteDesigner'])[:14]}{extra}")
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
