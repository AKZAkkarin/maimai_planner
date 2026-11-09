// 用途：打歌助手页面（雷达图 / 12 卡牌桌 / 三首预览 / 启动与日志）
// 状态：v1（页面与后端接口联动，UI 先保证可操作）
import { api } from "./core.js";
import { currentUser } from "./stats.js";
import { openSongFilterPicker } from "./songlist.js";
import { fitGrid, renderCardPage } from "./songlist-view.js";

const POOLS = {
  mastered: "熟练", pushable_b35: "推b35", pushable_b15: "推b15",
  ap: "AP推分", practice: "弱点练习", accuracy: "准度练习",
  challenge: "有挑战", unplayed: "待开荒", favorite: "收藏",
  underdog: "随机下埋",
};
const DIFFS = [["basic", "BAS"], ["advanced", "ADV"], ["expert", "EXP"], ["master", "MAS"], ["remaster", "REM"]];
let ASTATE = null;
let POLL = null;
let POLL_BUSY = false;
let POLL_PREVIEW = "";
let POLL_TICK = 0;
let PLATES = [];
let SETTINGS_INDEX = -1;

function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function currentPage() {
  return document.querySelector('.tab[data-page="assistant"]');
}

async function loadAssistant() {
  if (!currentUser) {
    renderEmpty("先选择一个用户。");
    return;
  }
  try {
    // 建库可能在页面已经打开之后才完成；每次进助手页都重新拉一次牌子表。
    await loadPlates();
    const data = await api("/api/assistant/state?user=" + encodeURIComponent(currentUser) + "&counts=1");
    ASTATE = data;
    renderAll();
    startPoll();
  } catch (err) {
    renderEmpty("打歌助手加载失败：" + err);
  }
}

function renderEmpty(text) {
  const table = document.getElementById("assist-table");
  if (table) table.innerHTML = '<p class="hint">' + esc(text) + "</p>";
  const preview = document.getElementById("assist-preview");
  if (preview) preview.innerHTML = "";
  const log = document.getElementById("assist-log");
  if (log) log.textContent = "";
}

function renderAll() {
  if (!ASTATE) return;
  renderRating();
  renderRadars();
  renderTable();
  renderPreview();
  renderStatus();
  renderSettings();
  renderLog();
}

function renderRating() {
  const el = document.getElementById("assist-rating");
  if (!el) return;
  const r = ASTATE.profile && ASTATE.profile.rating;
  el.innerHTML = "<span>当前 Rating</span><b>" + (r == null ? "—" : esc(r)) + "</b>"
    + (ASTATE.running ? '<span class="assist-live">运行中</span>' : "");
}

const CONFIG_ORDER = [
  "爆发", "转圈", "大位移", "绝赞段", "定拍", "跳拍", "交互",
  "散打", "纵连", "扫键", "反手", "错位", "一笔画", "拆弹",
];

function toneOf(value) {
  const v = Number(value) || 0;
  return v >= 0.05 ? "good" : (v <= -0.05 ? "bad" : "neutral");
}

function orderAxes(axes, order) {
  const byTag = new Map(axes.map((a) => [a.tag, a]));
  const out = order.map((tag) => byTag.get(tag)).filter(Boolean);
  const known = new Set(order);
  for (const a of axes) if (!known.has(a.tag)) out.push(a);
  return out;
}

