# 用途：落雪查分器（lxns）公开别名库 → 按内部号融成一张别名表，给 fusion 用
# 状态：现行
"""落雪 /api/v0/maimai/alias/list（公开、无需 token）→ data/aliasesTag_lxns/aliasesTag-lxns.json

【数据长什么样】
    GET https://maimai.lxns.net/api/v0/maimai/alias/list
    {"aliases": [{"song_id": 8, "aliases": ["真爱歌", "真爱", "真正的爱之歌"]}, …]}
  实测 1067 首 / 约 5 千条别名（2026-10-20）。

【id 怎么对】
  落雪的约定：std / dx 共用同一个 song_id（不存在 >10000 的常规 id，遇到就 mod 10000），宴会场 >100000。
  我们曲库里 DX 谱是「std 内部号 + 10000」（例如 std 2070 / dx 12070）。所以每个 song_id 试三个候选：
      song_id、song_id % 10000、song_id + 10000
  命中哪个就挂哪个（std+dx 会同时命中，两边都挂同一份别名）。

【输出】data/aliasesTag_lxns/aliasesTag-lxns.json
  songs[]     internalId / dataid / title / aliasesTag（按 internalId 升序）
  missing[]   对不上内部号的 song_id（不猜）
  counts      条目数 / 别名条数 / 覆盖曲数 / missing

【给谁用】fusion 读它在**歌曲级**追加到别名表最后（顺序：catalog → chiffon → lxns）。

【用法】
    python tools/aliasesTag_lxns/aliasesTag_lxns.py [选项]
      --proofread-dir PATH  用来对号的曲库目录（默认 data/Proofread-build/）
      --from-file PATH      不联网，改用本地已抓好的 alias/list 响应
      -o, --outdir PATH     输出目录（默认 data/aliasesTag_lxns/）
    退出码：0 成功；2 抓取/读入/写盘失败。

依赖：仅 Python 标准库（走系统代理，跟 catalog_dxdata 一样）。
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_ID = os.environ.get("MAIMAI_RUN_ID") or "manual"

sys.dont_write_bytecode = True

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    import env
except Exception:                                          # noqa: BLE001
    env = None

SCRIPT_DIR_NAME = "aliasesTag_lxns"
OUT_NAME = "aliasesTag-lxns.json"
SOURCE_URL = "https://maimai.lxns.net/api/v0/maimai/alias/list"
PROOFREAD_DIR_NAME = "Proofread-build"
TIMEOUT_SECONDS = 60
TZ_CN = timezone(timedelta(hours=8))
BAR = "=" * 72
RULE = "─" * 72


def now_stamp() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")


def data_root(script_name: str) -> Path:
    return PROJECT_DIR / "data" / script_name


def http_get(url: str) -> bytes:
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    }, method="GET")
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if getattr(response, "status", 200) != 200:
            raise OSError(f"HTTP {response.status}")
        return response.read()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + chr(10)
    tmp.write_bytes(body.encode("utf-8"))
    tmp.replace(path)


def load_songs(proofread_dir: Path) -> dict[int, dict]:
    """Proofread 产物 → {internalId: {dataid, title}}。"""
    songs = {}
    for path in sorted(proofread_dir.glob("Proofread-*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for song in doc.get("songs") or []:
            iid = song.get("internalId")
            if isinstance(iid, int):
                songs[iid] = {"dataid": song.get("dataid"), "title": song.get("title")}
    return songs


def candidates(song_id: int) -> list[int]:
    """落雪 id → 我们内部号的候选（std/dx 共用 id、dx 是 std+10000）。"""
    out = [song_id]
    if 10000 <= song_id < 100000:
        out.append(song_id % 10000)
    if song_id and song_id + 10000 < 100000:
        out.append(song_id + 10000)
    seen, uniq = set(), []
    for value in out:
        if value not in seen:
            seen.add(value)
            uniq.append(value)
    return uniq


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(prog="aliasesTag_lxns.py",
                                 description="落雪别名库 → data/aliasesTag_lxns/aliasesTag-lxns.json")
    ap.add_argument("--proofread-dir", metavar="PATH", help=f"曲库目录（默认 data/{PROOFREAD_DIR_NAME}/）")
    ap.add_argument("--from-file", metavar="PATH", help="不联网，改用本地 alias/list 响应")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="输出目录（默认 data/aliasesTag_lxns/）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    proofread_dir = Path(args.proofread_dir).resolve() if args.proofread_dir else data_root(PROOFREAD_DIR_NAME)
    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)
    out_path = outdir / OUT_NAME

    print(BAR)
    print("aliasesTag_lxns · 落雪别名库")
    print(f"  曲库     : {proofread_dir}/")
    print(f"  输出     : {out_path}")
    print(RULE)

    songs = load_songs(proofread_dir)
    if not songs:
        print(f"[错误] {proofread_dir} 里没有 Proofread-*.json，先跑 Proofread-build。", file=sys.stderr)
        return 2

    try:
        if args.from_file:
            raw = Path(args.from_file).read_bytes()
            print(f"  数据来源 : 本地文件 {args.from_file}")
        else:
            print(f"  下载     : {SOURCE_URL}")
            raw = http_get(SOURCE_URL)
        doc = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"[错误] 取别名失败：{exc}", file=sys.stderr)
        return 2

    items = doc.get("aliases") or []
    rows: dict[int, list[str]] = {}
    missing: list[dict] = []
    alias_count = 0
    for item in items:
        try:
            song_id = int(item.get("song_id"))
        except (TypeError, ValueError):
            continue
        names = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
        hit = [c for c in candidates(song_id) if c in songs]
        if not hit:
            missing.append({"songId": song_id, "aliases": names})
            continue
        for iid in hit:
            current = rows.setdefault(iid, [])
            title = str((songs[iid].get("title") or "")).strip().lower()
            for name in names:
                if name.lower() == title or name in current:
                    continue
                current.append(name)
                alias_count += 1

    payload = {
        "schema": 1, "runId": RUN_ID, "generatedAt": now_stamp(), "kind": "aliasesTag-lxns",
        "sources": {"url": SOURCE_URL, "proofread": f"data/{PROOFREAD_DIR_NAME}"},
        "counts": {"items": len(items), "aliases": alias_count, "songs": len(rows),
                   "missing": len(missing)},
        "songs": [{"internalId": iid, "dataid": songs[iid].get("dataid"),
                   "title": songs[iid].get("title"), "aliasesTag": rows[iid]}
                  for iid in sorted(rows) if rows[iid]],
        "missing": missing,
    }
    try:
        write_json(out_path, payload)
    except OSError as exc:
        print(f"[错误] 写盘失败：{exc}", file=sys.stderr)
        return 2
    print(f"  解析     : {len(items):,} 首 → 覆盖 {len(rows):,} 首 / {alias_count:,} 条别名"
          + (f"，对不上 {len(missing):,} 首" if missing else ""))
    print(f"  已写入  : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
