#!/usr/bin/env python3
# 用途：打歌助手桥接脚本（结算事件版）：推歌 → 等 ResultProcess 结算 → 轮询当前曲 playCount → 等 1 秒 → 同步存档 → 等可点歌。
# 状态：MaiPlannerWeb 重构中；不接收 mod 的结算回传。
"""独立桥接脚本；日志直接 print，由 server 重定向到 assistant-logs/*.log。"""

from __future__ import annotations

import argparse
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIFF_NAMES = ("BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER")
RETRYABLE = ("请稍后再点", "正在切换画面", "被取代", "superseded", "进场", "退场")
STOP_EVENT = threading.Event()
REPUSH_EVENT = threading.Event()
SKIP_EVENT = threading.Event()


def log(text: str) -> None:
    print(text, flush=True)


def log_status(text: str) -> None:
    print("\r" + text, end="", flush=True)


def log_status_end() -> None:
    print("", flush=True)


def push_pool_tag(current: dict) -> str:
    label = str((current or {}).get("poolLabel") or (current or {}).get("pool") or "").strip()
    return f"【{label}】" if label else ""


def push_type_label(current: dict) -> str:
    return "DX" if (current or {}).get("type") == "dx" else "STD"


def post_json(url: str, payload: dict, timeout: float = 30.0):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