function radarSvg(axes, title) {
  const w = 340, h = 330, cx = w / 2, cy = h / 2 + 8, R = 116;
  const min = -0.25, max = 0.25;
  const n = Math.max(3, axes.length);
  const pt = (i, r) => {
    const a = -Math.PI / 2 + (Math.PI * 2 * i) / n;
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
  };
  let s = '<svg class="assist-radar-svg" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(title) + '">';
  for (const rr of [0.25, 0.5, 0.75, 1]) {
    const ring = [];
    for (let i = 0; i < n; i++) ring.push(pt(i, R * rr).join(","));
    s += '<polygon points="' + ring.join(" ") + '" fill="none" stroke="' + (rr === 0.5 ? "#7aa2ff" : "#313747") + '" stroke-width="' + (rr === 0.5 ? 1.4 : 1) + '"/>';
  }
  for (let i = 0; i < n; i++) {
    const [x, y] = pt(i, R);
    s += '<line x1="' + cx + '" y1="' + cy + '" x2="' + x + '" y2="' + y + '" stroke="#313747"/>';
    const [lx, ly] = pt(i, R + 21);
    s += '<text x="' + lx + '" y="' + ly + '" text-anchor="' + (Math.abs(lx - cx) < 8 ? "middle" : (lx > cx ? "start" : "end")) + '" dominant-baseline="middle" class="assist-axis" data-i="' + i + '">' + esc(axes[i].tag) + "</text>";
  }
  const vals = axes.map((a) => {
    const v = Math.max(min, Math.min(max, Number(a.value) || 0));
    // 幂函数压缩两端、放大 0~0.10：p=0.5；0 仍在 0.5 半径
    const x = v / max;
    return 0.5 + 0.5 * Math.sign(x) * Math.pow(Math.abs(x), 0.5);
  });
  const points = vals.map((r, i) => pt(i, R * r).join(","));
  s += '<polygon points="' + points.join(" ") + '" fill="rgba(160,200,255,.16)" stroke="#a9c7ef" stroke-width="1.5"/>';
  vals.forEach((r, i) => {
    const [x, y] = pt(i, R * r);
    const tone = toneOf(axes[i].value);
    s += '<circle class="assist-point" data-i="' + i + '" cx="' + x + '" cy="' + y + '" r="2.6" fill="#cfe0ff"/>';
    s += '<circle class="assist-hit" data-i="' + i + '" data-tone="' + tone + '" cx="' + x + '" cy="' + y + '" r="9" fill="transparent"/>';
  });
  return s + "</svg>";
}

function bindRadarHover(container) {
  if (!container) return;
  container.querySelectorAll(".assist-hit").forEach((hit) => {
    const i = hit.dataset.i;
    const point = container.querySelector('.assist-point[data-i="' + i + '"]');
    const label = container.querySelector('.assist-axis[data-i="' + i + '"]');
    const tone = hit.dataset.tone;
    const on = () => {
      point?.classList.add("hot", tone);
      label?.classList.add("hot", tone);
    };
    const off = () => {
      point?.classList.remove("hot", "good", "bad", "neutral");
      label?.classList.remove("hot", "good", "bad", "neutral");
    };
    hit.addEventListener("mouseenter", on);
    hit.addEventListener("mouseleave", off);
  });
}

function renderRadars() {
  const p = ASTATE.profile || {};
  const config = orderAxes(p.config || [], CONFIG_ORDER);
  const evalTags = orderAxes(p.eval || [], ["体力谱", "高物量", "底力谱", "键盘谱", "星星谱"]);
  const a = document.getElementById("assist-radar-config");
  const b = document.getElementById("assist-radar-eval");
  if (a) {
    a.innerHTML = config.length ? radarSvg(config, "配置标签") : '<p class="hint">还没有标签数据</p>';
    bindRadarHover(a);
  }
  if (b) {
    b.innerHTML = evalTags.length ? radarSvg(evalTags, "评价标签") : '<p class="hint">还没有标签数据</p>';
    bindRadarHover(b);
  }
}

let DRAG = null;
let CARD_SORTABLE = null;
let CARD_SORTING = false;

function flipCards(box, before) {
  for (const el of box.querySelectorAll(".assist-card")) {
    if (el.classList.contains("assist-placeholder")) continue;
    const old = before.get(el);
    if (!old) continue;
    const dx = old.left - el.offsetLeft, dy = old.top - el.offsetTop;
    if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
      el.animate(
        [{ transform: "translate(" + dx + "px," + dy + "px)" }, { transform: "translate(0,0)" }],
        { duration: 180, easing: "cubic-bezier(.2,.8,.2,1)" },
      );
    }
  }
}

