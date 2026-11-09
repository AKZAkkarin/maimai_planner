# 用途：抓水鱼查分器的社区拟合定数（chart_stats.fit_diff），按 内部号 + 谱面索引 成一张表
# 状态：现行
"""社区拟合定数（fit_diff）→ data/fit_diff/fit_diff.json

【数据从哪来】
    GET https://www.diving-fish.com/api/maimaidxprober/chart_stats      （公开接口，不需要任何 token）
  返回里 `charts` 以**游戏内部号**为 key，数组下标就是谱面索引（0 BASIC … 4 Re:MASTER）：
    { "cnt": 5391, "diff": "5", "fit_diff": 5.0183, "avg": 100.06,
      "avg_dx": 11327.25, "std_dev": 0.97, "dist": [...], "fc_dist": [...] }

  fit_diff 是水鱼用全体玩家成绩分布重算出来的「拟合定数」，不是官方定数：
  越多人越级打某张谱，这个值通常越高。

【输出】
  data/fit_diff/chart_stats.json   原始响应（留着复查 / 以后算别的）
  data/fit_diff/fit_diff.json      归一化：charts["<internalId>:<chartid>"] = {fitDiff, diff, avg, stdDev, cnt}

【多久刷新】
  **每次都抓**（「只更新曲库」链里要的就是新数据）。数据有多旧由 WebUI 在「更新dxrating数据」链卡片上显示，
  脚本自己不做跳过、也不打提示。

【用法】
    python tools/fit_diff/fit_diff.py [选项]
      -o, --outdir PATH     输出目录（默认 data/fit_diff/）
      --refresh             不管新鲜度，强制重抓
      --max-age-days N      超过 N 天算旧（默认 7）
      --from-file PATH      不联网，改用本地已抓好的 chart_stats 响应
      --no-proxy            跳过系统代理，直接强制直连
      --dry-run             只抓不写盘
    退出码：0 成功（含"还新，跳过"）；2 抓取/写盘失败。

依赖：仅 Python 标准库（走系统代理，跟 catalog_dxdata 一样）。
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
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

SCRIPT_DIR_NAME = "fit_diff"
RAW_NAME = "chart_stats.json"
FIT_NAME = "fit_diff.json"
SOURCE_URL = "https://www.diving-fish.com/api/maimaidxprober/chart_stats"
TIMEOUT_SECONDS = 120
TZ_CN = timezone(timedelta(hours=8))
BAR = "=" * 72
RULE = "─" * 72


def now_stamp() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")


def data_root(script_name: str) -> Path:
    return PROJECT_DIR / "data" / script_name


def http_get(url: str, use_proxy: bool = True) -> bytes:
    """GET 一次；use_proxy=False 时强制绕过系统代理。"""
    context = ssl.create_default_context()
    handlers = [urllib.request.HTTPSHandler(context=context)]
    if not use_proxy:
        # build_opener 看到自定义 ProxyHandler 后，不会再叠加默认的代理处理器。
        handlers.insert(0, urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    }, method="GET")
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if getattr(response, "status", 200) != 200:
            raise OSError(f"HTTP {response.status}")
        return response.read()


def fetch_chart_stats(no_proxy: bool = False) -> bytes:
    """下载 chart_stats：系统代理失败时自动降级直连。"""
    routes = [("强制直连", False)] if no_proxy else [
        ("系统代理", True),
        ("强制直连", False),
    ]
    last_error = None
    for label, use_proxy in routes:
        print(f"  线路     : {label}")
        try:
            return http_get(SOURCE_URL, use_proxy=use_proxy)
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            print(f"  [警告] {label}失败：{exc}", file=sys.stderr)
    raise OSError(f"系统代理和直连都失败：{last_error}")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + chr(10)
    tmp.write_bytes(body.encode("utf-8"))
    tmp.replace(path)


def normalize(raw: dict) -> dict:
    """chart_stats 原始响应 → {"<iid>:<cid>": {fitDiff, diff, avg, stdDev, cnt}}。"""
    charts = {}
    for song_id, sheets in (raw.get("charts") or {}).items():
        digits = "".join(ch for ch in str(song_id) if ch.isdigit())
        if not digits:
            continue
        iid = int(digits)
        for cid, sheet in enumerate(sheets or []):
            if not isinstance(sheet, dict) or sheet.get("fit_diff") is None:
                continue
            charts[f"{iid}:{cid}"] = {
                "fitDiff": round(float(sheet["fit_diff"]), 4),
                "diff": sheet.get("diff"),
                "avg": sheet.get("avg"),
                "stdDev": sheet.get("std_dev"),
                "cnt": sheet.get("cnt"),
            }
    return charts


def age_days(path: Path) -> float | None:
    try:
        return (time.time() - path.stat().st_mtime) / 86400
    except OSError:
        return None


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(prog="fit_diff.py",
                                 description="水鱼 chart_stats → 社区拟合定数（data/fit_diff/fit_diff.json）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="输出目录（默认 data/fit_diff/）")
    ap.add_argument("--refresh", action="store_true", help="不管新鲜度，强制重抓")
    ap.add_argument("--from-file", metavar="PATH", help="不联网，改用本地 chart_stats 响应")
    ap.add_argument("--no-proxy", action="store_true", help="跳过系统代理，直接强制直连")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写盘")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    outdir = Path(args.outdir).resolve() if args.outdir else data_root(SCRIPT_DIR_NAME)
    raw_path = outdir / RAW_NAME
    fit_path = outdir / FIT_NAME
    age = age_days(fit_path)

    print(BAR)
    print("fit_diff · 社区拟合定数（水鱼 chart_stats）")
    print(f"  输出     : {fit_path}")
    print(RULE)


    try:
        if args.from_file:
            raw_bytes = Path(args.from_file).read_bytes()
            print(f"  数据来源 : 本地文件 {args.from_file}")
        else:
            print(f"  下载     : {SOURCE_URL}")
            raw_bytes = fetch_chart_stats(no_proxy=args.no_proxy)
    except (OSError, urllib.error.URLError) as exc:
        print(f"[错误] 抓 chart_stats 失败：{exc}", file=sys.stderr)
        return 2

    try:
        raw = json.loads(raw_bytes.decode("utf-8-sig"))
    except ValueError as exc:
        print(f"[错误] 返回不是 JSON：{exc}", file=sys.stderr)
        return 2

    charts = normalize(raw)
    if not charts:
        print("[错误] 一张带 fit_diff 的谱都没解析出来。", file=sys.stderr)
        return 2
    songs = len({k.split(":")[0] for k in charts})
    print(f"  解析     : {songs:,} 首 / {len(charts):,} 张谱有拟合定数")

    if args.dry_run:
        for k, v in list(charts.items())[:3]:
            print(f"    {k} → {v}")
        return 0

    try:
        if not args.from_file:
            outdir.mkdir(parents=True, exist_ok=True)
            tmp = raw_path.with_suffix(".json.part")
            tmp.write_bytes(raw_bytes)
            tmp.replace(raw_path)
        write_json(fit_path, {
            "schema": 1, "runId": RUN_ID, "generatedAt": now_stamp(), "kind": "fit_diff",
            "source": "https://www.diving-fish.com/api/maimaidxprober/chart_stats",
            "counts": {"songs": songs, "charts": len(charts)},
            "charts": charts,
        })
    except OSError as exc:
        print(f"[错误] 写盘失败：{exc}", file=sys.stderr)
        return 2
    print(f"  已写入  : {fit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
