# 用途：把上游曲库的两种别名（曲名写法 searchAcronyms 与玩家别名 aliases）融成一张按内部号查的 aliasesTag 表
# 状态：现行
"""把上游 dxdata.json 里的两种别名融成一份按**内部号**查的别名表。

【融什么（用户 2026-10-17 拍板）】
  上游把别名分两处存，实测是**两个不同的东西**：
    songs[].searchAcronyms  曲名的各种写法（官方 / 罗马字 / 中文 / 梗写法），1141/1799 首有值
    顶层 aliases            玩家起的别名，2026 条 {song_id, name}，覆盖 751 首歌
  两份都是**歌曲级**。本脚本合成一个字段 `aliasesTag`：
    并集去重、保留上游顺序 —— 先按 searchAcronyms 的原序，再按 aliases[] 的原序追加没出现过的
    （实测两者有交集，去重后不重复；顺序不变是为了让「官方写法」排在前头）。

【挂在哪个号上】
  **internalId**（用户拍板）。上游一条歌曲记录里 std 与 dx 谱混放，所以同一首歌通常产出两条
  记录（std 一条、dx 一条），两条各挂同一份 aliasesTag。
  号的算法与 dxrating_build 完全一致，不另立一套：
      dataid = 谱面级 internalId % 10000（取后 4 位）
      std → dataid        dx → dataid + 10000        宴谱（utage / utage2p）→ 上游原值
  ⚠ 不用「dx 减 10000」那种算法：本地实测有 dx 谱 internalId 本身就 < 10000（けーね 360），
    减出来是负数；取后 4 位才对（dxrating_build docstring 的规则 4 同一句话）。
  ⚠ 号只认**谱面级** internalId（上游歌曲级根本没有这个字段）。缺号的谱面用同曲其它谱面推曲绘号
    再按族算；宴谱缺号推不出来 —— 那几条进 missing[]，**不猜**。

【输出】data/aliasesTag_catalog/aliasesTag.json
  songs[]   每条：internalId / dataid / title / aliasesTag
            （带上 dataid + title 是为了人工排查时一眼看懂这条别名属于哪首曲）
  missing[] 每条：song_id / title / why / aliasesTag —— 推不出号的曲，别名暂时挂不上去
  counts    上游曲数 / 记录数 / 有别名记录数 / 别名条数（去重前后）/ missing 数 / 撞号数

【给谁用】fusion 读它在**歌曲级**补 `aliasesTag` 字段（取代原来的 searchAcronyms）。

【用法】
    python tools/aliasesTag_catalog/aliasesTag_catalog.py [选项]
      --dxdata PATH       原始曲库（默认 data/catalog_dxdata/dxdata.json）
      -o, --outdir PATH   输出目录（默认 data/aliasesTag_catalog/）
    退出码：0 成功；2 读不出 / 写盘失败。



依赖：仅 Python 标准库，不联网。
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

SCRIPT_DIR_NAME = "aliasesTag_catalog"
DXDATA_SCRIPT_DIR = "catalog_dxdata"
DXDATA_NAME = "dxdata.json"
OUT_NAME = "aliasesTag.json"

UTAGE_TYPES = ("utage", "utage2p")
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


def index_aliases(aliases) -> dict[str, list[str]]:
    """顶层 aliases[] → {song_id: [name, …]}，保上游原序。"""
    out: dict[str, list[str]] = {}
    for row in aliases or []:
        if not isinstance(row, dict):
            continue
        song_id, name = row.get("song_id"), row.get("name")
        if isinstance(song_id, str) and isinstance(name, str) and name:
            out.setdefault(song_id, []).append(name)
    return out


def merge_tag(song: dict, alias_by_song: dict[str, list[str]]) -> list[str]:
    """searchAcronyms ∪ 该曲的 aliases —— 并集去重、保留上游顺序。"""
    names = list(song.get("searchAcronyms") or []) + list(alias_by_song.get(song.get("id")) or [])
    tag: list[str] = []
    for name in names:
        if isinstance(name, str) and name and name not in tag:
            tag.append(name)
    return tag


def dataid_of(internal_id: int) -> int:
    """内部号 → 曲绘号：取后 4 位（与 dxrating_build 规则 4 同一算法）。"""
    return internal_id % 10000


def song_dataid(song: dict) -> int | None:
    """同曲任一有号的谱面 → 曲绘号；一张都没有就是 None。"""
    for sheet in song.get("sheets") or []:
        internal_id = sheet.get("internalId")
        if isinstance(internal_id, int):
            return dataid_of(internal_id)
    return None


def keys_of(song: dict, local_dataids: dict[str, int] | None = None) -> tuple[list[int], list[str]]:
    """本曲的谱面都挂到哪个内部号 → (号列表, 推不出号的谱面说明)。"""
    fallback = song_dataid(song)
    local_dataids = local_dataids or {}
    keys: list[int] = []
    gaps: list[str] = []
    for sheet in song.get("sheets") or []:
        internal_id = sheet.get("internalId")
        kind = sheet.get("type")
        dataid = dataid_of(internal_id) if isinstance(internal_id, int) else (
            fallback if fallback is not None else local_dataids.get(kind))
        label = f"{kind}/{sheet.get('difficulty')}"
        if kind in UTAGE_TYPES:
            if isinstance(internal_id, int):           # 宴谱号只能是上游原值，推不出
                keys.append(internal_id)
            else:
                gaps.append(label)
        elif kind in ("std", "dx"):
            if dataid is None:
                gaps.append(label)
            else:
                keys.append(dataid + 10000 if kind == "dx" else dataid)
        else:
            gaps.append(label)                         # 意料之外的族，报出来不静默丢
    return sorted(set(keys)), gaps


def build(dxdata: dict, local_map: dict[tuple[str, str, str], int] | None = None) -> dict:
    alias_by_song = index_aliases(dxdata.get("aliases"))
    local_map = local_map or {}
    rows: dict[int, dict] = {}
    missing: list[dict] = []
    conflicts: list[dict] = []
    stats = {"songs": 0, "songsWithTag": 0, "raw": 0, "kept": 0}

    for song in dxdata.get("songs") or []:
        stats["songs"] += 1
        tag = merge_tag(song, alias_by_song)
        stats["raw"] += len(song.get("searchAcronyms") or []) + len(alias_by_song.get(song.get("id")) or [])
        stats["kept"] += len(tag)
        if tag:
            stats["songsWithTag"] += 1

        local = {family: local_map[(family, song.get("title"), song.get("version"))]
                 for family in UTAGE_TYPES + ("std", "dx")
                 if (family, song.get("title"), song.get("version")) in local_map}
        keys, gaps = keys_of(song, local)
        if not keys:
            missing.append({"song_id": song.get("id"), "title": song.get("title"),
                            "why": "上游没给谱面号，同曲也没有号可推（宴谱缺号）",
                            "aliasesTag": tag})
            continue
        if gaps:
            missing.append({"song_id": song.get("id"), "title": song.get("title"),
                            "why": "这些谱面推不出号：" + "、".join(gaps),
                            "aliasesTag": tag})
        for key in keys:
            row = rows.get(key)
            if row is None:
                rows[key] = {"internalId": key, "dataid": dataid_of(key),
                             "title": song.get("title"), "aliasesTag": tag}
            elif row["aliasesTag"] != tag:             # 同一个号被两首歌命中：报出来，不静默合并
                conflicts.append({"internalId": key, "kept": row["aliasesTag"],
                                  "keptTitle": row["title"], "dropped": tag,
                                  "droppedTitle": song.get("title")})

    songs = [rows[key] for key in sorted(rows)]
    return {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "source": f"data/{DXDATA_SCRIPT_DIR}/{DXDATA_NAME}",
        "counts": {
            "upstreamSongs": stats["songs"],
            "records": len(songs),
            "withTag": sum(1 for row in songs if row["aliasesTag"]),
            "upstreamSongsWithTag": stats["songsWithTag"],
            "aliasesRaw": stats["raw"],
            "aliasesKept": stats["kept"],
            "missing": len(missing),
            "conflicts": len(conflicts),
        },
        "songs": songs,
        "missing": missing,
        "conflicts": conflicts,
    }


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="aliasesTag_catalog.py",
        description="把上游的 searchAcronyms 与玩家 aliases 融成按内部号查的 aliasesTag 表",
    )
    ap.add_argument("--dxdata", metavar="PATH", help=f"原始曲库（默认 data/{DXDATA_SCRIPT_DIR}/{DXDATA_NAME}）")
    ap.add_argument("--local-dir", metavar="PATH", help="本地曲库目录（默认 data/localMusicData_build/；新曲兜底）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help=f"输出目录（默认 data/{SCRIPT_DIR_NAME}/）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    dxdata_path = Path(args.dxdata).resolve() if args.dxdata else data_root(DXDATA_SCRIPT_DIR) / DXDATA_NAME
    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)
    local_dir = Path(args.local_dir).resolve() if args.local_dir else data_root("localMusicData_build")
    local_map = {}
    for family in ("std", "dx"):
        try:
            ldoc = json.loads((local_dir / f"localMusicData-{family}.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for song in ldoc.get("songs") or []:
            if song.get("dataid") is not None:
                local_map[(family, song.get("title"), song.get("version"))] = int(song["dataid"])

    print(BAR)
    print("aliasesTag_catalog · 曲别名（曲名写法 + 玩家别名）")
    print(f"  原始曲库 : {dxdata_path}")
    print(f"  输出     : {outdir}/{OUT_NAME}")
    print(RULE)

    try:
        dxdata = read_json(dxdata_path, "原始曲库")
    except (OSError, ValueError) as exc:
        print(f"[错误] 读输入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    built = build(dxdata, local_map)
    c = built["counts"]
    print(f"输入 : 上游曲 {c['upstreamSongs']:,} 首   别名 {c['aliasesRaw']:,} 条"
          f"（其中 {c['upstreamSongsWithTag']:,} 首有别名）")
    print(f"产出 : 记录 {c['records']:,} 条   有别名 {c['withTag']:,} 条   "
          f"去重后 {c['aliasesKept']:,} 条（原 {c['aliasesRaw']:,} 条）")
    print(f"缺号 : {c['missing']:,} 首无处挂   撞号 {c['conflicts']:,} 处")

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