function stopCardDrag(restore) {
  if (!DRAG) return;
  const d = DRAG;
  DRAG = null;
  document.removeEventListener("pointermove", moveCardDrag);
  document.removeEventListener("pointerup", endCardDrag);
  document.removeEventListener("pointercancel", cancelCardDrag);
  document.removeEventListener("keydown", dragEsc);
  document.body.classList.remove("assist-dragging");
  if (restore) {
    d.ghost.remove();
    d.placeholder.remove();
    renderTable();
    return;
  }
  const target = d.target;
  if (target === d.index) {
    // 位置没变就不保存，避免 /cards 重置预览导致无意义重抽
    d.ghost.remove();
    d.placeholder.remove();
    renderTable();
    return;
  }
  const finalRect = d.placeholder.getBoundingClientRect();
  d.ghost.style.transition = "left .16s ease, top .16s ease, transform .16s ease";
  d.ghost.style.left = finalRect.left + "px";
  d.ghost.style.top = finalRect.top + "px";
  d.ghost.style.transform = "rotate(0deg) scale(1)";
  setTimeout(() => {
    d.ghost.remove();
    const cards = (ASTATE.cards || []).slice();
    const [moved] = cards.splice(d.index, 1);
    cards.splice(target, 0, moved);
    ASTATE.cards = cards;
    d.placeholder.remove();
    // 先按新顺序重绘，再异步保存；否则会等接口返回前少一张卡
    renderTable();
    saveCards().catch(() => {});
  }, 160);
}

// 落点按“当前已经让过位”的卡片矩形计算，而不是拖拽开始时的旧位置。
// 否则先拖到第一行第一个后，原第一行最后一个已经被挤到第二行，
// 指针再放到第一行末尾时仍按旧坐标算，会让第二行第一个卡片飞上来。
function dragTargetIndex(d, clientX, clientY) {
  const cards = Array.from(d.box.querySelectorAll(".assist-card"));
  let target = 0;
  for (const card of cards) {
    const r = card.getBoundingClientRect();
    if (clientY > r.bottom) {
      target += 1;
      continue;
    }
    if (clientY < r.top) break;
    if (clientX > r.left + r.width / 2) {
      target += 1;
      continue;
    }
    break;
  }
  return target;
}

function moveCardDrag(e) {
  if (!DRAG) return;
  e.preventDefault();
  const d = DRAG;
  d.ghost.style.left = (e.clientX - d.grabX) + "px";
  d.ghost.style.top = (e.clientY - d.grabY) + "px";
  if (e.clientY < 72) window.scrollBy(0, -12);
  else if (e.clientY > window.innerHeight - 72) window.scrollBy(0, 12);

  const target = dragTargetIndex(d, e.clientX, e.clientY);
  if (target === d.target) return;
  const cards = Array.from(d.box.querySelectorAll(".assist-card"));
  const before = cards[target] || null;
  const beforeRects = new Map();
  for (const card of d.box.querySelectorAll(".assist-card")) {
    beforeRects.set(card, { left: card.offsetLeft, top: card.offsetTop });
  }
  d.box.insertBefore(d.placeholder, before);
  d.target = target;
  flipCards(d.box, beforeRects);
}

function endCardDrag(e) {
  if (!DRAG) return;
  e.preventDefault();
  stopCardDrag(false);
}

function cancelCardDrag() {
  stopCardDrag(true);
}

function dragEsc(e) {
  if (e.key === "Escape") stopCardDrag(true);
}

function startCardDrag(e) {
  if (DRAG || ASTATE.running || !e.currentTarget || e.button !== 0 || !e.isPrimary) return;
  const card = e.currentTarget.closest(".assist-card");
  if (!card || !card.dataset.index) return;
  e.preventDefault();
  try {
    if (e.currentTarget.setPointerCapture) e.currentTarget.setPointerCapture(e.pointerId);
  } catch (_) {
    // 某些浏览器/合成事件没有 pointerId，退化为 document 监听即可。
  }
  const box = document.getElementById("assist-table");
  if (!box) return;
  const rect = card.getBoundingClientRect();
  const ghost = card.cloneNode(true);
  ghost.classList.add("assist-card-ghost");
  ghost.style.position = "fixed";
  ghost.style.left = rect.left + "px";
  ghost.style.top = rect.top + "px";
  ghost.style.width = rect.width + "px";
  ghost.style.height = rect.height + "px";
  ghost.style.margin = "0";
  ghost.style.pointerEvents = "none";
  ghost.style.zIndex = "9999";
  document.body.appendChild(ghost);
  const placeholder = document.createElement("div");
  placeholder.className = "assist-placeholder";
  placeholder.style.height = rect.height + "px";
  card.replaceWith(placeholder);
  document.body.classList.add("assist-dragging");
  DRAG = {
    box, ghost, placeholder,
    index: Number(card.dataset.index),
    target: Number(card.dataset.index),
    grabX: e.clientX - rect.left, grabY: e.clientY - rect.top,
  };
  document.addEventListener("pointermove", moveCardDrag, { passive: false });
  document.addEventListener("pointerup", endCardDrag);
  document.addEventListener("pointercancel", cancelCardDrag);
  document.addEventListener("keydown", dragEsc);
}

