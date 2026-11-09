import { $, api, chain, config, loadUsers, usersById , setNote } from "./core.js";
import { buildChainNames, refreshAlsButton, refreshBuildButton, renderChains, renderRun, showPage, smallChainName, startRun, watch } from "./db.js";
import { askDeleteUser, deleteMode, importScores, setBigState } from "./songlist.js";

// 用途：成绩页：用户卡、头像、版本/rating 绘制、统计（从 app.js 按领域拆出，classic script，index.html 按顺序加载）
// 依赖：全局函数/变量在调用时解析，不要求定义顺序

/* 旧「成绩导入」块（下拉+选文件+导入按钮）已删：导入改到大卡上（importScores） */

/* ── 头像：1:1 + 转 PNG + 缩到 256×256（裁剪在浏览器 canvas 里做） ── */
const AVATAR_SIDE = 256;
function stageSize() { const el = $("crop-stage"); return (el && el.clientWidth) || 520; }
const crop = {img: null, w: 0, h: 0, scale: 1, x: 0, y: 0, dragging: null};

function cropClamp() {
  crop.x = Math.min(0, Math.max(stageSize() - crop.w * crop.scale, crop.x));
  crop.y = Math.min(0, Math.max(stageSize() - crop.h * crop.scale, crop.y));
}

function cropRender() {
  const el = $("crop-img");
  el.style.width = crop.w * crop.scale + "px";
  el.style.height = crop.h * crop.scale + "px";
  el.style.left = crop.x + "px";
  el.style.top = crop.y + "px";
}

function cropZoom(percent) {
  const base = Math.max(stageSize() / crop.w, stageSize() / crop.h);
  const next = base * (percent / 100);
  const cx = (stageSize() / 2 - crop.x) / crop.scale;
  const cy = (stageSize() / 2 - crop.y) / crop.scale;
  crop.scale = next;
  crop.x = stageSize() / 2 - cx * next;
  crop.y = stageSize() / 2 - cy * next;
  cropClamp();
  cropRender();
}

function openCrop(dataUrl) {
  const img = new Image();
  img.onload = () => {
    crop.img = img;
    crop.w = img.naturalWidth;
    crop.h = img.naturalHeight;
    crop.scale = Math.max(stageSize() / crop.w, stageSize() / crop.h);
    crop.x = (stageSize() - crop.w * crop.scale) / 2;
    crop.y = (stageSize() - crop.h * crop.scale) / 2;
    $("crop-img").src = dataUrl;
    $("crop-zoom").value = 100;
    setNote("crop-note", `原图 ${crop.w}×${crop.h}`);
    $("cropbox").hidden = false;
    cropRender();
  };
  img.onerror = () => { setNote("view-avatar-note", "这张图读不出来，换一张吧。"); };
  img.src = dataUrl;
}

function cropToPng() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = AVATAR_SIDE;
  const size = stageSize() / crop.scale;
  canvas.getContext("2d").drawImage(crop.img, -crop.x / crop.scale, -crop.y / crop.scale,
                                    size, size, 0, 0, AVATAR_SIDE, AVATAR_SIDE);
  return canvas.toDataURL("image/png");
}

function squareToPng(img) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = AVATAR_SIDE;
  canvas.getContext("2d").drawImage(img, 0, 0, AVATAR_SIDE, AVATAR_SIDE);
  return canvas.toDataURL("image/png");
}


if ($("cropbox")) {
  $("crop-stage").addEventListener("pointerdown", (e) => {
    crop.dragging = {x: e.clientX, y: e.clientY, ox: crop.x, oy: crop.y};
    $("crop-stage").setPointerCapture(e.pointerId);
  });
  $("crop-stage").addEventListener("pointermove", (e) => {
    if (!crop.dragging) return;
    crop.x = crop.dragging.ox + (e.clientX - crop.dragging.x);
    crop.y = crop.dragging.oy + (e.clientY - crop.dragging.y);
    cropClamp();
    cropRender();
  });
  $("crop-stage").addEventListener("pointerup", () => { crop.dragging = null; });
  $("crop-stage").addEventListener("wheel", (e) => {
    if (!crop.img) return;
    e.preventDefault();
    const next = Math.min(400, Math.max(100, Number($("crop-zoom").value) + (e.deltaY < 0 ? 10 : -10)));
    $("crop-zoom").value = next;
    cropZoom(next);
  }, { passive: false });
  $("crop-stage").addEventListener("pointercancel", () => { crop.dragging = null; });
  $("crop-zoom").oninput = () => cropZoom(Number($("crop-zoom").value));
  $("crop-ok").onclick = async () => {
    $("crop-ok").disabled = true;
    await saveAvatar(cropToPng());
    $("crop-ok").disabled = false;
    $("cropbox").hidden = true;
  };
  $("crop-cancel").onclick = () => {
    $("cropbox").hidden = true;
    setNote("view-avatar-note", "已取消");
  };
}
function refreshSmallButton() {
  $("small-build").disabled = !$("small").value;
}

