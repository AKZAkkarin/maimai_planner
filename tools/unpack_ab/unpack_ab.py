# 用途：解包指定文件夹里的 UnityFS(.ab) 资源包，默认只导出 Texture2D 为 PNG（--sprites 时附带 Sprite）
# 状态：现行
"""批量解包 .ab（Unity AssetBundle / UnityFS，明文未加密）里的图片对象。

【数据归属 · 先读这条，别误解】
  * 本文件是「构建脚本」，仓库里只留脚本。**游戏素材本体（.ab）与解出来的
    PNG（曲绘 / 牌子贴图）都不进仓库、不进任何外发内容** —— SEGA 版权 + 隐私红线。
  * 曲库、贴图一律由「本机游戏数据」现场构建：人在网页前端选一次游戏文件夹
    （或命令行传 <输入文件夹>），脚本临时读它、生成 data/ 里的库，然后就与源文件无关了。
    所以代码里**不记录、不写死**任何游戏包路径，也不落配置文件。
  * 输入是什么、输出放哪，全部由命令行参数或后端调用当场给 —— 整个
    maimai-planner/ 文件夹搬到别处也照样能跑。

用法：
    python tools/unpack_ab/unpack_ab.py <输入文件夹> [-o <输出文件夹>] [--recursive] [--obj-name-only] [--sprites]
    # 典型：由后端（server/）把前端选中的文件夹交给它，输出到 data/jackets/

行为：
  * 默认只扫输入文件夹「当前层」的 *.ab（自动跳过 *.ab.manifest）；
    --recursive 时递归所有子文件夹，并在输出目录保留相对子目录结构。
  * 每个 .ab：UnityPy.load() → 遍历 env.objects → 默认取 Texture2D → data.image.save(png)；
    --sprites 时改为取 Texture2D 与 Sprite（同一张图在包里常同时存在两种对象，
    像素逐字节相同，所以默认只导一份，避免冗余）。
  * 默认输出目录 <输入文件夹>/_unpacked（-o 可改）。
  * 文件名默认 <ab文件名去掉.ab>__<对象名>.png；同名自动加 _2 / _3；
    --obj-name-only 则只用 <对象名>.png（与参考脚本口径一致）。
  * 单个 .ab 失败不中断整体：记录错误继续跑，结尾汇总成功/失败数与失败清单。
  * 结尾打印每个产物的尺寸（宽×高）。

依赖：UnityPy>=1.25 + Pillow（.image 解码由 Pillow 提供）
      安装：<python> -m pip install UnityPy Pillow
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

EXPORT_TYPES = ("Texture2D",)                 # 默认只导 Texture2D
EXPORT_TYPES_WITH_SPRITES = ("Texture2D", "Sprite")   # --sprites：保留原「两种都导」的能力
INSTALL_HINT = (
    "缺少依赖 UnityPy（或 Pillow）。请先安装：\n"
    '    python -m pip install UnityPy Pillow\n'
    "（.ab 的图片解码依赖 Pillow；UnityPy 1.25.x 会自带 lz4 解压支持）"
)

# Windows 文件名非法字符
_ILLEGAL = set('<>:"/\\|?*')


def _force_utf8_when_piped() -> None:
    """stdout/stderr 被重定向（管道/文件）时，Python 用系统本地编码(cp936)输出，
    会让 UTF-8 侧的消费者（bash / 日志 / CI）看到乱码。这里只在「非终端」时切成
    UTF-8；真正的交互式控制台保持原样（Python 会用 WriteConsoleW 正常显示中文）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty() and (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def sanitize(name: str) -> str:
    """把对象名清洗成安全的文件名片段。"""
    out = "".join("_" if c in _ILLEGAL or ord(c) < 32 else c for c in name)
    out = out.strip().rstrip(".")          # Windows 不允许结尾是空格或点
    return out or "unnamed"


def object_name(data, obj) -> str:
    """取对象名。UnityPy 1.25.x 用 m_Name；旧版用 name —— 两者都兼容。

    对象名为空时回退到 path_id，避免产出 '.png' 这种无主文件。
    """
    for attr in ("m_Name", "name"):
        val = getattr(data, attr, None)
        if val:
            return str(val)
    return f"unnamed_{obj.path_id}"


def unique_path(out_dir: Path, stem: str, used: set) -> Path:
    """在 out_dir 里给 stem 找一个不冲突的 .png 路径（_2/_3...），并登记。

    used 按小写比较：Windows 文件系统大小写不敏感，'A.png' 与 'a.png' 会互相覆盖。
    """
    cand = f"{stem}.png"
    i = 2
    while cand.lower() in used:
        cand = f"{stem}_{i}.png"
        i += 1
    used.add(cand.lower())
    return out_dir / cand


def iter_ab_files(in_dir: Path, recursive: bool):
    """产出 (ab路径, 相对子目录)。跳过 *.ab.manifest。"""
    pattern = "**/*" if recursive else "*"
    for p in sorted(in_dir.glob(pattern)):
        if not p.is_file():
            continue
        if p.name.lower().endswith(".ab.manifest"):   # 先排除 manifest
            continue
        if p.suffix.lower() != ".ab":
            continue
        yield p, p.parent.relative_to(in_dir)


def unpack_one(ab_path: Path, out_dir: Path, obj_name_only: bool, export_types, up) -> dict:
    """解一个 .ab。返回 {'files','errors','objects','total_objects'}。

    export_types：本次要导出的对象类型名集合（由调用方决定，不在此硬编码常量）。
    """
    res = {"files": [], "errors": [], "objects": 0, "total_objects": 0}
    out_dir.mkdir(parents=True, exist_ok=True)
    used: set = set()          # 每个 .ab 独立去重（同名 ab 名已由前缀区分）
    stem_prefix = ab_path.stem  # 'ui_jacket_000018.ab' -> 'ui_jacket_000018'

    env = up.load(str(ab_path))                      # 懒加载，真正报错多发生在 read()
    objs = list(env.objects)
    res["total_objects"] = len(objs)
    for obj in objs:
        if obj.type.name not in export_types:
            continue
        res["objects"] += 1
        tag = f"{ab_path.name} :: {obj.type.name}(path_id={obj.path_id})"
        try:
            data = obj.read()
            name = object_name(data, obj)
            img = data.image                          # PIL.Image
            stem = sanitize(name) if obj_name_only else sanitize(f"{stem_prefix}__{name}")
            dst = unique_path(out_dir, stem, used)
            img.save(dst, format="PNG")
            res["files"].append((dst, img.size[0], img.size[1]))
        except Exception as exc:                      # 单对象失败不影响同包其它对象
            res["errors"].append(f"{tag} -> {type(exc).__name__}: {exc}")
    return res


def main(argv=None) -> int:
    _force_utf8_when_piped()
    ap = argparse.ArgumentParser(
        description="批量把 .ab(UnityFS) 里的 Texture2D 导出为 PNG（--sprites 时同时导出 Sprite）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input_dir", help="存放 .ab 的输入文件夹")
    ap.add_argument("-o", "--output-dir", default=None,
                    help="输出文件夹（默认 <输入文件夹>/_unpacked）")
    ap.add_argument("--recursive", action="store_true",
                    help="递归扫描所有子文件夹，并在输出目录保留相对结构")
    ap.add_argument("--obj-name-only", action="store_true",
                    help="文件名只用对象名（默认 <ab名>__<对象名>，防止跨 ab 覆盖）")
    ap.add_argument("--sprites", action="store_true",
                    help="同时导出 Sprite（默认只导 Texture2D；两者像素通常逐字节相同，"
                         "开启后会多出一份重复 PNG）")
    ap.add_argument("--only", metavar="FILE",
                    help="只解这个清单里的 .ab（一行一个：相对 <输入文件夹> 或绝对路径），"
                         "不在输入文件夹里的平铺到输出根；用于「只要某几个资源」的场合")
    args = ap.parse_args(argv)

    export_types = EXPORT_TYPES_WITH_SPRITES if args.sprites else EXPORT_TYPES

    in_dir = Path(args.input_dir).expanduser().resolve()
    if not in_dir.is_dir():
        print(f"[错误] 输入文件夹不存在或不是文件夹：{in_dir}", file=sys.stderr)
        return 2

    out_root = (Path(args.output_dir).expanduser().resolve()
                if args.output_dir else in_dir / "_unpacked")

    try:
        import UnityPy as up
    except ImportError as exc:
        print(INSTALL_HINT, file=sys.stderr)
        print(f"\n实际错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"当前解释器：{sys.executable}", file=sys.stderr)
        print(f"sys.path 前 4 项：{sys.path[:4]}", file=sys.stderr)
        return 3
    try:
        from PIL import Image  # noqa: F401  (UnityPy 的 .image 依赖它)
    except ImportError as exc:
        print(INSTALL_HINT, file=sys.stderr)
        print(f"\n实际错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"当前解释器：{sys.executable}", file=sys.stderr)
        return 3

    only_files = None
    if args.only:
        listed = [line.strip() for line in Path(args.only).read_text(encoding="utf-8-sig").splitlines()
                  if line.strip()]
        only_files, absent = [], []
        for item in listed:
            path = Path(item)
            path = path if path.is_absolute() else (in_dir / path)
            if not path.is_file():
                absent.append(path)
                continue
            path = path.resolve()
            try:
                rel_dir = path.parent.relative_to(in_dir)
            except ValueError:
                rel_dir = Path(".")                       # 不在输入文件夹里的：平铺到输出根
            only_files.append((path, rel_dir))
        if absent:
            print(f"[注意] 清单里 {len(absent)} 个文件找不到，已跳过：{[p.name for p in absent[:3]]}",
                  file=sys.stderr)

    ab_files = only_files if only_files is not None else list(iter_ab_files(in_dir, args.recursive))
    print(f"输入：{in_dir}")
    print(f"输出：{out_root}")
    print(f"模式：{'清单（--only）' if only_files is not None else ('递归子文件夹' if args.recursive else '仅当前层')} | "
          f"命名：{'仅对象名' if args.obj_name_only else '<ab名>__<对象名>'} | "
          f"导出：{' + '.join(export_types)}")
    print(f"待解 .ab 文件：{len(ab_files)} 个"
          + ("（来自清单）" if only_files is not None else "（已跳过 *.ab.manifest）"))
    print("-" * 68)

    if not ab_files:
        print("没有找到任何 .ab 文件，结束。")
        return 0

    ok = fail = empty = 0
    failures: list[tuple[str, str]] = []
    no_img: list[str] = []
    all_files: list[tuple[Path, int, int]] = []

    for ab_path, rel_dir in ab_files:
        out_dir = out_root / rel_dir
        rel = ab_path.relative_to(in_dir)
        print(f"[解包] {rel}")
        try:
            res = unpack_one(ab_path, out_dir, args.obj_name_only, export_types, up)
        except Exception as exc:                      # 整包加载/解析失败
            fail += 1
            msg = f"{type(exc).__name__}: {exc}"
            failures.append((str(rel), msg))
            print(f"        !! 失败：{msg}")
            continue

        for e in res["errors"]:
            print(f"        !! 对象失败：{e}")
        if res["files"]:
            ok += 1
            for dst, w, h in res["files"]:
                print(f"        -> {dst.name}  [{w}x{h}]")
                all_files.append((dst.relative_to(out_root), w, h))
        elif res["total_objects"] == 0:
            # UnityPy.load() 对损坏/非 AssetBundle 文件不抛异常，只会返回 0 个对象。
            # 正常的资源包一定有对象，所以这里判为失败，避免把坏文件误报成「无图片」。
            fail += 1
            msg = "文件中没有任何对象（疑似损坏、截断或不是 AssetBundle）"
            failures.append((str(rel), msg))
            print(f"        !! 失败：{msg}")
        else:
            empty += 1
            no_img.append(str(rel))
            print(f"        -- 未导出图片对象（包内有 {res['total_objects']} 个对象，"
                  f"但无 {'/'.join(export_types)}）")

    print("=" * 68)
    print("汇总")
    print(f"  成功（有产物） : {ok}")
    print(f"  失败           : {fail}")
    print(f"  无图片对象     : {empty}")
    print(f"  总计 .ab       : {len(ab_files)}")
    print(f"  导出 PNG 总数  : {len(all_files)}")
    print(f"  输出根目录     : {out_root}")

    if failures:
        print("\n失败清单：")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
    if no_img:
        print("\n无图片对象清单：")
        for name in no_img:
            print(f"  - {name}")

    if all_files:
        print("\n产物尺寸（宽×高）：")
        for rel, w, h in sorted(all_files, key=lambda t: str(t[0])):
            print(f"  {rel}  {w}x{h}  {w*h}px")

    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