function initCardSortable(box) {
  if (CARD_SORTABLE || !window.Sortable) return;
  try {
    CARD_SORTABLE = window.Sortable.create(box, {
      animation: 160,
      handle: ".assist-card-title",
      draggable: ".assist-card",
      forceFallback: true,
      fallbackOnBody: true,
      fallbackTolerance: 4,
      ghostClass: "assist-card-source",
      fallbackClass: "assist-drag-image",
      chosenClass: "assist-card-chosen",
      onStart() {
        CARD_SORTING = true;
      },
      onEnd(evt) {
        CARD_SORTING = false;
        const from = evt.oldIndex;
        const to = evt.newIndex;
        if (from == null || to == null || from === to) return;
        const cards = (ASTATE.cards || []).slice();
        const [moved] = cards.splice(from, 1);
        cards.splice(to, 0, moved);
        ASTATE.cards = cards;
        renderTable();
        saveCards().catch(() => {});
      },
    });
  } catch (_) {
    CARD_SORTABLE = null;
  }
}

function renderTable() {
  if (CARD_SORTING) return;
  const box = document.getElementById("assist-table");
  if (!box) return;
  const cards = ASTATE.cards || [];
  box.innerHTML = cards.map((c, i) => {
    const name = POOLS[c.pool] || c.pool;
    return '<div class="assist-card" data-index="' + i + '">'
      + '<div class="assist-card-top"><span class="assist-card-count">' + (c.count == null ? "—" : c.count) + '</span><b class="assist-card-title" data-drag="1" title="按住拖动排序">' + esc(name) + '</b><span>#' + (i + 1) + "</span></div>"
      + '<div class="assist-card-actions"><button class="assist-small" data-act="assist-edit" data-index="' + i + '"' + (ASTATE.running ? " disabled" : "") + '>设置</button>'
      + '<button class="assist-small" data-act="assist-del" data-index="' + i + '"' + (ASTATE.running ? " disabled" : "") + '>删除</button>'
      + (i ? '<button class="assist-small" data-act="assist-left" data-index="' + i + '"' + (ASTATE.running ? " disabled" : "") + '>←</button>' : "")
      + (i + 1 < cards.length ? '<button class="assist-small" data-act="assist-right" data-index="' + i + '"' + (ASTATE.running ? " disabled" : "") + '>→</button>' : "")
      + "</div></div>";
  }).join("");
  const palette = document.getElementById("assist-palette");
  if (palette) {
    palette.innerHTML = Object.keys(POOLS).map((k) =>
      '<button class="assist-pool" data-act="assist-add" data-pool="' + k + '"' + (ASTATE.running ? " disabled" : "") + '>+ ' + POOLS[k] + "</button>").join("");
  }
  if (!CARD_SORTABLE) initCardSortable(box);
  if (CARD_SORTABLE) CARD_SORTABLE.option("disabled", !!ASTATE.running);
}