$("pick-small").onclick = async () => {
  setNote("small-note", "等待选取中…");
  try {
    const data = await api("/api/pick-folder", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({mode: "package"}),
    });
    $("small").value = data.path || "";
    setNote("small-note", data.path
      ? (data.warning || data.note || "完成") : (data.note || "已取消"));
  } catch (err) {
    setNote("small-note", "选目录失败：" + err.message);
  }
  refreshSmallButton();
};



/* ── 成绩页 v2：用户卡片 + 精简统计 + 游戏贴图 ──
   本段函数名与上面同名 → 后定义的生效（覆盖旧的成绩页实现）。 */
const RANK_IMG = {S: "UI_GAM_Rank_S.png", SS: "UI_GAM_Rank_SS.png", "SS+": "UI_GAM_Rank_SSp.png",
                  SSS: "UI_GAM_Rank_SSS.png", "SSS+": "UI_GAM_Rank_SSSp.png"};
const VERSION_IMAGES = {
  "maimai": "Ver100", "maimai PLUS": "Ver110",
  "GreeN": "Ver120", "GreeN PLUS": "Ver130",
  "ORANGE": "Ver140", "ORANGE PLUS": "Ver150",
  "PiNK": "Ver160", "PiNK PLUS": "Ver170",
  "MURASAKi": "Ver180", "MURASAKi PLUS": "Ver185",
  "MiLK": "Ver190", "MiLK PLUS": "Ver195",
  "FiNALE": "Ver199", "maimaiでらっくす": "Ver200", "maimaiでらっくす PLUS": "Ver210",
  "Splash": "Ver214", "Splash PLUS": "Ver215", "UNiVERSE": "Ver220", "UNiVERSE PLUS": "Ver225",
  "FESTiVAL": "Ver230", "FESTiVAL PLUS": "Ver235", "BUDDiES": "Ver240", "BUDDiES PLUS": "Ver245",
  "PRiSM": "Ver250", "PRiSM PLUS": "Ver255", "CiRCLE": "Ver260", "CiRCLE PLUS": "Ver265",
  "MAGiCAL": "Ver270",
  "DX": "Ver200", "DX PLUS": "Ver210",     // 曲库 canonical 写法；老 key 是兼容旧数据
};
const VERSION_ALIASES = {
  "maimaiでらっくす": "DX",
  "maimaiでらっくす PLUS": "DX PLUS",
  "maimaiでらっくすPLUS": "DX PLUS",
  "maimaDX": "DX",
  "maimaDXPLUS": "DX PLUS",
  "maimai DX": "DX",
  "maimai DX PLUS": "DX PLUS",
  "CiRCLEPLUS": "CiRCLE PLUS",
};
function canonicalVersion(name) {
  const value = String(name == null ? "" : name).trim();
  return VERSION_ALIASES[value] || value;
}
let currentUser = null;
let statsToken = 0;
let ratingToken = 0;
let versionBusy = false;
let statsUser = null;
let statsLoadingTimer = null;     // 切用户/重复进入时丢弃旧请求

function fmtTime(iso) {
  if (!iso) return "—";
  return String(iso).replace("T", " ").slice(0, 16);
}

function userList() {
  // 顺序由 users.json 维护；默认“第一个”就是这里的第一个
  return Object.values(usersById);
}

let avatarVersion = 0;                       // 每次读用户表 +1，头像地址跟着变，避免拿缓存

function avatarSrc(user) {
  if (!user || !user.avatar) return "";
  return `/api/avatar?user=${encodeURIComponent(user.id)}&v=${avatarVersion}`;
}


