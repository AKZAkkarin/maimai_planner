# 用途：按 chains.json 的步骤表串行跑构建脚本，每步核对批次号（runId），防抢跑、防下游吃旧产物。
# 状态：现行
"""run_chain.py · 构建链编排

【它做什么】
  读同目录 chains.json 里某条链的 steps，按顺序 subprocess 跑 maimai-planner/tools/<目录>/<脚本>.py。
  一步「退出码 0 且产物里出现本批 runId」才算成功，才发下一步；任一步失败立即停（fail-stop）。

【为什么核对 runId】
  脚本之间不 import、不互相调用，文件就是接口。要确认「下游读到的是本批上游刚写的那一份」，
  编排者生成一个 uuid，用环境变量 MAIMAI_RUN_ID 下发给子脚本，子脚本写进产物头部的 runId 字段；
  编排者再读产物核对：对不上＝没跑完或还是上一批 → 停下不发下一步。手工直接跑脚本时 runId = manual。

【只读 / 只写】
  读：chains.json、各步骤声明的产物文件（只读来核对 runId）
  写：本目录 .run_chain.lock（互斥锁，跑完自动删）、本目录 .logs/（每步的子进程原始输出，只留最近 40 个）
  绝不写 data/ 里的产物 —— 产物只由各脚本自己原子替换。

【退出码】
  0 全部成功 / 2 配置或输入问题 / 3 产物非本批或缺失 / 4 某步失败 / 5 某步超时

【stdout 事件】一行一条 NDJSON（前缀 RUNCHAIN），给 server / WebUI 流式读：
  run.start / step.start / step.log / step.done / step.fail / run.done / run.fail

【用法】
  python maimai-planner/tools/run_chain/run_chain.py --list
  python maimai-planner/tools/run_chain/run_chain.py --chain dxratingUpdate --dry-run
  python maimai-planner/tools/run_chain/run_chain.py --chain full --game-package "D:\\...\\StreamingAssets"

【--game-package】
  chains.json 里 needs 含 gamePackage 的步骤（localMusicData_build）在 args 里用 {gamePackage} 占位；
  不传就报错停下（退出码 2），不会拿旧产物凑合。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CHAINS_NAME = "chains.json"
LOCK_NAME = ".run_chain.lock"
LOG_DIR_NAME = ".logs"
LOG_KEEP = 40
RUN_ID_ENV_VAR = "MAIMAI_RUN_ID"
MANUAL_RUN_ID = "manual"
STALE_LOCK_SECONDS = 6 * 3600
TAIL_LINES = 25
TZ_CN = timezone(timedelta(hours=8))
BAR = "─" * 62

sys.path.insert(0, str(PROJECT_DIR))
try:
    import env  # type: ignore
except Exception:  # pragma: no cover - env.py 应当总在，兜底不拦
    env = None


def now_stamp() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+0800")


def emit(event: str, **fields) -> None:
    """一行 NDJSON 事件，给上层读；人类看的是同一条。"""
    payload = {"event": event, "at": now_stamp(), **fields}
    print("RUNCHAIN " + json.dumps(payload, ensure_ascii=False), flush=True)


def find_script(name: str, file_name: str | None = None) -> Path | None:
    """脚本在 tools/<名>/；文件名默认与目录同名，否则取目录里唯一的 .py。"""
    for parent in (PROJECT_DIR / "tools",):
        folder = parent / name
        if not folder.is_dir():
            continue
        if file_name:
            candidate = folder / file_name
            return candidate if candidate.is_file() else None
        same_name = folder / f"{name}.py"
        if same_name.is_file():
            return same_name
        py_files = sorted(folder.glob("*.py"))
        if len(py_files) == 1:
            return py_files[0]
        return None
    return None


def pid_alive(pid: int) -> bool:
    """进程还在不在。不能用 os.kill(pid, 0)：Windows 上那会真把进程杀掉。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            # 只看 OpenProcess 会误判：进程已经死了、但内核对象还被句柄或 Job 钉着时，
            # OpenProcess 照样成功（实测：被 kill 的 run_chain 会被判成「还在跑」，
            # 于是中止之后再也起不了下一条链）。必须再问退出码，259=STILL_ACTIVE 才是真活着。
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Lock:
    """互斥锁：同一时间只允许一条链在跑（server 与手工各起一个也会撞上）。"""

    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def acquire(self, run_id: str) -> None:
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            holder = self.path.read_text(encoding="utf-8", errors="replace").strip()
            pid = next((int(t[4:]) for t in holder.split()
                        if t.startswith("pid=") and t[4:].isdigit()), 0)
            alive = pid_alive(pid)
            if alive and age < STALE_LOCK_SECONDS:
                raise SystemExit(
                    f"[错误] 已有一条链在跑（锁 {self.path}，{int(age)} 秒前建立）：{holder}\n"
                    f"       等它结束；确认它已经死了再删掉这个文件重跑。"
                )
            print(f"[注意] 清掉过期锁（{'进程不在了' if not alive else f'{int(age / 3600)} 小时前'}）：{holder}",
                  flush=True)
            self.path.unlink(missing_ok=True)      # 光印一行不算清掉：不删文件，下面 O_EXCL 建锁会 FileExistsError
        payload = f"pid={os.getpid()} runId={run_id} at={now_stamp()}"
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        self.held = True

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


