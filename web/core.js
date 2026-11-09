import { buildChainNames, refreshAlsButton, refreshBuildButton, renderChains, renderRun, showPage, smallChainName, startRun, watch } from "./db.js";
import { currentUser, loadStats, refreshSmallButton, renderAvatar, selectUser } from "./stats.js";

// 用途：全局工具（$ / api / showPage / 页面路由）+ 用户表与用户切换（从 app.js 按领域拆出，classic script，index.html 按顺序加载）
// 依赖：全局函数/变量在调用时解析，不要求定义顺序
/* 打歌计划器 · 页面：建库（可折叠）+ 曲别名（Chiffonmai）+ 进度 */

function $(id) { return document.getElementById(id); }   // 函数声明：循环 import 时也能在模块求值前用
const NEEDS = {network: "联网", proxy: "走系统代理", gamePackage: "游戏目录", packageDir: "包目录", als: "别名文件", scores: "成绩文件", user: "用户"};

let config = null;
let chain = null;
let polling = null;
let busy = false;

function setPickMask(on) {
  const mask = document.getElementById("pickmask");
  if (mask) mask.hidden = !on;
}

async function api(path, options) {
  const isPick = /\/api\/(pick-|user-avatar$)/.test(path);   // 弹系统对话框的请求
  if (isPick) setPickMask(true);
  try {
    const res = await fetch(path, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok && !data.started) throw new Error(data.error || data.info || res.statusText);
    return data;
  } finally {
    if (isPick) setPickMask(false);
  }
}

/* 别名那块单独对应「needs 里有 als」的那条链（不写死链名） */

/* ── 用户 ── */
let usersById = {};

async function loadUsers() {
  let list = [];
  try {
    const data = await api("/api/users");
    list = data.users || [];
    usersById = Object.fromEntries(list.map((u) => [u.id, u]));
  } catch (err) {
    console.warn("读用户表失败：" + err.message);
  }
    renderAvatar();
  // 用户列表刚加载完，如果正好停在成绩页，补跑一次统计（首屏时 loadUsers 早于 showPage）
  const scoresPage = document.querySelector('section.page[data-page="scores"]:not([hidden])');
  if (scoresPage && currentUser) loadStats();
}



/* 一次性提示：写进 .note 元素，5s 后淡出并清空（重复调用会重置计时） */
const noteTimers = new Map();
export function setNote(id, text, holdMs = 5000) {
  const el = typeof id === "string" ? $(id) : id;
  if (!el) return;
  const key = el.id || id;
  const prev = noteTimers.get(key);
  if (prev) { clearTimeout(prev); noteTimers.delete(key); }
  el.textContent = text == null ? "" : String(text);
  el.classList.remove("note-fade");
  if (!el.textContent) return;
  const t = setTimeout(() => {
    el.classList.add("note-fade");
    setTimeout(() => {
      if (el.classList.contains("note-fade")) { el.textContent = ""; el.classList.remove("note-fade"); }
    }, 700);
  }, holdMs);
  noteTimers.set(key, t);
}

export { $, NEEDS, api, busy, chain, config, loadUsers, polling, setPickMask, usersById };

/* 所有弹层：任何一个可见时锁住页面滚动（弹层内部自己滚） */
const OVERLAY_IDS = ["songmodal", "songfull", "songfilter", "songsearch", "bldlg",
                     "newmodal", "delmodal", "cropbox", "pickmask", "verbox"];

export function installScrollLock() {
  const els = OVERLAY_IDS.map((id) => $(id)).filter(Boolean);
  const sync = () => {
    const any = els.some((el) => !el.hidden);
    document.documentElement.classList.toggle("no-scroll", any);
  };
  const obs = new MutationObserver(sync);
  els.forEach((el) => obs.observe(el, { attributes: true, attributeFilter: ["hidden"] }));
  sync();
}

export function setBusy(value) {
  busy = !!value;
}

export function setPolling(timer) {
  polling = timer;
}

export function setChain(name) {
  chain = name;
}

async function initUiToggle() {
  const btn = $("ui-toggle");
  if (!btn) return;
  let data = null;
  try { data = await api("/api/ui-assets"); } catch (_) { data = null; }
  const mode = data && data.mode === "vg" ? "vg" : "base";
  btn.disabled = false;
  btn.textContent = mode === "vg" ? "素材：代码" : "素材：图片";
  btn.title = data && data.canToggle
    ? "在游戏原图与代码版 UI 素材之间切换"
    : "素材不完整，切换时会弹出警告";
  btn.onclick = () => {
    if (!data || !data.canToggle) {
      const missData = data ? (data.missingData || []).length : 0;
      const missVg = data ? (data.missingVg || []).length : 0;
      const detail = data
        ? `dataUI 缺 ${missData} 个，dataUI_VG 缺 ${missVg} 个。`
        : "读取素材状态失败。";
      if (!confirm("素材不完整：" + detail + " 切换后可能显示缺图，仍要切换吗？")) return;
    }
    const next = mode === "vg" ? "base" : "vg";
    document.cookie = "ui_asset=" + next + "; path=/; max-age=31536000; SameSite=Lax";
    location.reload();
  };
}

/* ── 启动：读配置/链、恢复初始页、加载用户（v103：从页面顶部 IIFE 挪进来，由 main.js 在所有模块加载完后调用）── */
export async function boot() {
  await initUiToggle();
  $("small-build").onclick = () =>
    startRun({chain: smallChainName(), gamePackage: $("small").value || null}, $("small-note2"));

  $("stop").onclick = async () => { await api("/api/stop", {method: "POST"}); };


    config = await api("/api/config");
    chain = buildChainNames()[0];
    renderChains();
    refreshBuildButton();
    refreshAlsButton();
        refreshSmallButton();
    await loadUsers();
    if (!currentUser) {                       // 没选过用户就默认选排序里的第一个
      const first = Object.values(usersById)[0];
      if (first) selectUser(first.id);
    }
    showPage(location.hash.slice(1));
    const state = await api("/api/run");
    renderRun(state);
    if (state.running) watch();
}
