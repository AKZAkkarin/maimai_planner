# 用途：本机 HTTP 服务，给 WebUI 提供「选游戏包目录 → 按顺序建库 → 看进度」的接口，只调 run_chain 和系统选目录对话框。
# 状态：现行
"""server.py · 本机服务（局域网可用）

【它做什么】
  一个只用标准库的 HTTP 服务：托管 web/ 页面，并把「建库」请求转给 tools/run_chain/run_chain.py 跑。
  顺序、防抢跑、失败即停都由 run_chain 负责，本文件不重复实现一遍。

【接口】
  GET  /                 → web/index.html（同目录静态文件，无 CDN，局域网可直接开）
  GET  /api/config       → 有哪些链、每条链需要什么（network / proxy / gamePackage）
  POST /api/pick-folder  → 在**服务端这台机器**弹系统文件夹对话框，回填绝对路径
  POST /api/pick-file    → 同上，弹选文件对话框；mode 决定标题/过滤器（als 别名 JSON / score 存档）
  POST /api/build        → {"chain":"full","gamePackage":"D:\\\\...\\\\StreamingAssets","als":"D:\\\\...\\\\als.json"} 开跑；已有任务在跑时返回 409
  GET  /api/run          → 当前任务状态：每一步的 等待/运行/完成/失败 + 日志尾巴 + 结果
  POST /api/stop         → 中止当前任务

【只读 / 只写】
  读：tools/run_chain/chains.json、web/ 静态文件
  写：tools/run_chain/.logs/（run_chain 自己写的每步日志）
  选中的游戏包路径**只放内存**，不落任何配置文件（数据源不进配置文件是项目铁律），服务重启需重选。

【用法】
  python maimai-planner/server/server.py                  # 0.0.0.0:20001
  python maimai-planner/server/server.py --port 20005 --host 127.0.0.1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode
import urllib.request
import base64

PROJECT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_DIR / "web"
CHAIN_SCRIPT = PROJECT_DIR / "tools" / "run_chain" / "run_chain.py"
CHAINS_FILE = PROJECT_DIR / "tools" / "run_chain" / "chains.json"
LOG_DIR = PROJECT_DIR / "tools" / "run_chain" / ".logs"
RUN_ID_ENV_VAR = "MAIMAI_RUN_ID"
LOG_TAIL = 400
TZ_CN = timezone(timedelta(hours=8))
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".ico": "image/x-icon"}

STATIC_FILES = {"/": "index.html", "/index.html": "index.html",
                "/main.js": "main.js", "/core.js": "core.js", "/db.js": "db.js", "/stats.js": "stats.js",
                "/songlist.js": "songlist.js", "/songlist-view.js": "songlist-view.js", "/songcard.js": "songcard.js", "/pageup.js": "pageup.js",
                "/assistant.js": "assistant.js", "/style.css": "style.css",
                "/vendor/Sortable.min.js": "vendor/Sortable.min.js"}
DIALOG_TIMEOUT = None       # 不限时：一直等用户选或取消（页面上会挡住操作）


def now_stamp() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")


def run_capture(cmd: list, timeout: float) -> tuple[int | None, str, str]:
    """跑一个子进程并把 stdout 收进临时文件（不用管道：受限沙箱里管道会被拒）。"""
    LOG_DIR.mkdir(exist_ok=True)
    out_path = LOG_DIR / f".capture-{uuid.uuid4().hex[:8]}.out"
    err_path = out_path.with_suffix(".err")
    try:
        with open(out_path, "wb") as out_handle, open(err_path, "wb") as err_handle:
            proc = subprocess.Popen(cmd, stdout=out_handle, stderr=err_handle)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                return None, "", "超时"
        return (proc.returncode, out_path.read_text(encoding="utf-8", errors="replace"),
                err_path.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return None, "", "命令不存在"
    finally:
        out_path.unlink(missing_ok=True)
        err_path.unlink(missing_ok=True)


def pick_path(file_mode: bool = False, kind: str = "als") -> tuple[str | None, str]:
    """在服务端弹系统对话框，返回 (路径, 说明)；取消返回 (None, 说明)。

    file_mode=False 选目录；True 选文件，kind 取 "als" / "avatar" / "score"。
    """
    LOG_DIR.mkdir(exist_ok=True)
    out_path = LOG_DIR / f".pick-{uuid.uuid4().hex[:8]}.txt"
    out_lit = str(out_path).replace("'", "''")          # PowerShell 单引号字符串里的转义
    owner = (
        # OpenFileDialog 本身没有 TopMost，靠一个透明置顶的宿主窗口把它顶在最前
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$o = New-Object System.Windows.Forms.Form;"
        "$o.TopMost = $true;"
        "$o.ShowInTaskbar = $false;"
        "$o.FormBorderStyle = 'None';"
        "$o.Opacity = 0;"
        "$o.Size = New-Object System.Drawing.Size(1,1);"
        "$o.StartPosition = 'CenterScreen';"
        "$o.Show();"
    )
    if file_mode:
        titles = {
            "als": ("选择Chiffonmai别名文件",
                    "JSON (*.json)|*.json|所有文件 (*.*)|*.*"),
            "avatar": ("选择头像图片",
                       "图片 (*.png;*.jpg;*.jpeg;*.webp)|*.png;*.jpg;*.jpeg;*.webp|所有文件 (*.*)|*.*"),
            "score": ("选择MuNET存档",
                      "JSON (*.json)|*.json|所有文件 (*.*)|*.*"),
        }
        title, filt = titles.get(kind, titles["als"])
        head = owner + (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
            "$d = New-Object System.Windows.Forms.OpenFileDialog;"
            f"$d.Title = '{title}';"
            f"$d.Filter = '{filt}';"
            "$d.CheckFileExists = $true;"
        )
        picked, what = "$d.FileName", "选文件"
    else:
        # 用 OpenFileDialog 冒充目录选择：塞占位名、取父目录（原因见 notes.md 第 37 行）
        head = owner + (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
            "$d = New-Object System.Windows.Forms.OpenFileDialog;"
            "$d.Title = '选择游戏目录';"
            "$d.CheckFileExists = $false;"
            "$d.ValidateNames = $false;"
            "$d.FileName = '选择这个文件夹';"
        )
        picked, what = "(Split-Path -Parent $d.FileName)", "选目录"
    script = head + ("$r = $d.ShowDialog($o); $o.Close(); "
                     "if ($r -eq [System.Windows.Forms.DialogResult]::OK) "
                     f"{{ [System.IO.File]::WriteAllText('{out_lit}', {picked}, "
                     "[System.Text.Encoding]::UTF8) }")
    last_error = ""
    try:
        for exe in ("powershell.exe", "pwsh.exe", "pwsh"):
            code, out, err = run_capture([exe, "-NoProfile", "-STA", "-Command", script],
                                         timeout=DIALOG_TIMEOUT)
            if code is None and err == "命令不存在":
                last_error = f"找不到 {exe}"
                continue
            if code is None:
                return None, f"{what}超时或没有界面（{exe}）"
            if code != 0:
                return None, f"{what}窗口没能打开（{exe}）"
            chosen = out_path.read_text(encoding="utf-8-sig").strip() if out_path.is_file() else ""
            if not chosen:
                return None, "已取消"
            return chosen, "好"
        return None, last_error or "找不到可用的 PowerShell"
    finally:
        out_path.unlink(missing_ok=True)


def check_als(path: str) -> str | None:
    """粗检别名文件：存不存在、是不是 .json。**不解析内容**（去敏文件里别的字段一律不读）。"""
    p = Path(path)
    if not p.is_file():
        return "这个路径不是文件"
    if p.suffix.lower() != ".json":
        return "这个文件不是 .json"
    return None


USER_DIR = PROJECT_DIR / "dataUser"                  # 用户数据（成绩/头像/用户表）与 data/ 曲库分开
USER_FILE_PATH = USER_DIR / "users.json"
STATS_SCRIPT = PROJECT_DIR / "tools" / "scores_stats" / "scores_stats.py"
RATING_SCRIPT = PROJECT_DIR / "tools" / "dxrating" / "dxrating.py"
DXRATING_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()
STATS_LOCK = threading.Lock()
DEFAULT_AVATAR = PROJECT_DIR / "dataUI" / "dummyHeadPicture" / "dummyHeadPicture.png"
AVATAR_EXTS = (".png", ".jpg", ".jpeg", ".webp")     # 不收 gif：会动的不支持
ANIMATED_EXTS = (".gif", ".apng", ".mng", ".flif")
AVATAR_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
AVATAR_SIDE = 256
AVATAR_MAX_BYTES = 64 * 1024 * 1024                 # 大图也能选：反正最后会裁成 256×256
PICK_CACHE: dict[str, Path] = {}                    # token → 待裁剪的原图
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes):
    """读 PNG 的 IHDR 宽高；不是 PNG 就返回 None。"""
    if len(data) < 24 or data[:8] != PNG_SIG or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def write_users(users: list) -> str:
    try:
        tmp = USER_FILE_PATH.with_suffix(".json.part")
        tmp.write_text(json.dumps({"schema": 1, "users": users}, ensure_ascii=False, indent=2) + chr(10),
                       encoding="utf-8")
        tmp.replace(USER_FILE_PATH)
    except OSError as exc:
        return f"写用户表失败：{exc}"
    return ""


def put_default_avatar(user_id: str) -> str:
    """新建用户时塞一张默认头像（dataUI/dummyHeadPicture，本来就是 256×256 PNG）。"""
    if not DEFAULT_AVATAR.is_file():
        return ""                                        # 没有默认图就不塞，不报错
    try:
        target = user_dir(user_id)
        target.mkdir(parents=True, exist_ok=True)
        (target / "avatar.png").write_bytes(DEFAULT_AVATAR.read_bytes())
    except OSError as exc:
        return f"放默认头像失败：{exc}"
    return ""


def pick_avatar_image(src: str) -> tuple[str | None, str]:
    """登记一张待裁剪的图，返回 token（页面用 /api/avatar-source?token= 取图再裁剪）。"""
    path = Path(src)
    if not path.is_file():
        return None, "选的不是文件"
    suffix = path.suffix.lower()
    if suffix in ANIMATED_EXTS:
        return None, "会动的图（gif 之类）不支持，换成 png / jpg / webp"
    if suffix not in AVATAR_EXTS:
        return None, "头像只收 png / jpg / jpeg / webp"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"读图失败：{exc}"
    if size > AVATAR_MAX_BYTES:
        return None, f"图片太大了（超过 {AVATAR_MAX_BYTES // 1048576}MB）"
    token = uuid.uuid4().hex
    PICK_CACHE[token] = path
    while len(PICK_CACHE) > 5:                      # 只留最近 5 张
        PICK_CACHE.pop(next(iter(PICK_CACHE)))
    return token, path.name


def save_avatar_png(user_id: str, data_url: str) -> tuple[str | None, str]:
    """收页面裁好、缩到 256×256 的 PNG，校验后落地成 <uuid>/avatar.png。"""
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        return None, "只收 PNG（页面会用 canvas 转好）"
    try:
        data = base64.b64decode(data_url[len(prefix):], validate=True)
    except (ValueError, TypeError):
        return None, "PNG 数据坏了"
    size = png_size(data)
    if size is None:
        return None, "这不是有效的 PNG"
    if size != (AVATAR_SIDE, AVATAR_SIDE):
        return None, f"尺寸必须是 {AVATAR_SIDE}×{AVATAR_SIDE}，收到的是 {size[0]}×{size[1]}"
    if not any(u.get("id") == user_id for u in load_users()):
        return None, "没有这个本地用户"
    try:
        target = user_dir(user_id)
        target.mkdir(parents=True, exist_ok=True)
        for old in target.glob("avatar.*"):              # 换图时清掉旧的（含旧格式）
            old.unlink(missing_ok=True)
        (target / "avatar.png").write_bytes(data)
    except OSError as exc:
        return None, f"存头像失败：{exc}"
    users = load_users()
    for user in users:
        if user.get("id") == user_id:
            user["avatar"] = "avatar.png"
    problem = write_users(users)
    return (None, problem) if problem else ("avatar.png", "头像已更新")


def user_dir(user_id: str) -> Path:
    return USER_DIR / user_id


def valid_user_id(user_id: str) -> bool:
    """只接受 UUID 形态的本地用户 id，防止 API 的 user 参数跳到 USER_DIR 之外。"""
    try:
        uuid.UUID(str(user_id or ""))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def file_response(handler, path: Path, root: Path) -> None:
    """把 root 下的文件发出去（不许跳出 root）。"""
    try:
        target = path.resolve()
        target.relative_to(root.resolve())
    except (OSError, ValueError):
        return handler._json({"error": "not found"}, 404)
    if not target.is_file():
        return handler._json({"error": "not found"}, 404)
    body = target.read_bytes()
    ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif", ".ttf": "font/ttf",
             ".otf": "font/otf", ".css": "text/css", ".js": "text/javascript"}.get(
                 target.suffix.lower(), "application/octet-stream")
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


COVER_DIR = PROJECT_DIR / "data" / "image" / "cover"

# ── 曲库浏览（GET /api/songs）：一次给整个曲库，排序 / 筛选 / 分页都在前端做 ──
# 排序口径（前端）：dataid 从大到小；同一个 dataid 里按 dx → std → utage → utage2p；
# 同族多张（宴谱）按难度从大到小。
FAMILIES = ("dx", "std", "utage", "utage2p")
sys.path.insert(0, str(PROJECT_DIR))              # 让 server 也能 import lib/
from lib.cache import is_fresh
from lib.versions import (RATING_VERSIONS, VERSION_FIX, VERSION_ORDER,  # noqa: F401
                          canonical_version)
from tools.play_assistant import assistant as assist
_SONG_BASE = {"key": None, "songs": []}
_COVER_IDS = {"mtime": None, "ids": set()}


def score_bits(row: dict | None) -> dict:
    """成绩行 → 页面要的几个数（没有成绩就全是 None，前端画白空位）。"""
    if not row:
        return {"rank": None, "dxscore": None, "combo": None, "sync": None}
    return {"rank": row.get("RANK"), "dxscore": row.get("dxscore"),
            "combo": row.get("comboStatus"), "sync": row.get("syncStatus"),
            "achievement": row.get("achievement")}          # ×10000（1010000 = 101.0000%），完成率用


def my_scores(user_id: str) -> dict:
    """(internalId, chartid) → 成绩行；没用户或没成绩就给空表。"""
    if not valid_user_id(user_id):
        return {}
    path = user_dir(user_id) / "scores.json"
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {(r.get("internalId"), r.get("chartid")): r for r in doc.get("scores") or []}


def cover_ids() -> set:
    """曲绘目录里实际存在的 dataid（目录 mtime 变了才重扫）。"""
    try:
        mtime = COVER_DIR.stat().st_mtime_ns
    except OSError:
        return set()
    if _COVER_IDS["mtime"] == mtime:
        return _COVER_IDS["ids"]
    ids = set()
    try:
        for name in os.listdir(COVER_DIR):
            low = name.lower()
            if low.startswith("ui_jacket_") and low.endswith(".png"):
                num = name[len("UI_Jacket_"):-4]
                if num.isdigit():
                    ids.add(int(num))
    except OSError:
        pass
    _COVER_IDS["mtime"], _COVER_IDS["ids"] = mtime, ids
    return ids


def song_base() -> list:
    """整个曲库（还没并成绩），fusion 文件变了才重读。"""
    files = [(family, PROJECT_DIR / "data" / "fusion" / ("fusion-" + family + ".json"))
             for family in FAMILIES]
    key = []
    for _, path in files:
        try:
            st = path.stat()
            key.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((str(path), 0, 0))
    key = tuple(key)
    if _SONG_BASE["key"] == key:
        return _SONG_BASE["songs"]
    songs = []
    for family, path in files:
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for song in doc.get("songs") or []:
            dataid = song.get("dataid")
            if not dataid:
                continue
            sheets = [dict(
                chartid=sh.get("chartid"),
                difficulty=sh.get("difficulty"),
                level=sh.get("dxratingLevelValue") or sh.get("localLevelValue"),
                levelText=sh.get("level"),          # 宴谱 "12?""14+?" 这种文本，卡片优先用它
            ) for sh in song.get("sheets") or []]
            names = {str(sh.get("difficulty") or "").lower() for sh in sheets}
            version = song.get("version") or ""
            songs.append({
                "internalId": song.get("internalId"),
                "family": family,
                "type": song.get("type"),
                "title": song.get("title"),
                "artist": song.get("artist"),
                "version": VERSION_FIX.get(version, version),
                "dataid": int(dataid),
                "hasRemaster": "remaster" in names,
                "aliasesTag": song.get("aliasesTag") or [],
                "sheets": sheets,
            })
    _SONG_BASE["key"], _SONG_BASE["songs"] = key, songs
    return songs


_PLATE_MASKS = {"key": None, "plates": []}


def plate_masks() -> list:
    """牌子条件：每块牌 → {internalId: chartid 位掩码}（chartid 0..31 各占一位），文件变了才重读。"""
    path = PROJECT_DIR / "data" / "plate_conditions" / "plates.json"
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    if _PLATE_MASKS["key"] == key:
        return _PLATE_MASKS["plates"]
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    out = []
    for plate in doc.get("plates") or []:
        mask = {}
        for row in plate.get("sheets") or []:
            iid = row.get("internalId")
            if iid is None:
                continue
            bits = 0
            for cid in row.get("chartid") or []:
                if isinstance(cid, int) and 0 <= cid < 32:
                    bits |= 1 << cid
            k = str(int(iid))
            mask[k] = mask.get(k, 0) | bits
        out.append({
            "id": plate.get("plateId"),
            "num": plate.get("plateNum"),
            "name": plate.get("name"),
            "cond": plate.get("condition"),
            "ver": plate.get("version"),
            "charts": plate.get("chartCount"),
            "mask": mask,
        })
    _PLATE_MASKS["key"], _PLATE_MASKS["plates"] = key, out
    return out


_SONG_INDEX = {"key": None, "map": {}}


def song_index() -> dict:
    """internalId → (family, song)，fusion 文件变了才重读（详情页按单曲取）。"""
    files = [(family, PROJECT_DIR / "data" / "fusion" / ("fusion-" + family + ".json"))
             for family in FAMILIES]
    key = []
    for _, path in files:
        try:
            st = path.stat()
            key.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((str(path), 0, 0))
    key = tuple(key)
    if _SONG_INDEX["key"] == key:
        return _SONG_INDEX["map"]
    index = {}
    for family, path in files:
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for song in doc.get("songs") or []:
            try:
                index[int(song["internalId"])] = (family, song)
            except (KeyError, TypeError, ValueError):
                continue
    _SONG_INDEX["key"], _SONG_INDEX["map"] = key, index
    return index


BLACKLIST_NAME = "blacklist.json"
from lib.blacklist import DURATION_DAYS as BLACKLIST_DURATIONS, expiry_of, is_active


FAVORITES_NAME = "favorites.json"


def favorites_load(user_id: str) -> dict:
    """收藏夹：{"entries": [{"internalId", "chartid", "addedAt"}]}，按用户独立存。"""
    if not valid_user_id(user_id):
        return {"entries": []}
    path = user_dir(user_id) / FAVORITES_NAME
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"entries": []}
    if not isinstance(doc, dict):
        return {"entries": []}
    doc.setdefault("entries", [])
    return doc


def favorites_save(user_id: str, doc: dict) -> None:
    if not valid_user_id(user_id):
        return
    path = user_dir(user_id) / FAVORITES_NAME
    tmp = path.with_suffix(".json.part")
    tmp.write_bytes((json.dumps(doc, ensure_ascii=False, indent=2) + chr(10)).encode("utf-8"))
    tmp.replace(path)


def favorites_map(user_id: str) -> dict:
    """{(internalId, chartid): addedAt}"""
    out = {}
    for entry in favorites_load(user_id).get("entries") or []:
        try:
            out[(int(entry["internalId"]), int(entry["chartid"]))] = entry.get("addedAt")
        except (KeyError, TypeError, ValueError):
            continue
    return out


def blacklist_load(user_id: str) -> dict:
    if not valid_user_id(user_id):
        return {"schema": 1, "entries": []}
    path = user_dir(user_id) / BLACKLIST_NAME
    if not path.is_file():
        return {"schema": 1, "entries": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"schema": 1, "entries": []}
    if not isinstance(doc.get("entries"), list):
        doc["entries"] = []
    return doc


def blacklist_save(user_id: str, doc: dict) -> None:
    if not valid_user_id(user_id):
        return
    path = user_dir(user_id) / BLACKLIST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def blacklist_active(user_id: str) -> dict:
    """生效中的黑名单：{(internalId, chartid): entry}。规则见 lib/blacklist.py。"""
    now = datetime.now(TZ_CN)
    out = {}
    for entry in blacklist_load(user_id).get("entries") or []:
        iid, cid = entry.get("internalId"), entry.get("chartid")
        if iid is None or cid is None or not is_active(entry, now.date()):
            continue
        expires = expiry_of(entry)
        entry = dict(entry, expiresAt=(expires.isoformat() if expires else None))
        out[(int(iid), int(cid))] = entry
    return out


def blacklist_upsert(user_id: str, internal_id: int, chartid: int, duration: str) -> dict:
    doc = blacklist_load(user_id)
    entry = {"internalId": int(internal_id), "chartid": int(chartid),
             "addedAt": datetime.now(TZ_CN).date().isoformat(),   # 只到日
             "duration": duration}
    doc["entries"] = [e for e in doc.get("entries") or []
                      if not (int(e.get("internalId") or -1) == int(internal_id)
                              and int(e.get("chartid") or -1) == int(chartid))] + [entry]
    blacklist_save(user_id, doc)
    return entry


def blacklist_remove(user_id: str, internal_id: int, chartid: int) -> bool:
    doc = blacklist_load(user_id)
    before = len(doc.get("entries") or [])
    doc["entries"] = [e for e in doc.get("entries") or []
                      if not (int(e.get("internalId") or -1) == int(internal_id)
                              and int(e.get("chartid") or -1) == int(chartid))]
    blacklist_save(user_id, doc)
    return len(doc["entries"]) != before


_FIT_DIFF = {"key": None, "map": {}}


_CHAIN_AGES = {"key": None, "map": {}}  # 保留变量兼容旧代码，不再作为缓存


def chain_ages() -> dict:
    """每条链的产物里最新的那个文件：{chain: {ageDays, updatedAt}}（UI 显示「N 天前」）。

    不能按 chains.json 的 mtime 缓存：产物更新后 chains.json 不变，页面会一直显示旧天数。
    """
    chains_path = PROJECT_DIR / "tools" / "run_chain" / "chains.json"
    try:
        doc = json.loads(chains_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time()
    out = {}
    for name, chain in (doc.get("chains") or {}).items():
        newest = None
        for step in chain.get("steps") or []:
            for rel in step.get("products") or []:
                try:
                    m = (PROJECT_DIR / rel).stat().st_mtime
                except OSError:
                    continue
                newest = m if newest is None else max(newest, m)
        if newest is None:
            continue
        out[name] = {"ageDays": round((now - newest) / 86400, 2),
                     "updatedAt": datetime.fromtimestamp(newest, TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")}
    return out


def fit_diffs() -> dict:
    """社区拟合定数：{"<iid>:<cid>": {fitDiff, avg, cnt}}，fit_diff.json 变了才重读。"""
    path = PROJECT_DIR / "data" / "fit_diff" / "fit_diff.json"
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _FIT_DIFF["key"] == key:
        return _FIT_DIFF["map"]
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        out = doc.get("charts") or {}
    except (OSError, ValueError):
        out = {}
    _FIT_DIFF["key"], _FIT_DIFF["map"] = key, out
    return out


_ANALYSIS_LEVELS = {"key": None, "map": {}, "model": {}}


def analysis_levels(user_id: str) -> tuple[dict, dict]:
    """分析结果里的有效定数 + fitModel；analysis.json 变了才重读。"""
    if not valid_user_id(user_id):
        return {}, {}
    path = user_dir(user_id) / "analysis.json"
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}, {}
    if _ANALYSIS_LEVELS["key"] == key:
        return _ANALYSIS_LEVELS["map"], _ANALYSIS_LEVELS["model"]
    out = {}
    model = {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        model = doc.get("fitModel") or {}
        for row in doc.get("charts") or []:
            if row.get("effectiveLevel") is None:
                continue
            out[(int(row.get("internalId") or 0), int(row.get("chartid") or 0))] = {
                "fit": row["effectiveLevel"],
                "eval": row.get("fitEvalLevel"),
                "tier": row.get("fitTier"),
                "tagCorr": row.get("tagCorr"),
                "tagRaw": row.get("tagRaw"),
                "tagK": row.get("tagK"),
                "tagFactor": row.get("tagFactor"),
                "tagBalance": row.get("tagBalance"),
                "tagScope": row.get("tagScope"),
                "tagSample": row.get("tagSample"),
                "waterKind": row.get("waterKind"),
                "waterPrior": row.get("waterPrior"),
            }
    except (OSError, ValueError, TypeError):
        out, model = {}, {}
    _ANALYSIS_LEVELS["key"], _ANALYSIS_LEVELS["map"], _ANALYSIS_LEVELS["model"] = key, out, model
    return out, model


def song_detail(internal_id: int, user_id: str) -> dict | None:
    """单曲全量：曲目字段 + 每张谱的定数/物量/谱师/版本/日期/可用服/三组标签 + 当前用户成绩。"""
    if user_id and not valid_user_id(user_id):
        return None
    hit = song_index().get(int(internal_id))
    if not hit:
        return None
    family, song = hit
    scored = my_scores(user_id)
    blocked = blacklist_active(user_id)
    levels, fit_model = analysis_levels(user_id)
    favs = favorites_map(user_id)
    fits = fit_diffs()
    sheets = []
    for sh in song.get("sheets") or []:
        row = scored.get((song.get("internalId"), sh.get("chartid"))) or {}
        info = levels.get((song.get("internalId"), sh.get("chartid"))) or {}
        sheets.append({
            "chartid": sh.get("chartid"),
            "difficulty": sh.get("difficulty"),
            "levelText": sh.get("level"),
            "dxratingLevel": sh.get("dxratingLevelValue"),
            "localLevel": sh.get("localLevelValue"),
            "fitLevel": info.get("fit"),
            "fitEvalLevel": info.get("eval"),
            "fitTier": info.get("tier"),
            "tagCorr": info.get("tagCorr"),
            "tagRaw": info.get("tagRaw"),
            "tagK": info.get("tagK"),
            "tagFactor": info.get("tagFactor"),
            "tagBalance": info.get("tagBalance"),
            "tagScope": info.get("tagScope"),
            "tagSample": info.get("tagSample"),
            "waterKind": info.get("waterKind"),
            "waterPrior": info.get("waterPrior"),
            "fitDiff": (fits.get(f"{song.get('internalId')}:{sh.get('chartid')}") or {}).get("fitDiff"),
            "noteDesigner": sh.get("noteDesigner"),
            "noteCounts": sh.get("noteCounts"),
            "version": sh.get("version"),
            "releaseDate": sh.get("releaseDate"),
            "serverIds": sh.get("serverIds") or [],
            "isSpecial": sh.get("isSpecial"),
            "historyLevelValue": sh.get("historyLevelValue") or [],
            "PatternsTag": sh.get("PatternsTag") or [],
            "DifficultyTag": sh.get("DifficultyTag") or [],
            "EvaluationTag": sh.get("EvaluationTag") or [],
            "rank": row.get("RANK"),
            "dxscore": row.get("dxscore"),
            "combo": row.get("comboStatus"),
            "sync": row.get("syncStatus"),
            "favorite": (song.get("internalId"), sh.get("chartid")) in favs,
            "favoriteAt": favs.get((song.get("internalId"), sh.get("chartid"))),
            "blacklisted": (song.get("internalId"), sh.get("chartid")) in blocked,
            "blacklist": blocked.get((song.get("internalId"), sh.get("chartid"))),
            "achievement": row.get("achievement"),
            "playCount": row.get("playCount"),
            "deluxscoreMax": row.get("deluxscoreMax"),
        })
    dataid = song.get("dataid")
    return {"song": {
        "internalId": song.get("internalId"),
        "family": family,
        "type": song.get("type"),
        "dataid": dataid,
        "title": song.get("title"),
        "artist": song.get("artist"),
        "category": song.get("category"),
        "bpm": song.get("bpm"),
        "version": VERSION_FIX.get(song.get("version") or "", song.get("version") or ""),
        "isNew": song.get("isNew"),
        "isLocked": song.get("isLocked"),
        "aliasesTag": song.get("aliasesTag") or [],
        "jacket": ("/jacket?dataid=%d" % int(dataid)) if dataid else None,
        "sheets": sheets,
        "fitModel": fit_model,
    }}


def all_songs(user_id: str) -> dict:
    """整库 + 当前用户的成绩 + 曲绘是否存在；排序/筛选/分页交给前端。"""
    scored = my_scores(user_id)
    covers = cover_ids()
    songs = []
    for song in song_base():
        dataid = song["dataid"]
        sheets = [dict(sh, **score_bits(scored.get((song["internalId"], sh["chartid"]))))
                  for sh in song["sheets"]]
        songs.append({
            "internalId": song["internalId"],
            "family": song["family"],
            "type": song["type"],
            "title": song["title"],
            "artist": song["artist"],
            "version": song["version"],
            "dataid": dataid,
            "hasRemaster": song["hasRemaster"],
            "aliasesTag": song["aliasesTag"],
            "jacket": ("/jacket?dataid=%d" % dataid) if dataid in covers else None,
            "sheets": sheets,
        })
    return {"songs": songs, "versions": VERSION_ORDER, "count": len(songs)}


def sample_songs(per_family: int = 1, user_id: str = "", only_family: str = "") -> list:
    """每个谱面族挑几首（带曲绘的），带上该难度的成绩（没有就是 null）。"""
    scored = my_scores(user_id)
    out = []
    for family in ([only_family] if only_family else ["std", "dx", "utage", "utage2p"]):
        path = PROJECT_DIR / "data" / "fusion" / ("fusion-" + family + ".json")
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        picked = 0
        candidates = []                          # 先收带曲绘的歌，再挑“有的难度有成绩、有的没有”的
        for song in doc.get("songs") or []:
            dataid = song.get("dataid")
            if not dataid:
                continue
            if not (COVER_DIR / ("UI_Jacket_%06d.png" % int(dataid))).is_file():
                continue
            sheets = [dict(
                chartid=sh.get("chartid"),
                difficulty=sh.get("difficulty"),
                level=sh.get("dxratingLevelValue") or sh.get("localLevelValue"),
                **score_bits(scored.get((song.get("internalId"), sh.get("chartid")))),
            ) for sh in song.get("sheets") or []]
            hit = sum(1 for sh in sheets if sh.get("rank") is not None)
            candidates.append((hit, len(sheets), {
                "family": family,
                "type": song.get("type"),
                "title": song.get("title"),
                "artist": song.get("artist"),
                "version": song.get("version"),
                "dataid": dataid,
                "jacket": "/jacket?dataid=%d" % int(dataid),
                "sheets": sheets,
            }))
        if not candidates:
            continue
        # 优先：部分难度有成绩（0 < 有成绩数 < 难度数）
        partial = [c for c in candidates if 0 < c[0] < c[1]]
        chosen = partial[:per_family]
        if len(chosen) < per_family:                 # 部分成绩的歌不够时，用其余带曲绘的补齐
            rest = [c for c in candidates if not (0 < c[0] < c[1])]
            chosen = chosen + rest[:per_family - len(chosen)]
        for _, _, entry in chosen:
            out.append(entry)
    return out


def sample_group(family: str, want_remaster: bool, count: int, scored: dict) -> list:
    """按 (谱面族, 有没有 Re:MASTER) 挑几首带曲绘的歌，带上该用户在这几张谱上的成绩。"""
    path = PROJECT_DIR / "data" / "fusion" / ("fusion-" + family + ".json")
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    partial, rest = [], []
    for song in doc.get("songs") or []:
        dataid = song.get("dataid")
        if not dataid:
            continue
        if not (COVER_DIR / ("UI_Jacket_%06d.png" % int(dataid))).is_file():
            continue
        names = {str(sh.get("difficulty") or "").lower() for sh in song.get("sheets") or []}
        if ("remaster" in names) != want_remaster:
            continue
        sheets = [dict(
            chartid=sh.get("chartid"),
            difficulty=sh.get("difficulty"),
            level=sh.get("dxratingLevelValue") or sh.get("localLevelValue"),
            levelText=sh.get("level"),          # utage 的 "12?""14+?" 这种文本，卡片优先用它
            **score_bits(scored.get((song.get("internalId"), sh.get("chartid")))),
        ) for sh in song.get("sheets") or []]
        hit = sum(1 for sh in sheets if sh.get("rank") is not None)
        entry = {
            "family": family,
            "type": song.get("type"),
            "title": song.get("title"),
            "artist": song.get("artist"),
            "version": song.get("version"),
            "dataid": dataid,
            "hasRemaster": "remaster" in names,
            "jacket": "/jacket?dataid=%d" % int(dataid),
            "sheets": sheets,
        }
        (partial if 0 < hit < len(sheets) else rest).append(entry)
    picked = partial[:count]
    if len(picked) < count:
        picked += rest[:count - len(picked)]
    return picked


def sample_groups(spec: list, user_id: str) -> list:
    """spec = [(family, want_remaster, count), ...]，按顺序各挑几首。"""
    scored = my_scores(user_id)
    out = []
    for family, want_remaster, count in spec:
        out.extend(sample_group(family, want_remaster, count, scored))
    return out


def _run_dxrating_unlocked(user_id: str, version: str) -> tuple[dict | None, str]:
    """跑一次按版本算 DX rating 的脚本，读它写出来的 dxrating.json。"""
    if not valid_user_id(user_id):
        return None, "用户 id 不合法"
    version = canonical_version(version)
    if version not in RATING_VERSIONS:
        return None, "不支持的 DX rating 版本"
    if not RATING_SCRIPT.is_file():
        return None, f"缺脚本：{RATING_SCRIPT}"
    dx_path = user_dir(user_id) / "dxrating.json"
    dx_dir = user_dir(user_id)
    if _script_fresh(dx_path, [dx_dir / "scores.json",
                               *sorted((PROJECT_DIR / "data" / "fusion").glob("fusion-*.json")),
                               *sorted((PROJECT_DIR / "data" / "dxrating_build").glob("dxrating-*.json")),
                               PROJECT_DIR / "lib" / "versions.py", RATING_SCRIPT]):
        try:
            doc = _read_doc(dx_path)
            if doc.get("version") == version:
                return doc, ""
        except (OSError, ValueError):
            pass
    cmd = [sys.executable, str(RATING_SCRIPT), "--user", user_id, "--version", version]
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_DIR), stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"跑不起来：{exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or b"").decode("utf-8", "ignore").strip()[:200] or "算 rating 失败"
    try:
        doc = json.loads((user_dir(user_id) / "dxrating.json").read_text(encoding="utf-8-sig"))
        if doc.get("version") != version:
            return None, "rating 结果的版本和请求不一致，请重试"
        return doc, ""
    except (OSError, ValueError) as exc:
        return None, f"结果读不出来：{exc}"


def run_dxrating(user_id: str, version: str) -> tuple[dict | None, str]:
    """串行化同一时刻的 rating 计算/读写，避免并发覆盖同一个 dxrating.json。"""
    with DXRATING_LOCK:
        return _run_dxrating_unlocked(user_id, version)


ANALYSIS_SCRIPT = PROJECT_DIR / "tools" / "analyze_profile" / "analyze_profile.py"


_DOC_CACHE = {"key": None, "doc": None}


def _read_doc(path):
    """读 JSON 文档，按 (路径, mtime, 大小) 缓存一份（analysis.json 有 1.5MB，别每次重解析）。"""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _DOC_CACHE["key"] == key:
        return _DOC_CACHE["doc"]
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    _DOC_CACHE["key"], _DOC_CACHE["doc"] = key, doc
    return doc


def _script_fresh(out_path, inputs) -> bool:
    """输出比所有输入（成绩/曲库/脚本自己）都新 → 不用跑脚本，直接读旧产物。"""
    return is_fresh(out_path, inputs)


def run_analysis(user_id: str) -> tuple[dict | None, str]:
    """串行化同一时刻的分析进程，避免多个线程同时写 analysis.json。"""
    with ANALYSIS_LOCK:
        return _run_analysis_unlocked(user_id)


def _run_analysis_unlocked(user_id: str) -> tuple[dict | None, str]:
    """跑一次分析脚本（卡池预览每次进来都会激活它），再读它写出来的 analysis.json。"""
    if not valid_user_id(user_id):
        return None, "用户 id 不合法"
    if not ANALYSIS_SCRIPT.is_file():
        return None, f"缺分析脚本：{ANALYSIS_SCRIPT}"
    path = user_dir(user_id) / "analysis.json"
    udir = user_dir(user_id)
    if _script_fresh(path, [udir / "scores.json", udir / "blacklist.json", udir / "tag_offsets.json",
                            PROJECT_DIR / "data" / "fit_diff" / "fit_diff.json",
                            PROJECT_DIR / "lib" / "versions.py",
                            *sorted((PROJECT_DIR / "data" / "fusion").glob("fusion-*.json")),
                            ANALYSIS_SCRIPT]):
        try:
            return _read_doc(path), ""
        except (OSError, ValueError):
            pass
    cmd = [sys.executable, str(ANALYSIS_SCRIPT), "--user", user_id]
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_DIR), stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"分析脚本跑不起来：{exc}"
    path = user_dir(user_id) / "analysis.json"
    if proc.returncode != 0 or not path.is_file():
        err = (proc.stderr or b"").decode("utf-8", "ignore").strip()[:200]
        return None, err or f"分析没跑成（退出码 {proc.returncode}）"
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), ""
    except (OSError, ValueError) as exc:
        return None, f"读分析结果失败：{exc}"


def run_stats(user_id: str) -> tuple[dict | None, str]:
    """串行化同一时刻的统计进程，避免并发写 stats.json。"""
    with STATS_LOCK:
        return _run_stats_unlocked(user_id)


def _run_stats_unlocked(user_id: str) -> tuple[dict | None, str]:
    """跑一次统计脚本（成绩页每次进来都会激活它），再读它写出来的 stats.json。"""
    if not valid_user_id(user_id):
        return None, "用户 id 不合法"
    if not STATS_SCRIPT.is_file():
        return None, f"缺统计脚本：{STATS_SCRIPT}"
    st_path = user_dir(user_id) / "stats.json"
    st_dir = user_dir(user_id)
    if _script_fresh(st_path, [st_dir / "scores.json", st_dir.parent / "users.json",
                               PROJECT_DIR / "lib" / "versions.py",
                               *sorted((PROJECT_DIR / "data" / "fusion").glob("fusion-*.json")),
                               STATS_SCRIPT]):
        try:
            return _read_doc(st_path), ""
        except (OSError, ValueError):
            pass
    cmd = [sys.executable, str(STATS_SCRIPT), "--user", user_id]
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_DIR), stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"统计脚本跑不起来：{exc}"
    path = user_dir(user_id) / "stats.json"
    if proc.returncode != 0 or not path.is_file():
        return None, f"统计没跑成（退出码 {proc.returncode}）"
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), ""
    except (OSError, ValueError) as exc:
        return None, f"读统计失败：{exc}"


def load_users() -> list:
    """本地用户对照表（uuid ↔ 名字）。没有文件就是空表。"""
    try:
        doc = json.loads(USER_FILE_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    users = doc.get("users")
    return users if isinstance(users, list) else []


def write_users(users: list) -> str:
    """原子写回 users.json；返回错误字符串，空串表示成功。"""
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        tmp = USER_FILE_PATH.with_suffix(".json.part")
        tmp.write_text(json.dumps({"schema": 1, "users": users}, ensure_ascii=False, indent=2) + chr(10),
                       encoding="utf-8")
        tmp.replace(USER_FILE_PATH)
        return ""
    except OSError as exc:
        return f"写用户表失败：{exc}"


def save_user_order(order: list) -> tuple[bool, str]:
    """按用户给的 id 顺序重排 users.json；必须与当前用户集合完全一致。"""
    ids = []
    for value in order or []:
        uid = str(value or "").strip()
        if uid:
            ids.append(uid)
    users = load_users()
    current = [str(u.get("id")) for u in users]
    if len(ids) != len(set(ids)) or set(ids) != set(current):
        return False, "用户列表已经变了，刷新后再试"
    by_id = {str(u.get("id")): u for u in users}
    problem = write_users([by_id[uid] for uid in ids])
    return (False, problem) if problem else (True, "顺序已保存")


def create_user(name: str) -> tuple[dict | None, str]:
    """新建一个本地用户（uuid 当目录名）。重名就复用已有的那个。"""
    name = (name or "").strip()
    if not name:
        return None, "用户名不能为空"
    users = load_users()
    for user in users:
        if user.get("name") == name:
            return None, "已经有同名的本地用户了，换个名字"   # 用户要求：本地用户不能重名
    user = {"id": str(uuid.uuid4()), "name": name,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S+0800", time.localtime())}
    users.append(user)
    problem = put_default_avatar(user["id"])             # 新建用户先给张默认头像
    if not problem:
        user["avatar"] = "avatar.png"
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        tmp = USER_FILE_PATH.with_suffix(".json.part")
        tmp.write_text(json.dumps({"schema": 1, "users": users}, ensure_ascii=False, indent=2) + chr(10),
                       encoding="utf-8")
        tmp.replace(USER_FILE_PATH)
    except OSError as exc:
        return None, f"写用户表失败：{exc}"
    return user, "新建好了"


def rename_user(user_id: str, name: str) -> tuple[dict | None, str]:
    """改用户名：只改 users.json 里的 name，目录/成绩/头像都不动。"""
    name = (name or "").strip()
    if not name:
        return None, "用户名不能为空"
    users = load_users()
    hit = next((u for u in users if str(u.get("id")) == user_id), None)
    if hit is None:
        return None, "用户表里没有这个 id"
    for u in users:
        if u is not hit and u.get("name") == name:
            return None, "已经有同名的本地用户了，换个名字"
    hit["name"] = name
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        tmp = USER_FILE_PATH.with_suffix(".json.part")
        tmp.write_text(json.dumps({"schema": 1, "users": users}, ensure_ascii=False, indent=2) + chr(10),
                       encoding="utf-8")
        tmp.replace(USER_FILE_PATH)
    except OSError as exc:
        return None, f"写用户表失败：{exc}"
    return hit, "改好了"


def delete_user(user_id: str) -> tuple[bool, str]:
    """删掉一个本地用户：用户表里去掉 + 整个用户目录删掉。
    只认 USER_DIR 下、id 是 uuid 形态的目录，别的一律不动。"""
    user_id = (user_id or "").strip()
    if not user_id:
        return False, "没指定用户"
    users = load_users()
    if not any(str(u.get("id")) == user_id for u in users):
        return False, "用户表里没有这个 id"
    try:
        uuid.UUID(user_id)                                  # 形态校验，防手滑删到别的目录
    except ValueError:
        return False, "用户 id 不合法"
    folder = user_dir(user_id)
    if folder.parent != USER_DIR:
        return False, "用户目录不在用户根目录下"
    try:
        if folder.is_dir():
            shutil.rmtree(folder)
        rest = [u for u in users if str(u.get("id")) != user_id]
        tmp = USER_FILE_PATH.with_suffix(".json.part")
        tmp.write_text(json.dumps({"schema": 1, "users": rest}, ensure_ascii=False, indent=2) + chr(10),
                       encoding="utf-8")
        tmp.replace(USER_FILE_PATH)
    except OSError as exc:
        return False, f"删除失败：{exc}"
    return True, "删掉了"


def check_scores(path: str) -> str | None:
    """粗检 MuNET 导出的存档：是 .json 就行（内容怎么读交给转换脚本，这里不解析）。"""
    p = Path(path)
    if not p.is_file():
        return "这个路径不是文件"
    if p.suffix.lower() != ".json":
        return "这个文件不是 .json"
    return None


def check_game_package(path: str) -> str | None:
    """粗检：这个目录下面有没有 musicNNNNNN/Music.xml（跟 localMusicData_build 的找法一致）。

    真实布局是三层：StreamingAssets/<包>/music/musicNNNNNN/Music.xml
    （实测 A000\\music\\music000008\\Music.xml）。选 StreamingAssets 或选某个包目录都能认出来。
    """
    base = Path(path)
    if not base.is_dir():
        return "这个路径不是文件夹"
    for depth in (1, 2, 3):
        if next(base.glob("/".join(["*"] * depth) + "/Music.xml"), None):
            return None
    return "没在这个目录下面找到 musicNNNNNN/Music.xml —— 确认选的是游戏目录（Package）吗？"


def looks_like_base(path: Path) -> bool:
    """StreamingAssets 的形状：孩子里有含 music/ 的目录 —— 这正是构建脚本认的那个 base。"""
    try:
        return any(child.is_dir() and (child / "music").is_dir() for child in path.iterdir())
    except OSError:
        return False


def looks_like_package(path: Path) -> bool:
    """包目录的形状：自己下面有 music/musicNNNNNN/Music.xml。"""
    music = path / "music"
    try:
        return music.is_dir() and next(music.glob("*/Music.xml"), None) is not None
    except OSError:
        return False


def chain_wants_package_dir(chain_name: str) -> bool:
    """这条链要的是**单个包目录**（数据小更新），不是 StreamingAssets 根：看它的步骤有没有 packageDir。"""
    chain = load_chains().get(chain_name) or {}
    return any("packageDir" in (s.get("needs") or []) for s in chain.get("steps") or [])


def resolve_game_package(path: str, keep_package_dir: bool = False) -> tuple[str | None, str]:
    """把用户选中的目录解析成构建脚本真正要的 base。返回 (base 或 None, 说明)。

    允许选到 `Package` 为止（游戏根 / SDEZ170 不再往上认）：从选中目录往下最多找 2 层，
    即 StreamingAssets / Sinmai_Data / Package 这三种都能选；
    选到**单个包目录**时改读它的上级 —— localMusicData_build 只读一个包的话，
    会丢掉「包名大的覆盖小的」那部分（A003 覆盖 A000、N100 覆盖 A000 是实测存在的）。

    keep_package_dir=True（数据小更新那条链）反过来：就要这个包目录本身 —— 那一步是
    `--merge`，只把这个包的新内容并进现有曲库，不存在"丢掉别的包"的问题。
    """
    base = Path(path)
    if not base.is_dir():
        return None, "这个路径不是文件夹"
    if looks_like_base(base):
        return str(base), "好"
    if looks_like_package(base):
        if keep_package_dir:
            return str(base), f"数据小更新：只读这个包目录 {base.name}"
        parent = base.parent
        if looks_like_base(parent):
            return str(parent), f"你选的是包目录 {base.name}，已改读它的上级（否则会丢掉别的包的覆盖）"
        return str(base), f"只读包目录 {base.name}（它上级没有别的包）"
    for depth in (1, 2):
        for candidate in sorted(base.glob("/".join(["*"] * depth))):
            if candidate.is_dir() and looks_like_base(candidate):
                return str(candidate), f"从 {base.name} 里找到 {candidate.relative_to(base)}"
    return None, "没找到 <包>/music/musicNNNNNN/Music.xml —— 选 StreamingAssets、Sinmai_Data 或 Package 都行"


class RunState:
    """当前任务状态。同一时间只允许一个任务。"""

    def __init__(self):
        # 必须可重入：handle_line 在持锁状态下调 append_log，而 append_log 自己也要拿这把锁。
        # 用普通 Lock 会在第一条 RUNCHAIN 事件上自锁死 —— 读线程抱着锁不放，
        # 之后所有碰状态的接口（/api/build、/api/run、/api/stop）全部卡住。
        self.lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.running = False
        self.run_id: str | None = None
        self.chain: str | None = None
        self.game_package: str | None = None
        self.started_at: str | None = None
        self.seconds = 0.0
        self.steps: list[dict] = []
        self.log: list[str] = []
        self.result: dict | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running, "runId": self.run_id, "chain": self.chain,
                "gamePackage": self.game_package, "startedAt": self.started_at,
                "seconds": round(self.seconds, 1),
                "steps": [dict(s) for s in self.steps],
                "log": list(self.log[-LOG_TAIL:]),
                "result": dict(self.result) if self.result else None,
            }

    def append_log(self, line: str) -> None:
        with self.lock:
            self.log.append(line)
            del self.log[:-LOG_TAIL]

    def step_by_name(self, name: str) -> dict | None:
        for step in self.steps:
            if step["script"] == name:
                return step
        return None


STATE = RunState()


def load_chains() -> dict:
    return json.loads(CHAINS_FILE.read_text(encoding="utf-8"))["chains"]


def start_build(chain: str, game_package: str | None,
                als: str | None = None, scores: str | None = None,
                user: str | None = None) -> tuple[bool, str]:
    """起一条链。返回 (是否启动, 说明)。"""
    with STATE.lock:
        if STATE.running:
            return False, "已经有一个任务在跑了"
    try:
        chains = load_chains()
    except Exception as exc:
        return False, f"读不到步骤表：{exc}"
    if chain not in chains:
        return False, f"没有这条链：{chain}"
    steps = list(chains[chain].get("steps") or [])
    if any("gamePackage" in (s.get("needs") or []) for s in steps) and not game_package:
        return False, "这条链要用游戏包目录，先选目录"
    if game_package and any("gamePackage" in (s.get("needs") or []) for s in steps):
        problem = check_game_package(game_package)
        if problem:
            return False, f"游戏包目录不对：{problem}"      # 在这里挡住，别跑到第 3 步才炸
    needs_als = any("als" in (s.get("needs") or []) for s in steps)
    if needs_als and not als:
        return False, "这条链要用 Chiffonmai 的别名文件，先选文件"
    if als and needs_als:
        problem = check_als(als)
        if problem:
            return False, f"别名文件不对：{problem}"
    needs_scores = any("scores" in (s.get("needs") or []) for s in steps)
    if needs_scores and not scores:
        return False, "这条链要用 MuNET 导出的存档，先选文件"
    if scores and needs_scores:
        problem = check_scores(scores)
        if problem:
            return False, f"存档文件不对：{problem}"
    needs_user = any("user" in (s.get("needs") or []) for s in steps)
    if needs_user and not user:
        return False, "这条链要选一个本地用户（下拉里挑，或先新建）"
    if user and needs_user and not any(u.get("id") == user for u in load_users()):
        return False, "这个本地用户不在 users.json 里，先新建"

    run_id = uuid.uuid4().hex[:12]
    cmd = [sys.executable, str(CHAIN_SCRIPT), "--chain", chain, "--run-id", run_id,
           "--chains-file", str(CHAINS_FILE)]        # 必须和 /api/config 报的那张表是同一张
    if game_package:
        cmd += ["--game-package", game_package]
    if als:
        cmd += ["--als", als]
    if scores:
        cmd += ["--scores", scores]
    if user:
        cmd += ["--user", user]
    LOG_DIR.mkdir(exist_ok=True)
    out_path = LOG_DIR / f"{run_id}.chain.out.log"
    err_path = LOG_DIR / f"{run_id}.chain.err.log"

    with STATE.lock:
        STATE.running = True
        STATE.run_id = run_id
        STATE.chain = chain
        STATE.game_package = game_package
        STATE.started_at = now_stamp()
        STATE.seconds = 0.0
        STATE.result = None
        STATE.log = []
        STATE.steps = [{"script": s["script"], "state": "pending", "needs": s.get("needs") or [],
                        "seconds": None, "exit": None, "products": []} for s in steps]

    def reader() -> None:
        started = time.time()
        env = {**os.environ, RUN_ID_ENV_VAR: run_id, "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        with open(out_path, "wb") as out_handle, open(err_path, "wb") as err_handle:
            proc = subprocess.Popen(cmd, cwd=str(PROJECT_DIR), env=env,
                                    stdout=out_handle, stderr=err_handle)
            with STATE.lock:
                STATE.proc = proc
            pos = 0
            while True:
                with open(out_path, "r", encoding="utf-8", errors="replace") as reader_handle:
                    reader_handle.seek(pos)
                    chunk = reader_handle.read()
                    pos = reader_handle.tell()
                for line in chunk.splitlines():
                    handle_line(line)
                with STATE.lock:
                    STATE.seconds = time.time() - started
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            with open(out_path, "r", encoding="utf-8", errors="replace") as reader_handle:
                reader_handle.seek(pos)
                for line in reader_handle.read().splitlines():
                    handle_line(line)
        code = proc.returncode
        stderr_text = err_path.read_text(encoding="utf-8", errors="replace").strip()
        with STATE.lock:
            STATE.running = False
            STATE.proc = None
            STATE.seconds = time.time() - started
            if STATE.result is None:                       # run_chain 没来得及报结果就死了
                STATE.result = {"ok": code == 0, "code": code,
                                "reason": f"run_chain 退出码 {code}" + (f"：{stderr_text[-400:]}" if stderr_text else "")}
                for step in STATE.steps:
                    if step["state"] in ("pending", "running"):
                        step["state"] = "failed"
                        break
        STATE.append_log(f"—— 任务结束，退出码 {code} ——")

    def handle_line(line: str) -> None:
        if not line.startswith("RUNCHAIN "):
            if line.strip():
                STATE.append_log(line)
            return
        try:
            event = json.loads(line[len("RUNCHAIN "):])
        except Exception:
            STATE.append_log(line)
            return
        kind = event.get("event")
        name = event.get("script")
        with STATE.lock:
            if kind == "run.start":
                STATE.append_log(f"批次 {event.get('runId')} · 链 {event.get('chain')}")
            elif kind == "step.log":
                text = event.get("line", "")
                if text.strip():
                    STATE.append_log(f"[{name}] {text}")
            elif kind == "step.start":
                step = STATE.step_by_name(name)
                if step:
                    step["state"] = "running"
            elif kind == "step.done":
                step = STATE.step_by_name(name)
                if step:
                    step["state"] = "done"
                    step["seconds"] = event.get("seconds")
                    step["exit"] = event.get("exit")
                    step["products"] = event.get("products") or []
            elif kind == "step.fail":
                step = STATE.step_by_name(name)
                if step:
                    step["state"] = "failed"
                    step["reason"] = event.get("reason")
            elif kind == "run.done":
                STATE.result = {"ok": True, "code": 0, "reason": "全部成功"}
            elif kind == "run.fail":
                STATE.result = {"ok": False, "code": None, "reason": event.get("reason"),
                                "step": event.get("step")}

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return True, run_id


ASSIST = {"user": None, "proc": None, "logPath": None, "startedAt": None}
ASSIST_LOCK = threading.RLock()
BRIDGE_SCRIPT = PROJECT_DIR / "tools" / "play_assistant" / "bridge.py"


def assistant_analysis_map(user_id: str) -> dict:
    path = user_dir(user_id) / "analysis.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out = {}
    for row in doc.get("charts") or []:
        out[(int(row.get("internalId") or 0), int(row.get("chartid") or 0))] = row
    return out


def assistant_song_brief(song: dict, sheet: dict) -> dict:
    """把 all_songs 的一首歌裁成「只含选中难度」的卡，供 songCard() 复用。"""
    return {
        "internalId": song.get("internalId"), "family": song.get("family"),
        "type": song.get("type"), "title": song.get("title"), "artist": song.get("artist"),
        "version": song.get("version"), "dataid": song.get("dataid"),
        "jacket": song.get("jacket"), "sheets": [dict(sheet)],
    }


def assistant_candidates(user_id: str, card: dict) -> list[dict]:
    """按卡池/设置构造候选（chart 粒度）。"""
    pool = card.get("pool")
    settings = card.get("settings") or {}
    songs = all_songs(user_id).get("songs") or []
    analysis = assistant_analysis_map(user_id)
    favorites = favorites_map(user_id)
    blacklist = blacklist_active(user_id)
    out = []
    for song in songs:
        fam = song.get("family")
        if fam in ("utage", "utage2p"):
            continue
        for sheet in song.get("sheets") or []:
            key = (song.get("internalId"), sheet.get("chartid"))
            if key in blacklist:
                continue
            row = analysis.get(key) or {}
            labels = set(row.get("labels") or [])
            if pool == "underdog":
                level = sheet.get("level")
                try:
                    level = float(level)
                except (TypeError, ValueError):
                    level = None
                if level is not None:
                    if settings.get("levelMin") not in (None, "") and level < float(settings["levelMin"]):
                        continue
                    if settings.get("levelMax") not in (None, "") and level > float(settings["levelMax"]):
                        continue
                diffs = settings.get("difficulties") or []
                if diffs and str(sheet.get("difficulty") or "").lower() not in {d.lower() for d in diffs}:
                    continue
                if settings.get("version") and song.get("version") != settings["version"]:
                    continue
                ach = sheet.get("achievement")
                ach = ach / 10000.0 if ach else None
                if ach is None and settings.get("excludeUnplayed"):
                    continue
                combo = sheet.get("combo")
                sync = sheet.get("sync")
                # 成绩 / 连击 / 双人：配置了的条件之间取“或”；
                # 三项都没配置时不限制；未游玩没有对应数据，只可能靠其它已配置项通过。
                limits = []
                if settings.get("achMax") not in (None, ""):
                    limits.append(ach is not None and ach < float(settings["achMax"]))
                if settings.get("comboMax") not in (None, ""):
                    limits.append(combo is not None and int(combo) < int(settings["comboMax"]))
                if settings.get("syncMax") not in (None, ""):
                    limits.append(sync is not None and int(sync) < int(settings["syncMax"]))
                if limits and not any(limits):
                    continue
                plate_id = settings.get("plateId")
                if plate_id:
                    hit = next((p for p in plate_masks() if str(p.get("id")) == str(plate_id)), None)
                    bits = (hit or {}).get("mask", {}).get(str(song.get("internalId")))
                    if not bits or not (bits & (1 << int(sheet.get("chartid") or 0))):
                        continue
            else:
                if pool == "favorite":
                    if key not in favorites:
                        continue
                elif pool not in labels:
                    continue
            out.append({
                "internalId": song.get("internalId"), "chartid": sheet.get("chartid"),
                "title": song.get("title"), "difficulty": sheet.get("difficulty"),
                "level": sheet.get("level"), "labels": sorted(labels),
                "fit": row.get("effectiveLevel") if row else None,
                "ach": (sheet.get("achievement") / 10000.0 if sheet.get("achievement") else None),
                "song": assistant_song_brief(song, sheet),
            })
    return out


def assistant_draw_for_card(user_id: str, card: dict, recent: list[dict]) -> dict | None:
    cands = assistant_candidates(user_id, card)
    return assist.draw_candidate(cands, card, recent)


def assistant_fill_preview(user_id: str, state: dict) -> None:
    cards = state.get("cards") or []
    if not cards:
        state["preview"] = []
        return
    attempts = 0
    while len(state.get("preview") or []) < 3 and attempts < len(cards) * 3 + 3:
        attempts += 1
        idx = int(state.get("nextCard") or 0) % len(cards)
        card = cards[idx]
        pick = assistant_draw_for_card(user_id, card, state.get("recent") or [])
        state["nextCard"] = int(state.get("nextCard") or 0) + 1
        if not pick:
            continue
        state.setdefault("preview", []).append({
            "internalId": pick["internalId"], "chartid": pick["chartid"],
            "cardIndex": idx, "difficulty": pick.get("difficulty"),
        })
        assist.touch_recent(state, pick)
    state["updatedAt"] = assist.now_stamp()


def assistant_payload(user_id: str, with_counts: bool = False) -> dict:
    state = assist.load_state(user_id)
    if not state.get("preview") and state.get("cards"):
        assistant_fill_preview(user_id, state)
        assist.save_state(user_id, state)
    songs = all_songs(user_id).get("songs") or []
    by_song = {s.get("internalId"): s for s in songs}
    state_cards = state.get("cards") or []
    preview = []
    for p in state.get("preview") or []:
        song = by_song.get(p.get("internalId"))
        sheet = next((sh for sh in (song or {}).get("sheets") or []
                      if int(sh.get("chartid") or 0) == int(p.get("chartid") or 0)), None)
        if song and sheet:
            card_index = int(p.get("cardIndex") or 0)
            card = state_cards[card_index] if 0 <= card_index < len(state_cards) else {}
            preview.append({"song": assistant_song_brief(song, sheet),
                            "cardIndex": card_index, "pool": (card or {}).get("pool"),
                            "poolLabel": assistant_card_label(card) if card else ""})
    analysis = {}
    try:
        analysis = json.loads((user_dir(user_id) / "analysis.json").read_text(encoding="utf-8-sig")).get("fitModel") or {}
    except (OSError, ValueError):
        pass
    rating = None
    try:
        dx = json.loads((user_dir(user_id) / "dxrating.json").read_text(encoding="utf-8-sig"))
        rating = dx.get("rating") or dx.get("currentRating") or dx.get("dxRating") or dx.get("total")
    except (OSError, ValueError):
        pass
    cards = []
    for card in state.get("cards") or []:
        item = dict(card)
        if with_counts:
            item["count"] = len(assistant_candidates(user_id, card))
        cards.append(item)
    return {
        "user": user_id,
        "cards": cards,
        "preview": preview,
        "recent": state.get("recent") or [],
        "settings": assist.load_settings(),
        "versions": VERSION_ORDER,
        "profile": {
            "config": [{"tag": k, "value": v} for k, v in (analysis.get("tagUPattern") or {}).items()],
            "eval": [{"tag": k, "value": v} for k, v in (analysis.get("tagUEval") or {}).items()],
            "weakTags": analysis.get("weakTags") or [],
            "spread": (analysis.get("tagBalance") or {}).get("spread"),
            "balance": (analysis.get("tagBalance") or {}).get("factor"),
            "rating": rating,
        },
        "running": bool(ASSIST.get("proc") and ASSIST["proc"].poll() is None),
    }


def assistant_log_tail(limit: int = 500) -> str:
    path = ASSIST.get("logPath")
    if not path:
        return ""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-limit:])


def assistant_stop_callback_listener(port: int) -> None:
    """启动前先尝试停掉可能残留的旧 bridge；没有监听则静默跳过。"""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{int(port)}/stop",
            data=b"{}", method="POST",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=1).read()
    except Exception:
        return
    time.sleep(0.8)


def assistant_start_bridge(user_id: str, server_port: int, mode: str = "push") -> tuple[bool, str]:
    score_only = mode == "score"
    with ASSIST_LOCK:
        if ASSIST.get("proc") and ASSIST["proc"].poll() is None:
            return False, "打歌脚本已经在运行"
        state = assist.load_state(user_id)
        if not score_only:
            if not state.get("cards"):
                return False, "至少配置 1 张卡才能启动"
            if len(state.get("preview") or []) < 3:
                # 正常运行时新第三首由 /refill 按新分析补；这里只处理重启/上次中断留下的短预览
                assistant_fill_preview(user_id, state)
                assist.save_state(user_id, state)
        if not BRIDGE_SCRIPT.is_file():
            return False, "缺桥接脚本"
        settings = assist.load_settings()
        if not str(settings.get("host") or "").strip():
            return False, "请先去打歌助手页填写机台的局域网IP"
        callback_port = int(settings.get("callbackPort") or 20010)
        # 服务重启后 ASSIST 里没有旧进程记录，但旧 bridge 可能还占着回调端口。
        # 先给旧 bridge 发 /stop；没有监听时这个请求会立刻失败，不影响启动。
        assistant_stop_callback_listener(callback_port)
        log_dir = assist.log_dir(user_id)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / (time.strftime("%Y%m%d-%H%M%S") + ".log")
        cmd = [sys.executable, str(BRIDGE_SCRIPT), "--user", user_id,
               "--host", str(settings.get("host") or ""),
               "--mod-port", str(settings.get("modPort") or 8790),
               "--callback-port", str(callback_port),
               "--server", f"http://127.0.0.1:{server_port}"]
        if score_only:
            cmd.append("--score-only")
        try:
            handle = open(log_path, "a", encoding="utf-8", buffering=1)
            proc = subprocess.Popen(cmd, cwd=str(PROJECT_DIR), stdout=handle, stderr=subprocess.STDOUT)
        except OSError as exc:
            return False, f"启动失败：{exc}"
        ASSIST.update({"user": user_id, "proc": proc, "logPath": log_path,
                       "mode": "score" if score_only else "push",
                       "startedAt": assist.now_stamp()})
        assist.prune_logs(user_id)
        time.sleep(0.8)
        if proc.poll() is not None:
            try:
                tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-4:])
            except OSError:
                tail = ""
            ASSIST["proc"] = None
            return False, "启动失败：" + (tail or "bridge 立即退出")
        return True, "已启动"


def assistant_stop_bridge() -> tuple[bool, str]:
    with ASSIST_LOCK:
        proc = ASSIST.get("proc")
        if not proc:
            return False, "没有在运行"
        if proc.poll() is not None:
            return False, "已经结束"
        settings = assist.load_settings()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{int(settings.get('callbackPort') or 20010)}/stop",
                data=b"{}", method="POST",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            # 停止请求可能卡在长请求里；超时后强制结束，避免旧 bridge 占着回调端口
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
    return True, "已停止"


def assistant_rank_of(ach: float) -> int:
    cuts = ((100.5, 13), (100.0, 12), (99.5, 11), (99.0, 10), (98.0, 9),
            (97.0, 8), (94.0, 7), (90.0, 6), (80.0, 5), (75.0, 4),
            (70.0, 3), (60.0, 2), (50.0, 1))
    for lower, rank in cuts:
        if ach >= lower:
            return rank
    return 0


def assistant_merge_score(user_id: str, raw: dict) -> tuple[dict, str]:
    """合并一条回传成绩；返回 (score, error)。"""
    internal_id = int(raw.get("musicId") or raw.get("internalId") or 0)
    chartid = int(raw.get("difficulty") or raw.get("chartid") or 0)
    ach = raw.get("achive", raw.get("achievement"))
    if ach is None:
        return {}, "成绩缺 achievement"
    ach = float(ach)
    if ach <= 1:                 # 有的回传直接给 0.9 这种
        ach *= 100
    best_ach = int(round(ach * 10000))
    scores_path = user_dir(user_id) / "scores.json"
    try:
        doc = json.loads(scores_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}, "读不到成绩库"
    rows = doc.get("scores") or []
    old = next((r for r in rows if int(r.get("internalId") or 0) == internal_id
                and int(r.get("chartid") or 0) == chartid), None)
    if old is None:
        old = {"internalId": internal_id, "chartid": chartid,
               "achievement": 0, "comboStatus": 0, "syncStatus": 0,
               "deluxscoreMax": 0, "playCount": 0, "RANK": 0, "dxscore": None}
        rows.append(old)
    changed = False
    delux_raised = False
    for key in ("achievement", "comboStatus", "syncStatus", "deluxscoreMax"):
        val = raw.get(key)
        if val is None:
            continue
        val = int(val)
        if val > int(old.get(key) or 0):
            old[key] = val
            changed = True
            if key == "deluxscoreMax":
                delux_raised = True
    # achievement 单独按数值比较
    if best_ach > int(old.get("achievement") or 0):
        old["achievement"] = best_ach
        changed = True
    if int(old.get("achievement") or 0) > 0:
        old["RANK"] = assistant_rank_of(int(old["achievement"]) / 10000.0)
    if delux_raised:
        star = assistant_dx_star(user_id, internal_id, chartid, int(old.get("deluxscoreMax") or 0))
        if star is not None:
            old["dxscore"] = star
    old["playCount"] = int(old.get("playCount") or 0) + 1
    rows.sort(key=lambda r: (int(r.get("internalId") or 0), int(r.get("chartid") or 0)))
    doc["scores"] = rows
    tmp = scores_path.with_suffix(".json.part")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(scores_path)
    return {"internalId": internal_id, "chartid": chartid, "changed": changed}, ""


def assistant_dx_star(user_id: str, internal_id: int, chartid: int, delux: int) -> int | None:
    """按谱面满分（noteCounts.total × 3）把 DX 分换算成 0–5 星。"""
    if delux <= 0:
        return 0
    try:
        hit = song_index().get(int(internal_id))
        song = (hit[1] if hit else {}) or {}
        sheet = next((sh for sh in song.get("sheets") or []
                      if int(sh.get("chartid") or 0) == int(chartid)), None)
        total = int(((sheet or {}).get("noteCounts") or {}).get("total") or 0)
    except Exception:
        total = 0
    if total <= 0:
        return None
    pct = 100.0 * int(delux) / (total * 3)
    for lower, star in ((97.0, 5), (95.0, 4), (93.0, 3), (90.0, 2), (85.0, 1)):
        if pct >= lower:
            return star
    return 0


def assistant_edit_score(user_id: str, internal_id: int, chartid: int,
                         achievement: float, combo: int, sync: int,
                         deluxscore: int, play_count: int) -> tuple[dict, str]:
    """绝对覆盖一条成绩（可升可降），用于谱面详情里的手动编辑。"""
    if int(internal_id) <= 0 or int(chartid) < 0:
        return {}, "谱面 id 不对"
    if not (0 <= achievement <= 101):
        return {}, "达成率必须在 0~101 之间"
    if combo < 0 or combo > 4:
        return {}, "连击状态不对"
    if sync < 0 or sync > 5:
        return {}, "双人状态不对"
    if deluxscore < 0:
        return {}, "DX 分不能为负数"
    if play_count < 0:
        return {}, "游玩次数不能为负数"
    scores_path = user_dir(user_id) / "scores.json"
    try:
        doc = json.loads(scores_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}, "读不到成绩库"
    rows = doc.get("scores") or []
    old = next((r for r in rows if int(r.get("internalId") or 0) == int(internal_id)
                and int(r.get("chartid") or 0) == int(chartid)), None)
    if old is None:
        old = {"internalId": int(internal_id), "chartid": int(chartid),
               "achievement": 0, "comboStatus": 0, "syncStatus": 0,
               "deluxscoreMax": 0, "playCount": 1, "RANK": 0, "dxscore": None}
        rows.append(old)
    best_ach = int(round(float(achievement) * 10000))
    old["achievement"] = best_ach
    old["comboStatus"] = int(combo)
    old["syncStatus"] = int(sync)
    old["deluxscoreMax"] = int(deluxscore)
    old["playCount"] = int(play_count)
    star = assistant_dx_star(user_id, internal_id, chartid, int(deluxscore))
    if star is not None:
        old["dxscore"] = star
    old["RANK"] = assistant_rank_of(best_ach / 10000.0)
    rows.sort(key=lambda r: (int(r.get("internalId") or 0), int(r.get("chartid") or 0)))
    doc["scores"] = rows
    tmp = scores_path.with_suffix(".json.part")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(scores_path)
    return {"internalId": int(internal_id), "chartid": int(chartid),
            "achievement": best_ach, "comboStatus": int(combo),
            "syncStatus": int(sync), "deluxscoreMax": int(deluxscore),
            "dxscore": old.get("dxscore"), "RANK": old["RANK"],
            "playCount": old.get("playCount", 0)}, ""


def assistant_advance_if_match(user_id: str, internal_id: int, chartid: int) -> bool:
    state = assist.load_state(user_id)
    preview = state.get("preview") or []
    if not preview:
        return False
    first = preview[0]
    if int(first.get("internalId") or 0) != int(internal_id) or int(first.get("chartid") or 0) != int(chartid):
        return False
    preview = preview[1:]
    state["preview"] = preview
    # 只移除已打的第一首；新第三首等成绩分析完成后再按新池补，先让 bridge 推第二首
    assist.save_state(user_id, state)
    return True


def assistant_bridge_next(user_id: str) -> dict | None:
    state = assist.load_state(user_id)
    if not state.get("preview"):
        return None
    p = state["preview"][0]
    songs = all_songs(user_id).get("songs") or []
    song = next((s for s in songs if int(s.get("internalId") or 0) == int(p.get("internalId") or 0)), None)
    sheet = next((sh for sh in (song or {}).get("sheets") or []
                  if int(sh.get("chartid") or 0) == int(p.get("chartid") or 0)), None)
    if not sheet:
        return None
    cards = state.get("cards") or []
    card_index = int(p.get("cardIndex") or 0)
    card = cards[card_index] if 0 <= card_index < len(cards) else {}
    return {"internalId": p["internalId"], "chartid": p["chartid"],
            "title": (song or {}).get("title"), "difficulty": sheet.get("difficulty"),
            "difficultyNum": sheet.get("chartid"),
            "family": (song or {}).get("family"),
            "type": "dx" if (song or {}).get("family") == "dx" else "std",
            "cardIndex": card_index, "pool": (card or {}).get("pool"),
            "poolLabel": assistant_card_label(card) if card else ""}


def assistant_push_to_machine(internal_id: int, diff: int, chart_type: str = "") -> tuple[bool, str]:
    """单次推送，不重试；dx/std 显式带 type，宴谱不带 type。"""
    settings = assist.load_settings()
    host = str(settings.get("host") or "").strip()
    if not host:
        return False, "请先去打歌助手页填写机台的局域网IP"
    port = int(settings.get("modPort") or 8790)
    form = {"id": str(internal_id), "diff": str(diff)}
    if chart_type in ("dx", "std"):
        form["type"] = chart_type
    body = urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/jump", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return True, resp.read().decode("utf-8", "replace")
    except Exception as exc:                       # noqa: BLE001
        return False, str(exc)


ASSIST_POOL_LABELS = {
    "mastered": "熟练", "pushable_b35": "推b35", "pushable_b15": "推b15",
    "ap": "AP推分", "practice": "弱点练习", "accuracy": "准度练习",
    "challenge": "有挑战", "unplayed": "待开荒", "favorite": "收藏",
    "underdog": "随机下埋",
}


def assistant_log_line(text: str) -> None:
    """把一条助手事件追加到当前脚本日志；脚本没跑就忽略。"""
    path = ASSIST.get("logPath")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}" + chr(10))
    except OSError:
        pass


def assistant_card_label(card: dict) -> str:
    pool = str((card or {}).get("pool") or "?")
    return ASSIST_POOL_LABELS.get(pool, pool)


def assistant_run_related(user_id: str) -> str:
    """成绩更新后的统计/分析/rating；返回错误或空串。"""
    _, err = run_stats(user_id)
    if err:
        return "统计失败：" + err
    _, err = run_analysis(user_id)
    if err:
        return "分析失败：" + err
    _, err = run_dxrating(user_id, "MAGiCAL")
    if err:
        return "rating 失败：" + err
    return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "maimai-planner"

    def log_message(self, fmt, *args):                     # 别把每个请求都吐到控制台
        pass

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path.startswith("/plate/"):
            root = PROJECT_DIR / "data" / "image" / "nameplate"
            return file_response(self, root / path[7:], root)
        if path == "/api/song":
            q = parse_qs(urlparse(self.path).query)
            raw = (q.get("internalId") or [""])[0]
            if not raw.isdigit():
                return self._json({"error": "bad internalId"}, 400)
            user = (q.get("user") or [""])[0]
            if user and not valid_user_id(user):
                return self._json({"error": "bad user"}, 400)
            payload = song_detail(int(raw), user)
            if payload is None:
                return self._json({"error": "没有这首"}, 404)
            return self._json(payload)
        if path == "/api/analysis":
            q = parse_qs(urlparse(self.path).query)
            user_id = (q.get("user") or [""])[0]
            if not valid_user_id(user_id):
                return self._json({"error": "用户 id 不合法"}, 400)
            if (q.get("run") or [""])[0] == "1":
                payload, err = run_analysis(user_id)
                if payload is None:
                    return self._json({"error": err or "分析失败"}, 500)
                return self._json(payload)
            path_doc = user_dir(user_id) / "analysis.json"
            try:
                return self._json(json.loads(path_doc.read_text(encoding="utf-8-sig")))
            except (OSError, ValueError):
                return self._json({"error": "还没有这个用户的分析结果"}, 404)
        if path == "/api/plates":
            return self._json({"plates": plate_masks()})
        if path == "/api/songs":
            q = parse_qs(urlparse(self.path).query)
            return self._json(all_songs((q.get("user") or [""])[0]))
        if path == "/api/songs/sample":
            q = parse_qs(urlparse(self.path).query)
            family = (q.get("family") or ["dx"])[0]
            user_id = (q.get("user") or [""])[0]
            groups_raw = (q.get("groups") or [""])[0]
            if groups_raw:
                spec = []
                for part in groups_raw.split(","):
                    bits = part.split(":")
                    if len(bits) != 3:
                        continue
                    try:
                        spec.append((bits[0], bits[1] == "1", max(1, min(int(bits[2]), 12))))
                    except ValueError:
                        continue
                return self._json({"songs": sample_groups(spec, user_id)})
            try:
                count = int((q.get("count") or ["1"])[0])
            except ValueError:
                count = 1
            return self._json({"songs": sample_songs(max(1, min(count, 12)), user_id, family)})
        if path == "/jacket":
            q = parse_qs(urlparse(self.path).query)
            raw = (q.get("dataid") or [""])[0]
            if not raw.isdigit():
                return self._json({"error": "bad dataid"}, 404)
            return file_response(self, COVER_DIR / ("UI_Jacket_%06d.png" % int(raw)), COVER_DIR)
        if path == "/api/blacklist":
            user = (parse_qs(urlparse(self.path).query).get("user") or [""])[0]
            entries = [{"internalId": iid, "chartid": cid, **entry}
                       for (iid, cid), entry in blacklist_active(user).items()]
            return self._json({"ok": True, "entries": entries})

        if path == "/api/favorites":
            user = (parse_qs(urlparse(self.path).query).get("user") or [""])[0]
            return self._json({"ok": True, "entries": favorites_load(user).get("entries") or []})

        if path == "/api/config":
            chains = {name: {"title": chain.get("title", ""),
                             "steps": [{"script": s["script"], "needs": s.get("needs") or []}
                                       for s in chain.get("steps") or []]}
                      for name, chain in load_chains().items()}
            return self._json({"chains": chains, "chainAges": chain_ages(), "python": sys.executable,
                               "projectDir": str(PROJECT_DIR),
                               "ratingVersions": list(RATING_VERSIONS)})
        if path == "/api/run":
            return self._json(STATE.snapshot())
        if path == "/api/assistant/state":
            query = parse_qs(urlparse(self.path).query)
            user = (query.get("user") or [""])[0]
            with_counts = (query.get("counts") or [""])[0] == "1"
            if not valid_user_id(user):
                return self._json({"error": "bad user"}, 400)
            payload = assistant_payload(user, with_counts=with_counts)
            payload["log"] = assistant_log_tail()
            payload["logs"] = assist.list_logs(user)
            return self._json(payload)
        if path == "/api/assistant/bridge-next":
            user = (parse_qs(urlparse(self.path).query).get("user") or [""])[0]
            if not valid_user_id(user):
                return self._json({"error": "bad user"}, 400)
            return self._json({"next": assistant_bridge_next(user)})
        if path == "/api/assistant/log":
            user = (parse_qs(urlparse(self.path).query).get("user") or [""])[0]
            if not valid_user_id(user):
                return self._json({"error": "bad user"}, 400)
            return self._json({"ok": True, "log": assistant_log_tail(),
                               "logs": assist.list_logs(user),
                               "running": bool(ASSIST.get("proc") and ASSIST["proc"].poll() is None)})
        if path == "/api/users":
            users = load_users()
            for user in users:                              # 卡片要的：头像 / 有没有成绩 / 最近更新 / 三个数
                folder = user_dir(str(user.get("id")))
                user["hasScores"] = (folder / "scores.json").is_file()
                avatar = user.get("avatar")
                user["avatar"] = avatar if avatar and (folder / avatar).is_file() else None
                user["updatedAt"] = None
                user["summary"] = None
                stats_path = folder / "stats.json"
                if stats_path.is_file():
                    try:
                        stats = json.loads(stats_path.read_text(encoding="utf-8-sig"))
                        totals = stats.get("totals") or {}
                        user["updatedAt"] = stats.get("updatedAt")
                        user["summary"] = {"played": totals.get("played"),
                                           "fullCombo": totals.get("fullCombo"),
                                           "allPerfect": totals.get("allPerfect"),
                                           "rating": stats.get("dxRating")}
                    except (OSError, ValueError):
                        pass
                if not user["updatedAt"] and user["hasScores"]:
                    try:
                        doc = json.loads((folder / "scores.json").read_text(encoding="utf-8-sig"))
                        user["updatedAt"] = doc.get("generatedAt")
                    except (OSError, ValueError):
                        pass
            # users.json 的数组顺序就是用户拖动保存的顺序；不能再按 updatedAt 重排
            return self._json({"users": users})
        if path == "/api/avatar-source":
            token = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
            source = PICK_CACHE.get(token)
            if source is None or not source.is_file():
                return self._json({"error": "not found"}, 404)
            return file_response(self, source, source.parent)
        if path.startswith("/ui/"):
            return file_response(self, PROJECT_DIR / "dataUI" / path[4:], PROJECT_DIR / "dataUI")
        if path in ("/fonts/round", "/fonts/bold"):
            name = "圆体.ttf" if path.endswith("round") else "粗体.ttf"
            return file_response(self, PROJECT_DIR / "Fonts" / name, PROJECT_DIR / "Fonts")
        if path == "/api/avatar":
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            user_id = (query.get("user") or [""])[0]
            user = next((u for u in load_users() if u.get("id") == user_id), None)
            name = (user or {}).get("avatar")
            target = user_dir(user_id) / name if name else None
            if not target or not target.is_file():
                return self._json({"error": "这个用户还没有头像"}, 404)
            body = target.read_bytes()
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".webp": "image/webp", ".gif": "image/gif"}.get(target.suffix.lower(),
                                                                     "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path in STATIC_FILES:
            target = WEB_DIR / STATIC_FILES[path]
            if not target.is_file():
                return self._json({"error": f"缺文件 {target}"}, 500)
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", STATIC_TYPES.get(target.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "没有这个路径"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/pick-folder":
            body = self._read_json()
            keep = body.get("mode") == "package"      # 数据小更新：要的是包目录本身
            chosen, note = pick_path()
            warning = None
            if chosen:
                resolved, why = resolve_game_package(chosen, keep_package_dir=keep)
                if resolved is None:
                    warning = why                     # 选得不对：把原因当提示给页面
                else:
                    chosen, note = resolved, why
            return self._json({"path": chosen, "note": note, "warning": warning})

        if path == "/api/score/edit":
            body = self._read_json()
            user = str(body.get("user") or "")
            if not valid_user_id(user):
                return self._json({"ok": False, "error": "用户 id 不合法"}, 400)
            try:
                iid = int(body.get("internalId"))
                cid = int(body.get("chartid"))
                ach = float(body.get("achievement"))
                combo = int(body.get("comboStatus") or 0)
                sync = int(body.get("syncStatus") or 0)
                delux = int(body.get("deluxscoreMax") or 0)
                play_count = int(body.get("playCount") or 0)
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "成绩参数不完整"}, 400)
            score, err = assistant_edit_score(user, iid, cid, ach, combo, sync, delux, play_count)
            if err:
                return self._json({"ok": False, "error": err}, 409)
            related_error = assistant_run_related(user)
            return self._json({"ok": True, "score": score, "relatedError": related_error})

        if path in ("/api/favorite", "/api/favorite/remove"):
            body = self._read_json()
            user = str(body.get("user") or "")
            if not valid_user_id(user):
                return self._json({"ok": False, "error": "用户 id 不合法"}, 400)
            try:
                iid = int(body.get("internalId"))
                cid = int(body.get("chartid"))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "internalId/chartid 不对"}, 400)
            doc = favorites_load(user)
            entries = [e for e in doc.get("entries") or []
                       if not (int(e.get("internalId") or -1) == iid and int(e.get("chartid") or -1) == cid)]
            adding = path == "/api/favorite"
            if adding:
                entries.append({"internalId": iid, "chartid": cid,
                                "addedAt": datetime.now(TZ_CN).strftime("%Y-%m-%d")})
            doc["entries"] = entries
            favorites_save(user, doc)
            return self._json({"ok": True, "favorite": adding})

        if path == "/api/blacklist":
            body = self._read_json()
            user = str(body.get("user") or "")
            if not valid_user_id(user):
                return self._json({"ok": False, "error": "用户 id 不合法"}, 400)
            try:
                iid = int(body.get("internalId"))
                cid = int(body.get("chartid"))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "缺 internalId / chartid"}, 400)
            dur = str(body.get("duration") or "")
            if dur not in BLACKLIST_DURATIONS:
                return self._json({"ok": False, "error": "duration 只能是 1d/3d/7d/30d/forever"}, 400)
            return self._json({"ok": True, "entry": blacklist_upsert(user, iid, cid, dur)})
        if path == "/api/blacklist/remove":
            body = self._read_json()
            user = str(body.get("user") or "")
            if not valid_user_id(user):
                return self._json({"ok": False, "error": "用户 id 不合法"}, 400)
            try:
                iid = int(body.get("internalId"))
                cid = int(body.get("chartid"))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "缺 internalId / chartid"}, 400)
            return self._json({"ok": True, "removed": blacklist_remove(user, iid, cid)})
        if path == "/api/user-rename":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            user, info = rename_user(user_id, str(body.get("name") or ""))
            if user is None:
                return self._json({"ok": False, "info": info}, 409)
            return self._json({"ok": True, "info": info, "user": user})
        if path == "/api/user-order":
            body = self._read_json()
            ok, info = save_user_order(body.get("order") or [])
            return self._json({"ok": ok, "info": info}, 200 if ok else 409)
        if path == "/api/user-create":
            body = self._read_json()
            user, info = create_user(str(body.get("name") or ""))
            if user is None:
                return self._json({"created": False, "info": info}, 409)
            return self._json({"created": True, "info": info, "user": user})
        if path == "/api/user-delete":
            body = self._read_json()
            ok, info = delete_user(str(body.get("user") or ""))
            return self._json({"ok": ok, "info": info}, 200 if ok else 409)
        if path == "/api/user-avatar":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            if not user_id:
                return self._json({"ok": False, "info": "先选一个本地用户"}, 409)
            chosen, note = pick_path(file_mode=True, kind="avatar")
            if not chosen:
                return self._json({"ok": False, "info": note or "已取消"}, 409)
            image, info = pick_avatar_image(chosen)
            if image is None:
                return self._json({"ok": False, "info": info}, 409)
            return self._json({"ok": True, "token": image, "name": info, "side": AVATAR_SIDE})
        if path == "/api/user-avatar-save":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            saved, info = save_avatar_png(user_id, str(body.get("image") or ""))
            if saved is None:
                return self._json({"ok": False, "info": info}, 409)
            return self._json({"ok": True, "info": info, "avatar": saved})
        if path == "/api/scores/dxrating":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            version = canonical_version(str(body.get("version") or ""))
            if not user_id or version not in RATING_VERSIONS:
                return self._json({"ok": False, "info": "要给用户和一个 DX 时代的版本"}, 409)
            value, why = run_dxrating(user_id, version)
            if value is None:
                return self._json({"ok": False, "info": why}, 409)
            return self._json({"ok": True, "dxRating": value.get("dxRating"),
                               "version": value.get("version"), "counts": value.get("counts")})
        if path == "/api/scores/stats":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            if not user_id:
                return self._json({"ok": False, "info": "先选一个本地用户"}, 409)
            stats, why = run_stats(user_id)
            if stats is None:
                return self._json({"ok": False, "info": why}, 409)
            return self._json({"ok": True, "stats": stats})
        if path == "/api/pick-file":
            mode = ""
            try:
                mode = str((self._read_json() or {}).get("mode") or "")
            except Exception:                              # noqa: BLE001
                mode = ""
            chosen, note = pick_path(file_mode=True, kind=("score" if mode == "score" else "als"))
            if chosen:
                warning = (check_scores if mode == "score" else check_als)(chosen)
            else:
                warning = None
            return self._json({"path": chosen, "note": note, "warning": warning})
        if path == "/api/assistant/settings":
            body = self._read_json()
            return self._json({"ok": True, "settings": assist.save_settings(body)})
        if path == "/api/assistant/cards":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            if not valid_user_id(user_id):
                return self._json({"ok": False, "error": "bad user"}, 400)
            state = assist.load_state(user_id)
            cards = body.get("cards")
            if not isinstance(cards, list) or len(cards) > 12:
                return self._json({"ok": False, "error": "cards 必须是 <=12 的数组"}, 400)
            state["cards"] = cards
            state["preview"] = []
            state["nextCard"] = 0
            assistant_fill_preview(user_id, state)
            assist.save_state(user_id, state)
            return self._json({"ok": True, **assistant_payload(user_id, with_counts=True)})
        if path in ("/api/assistant/redraw", "/api/assistant/skip"):
            body = self._read_json()
            user_id = str(body.get("user") or "")
            if not valid_user_id(user_id):
                return self._json({"ok": False, "error": "bad user"}, 400)
            try:
                index = int(body.get("index"))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "index 不对"}, 400)
            force = bool(body.get("force"))
            state = assist.load_state(user_id)
            preview = state.get("preview") or []
            if index < 0 or index >= len(preview):
                return self._json({"ok": False, "error": "预览位置不存在"}, 400)
            entry = preview[index]
            cards = state.get("cards") or []
            card = cards[int(entry.get("cardIndex") or 0) % len(cards)] if cards else {}
            label = assistant_card_label(card)
            empty = False
            if path.endswith("/skip"):
                empty = not assistant_candidates(user_id, card)
                if empty:
                    assistant_log_line(f"已跳过空卡池：{label}")
                preview.pop(index)
            else:
                pick = assistant_draw_for_card(user_id, card, state.get("recent") or [])
                if pick:
                    entry.update({"internalId": pick["internalId"], "chartid": pick["chartid"],
                                  "difficulty": pick.get("difficulty")})
                    assist.touch_recent(state, pick)
                elif not force:
                    # 先只把“空”告诉前端；玩家点确认后再带 force 来移除并顺延
                    return self._json({"ok": True, "empty": True,
                                       **assistant_payload(user_id, with_counts=True)})
                else:
                    # 玩家已确认顺延：移除当前位置，后面预览前移
                    empty = True
                    assistant_log_line(f"重抽时卡池已空：{label}")
                    preview.pop(index)
            state["preview"] = preview
            assistant_fill_preview(user_id, state)
            assist.save_state(user_id, state)
            return self._json({"ok": True, "empty": empty,
                               **assistant_payload(user_id, with_counts=True)})
        if path == "/api/assistant/start":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            if not valid_user_id(user_id):
                return self._json({"ok": False, "error": "bad user"}, 400)
            mode = str(body.get("mode") or "push")
            if mode not in ("push", "score"):
                return self._json({"ok": False, "error": "mode 只能是 push 或 score"}, 400)
            ok, info = assistant_start_bridge(user_id, self.server.server_port, mode)
            return self._json({"ok": ok, "info": info}, 200 if ok else 409)
        if path == "/api/assistant/stop":
            ok, info = assistant_stop_bridge()
            return self._json({"ok": ok, "info": info})
        if path == "/api/assistant/repush":
            settings = assist.load_settings()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{int(settings.get('callbackPort') or 20010)}/repush",
                    data=b"{}", method="POST", headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=3).read()
                return self._json({"ok": True})
            except Exception as exc:                       # noqa: BLE001
                return self._json({"ok": False, "error": str(exc)}, 409)
        if path == "/api/assistant/push":
            body = self._read_json()
            try:
                iid = int(body.get("internalId"))
                diff = int(body.get("chartid"))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "internalId/chartid 不对"}, 400)
            chart_type = str(body.get("type") or "")
            ok, info = assistant_push_to_machine(iid, diff, chart_type)
            if info == "请先去打歌助手页填写机台的局域网IP":
                return self._json({"ok": False, "error": info}, 409)
            return self._json({"ok": ok, "info": info})
        if path == "/api/assistant/bridge-score":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            if not valid_user_id(user_id):
                return self._json({"error": "bad user"}, 400)
            score, err = assistant_merge_score(user_id, body.get("score") or {})
            if err:
                return self._json({"error": err}, 409)
            matched = assistant_advance_if_match(user_id, score.get("internalId"),
                                                  score.get("chartid"))
            related_error = ""
            if body.get("withStats") and score.get("changed"):
                related_error = assistant_run_related(user_id)
            return self._json({
                "ok": True,
                "matched": matched,
                "changed": bool(score.get("changed")),
                "relatedError": related_error,
                # 先让 bridge 推下一首；分析/补第三首由 bridge 随后调用 /refill 完成
                "refillNeeded": bool(score.get("changed") or matched),
                "next": assistant_bridge_next(user_id),
            })
        if path == "/api/assistant/refill":
            body = self._read_json()
            user_id = str(body.get("user") or "")
            if not valid_user_id(user_id):
                return self._json({"error": "bad user"}, 400)
            related_error = assistant_run_related(user_id)
            state = assist.load_state(user_id)
            assistant_fill_preview(user_id, state)
            assist.save_state(user_id, state)
            payload = assistant_payload(user_id, with_counts=True)
            return self._json({"ok": True, "relatedError": related_error, **payload})
        if path == "/api/build":
            body = self._read_json()
            chain_name = str(body.get("chain") or "")
            chosen = body.get("gamePackage") or None
            if chosen:
                resolved, why = resolve_game_package(
                    chosen, keep_package_dir=chain_wants_package_dir(chain_name))
                if resolved is None:
                    return self._json({"started": False, "info": f"游戏包目录不对：{why}"}, 409)
                chosen = resolved
            ok, info = start_build(chain_name, chosen, body.get("als") or None,
                                   body.get("scores") or None, body.get("user") or None)
            return self._json({"started": ok, "info": info}, 200 if ok else 409)
        if path == "/api/stop":
            with STATE.lock:
                proc = STATE.proc
            if proc and proc.poll() is None:
                proc.kill()
                STATE.append_log("—— 用户中止 ——")
                return self._json({"stopped": True})
            return self._json({"stopped": False, "info": "没有在跑的任务"})
        self._json({"error": "没有这个路径"}, 404)


def local_urls(port: int) -> list[str]:
    import socket
    urls = [f"http://127.0.0.1:{port}/"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                urls.append(f"http://{ip}:{port}/")
    except Exception:
        pass
    return sorted(set(urls))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="server.py", description="打歌计划器 · 本机服务")
    ap.add_argument("--host", default="0.0.0.0", help="绑定地址（默认 0.0.0.0，局域网可访问）")
    ap.add_argument("--port", type=int, default=20001,
                    help="端口（默认 20001；别用 1024–15000，那段是本机动态端口段，会被系统随机占走）")
    ap.add_argument("--chains-file", metavar="PATH",
                    help="换成别的步骤表（默认 tools/run_chain/chains.json；自检用来跑临时链）")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    global CHAINS_FILE
    if args.chains_file:
        CHAINS_FILE = Path(args.chains_file).resolve()

    if not CHAIN_SCRIPT.is_file() or not CHAINS_FILE.is_file():
        print(f"[错误] 缺少编排脚本或步骤表：{CHAIN_SCRIPT} / {CHAINS_FILE}", file=sys.stderr)
        return 2
    if not (WEB_DIR / "index.html").is_file():
        print(f"[错误] 缺少页面：{WEB_DIR / 'index.html'}", file=sys.stderr)
        return 2

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"[错误] 绑不上 {args.host}:{args.port}：{exc}\n"
              f"       端口被别的程序占着、或落进了系统的动态端口段（本机是 1024–15000，"
              f"浏览器/代理的外连会随机占走一段，撞上就拒绝绑定）。\n"
              f"       换一个端口再来：--port 20001", file=sys.stderr)
        return 2
    print("─" * 62)
    print("打歌计划器 · 本机服务")
    for url in local_urls(args.port):
        print(f"  打开：{url}")
    print(f"  项目：{PROJECT_DIR}")
    print(f"  解释器：{sys.executable}")
    print("  Ctrl+C 停止")
    print("─" * 62)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