async function saveAvatar(png) {                 // 覆盖旧版：用 currentUser
  try {
    const data = await api("/api/user-avatar-save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user: currentUser, image: png}),
    });
    setNote("view-avatar-note", data.info || "完成");
    await loadUsers();
    avatarVersion += 1;                      // 换完立刻显示新图（地址变了才不会拿缓存）
    renderUserCards();
    const me = currentUser ? usersById[currentUser] : null;
    if (me) {
      const img = $("now-avatar");
      const src = avatarSrc(me);
      if (img && src) { img.src = src; img.hidden = false; }
    }
  } catch (err) {
    setNote("view-avatar-note", "保存失败：" + err.message);
  }
}

if ($("user-cards")) {
  // 新建用户：弹窗里填名字 → /api/user-create
  $("view-new").onclick = () => {
    const dlg = document.getElementById("newmodal");
    const inp = document.getElementById("new-name");
    if (!dlg || !inp) return;
    inp.value = "";
    dlg.hidden = false;
    inp.focus();
  };
  $("view-sync").onclick = async () => {
    const btn = $("view-sync");
    const note = $("view-new-note");
    if (btn) btn.disabled = true;
    if (note) note.textContent = "正在停止推歌脚本并从机台拉取存档…";
    try {
      const data = await api("/api/save/sync", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({}),
      });
      await loadUsers();
      const id = data.user && data.user.id;
      if (id) {
        currentUser = id;
        renderUserCards();
        selectUser(id);
      }
      let msg = `已同步「${data.user && data.user.name || ""}」：${data.count || 0} 条成绩`;
      if (data.stopped) msg = "推歌脚本已停止；" + msg;
      if (note) note.textContent = data.relatedError ? `${msg}；分析：${data.relatedError}` : msg;
    } catch (err) {
      if (note) note.textContent = "同步失败：" + err.message;
    } finally {
      if (btn) btn.disabled = false;
    }
  };
  $("new-cancel").onclick = () => { const d = document.getElementById("newmodal"); if (d) d.hidden = true; };
  $("new-ok").onclick = async () => {
    const dlg = document.getElementById("newmodal");
    const inp = document.getElementById("new-name");
    const note = document.getElementById("new-note");
    const name = ((inp && inp.value) || "").trim();
    if (!name) { if (note) note.textContent = "先填个名字。"; return; }
    try {
      const data = await api("/api/user-create", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name}),
      });
      if (dlg) dlg.hidden = true;
      const created = data.user && data.user.id;
      await loadUsers();
      if (created) { currentUser = created; renderUserCards(); selectUser(created); }
    } catch (err) {
      if (note) note.textContent = "新建失败：" + err.message;
    }
  };
  document.getElementById("newmodal").addEventListener("click", (e) => {
    if (e.target && e.target.id === "newmodal") e.target.hidden = true;
  });
  $("view-name-edit").onclick = () => {
    if (!currentUser) { setNote("view-avatar-note", "先选一个用户。"); return; }
    const me = usersById[currentUser] || {};
    const dlg = document.getElementById("namedlg");
    const inp = document.getElementById("name-input");
    if (!dlg || !inp) return;
    inp.value = me.name || "";
    dlg.hidden = false;
    inp.focus();
    inp.select();
  };
  $("name-cancel").onclick = () => { const d = document.getElementById("namedlg"); if (d) d.hidden = true; };
  $("name-ok").onclick = async () => {
    const dlg = document.getElementById("namedlg");
    const inp = document.getElementById("name-input");
    const name = ((inp && inp.value) || "").trim();
    if (!name || !currentUser) return;
    try {
      await api("/api/user-rename", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({user: currentUser, name}),
      });
      if (dlg) dlg.hidden = true;
      await loadUsers();
      renderUserCards();
      selectUser(currentUser);
      setNote("view-avatar-note", "用户名已改为「" + name + "」");
    } catch (err) {
      setNote("view-avatar-note", "改名失败：" + err.message);
    }
  };
  document.getElementById("namedlg").addEventListener("click", (e) => {
    if (e.target && e.target.id === "namedlg") e.target.hidden = true;
  });
  $("view-avatar-pick").onclick = async () => {
    if (!currentUser) { setNote("view-avatar-note", "先选一个用户。"); return; }
    setNote("view-avatar-note", "等待选取中…");
    let data;
    try {
      data = await api("/api/user-avatar", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({user: currentUser}),
      });
    } catch (err) {
      setNote("view-avatar-note", "选图失败：" + err.message);
      return;
    }
    const img = new Image();
    img.onload = async () => {
      setNote("view-avatar-note", "完成");
      if (img.naturalWidth === img.naturalHeight) await saveAvatar(squareToPng(img));
      else openCrop("/api/avatar-source?token=" + encodeURIComponent(data.token));
    };
    img.onerror = () => { setNote("view-avatar-note", "这张图读不出来，换一张吧。"); };
    img.src = "/api/avatar-source?token=" + encodeURIComponent(data.token);
  };
}

