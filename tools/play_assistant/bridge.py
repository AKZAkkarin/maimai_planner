#!/usr/bin/env python3
# 用途：打歌助手桥接脚本：推歌 → 收成绩 → 交给 server 入库/分析 → 下一首
# 状态：现行（v1 骨架，接口按点歌 mod 的 /jump、/api/score）
"""独立桥接脚本；日志直接 print，由 server 重定向到 assistant-logs/*.log。"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIFF_NAMES = ("BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER")
RETRYABLE = ("请稍后再点", "正在切换画面", "被取代", "superseded", "进场", "退场")
SCORE_EVENT = threading.Event()
STOP_EVENT = threading.Event()
REPUSH_EVENT = threading.Event()
SCORES = []
SCORE_LOCK = threading.Lock()


def log(text: str) -> None:
    print(text, flush=True)


def push_pool_tag(current: dict) -> str:
    label = str((current or {}).get("poolLabel") or (current or {}).get("pool") or "").strip()
    return f"【{label}】" if label else ""


def push_type_label(current: dict) -> str:
    return "DX" if (current or {}).get("type") == "dx" else "STD"


def post_json(url: str, payload: dict, timeout: float = 30.0):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def post_form(url: str, form: dict, timeout: float = 8.0):
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def send_jump(host: str, port: int, music_id: int, diff: int, chart_type: str = "") -> bool:
    base = f"http://{host}:{int(port)}"
    last = ""
    for attempt in range(1, 6):
        try:
            try:
                form = {"id": str(music_id), "diff": str(diff)}
                if chart_type in ("dx", "std"):
                    form["type"] = chart_type
                st, body = post_form(base + "/jump", form)
            except urllib.error.HTTPError as exc:
                st, body = exc.code, exc.read().decode("utf-8", "replace")
            try:
                obj = json.loads(body)
            except Exception:
                obj = {}
            ok = st == 200 and bool(obj.get("ok", st == 200))
            msg = str(obj.get("msg") or obj.get("error") or "")
            if ok and not any(k in msg for k in RETRYABLE):
                return True
            if st != 200 and 400 <= st < 500 and st != 429:
                last = f"HTTP {st} {msg}"
            elif not ok and not msg:
                last = "ok=false"
            else:
                last = msg or f"HTTP {st}"
        except Exception as exc:                       # noqa: BLE001
            last = str(exc)
        if attempt < 5:
            log(f"推送失败：{last}，1秒后重试")
            time.sleep(1.0)
    log(f"推送失败：{last}，已达 5 次")
    return False


def normalize_score(raw: dict) -> dict:
    """把 mod 的结算回传转成成绩库的 comboStatus/syncStatus/deluxscoreMax 口径。"""
    score = dict(raw or {})
    judge = score.get("judge") or {}
    try:
        miss = int(judge.get("miss") or 0)
        good = int(judge.get("good") or 0)
        great = int(judge.get("great") or 0)
    except (TypeError, ValueError):
        miss = good = great = 0
    if score.get("comboStatus") is None:
        ach = score.get("achive", score.get("achievement"))
        try:
            ach = float(ach)
        except (TypeError, ValueError):
            ach = 0
        if ach >= 101.0:
            score["comboStatus"] = 4
        elif miss == 0 and good == 0 and great == 0:
            score["comboStatus"] = 3
        elif miss == 0 and good == 0:
            score["comboStatus"] = 2
        elif miss == 0:
            score["comboStatus"] = 1
        else:
            score["comboStatus"] = 0
    if score.get("syncStatus") is None:
        score["syncStatus"] = 0
    if score.get("deluxscoreMax") is None:
        try:
            cp = int(judge.get("criticalPerfect") or 0)
            perfect = int(judge.get("perfect") or 0)
            score["deluxscoreMax"] = cp * 3 + perfect * 2 + great
        except (TypeError, ValueError):
            score["deluxscoreMax"] = 0
    return score


class Handler(BaseHTTPRequestHandler):
    server_version = "assistant-bridge/1"

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
        raw = self.rfile.read(n) if n else b""
        if path == "/stop":
            STOP_EVENT.set()
            return self._reply(200, {"ok": True})
        if path == "/repush":
            REPUSH_EVENT.set()
            return self._reply(200, {"ok": True})
        if path not in ("/api/score", "/score", "/"):
            return self._reply(404, {"ok": False, "error": "unknown path"})
        try:
            score = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return self._reply(400, {"ok": False, "error": "bad json"})
        with SCORE_LOCK:
            SCORES.append(score)
        SCORE_EVENT.set()
        return self._reply(200, {"ok": True})

    def do_GET(self):
        return self._reply(200, {"ok": True, "hint": "POST score to /api/score"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--host", default="")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--mod-port", type=int, default=8790)
    ap.add_argument("--callback-port", type=int, default=20010)
    ap.add_argument("--server", default="http://127.0.0.1:20001")
    args = ap.parse_args()

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", args.callback_port), Handler)
    except OSError as exc:
        log(f"回调服务器启动失败：{exc}")
        return 2
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"回调服务器已启动：0.0.0.0:{args.callback_port}")

    if args.score_only:
        # 只接收成绩时不会发 /jump，mod 的 LastCallerIp 是空的；
        # 必须主动把本机回调地址告诉 mod，否则它不会回传成绩。
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect((args.host, args.mod_port))
            local_ip = probe.getsockname()[0]
            probe.close()
            callback_url = f"http://{local_ip}:{args.callback_port}/api/score"
            st, body = post_form(f"http://{args.host}:{args.mod_port}/callback",
                                 {"url": callback_url}, timeout=5)
            log(f"已通知机台回传地址：{callback_url}（HTTP {st}）")
        except Exception as exc:                       # noqa: BLE001
            log(f"通知机台回传地址失败：{exc}（可在 mod 配置里手动填回传地址）")
        log("仅接收成绩模式：不推歌、不跳转，只处理机台回传")
        while not STOP_EVENT.is_set():
            if not SCORE_EVENT.wait(0.5):
                continue
            SCORE_EVENT.clear()
            with SCORE_LOCK:
                batch = SCORES[:]
                SCORES.clear()
            for raw in batch:
                score = normalize_score(raw)
                score_id = score.get("musicId") or score.get("internalId") or "?"
                score_diff = score.get("difficulty", score.get("chartid"))
                try:
                    score_diff = int(score_diff)
                    score_diff_name = DIFF_NAMES[score_diff] if 0 <= score_diff <= 4 else str(score_diff)
                except (TypeError, ValueError):
                    score_diff_name = str(score_diff)
                score_name = score.get("name") or ""
                ach = score.get("achive", score.get("achievement"))
                try:
                    ach_text = f"{float(ach):.4f}"
                except Exception:
                    ach_text = str(ach)
                log(f"收到回传成绩：“{score_id}”“{score_name}”“{score_diff_name}”，成绩：“{ach_text}”")
                try:
                    st, body = post_json(args.server.rstrip("/") + "/api/assistant/bridge-score",
                                         {"user": args.user, "score": score, "withStats": True},
                                         timeout=300)
                    result = json.loads(body)
                    if result.get("error"):
                        log(f"并入成绩失败：{result['error']}")
                    elif result.get("changed"):
                        log("存档更新成功。")
                        if result.get("relatedError"):
                            log(f"统计更新失败：{result['relatedError']}")
                    else:
                        log("成绩未超过旧记录或无需更新。")
                except Exception as exc:                   # noqa: BLE001
                    log(f"并入成绩失败：{exc}")
            STOP_EVENT.wait(0.2)
        srv.shutdown()
        log("仅接收成绩模式结束。")
        return 0

    def refill_after_score():
        """成绩已回传、下一首已推送后，再跑统计并按新池补第三首。"""
        url = args.server.rstrip("/") + "/api/assistant/refill"
        try:
            st, body = post_json(url, {"user": args.user}, timeout=300)
            result = json.loads(body)
            if result.get("error"):
                log(f"补统计失败：{result['error']}")
            elif result.get("relatedError"):
                log(f"补统计失败：{result['relatedError']}")
            else:
                log("统计信息更新成功。")
        except Exception as exc:                       # noqa: BLE001
            log(f"补统计失败：{exc}")

    current = None
    pushed = False
    need_refill = False
    while not STOP_EVENT.is_set():
        if current is None:
            next_url = args.server.rstrip("/") + "/api/assistant/bridge-next?user=" + urllib.parse.quote(args.user)
            try:
                payload = get_json(next_url)
                if isinstance(payload, dict) and "next" in payload:
                    current = payload.get("next")
                elif isinstance(payload, dict) and payload.get("error"):
                    log(f"取下一首失败：{payload.get('error')}")
                    return 2
                else:
                    current = payload
            except Exception as exc:                   # noqa: BLE001
                log(f"取下一首失败：{exc}")
                return 2
            if current is not None and not isinstance(current, dict):
                log(f"取下一首失败：返回格式不对 {current!r}")
                return 2
            if current is not None and "internalId" not in current:
                log(f"取下一首失败：缺少 internalId {current!r}")
                return 2
            if not current:
                if need_refill:
                    refill_after_score()
                    need_refill = False
                log("没有可推送的歌，脚本结束")
                break
            pushed = False
        name = current.get("title") or ""
        diff = int(current.get("chartid") or 0)
        diff_name = DIFF_NAMES[diff] if 0 <= diff <= 4 else str(diff)
        if not pushed:
            # 先清事件再推送：推送期间到达的成绩不能被误清掉
            REPUSH_EVENT.clear()
            SCORE_EVENT.clear()
            if not send_jump(args.host, args.mod_port, int(current["internalId"]), diff, current.get("type") or ""):
                break
            log(f"已推送{push_pool_tag(current)}“{current['internalId']}”“{name}”“{diff_name}”({push_type_label(current)})")
            pushed = True
            if need_refill:
                refill_after_score()
                need_refill = False
        while not STOP_EVENT.is_set():
            if REPUSH_EVENT.is_set():
                REPUSH_EVENT.clear()
                if send_jump(args.host, args.mod_port, int(current["internalId"]), diff, current.get("type") or ""):
                    log(f"已推送{push_pool_tag(current)}“{current['internalId']}”“{name}”“{diff_name}”({push_type_label(current)})")
                else:
                    log("重新推送失败")
            if SCORE_EVENT.wait(0.2):
                break
        if STOP_EVENT.is_set():
            break
        with SCORE_LOCK:
            score = SCORES.pop(0) if SCORES else {}
        if not score:
            continue
        score = normalize_score(score)
        score_id = score.get("musicId") or score.get("internalId") or "?"
        score_diff = score.get("difficulty", score.get("chartid"))
        try:
            score_diff = int(score_diff)
            score_diff_name = DIFF_NAMES[score_diff] if 0 <= score_diff <= 4 else str(score_diff)
        except (TypeError, ValueError):
            score_diff_name = str(score_diff)
        score_name = score.get("name") or name
        ach = score.get("achive", score.get("achievement"))
        try:
            ach_text = f"{float(ach):.4f}"
        except Exception:
            ach_text = str(ach)
        combo = score.get("comboStatus", score.get("combo"))
        sync = score.get("syncStatus", score.get("sync"))
        dx = score.get("deluxscoreMax", score.get("dxscore"))
        log(f"收到回传成绩：“{score_id}”“{score_name}”“{score_diff_name}”，成绩：“{ach_text}”，"
            f"Status：“{combo}”“{sync}”，dx分：“{dx}”")
        payload = {"user": args.user, "score": score}
        try:
            st, body = post_json(args.server.rstrip("/") + "/api/assistant/bridge-score", payload, timeout=300)
            result = json.loads(body)
        except Exception as exc:                       # noqa: BLE001
            log(f"并入成绩失败：{exc}")
            break
        if result.get("error"):
            log(f"统计未完成：{result['error']}")
            break
        if result.get("changed"):
            log("存档更新成功。")
        if result.get("refillNeeded"):
            need_refill = True
        if result.get("matched"):
            current = None
            pushed = False
        else:
            # 别的歌的成绩：已并入，但当前卡继续等；不重发当前跳转
            log(f"回传不是当前待结算曲目（当前 {current['internalId']}:{diff}），继续等待")
            pushed = True
            SCORE_EVENT.clear()
            if need_refill:
                refill_after_score()
                need_refill = False
        # 收到回传后先等 2.5 秒，再进入下一轮推送
        STOP_EVENT.wait(2.5)
    srv.shutdown()
    log("打歌脚本结束。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
