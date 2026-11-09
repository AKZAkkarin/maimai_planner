# 用途：把游戏包里的牌子贴图（A000/AssetBundleImages/nameplate/ui_plate_*.ab）解成 PNG → data/image/nameplate/
# 状态：现行
"""牌子贴图转换：任务来自**本地游戏包**（包里有几个 ui_plate_*.ab 就解几个），解包交给 tools/unpack_ab/。

【任务从哪来】
  扫各包目录下的 `AssetBundleImages/nameplate/ui_plate_<6位>.ab`（见 docs/notes.md §六 的加载顺序：
  包名升序，后加载的覆盖先加载的，编号相同取后面的包）。
  编号就是牌子 id（`plate_inventory` 里的 `nameplateIds` 同一套：6101 → ui_plate_006101.ab）。
  有 `data/plate_inventory/plate_inventory.json` 时只用它登记过的 nameplateIds（避免解到无关文件），
  没有就退化成「包里所有 ui_plate_*.ab」。

【输入可以是包根，也可以是单个包目录】
  给 `…/StreamingAssets`（包根）：按游戏加载顺序读所有包。
  给 `…/StreamingAssets/A000`（单个包目录）：只处理这一个。

【怎么解】脚本之间不 import：把清单写成文件，subprocess 调
  `python tools/unpack_ab/unpack_ab.py <nameplate 目录> --only <清单> --obj-name-only -o data/image/nameplate`
  （--obj-name-only 出来的文件名就是 Unity 对象名，正好是 `UI_Plate_006101.png`）

【输出】data/image/nameplate/
  UI_Plate_<6位>.png       每块牌一张（720×116 这种宽条）
  nameplate-files.txt      本次要解的 .ab 清单（一行一个）
  unpack.log.<包名>        unpack_ab 的原样输出（受限沙箱不能走管道，只能重定向到文件）
  nameplate_build.json     本脚本的产物：runId / 计数 / 牌子号→PNG 对照 / 缺哪些
  **素材仅本地用**（版权属 SEGA），不进仓库、不外发；产物里不记本机绝对路径。

【用法】
    python tools/nameplate_build/nameplate_build.py -i <游戏包根 或 单个包目录> [选项]
      -i, --game-package PATH  含 A000/ 的包根，或单个包目录（也可用环境变量 MAIMAI_STREAMING_ASSETS）
      -o, --outdir PATH        输出目录（默认 data/image/nameplate/）
      --all                    不看 plate_inventory，解包里所有 ui_plate_*.ab
    退出码：0 成功；2 输入读不出/写盘失败；4 解包失败（有 .ab 解不开）。

依赖：本脚本只用标准库；真正的解包依赖 unpack_ab 的 UnityPy + Pillow。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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

SCRIPT_DIR_NAME = "nameplate_build"
UNPACK_SCRIPT = PROJECT_DIR / "tools" / "unpack_ab" / "unpack_ab.py"
INVENTORY_SCRIPT_DIR = "plate_inventory"
INVENTORY_FILE = "plate_inventory.json"
NAMEPLATE_REL = ("AssetBundleImages", "nameplate")
OUT_NAME = "nameplate_build.json"
LIST_NAME = "nameplate-files.txt"
LOG_NAME = "unpack.log"

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


def resolve_targets(raw: str) -> tuple[Path, list[tuple[str, Path]]] | None:
    """给**游戏包根**（含 A000…）或**单个包目录**（如 …/StreamingAssets/A000）都认。"""
    base = Path(raw).expanduser().resolve()
    if not base.is_dir():
        return None
    for cand in (base, base / "StreamingAssets", base / "Sinmai_Data" / "StreamingAssets"):
        if (cand / "A000").is_dir():
            return cand, load_packages(cand, Path(*NAMEPLATE_REL))
    single = base / Path(*NAMEPLATE_REL)
    if single.is_dir():
        return base.parent, [(base.name, single)]
    return None


def load_packages(base: Path, rel: Path) -> list[tuple[str, Path]]:
    """按游戏加载顺序（包名升序）列出各包目录下的 rel，后加载的覆盖先加载的。"""
    found: list[tuple[str, Path]] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        target = entry / rel
        if target.is_dir():
            found.append((entry.name, target))
    return found


def inventory_nameplate_ids(inventory_dir: Path) -> set[int] | None:
    """要解的牌子贴图号：优先 plates.json（曲库真正用到的牌子），其次 plate_inventory 的 nameplateIds。

    牌子表里登记了 575 个牌子贴图，但项目里真正用到的是 plates.json 那 108 块 —— 只解这些。
    """
    plates_path = PROJECT_DIR / "data" / "plate_conditions" / "plates.json"
    if plates_path.is_file():
        try:
            doc = json.loads(plates_path.read_text(encoding="utf-8-sig"))
            nums = [int(p["plateNum"]) for p in doc.get("plates") or [] if p.get("plateNum") is not None]
            if nums:
                return set(nums)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    path = inventory_dir / INVENTORY_FILE
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    ids = doc.get("nameplateIds")
    if not isinstance(ids, list) or not ids:
        return None
    return {int(x) for x in ids if isinstance(x, int) or str(x).isdigit()}


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(prog="nameplate_build.py",
                                 description="游戏包里的牌子贴图 → PNG（data/image/nameplate/）")
    ap.add_argument("-i", "--game-package", metavar="PATH",
                    help=f"含 A000/ 的包根，或单个包目录（也可用环境变量 {PKG_ENV}）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="输出目录（默认 data/image/nameplate/）")
    ap.add_argument("--all", dest="all_files", action="store_true",
                    help="不看 plate_inventory，解包里所有 ui_plate_*.ab")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    raw = args.game_package or os.environ.get(PKG_ENV)
    if not raw:
        print(f"[错误] 没给游戏包目录：用 -i PATH 或设环境变量 {PKG_ENV}", file=sys.stderr)
        return 2
    package = resolve_targets(raw)
    if package is None:
        print(f"[错误] 这个目录既不是含 A000/ 的包根，也不是包目录（要有 "
              f"{'/'.join(NAMEPLATE_REL)}/）：{raw}", file=sys.stderr)
        return 2
    package, plate_dirs = package
    if not plate_dirs:
        print(f"[错误] 哪个包目录下都没有牌子贴图目录：{'/'.join(NAMEPLATE_REL)}", file=sys.stderr)
        return 2

    outdir = (Path(args.outdir).resolve() if args.outdir
              else data_root("image") / "nameplate")          # data/image/nameplate/
    inventory_dir = data_root(INVENTORY_SCRIPT_DIR)
    wanted = None if args.all_files else inventory_nameplate_ids(inventory_dir)

    print(BAR)
    print("nameplate_build · 牌子贴图转换（游戏包 → PNG）")
    print(f"  牌子目录 : " + "  ".join(n for n, _ in plate_dirs) + "（按加载顺序，大的覆盖小的）")
    print(f"  输出     : {outdir}")
    print(f"  任务来源 : " + ("包里所有 ui_plate_*.ab（--all）" if wanted is None
                              else f"plates.json 用到的牌子（{len(wanted):,} 个）"))
    print(RULE)

    plates: dict[int, Path] = {}
    per_package: dict[str, int] = {}
    for pkg_name, dir_path in plate_dirs:
        n = 0
        for ab in sorted(dir_path.glob("ui_plate_*.ab")):
            digits = "".join(ch for ch in ab.stem if ch.isdigit())
            if not digits:
                continue
            num = int(digits)
            if wanted is not None and num not in wanted:
                continue
            plates[num] = ab
            n += 1
        per_package[pkg_name] = n

    target_ids = sorted(plates)
    have = len(target_ids)
    missing = sorted(wanted - set(plates)) if wanted is not None else []
    print(f"牌子包   : " + "  ".join(f"{n}({c})" for n, c in per_package.items())
          + f" → 取到 {have:,} 个" + (f"，缺 {len(missing):,} 个" if missing else ""))
    if not have:
        print("[错误] 一个牌子贴图包都没找到，停。", file=sys.stderr)
        return 2
    if not UNPACK_SCRIPT.is_file():
        print(f"[错误] 找不到解包脚本：{UNPACK_SCRIPT}", file=sys.stderr)
        return 2

    outdir.mkdir(parents=True, exist_ok=True)

    by_dir: dict[Path, list[str]] = {}
    for num in target_ids:
        ab = plates[num]
        by_dir.setdefault(ab.parent, []).append(ab.name)
    (outdir / LIST_NAME).write_text(
        "\n".join(n for names in by_dir.values() for n in names) + "\n", encoding="utf-8")

    print(RULE)
    print(f"解包（unpack_ab --only，{have:,} 个，分布在 {len(by_dir)} 个包目录）…")
    failed: list[str] = []
    last_tail: list[str] = []
    for pkg_name, dir_path in plate_dirs:
        names = by_dir.get(dir_path)
        if not names:
            continue
        sub_list = outdir / f"{LIST_NAME}.{pkg_name}"
        sub_list.write_text("\n".join(names) + "\n", encoding="utf-8")
        print(f"  [{pkg_name}] {len(names)} 个 …")
        cmd = [sys.executable, str(UNPACK_SCRIPT), str(dir_path),
               "--only", str(sub_list), "--obj-name-only", "-o", str(outdir)]
        log_path = outdir / f"{LOG_NAME}.{pkg_name}"
        with log_path.open("w", encoding="utf-8") as log:      # 受限沙箱不能走管道，只能重定向到文件
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                  cwd=str(PROJECT_DIR), check=False)
        last_tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-4:]
        if proc.returncode != 0:
            failed.append(f"{pkg_name}（退出码 {proc.returncode}）")
        sub_list.unlink(missing_ok=True)
    for line in last_tail:
        print("  | " + line)

    # 只保留 plates.json 里用到的牌子贴图（--all 时不清）
    if wanted is not None and not args.all_files:
        for stale in list(outdir.glob("*.png")):
            digits = "".join(ch for ch in stale.stem if ch.isdigit())
            if digits and int(digits) not in wanted:
                try:
                    stale.unlink()
                except OSError:
                    pass
    pngs = sorted(outdir.glob("*.png"))
    found: dict[int, str] = {}
    for png in pngs:
        digits = "".join(ch for ch in png.stem if ch.isdigit())
        if digits and int(digits) in plates:
            found[int(digits)] = png.name
    not_extracted = [i for i in target_ids if i not in found]
    total_bytes = sum(p.stat().st_size for p in pngs)

    payload = {
        "schema": 1, "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "kind": "nameplate",
        "sources": {"plateDir": "/".join(NAMEPLATE_REL),
                    "packages": [n for n, _ in plate_dirs],
                    "inventory": (f"data/{INVENTORY_SCRIPT_DIR}/{INVENTORY_FILE}" if wanted is not None else None),
                    "unpackScript": "tools/unpack_ab/unpack_ab.py"},
        "counts": {"targets": len(target_ids), "png": len(pngs), "extracted": len(found),
                   "notExtracted": len(not_extracted), "missingInPackage": len(missing),
                   "bytes": total_bytes, "byPackage": per_package},
        "nameplates": {str(k): found[k] for k in sorted(found)},
        "missingInPackage": [f"ui_plate_{i:06d}.ab" for i in missing],
        "notExtracted": [f"ui_plate_{i:06d}.ab" for i in not_extracted],
        "failed": failed,
    }
    (outdir / OUT_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")

    print(RULE)
    print("完成")
    print(f"  牌子贴图   : 取到 {have:,} 个，解出 {len(found):,} 张 PNG"
          + (f"，没解出来 {len(not_extracted):,} 个" if not_extracted else ""))
    print(f"  目录       : {outdir}")
    if failed:
        print(f"  [失败] 解包：" + "；".join(failed), file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
