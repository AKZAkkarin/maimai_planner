#!/usr/bin/env python3
# 用途：把 MuNET 导出的存档（userMusicDetailList）转成「某个本地用户」的成绩库（纯 JSON）
# 状态：现行
"""把 MuNET 存档里的成绩转成我们自己的成绩库，按本地用户分开存。

【本地用户】
  一个本地用户 = 一个 uuid，成绩库放在 `dataUser/<uuid>/scores.json`。
  对照表是 `dataUser/users.json`：
      {"schema": 1, "users": [{"id": "<uuid>", "name": "阿卡林", "createdAt": "2026-10-18T..."}]}
  名字只是给人看的标签，不参与任何关联；关联一律用 uuid。
  用户可以在页面上新建，也可以用本脚本的 `--new-user` 建（会写进对照表）。

【只读一组数据】
  输入的 MuNET 导出只取 `userMusicDetailList`（一歌一难度一条）：
      {"musicId":8, "level":0, "playCount":2, "achievement":1010000,
       "comboStatus":4, "syncStatus":0, "deluxscoreMax":263, "scoreRank":13, "extNum1":1}
  存档里其它字段（playlog、角色、道具……）一律不读；**不含任何账号信息进库**。

【字段映射（用户定）】
  musicId → internalId；level → chartid（0–4 = BASIC…Re:MASTER，10 = 宴谱）；
  playCount / achievement / comboStatus / syncStatus / deluxscoreMax 原样；
  **scoreRank 丢弃**，改存 `RANK`：导入时按 achievement 现算的 0–13（读取时以库里的值为准）；
  extNum1 不存；曲名不入库（曲名在曲库里）。

【dxscore（0–5）】用曲库算：`noteCounts.total × 3` 是这张谱的 deluxscore 满分，
  拿它除 `deluxscoreMax` 得百分比再归档：
      0 = 0.00–84.99% · 1 = 85.00–89.99% · 2 = 90.00–92.99% · 3 = 93.00–94.99%
      4 = 95.00–96.99% · 5 = 97.00–100.00%
  曲库里查不到这张谱（已删除曲等）时 dxscore 记 null —— 算不出来就不编。

【RANK 规则】按达成率的下界降序判定（achievement 单位是 ×10000）：
  13 SSS+ ≥100.5% · 12 SSS ≥100.0% · 11 SS+ ≥99.5% · 10 SS ≥99.0% · 9 S+ ≥98.0% ·
  8 S ≥97.0% · 7 AAA ≥94.0% · 6 AA ≥90.0% · 5 A ≥80.0% · 4 BBB ≥75.0% ·
  3 BB ≥70.0% · 2 B ≥60.0% · 1 C ≥50.0% · 0 D 其余。
  utage2p（2P 宴谱，200% 制）的**下界**正好是上表的 ×2，所以用 `achievement >= 2×下界×10000`
  做整数比较；**不写上界**（2×100.4999 = 200.9998，而真实上界是 200.9999，写上界会算错）。

【族怎么判】internalId 属于 data/fusion/fusion-utage2p.json 的才走 200% 制；
  读不到那张表时全部按 1P 表算（会打印警告）。查不到族的（已删除曲）按 1P 表。

【输出】dataUser/（用户数据，和 data/ 曲库分开）
  <uuid>/scores.json         该用户的成绩库（一个文件：头部元信息 + `scores[]`，一行一条）
  last-import.json           最近一次导入的指针（给页面和构建链核对用，固定路径）
  users.json                 本地用户对照表

  数据格式（不用数据库，纯 JSON，方便人看和 diff）：
      {"schema":1, "runId":"…", "generatedAt":"…", "kind":"scores",
       "user":{"id":"<uuid>","name":"…"},
       "source":{"file":"MuNET Export.json","field":"userMusicDetailList","rowsInFile":3219},
       "counts":{…},
       "scores":[{"internalId":8,"chartid":0,"playCount":2,"achievement":1010000,
                  "comboStatus":4,"syncStatus":0,"deluxscoreMax":263,"RANK":13}, …]}
  行按 (internalId, chartid) 升序，写出来是确定的 —— 同样的输入永远得到同样的文件。

【用法】
    python tools/scores_import/scores_import.py --input "<MuNET Export.json>" --user <uuid>
    python tools/scores_import/scores_import.py --input "<MuNET Export.json>" --new-user "阿卡林"
      --list-users        只打印现有的本地用户
      -o, --outdir PATH   根目录（默认 dataUser/）
    退出码：0 成功；2 读输入/用户不对/写盘失败。不联网。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid as uuidlib
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

SCRIPT_DIR_NAME = "scores_import"
FUSION_SCRIPT_DIR = "fusion"
FUSION_2P_NAME = "fusion-utage2p.json"
SCORES_NAME = "scores.json"
USER_FILE = "users.json"
LAST_FILE = "last-import.json"
SOURCE_KEY = "userMusicDetailList"
TZ_CN = timezone(timedelta(hours=8))
BAR = "=" * 68
RULE = "─" * 68
SKYSEA_FLOOR = 1000000            # 云海模式特殊曲在这个号段，整段跳过（用户口径：不用管）

# 达成率下界（百分比）→ RANK；降序排列，整数比较
LOWER_BOUNDS = (
    (100.5, 13), (100.0, 12), (99.5, 11), (99.0, 10), (98.0, 9), (97.0, 8),
    (94.0, 7), (90.0, 6), (80.0, 5), (75.0, 4), (70.0, 3), (60.0, 2),
    (50.0, 1), (0.0, 0),
)
RANK_NAMES = ("D", "C", "B", "BB", "BBB", "A", "AA", "AAA",
              "S", "S+", "SS", "SS+", "SSS", "SSS+")
COLUMNS = ("internalId", "chartid", "playCount", "achievement",
           "comboStatus", "syncStatus", "deluxscoreMax", "RANK", "dxscore")
DXSCORE_BOUNDS = ((97.0, 5), (95.0, 4), (93.0, 3), (90.0, 2), (85.0, 1))


def now_stamp() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")


def user_data_root() -> Path:
    """用户数据根目录（默认 dataUser/）：<uuid>/scores.json、users.json、last-import.json 都落这里。"""
    if env is not None and callable(getattr(env, "user_data_dir", None)):
        try:
            return Path(env.user_data_dir())
        except Exception:                                  # noqa: BLE001
            pass
    return PROJECT_DIR / "dataUser"


def data_root(script_name: str) -> Path:
    if env is not None and callable(getattr(env, "data_dir_for", None)):
        return Path(env.data_dir_for(script_name))
    return PROJECT_DIR / "data" / script_name


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        tmp.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def load_users(root: Path) -> list:
    """读对照表；没有就是空表（不建文件，等真要加用户时再写）。"""
    path = root / USER_FILE
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} 读不出：{exc}") from exc
    users = doc.get("users")
    return users if isinstance(users, list) else []


def save_users(root: Path, users: list) -> None:
    write_json(root / USER_FILE, {"schema": 1, "users": users})


def resolve_user(root: Path, user_id: str | None, new_name: str | None) -> tuple[dict, list]:
    """返回 (用户, 用户表)。给了 --new-user 就新建并写回对照表。"""
    users = load_users(root)
    if new_name:
        name = new_name.strip()
        if not name:
            raise ValueError("用户名不能为空")
        for u in users:
            if u.get("name") == name:                  # 用户要求：本地用户不能重名
                raise ValueError(f"已经有同名的本地用户：{name}，换个名字")
        user = {"id": str(uuidlib.uuid4()), "name": name, "createdAt": now_stamp()}
        users.append(user)
        save_users(root, users)
        return user, users
    if not user_id:
        raise ValueError("要么用 --user <uuid> 指定本地用户，要么用 --new-user <名字> 新建一个")
    for u in users:
        if u.get("id") == user_id:
            return u, users
    raise ValueError(f"对照表里没有这个本地用户：{user_id}（--list-users 看现有的）")


def rank_of(achievement: int, two_player: bool) -> int:
    """按达成率定档。two_player=True 时走 200% 制（下界 ×2，整数比较，精确）。"""
    scale = 2 if two_player else 1
    for lower, rank in LOWER_BOUNDS:
        if achievement >= int(round(lower * scale * 10000)):
            return rank
    return 0


def load_two_player_ids(fusion_dir: Path) -> tuple[set, str]:
    """utage2p 族的 internalId 集合（只看这一族：其余族都是 100% 制）。"""
    path = fusion_dir / FUSION_2P_NAME
    if not path.is_file():
        return set(), f"{path} 不存在，全部按 100% 制算"
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return set(), f"{path} 读不出（{exc}），全部按 100% 制算"
    ids = {s.get("internalId") for s in doc.get("songs") or [] if s.get("internalId") is not None}
    return ids, ""


def family_of(internal_id: int, two_ids: set) -> str:
    if internal_id in two_ids:
        return "utage2p"
    if internal_id >= 100000:
        return "utage"
    if internal_id >= 10000:
        return "dx"
    return "std"


def dxscore_of(delux: int, max_delux: int | None) -> int | None:
    """deluxscoreMax 占满分（noteCounts.total × 3）的百分比 → 0–5；算不出来给 None。"""
    if not max_delux:
        return None
    percent = delux * 100.0 / max_delux
    for lower, bucket in DXSCORE_BOUNDS:
        if percent >= lower:
            return bucket
    return 0


def load_max_delux(fusion_dir: Path) -> dict:
    """(internalId, chartid) → 这张谱的 deluxscore 满分（noteCounts.total × 3）。"""
    table = {}
    for family in ("std", "dx", "utage", "utage2p"):
        path = fusion_dir / f"fusion-{family}.json"
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for song in doc.get("songs") or []:
            internal_id = song.get("internalId")
            if internal_id is None:
                continue
            for sheet in song.get("sheets") or []:
                total = (sheet.get("noteCounts") or {}).get("total")
                if total:
                    table[(internal_id, sheet.get("chartid"))] = int(total) * 3
    return table


def read_source(path: Path) -> list:
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = doc.get(SOURCE_KEY)
    if not isinstance(rows, list):
        raise ValueError(f"{path.name} 里没有 {SOURCE_KEY}（这不像 MuNET 的导出文件）")
    return rows


def build_rows(raw_rows: list, two_ids: set, max_delux: dict) -> tuple[list, dict]:
    rows, stats = [], {"skippedSkysea": 0, "skippedBad": 0, "twoPlayer": 0}
    for item in raw_rows:
        try:
            internal_id = int(item["musicId"])
            chartid = int(item["level"])
            achievement = int(item["achievement"])
            row = (internal_id, chartid,
                   int(item["playCount"]), achievement,
                   int(item["comboStatus"]), int(item["syncStatus"]),
                   int(item["deluxscoreMax"]))
        except (KeyError, TypeError, ValueError):
            stats["skippedBad"] += 1
            continue
        if internal_id >= SKYSEA_FLOOR:
            stats["skippedSkysea"] += 1
            continue
        two_p = internal_id in two_ids
        if two_p:
            stats["twoPlayer"] += 1
        rows.append(row + (rank_of(achievement, two_p),
                          dxscore_of(row[6], max_delux.get((internal_id, chartid)))))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows, stats


def write_scores(path: Path, meta: dict, rows: list) -> int:
    """写成绩库：头部元信息（好读）+ `scores[]` 一行一条（好 diff）。返回字节数。"""
    head = json.dumps(meta, ensure_ascii=False, indent=2).rstrip()
    if not head.endswith("}"):
        raise ValueError("内部错误：元信息不是 JSON 对象")
    body = ",\n".join("    " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    text = (head[:-1].rstrip() + ',\n  "scores": [\n' + body + "\n  ]\n}\n")
    payload = text.encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        tmp.write_bytes(payload)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return len(payload)


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="scores_import.py",
        description="MuNET 存档 → 某个本地用户的成绩库（纯 JSON）",
    )
    ap.add_argument("--input", metavar="PATH", help="MuNET 导出的存档 JSON（只读 userMusicDetailList）")
    ap.add_argument("--user", metavar="UUID", help="本地用户 uuid（用户表里的 id）")
    ap.add_argument("--new-user", metavar="NAME", help="新建一个本地用户（写进 users.json）后导入")
    ap.add_argument("--list-users", action="store_true", help="只打印现有的本地用户")
    ap.add_argument("--fusion-dir", metavar="PATH",
                    help=f"曲库目录，用来认 utage2p 族（默认 data/{FUSION_SCRIPT_DIR}/）")
    ap.add_argument("-o", "--outdir", metavar="PATH", help="根目录（默认 dataUser/）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    root = Path(args.outdir).resolve() if args.outdir else user_data_root()

    if args.list_users:
        try:
            users = load_users(root)
        except ValueError as exc:
            print(f"[错误] {exc}", file=sys.stderr)
            return 2
        print(f"本地用户（{root / USER_FILE}）")
        if not users:
            print("  （还没有用户）")
        for u in users:
            mark = "★" if (u.get("id") == args.user) else " "
            print(f"  {mark} {u.get('id')}  {u.get('name')}  建于 {u.get('createdAt')}")
        return 0

    if not args.input:
        print("[错误] 要 --input <MuNET 导出的 JSON>", file=sys.stderr)
        return 2

    src = Path(args.input).expanduser().resolve()
    fusion_dir = Path(args.fusion_dir).resolve() if args.fusion_dir else data_root(FUSION_SCRIPT_DIR)

    print(BAR)
    print("scores_import · MuNET 存档 → 本地成绩库（按用户分开）")
    print(f"  存档 : {src}")
    print(f"  根目录 : {root}/")
    print(RULE)

    if not src.is_file():
        print(f"[错误] 存档文件不存在：{src}", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=True)
    try:
        user, _users = resolve_user(root, args.user, args.new_user)
    except (OSError, ValueError) as exc:
        print(f"[错误] 本地用户处理失败：{exc}", file=sys.stderr)
        return 2
    print(f"  用户 : {user.get('name')}  ({user.get('id')})")

    two_ids, warn = load_two_player_ids(fusion_dir)
    if warn:
        print(f"[注意] {warn}")
    try:
        raw_rows = read_source(src)
    except (OSError, ValueError) as exc:
        print(f"[错误] 读存档失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    max_delux = load_max_delux(fusion_dir)
    rows, stats = build_rows(raw_rows, two_ids, max_delux)
    if not rows:
        print("[错误] 一条成绩都没解析出来", file=sys.stderr)
        return 2

    user_dir = root / str(user.get("id"))
    scores_path = user_dir / SCORES_NAME
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[错误] 建用户目录失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    ranks = [0] * len(RANK_NAMES)
    for row in rows:
        ranks[row[7]] += 1
    families = {"std": 0, "dx": 0, "utage": 0, "utage2p": 0}
    for row in rows:
        families[family_of(row[0], two_ids)] += 1
    dx_hist = {str(i): 0 for i in range(6)}
    dx_unknown = 0
    for row in rows:
        if row[8] is None:
            dx_unknown += 1
        else:
            dx_hist[str(row[8])] += 1

    counts = {
        "rows": len(rows),
        "songs": len({r[0] for r in rows}),
        "byFamily": families,
        "twoPlayer": stats["twoPlayer"],
        "skippedSkysea": stats["skippedSkysea"],
        "skippedBad": stats["skippedBad"],
        "rank": {RANK_NAMES[i]: c for i, c in enumerate(ranks) if c},
        "dxscore": dx_hist,
        "dxscoreUnknown": dx_unknown,
    }
    generated = now_stamp()
    doc = {
        "schema": 1,
        "runId": RUN_ID,
        "generatedAt": generated,
        "kind": "scores",
        "user": {"id": user.get("id"), "name": user.get("name")},
        "source": {"file": src.name, "field": SOURCE_KEY, "rowsInFile": len(raw_rows)},
        "counts": counts,
    }
    try:
        size = write_scores(scores_path, doc, [dict(zip(COLUMNS, row)) for row in rows])
        write_json(root / LAST_FILE, {                       # 固定路径：给页面和构建链核对
            "schema": 1,
            "runId": RUN_ID,
            "generatedAt": generated,
            "kind": "scores",
            "user": doc["user"],
            "scores": f"{user.get('id')}/{SCORES_NAME}",
            "counts": counts,
        })
    except (OSError, ValueError) as exc:
        print(f"[错误] 写成绩库失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"存档 : {len(raw_rows):,} 行   入库 {len(rows):,} 行（{counts['songs']:,} 首曲）"
          f"   跳云海 {stats['skippedSkysea']}   解析不了 {stats['skippedBad']}")
    print("分族 : " + "  ".join(f"{k} {v:,}" for k, v in families.items() if v)
          + f"   （其中 2P 宴谱 {stats['twoPlayer']} 行走 200% 制）")
    print("RANK : " + "  ".join(f"{RANK_NAMES[i]}:{c}" for i, c in enumerate(ranks) if c))
    print("DX分 : " + "  ".join(f"{k}:{v}" for k, v in dx_hist.items()) + f"   （曲库里查不到 {dx_unknown}）")
    print(RULE)
    print(f"完成 : {scores_path}（{size / 1048576:.2f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