def get_json(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def post_form(url: str, form: dict, timeout: float = 8.0):
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


def send_jump(host: str, port: int, music_id: int, diff: int, chart_type: str = "") -> bool:
    base = f"http://{host}:{int(port)}"
    last = ""
    showed_status = False
    for attempt in range(1, 6):
        if STOP_EVENT.is_set():
            return False
        try:
            form = {"id": str(music_id), "diff": str(diff)}
            if chart_type in ("dx", "std"):
                form["type"] = chart_type
            try:
                status, body = post_form(base + "/jump", form)
            except urllib.error.HTTPError as exc:
                status, body = exc.code, exc.read().decode("utf-8", "replace")
            try:
                obj = json.loads(body)
            except Exception:
                obj = {}
            ok = status == 200 and bool(obj.get("ok", status == 200))
            msg = str(obj.get("msg") or obj.get("error") or "")
            if ok and not any(key in msg for key in RETRYABLE):
                if showed_status:
                    log_status_end()
                return True
            if status != 200 and 400 <= status < 500 and status != 429:
                last = f"HTTP {status} {msg}"
            elif not ok and not msg:
                last = "ok=false"
            else:
                last = msg or f"HTTP {status}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        if attempt < 5:
            log_status(f"推送失败：{last} 重试{attempt}次")
            showed_status = True
            STOP_EVENT.wait(1.0)
    if showed_status:
        log_status_end()
    log(f"推送失败：{last} 重试5次，放弃")
    return False


def get_status(host: str, port: int) -> dict:
    doc = get_json(f"http://{host}:{int(port)}/status", timeout=5)
    if not isinstance(doc, dict) or not doc.get("ok"):
        raise RuntimeError("mod /status 返回失败")
    return doc


def get_score(host: str, port: int, internal_id: int, chart_id: int) -> dict:
    url = (f"http://{host}:{int(port)}/score?internalId={int(internal_id)}"
           f"&chartId={int(chart_id)}")
    doc = get_json(url, timeout=5)
    if not isinstance(doc, dict) or not doc.get("ok"):
        raise RuntimeError("mod /score 返回失败")
    return doc


def result_serial(status: dict) -> int:
    if "resultSerial" not in status:
        raise RuntimeError("当前 mod 不支持 resultSerial，请升级到 0.1.8")
    return int(status.get("resultSerial") or 0)


def fetch_save(host: str, port: int) -> dict:
    doc = get_json(f"http://{host}:{int(port)}/save", timeout=15)
    if not isinstance(doc, dict) or not doc.get("ok"):
        raise RuntimeError("mod /save 返回失败：" + str((doc or {}).get("error") or "ok=false"))
    return doc


def _chart_id(row: dict) -> int:
    value = row.get("chartId") if row.get("chartId") is not None else row.get("chartid")
    return int(value or 0)


def find_score(save: dict, internal_id: int, chart_id: int) -> dict | None:
    for row in (save or {}).get("scores") or []:
        try:
            if int(row.get("internalId") or 0) == int(internal_id) \
                    and _chart_id(row) == int(chart_id):
                return row
        except (TypeError, ValueError):
            continue
    return None


def score_map(save: dict) -> dict:
    """整份存档的轻量快照；用于判断任意成绩是否变化。"""
    out = {}
    for row in (save or {}).get("scores") or []:
        try:
            key = (int(row.get("internalId") or 0), _chart_id(row))
            out[key] = score_signature(row)
        except (TypeError, ValueError):
            continue
    return out


def save_changed(before: dict | None, after: dict | None) -> bool:
    return before is not None and after is not None and before != after


def score_signature(row: dict | None) -> tuple | None:
    if not row:
        return None
    try:
        return (
            int(row.get("playCount") or 0),
            int(round(float(row.get("achievement") or 0) * 10000)),
            int(row.get("combo") or 0),
            int(row.get("sync") or 0),
            int(row.get("deluxScore") or 0),
        )
    except (TypeError, ValueError):
        return None


def score_played(before: dict | None, after: dict | None) -> bool:
    """playCount 增加也算完成；before 为空、after 存在也算完成。"""
    if after is None or not after.get("found", True):
        return False
    if before is None:
        return True
    old = score_signature(before)
    new = score_signature(after)
    return old is not None and new is not None and old != new


def login_name(status: dict) -> str:
    if not status.get("isEntry"):
        return ""
    name = str(status.get("userName") or "").strip()
    return "" if name == "游客" else name


def wait_for_select(host: str, port: int, expected_name: str = "") -> bool:
    """等到可点歌且已登录；换人立即停，游客不推歌。"""
    attempts = 0
    showed_status = False
    while not STOP_EVENT.is_set():
        if SKIP_EVENT.is_set():
            SKIP_EVENT.clear()
            if showed_status:
                log_status_end()
            log("已跳过当前歌曲")
            return False
        attempts += 1
        try:
            doc = get_json(f"http://{host}:{int(port)}/wait-select?timeout=3000", timeout=8)
            if isinstance(doc, dict) and doc.get("ready"):
                status = get_status(host, port)
                name = login_name(status)
                if not name:
                    log_status(f"等待玩家登录…（{attempts}次）")
                    showed_status = True
                    STOP_EVENT.wait(1.0)
                    continue
                if expected_name and name != expected_name:
                    if showed_status:
                        log_status_end()
                    log(f"检测到换人：「{expected_name}」→「{name}」，停止当前推歌脚本")
                    STOP_EVENT.set()
                    return False
                if showed_status:
                    log_status_end()
                log("已检测到可点歌界面")
                STOP_EVENT.wait(2.0)
                return not STOP_EVENT.is_set()
        except Exception:
            pass
        log_status(f"等待可点歌界面…（{attempts}次）")
        showed_status = True
    if showed_status:
        log_status_end()
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "assistant-bridge/4"

    def log_message(self, fmt, *args):
        pass

    def _reply(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if path == "/stop":
            STOP_EVENT.set()
            return self._reply(200, {"ok": True})
        if path == "/repush":
            REPUSH_EVENT.set()
            return self._reply(200, {"ok": True})
        if path == "/skip":
            SKIP_EVENT.set()
            return self._reply(200, {"ok": True})
        return self._reply(404, {"ok": False, "error": "unknown path"})

    def do_GET(self):
        return self._reply(200, {"ok": True, "hint": "POST /stop, /repush or /skip"})


def sync_current(server: str, user: str, internal_id: int, chart_id: int) -> dict:
    payload = {"user": user, "internalId": internal_id, "chartid": chart_id}
    status, body = post_json(server.rstrip("/") + "/api/assistant/sync-save", payload, timeout=300)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:200]}")
    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "sync-save failed"))
    return result


def sync_other(server: str, user: str) -> dict:
    status, body = post_json(server.rstrip("/") + "/api/save/sync",
                             {"user": user}, timeout=300)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:200]}")
    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "sync failed"))
    return result


def sync_only(server: str, user: str) -> dict:
    """整份同步，但不推进当前目标。"""
    status, body = post_json(server.rstrip("/") + "/api/assistant/sync-save",
                             {"user": user}, timeout=300)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:200]}")
    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "sync failed"))
    return result