/* ── 成绩页 v3：大卡在上（头像/rating/版本/各档计数/rank/统计于），小卡在下 ── */
let USER_DRAG = null;
let USER_DRAG_SUPPRESS = false;

function userFlip(box, before) {
  for (const el of box.querySelectorAll(".usercard")) {
    if (el.classList.contains("assist-placeholder")) continue;
    const old = before.get(el);
    if (!old) continue;
    const dx = old.left - el.offsetLeft, dy = old.top - el.offsetTop;
    if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
      const lift = el.classList.contains("selected") ? -10 : 0;
      el.animate(
        [{ transform: "translate(" + dx + "px," + (dy + lift) + "px)" },
         { transform: "translateY(" + lift + "px)" }],
        { duration: 180, easing: "cubic-bezier(.2,.8,.2,1)" },
      );
    }
  }
}

function cleanupUserDrag() {
  document.removeEventListener("pointermove", moveUserDrag);
  document.removeEventListener("pointerup", endUserDrag);
  document.removeEventListener("pointercancel", cancelUserDrag);
  document.removeEventListener("keydown", userDragEsc);
  document.body.classList.remove("assist-dragging");
}

function stopUserDrag(restore) {
  if (!USER_DRAG) return;
  const d = USER_DRAG;
  USER_DRAG = null;
  cleanupUserDrag();
  if (d.pending) {
    USER_DRAG_SUPPRESS = false;
    return;
  }
  if (restore) {
    d.ghost.remove();
    d.placeholder.remove();
    USER_DRAG_SUPPRESS = false;
    renderUserCards();
    return;
  }
  const children = Array.from(d.box.children);
  const pos = children.indexOf(d.placeholder);
  let target = 0;
  for (let i = 0; i < pos; i++) if (children[i].classList.contains("usercard")) target++;
  const ids = userList().map((u) => u.id);
  const [moved] = ids.splice(d.index, 1);
  ids.splice(target, 0, moved);
  const finalRect = d.placeholder.getBoundingClientRect();
  d.ghost.style.transition = "left .16s ease, top .16s ease, transform .16s ease";
  d.ghost.style.left = finalRect.left + "px";
  d.ghost.style.top = finalRect.top + "px";
  d.ghost.style.transform = "translateY(0)";
  setTimeout(() => {
    d.ghost.remove();
    const ordered = ids.map((id) => usersById[id]).filter(Boolean);
    for (const key of Object.keys(usersById)) delete usersById[key];
    for (const user of ordered) usersById[user.id] = user;
    d.placeholder.remove();
    renderUserCards();
    saveUserOrder(ids);
    setTimeout(() => { USER_DRAG_SUPPRESS = false; }, 120);
  }, 160);
}

function beginUserDrag(e, d) {
  const card = d.card;
  const rect = card.getBoundingClientRect();
  const ghost = card.cloneNode(true);
  ghost.classList.remove("selected");
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
  placeholder.style.width = rect.width + "px";
  placeholder.style.height = rect.height + "px";
  placeholder.style.flex = "0 0 auto";
  card.replaceWith(placeholder);
  document.body.classList.add("assist-dragging");
  d.pending = false;
  d.ghost = ghost;
  d.placeholder = placeholder;
  d.grabX = e.clientX - rect.left;
  d.grabY = e.clientY - rect.top;
  d.lastPlacement = null;
  return true;
}

