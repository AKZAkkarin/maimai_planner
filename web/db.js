import { $, NEEDS, api, busy, chain, config, polling, setBusy, setChain, setNote, setPolling } from "./core.js";
import { loadStats } from "./stats.js";
import { loadPool, loadSongs } from "./songlist.js";

// 用途：数据维护页：选包、链选择、跑构建、进度（从 app.js 按领域拆出，classic script，index.html 按顺序加载）
// 依赖：全局函数/变量在调用时解析，不要求定义顺序
function alsChainName() {
  if (!config) return null;
  for (const [name, info] of Object.entries(config.chains)) {
    if (info.steps.some((s) => (s.needs || []).includes("als"))) return name;
  }
  return null;
}

/* 数据小更新那条链（步骤里带 packageDir），它有自己的块 */
function smallChainName() {
  if (!config) return null;
  for (const [name, info] of Object.entries(config.chains)) {
    if (info.steps.some((s) => (s.needs || []).includes("packageDir"))) return name;
  }
  return null;
}

/* 成绩导入那条链（步骤里带 scores） */
function scoresChainName() {
  if (!config) return null;
  for (const [name, info] of Object.entries(config.chains)) {
    if (info.steps.some((s) => (s.needs || []).includes("scores"))) return name;
  }
  return null;
}

/* 页面切换：tab 的 data-page 对上 section.page 的 data-page；选中记进 #hash，刷新不丢 */
function showPage(name) {
  const verbox = document.getElementById("verbox");
  if (verbox) verbox.hidden = true;
  const pages = [...document.querySelectorAll("section.page")];
  const names = pages.map((p) => p.dataset.page);
  const want = names.includes(name) ? name : names[0];
  pages.forEach((p) => { p.hidden = p.dataset.page !== want; });
  document.querySelectorAll(".tab[data-page]").forEach((t) => t.classList.toggle("active", t.dataset.page === want));
  if (location.hash.slice(1) !== want) history.replaceState(null, "", "#" + want);
  if (want === "songs") loadSongs();           // 进歌曲页就拉曲库（loadSongs 自己会缓存）
  if (want === "scores") loadStats();          // 进成绩页就激活统计（每次进入都算一遍）
  if (want === "pool") loadPool();             // 卡池预览：读 analysis.json
}

document.querySelectorAll(".tab[data-page]").forEach((t) => { t.onclick = () => { showPage(t.dataset.page); if (t.dataset.page === "songs") loadSongs(); }; });
window.addEventListener("hashchange", () => { showPage(location.hash.slice(1)); if (location.hash.slice(1) === "songs") loadSongs(); });

/* 建库那块的链列表：自己有块的（别名、数据小更新）不在这里重复列 */
function buildChainNames() {
  const own = [alsChainName(), smallChainName(), scoresChainName()];
  return Object.keys(config.chains).filter((name) => !own.includes(name));
}

let showSteps = false;                     // 「显示流程」开关，默认关

function renderChains() {
  const box = $("chains");
  const toggle = $("steps-toggle");
  if (toggle) toggle.textContent = showSteps ? "隐藏流程" : "显示流程";
  box.innerHTML = "";
  for (const name of buildChainNames()) {
    const info = config.chains[name];
    const label = document.createElement("label");
    label.className = "chain";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "chain";
    radio.value = name;
    radio.checked = name === chain;
    radio.onchange = () => { setChain(name); renderChains(); refreshBuildButton(); };
    const text = document.createElement("span");
    const needs = [...new Set(info.steps.flatMap((s) => s.needs || []))].filter((n) => n !== "proxy");
    // 只有「联网更新数据」这条链需要看数据有多旧
    const age = name === "dxratingUpdate" ? (config.chainAges || {})[name] : null;
    const ageText = age ? (age.ageDays < 1 ? "今天更新" : `${Math.floor(age.ageDays)} 天前`) : "";
    const ageColor = !age ? "" : (age.ageDays <= 3 ? "var(--ok)" : age.ageDays <= 7 ? "#e2a93b" : "var(--bad)");
    text.innerHTML = `<b>${info.title}</b>` +
      (needs.length ? `<small>${needs.map((n) => NEEDS[n] || n).join("・")}</small>` : "") +
      (ageText ? `<small class="chain-age" style="color:${ageColor}">${ageText}</small>` : "") +
      (showSteps ? `<small class="chain-steps">${info.steps.map((s) => s.script).join(" → ")}</small>` : "");
    label.append(radio, text);
    box.append(label);
  }
}

function currentChain() {
  return config && chain ? config.chains[chain] : null;
}

function refreshBuildButton() {
  const info = currentChain();
  const needsPkg = info ? info.steps.some((s) => (s.needs || []).includes("gamePackage")) : false;
  const needsNet = info ? info.steps.some((s) => (s.needs || []).includes("network")) : false;
  const note = !info ? "" : (needsPkg && !$("pkg").value ? "未选择游戏目录" : (needsNet ? "需要联网" : ""));
  $("build-note").textContent = note;
  $("build").disabled = busy || !info || (needsPkg && !$("pkg").value);
}