def read_product(path: Path, run_id: str) -> dict:
    """核对一份产物：文件在不在、runId 对不对、顺手取 counts 给人看。"""
    if not path.is_file():
        return {"path": str(path), "ok": False, "why": "文件不存在"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"path": str(path), "ok": False, "why": f"读不出 JSON：{exc}"}
    if not isinstance(doc, dict):
        return {"path": str(path), "ok": False, "why": "顶层不是对象"}
    got = doc.get("runId")
    if got != run_id:
        return {"path": str(path), "ok": False, "why": f"runId={got!r}（本批 {run_id}）",
                "counts": doc.get("counts")}
    return {"path": str(path), "ok": True, "runId": got, "counts": doc.get("counts")}


def prune_logs(log_dir: Path) -> None:
    """只留最近 LOG_KEEP 个日志文件。"""
    entries = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in entries[LOG_KEEP:]:
        stale.unlink(missing_ok=True)


def drain(path: Path, pos: int) -> tuple[str, int]:
    """读临时日志文件里 pos 之后的新内容。"""
    with open(path, "r", encoding="utf-8", errors="replace") as reader:
        reader.seek(pos)
        return reader.read(), reader.tell()


def run_step(step: dict, index: int, total: int, run_id: str, python: str,
             game_package: str | None, als: str | None, scores: str | None,
             user: str | None, timeout: float) -> dict:
    """跑一步：流式转发子进程 stdout，核对产物 runId。返回 {ok, exit, reason, products}。"""
    name = step["script"]
    script_path = find_script(name, step.get("file"))
    if script_path is None:
        return {"ok": False, "exit": None, "reason": f"找不到脚本 tools/{name}/"}

    raw_args = list(step.get("args") or [])
    if any("{gamePackage}" in a for a in raw_args) and not game_package:
        return {"ok": False, "exit": None, "reason": "这一步需要游戏包目录，但没给 --game-package"}
    if any("{als}" in a for a in raw_args) and not als:
        return {"ok": False, "exit": None, "reason": "这一步需要 Chiffonmai 的别名文件，但没给 --als"}
    if any("{scores}" in a for a in raw_args) and not scores:
        return {"ok": False, "exit": None, "reason": "这一步需要 MuNET 导出的存档，但没给 --scores"}
    if any("{user}" in a for a in raw_args) and not user:
        return {"ok": False, "exit": None, "reason": "这一步需要本地用户，但没给 --user"}
    cmd = [python, str(script_path)] + [
        a.replace("{gamePackage}", game_package or "").replace("{als}", als or "")
         .replace("{scores}", scores or "").replace("{user}", user or "") for a in raw_args]

    emit("step.start", script=name, index=index, total=total, cmd=" ".join(cmd[1:]) or None)
    child_env = {**os.environ, RUN_ID_ENV_VAR: run_id, "PYTHONDONTWRITEBYTECODE": "1",
                 "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    started = time.time()
    hit_timeout = False
    # 子进程 stdio 落日志文件，不用管道：受限沙箱里管道会被拒（WinError 5）；
    # 文件方式照样能边跑边读、实时转发，跑完日志还留着可查。
    log_dir = HERE / LOG_DIR_NAME
    log_dir.mkdir(exist_ok=True)
    prune_logs(log_dir)
    out_path = log_dir / f"{run_id}.{index:02d}.{name}.out.log"
    err_path = log_dir / f"{run_id}.{index:02d}.{name}.err.log"
    with open(out_path, "wb") as out_handle, open(err_path, "wb") as err_handle:
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_DIR), env=child_env,
                                stdout=out_handle, stderr=err_handle)
        pos = 0
        while True:
            chunk, pos = drain(out_path, pos)
            for line in chunk.splitlines():
                emit("step.log", script=name, line=line)
            if proc.poll() is not None:
                break
            if timeout > 0 and time.time() - started > timeout:
                hit_timeout = True
                proc.kill()
                proc.wait()
                break
            time.sleep(0.2)
        chunk, pos = drain(out_path, pos)          # 收尾再读一次，免得最后几行落在 poll 之后
        for line in chunk.splitlines():
            emit("step.log", script=name, line=line)
    exit_code = proc.returncode
    stderr_text = err_path.read_text(encoding="utf-8", errors="replace")
    seconds = round(time.time() - started, 1)

    if hit_timeout:
        emit("step.fail", script=name, seconds=seconds, reason=f"超时（>{int(timeout)}s）")
        return {"ok": False, "exit": exit_code, "reason": f"超时（>{int(timeout)}s）", "products": []}

    if exit_code != 0:
        tail = [l for l in stderr_text.strip().splitlines() if l.strip()][-TAIL_LINES:]
        for line in tail:
            emit("step.log", script=name, line=line, stream="stderr")
        emit("step.fail", script=name, seconds=seconds, exit=exit_code, reason="子脚本退出码非 0",
             stderr_tail=tail)
        return {"ok": False, "exit": exit_code, "reason": "子脚本退出码非 0", "products": []}

    products = []
    for rel in step.get("products") or []:
        products.append(read_product((PROJECT_DIR / rel).resolve(), run_id))
    bad = [p for p in products if not p["ok"]]
    if bad:
        emit("step.fail", script=name, seconds=seconds, exit=exit_code,
             reason="产物里没有本批 runId：" + "；".join(f"{p['path']}（{p['why']}）" for p in bad),
             products=products)
        return {"ok": False, "exit": exit_code, "reason": "产物非本批或缺失", "products": products,
                "bad": True}

    emit("step.done", script=name, seconds=seconds, exit=exit_code, products=products)
    return {"ok": True, "exit": exit_code, "products": products, "seconds": seconds}