function moveUserDrag(e) {
  if (!USER_DRAG) return;
  const d = USER_DRAG;
  if (d.pending) {
    if (Math.abs(e.clientX - d.startX) + Math.abs(e.clientY - d.startY) <= 4) return;
    if (!beginUserDrag(e, d)) { stopUserDrag(true); return; }
  }
  e.preventDefault();
  USER_DRAG_SUPPRESS = true;
  d.ghost.style.left = (e.clientX - d.grabX) + "px";
  d.ghost.style.top = (e.clientY - d.grabY) + "px";
  const br = d.box.getBoundingClientRect();
  if (e.clientX < br.left + 48) d.box.scrollLeft -= 12;
  else if (e.clientX > br.right - 48) d.box.scrollLeft += 12;
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const over = el && el.closest ? el.closest(".usercard") : null;
  if (!over) return;
  const rect = over.getBoundingClientRect();
  const after = e.clientX > rect.left + rect.width / 2;
  const placement = over.dataset.id + (after ? "a" : "b");
  if (placement === d.lastPlacement) return;
  const before = new Map();
  for (const card of d.box.querySelectorAll(".usercard")) before.set(card, { left: card.offsetLeft, top: card.offsetTop });
  if (after) over.after(d.placeholder);
  else over.before(d.placeholder);
  d.lastPlacement = placement;
  userFlip(d.box, before);
}

function endUserDrag(e) {
  if (!USER_DRAG) return;
  if (!USER_DRAG.pending) e.preventDefault();
  stopUserDrag(false);
}

function cancelUserDrag() { stopUserDrag(true); }

function userDragEsc(e) {
  if (e.key === "Escape") stopUserDrag(true);
}

function startUserDrag(e) {
  if (deleteMode || USER_DRAG || e.button !== 0 || !e.isPrimary) return;
  const card = e.currentTarget.closest(".usercard");
  const box = $("user-cards");
  if (!card || !box) return;
  const index = userList().findIndex((u) => u.id === card.dataset.id);
  if (index < 0) return;
  USER_DRAG = {
    pending: true, card, box, index,
    startX: e.clientX, startY: e.clientY,
  };
  document.addEventListener("pointermove", moveUserDrag, { passive: false });
  document.addEventListener("pointerup", endUserDrag);
  document.addEventListener("pointercancel", cancelUserDrag);
  document.addEventListener("keydown", userDragEsc);
}

async function saveUserOrder(ids) {
  try {
    const data = await api("/api/user-order", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order: ids }),
    });
    if (data.error) throw new Error(data.error);
  } catch (err) {
    await loadUsers();
    renderUserCards();
    setNote("view-avatar-note", "保存用户顺序失败：" + err.message);
  }
}

let currentVersion = null;

function renderUserCards() {
  const box = $("user-cards");
  if (!box) return;
  box.innerHTML = "";
  for (const user of userList()) {
    const s = user.summary || {};
    const card = document.createElement("div");
    card.className = "usercard" + (user.id === currentUser ? " selected" : "") + (deleteMode ? " delmode" : "");
    card.dataset.id = user.id;
    const src = avatarSrc(user);
    const handle = src
      ? `<img class="ucavatar" src="${src}" alt="" draggable="false" title="按住头像拖动排序">`
      : `<div class="ucavatar" title="按住头像拖动排序"></div>`;
    const head = handle + `<div class="ucname">${user.name || user.id}</div>`;
    if (deleteMode) {                            // v26：删除模式下不显示统计，换成删除按钮
      card.innerHTML = head + `<button class="ucdel" type="button">删除</button>`;
      card.querySelector(".ucdel").onclick = (e) => { e.stopPropagation(); askDeleteUser(user); };
    } else {
      card.innerHTML = head +
        `<div class="ucmeta">rating ${s.rating ?? "—"}<br>FC ${s.fullCombo ?? "—"} · AP ${s.allPerfect ?? "—"}` +
        `<br>${fmtTime(user.updatedAt)}</div>`;
      card.onclick = () => { if (!USER_DRAG_SUPPRESS) selectUser(user.id); };
    }
    box.append(card);
    if (!deleteMode) {
      const av = card.querySelector(".ucavatar");
      if (av) av.addEventListener("pointerdown", startUserDrag);
    }
  }
}

/* ── 成绩页 v4：selectUser / renderAvatar / loadStats 重写（不再引用已删掉的元素） ── */
function selectUser(id) {
  currentUser = id;
  ratingToken += 1;
  clearRating();
  setVersionBusy(false);
  document.querySelectorAll(".usercard").forEach((c) => c.classList.toggle("selected", c.dataset.id === id));
  const user = usersById[id];
  const now = $("view-now");
  if (user && now) {
    now.hidden = false;
    $("now-name").textContent = user.name || id;
    const img = $("now-avatar");
    const src = avatarSrc(user);
    if (src) { img.src = src; img.hidden = false; } else { img.removeAttribute("src"); img.hidden = true; }
  }
  loadStats();
}