def advance_current(server: str, user: str, internal_id: int, chart_id: int) -> dict:
    status, body = post_json(server.rstrip("/") + "/api/assistant/advance",
                             {"user": user, "internalId": internal_id, "chartid": chart_id},
                             timeout=30)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:200]}")
    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "advance failed"))
    return result


def next_song(server: str, user: str) -> dict | None:
    url = server.rstrip("/") + "/api/assistant/bridge-next?user=" + urllib.parse.quote(user)
    payload = get_json(url)
    return payload.get("next") if isinstance(payload, dict) and "next" in payload else payload


def wait_for_save_change(host: str, port: int, before: dict | None) -> tuple[dict | None, dict | None]:
    """ResultProcess 后每秒拉一次完整存档，等任意成绩变化；返回 (save, snapshot)。"""
    for attempt in range(1, 11):
        if STOP_EVENT.is_set():
            return None, None
        try:
            save = fetch_save(host, port)
            snapshot = score_map(save)
            if before is None or save_changed(before, snapshot):
                log(f"检测到存档成绩变化（第 {attempt} 次轮询）")
                return save, snapshot
        except Exception as exc:  # noqa: BLE001
            log(f"轮询整份存档失败：{exc}")
        STOP_EVENT.wait(1.0)
    return None, None


def login_changed(status: dict, expected_name: str) -> tuple[bool, str]:
    """返回 (是否换人, 当前用户名)。退出登录不算换人。"""
    name = str(status.get("userName") or "") if status.get("isEntry") else ""
    if not name:
        return False, expected_name
    if expected_name and name != expected_name:
        return True, name
    return False, name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--host", default="")
    ap.add_argument("--mod-port", type=int, default=8790)
    ap.add_argument("--callback-port", type=int, default=20010,
                    help="控制端口：只用于 /stop、/repush、/skip")
    ap.add_argument("--server", default="http://127.0.0.1:20001")
    ap.add_argument("--miss-mode", default="wait",
                    choices=("wait", "repush", "skip"),
                    help="当前曲未完成时的策略：wait/repush/skip")
    args = ap.parse_args()

    if not args.host:
        log("缺少机台 IP")
        return 2

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", args.callback_port), Handler)
    except OSError as exc:
        log(f"控制服务器启动失败：{exc}")
        return 2
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"控制服务器已启动：0.0.0.0:{args.callback_port}")

    current = None
    pushed = False
    baseline = None
    baseline_known = False
    last_result = None
    last_snapshot = None
    expected_name = ""
    status_error_logged = False

    try:
        while not STOP_EVENT.is_set():
            if current is None:
                try:
                    current = next_song(args.server, args.user)
                except Exception as exc:  # noqa: BLE001
                    log(f"取下一首失败：{exc}")
                    return 2
                if not current:
                    log("没有可推送的歌，脚本结束")
                    break
                if not isinstance(current, dict) or not current.get("internalId"):
                    log(f"取下一首失败：返回格式不对 {current!r}")
                    return 2
                pushed = False
                baseline = None
                baseline_known = False
                try:
                    status = get_status(args.host, args.mod_port)
                    last_result = result_serial(status)
                    name = str(status.get("userName") or "") if status.get("isEntry") else ""
                    if name:
                        if expected_name and name != expected_name:
                            log(f"检测到换人：「{expected_name}」→「{name}」，停止当前推歌脚本")
                            return 0
                        expected_name = name
                    save = fetch_save(args.host, args.mod_port)
                    baseline = find_score(save, int(current["internalId"]),
                                          int(current.get("chartid") or 0))
                    last_snapshot = score_map(save)
                    baseline_known = True
                    status_error_logged = False
                except Exception as exc:  # noqa: BLE001
                    log(f"推送前读取状态/存档失败（会继续推送，稍后重试）：{exc}")

            name = current.get("title") or ""
            diff = int(current.get("chartid") or 0)
            diff_name = DIFF_NAMES[diff] if 0 <= diff <= 4 else str(diff)

            if not pushed:
                REPUSH_EVENT.clear()
                SKIP_EVENT.clear()
                if not wait_for_select(args.host, args.mod_port, expected_name):
                    if STOP_EVENT.is_set():
                        break
                    current = None
                    pushed = False
                    baseline = None
                    baseline_known = False
                    break
                try:
                    check_status = get_status(args.host, args.mod_port)
                    changed, expected_name = login_changed(check_status, expected_name)
                    if changed:
                        log(f"检测到换人：「{expected_name}」，停止当前推歌脚本")
                        return 0
                except Exception:
                    pass
                if not send_jump(args.host, args.mod_port, int(current["internalId"]), diff,
                                 current.get("type") or ""):
                    break
                log(f"已推送{push_pool_tag(current)}“{current['internalId']}”“{name}”“{diff_name}”({push_type_label(current)})")
                pushed = True

            while not STOP_EVENT.is_set():
                if SKIP_EVENT.is_set():
                    SKIP_EVENT.clear()
                    log("已跳过当前歌曲")
                    current = None
                    pushed = False
                    baseline = None
                    baseline_known = False
                    break

                if REPUSH_EVENT.is_set():
                    REPUSH_EVENT.clear()
                    if send_jump(args.host, args.mod_port, int(current["internalId"]), diff,
                                 current.get("type") or ""):
                        log(f"已重新推送{push_pool_tag(current)}“{current['internalId']}”“{name}”“{diff_name}”")
                    else:
                        log("重新推送失败")

                try:
                    status = get_status(args.host, args.mod_port)
                    status_error_logged = False
                except Exception as exc:  # noqa: BLE001
                    if not status_error_logged:
                        log(f"读取状态失败（继续等待）：{exc}")
                        status_error_logged = True
                    STOP_EVENT.wait(1.0)
                    continue

                changed, expected_name = login_changed(status, expected_name)
                if changed:
                    log(f"检测到换人：「{expected_name}」，停止当前推歌脚本")
                    return 0

                try:
                    serial = result_serial(status)
                except Exception as exc:  # noqa: BLE001
                    log(str(exc))
                    return 2

                if last_result is None:
                    last_result = serial
                    STOP_EVENT.wait(1.0)
                    continue
                if serial == last_result:
                    STOP_EVENT.wait(1.0)
                    continue

                last_result = serial
                log("检测到进入结算界面，等待存档成绩变化")
                save, new_snapshot = wait_for_save_change(
                    args.host, args.mod_port, last_snapshot)
                if save is None:
                    log("结算后 10 秒内没有检测到存档变化")
                    STOP_EVENT.wait(1.0)
                    continue

                last_snapshot = new_snapshot
                after = find_score(save, int(current["internalId"]), diff)
                current_done = baseline_known and score_played(baseline, after)
                STOP_EVENT.wait(1.0)

                if current_done:
                    try:
                        result = sync_current(args.server, args.user,
                                              int(current["internalId"]), diff)
                    except Exception as exc:  # noqa: BLE001
                        log(f"同步整份存档失败，稍后重试：{exc}")
                        last_result = serial - 1
                        STOP_EVENT.wait(1.0)
                        continue
                    log(f"存档已同步，本次 {result.get('count', '?')} 条成绩"
                        + (f"；分析：{result.get('relatedError')}" if result.get("relatedError") else ""))
                    current = None
                    pushed = False
                    baseline = None
                    baseline_known = False
                    last_snapshot = None
                    break

                try:
                    result = sync_only(args.server, args.user)
                except Exception as exc:  # noqa: BLE001
                    log(f"同步其他成绩失败，稍后重试：{exc}")
                    last_result = serial - 1
                    STOP_EVENT.wait(1.0)
                    continue

                log(f"本局不是当前目标，已同步其他成绩（本次 {result.get('count', '?')} 条）")
                if args.miss_mode == "wait":
                    log("当前目标未完成，将一直等待")
                    STOP_EVENT.wait(1.0)
                    continue
                if args.miss_mode == "repush":
                    log("当前目标未完成，将重推当前")
                    pushed = False
                    break
                # skip
                try:
                    advance_current(args.server, args.user,
                                    int(current["internalId"]), diff)
                except Exception as exc:  # noqa: BLE001
                    log(f"跳过当前目标失败：{exc}")
                    STOP_EVENT.wait(1.0)
                    continue
                log("当前目标未完成，将跳过当前")
                current = None
                pushed = False
                baseline = None
                baseline_known = False
                last_snapshot = None
                break
    finally:
        srv.shutdown()
    log("打歌脚本结束。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
