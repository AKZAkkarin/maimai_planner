# 用途：把本地曲库里每首曲的曲绘解成 PNG（A000/AssetBundleImages/jacket/ui_jacket_<曲绘号>.ab → data/image/cover/）
# 状态：现行
"""曲绘转换：任务默认由**本地曲库**决定（`--all` 时改成"包里所有曲绘"），解包交给 tools/unpack_ab/。

【任务从哪来】
  默认：读 `data/localMusicData_build/localMusicData-*.json`（本地曲库，上一次读游戏包得到的），
    每首曲的 `dataid` 就是曲绘号 → 曲绘包 = `<包目录>/AssetBundleImages/jacket/ui_jacket_<dataid:06d>.ab`。
    同一个曲绘号（同一首歌的 std / dx 两族）只解一次，不去重就会重复解同一张。
    本地曲库里**没有**曲绘字段，所以任务只能这样推 —— 推出来的包在游戏包里找不到时记进 `missing[]`，不猜。
  `--all`：不看曲库，把**游戏包里所有可能的曲绘**（各包 `ui_jacket_*.ab` 并集，编号相同取后加载的包）
    都解出来 —— 完整构建（`full`）与换包（`afterGameUpdate`）用这个模式，免得曲库没跟上时漏图解。

【输入可以是包根，也可以是单个包目录】
  给 `…/StreamingAssets`（包根）：按**游戏加载顺序**读所有包 —— 包名升序、**大的覆盖小的**
    （见 docs/notes.md §六；与 localMusicData_build 的 load_packages 同一套规则）。
  给 `…/StreamingAssets/A003`（单个包目录）：只处理这一个目录 —— 数据小更新（`smallUpdate`）用它，
    新包进来时只解这个文件夹里的新曲绘。

【怎么解】脚本之间不 import：这里把清单写成文件，subprocess 调
  `python tools/unpack_ab/unpack_ab.py <jacket 目录> --only <清单> --obj-name-only -o data/image/cover`
  （unpack_ab 保持它通用解包器的本色；解出来放哪、叫什么由本脚本决定）

【输出】data/image/cover/
  UI_Jacket_<6位>.png    每首曲一张（同一曲绘号只一张）
  jacket-files.txt       本次要解的 .ab 清单（一行一个）
  unpack.log.<包名>       unpack_ab 的原样输出（受限沙箱不能走管道，只能重定向到文件）
  cover_build.json       本脚本的产物：runId / 模式 / 计数 / 曲绘号→PNG 对照 / 缺哪些
  **素材仅本地用**（版权属 SEGA），不进仓库、不外发；产物里不记本机绝对路径。

【用法】
    python tools/cover_build/cover_build.py -i <游戏包根 或 单个包目录> [选项]
      -i, --game-package PATH  含 A000/ 的包根，或单个包目录（也可用环境变量 MAIMAI_STREAMING_ASSETS）
      --all                    解包里所有曲绘（不看曲库）
      --local-dir PATH         本地曲库目录（默认 data/localMusicData_build/；--all 时只用来查曲名）
      -o, --outdir PATH        输出目录（默认 data/image/cover/）
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

SCRIPT_DIR_NAME = "cover_build"
UNPACK_SCRIPT = PROJECT_DIR / "tools" / "unpack_ab" / "unpack_ab.py"
LOCAL_SCRIPT_DIR = "localMusicData_build"
JACKET_REL = ("AssetBundleImages", "jacket")               # 各包目录下的相对路径（包名由 load_packages 拼）
FAMILY_ORDER = ("dx", "std", "utage", "utage2p")
LOCAL_FILE = "localMusicData-{family}.json"
OUT_NAME = "cover_build.json"
LIST_NAME = "jacket-files.txt"
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
    """给**游戏包根**（含 A000…）或**单个包目录**（如 …/StreamingAssets/A003）都认。

    返回 (包根, [(包名, 曲绘目录)…])，顺序就是游戏加载顺序（包名升序、大的覆盖小的，见 notes §六）。
    只给单个包目录时只处理它自己 —— 数据小更新就是这么用的。
    """
    base = Path(raw).expanduser().resolve()
    if not base.is_dir():
        return None
    for cand in (base, base / "StreamingAssets", base / "Sinmai_Data" / "StreamingAssets"):
        if (cand / "A000").is_dir():
            return cand, load_packages(cand, Path(*JACKET_REL))
    single = base / Path(*JACKET_REL)
    if single.is_dir():
        return base.parent, [(base.name, single)]
    return None


def load_packages(base: Path, rel: Path) -> list[tuple[str, Path]]:
    """按**游戏加载顺序**（包名升序）列出各包目录下的 rel，后加载的包覆盖先加载的。

    规则见 docs/notes.md §六：StreamingAssets 下是多包（A000 / A003 / A997 / A999 / N100），
    包名一律定宽、按字符串升序 == 数字升序，引擎按这个顺序加载、**大的覆盖小的**。
    与 tools/localMusicData_build 的 load_packages / merge_packages 同一套规则。
    """
    found: list[tuple[str, Path]] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        target = entry / rel
        if target.is_dir():
            found.append((entry.name, target))
    return found


def collect_tasks(local_dir: Path) -> tuple[dict, int, list]:
    """本地曲库 → {曲绘号: {titles, families, songs}}（同一曲绘号只留一条）。"""
    tasks: dict[int, dict] = {}
    songs = 0
    no_dataid: list[dict] = []
    for family in FAMILY_ORDER:
        path = local_dir / LOCAL_FILE.format(family=family)
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        for song in doc.get("songs") or []:
            songs += 1
            dataid = song.get("dataid")
            if dataid is None:
                no_dataid.append({"type": family, "title": song.get("title"),
                                  "why": "这首本地曲没有 dataid（曲绘号）"})
                continue
            slot = tasks.setdefault(int(dataid), {"dataid": int(dataid), "titles": [], "families": []})
            if song.get("title") and song["title"] not in slot["titles"]:
                slot["titles"].append(song["title"])
            if family not in slot["families"]:
                slot["families"].append(family)
    return tasks, songs, no_dataid


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(prog="cover_build.py",
                                 description="本地曲库 → 曲绘包清单 → 解成 PNG（data/image/cover/）")
    ap.add_argument("-i", "--game-package", metavar="PATH",
                    help=f"含 A000/ 的包根，或单个包目录（也可用环境变量 {PKG_ENV}）")
    ap.add_argument("--all", dest="all_jackets", action="store_true",
                    help="不看曲库，解包里所有曲绘（完整构建 / 换包用）")
    ap.add_argument("--local-dir", metavar="PATH",
                    help=f"本地曲库目录（默认 data/{LOCAL_SCRIPT_DIR}/）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="输出目录（默认 data/image/cover/）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    raw = args.game_package or os.environ.get(PKG_ENV)
    if not raw:
        print(f"[错误] 没给游戏包目录：用 -i PATH 或设环境变量 {PKG_ENV}", file=sys.stderr)
        return 2
    package = resolve_targets(raw)
    if package is None:
        print(f"[错误] 这个目录既不是含 A000/ 的包根，也不是包目录（要有 "
              f"{'/'.join(JACKET_REL)}/）：{raw}", file=sys.stderr)
        return 2
    package, jacket_dirs = package
    if not jacket_dirs:
        print(f"[错误] 哪个包目录下都没有曲绘目录：{'/'.join(JACKET_REL)}", file=sys.stderr)
        return 2

    local_dir = Path(args.local_dir).resolve() if args.local_dir else data_root(LOCAL_SCRIPT_DIR)
    outdir = (Path(args.outdir).resolve() if args.outdir
              else data_root("image") / "cover")            # data/image/cover/（用户指定）

    print(BAR)
    print("cover_build · 曲绘转换（本地曲库定任务 → 解 jacket）")
    print(f"  本地曲库 : {local_dir}")
    print(f"  曲绘目录 : " + "  ".join(f"{n}" for n, _ in jacket_dirs) + "（按加载顺序，大的覆盖小的）")
    print(f"  输出     : {outdir}")
    print(RULE)

    mode = "all" if args.all_jackets else "library"
    tasks: dict = {}
    songs, no_dataid = 0, []
    if args.all_jackets:
        if local_dir.is_dir():                             # --all 不看曲库，能读就读来查曲名
            try:
                tasks, songs, no_dataid = collect_tasks(local_dir)
            except (OSError, ValueError):
                tasks, songs, no_dataid = {}, 0, []
    else:
        try:
            tasks, songs, no_dataid = collect_tasks(local_dir)
        except (OSError, ValueError) as exc:
            print(f"[错误] 读本地曲库失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    # 按包名升序收各包的曲绘：编号相同的取**后加载**的那个包（大的覆盖小的）
    jackets: dict[int, Path] = {}
    per_package: dict[str, int] = {}
    for pkg_name, dir_path in jacket_dirs:
        n = 0
        for ab in sorted(dir_path.glob("ui_jacket_*.ab")):
            digits = "".join(ch for ch in ab.stem if ch.isdigit())
            if not digits:
                continue
            jackets[int(digits)] = ab
            n += 1
        per_package[pkg_name] = n

    by_dir: dict[Path, list[str]] = {}
    missing: list[str] = []
    if args.all_jackets:
        target_ids = sorted(jackets)                       # 包里所有可能的曲绘
    else:
        target_ids = [d for d in sorted(tasks) if d in jackets]
        missing = [f"ui_jacket_{d:06d}.ab" for d in sorted(tasks) if d not in jackets]
    for dataid in target_ids:
        ab = jackets[dataid]
        by_dir.setdefault(ab.parent, []).append(ab.name)
    have = sum(len(names) for names in by_dir.values())
    print(f"模式     : " + ("--all：包里所有曲绘（不看曲库）" if args.all_jackets
                           else f"按本地曲库出任务（{songs:,} 首 → 曲绘号 {len(tasks):,} 个）"))
    print(f"曲绘包   : " + "  ".join(f"{n}({c})" for n, c in per_package.items())
          + f" → 覆盖后取到 {have:,} 个" + (f"，缺 {len(missing):,} 个" if missing else ""))

    if not have:
        print("[错误] 一个曲绘包都没找到，停。", file=sys.stderr)
        return 2
    if not UNPACK_SCRIPT.is_file():
        print(f"[错误] 找不到解包脚本：{UNPACK_SCRIPT}", file=sys.stderr)
        return 2

    outdir.mkdir(parents=True, exist_ok=True)
    list_path = outdir / LIST_NAME
    list_path.write_text("\n".join(n for names in by_dir.values() for n in names) + "\n", encoding="utf-8")

    print(RULE)
    print(f"解包（unpack_ab --only，{have:,} 个，分布在 {len(by_dir)} 个包目录）…")
    failed: list[str] = []
    logs: list[str] = []
    last_tail: list[str] = []
    for pkg_name, dir_path in jacket_dirs:
        names = by_dir.get(dir_path)
        if not names:
            continue
        sub_list = outdir / f"{LIST_NAME}.{pkg_name}"
        sub_list.write_text("\n".join(names) + "\n", encoding="utf-8")
        print(f"  [{pkg_name}] {len(names)} 个 …")
        cmd = [sys.executable, str(UNPACK_SCRIPT), str(dir_path),
               "--only", str(sub_list), "--obj-name-only", "-o", str(outdir)]
        log_path = outdir / f"{LOG_NAME}.{pkg_name}"
        logs.append(log_path.name)
        with log_path.open("w", encoding="utf-8") as log:      # 受限沙箱不能走管道，只能重定向到文件
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                  cwd=str(PROJECT_DIR), check=False)
        last_tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-4:]
        if proc.returncode != 0:
            failed.append(f"{pkg_name}（退出码 {proc.returncode}）")
        sub_list.unlink(missing_ok=True)
    for line in last_tail:
        print("  | " + line)

    # 文件名统一成 UI_Jacket_<6位>.png（unpack_ab 按对象名落地，有些包是小写 ui_jacket_…，
    # 服务端 cover_ids() 以前只认大写前缀，会漏图）
    for png in sorted(outdir.glob("*.png")):
        digits = "".join(ch for ch in png.stem if ch.isdigit())
        if not digits:
            continue
        target = outdir / ("UI_Jacket_%06d.png" % int(digits))
        if png.name != target.name and not target.exists():
            try:
                png.rename(target)
            except OSError:
                pass
    pngs = sorted(p for p in outdir.glob("*.png"))
    wanted = set(target_ids)
    covers, unmatched_png = {}, []
    for png in pngs:
        digits = "".join(ch for ch in png.stem if ch.isdigit())
        if digits and int(digits) in wanted:
            covers[int(digits)] = png.name
        else:
            unmatched_png.append(png.name)
    total_bytes = sum(p.stat().st_size for p in pngs)
    not_extracted = [i for i in target_ids if i not in covers]

    payload = {
        "schema": 1, "runId": RUN_ID,
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800"),
        "kind": "cover",
        "mode": mode,
        "sources": {"localDir": f"data/{LOCAL_SCRIPT_DIR}",
                    "jacketDir": "/".join(JACKET_REL),
                    "packages": [n for n, _ in jacket_dirs],
                    "unpackScript": "tools/unpack_ab/unpack_ab.py"},
        "counts": {"songs": songs, "coverIds": len(tasks), "inPackage": have,
                   "missingInPackage": len(missing), "png": len(pngs),
                   "extracted": len(covers), "notExtracted": len(not_extracted),
                   "extractedThisRun": have, "targets": len(target_ids),
                   "bytes": total_bytes, "songsWithoutDataid": len(no_dataid),
                   "byPackage": per_package},
        "covers": {str(k): covers[k] for k in sorted(covers)},
        "missingInPackage": missing,
        "notExtracted": not_extracted,
        "songsWithoutDataid": no_dataid,
        "unmatchedPng": unmatched_png,
        "list": LIST_NAME, "logs": logs,
    }
    try:
        tmp = outdir / (OUT_NAME + ".part")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(outdir / OUT_NAME)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[错误] 写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(RULE)
    print(f"本次 : 解了 {have:,} 个曲绘包（分布在 {len(by_dir)} 个包目录）"
          + (f"，其中 {len(not_extracted):,} 个没解出图 ★" if not_extracted else ""))
    print(f"目录 : PNG {len(pngs):,} 张（{total_bytes / 1024 / 1024:.1f} MB）"
          f"，本次对上曲绘号 {len(covers):,} 个")
    if missing:
        print(f"       游戏包里缺 {len(missing):,} 个曲绘包（见 {OUT_NAME} 的 missingInPackage）")
    if unmatched_png:
        print(f"       ★ {len(unmatched_png):,} 张 PNG 对不上任何曲绘号（见 unmatchedPng）")
    print("抽查")
    for dataid in sorted(covers)[:3]:
        titles = (tasks.get(dataid) or {}).get("titles") or [""]
        print(f"  曲绘号 {dataid:<7} → {covers[dataid]:<24} {str(titles[0])[:26]}")
    print(RULE)
    print(f"完成 : {outdir / OUT_NAME}")
    if failed:
        print(f"[错误] unpack_ab 失败：" + "、".join(failed) + f"（原样输出在 {LOG_NAME}.<包名>）",
              file=sys.stderr)
        return 4
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