function renderPreview() {
  const box = document.getElementById("assist-preview");
  if (!box) return;
  const list = ASTATE.preview || [];
  if (!list.length) {
    box.innerHTML = '<p class="hint">还没有预览；先配牌桌。</p>';
    return;
  }
  box.innerHTML = '<div class="assist-preview-slots">' + list.map((p, i) => {
    const actions = (i === 0 && ASTATE.running)
      ? '<button class="assist-small" data-act="assist-repush">重新推送</button>'
        + '<button class="assist-small" data-act="assist-skip" data-index="0">跳过</button>'
      : ((i > 0 || !ASTATE.running)
        ? '<button class="assist-small" data-act="assist-redraw" data-index="' + i + '">重抽</button>'
          + '<button class="assist-small" data-act="assist-skip" data-index="' + i + '">跳过</button>'
        : "");
    return '<div class="assist-preview-slot">'
      + (p.poolLabel ? '<div class="assist-preview-pool">' + esc(p.poolLabel) + '</div>' : "")
      + '<div class="assist-preview-card songcards" data-slot="' + i + '"></div>'
      + '<div class="assist-preview-btns">' + actions + '</div>'
      + '</div>';
  }).join("") + '</div>';
  box.querySelectorAll(".assist-preview-card").forEach((cardBox) => {
    const i = Number(cardBox.dataset.slot);
    const song = list[i] && list[i].song;
    if (song) renderCardPage(cardBox, [song], 1);
    else cardBox.innerHTML = '<p class="hint">这一首暂时没有谱面数据。</p>';
  });
  // 卡片区可能在页面隐藏时渲染，clientWidth 为 0；等一帧后重新拟合，避免卡片按默认尺寸溢出。
  requestAnimationFrame(() => {
    box.querySelectorAll(".assist-preview-card").forEach((cardBox) => fitGrid(cardBox, 1));
  });
}

function fitPreviewCards() {
  document.querySelectorAll("#assist-preview .assist-preview-card").forEach((box) => fitGrid(box, 1));
}

window.addEventListener("resize", fitPreviewCards);

if (window.ResizeObserver) {
  const previewBox = document.getElementById("assist-preview");
  if (previewBox) {
    new ResizeObserver(() => fitPreviewCards()).observe(previewBox);
  }
}

function renderStatus() {
  const el = document.getElementById("assist-status");
  if (el) {
    el.innerHTML = '<span class="dot ' + (ASTATE.running ? "ok" : "") + '"></span>'
      + (ASTATE.running ? "脚本运行中" : "脚本未运行");
  }
  const start = document.getElementById("assist-start");
  const stop = document.getElementById("assist-stop");
  if (start) start.disabled = !!ASTATE.running || !(ASTATE.cards || []).length;
  if (stop) stop.disabled = !ASTATE.running;
  document.querySelectorAll('.tab[data-page="db"], .tab[data-page="scores"]').forEach((tab) => {
    tab.disabled = !!ASTATE.running;
  });
  const hist = document.getElementById("assist-history");
  if (hist) {
    hist.innerHTML = (ASTATE.logs || []).map((x) =>
      "<div>" + esc(x.mtime) + " · " + esc(x.name) + "</div>").join("") || "暂无历史日志";
  }
}

function renderSettings() {
  const s = ASTATE.settings || {};
  const host = document.getElementById("assist-host");
  const mp = document.getElementById("assist-mod-port");
  const cp = document.getElementById("assist-callback-port");
  const mm = document.getElementById("assist-miss-mode");
  if (host && document.activeElement !== host) host.value = s.host || "";
  if (mp && document.activeElement !== mp) mp.value = s.modPort || 8790;
  if (cp && document.activeElement !== cp) cp.value = s.callbackPort || 20010;
  if (mm && document.activeElement !== mm) mm.value = s.missMode || "wait";
}

function renderLog(forceBottom) {
  const el = document.getElementById("assist-log");
  if (!el) return;
  const text = ASTATE.log || "";
  if (el.textContent === text) return;
  const wasAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  el.textContent = text;
  // 轮询会反复重写 textContent；只有原本就在底部（或调用方明确要求）才自动跟到最新一行，
  // 这样既不会停止滚动，也不会把正在往上翻日志的人硬拽回底部。
  if (forceBottom || wasAtBottom) el.scrollTop = el.scrollHeight;
}

async function saveCards() {
  if (!ASTATE || !currentUser) return;
  try {
    const data = await api("/api/assistant/cards", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: currentUser, cards: ASTATE.cards }),
    });
    if (data.error) throw new Error(data.error);
    ASTATE = { ...data, log: ASTATE.log, settings: ASTATE.settings };
    renderAll();
  } catch (err) {
    renderTable();
    alert("保存牌桌失败：" + err);
  }
}