function refreshAlsButton() {
  const name = alsChainName();
  const info = name ? config.chains[name] : null;
  if (!info) {
    $("als-build").disabled = true;
    setNote("als-note2", "这张步骤表里没有带别名文件的链。");
    return;
  }
  $("als-build").disabled = busy || !$("als").value;
  setNote("als-note2", $("als").value ? "" : "先选那份 JSON。");
}

function stepLine(step) {
  const mark = {pending: "·", running: "…", done: "✓", failed: "✗"}[step.state] || "·";
  const cls = step.state || "pending";
  const secs = step.seconds ? ` ${step.seconds}s` : "";
  const why = step.state === "failed" && step.reason ? ` —— ${step.reason}` : "";
  const counts = step.products && step.products[0] && step.products[0].counts
    ? " " + JSON.stringify(step.products[0].counts).replace(/["{}]/g, "").slice(0, 90) : "";
  return `<li class="${cls}"><span class="mark">${mark}</span> <b>${step.script}</b>${secs}${why}<small>${counts}</small></li>`;
}

function renderRun(state) {
  if (state.steps && state.steps.length) {
    const list = $("steps");
    list.innerHTML = state.steps.map(stepLine).join("");
    // 歌词式：把当前步骤滚到中间（5 行窗口）
    const cur = list.querySelector("li.running") || list.querySelector("li.pending") || list.lastElementChild;
    if (cur) {
      const top = cur.offsetTop - (list.clientHeight - cur.offsetHeight) / 2;
      list.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }
  }
  const logEl = $("log");
  // 新内容来了自动跟到最下面；用户手动往上翻时不抢（翻回底部后自动恢复）
  const stick = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 24;
  logEl.textContent = (state.log || []).join("\n");
  if (stick) logEl.scrollTop = logEl.scrollHeight;
  if (state.running) $("logbox").open = true;

  const result = $("result");
  if (state.running) {
    result.className = "result running";
    result.textContent = `正在跑：${state.chain} · 批次 ${state.runId} · ${state.seconds}s`;
  } else if (state.result) {
    result.className = "result " + (state.result.ok ? "ok" : "bad");
    result.textContent = state.result.ok
      ? `完成 ✓ 全部成功（批次 ${state.runId}）`
      : `失败 ✗ ${state.result.reason || ""}`;
  } else {
    result.className = "result";
    result.textContent = "";
  }
  $("stop").disabled = !state.running;
  setBusy(!!state.running);
  refreshBuildButton();
  refreshAlsButton();
}

async function startRun(body, noteEl) {
  try {
    await api("/api/build", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    $("steps").innerHTML = '<li class="idle">正在启动…</li>';
    setBusy(true);
    refreshBuildButton();
    refreshAlsButton();
    watch();
  } catch (err) {
    noteEl.textContent = "启动失败：" + err.message;
  }
}

function watch() {
  clearInterval(polling);
  let wasRunning = false;
  setPolling(setInterval(async () => {
    const state = await api("/api/run");
    renderRun(state);
    if (wasRunning && !state.running) {
      try {
        const fresh = await api("/api/config");
        config.chains = fresh.chains;
        config.chainAges = fresh.chainAges;
        renderChains();
      } catch (_) { /* 刷新失败不影响本次运行结果 */ }
    }
    wasRunning = !!state.running;
    if (!state.running) clearInterval(polling);
  }, 700));
}

$("pick").onclick = async () => {
  setNote("pkg-note", "等待选取中…");
  try {
    const data = await api("/api/pick-folder", {method: "POST"});
    $("pkg").value = data.path || "";
    setNote("pkg-note", data.path ? (data.warning || "完成") : (data.note || "已取消"));
  } catch (err) {
    setNote("pkg-note", "选目录失败：" + err.message);
  }
  refreshBuildButton();
};

const stepsToggle = $("steps-toggle");
if (stepsToggle) stepsToggle.onclick = () => { showSteps = !showSteps; renderChains(); };

$("build").onclick = () =>
  startRun({chain, gamePackage: $("pkg").value || null}, $("build-note"));

$("pick-als").onclick = async () => {
  setNote("als-note", "等待选取中…");
  try {
    const data = await api("/api/pick-file", {method: "POST"});
    $("als").value = data.path || "";
    setNote("als-note", data.path ? (data.warning || "完成") : (data.note || "已取消"));
  } catch (err) {
    setNote("als-note", "选文件失败：" + err.message);
  }
  refreshAlsButton();
};

$("als-build").onclick = () =>
  startRun({chain: alsChainName(), als: $("als").value || null}, $("als-note2"));

/* 本地用户下拉：数据来自 /api/users（uuid ↔ 名字的对照表）。成绩页和导入块共用一份。 */


export { alsChainName, buildChainNames, currentChain, refreshAlsButton, refreshBuildButton, renderChains, renderRun, scoresChainName, showPage, smallChainName, startRun, stepLine, watch };