function renderAvatar() {
  renderUserCards();
  const first = userList()[0];
  if (!first) {                                   // 用户删光了：大卡和占位都收起来
    const now = $("view-now"), box = $("view-loading");
    if (now) now.hidden = true; if (box) box.hidden = true;
    return;
  }   // 用户删光了：大卡收起来
  if (!currentUser || !usersById[currentUser]) { selectUser(first.id); return; }
  loadStats();
}

function clearRating() {
  const canvas = $("rating-img");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
}

function setVersionBusy(on) {
  versionBusy = on;
  const btn = $("version-btn");
  if (btn) btn.disabled = on || !versionList.length;
  const grid = $("ver-grid");
  if (grid) grid.querySelectorAll("button").forEach((b) => { b.disabled = on; });
}

async function loadDxRating() {
  const user = currentUser;
  const version = canonicalVersion(currentVersion);
  const token = (ratingToken += 1);
  if (!user || !version) {
    clearRating();
    setVersionBusy(false);
    return;
  }
  clearRating();
  setVersionBusy(true);
  try {
    const data = await api("/api/scores/dxrating", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user, version}),
    });
    if (token !== ratingToken || user !== currentUser || version !== canonicalVersion(currentVersion)) return;
    const got = canonicalVersion(data.version || "");
    if (typeof data.dxRating !== "number" || got !== version) {
      clearRating();
      setNote("view-avatar-note", "rating 计算失败：返回版本不一致或没有数值");
      return;
    }
    drawRating(data.dxRating, token);
  } catch (err) {
    if (token !== ratingToken) return;
    clearRating();
    setNote("view-avatar-note", "rating 计算失败：" + err.message);
  } finally {
    if (token === ratingToken) setVersionBusy(false);
  }
}

async function loadStats() {
  const at = $("view-stats-at");
  const token = (statsToken = (statsToken || 0) + 1);                     // 切用户/重复进入时，旧的请求结果直接丢弃
  if (!currentUser) { setBigState("loading", "请选择用户。"); if (at) at.textContent = ""; return; }
  // 加载动画延迟出现：服务端命中「数据没变」时几十毫秒就回来，不闪；真的在算才显示
  const token2 = token;
  clearTimeout(statsLoadingTimer);
  statsLoadingTimer = setTimeout(() => {
    if (token2 === statsToken) setBigState("loading", "正在统计…");
  }, 150);
  if (at) at.textContent = "";
  try {
    const data = await api("/api/scores/stats", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user: currentUser}),
    });
    if (token !== statsToken) return;
    renderStats(data.stats || {});
    await loadDxRating();                         // 等 B35+B15 算完，避免先闪 99999
    if (token !== statsToken) return;
    clearTimeout(statsLoadingTimer);
    statsUser = currentUser;
    setBigState("ready");
  } catch (err) {
    clearTimeout(statsLoadingTimer);
    if (token !== statsToken) return;
    const u = usersById[currentUser];
    const empty = !!(u && u.hasScores === false);
    setBigState("loading", empty ? "这个用户还没有成绩，先导入 MuNET 导出的 JSON" : "统计失败：" + err.message, empty);
  }
}

/* ── 成绩页 v5：版本弹窗 + rating 逐位绘制（琥珀色 + 深色描边） ── */
let versionList = [];
// ★ rating 数字坐标：贴图统一 664×130。下面 5 个数是 5 个空位的水平中心（从左到右），
//   RATING_Y 是竖直中心。等人工量好后只改这两行。
// rating 贴图按数值选：文件名里的数字是下界，取"不超过数值的最大下界"
const RATING_BG_LOWERS = [0, 1000, 2000, 4000, 7000, 10000, 12000, 13000, 14000, 14250,
                          14500, 14750, 15000, 15250, 15500, 15750, 16000, 16250, 16500, 16750];

const DX_NUM_URL = "/ui/Number/UI_NUM_Dxrating.png";     // 4×4 网格，前 10 格 = 0-9
let dxNumSheet = null;

function loadDxNumSheet() {
  if (dxNumSheet) return;
  const im = new Image();
  im.onload = () => { dxNumSheet = im; };
  im.src = DX_NUM_URL;
}

