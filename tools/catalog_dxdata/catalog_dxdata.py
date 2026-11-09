# 用途：拉取 DXRating 完整曲库目录（GET /api/v1/dxdata）→ maimai-planner/data/catalog_dxdata/dxdata.json，带 ETag 增量与写盘前的结构自检
# 状态：现行
"""把 DXRating 的完整曲库目录抓成本地 JSON 库（打歌计划器的曲库来源之一）。

【数据归属 · 先读这条】
  * 本脚本只产出 data/ 下的「构建产物」，可整个删掉重建；产物落在**本脚本专属的子目录**里
    （约定见 maimai-planner/env.py 的 data_dir_for()：data/ 下按产出它的脚本名分目录）：
        data/catalog_dxdata/dxdata.json        上游目录原文（原样逐字节落盘，不改一个字段）
        data/catalog_dxdata/dxdata.meta.json   本次下载的元信息（ETag / 时间 / 字节数 / 计数）
  * 不记录、不写死任何本机路径：整个 maimai-planner/ 文件夹搬到任何位置、
    任何机器、放进别的仓库都照样能跑（目录锚点只有 env.py 一处）。

【用法】
    python tools/catalog_dxdata/catalog_dxdata.py [选项]
      -o, --output PATH   输出文件（默认 env.DATA_DIR/dxdata.json）
      --force             忽略 ETag 强制重新下载
      --no-proxy          跳过系统代理，直接用强制直连线路

【行为】
  * 线路：先走系统代理（urllib 自动继承），失败再强制直连（ProxyHandler({})）重试一次；
    两条都失败才算失败，并打印每一条的失败原因（不静默吞掉）。成功即停，不重复下载。
  * ETag 增量：把上次响应的 ETag 记在 data/catalog_dxdata/dxdata.meta.json；下次请求带
    If-None-Match，命中 304 就打「已是最新」退出：曲库文件不重写，只把 meta 的 runId 记成本批
    （链上每一步都必须留下本批印记，否则 run_chain 会判「产物非本批」）。
    （本地 json 缺失时即便有 ETag 也会正常重新下载，避免只留 meta 没有数据。）
  * 结构自检：「必需键」缺一即失败，「可选键」只统计不判失败 —— 见下节。
  * 写盘：先写同目录内的 .part 临时文件再原子替换，校验通过才落盘；临时文件用完即删，
    data/catalog_dxdata/ 最终只留 dxdata.json 与 dxdata.meta.json 两个文件。
  * 退出码：0 成功（含 304 已是最新）；1 网络失败 或 写盘前自检不通过。

【自检口径：必需键 vs 可选键】（2026-10-15 实测 schemaVersion=1 的 1799 曲 / 7388 谱）
  上游对部分记录**本就省略**下面这些键，属正常数据形态，不是损坏：
      song.bpm                  少 7 首（宴会场曲目）
      sheet.noteDesigner        少 105 张（utage / utage2p）
      sheet.internalId          少 122 张（dx 103 / utage 16 / utage2p 3）
      noteCounts 的单个计数      touch 少 2962 张（为 0 时省略）；tap/hold/slide/break/total
                                各少 14 张（宴会场谱面无 note 数据）
  所以自检分两档：
      * 必需键（实测 100% 出现）：顶层 12 键；song 除 bpm 外 11 键；sheet 除
        noteDesigner/internalId 外 10 键；noteCounts 必须是对象。
        任一缺失 → 判为结构异常（多半是上游改版），非 0 退出且不覆盖已有库。
      * 可选键：只计数并打印，供人判断上游有没有大改，不影响退出码。
    这样既保留了真实的完整性闸门，又不会因为上游的正常省略而永远失败。

依赖：仅 Python 标准库（json / ssl / urllib / argparse / pathlib / sys / time），无需第三方包。
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
import os

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"  # 批次号：run_chain 下发（手工跑记 manual），写进产物头部

# ── 常量 ────────────────────────────────────────────────────────────────
URL = "https://miruku.dxrating.net/api/v1/dxdata"
USER_AGENT = "maimai-planner-catalog-dxdata/1.0 (urllib; local offline tool)"
TIMEOUT_SECONDS = 180
OUTPUT_NAME = "dxdata.json"
SCRIPT_DIR_NAME = "catalog_dxdata"      # data/ 下的专属子目录名 = tools/ 下的脚本目录名

# 本 meta 文件「自身」的格式版本；与上游 dxdata 的 schemaVersion 无关（后者原样抄进 meta）
META_SCHEMA = 1

# 上游 dxdata 的结构契约（依据 schemaVersion = 1 实测）
TOP_KEYS = (
    "schemaVersion", "updatedAt", "categories", "versions", "types", "difficulties",
    "servers", "songs", "tagGroups", "tags", "tagSongs", "aliases",
)
# 必需：2026-10-15 实测 1799 曲 / 7388 谱里 100% 出现
SONG_KEYS_REQUIRED = (
    "id", "category", "title", "artist", "imageName", "version",
    "isNew", "isLocked", "sheets", "searchAcronyms",
)
SHEET_KEYS_REQUIRED = (
    "id", "type", "difficulty", "level", "internalLevelValue", "noteCounts",
    "serverIds", "isSpecial", "version", "releaseDate",
)
# 可选：上游对宴会场等记录本就省略（见文件头「自检口径」）
SONG_KEYS_OPTIONAL = ("bpm",)
SHEET_KEYS_OPTIONAL = ("noteDesigner", "internalId")
NOTE_KEYS = ("tap", "hold", "slide", "touch", "break", "total")

MAX_SAMPLES = 5          # 每类问题最多列举几条实例，避免刷屏
RULE = "-" * 68
BAR = "=" * 68


class FetchError(RuntimeError):
    """一次线路尝试失败（网络、超时、非 200），带可读原因。"""


class NotModified(Exception):
    """服务端回了 304：本地库已是最新。"""


# ── 环境与路径（零硬编码） ──────────────────────────────────────────────
def _load_env():
    """把 maimai-planner/ 加进 sys.path 后 import env；失败则返回 (None, 推导出的项目根)。

    本文件位于 maimai-planner/tools/catalog_dxdata/，上三级就是 maimai-planner/。
    """
    project_dir = Path(__file__).resolve().parents[2]
    if str(project_dir) not in sys.path:
        sys.path.insert(0, str(project_dir))
    # import env 会让 Python 往 maimai-planner/ 里丢一个 __pycache__/。本项目讲究
    # 「跑一次只该多出 data/ 里的产物」，所以先关掉字节码落盘，别污染仓库树。
    sys.dont_write_bytecode = True
    try:
        import env  # type: ignore[import-not-found]
    except Exception:                                     # env.py 缺失/损坏也不致命
        return None, project_dir
    return env, project_dir


def _setup_console(env) -> None:
    """让中文输出能安全进管道（本机 Python 默认 stdout=gbk）。"""
    if env is not None:
        try:
            env.force_utf8_stdio()
            return
        except Exception:                                 # noqa: BLE001  退回自带实现
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _default_output_dir(env, project_dir: Path) -> Path:
    """本脚本的产物目录：优先 env.data_dir_for(SCRIPT_DIR_NAME)，退回 <项目根>/data/<脚本名>。

    失败也**不致命**：退回自己按 __file__ 推导的路径，保证 env.py 缺失时仍能跑。
    """
    if env is not None:
        helper = getattr(env, "data_dir_for", None)
        if callable(helper):
            try:
                return Path(helper(SCRIPT_DIR_NAME))
            except Exception:                             # noqa: BLE001  env 版本旧/参数变了
                pass
        data_dir = getattr(env, "DATA_DIR", None)
        if data_dir:
            return Path(data_dir) / SCRIPT_DIR_NAME
    return project_dir / "data" / SCRIPT_DIR_NAME


def _counts(catalog: dict) -> dict:
    """目录规模统计（songs / sheets / versions / … 全部计数）。"""
    songs = catalog.get("songs") or []
    if not isinstance(songs, list):
        songs = []
    sheets = 0
    for song in songs:
        song_sheets = song.get("sheets") if isinstance(song, dict) else None
        if isinstance(song_sheets, list):
            sheets += len(song_sheets)
    out = {"songs": len(songs), "sheets": sheets}
    for key in ("categories", "versions", "types", "difficulties", "servers",
                "tagGroups", "tags", "tagSongs", "aliases"):
        value = catalog.get(key)
        out[key] = len(value) if isinstance(value, list) else 0
    return out


def _fmt_missing(counter: dict, samples: list) -> str:
    detail = "、".join(f"{k}×{v}" for k, v in sorted(counter.items()))
    tail = f"；例：{' | '.join(samples)}" if samples else ""
    return detail + tail


def self_check(catalog: dict) -> tuple[dict, list, list]:
    """结构自检。返回 (计数, 致命问题清单, 可选字段提示清单)。

    必需键全量遍历（缺一即记一），可选键只统计。口径见文件头「自检口径」。
    """
    problems: list[str] = []
    notes: list[str] = []

    if not isinstance(catalog, dict):
        return {}, [f"顶层不是 JSON 对象，而是 {type(catalog).__name__}"], []

    miss_top = _missing(TOP_KEYS, catalog)
    if miss_top:
        problems.append(f"顶层缺键：{', '.join(miss_top)}")

    songs = catalog.get("songs")
    if not isinstance(songs, list) or not songs:
        problems.append("songs 为空或不是数组（曲库拿不到内容）")
        return _counts(catalog), problems, notes

    req_song: dict = {}
    opt_song: dict = {}
    req_sheet: dict = {}
    opt_sheet: dict = {}
    note_miss: dict = {}
    song_samples: list = []
    sheet_samples: list = []
    note_shape_bad = 0

    for song in songs:
        if not isinstance(song, dict):
            req_song["<非对象记录>"] = req_song.get("<非对象记录>", 0) + 1
            continue
        for key in SONG_KEYS_REQUIRED:
            if key not in song:
                req_song[key] = req_song.get(key, 0) + 1
                if len(song_samples) < MAX_SAMPLES:
                    song_samples.append(f"song id={song.get('id')} 缺 {key}")
        for key in SONG_KEYS_OPTIONAL:
            if key not in song:
                opt_song[key] = opt_song.get(key, 0) + 1

        sheets = song.get("sheets")
        if not isinstance(sheets, list):
            problems.append(f"song id={song.get('id')} 的 sheets 不是数组")
            continue
        for sheet in sheets:
            if not isinstance(sheet, dict):
                req_sheet["<非对象记录>"] = req_sheet.get("<非对象记录>", 0) + 1
                continue
            for key in SHEET_KEYS_REQUIRED:
                if key not in sheet:
                    req_sheet[key] = req_sheet.get(key, 0) + 1
                    if len(sheet_samples) < MAX_SAMPLES:
                        sheet_samples.append(f"sheet id={sheet.get('id')} 缺 {key}")
            for key in SHEET_KEYS_OPTIONAL:
                if key not in sheet:
                    opt_sheet[key] = opt_sheet.get(key, 0) + 1
            notes_obj = sheet.get("noteCounts")
            if not isinstance(notes_obj, dict):
                note_shape_bad += 1
                continue
            for key in NOTE_KEYS:
                if key not in notes_obj:
                    note_miss[key] = note_miss.get(key, 0) + 1

    if req_song:
        problems.append("song 必需字段缺失：" + _fmt_missing(req_song, song_samples))
    if req_sheet:
        problems.append("sheet 必需字段缺失：" + _fmt_missing(req_sheet, sheet_samples))
    if note_shape_bad:
        problems.append(f"noteCounts 不是对象：{note_shape_bad} 张谱面")

    if opt_song:
        notes.append("song 可选字段缺失（上游正常省略，不影响可用性）：" + _fmt_missing(opt_song, []))
    if opt_sheet:
        notes.append("sheet 可选字段缺失（上游正常省略，不影响可用性）：" + _fmt_missing(opt_sheet, []))
    if note_miss:
        notes.append("noteCounts 个别计数缺失（为 0 时上游省略，读作 0）：" + _fmt_missing(note_miss, []))

    return _counts(catalog), problems, notes


def _missing(keys, obj) -> list:
    return [k for k in keys if k not in obj]


def print_counts(counts: dict) -> None:
    print(f"  songs      : {counts.get('songs', 0):,} 首")
    print(f"  sheets     : {counts.get('sheets', 0):,} 张")
    print(f"  versions   : {counts.get('versions', 0):,}")
    print(f"  tags       : {counts.get('tags', 0):,}")
    print(f"  tagGroups  : {counts.get('tagGroups', 0):,}")
    print(f"  tagSongs   : {counts.get('tagSongs', 0):,}")
    print(f"  aliases    : {counts.get('aliases', 0):,}")
    print(f"  categories : {counts.get('categories', 0):,}   types: {counts.get('types', 0):,}"
          f"   difficulties: {counts.get('difficulties', 0):,}   servers: {counts.get('servers', 0):,}")


def print_problems(problems: list, notes: list, indent: str = "  ") -> None:
    for item in problems:
        print(f"{indent}[不通过] {item}")
    for item in notes:
        print(f"{indent}[提示] {item}")


# ── 网络 ────────────────────────────────────────────────────────────────
def http_get(headers: dict, use_proxy: bool) -> tuple[bytes, str | None]:
    """GET 一次端点。返回 (响应体, ETag)。304 抛 NotModified，其余失败抛 FetchError。"""
    context = ssl.create_default_context()
    if use_proxy:
        # 不带参数时 build_opener 会装上一整套默认 handler，其中 ProxyHandler 读系统代理设置
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
    else:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
        )
    request = urllib.request.Request(URL, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
            if status != 200:
                raise FetchError(f"HTTP {status}（期望 200）")
            body = response.read()
            return body, response.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            raise NotModified() from exc
        raise FetchError(f"HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"URLError: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FetchError(f"请求超时（>{TIMEOUT_SECONDS}s）") from exc
    except (FetchError, NotModified):
        raise
    except OSError as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc


def atomic_write(path: Path, data: bytes) -> None:
    """先写同目录 .part 再原子替换；异常/中断时清理临时文件。"""
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def read_meta(meta_path: Path) -> dict:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"[提示] 元信息文件读不了，当作首次下载：{meta_path.name}: {type(exc).__name__}: {exc}")
        return {}
    return meta if isinstance(meta, dict) else {}


def write_meta(meta_path: Path, etag: str | None, body: bytes, catalog: dict, counts: dict) -> None:
    """记录 ETag / 下载时间 / 字节数 / 上游 schemaVersion 与 updatedAt / 各项计数。

    注意：schemaVersion 与 updatedAt 是「上游 dxdata」的值；schema/generatedAt 才是本 meta 自己的。
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta = {
        "schema": META_SCHEMA,
        "runId": RUN_ID,
        "generatedAt": now,
        "source": URL,
        "etag": etag,
        "downloadedAt": now,
        "bytes": len(body),
        "schemaVersion": catalog.get("schemaVersion"),
        "updatedAt": catalog.get("updatedAt"),
        "songs": counts.get("songs", 0),
        "sheets": counts.get("sheets", 0),
    }
    atomic_write(meta_path, (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def stamp_meta_only(meta_path: Path, meta: dict) -> None:
    """304：曲库没变，但这一步仍要留下本批的印记 —— 只改 meta 的 runId / generatedAt。

    run_chain 逐步核对「产物带没带本批 runId」（防抢跑、防下游吃旧产物）。
    若 304 时什么都不写，产物就还挂着上一批的号，整条链会在第 1 步判成「产物非本批」。
    """
    stamped = dict(meta)
    stamped["runId"] = RUN_ID
    stamped["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    atomic_write(meta_path, (json.dumps(stamped, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


# ── 主流程 ────────────────────────────────────────────────────────────
def do_download(out_path: Path, meta_path: Path, force: bool, no_proxy: bool) -> int:
    """联网下载（默认 ETag 增量）。"""
    print(BAR)
    print("DXRating 曲库拉取")
    print(f"  端点 : {URL}")
    print(f"  输出 : {out_path}")
    print(f"  meta : {meta_path}")
    print(f"  线路 : {'仅强制直连（--no-proxy）' if no_proxy else '系统代理 → 失败则强制直连'}")
    print(f"  ETag : {'忽略（--force 强制重下）' if force else '增量（命中 304 则不动文件）'}")
    print(RULE)

    meta = read_meta(meta_path)
    old_etag = meta.get("etag") if isinstance(meta.get("etag"), str) else None
    if old_etag and not out_path.is_file():
        print("[提示] 有 ETag 但本地 JSON 不在，忽略 If-None-Match 直接重新下载。")
        old_etag = None

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if old_etag and not force:
        headers["If-None-Match"] = old_etag

    routes = [("强制直连", False)] if no_proxy else [("系统代理", True), ("强制直连", False)]
    body: bytes | None = None
    etag: str | None = None
    used_route: str | None = None
    not_modified = False

    for label, use_proxy in routes:
        print(f"[下载] 尝试 {label} …")
        started = time.monotonic()
        try:
            body, etag = http_get(headers, use_proxy)
        except NotModified:
            elapsed = time.monotonic() - started
            print(f"  304 Not Modified（{label}，{elapsed:.1f}s）")
            not_modified = True
            used_route = label
            break
        except FetchError as exc:
            elapsed = time.monotonic() - started
            print(f"  失败（{label}，{elapsed:.1f}s）：{exc}", file=sys.stderr)
            continue
        elapsed = time.monotonic() - started
        used_route = label
        print(f"  200 OK（{label}）：{len(body):,} 字节，耗时 {elapsed:.1f}s")
        print(f"  ETag : {etag}")
        break                                            # 拿到数据就停，不再试下一条线路

    if not_modified:
        print(RULE)
        print(f"[已是最新] ETag 命中 304（{used_route}），{out_path.name} 未改动；"
              f"只把 {meta_path.name} 的 runId 记成本批 {RUN_ID}。")
        if meta:
            print(f"  ETag          : {meta.get('etag')}")
            print(f"  上次下载时间  : {meta.get('downloadedAt')}")
            print(f"  schemaVersion : {meta.get('schemaVersion')}")
            print(f"  updatedAt     : {meta.get('updatedAt')}")
            print(f"  songs/sheets  : {meta.get('songs')} / {meta.get('sheets')}（取自 meta，未重新解析）")
            stamp_meta_only(meta_path, meta)
        print(BAR)
        return 0

    if body is None:
        print(RULE)
        print(f"[错误] {len(routes)} 条线路全部失败，未取得数据。"
              f"请检查网络/代理后重试（每条线路的失败原因见上）。", file=sys.stderr)
        return 1

    # 先解析 + 自检，通过才落盘：坏响应绝不覆盖已有的好库
    try:
        catalog = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        print(RULE)
        print(f"[错误] 响应不是合法 JSON：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    counts, problems, notes = self_check(catalog)
    print(RULE)
    print("[自检] 结构校验")
    print(f"  schemaVersion : {catalog.get('schemaVersion') if isinstance(catalog, dict) else None}")
    print(f"  updatedAt     : {catalog.get('updatedAt') if isinstance(catalog, dict) else None}")
    print_counts(counts)
    print_problems(problems, notes)
    if problems:
        print(RULE)
        print("[错误] 自检不通过，未写入任何文件（保留本地已有库）。", file=sys.stderr)
        return 1
    print("  [通过] 必需键齐全、songs/sheets 非空、记录必需字段完整。")

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(out_path, body)
        write_meta(meta_path, etag, body, catalog, counts)
    except OSError as exc:
        print(RULE)
        print(f"[错误] 写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(RULE)
    print(f"[完成] 已写入 {out_path}")
    print(f"  字节数        : {len(body):,}")
    print(f"  ETag          : {etag}")
    print(f"  schemaVersion : {catalog.get('schemaVersion')}")
    print(f"  updatedAt     : {catalog.get('updatedAt')}")
    print_counts(counts)
    print(f"  元信息        : {meta_path}（已记录 ETag，下次自动走 304）")
    print(BAR)
    return 0


def main(argv=None) -> int:
    env, project_dir = _load_env()
    _setup_console(env)
    default_dir = _default_output_dir(env, project_dir)
    default_output = default_dir / OUTPUT_NAME

    parser = argparse.ArgumentParser(
        description="拉取 DXRating 完整曲库目录（/api/v1/dxdata）→ data/catalog_dxdata/dxdata.json，"
                    "带 ETag 增量与结构自检",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例：python tools/catalog_dxdata/catalog_dxdata.py            # 增量更新\n"
               "    python tools/catalog_dxdata/catalog_dxdata.py --force    # 强制重下\n",
    )
    parser.add_argument("-o", "--output", default=str(default_output),
                        help="输出 JSON 路径（默认 data/<脚本名>/dxdata.json；同目录会生成 <名>.meta.json）")
    parser.add_argument("--force", action="store_true", help="忽略 ETag，强制重新下载")
    parser.add_argument("--no-proxy", action="store_true", help="跳过系统代理，直接强制直连")
    args = parser.parse_args(argv)

    out_path = Path(args.output).expanduser().resolve()
    meta_path = out_path.with_name(out_path.stem + ".meta.json")

    return do_download(out_path, meta_path, args.force, args.no_proxy)


if __name__ == "__main__":
    sys.exit(main())