async function openSettings(index) {
  const card = ASTATE.cards[index];
  if (!card) return;
  SETTINGS_INDEX = index;
  // 用户可能刚完成建库就打开设置；这里再刷新一次，避免继续用页面加载时的空列表。
  await loadPlates();
  const dlg = document.getElementById("assistdlg");
  const form = document.getElementById("assist-form");
  const s = card.settings || {};
  const normal = card.pool !== "underdog"
    ? '<label class="sm-line crit-line"><span>个人拟合</span><select id="assist-fit">'
      + '<option value="none">无</option><option value="high">高难度优先</option><option value="low">低难度优先</option></select></label>'
      + '<label class="sm-line crit-line"><span>成绩</span><select id="assist-ach">'
      + '<option value="none">无</option><option value="high">高达成率优先</option><option value="low">低达成率优先</option></select></label>'
    : '<label class="sm-line crit-line"><span>官方定数下限</span><input id="assist-lmin" type="number" step="0.1" placeholder="不限"></label>'
      + '<label class="sm-line crit-line"><span>官方定数上限</span><input id="assist-lmax" type="number" step="0.1" placeholder="不限"></label>'
      + '<label class="sm-line crit-line"><span>成绩&lt;</span><input id="assist-achmax" type="number" step="0.1" placeholder="不限"></label>'
      + '<label class="sm-line crit-line"><span>连击 &lt;</span><select id="assist-combo">'
      + '<option value="">任意</option><option value="1">FC</option><option value="2">FC+</option><option value="3">AP</option><option value="4">AP+</option></select></label>'
      + '<label class="sm-line crit-line"><span>双人 &lt;</span><select id="assist-sync">'
      + '<option value="">任意</option><option value="1">FS</option><option value="2">FS+</option><option value="3">FSD</option><option value="4">FSD+</option><option value="5">SP</option></select></label>'
      + '<div class="sm-line crit-line"><span>难度</span><div id="assist-diffs" class="assist-diffs"></div></div>'
      + '<label class="sm-line crit-line"><span>难度权重</span><select id="assist-under-fit">'
      + '<option value="none">无</option><option value="high">高难度优先</option><option value="low">低难度优先</option></select></label>'
      + '<label class="sm-line crit-line"><span>未游玩歌曲</span><select id="assist-unplayed">'
      + '<option value="0">包含</option><option value="1">不包含</option></select></label>'
      + '<div class="sm-line crit-line"><span>版本</span><button type="button" id="assist-version-pick" class="assist-pick">不限</button></div>'
      + '<div class="sm-line crit-line"><span>牌子</span><button type="button" id="assist-plate-pick" class="assist-pick">不限</button></div>';
  form.innerHTML = '<p class="hint">' + POOLS[card.pool] + "</p>" + normal
    + '<div class="row"><button class="primary" id="assist-save-settings">保存</button><button id="assist-cancel-settings">取消</button></div>';
  if (normal.indexOf("assist-fit") >= 0) {
    document.getElementById("assist-fit").value = s.fitPriority || "none";
    document.getElementById("assist-ach").value = s.achPriority || "none";
  } else {
    document.getElementById("assist-lmin").value = s.levelMin ?? "";
    document.getElementById("assist-lmax").value = s.levelMax ?? "";
    document.getElementById("assist-achmax").value = s.achMax ?? "";
    document.getElementById("assist-combo").value = s.comboMax ?? "";
    document.getElementById("assist-sync").value = s.syncMax ?? "";
    const versionPick = document.getElementById("assist-version-pick");
    const platePick = document.getElementById("assist-plate-pick");
    const validVersion = (ASTATE.versions || []).includes(s.version) ? s.version : "";
    const validPlate = (PLATES || []).find((p) => p.id === s.plateId) || null;
    versionPick.dataset.value = validVersion;
    versionPick.textContent = validVersion || "不限";
    platePick.dataset.value = validPlate ? validPlate.id : "";
    platePick.textContent = validPlate ? validPlate.name : "不限";
    versionPick.onclick = () => openSongFilterPicker({
      kind: "version", versions: ASTATE.versions || [], plates: PLATES || [],
      current: versionPick.dataset.value || "",
      onPick: (value, name) => {
        versionPick.dataset.value = value || "";
        versionPick.textContent = name || "不限";
      },
    });
    platePick.onclick = () => openSongFilterPicker({
      kind: "plate", versions: ASTATE.versions || [], plates: PLATES || [],
      current: platePick.dataset.value || "",
      onPick: (value, name) => {
        platePick.dataset.value = value || "";
        platePick.textContent = name || "不限";
      },
    });
    document.getElementById("assist-unplayed").value = s.excludeUnplayed ? "1" : "0";
    document.getElementById("assist-under-fit").value = s.fitPriority || "none";
    const diffsBox = document.getElementById("assist-diffs");
    diffsBox.innerHTML = DIFFS.map(([k, label]) =>
      '<button type="button" class="assist-diff d-' + k + ((s.difficulties || []).includes(k) ? " on" : "") + '" data-diff="' + k + '">' + label + '</button>').join("");
    diffsBox.onclick = (e) => {
      const btn = e.target.closest("button[data-diff]");
      if (btn) btn.classList.toggle("on");
    };
  }
  document.getElementById("assist-save-settings").onclick = async () => {
    if (normal.indexOf("assist-fit") >= 0) {
      card.settings = {
        fitPriority: document.getElementById("assist-fit").value,
        achPriority: document.getElementById("assist-ach").value,
      };
    } else {
      card.settings = {
        levelMin: document.getElementById("assist-lmin").value || null,
        levelMax: document.getElementById("assist-lmax").value || null,
        achMax: document.getElementById("assist-achmax").value || null,
        comboMax: document.getElementById("assist-combo").value || null,
        syncMax: document.getElementById("assist-sync").value || null,
        fitPriority: document.getElementById("assist-under-fit").value,
        version: document.getElementById("assist-version-pick").dataset.value || "",
        plateId: document.getElementById("assist-plate-pick").dataset.value || "",
        excludeUnplayed: document.getElementById("assist-unplayed").value === "1",
        difficulties: [...document.querySelectorAll("#assist-diffs .assist-diff.on")].map((x) => x.dataset.diff),
      };
    }
    dlg.hidden = true;
    await saveCards();
  };
  dlg.hidden = false;
}

