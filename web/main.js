// 用途：ESM 入口 —— 先加载各领域模块，再跑启动流程
import "./core.js";
import "./db.js";
import "./stats.js";
import "./songlist.js";
import "./songcard.js";
import "./assistant.js";
import { boot, installScrollLock } from "./core.js";
import { attachPageUp } from "./pageup.js";

attachPageUp();
installScrollLock();
boot().catch((err) => console.error("启动失败", err));