function drawRatingSprites(ctx, text) {
  const CW = dxNumSheet.naturalWidth / 4;
  const CH = dxNumSheet.naturalHeight / 4;
  const h = 64, w = (h * CW) / CH;
  for (let i = 0; i < text.length && i < RATING_SLOT_CENTERS.length; i++) {
    const digit = text.charCodeAt(text.length - 1 - i) - 48;   // 从右往左摆
    if (digit < 0 || digit > 9) continue;
    const sx = (digit % 4) * CW;
    const sy = Math.floor(digit / 4) * CH;
    const cx = RATING_SLOT_CENTERS[RATING_SLOT_CENTERS.length - 1 - i];
    ctx.drawImage(dxNumSheet, sx, sy, CW, CH, cx - w / 2, RATING_Y - h / 2, w, h);
  }
}

function ratingBgFor(value) {
  let picked = RATING_BG_LOWERS[0];
  for (const lower of RATING_BG_LOWERS) {
    if (value >= lower) picked = lower;
  }
  return "/ui/ratingbg/Mai_rating" + picked + ".png";
}

const RATING_SLOT_CENTERS = [336, 390, 442, 494, 547];
const RATING_Y = 64;


function renderVersionGrid() {
  const box = $("ver-grid");
  if (!box) return;
  box.innerHTML = "";
  for (const name of versionList) {
    const btn = document.createElement("button");
    btn.className = name === currentVersion ? "selected" : "";
    btn.title = name;
    btn.disabled = versionBusy;
    btn.innerHTML = `<img src="/ui/version/UI_CMN_TabTitle_MaimaiTitle_${VERSION_IMAGES[name]}.png" alt="${name}">`;
    btn.onclick = () => {
      if (versionBusy) return;
      currentVersion = name;
      refreshVersionButton();
      $("verbox").hidden = true;
      loadDxRating();
    };
    box.append(btn);
  }
}

if ($("verbox")) {
  $("version-btn").onclick = () => {
    if (!versionList.length || versionBusy) return;
    renderVersionGrid();
    $("verbox").hidden = false;
  };
  $("ver-close").onclick = () => { $("verbox").hidden = true; };
  $("verbox").addEventListener("click", (e) => { if (e.target === $("verbox")) $("verbox").hidden = true; });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("verbox").hidden) $("verbox").hidden = true;
  });
}

/* ── 成绩页 v6：两框四行（图标行 + 数据行 ×2），版本显示与按钮分开 ── */
const BADGES = [["FC", "UI_RSL_FC_Text_01.png"], ["FC+", "UI_RSL_FCp_Text_01.png"],
                ["AP", "UI_RSL_AP_Text_01.png"], ["AP+", "UI_RSL_APp_Text_01.png"],
                ["FDX", "UI_RSL_FSD_Text_01.png"], ["FDX+", "UI_RSL_FSDp_Text_01.png"]];
const RANK_ORDER = ["SSS+", "SSS", "SS+", "SS", "S"];

function renderStats(stats) {
  const t = stats.totals || {};
  $("stat-played").textContent = `${t.played ?? 0}${t.sheetsInLibrary ? " / " + t.sheetsInLibrary : ""}`;

  const badgeValues = [t.fc, t.fcPlus, t.ap, t.apPlus, t.fdx, t.fdxPlus];
  $("badge-icons").innerHTML = BADGES.map(([name, file]) =>
    `<img src="/ui/status/${file}" alt="${name}" title="${name}">`).join("");
  $("badge-values").innerHTML = badgeValues.map((v) => `<b>${v ?? 0}</b>`).join("");

  const rank = stats.rank || {};
  $("rank-icons").innerHTML = RANK_ORDER.map((name) =>
    `<img src="/ui/rank/${RANK_IMG[name]}" alt="${name}" title="${name}">`).join("") + "<span></span>";
  $("rank-values").innerHTML = RANK_ORDER.map((name) => `<b>${rank[name] ?? 0}</b>`).join("") + "<span></span>";

  $("view-stats-at").textContent = "统计于 " + fmtTime(stats.generatedAt);
  renderVersion(stats.versions || []);             // 先定版本；rating 由 loadStats 里 await loadDxRating() 画
  fitBigCard();                                    // 卡片渲染完按容器宽度整体缩放
}