async function act(action, body = {}) {
  if (!currentUser) return;
  try {
    const data = await api("/api/assistant/" + action, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: currentUser, ...body }),
    });
    if (data.error) throw new Error(data.error);
    if (data.bridgeOk === false) {
      alert("预览已跳过，但 bridge 没有收到跳过信号：" + (data.bridgeError || "未知错误"));
    }
    if (data.cards || data.preview) ASTATE = { ...ASTATE, ...data, log: data.log || ASTATE.log };
    renderAll();
    if (action === "redraw" && data.empty && !body.force) {
      if (confirm("该卡池已空，是否跳过该卡池并顺延？")) {
        return act("redraw", { ...body, force: true });
      }
    }
    return data;
  } catch (err) {
    if (action === "start") {
      ASTATE.log = (ASTATE.log || "") + "\n启动失败：" + err;
      renderLog();
    } else {
      alert("操作失败：" + err);
    }
  }
}

function startPoll() {
  if (POLL) return;
  POLL = setInterval(async () => {
    if (POLL_BUSY || !currentUser || document.querySelector('[data-page="assistant"]').hidden) return;
    POLL_BUSY = true;
    try {
      const data = await api("/api/assistant/log?user=" + encodeURIComponent(currentUser));
      if (data.log != null) {
        ASTATE.log = data.log;
        const was = ASTATE.running;
        ASTATE.running = !!data.running;
        renderStatus();
        renderRating();
        renderLog();
        if (was !== ASTATE.running) renderTable();
      }
      // 存档由 bridge 轮询 mod /save 后同步；运行时轮询最新状态。
      // 预览变化立即拉带数量的状态；平时每 5 个 tick 刷新一次卡池数量。
      if (data.running) {
        const state = await api("/api/assistant/state?user=" + encodeURIComponent(currentUser));
        const sig = JSON.stringify(state.preview || []);
        POLL_TICK += 1;
        if (sig !== POLL_PREVIEW || POLL_TICK % 5 === 0) {
          POLL_PREVIEW = sig;
          const detailed = await api("/api/assistant/state?user=" + encodeURIComponent(currentUser) + "&counts=1");
          ASTATE = { ...ASTATE, ...detailed, log: ASTATE.log };
          renderTable();
          renderPreview();
          renderRating();
          renderStatus();
        }
      }
    } catch (_) { /* ignore */ }
    finally { POLL_BUSY = false; }
  }, 1200);
}

