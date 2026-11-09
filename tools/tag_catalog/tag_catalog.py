# 用途：抓 dxrating 的社区谱面标签，按内部号 + 谱面索引成一份标签表
# 状态：现行
"""抓取 dxrating 的社区谱面标签（转圈 / 扫键 / 绝赞段 / 诈称谱 …），整理成按谱面查的表。

【数据从哪来】
    GET https://miruku.dxrating.net/api/v1/tags?idScheme=public
  返回三样东西：
    tags      标签本身（21 个），localized_name / localized_description 带 en / ja / ko / zh-Hans
    tagGroups 标签分组（3 个）：配置 / 难度 / 评价
    tagSongs  标签挂在哪张谱（7000+ 条）：{song_id, sheet_id, sheet_type, sheet_difficulty, tag_id}

【为什么要翻译】
  tagSongs 用的是**公开 id**（dsng_ / dsht_），而项目曲库用的是**游戏内部号**。
  公开谱面 id 翻译成内部号靠原始 dxdata（data/catalog_dxdata/dxdata.json）：
  实测 tagSongs 的 sheet_id 在原始 dxdata 里 **7072/7072 全部命中**，
  命中后取该谱面的 internalId 即可。

【输出（一份按「内部号 → 谱面 → 三段标签」查的表）】
  歌曲级：internalId / dataid / type / title
  谱面级：difficulty / level / chartid
           PatternsTag / DifficultyTag / EvaluationTag   ← 按分组拆成三段
  每段是 [{id, zh}]；没有标签就是空数组。
  拆分按 tag group_id 对应 tagGroups 的 localized_name：
    配置 → PatternsTag、难度 → DifficultyTag、评价 → EvaluationTag

【用法】
    python tools/tag_catalog/tag_catalog.py [选项]
      --dxdata PATH       原始曲库（默认 data/catalog_dxdata/dxdata.json）
      -o, --outdir PATH   输出目录（默认 data/tag_catalog/）
      --from-file PATH    不联网，改用本地已抓好的 /tags 响应（上面那个 JSON）
      --dry-run           只抓不写盘，打印统计
    退出码：0 成功；2 缺输入 / 抓取失败 / 写盘失败。

依赖：仅 Python 标准库（走系统代理，跟 catalog_dxdata 一样）。
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
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
except Exception:
    env = None

SCRIPT_DIR_NAME = "tag_catalog"
DXDATA_SCRIPT_DIR = "catalog_dxdata"
DXDATA_NAME = "dxdata.json"
OUT_NAME = "tags.json"
TAGS_URL = "https://miruku.dxrating.net/api/v1/tags?idScheme=public"
TIMEOUT_SECONDS = 120
LANG = "zh-Hans"                                           # 显示名用中文（项目界面语言）

# 分组名 → 产物里的字段名
GROUP_FIELD = {"配置": "PatternsTag", "难度": "DifficultyTag", "评价": "EvaluationTag"}
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


def http_get(url: str) -> bytes:  # noqa: D401
    """走系统代理 GET 一次（与 catalog_dxdata 同一套办法）。"""
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        # 默认 UA（Python-urllib）会被服务端 403，必须带浏览器 UA
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    }, method="GET")
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if getattr(response, "status", 200) != 200:
            raise OSError(f"HTTP {response.status}")
        return response.read()


def sheet_internal_id(sheet: dict, song_dataid: int | None) -> int | None:
    """谱面 → 游戏内部号：原始 dxdata 给了就用，没给就按曲绘号 + 谱面族算。

    std → dataid、dx → dataid+10000、宴谱 → 原值（与本地曲库、dxrating_build 同一套算法）。
    """
    raw = sheet.get("internalId")
    if isinstance(raw, int):
        return raw
    if song_dataid is None:
        return None
    kind = sheet.get("type")
    if kind == "dx":
        return song_dataid + 10000
    if kind == "std":
        return song_dataid
    return None                                            # 宴谱缺号只能靠上游值


def tag_entry(tag: dict) -> dict:
    """产出一个标签条目：只要 id + 中文名（ja / en 不要）。"""
    localized = tag.get("localized_name") or {}
    return {"id": tag["id"], "zh": localized.get(LANG) or localized.get("en") or ""}


def build_tags(tags_doc: dict, dxdata: dict, proofread_dir: Path | None = None) -> dict:
    """把 /tags 的响应翻译成「内部号 + 谱面 → 三段标签」的表。

    v2：新曲在原始 dxdata 里可能没有 internalId（上游新曲缺字段），
    用 Proofread-build 的 title/version/difficulty 反查内部号。
    """
    group_by_id = {g["id"]: (g.get("localized_name") or {}) for g in tags_doc.get("tagGroups") or []}
    tag_by_id = {t["id"]: t for t in tags_doc.get("tags") or []}

    # 新曲兜底：Proofread 有内部号，但已经不带公开 sheet id
    proof_map = {}
    if proofread_dir:
        for family in ("dx", "std"):
            p = proofread_dir / f"Proofread-{family}.json"
            try:
                doc = json.loads(p.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            for song in doc.get("songs") or []:
                for sheet in song.get("sheets") or []:
                    proof_map[(song.get("title"), song.get("version"), sheet.get("difficulty"))] = \
                        (song.get("internalId"), sheet.get("chartid"))
    dx_songs = {s.get("id"): s for s in dxdata.get("songs") or []}

    # sheet_id → 该谱面的内部号（用原始 dxdata 翻译）
    # 实测原始 dxdata 的**谱面级** internalId 是准的（7266 张有值，122 张缺）
    sheet_to_internal: dict[str, int] = {}
    fallback = 0
    for song in dxdata.get("songs") or []:
        # 缺 internalId 的谱面，用同曲其它谱面推曲绘号再算（std→dataid、dx→dataid+10000）
        dataid = None
        for sheet in song.get("sheets") or []:
            raw = sheet.get("internalId")
            if not isinstance(raw, int):
                continue
            dataid = raw - 10000 if sheet.get("type") == "dx" else raw
            break
        for sheet in song.get("sheets") or []:
            internal_id = sheet.get("internalId")
            if not isinstance(internal_id, int):
                internal_id = sheet_internal_id(sheet, dataid)
                if internal_id is not None:
                    fallback += 1
            if internal_id is not None and sheet.get("id"):
                sheet_to_internal[sheet["id"]] = internal_id

    # 同一张谱可能挂多个同组标签，全部收进来
    bucket: dict[tuple, dict] = {}
    stats = {"rows": 0, "untranslated": 0, "byGroup": {}, "computedId": fallback}
    for row in tags_doc.get("tagSongs") or []:
        stats["rows"] += 1
        internal_id = sheet_to_internal.get(row.get("sheet_id"))
        if internal_id is None and proof_map:
            song = dx_songs.get(row.get("song_id")) or {}
            hit = proof_map.get((song.get("title"), song.get("version"), row.get("sheet_difficulty")))
            if hit:
                internal_id = hit[0]
        if internal_id is None:
            stats["untranslated"] += 1
            continue
        tag = tag_by_id.get(row.get("tag_id"))
        if not tag:
            continue
        group = group_by_id.get(tag.get("group_id")) or {}
        field = GROUP_FIELD.get((group.get(LANG) or group.get("en") or "").strip())
        if field is None:
            continue
        key = (internal_id, row.get("sheet_type"), row.get("sheet_difficulty"))
        entry = bucket.setdefault(key, {"PatternsTag": [], "DifficultyTag": [], "EvaluationTag": []})
        if all(t["id"] != tag["id"] for t in entry[field]):
            entry[field].append(tag_entry(tag))
            stats["byGroup"][field] = stats["byGroup"].get(field, 0) + 1

    sheets = []
    for (internal_id, sheet_type, difficulty) in sorted(bucket, key=lambda k: (k[0], str(k[2]))):
        entry = bucket[(internal_id, sheet_type, difficulty)]
        sheets.append({
            "internalId": internal_id,
            "type": sheet_type,
            "difficulty": difficulty,
            "PatternsTag": sorted(entry["PatternsTag"], key=lambda t: t["id"]),
            "DifficultyTag": sorted(entry["DifficultyTag"], key=lambda t: t["id"]),
            "EvaluationTag": sorted(entry["EvaluationTag"], key=lambda t: t["id"]),
        })

    return {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "source": TAGS_URL,
        "language": LANG,
        "groups": [{"id": g["id"], "name": (g.get("localized_name") or {}).get(LANG),
                    "field": GROUP_FIELD.get(((g.get("localized_name") or {}).get(LANG) or "").strip())}
                   for g in tags_doc.get("tagGroups") or []],
        "tags": [{**tag_entry(t), "group_id": t.get("group_id")}
                 for t in tags_doc.get("tags") or []],
        "counts": {
            "tagSongs": stats["rows"],
            "sheets": len(sheets),
            "untranslated": stats["untranslated"],
            "computedId": stats["computedId"],
            **stats["byGroup"],
        },
        "sheets": sheets,
    }


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(prog="tag_catalog.py",
                                 description="抓 dxrating 社区谱面标签，按内部号索引成标签表")
    ap.add_argument("--dxdata", metavar="PATH", help=f"原始曲库（默认 data/{DXDATA_SCRIPT_DIR}/{DXDATA_NAME}）")
    ap.add_argument("--proofread-dir", metavar="PATH",
                    help="Proofread 曲库目录（默认 data/Proofread-build；用于新曲兜底）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help=f"输出目录（默认 data/{SCRIPT_DIR_NAME}/）")
    ap.add_argument("--from-file", metavar="PATH", help="不联网，读本地已抓好的 /tags 响应")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写盘")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    dxdata_path = Path(args.dxdata).resolve() if args.dxdata else data_root(DXDATA_SCRIPT_DIR) / DXDATA_NAME
    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)
    proofread_dir = (Path(args.proofread_dir).resolve() if args.proofread_dir
                     else PROJECT_DIR / "data" / "Proofread-build")

    print(BAR)
    print("tag_catalog · dxrating 社区谱面标签")
    print(f"  原始曲库 : {dxdata_path}")
    print(f"  新曲兜底 : {proofread_dir}")
    print(f"  标签来源 : {args.from_file or TAGS_URL}")
    print(f"  输出     : {outdir}/{OUT_NAME}")
    print(RULE)

    try:
        dxdata = read_json(dxdata_path, "原始曲库")
        if args.from_file:
            tags_doc = read_json(Path(args.from_file).resolve(), "本地 tags 响应")
        else:
            tags_doc = json.loads(http_get(TAGS_URL).decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"[错误] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    built = build_tags(tags_doc, dxdata, proofread_dir)
    c = built["counts"]
    print(f"输入 : 标签 {len(built['tags'])} 个   分组 {len(built['groups'])} 个   "
          f"tagSongs {c['tagSongs']:,} 条")
    print(f"翻译 : 内部号译出 {c['tagSongs'] - c['untranslated']:,} 条，"
          f"译不出（公开谱面 id 不在原始曲库里）{c['untranslated']} 条   "
          f"其中 {c['computedId']} 条靠曲绘号推算")
    print(f"产出 : 涉及谱面 {c['sheets']:,} 张   "
          f"PatternsTag {c.get('PatternsTag', 0):,} · DifficultyTag {c.get('DifficultyTag', 0):,} · "
          f"EvaluationTag {c.get('EvaluationTag', 0):,}")

    if args.dry_run:
        print(BAR)
        print("（--dry-run，未写盘）")
        return 0

    try:
        outdir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(built, ensure_ascii=False, indent=2) + "\n"
        tmp = outdir / (OUT_NAME + ".part")
        try:
            tmp.write_bytes(payload.encode("utf-8"))
            tmp.replace(outdir / OUT_NAME)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[错误] 写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(RULE)
    print("抽查")
    for row in built["sheets"][:4]:
        p = " · ".join(t["zh"] for t in row["PatternsTag"]) or "—"
        d = " · ".join(t["zh"] for t in row["DifficultyTag"]) or "—"
        e = " · ".join(t["zh"] for t in row["EvaluationTag"]) or "—"
        print(f"  internalId={row['internalId']:<7} {row['type']:<6} {str(row['difficulty']):<10} "
              f"配置[{p}]  难度[{d}]  评价[{e}]")
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
