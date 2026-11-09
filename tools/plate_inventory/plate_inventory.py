# 用途：纯遍历游戏包，列出全部牌子的 id/条件/它引用的 musicGroup 与 nameplate 贴图 id，并生成牌子映射表
# 状态：现行
"""把游戏包里跟牌子有关的三份事实原样倒出来，不做任何推断：

1. `A000/plate/<plateId>/Plate.xml` —— 每个牌子：plateId、牌号、名字、条件文本、
   是否带 MUSIC_VERSION_ALL、它**真正写到**的 musicGroupId（id + 名字，跳过 0/空）
2. `A000/musicGroup/MusicGroup<6位>/MusicGroup.xml` —— 每个曲组：id、名字、曲目数（internalId 列表）
3. `A000/AssetBundleImages/nameplate/ui_plate_<6位>.ab` —— 贴图 id 清单

【输出】data/plate_inventory/
  plate_inventory.json   全量事实：557 个牌子 + 179 个曲组（含各自 internalId）+ 贴图 id + 三边对照
  plate_map.json         牌子映射表：范围内 108 个牌 → 范围组/除外组/internalId/贴图
                         （tools/plate_conditions/ 读它展开条件库；人工可核可改）
  产物里**不记任何本机路径**，只记计数与文件名。

【用法】
    python tools/plate_inventory/plate_inventory.py -i <游戏包目录> [选项]
      -i, --game-package PATH  含 A000/ 的游戏包目录（也可用环境变量 MAIMAI_STREAMING_ASSETS）
      -o, --out PATH           全量事实的输出文件（默认 data/plate_inventory/plate_inventory.json）
      --map-out PATH           映射表的输出文件（默认 data/plate_inventory/plate_map.json）
    退出码：0 成功；2 没给游戏包 / 路径里没有 A000/ / 写盘失败。
    游戏包换版本后重跑一次即可 —— 这张表就是给版本更新用的。

依赖：仅 Python 标准库，不联网。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import os
import xml.etree.ElementTree as ET
from pathlib import Path

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"
PKG_ENV = "MAIMAI_STREAMING_ASSETS"

sys.dont_write_bytecode = True

PROJECT_DIR = Path(__file__).resolve().parents[2]        # …/maimai-planner
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    import env
except Exception:
    env = None

SCRIPT_DIR_NAME = "plate_inventory"
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


def resolve_package(raw: str) -> Path | None:
    base = Path(raw).expanduser().resolve()
    if not base.is_dir():
        return None
    for cand in (base, base / "StreamingAssets", base / "Sinmai_Data" / "StreamingAssets"):
        if (cand / "A000").is_dir():
            return cand
    return None


def txt(root: ET.Element, tag: str) -> str:
    node = root.find(tag)
    if node is None:
        return ""
    inner = node.find("str")
    return (inner.text if inner is not None and inner.text else node.text or "").strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plate_inventory.py",
                                 description="纯遍历：牌子 → musicGroup → nameplate 的事实清单")
    ap.add_argument("-i", "--game-package", metavar="PATH",
                    help=f"含 A000/ 的游戏包目录（也可用环境变量 {PKG_ENV}）")
    ap.add_argument("-o", "--out", metavar="PATH",
                    help=f"全量事实输出（默认 data/{SCRIPT_DIR_NAME}/plate_inventory.json）")
    ap.add_argument("--map-out", metavar="PATH",
                    help=f"映射表输出（默认 data/{SCRIPT_DIR_NAME}/plate_map.json）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()
    raw = args.game_package or os.environ.get(PKG_ENV)
    if not raw:
        print(f"[错误] 没给游戏包目录：用 -i PATH 或设环境变量 {PKG_ENV}", file=sys.stderr)
        return 2
    package = resolve_package(raw)
    if package is None:
        print(f"[错误] 这个目录里没有 A000/：{raw}", file=sys.stderr)
        return 2
    outdir = data_root(SCRIPT_DIR_NAME)
    out_path = Path(args.out).resolve() if args.out else outdir / "plate_inventory.json"
    map_path = Path(args.map_out).resolve() if args.map_out else outdir / "plate_map.json"
    a000 = package / "A000"
    plate_dir, group_dir = a000 / "plate", a000 / "musicGroup"
    np_dir = a000 / "AssetBundleImages" / "nameplate"

    print(BAR)
    print("牌子 / 曲组 / 贴图 · 事实清单（纯遍历，不做推断）")
    print(f"  包       : {package}")
    print(f"  牌子目录 : {plate_dir.is_dir()}")
    print(f"  曲组目录 : {group_dir.is_dir()}")
    print(f"  贴图目录 : {np_dir.is_dir()}")
    print(RULE)

    # ---- 1. 牌子 ----
    plates = []
    if plate_dir.is_dir():
        for entry in sorted(p for p in plate_dir.iterdir() if p.is_dir()):
            xml = entry / "Plate.xml"
            if not xml.is_file():
                continue
            try:
                root = ET.parse(xml).getroot()
            except ET.ParseError:
                continue
            rel = root.find("relConds")
            body = ET.tostring(rel, encoding="unicode") if rel is not None else ""
            groups = []
            if rel is not None:
                for node in rel.iter("musicGroupId"):
                    gid = (node.findtext("id") or "").strip()
                    gname = (node.findtext("str") or "").strip()
                    if gid and gid != "0":
                        groups.append({"id": gid, "name": gname})
            num = int(re.sub(r"\D", "", entry.name) or 0)
            plates.append({
                "plateId": entry.name, "plateNum": num,
                "name": txt(root, "name"), "genre": txt(root, "genre"),
                "normText": txt(root, "normText"),
                "releaseTagName": txt(root, "releaseTagName"),
                "disabled": (root.findtext("disable") or "").strip().lower() == "true",
                "versionAll": "MUSIC_VERSION_ALL" in body,
                "groups": groups,
            })

    # ---- 2. 曲组 ----
    groups = {}
    if group_dir.is_dir():
        for entry in sorted(p for p in group_dir.iterdir() if p.is_dir()):
            xml = entry / "MusicGroup.xml"
            if not xml.is_file():
                continue
            try:
                root = ET.parse(xml).getroot()
            except ET.ParseError:
                continue
            ids = sorted(int(i.findtext("id")) for i in root.iter("StringID")
                         if i.find("id") is not None and (i.findtext("id") or "").strip().isdigit())
            gid = entry.name.replace("MusicGroup", "")
            groups[gid] = {"id": gid, "name": txt(root, "name"), "count": len(ids), "internalIds": ids}

    # ---- 3. 贴图 ----
    np_ids = []
    if np_dir.is_dir():
        for entry in sorted(np_dir.iterdir()):
            m = re.fullmatch(r"ui_plate_(\d{6})\.ab", entry.name)
            if m:
                np_ids.append(int(m.group(1)))
    np_ids = sorted(set(np_ids))

    # ---- 4. 纯对照（集合运算，不含推断）----
    plate_nums = {p["plateNum"] for p in plates}
    with_groups = [p for p in plates if p["groups"]]
    np_missing_for_plate = sorted(plate_nums - set(np_ids))
    np_without_plate = sorted(set(np_ids) - plate_nums)
    ref_ids = sorted({g["id"] for p in plates for g in p["groups"]})
    unknown_refs = [g for g in ref_ids if f"{int(g):06d}" not in groups]

    counts = {
        "plates": len(plates),
        "platesWithGroups": len(with_groups),
        "platesVersionAll": sum(1 for p in plates if p["versionAll"]),
        "platesDisabled": sum(1 for p in plates if p["disabled"]),
        "groups": len(groups),
        "groupInternalIds": sum(g["count"] for g in groups.values()),
        "nameplateFiles": len(np_ids),
        "platesWithoutNameplate": len(np_missing_for_plate),
        "nameplateWithoutPlate": len(np_without_plate),
        "referencedGroupIds": len(ref_ids),
        "referencedGroupsNotFound": len(unknown_refs),
    }
    for key, value in counts.items():
        print(f"  {key:<26} {value}")

    print(RULE)
    np_set = set(np_ids)
    print(f"带 musicGroupId 的牌子（{len(with_groups)} 个）：")
    for p in with_groups:
        parts = []
        for g in p["groups"]:
            info = groups.get(f"{int(g['id']):06d}") or {}
            parts.append(f"{g['id']}「{g['name']}」[{info.get('count', '?')}首]")
        print(f"  {p['plateId']} {p['name'][:10]:<12} Ver={'Y' if p['versionAll'] else 'n'} "
              f"贴图={'Y' if p['plateNum'] in np_set else 'n'} | {p['normText'][:40]:<42} | "
              + ", ".join(parts))

    print(RULE)
    print(f"牌号没贴图的 {len(np_missing_for_plate)} 个：{np_missing_for_plate[:20]}")
    print(f"贴图没牌号的 {len(np_without_plate)} 个：{np_without_plate[:20]}")
    print(f"牌子引用了但本地没有的曲组：{unknown_refs}")
    print(f"贴图 id 范围：{np_ids[0] if np_ids else '-'} … {np_ids[-1] if np_ids else '-'}")
    print(RULE)

    # ---- 5. 显式映射表：范围内那 108 个牌 → 范围组 / 除外组 / 贴图 ----
    #  范围牌 = `MUSIC_VERSION_ALL` 的（103）+ 写了曲组且不是版本牌的（5 个舞牌）= 108
    #  版本牌自己没写曲组，范围组 = 同代里牌号最小的那个（就是「極」那个组）
    ver = [p for p in plates if p["versionAll"]]
    dance = [p for p in plates if not p["versionAll"] and p["groups"]]
    clusters: dict[str, list[int]] = {}
    for p in ver:
        clusters.setdefault(p["normText"].split("/")[0], []).append(p["plateNum"])
    base_of = {key: min(nums) for key, nums in clusters.items()}

    plate_map = []
    for p in ver:
        label = p["normText"].split("/")[0]
        base = base_of[label]
        info = groups.get(f"{base:06d}") or {}
        parts = p["normText"].split("/")
        plate_map.append({
            "plateId": p["plateId"], "plateNum": p["plateNum"], "name": p["name"], "kind": "version",
            "versionLabel": label, "condition": parts[2] if len(parts) > 2 else "",
            "difficultyRange": parts[1] if len(parts) > 1 else "",
            "normText": p["normText"], "genre": p["genre"], "releaseTagName": p["releaseTagName"],
            "rangeGroups": [{"id": f"{base:06d}", "name": info.get("name", ""),
                             "count": info.get("count", 0), "internalIds": info.get("internalIds", [])}],
            "excludedGroups": [{"id": f"{int(g['id']):06d}", "name": g["name"],
                                "count": (groups.get(f"{int(g['id']):06d}") or {}).get("count", 0),
                                "internalIds": (groups.get(f"{int(g['id']):06d}") or {}).get("internalIds", [])}
                               for g in p["groups"] if int(g["id"]) != base],
            "nameplate": f"ui_plate_{p['plateNum']:06d}.ab",
        })
    for p in dance:
        parts = p["normText"].split("/")
        plate_map.append({
            "plateId": p["plateId"], "plateNum": p["plateNum"], "name": p["name"], "kind": "dance",
            "versionLabel": parts[0] if parts else "", "condition": parts[2] if len(parts) > 2 else "",
            "difficultyRange": parts[1] if len(parts) > 1 else "",
            "normText": p["normText"], "genre": p["genre"], "releaseTagName": p["releaseTagName"],
            "rangeGroups": [{"id": f"{int(g['id']):06d}", "name": g["name"],
                             "count": (groups.get(f"{int(g['id']):06d}") or {}).get("count", 0),
                             "internalIds": (groups.get(f"{int(g['id']):06d}") or {}).get("internalIds", [])}
                            for g in p["groups"]],
            "excludedGroups": [],
            "nameplate": f"ui_plate_{p['plateNum']:06d}.ab",
        })
    for row in plate_map:                                  # 要求的 internalId 在这里一次算清（范围组 − 除外组）
        ids = {i for g in row["rangeGroups"] for i in g["internalIds"]}
        for g in row["excludedGroups"]:
            ids -= set(g["internalIds"])
        row["internalIds"] = sorted(ids)
        row["tier"] = next((t for t in ("極", "将", "神", "舞舞") if row["name"].endswith(t)), row["name"])

    print(f"显式映射表（{len(plate_map)} 行）按版本列：")
    by_label: dict[str, list[dict]] = {}
    for row in plate_map:
        by_label.setdefault(row["versionLabel"], []).append(row)
    for label, rows in by_label.items():
        gid = rows[0]["rangeGroups"][0]["id"]
        ginfo = rows[0]["rangeGroups"][0]
        excl = sorted({g["id"] for r in rows for g in r["excludedGroups"]})
        tiers = " ".join(f"{r['name']}({r['plateNum']})" for r in sorted(rows, key=lambda x: x["plateNum"]))
        print(f"  {label[:26]:<28} {tiers:<52} 范围组 {gid}「{ginfo.get('name','')}」"
              f"[{ginfo.get('count',0)}首] 除外 {excl or '—'}")
    print(RULE)

    out = {"schema": 1, "runId": RUN_ID, "kind": "plate-inventory",
           "source": "本地游戏包（路径不入产物）", "counts": counts,
           "plates": plates,
           "groups": {k: {"id": v["id"], "name": v["name"], "count": v["count"]} for k, v in groups.items()},
           "groupInternalIds": {k: v["internalIds"] for k, v in groups.items()},
           "plateMap": plate_map,
           "nameplateIds": np_ids,
           "platesWithoutNameplate": np_missing_for_plate,
           "nameplateWithoutPlate": np_without_plate,
           "referencedGroupsNotFound": unknown_refs}
    map_doc = {
        "schema": 1, "runId": RUN_ID,
        "note": "牌子 → 范围组/除外组/贴图 的显式映射表（tools/plate_inventory 从本地游戏包生成，"
                "人工可核可改；tools/plate_conditions 只读这张表，不自己遍历推断）",
        "source": "本地游戏包（路径不入产物）",
        "counts": {"rows": len(plate_map),
                   "versionRows": len(ver), "danceRows": len(dance),
                   "versions": len({r["versionLabel"] for r in plate_map if r["kind"] == "version"}),
                   "internalIds": sum(len(r["internalIds"]) for r in plate_map)},
        "plates": plate_map,
    }
    outdir.mkdir(parents=True, exist_ok=True)
    for target, doc in ((out_path, out), (map_path, map_doc)):
        tmp = target.with_name(target.name + ".part")
        try:
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
    print(f"完整事实 : {out_path}")
    print(f"映射表   : {map_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