function bind() {
  const saveSettingsBtn = document.getElementById("assist-save-global");
  if (saveSettingsBtn) {
    saveSettingsBtn.onclick = async () => {
      saveSettingsBtn.disabled = true;
      await saveGlobalSettings();
      saveSettingsBtn.disabled = false;
    };
  }
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const actName = btn.dataset.act;
    if (actName === "assist-add") {
      if ((ASTATE.cards || []).length >= 12) return;
      const pool = btn.dataset.pool;
      const src = (ASTATE.cards || []).find((c) => c.pool === pool);
      let settings = {};
      if (src && src.settings) settings = JSON.parse(JSON.stringify(src.settings));
      ASTATE.cards = [...(ASTATE.cards || []), { pool, settings }];
      await saveCards();
      openSettings(ASTATE.cards.length - 1);
    } else if (actName === "assist-del") {
      ASTATE.cards.splice(Number(btn.dataset.index), 1);
      await saveCards();
    } else if (actName === "assist-left" || actName === "assist-right") {
      const i = Number(btn.dataset.index);
      const cards = ASTATE.cards.slice();
      const [card] = cards.splice(i, 1);
      cards.splice(i + (actName === "assist-left" ? -1 : 1), 0, card);
      ASTATE.cards = cards;
      await saveCards();
    } else if (actName === "assist-edit") {
      openSettings(Number(btn.dataset.index));
    } else if (actName === "assist-redraw" || actName === "assist-skip") {
      await act(actName.replace("assist-", ""), { index: Number(btn.dataset.index) });
    } else if (actName === "assist-repush") {
      await act("repush");
    } else if (actName === "assist-start") {
      if (await saveGlobalSettings()) await act("start");
    } else if (actName === "assist-stop") {
      await act("stop");
    }
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("#assist-cancel-settings") || e.target.id === "assistdlg") {
      const d = document.getElementById("assistdlg");
      if (d) d.hidden = true;
    }
  });
  const tab = document.querySelector('.tab[data-page="assistant"]');
  if (tab) tab.addEventListener("click", () => {
    if (currentUser) loadAssistant();
    else renderEmpty("先选择一个用户。");
  });
  if (currentPage() && !currentPage().hidden && currentUser) loadAssistant();
}

async function saveGlobalSettings() {
  const host = document.getElementById("assist-host").value.trim();
  const modPort = Number(document.getElementById("assist-mod-port").value) || 8790;
  const callbackPort = Number(document.getElementById("assist-callback-port").value) || 20010;
  const missMode = document.getElementById("assist-miss-mode").value || "wait";
  const note = document.getElementById("assist-settings-note");
  if (note) note.textContent = "保存中…";
  try {
    const data = await api("/api/assistant/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, modPort, callbackPort, missMode }),
    });
    if (ASTATE) ASTATE.settings = data.settings;
    if (note) note.textContent = "设置已保存";
    return true;
  } catch (err) {
    if (note) note.textContent = "保存失败：" + err.message;
    return false;
  }
}

async function loadPlates() {
  try {
    const data = await api("/api/plates");
    PLATES = data.plates || [];
  } catch (_) { PLATES = []; }
}

document.addEventListener("DOMContentLoaded", async () => {
  bind();
  await loadPlates();
  if (currentPage() && !currentPage().hidden && currentUser) loadAssistant();
});

window.addEventListener("assist:user", () => {
  if (currentPage() && !currentPage().hidden) loadAssistant();
});
