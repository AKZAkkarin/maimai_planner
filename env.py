# 用途：项目内共享的目录常量，给 tools/ lib/ server/ 里的所有代码 import
# 状态：现行（目录契约，改动前先看 maimai-planner/readme.txt）
"""项目目录锚点：一切路径都相对「本文件所在目录」推导，零硬编码绝对路径。

设计约束（用户 2026-10-15 拍板）：
  * 脚本里「绝不」写死盘符/用户名/固定地址 —— 整个 maimai-planner/ 文件夹
    被移到任何位置（换盘、换机器、放进别的仓库）都必须照样能跑。
  * 机器相关的「数据源」路径（游戏包 D:/Maimai/… 之类）不写进代码、也不落
    配置文件：游戏文件的读取是一次性的，由网页前端让用户当场选文件夹 →
    后端把选中的内容写进 data/，之后所有计算只认 data/ 里的库。
    （见 maimai-planner/readme.txt「数据从哪来」一节）

【data/ 的存放约定（用户 2026-10-15 拍板）】
  data/ 下**按产出它的脚本名分目录**，一份脚本一个文件夹，互不混淆：

      data/
        README.md                    ← 索引：哪个目录是谁产的
        catalog_dxdata/              ← tools/catalog_dxdata/ 的产物
            dxdata.json
            dxdata.meta.json
        <别的脚本名>/                ← 别的 tools/<脚本名>/ 的产物

  脚本要用 data_dir_for("catalog_dxdata") 取自己的目录，**不要**直接往
  DATA_DIR 根上写 —— 根目录只留 README.md。

  **用户数据不进 data/**（2026-10-19 拆分）：成绩 / 头像 / users.json 放在
  `dataUser/`（env.DATA_USER_DIR，用 user_data_dir() 取）。data/ 可以整个删掉重建，
  dataUser/ 重建不回来，两者不要混。

用法：
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # tools/xxx/xxx.py
    import env
    print(env.DATA_DIR)                        # …/maimai-planner/data（总目录）
    print(env.data_dir_for("catalog_dxdata"))  # …/maimai-planner/data/catalog_dxdata（本脚本的）
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR: Path = Path(__file__).resolve().parent      # …/maimai-planner
DATA_DIR: Path = PROJECT_DIR / "data"                   # 生成的数据库（可整个删掉重建）
DATA_USER_DIR: Path = PROJECT_DIR / "dataUser"           # 用户数据（成绩 / 头像 / 用户表；重建不回来）
TOOLS_DIR: Path = PROJECT_DIR / "tools"                 # 离线脚本（重活）
LIB_DIR: Path = PROJECT_DIR / "lib"                     # 纯函数库（被 import）
SERVER_DIR: Path = PROJECT_DIR / "server"               # 常驻后端
WEB_DIR: Path = PROJECT_DIR / "web"                     # 前端静态文件


def user_data_dir(*, create: bool = False) -> Path:
    """返回用户数据根目录 **dataUser/**（成绩、头像、users.json、last-import.json）。

    和 data/ 分开：data/ 是「按脚本名分目录」的曲库产物，整目录删掉都能从游戏包/网络重建；
    dataUser/ 是个人数据，丢了补不回来，所以不放在 data/ 里，重建曲库时也不会被连带清掉。
    create=True 时顺手建目录。
    """
    if create:
        DATA_USER_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_USER_DIR


def data_dir_for(script_name: str, *, create: bool = False) -> Path:
    """返回「某个脚本专属的 data/ 子目录」：DATA_DIR / <脚本名>。

    约定见模块 docstring：data/ 下按**产出它的脚本名**分目录，谁产的就归谁。
    脚本名与本项目 tools/<脚本名>/<脚本名>.py 的目录名保持一致，便于对应。
    create=True 时顺手建目录（命令行工具写盘前调一次）。
    """
    if not script_name or "/" in script_name or "\\" in script_name or script_name in (".", ".."):
        raise ValueError(f"脚本名不合法：{script_name!r}（应是 tools/ 下的目录名，如 'catalog_dxdata'）")
    path = DATA_DIR / script_name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path

if __name__ == "__main__":                              # 自检：python env.py
    for name in ("PROJECT_DIR", "DATA_DIR", "DATA_USER_DIR", "TOOLS_DIR", "LIB_DIR",
                 "SERVER_DIR", "WEB_DIR"):
        print(f"{name:14} = {globals()[name]}")


def force_utf8_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8，避免中文输出在 GBK 控制台/管道里炸掉。

    本机实测（2026-10-15）：DSH 的 Python 3.12 默认 sys.stdout.encoding = 'gbk'、
    UTF-8 mode 关闭 → 只要脚本 print 中文再被重定向或接管道，就会
    UnicodeEncodeError。每个会 print 中文的命令行工具在 main() 开头调用一次即可：

        import env
        env.force_utf8_stdio()
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