function versionCode(name) {
  return Number(String(VERSION_IMAGES[name] || "").replace("Ver", "")) || 0;
}

function renderVersion(versions) {
  const names = versions.map((v) => canonicalVersion(v.version)).filter(Boolean);
  const allowed = (config && config.ratingVersions)
    ? config.ratingVersions.map(canonicalVersion)
    : names;
  // 同一 canonical 版本只保留一个按钮（旧数据可能同时有 DX 和日文旧名）
  versionList = [...new Set(names)]
    .filter((n) => n && VERSION_IMAGES[n] && allowed.includes(n))
    .sort((a, b) => versionCode(b) - versionCode(a));

  currentVersion = canonicalVersion(currentVersion);
  if (!currentVersion || !versionList.includes(currentVersion)) currentVersion = versionList[0] || null;
  refreshVersionButton();
  if ($("verbox") && !$("verbox").hidden) renderVersionGrid();
}

function refreshVersionButton() {
  const img = $("version-img");
  if (!img) return;
  const btn = $("version-btn");
  if (btn) btn.disabled = versionBusy || !versionList.length;
  if (!currentVersion) {
    img.hidden = true;
    img.removeAttribute("src");
    img.removeAttribute("title");
    img.alt = "";
    return;
  }
  img.src = `/ui/version/UI_CMN_TabTitle_MaimaiTitle_${VERSION_IMAGES[currentVersion]}.png`;
  img.title = currentVersion;
  img.alt = currentVersion;
  img.hidden = false;
}

function drawRating(value, token) {
  const canvas = $("rating-img");
  if (!canvas) return;
  const img = new Image();
  img.onload = () => {
    if (token !== undefined && token !== ratingToken) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0);
    const paint = () => {
      if (token !== undefined && token !== ratingToken) return;
      const text = String(value ?? "");
      if (dxNumSheet) { drawRatingSprites(ctx, text); return; }
      ctx.font = `700 68px MaiBold, sans-serif`;    // 粗体，居中放在框里（不塞满）
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";
      ctx.lineWidth = 5;
      ctx.strokeStyle = "#241f1a";
      ctx.fillStyle = "#F2B23C";
      for (let i = 0; i < text.length && i < RATING_SLOT_CENTERS.length; i++) {
        const cx = RATING_SLOT_CENTERS[RATING_SLOT_CENTERS.length - 1 - i];   // 从右往左摆
        const ch = text[text.length - 1 - i];
        ctx.strokeText(ch, cx, RATING_Y);
        ctx.fillText(ch, cx, RATING_Y);
      }
    };
    (document.fonts && document.fonts.load) ? document.fonts.load("700 68px MaiBold").then(paint).catch(paint) : paint();
  };
  img.onerror = () => {
    if (token === undefined || token === ratingToken) clearRating();
  };
  img.src = ratingBgFor(Number(value) || 0);
}


loadDxNumSheet();          // 预取 DX rating 的数字贴图

/* ── 歌曲浏览：卡片（曲绘 + 标题 + 各难度行），贴图都在 dataUI/musicLib ── */


/* 大卡宽度不够时整体缩放（zoom 会连布局一起缩，不用手工补高度） */
const BIG_CARD_W = 1000;

export function fitBigCard() {
  const card = $("view-now");
  const loading = $("view-loading");
  const host = (card && card.parentElement) || (loading && loading.parentElement);
  if (!host) return;
  const k = Math.max(0.5, Math.min(1, host.clientWidth / BIG_CARD_W));
  [card, loading].forEach((el) => {
    if (!el) return;
    if (k >= 0.999) el.style.removeProperty("zoom");
    else el.style.zoom = k.toFixed(3);
  });
}

export function setCurrentUser(value) {
  currentUser = value;
}

export { AVATAR_SIDE, refreshSmallButton, BADGES, DX_NUM_URL, RANK_IMG, RANK_ORDER, RATING_BG_LOWERS, RATING_SLOT_CENTERS, RATING_Y, VERSION_IMAGES, avatarSrc, avatarVersion, crop, cropClamp, cropRender, cropToPng, cropZoom, currentUser, drawRatingSprites, dxNumSheet, fmtTime, loadDxNumSheet, loadStats, openCrop, ratingBgFor, renderAvatar, renderStats, renderUserCards, saveAvatar, selectUser, squareToPng, stageSize, userList };

window.addEventListener("resize", () => { fitBigCard(); });