def main(argv=None) -> int:
    if env is not None and callable(getattr(env, "force_utf8_stdio", None)):
        env.force_utf8_stdio()

    ap = argparse.ArgumentParser(
        prog="run_chain.py",
        description="按 chains.json 的顺序串行跑构建脚本，用 runId 核对产物，失败即停",
    )
    ap.add_argument("--chain", metavar="NAME", help="要跑的链名（--list 看有哪些）")
    ap.add_argument("--chains-file", metavar="PATH", help=f"步骤表（默认本目录 {CHAINS_NAME}）")
    ap.add_argument("--list", action="store_true", help="列出所有链和步骤，不跑")
    ap.add_argument("--only", metavar="SCRIPT", help="只跑链里某一步（调试用，仍然核对 runId）")
    ap.add_argument("--game-package", metavar="PATH", help="游戏包目录（含 music/），填 {gamePackage} 占位")
    ap.add_argument("--als", metavar="PATH", help="Chiffonmai 的别名文件，填 {als} 占位")
    ap.add_argument("--scores", metavar="PATH", help="MuNET 导出的存档 JSON，填 {scores} 占位")
    ap.add_argument("--user", metavar="UUID", help="本地用户 uuid，填 {user} 占位")
    ap.add_argument("--run-id", metavar="UUID", help="批次号（默认自己生成一个）")
    ap.add_argument("--python", metavar="PATH", help="跑子脚本的解释器（默认当前解释器）")
    ap.add_argument("--timeout", type=float, default=1800.0, metavar="SEC",
                    help="单步超时秒数，0＝不限（默认 1800）")
    ap.add_argument("--dry-run", action="store_true", help="只打印要跑什么，不执行")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    chains_path = Path(args.chains_file).resolve() if args.chains_file else HERE / CHAINS_NAME
    if not chains_path.is_file():
        print(f"[错误] 没有步骤表：{chains_path}", file=sys.stderr)
        return 2
    try:
        config = json.loads(chains_path.read_text(encoding="utf-8"))
        chains = config["chains"]
    except Exception as exc:
        print(f"[错误] 步骤表读不动：{exc}", file=sys.stderr)
        return 2

    if args.list or not args.chain:
        print(BAR)
        print(f"构建链（{chains_path.name}）")
        for name, chain in chains.items():
            print(f"  {name:<24} {chain.get('title', '')}")
            for i, step in enumerate(chain.get("steps") or [], 1):
                needs = ",".join(step.get("needs") or []) or "-"
                print(f"      {i}. {step['script']:<22} needs={needs}")
        print(BAR)
        print("用法：run_chain.py --chain <名> [--game-package PATH] [--dry-run]")
        return 0 if args.list else 2

    chain = chains.get(args.chain)
    if chain is None:
        print(f"[错误] 没有这条链：{args.chain}（--list 看可用的）", file=sys.stderr)
        return 2

    steps = list(chain.get("steps") or [])
    if args.only:
        steps = [s for s in steps if s["script"] == args.only]
        if not steps:
            print(f"[错误] 这条链里没有步骤：{args.only}", file=sys.stderr)
            return 2

    missing = sorted({n for s in steps for n in (s.get("needs") or [])
                      if n in ("gamePackage", "als", "scores", "user")})
    if "gamePackage" in missing and not args.game_package:
        print(f"[错误] 这条链需要 --game-package（{', '.join(s['script'] for s in steps if 'gamePackage' in (s.get('needs') or []))}）",
              file=sys.stderr)
        return 2
    if "gamePackage" in missing and not Path(args.game_package).is_dir():
        print(f"[错误] 游戏包目录不存在：{args.game_package}", file=sys.stderr)
        return 2
    if "als" in missing and not args.als:
        print(f"[错误] 这条链需要 --als（{', '.join(s['script'] for s in steps if 'als' in (s.get('needs') or []))}）",
              file=sys.stderr)
        return 2
    if "als" in missing and not Path(args.als).is_file():
        print(f"[错误] 别名文件不存在：{args.als}", file=sys.stderr)
        return 2
    if "scores" in missing and not args.scores:
        print(f"[错误] 这条链需要 --scores（{', '.join(s['script'] for s in steps if 'scores' in (s.get('needs') or []))}）",
              file=sys.stderr)
        return 2
    if "scores" in missing and not Path(args.scores).is_file():
        print(f"[错误] 存档文件不存在：{args.scores}", file=sys.stderr)
        return 2
    if "user" in missing and not args.user:
        print(f"[错误] 这条链需要 --user（{', '.join(s['script'] for s in steps if 'user' in (s.get('needs') or []))}）",
              file=sys.stderr)
        return 2

    run_id = args.run_id or uuid.uuid4().hex[:12]
    python = args.python or sys.executable
    total = len(steps)

    emit("run.start", chain=args.chain, runId=run_id, steps=[s["script"] for s in steps],
         gamePackage=args.game_package, als=args.als, scores=args.scores, user=args.user,
         dryRun=bool(args.dry_run))
    print(BAR)
    print(f"构建链 {args.chain} · 批次 {run_id} · {total} 步")

    if args.dry_run:
        for i, step in enumerate(steps, 1):
            target = find_script(step["script"], step.get("file"))
            print(f"  {i}. {step['script']:<22} → {target or '(找不到脚本)'}")
        print(BAR)
        emit("run.done", chain=args.chain, runId=run_id, ok=True, dryRun=True, seconds=0.0)
        return 0

    lock = Lock(HERE / LOCK_NAME)
    try:
        lock.acquire(run_id)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        emit("run.fail", chain=args.chain, runId=run_id, reason=str(exc))
        return 2

    started = time.time()
    try:
        for i, step in enumerate(steps, 1):
            result = run_step(step, i, total, run_id, python, args.game_package, args.als,
                              args.scores, args.user, args.timeout)
            if result["ok"]:
                print(f"  [完成] {i}/{total} {step['script']}", flush=True)
                continue
            seconds = round(time.time() - started, 1)
            reason = result["reason"]
            code = 5 if "超时" in reason else (3 if result.get("bad") else 4)
            print(f"  [失败] {i}/{total} {step['script']} —— {reason}", flush=True)
            print("  已停在失败处，后面的步骤没有跑。", flush=True)
            emit("run.fail", chain=args.chain, runId=run_id, step=step["script"], reason=reason,
                 exit=result.get("exit"), seconds=seconds)
            return code
        seconds = round(time.time() - started, 1)
        print(BAR)
        print(f"全部成功 · {total} 步 · {seconds}s · 批次 {run_id}")
        emit("run.done", chain=args.chain, runId=run_id, ok=True, seconds=seconds)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
