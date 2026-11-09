# 用途：把 Chiffonmai 的第三方曲别名（按曲名的配置片段）按内部号融成一张别名表，给 fusion 用
# 状态：现行
"""把 Chiffonmai 的曲别名并进打歌计划器的曲别名表。

【这份数据长什么样】
  它来自 Chiffonmai 的一份配置，`song_aliases` 是其中一个 **String 字段**，内容是一段转义的 JSON：
      { …配置的别的字段…,
        "song_aliases": { "type": "String",
                          "value": "{\"曲名\": [\"别名1\", \"别名2\"], …}" } }
  实测 1502 条曲目 / 12609 个别名（2026-10-16）。键是**曲名**、不是内部号 —— 所以要在库里按标题找。
  ⚠ 用户给的是**去敏片段**（可能只是整份配置里摘出来的一段，连最外层花括号都没有）。所以本脚本：
    * 在**任意层级**里定位 `song_aliases` 那一条（BFS，路径会打印出来）；
    * 除它以外**不读、不存、不打印**任何别的字段（配置里其它内容与本脚本无关）；
    * 片段形态（以 "song_aliases": { 开头）补上外层花括号再解析，整份配置形态则直接解析。

【怎么对上内部号】
  1. 先查人工修正表 title_map.json（本脚本同目录）：标题在库里对不上、或一个标题对上多首不同的曲时，
     只认这张表指定的 internalId（现有一条 Link / Link(CoF) / [宴]人マニア，见该文件的 why）。
  2. 否则按**标题精确匹配** Proofread 产物；同一标题落在**同一首曲**的多条记录（std / dx 各一条，
     宴谱还有变体）时，每条都挂同一份别名 —— 与 aliasesTag_catalog 的口径一致。
  3. 同一标题落到**不同 dataid**（真的重名，如 131 与 383 都叫 Link）→ 记 conflicts[] 且**不挂**，不猜。
  4. 库里根本没这个标题 → 记 missing[]，不猜。

【输出】data/aliasesTag_chiffon/aliasesTag-chiffon.json
  songs[]     internalId / dataid / title / aliasesTag（按 internalId 升序）
  missing[]   标题 / 别名 / 为什么没挂上
  conflicts[] 标题 / 别名 / 候选记录（等 title_map.json 指定）
  counts      条目数 / 别名条数 / 记录数 / 有别名记录数 / 修正表命中数 / missing / conflicts

【给谁用】fusion 读它在**歌曲级**把这份别名**追加**到 aliasesTag 后面（现有在前、这份在后，并集去重）。

【用法】
    python tools/aliasesTag_chiffon/aliasesTag_chiffon.py --als <als.json> [选项]
      --als PATH           Chiffonmai 的别名文件（也可用环境变量 MAIMAI_CHIFFON_ALS）
      --proofread-dir PATH 用来对号的曲库目录（默认 data/Proofread-build/）
      --title-map PATH     人工修正表（默认本脚本同目录的 title_map.json）
      -o, --outdir PATH    输出目录（默认 data/aliasesTag_chiffon/）
    退出码：0 成功；2 没给 --als / 读不出输入 / 写盘失败。

依赖：仅 Python 标准库，不联网。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"   # 批次号：run_chain 下发（手工跑记 manual）
ALS_ENV = "MAIMAI_CHIFFON_ALS"                          # 也接受环境变量，便于链里不带参数跑

sys.dont_write_bytecode = True

PROJECT_DIR = Path(__file__).resolve().parents[2]       # …/maimai-planner
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    import env
except Exception:                                       # env.py 缺失也不致命
    env = None

SCRIPT_DIR_NAME = "aliasesTag_chiffon"
SCRIPT_DIR = Path(__file__).resolve().parent
TITLE_MAP_NAME = "title_map.json"
PROOFREAD_SCRIPT_DIR = "Proofread-build"
OUT_NAME = "aliasesTag-chiffon.json"

FAMILY_ORDER = ("dx", "std", "utage", "utage2p")
PROOFREAD_FILE = "Proofread-{family}.json"

TZ_CN = timezone(timedelta(hours=8))
RULE = "─" * 68
BAR = "=" * 68


def data_root(script_dir_name: str) -> Path:
    """data/<脚本名>/ —— 与 env.data_dir_for() 同一口径。"""
    if env is not None and callable(getattr(env, "data_dir_for", None)):
        try:
            return Path(env.data_dir_for(script_dir_name))
        except Exception:
            pass
    return PROJECT_DIR / "data" / script_dir_name


def read_json(path: Path, what: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise OSError(f"{what} 读不出：{path}（{exc}）") from exc
    except ValueError as exc:
        raise ValueError(f"{what} 不是合法 JSON：{path}（{exc}）") from exc


def find_song_aliases(doc):
    """在任意层级的 JSON 里**只**定位 song_aliases 那一条，返回 (值, 路径)。

    这份文件可能是整份配置（song_aliases 藏在某层里），也可能是去敏后只摘出来的片段；
    除 song_aliases 外**不读、不存、不打印**任何别的字段（去敏文件里其它内容与本脚本无关）。
    """
    queue = [("$", doc)]
    while queue:
        where, node = queue.pop(0)
        if isinstance(node, dict):
            if "song_aliases" in node:
                return node["song_aliases"], f"{where}.song_aliases"
            queue.extend((f"{where}.{k}", v) for k, v in node.items())
        elif isinstance(node, list):
            queue.extend((f"{where}[{i}]", v) for i, v in enumerate(node))
    return None, ""


def load_als(path: Path) -> tuple[dict, str]:
    """读 Chiffonmai 的别名文件 → ({曲名: [别名]}, song_aliases 在原文里的位置)。"""
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        doc = json.loads(text)
    except ValueError:
        # 去敏片段：以 "song_aliases": { 开头、没有最外层花括号 → 补上再解
        doc = json.loads("{" + text.strip().rstrip(",") + "}")
    field, where = find_song_aliases(doc)
    if field is None:
        raise ValueError("这份文件里找不到 song_aliases 这一条")
    payload = field.get("value") if isinstance(field, dict) else field
    payload = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(payload, dict):
        raise ValueError(f"song_aliases（{where}）里不是一个 {{曲名: [别名]}} 的表")

    out = {}
    for title, aliases in payload.items():
        if not isinstance(aliases, list):
            continue
        seen, kept = set(), []
        for alias in aliases:
            alias = str(alias).strip()
            if alias and alias not in seen:
                seen.add(alias)
                kept.append(alias)
        if kept:
            out[str(title)] = kept
    return out, where


def load_title_map(path: Path) -> dict:
    """人工修正表 → {标题: internalId}。容忍 {"entries": {标题: {...}}} 与 {标题: 号} 两种写法。"""
    if not Path(path).is_file():
        return {}
    doc = read_json(Path(path), "修正表")
    entries = doc.get("entries") if isinstance(doc, dict) and "entries" in doc else doc
    out = {}
    for title, spec in (entries or {}).items():
        if isinstance(spec, dict) and spec.get("internalId") is not None:
            out[str(title)] = int(spec["internalId"])
        elif isinstance(spec, int):
            out[str(title)] = spec
    return out


def load_index(proofread_dir: Path) -> dict:
    """标题 → [{family, internalId, dataid}, …]；只收有 internalId 的记录（缺号的对不上号）。"""
    index = {}
    for family in FAMILY_ORDER:
        path = Path(proofread_dir) / PROOFREAD_FILE.format(family=family)
        if not path.is_file():
            continue
        for rec in (read_json(path, f"曲库（{family}）").get("songs") or []):
            internal_id = rec.get("internalId")
            if internal_id is None:
                continue
            index.setdefault(str(rec.get("title")), []).append(
                {"family": family, "internalId": internal_id, "dataid": rec.get("dataid")})
    return index


def build(als: dict, index: dict, title_map: dict, sources: dict) -> dict:
    songs: dict[int, dict] = {}
    missing, conflicts = [], []
    pinned_hits = 0

    for title, aliases in als.items():
        pin = title_map.get(title)
        if pin is not None:                                  # 修正表：只认它指定的号
            pinned_hits += 1
            db_title = next((t for t, recs in index.items()
                             if any(r["internalId"] == pin for r in recs)), title)
            targets = [{"internalId": pin, "dataid": pin % 10000, "title": db_title}]
        else:
            recs = index.get(title) or []
            if not recs:
                missing.append({"title": title, "aliasesTag": aliases, "why": "库里没有这个标题"})
                continue
            if len({r["dataid"] for r in recs}) > 1:         # 真重名：不猜
                conflicts.append({
                    "title": title, "aliasesTag": aliases,
                    "candidates": [{"internalId": r["internalId"], "dataid": r["dataid"],
                                    "family": r["family"]} for r in sorted(recs, key=lambda x: x["internalId"])],
                    "why": "同一标题在库里对应多首不同的曲 —— 请在 title_map.json 里指定 internalId"})
                continue
            targets = [{"internalId": r["internalId"], "dataid": r["dataid"],
                        "title": next((t for t, rs in index.items() if r in rs), title)} for r in recs]

        for target in targets:
            rec = songs.setdefault(target["internalId"], {
                "internalId": target["internalId"], "dataid": target["dataid"],
                "title": target["title"], "aliasesTag": []})
            seen = set(rec["aliasesTag"])
            for alias in aliases:
                if alias not in seen:
                    seen.add(alias)
                    rec["aliasesTag"].append(alias)

    ordered = [songs[k] for k in sorted(songs)]
    counts = {
        "alsEntries": len(als),
        "aliasesRaw": sum(len(v) for v in als.values()),
        "records": len(ordered),
        "withTag": sum(1 for r in ordered if r["aliasesTag"]),
        "aliasesKept": sum(len(r["aliasesTag"]) for r in ordered),
        "pinned": pinned_hits,
        "missing": len(missing),
        "conflicts": len(conflicts),
    }
    return {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "kind": "aliasesTag-chiffon",
        "sources": sources,
        "counts": counts,
        "songs": ordered,
        "missing": missing,
        "conflicts": conflicts,
    }


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="aliasesTag_chiffon.py",
        description="把 Chiffonmai 的曲别名（按曲名）按内部号融成一张 aliasesTag 表",
    )
    ap.add_argument("--als", metavar="PATH",
                    help=f"Chiffonmai 的别名文件（也可用环境变量 {ALS_ENV}）")
    ap.add_argument("--proofread-dir", metavar="PATH",
                    help=f"用来对号的曲库目录（默认 data/{PROOFREAD_SCRIPT_DIR}/）")
    ap.add_argument("--title-map", metavar="PATH",
                    help=f"人工修正表（默认本脚本同目录的 {TITLE_MAP_NAME}）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help=f"输出目录（默认 data/{SCRIPT_DIR_NAME}/）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    als_arg = args.als or os.environ.get(ALS_ENV)
    if not als_arg:
        print(f"[错误] 没给别名文件：用 --als PATH 或设环境变量 {ALS_ENV}", file=sys.stderr)
        return 2
    als_path = Path(als_arg).expanduser().resolve()
    proofread_dir = (Path(args.proofread_dir).resolve() if args.proofread_dir
                     else data_root(PROOFREAD_SCRIPT_DIR))
    title_map_path = (Path(args.title_map).resolve() if args.title_map
                      else SCRIPT_DIR / TITLE_MAP_NAME)
    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)

    print(BAR)
    print("aliasesTag_chiffon · Chiffonmai 曲别名")
    print(f"  别名文件 : {als_path}")
    print(f"  对号曲库 : {proofread_dir}")
    print(f"  修正表   : {title_map_path}")
    print(f"  输出     : {outdir / OUT_NAME}")
    print(RULE)

    try:
        als, als_field = load_als(als_path)
        index = load_index(proofread_dir)
        title_map = load_title_map(title_map_path)
    except (OSError, ValueError) as exc:
        print(f"[错误] 读输入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    built = build(als, index, title_map,
                  sources={"als": als_path.name,                       # 只记文件名：产物里绝不落本机路径
                           "proofreadDir": f"data/{PROOFREAD_SCRIPT_DIR}",
                           "titleMap": f"tools/{SCRIPT_DIR_NAME}/{TITLE_MAP_NAME}"})
    c = built["counts"]
    print(f"输入 : 别名条目 {c['alsEntries']:,} 条   别名 {c['aliasesRaw']:,} 个"
          f"   对号曲库 {len(index):,} 个标题")
    print(f"      song_aliases 定位在 : {als_field}")
    print(f"产出 : 记录 {c['records']:,} 条   有别名 {c['withTag']:,} 条   "
          f"去重后 {c['aliasesKept']:,} 个（原 {c['aliasesRaw']:,} 个）")
    print(f"修正表命中 {c['pinned']:,} 条   没对上 {c['missing']:,} 条   重名待定 {c['conflicts']:,} 条")

    if built["missing"]:
        print(RULE)
        print("没对上的（库里没这些标题，不猜）：")
        for row in built["missing"][:8]:
            print(f"  {row['title'][:36]:<38} {row['aliasesTag'][:3]}")
    if built["conflicts"]:
        print(RULE)
        print("重名待定的（同一标题对上多首不同的曲，等 title_map.json 指定）：")
        for row in built["conflicts"]:
            print(f"  {row['title'][:36]:<38} 候选 {[x['internalId'] for x in row['candidates']]}")

    print(RULE)
    print("抽查")
    by_id = {r["internalId"]: r for r in built["songs"]}
    for internal_id in (131, 383, 111772):
        if internal_id in by_id:
            row = by_id[internal_id]
            print(f"  internalId={row['internalId']:<8} dataid={row['dataid']:<6} "
                  f"{str(row['title'])[:16]:<18} {row['aliasesTag'][:6]}")
    for row in built["songs"][:2]:
        print(f"  internalId={row['internalId']:<8} dataid={row['dataid']:<6} "
              f"{str(row['title'])[:16]:<18} {row['aliasesTag'][:6]}")

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
    print(f"完成 : {outdir / OUT_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
