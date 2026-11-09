import { $, api, chain, loadUsers, usersById , setNote } from "./core.js";
import { scoresChainName } from "./db.js";
import { VERSION_IMAGES, avatarSrc, currentUser, fmtTime, loadStats, renderUserCards, selectUser, setCurrentUser } from "./stats.js";
import { escapeHtml, glyph, glyphs, marqueeIfLong, pillDataUrl, slot, songCard } from "./songcard.js";
import { attachPagers, fillPagers, fitGrid, renderCardPage, renderPagers } from "./songlist-view.js";

// 用途：歌曲浏览 + 卡池预览：列表、筛选、搜索、翻页、统计、选歌池（从 app.js 按领域拆出，classic script，index.html 按顺序加载）
// 依赖：全局函数/变量在调用时解析，不要求定义顺序
const TYPE_BADGE = {std: "Type_STD.png", dx: "Type_DX.png", utage: "Type_UTG.png", utage2p: "Type_UTG_2p.png"};
const DIFF_BTN = {BASIC: "Btn_BAS.png", ADVANCED: "Btn_ADV.png", EXPERT: "Btn_EXP.png",
                  MASTER: "Btn_MAS.png", "Re:MASTER": "Btn_REM.png", UTAGE: "Btn_UTG.png"};
const BASE_BY_FAMILY = {std: "Base_STD.png", dx: "Base_DX.png", utage: "Base_UTG.png", utage2p: "Base_UTG.png"};

/* ── 歌曲卡片 v2：严格按示意（贴图为主，只有白空位是代码画的） ── */
const DIFF_BTN_FILE = {basic: "Btn_BAS.png", advanced: "Btn_ADV.png", expert: "Btn_EXP.png",
                     master: "Btn_MAS.png", remaster: "Btn_REM.png", utage: "Btn_UTG.png"};
const DIFF_SHEET = {basic: "UI_NUM_BAS", advanced: "UI_NUM_ADV", expert: "UI_NUM_EXP",
                    master: "UI_NUM_MAS", remaster: "UI_NUM_REM",
                    // utage 原本没有 UI_NUM_UTG.png，实际那张叫 "UI_NUM_UTG_Question (2).png"（192×240 4×4，格子位置和普通表一致），
                    // 已复制成 UI_NUM_UTG.png（带空格的文件名走 /ui/ 会 404）
                    utage: "UI_NUM_UTG"};
const RANK_FILE = ["D", "C", "B", "BB", "BBB", "A", "AA", "AAA", "S", "Sp", "SS", "SSp", "SSS", "SSSp"];
const COMBO_FILE = {1: "FC", 2: "FCp", 3: "AP", 4: "APp"};
const SYNC_FILE = {1: "FS", 2: "FSp", 3: "FSD", 4: "FSDp", 5: "SP"};
const SLOT = `<span class="slot"></span>`;

function glyphIndex(ch) {
  if (ch >= "0" && ch <= "9") return ch.charCodeAt(0) - 48;
  if (ch === "+") return 10;
  if (ch === "-") return 11;
  if (ch === ",") return 12;
  if (ch === ".") return 13;
  return -1;
}

// 4×4 网格（每格宽:高 = 48:60），用 CSS 精灵取一格
function scoreSlots(sheet, info) {
  const rank = info.rank == null ? SLOT
    : `<img class="sc-icon" src="/ui/rank/UI_GAM_Rank_${RANK_FILE[info.rank]}.png" alt="">`;
  const dx = info.dxscore == null ? SLOT
    : `<span class="sc-dxs">${glyph(sheet, 10, 26)}<img class="sc-icon" src="/ui/dxScoreSmall/UI_MSS_Rival_DXscoreIcon_0${info.dxscore}.png" alt=""></span>`;
  const combo = COMBO_FILE[info.combo]
    ? `<img class="sc-icon" src="/ui/status/UI_MSS_MBase_Icon_${COMBO_FILE[info.combo]}.png" alt="">` : SLOT;
  const sync = SYNC_FILE[info.sync]
    ? `<img class="sc-icon" src="/ui/status/UI_MSS_MBase_Icon_${SYNC_FILE[info.sync]}.png" alt="">` : SLOT;
  return rank + dx + combo + sync;
}

/* ── 歌曲浏览 v54：真曲库（全曲分类）。一次拉全库，排序 / 筛选 / 分页都在前端做 ──
   默认排序：dataid 从大到小 → 同一 dataid 里按 dx、std、utage、utage2p → 同族多张（宴谱）按难度从大到小。
   筛选：版本（贴图按钮 + 弹出框，第二页是牌子条件）、谱面族、搜索（按钮开浮层，避免输入法抖动）。 */
const SONG_FAMILY_RANK = { dx: 0, std: 1, utage: 2, utage2p: 3 };
const SONG_PAGE_SIZE = 100;                       // 100 首 = 25 行（一排 4 张）
let SONG_FILTER_PICK = null;                      // 非空：歌曲筛选器被“卡片设置”借用来当版本/牌子选择器
const SONG_STATE = { songs: [], versions: [], loaded: false, user: null,
                     family: "dxstd", version: "", plate: null, plates: [], platesLoaded: false,
                     verDesc: false, sort: "dataid-desc", q: "", page: 1, pages: 1,
                     criteria: { score: 100, combo: 1, sync: 0 },   // 默认：达成率 100.0% + FC，双人任意
                     incomplete: false };
// 难度桶：chartid 0..4 = BAS/ADV/EXP/MAS/REM，chartid 10 = 宴（utage 与 utage2p 统一算）
const SONG_DIFFS = [
  { name: "BAS", key: "bas" }, { name: "ADV", key: "adv" }, { name: "EXP", key: "exp" },
  { name: "MAS", key: "mas" }, { name: "REM", key: "rem" }, { name: "UTG", key: "utage" },
];
function songDiffIndex(chartid) {
  const id = Number(chartid);
  if (id >= 0 && id <= 4) return id;
  if (id === 10) return 5;
  return -1;
}
const SONG_COMBO_NAMES = ["任意", "FC", "FC+", "AP", "AP+"];      // comboStatus
const SONG_SYNC_NAMES = ["任意", "FS", "FS+", "FSD", "FSD+", "SP"];  // syncStatus（双人游玩）
// 版本牌组里的四档（極 / 将 / 神 / 舞舞），缺的那档在列表里留空位
const SONG_PLATE_TIERS = ["FULL COMBO", "RANK SSS", "ALL PERFECT", "FULL SYNC DX"];
const SONG_PLATE_TIER_RANK = { "FULL COMBO": 0, "RANK SSS": 1, "ALL PERFECT": 2, "FULL SYNC DX": 3, "CLEAR": 4 };
// 牌子分组标题：真代那组（真極/真神/真舞舞）覆盖 maimai～maimai PLUS，显示成区间写法
// （CiRCLE PLUS 那组的版本名已在 plate_conditions 里归一，数据里就是 "CiRCLE PLUS"）
const SONG_PLATE_GROUP_LABEL = { "maimai PLUS": "maimai～maimai PLUS" };
// 版本搜索按「版本名整体」匹配（不是子串）：maimai 只命中真代那组，不会命中 maimai～FiNALE / CiRCLE PLUS
const SONG_PLATE_VER_TOKENS = {
  "maimai PLUS": ["maimai", "maimai PLUS"],          // 真代覆盖 maimai ~ maimai PLUS，两个都算
  "maimai～FiNALE": ["FiNALE"],                     // 舞牌挂在 FiNALE 下（排位也在 FiNALE 后）
};

function plateVerTokens(ver) {
  const out = (SONG_PLATE_VER_TOKENS[ver] || [ver || ""]).map(plateSearchKey);
  const label = SONG_PLATE_GROUP_LABEL[ver];
  if (label) out.push(plateSearchKey(label));
  return out.filter(Boolean);
}

/* 牌子搜索用的归一化：忽略大小写；「+」当「PLUS」（maimai+ == maimai PLUS）；各种波浪号统一；
   全角加号/空格也归一。查询串和候选串走同一个函数。 */
function plateSearchKey(text) {
  return String(text == null ? "" : text)
    .toLowerCase()
    .replace(/[+＋]/g, " plus ")
    .replace(/[~～〜]/g, "～")
    .replace(/[　\s]+/g, " ")
    .trim();
}
let SONG_SEARCH_TIMER = 0;

function songMaxLevel(song) {
  let top = 0;
  (song.sheets || []).forEach((sh) => { const v = Number(sh.level); if (v > top) top = v; });
  return top;
}

function songSorter(mode) {
  const fam = (s) => (SONG_FAMILY_RANK[s.family] == null ? 9 : SONG_FAMILY_RANK[s.family]);
  const byDataidDesc = (a, b) => (b.dataid - a.dataid) || (fam(a) - fam(b)) || (songMaxLevel(b) - songMaxLevel(a));
  const byDataidAsc = (a, b) => (a.dataid - b.dataid) || (fam(a) - fam(b)) || (songMaxLevel(b) - songMaxLevel(a));
  if (mode === "dataid-asc") return byDataidAsc;
  if (mode === "level-desc") return (a, b) => (songMaxLevel(b) - songMaxLevel(a)) || byDataidDesc(a, b);
  if (mode === "level-asc") return (a, b) => (songMaxLevel(a) - songMaxLevel(b)) || byDataidDesc(a, b);
  if (mode === "title") return (a, b) => String(a.title || "").localeCompare(String(b.title || ""), "ja") || byDataidDesc(a, b);
  return byDataidDesc;
}

/* 牌子条件：mask[internalId] 是 chartid 位掩码（0..31 各一位） */
function sheetInPlate(song, sheet, plate) {
  if (!plate || !plate.mask) return true;
  const bits = plate.mask[String(song.internalId)];
  return !!bits && !!(bits & (1 << Number(sheet.chartid)));
}

/* 按牌子看时只留牌子里要求的难度（不需要的谱/难度隐藏） */
function songViewSheets(song) {
  let sheets = song.sheets || [];
  if (SONG_STATE.plate) sheets = sheets.filter((sh) => sheetInPlate(song, sh, SONG_STATE.plate));
  if (SONG_STATE.incomplete) sheets = sheets.filter((sh) => !chartDone(sh));   // 只看未完成
  return sheets;
}

/* 牌子条件 → 完成率条件（选中牌子时自动切换并锁定）：
   極 FULL COMBO = 连击 ≥ FC；将 RANK SSS = 达成率 ≥ 100.0%；神 ALL PERFECT = 连击 ≥ AP；
   舞 FULL SYNC DX = 双人 ≥ FSD；霸者 CLEAR = 达成率 ≥ 80.0%（唯一特殊） */
const PLATE_CRITERIA = {
  "FULL COMBO": { score: null, combo: 1, sync: null },
  "RANK SSS": { score: 100, combo: null, sync: null },
  "ALL PERFECT": { score: null, combo: 3, sync: null },
  "FULL SYNC DX": { score: null, combo: null, sync: 3 },
  "CLEAR": { score: 80, combo: null, sync: null },
};

function plateCriteria(plate) {
  return plate ? (PLATE_CRITERIA[plate.cond] || null) : null;
}

function activeCriteria() {
  return plateCriteria(SONG_STATE.plate) || SONG_STATE.criteria;
}

function criteriaText(c) {
  if (!c) return "";
  const parts = [];
  if (c.score != null) parts.push("达成率 ≥ " + Number(c.score).toFixed(1) + "%");
  if (c.combo != null) parts.push("连击 ≥ " + SONG_COMBO_NAMES[c.combo]);
  if (c.sync != null) parts.push("双人 ≥ " + SONG_SYNC_NAMES[c.sync]);
  return parts.join(" · ");
}

/* 条件按钮：按牌子统计时显示牌子条件并禁用 */
function renderSongIncompleteBtn() {
  const btn = $("song-incomplete");
  if (btn) btn.classList.toggle("on", !!SONG_STATE.incomplete);
}

function syncSongCriteriaControls() {
  const btn = $("song-crit-btn");
  if (!btn) return;
  const crit = plateCriteria(SONG_STATE.plate);
  if (crit) {
    btn.disabled = true;
    btn.textContent = "条件：" + criteriaText(crit);
    btn.title = "按牌子条件统计（已锁定）";
    return;
  }
  btn.disabled = false;
  btn.textContent = "条件";
  btn.title = "完成条件（分数线 / 连击 / 双人）";
  const sc = $("crit-score");
  const cb = $("crit-combo");
  const sy = $("crit-sync");
  if (sc) sc.value = String(SONG_STATE.criteria.score);
  if (cb) cb.value = String(SONG_STATE.criteria.combo);
  if (sy) sy.value = String(SONG_STATE.criteria.sync);
}

function chartDone(sheet) {
  const crit = activeCriteria();
  const ach = Number(sheet.achievement);
  if (crit.score != null) {
    if (!(ach > 0)) return false;
    if (ach / 10000 < crit.score - 1e-6) return false;
  }
  if (crit.combo != null && (Number(sheet.combo) || 0) < crit.combo) return false;
  if (crit.sync != null && (Number(sheet.sync) || 0) < crit.sync) return false;
  return true;
}

function songStats(list) {
  const byDiff = SONG_DIFFS.map((d) => ({ name: d.name, key: d.key, done: 0, total: 0 }));
  let total = 0;
  let done = 0;
  for (const song of list) {
    for (const sh of song.sheets || []) {
      const ok = chartDone(sh);
      total += 1;
      if (ok) done += 1;
      const idx = songDiffIndex(sh.chartid);
      if (idx >= 0) {
        byDiff[idx].total += 1;
        if (ok) byDiff[idx].done += 1;
      }
    }
  }
  return { total, done, byDiff };
}

function renderSongStats(list) {
  const count = $("song-count");
  const stats = $("song-stats");
  if (!SONG_STATE.loaded) {
    if (count) count.textContent = "加载中…";
    if (stats) stats.hidden = true;
    return;
  }
  const s = songStats(list);
  const pct = s.total ? (100 * s.done / s.total) : 0;
  if (count) count.textContent = "共 " + list.length + " 首 · 完成率 " + pct.toFixed(1) + "%";
  if (stats) {
    stats.hidden = !s.total;
    stats.innerHTML = '<span class="songdiff">'
      + s.byDiff.map((d) => '<span class="d-' + d.key + '">' + d.name + "</span> - "
        + d.done + "/" + d.total).join(" | ")
      + "</span>";
  }
}

/* 谱面族：分类 chip 和下拉框同一份状态；按牌子看时下拉禁用 */
function syncSongFamilyControls() {
  const sel = $("song-family");
  const lock = $("song-family-lock");
  const locked = !!SONG_STATE.plate;
  if (sel) {
    sel.value = SONG_STATE.family;
    sel.disabled = locked;
    sel.hidden = locked;                 // v77：锁定时直接换成一个「锁定」标签
  }
  if (lock) lock.hidden = !locked;
}

function songInPlate(song, plate) {
  if (!plate || !plate.mask) return false;
  const bits = plate.mask[String(song.internalId)];
  if (!bits) return false;
  for (const sh of song.sheets || []) {
    if (bits & (1 << Number(sh.chartid))) return true;
  }
  return false;
}

/* 搜索：纯数字查 id（internalId / dataid），其余按 曲名 / 别名 / 作者 子串匹配 */
function songMatches(song) {
  const st = SONG_STATE;
  if (!st.plate && st.family) {                     // 按牌子看时谱面类型无效（牌子条件说了算）
    if (st.family === "dxstd") {
      if (song.family !== "dx" && song.family !== "std") return false;
    } else if (st.family === "utageall") {
      if (song.family !== "utage" && song.family !== "utage2p") return false;
    } else if (song.family !== st.family) return false;
  }
  if (st.version && song.version !== st.version) return false;
  if (st.plate && !songInPlate(song, st.plate)) return false;
  if (!st.q) return true;
  const idHit = /^id-([0-9]+)$/.exec(st.q);          // 只有 id-数字 才按 id 查（纯数字可能是别名）
  if (idHit) {
    const digits = idHit[1];
    const iid = String(song.internalId == null ? "" : song.internalId);
    const did = String(song.dataid == null ? "" : song.dataid);
    return iid.indexOf(digits) >= 0 || did.indexOf(digits) >= 0;
  }
  const needle = st.q;
  if (String(song.title || "").toLowerCase().indexOf(needle) >= 0) return true;
  if (String(song.artist || "").toLowerCase().indexOf(needle) >= 0) return true;
  return (song.aliasesTag || []).some((a) => String(a).toLowerCase().indexOf(needle) >= 0);
}

function songFilteredList() {
  const list = SONG_STATE.songs.filter(songMatches);
  list.sort(songSorter(SONG_STATE.sort));
  return list;
}

function renderSongs(scrollTop) {
  const box = $("song-cards");
  if (!box) return;
  const st = SONG_STATE;
  const all = songFilteredList();                       // 统计口径：不受「只看未完成」影响
  // 注意：牌子模式下列表里的谱面是「牌子 ∩ 未完成」，判断这首歌还剩不剩谱面要用过滤后的结果
  const list = st.incomplete ? all.filter((song) => songViewSheets(song).length > 0) : all;
  const view = (st.plate || st.incomplete)
    ? list.map((song) => Object.assign({}, song, { sheets: songViewSheets(song) }))
    : list;
  const statView = st.plate                           // 统计只受牌子过滤影响，不看「只看未完成」
    ? all.map((song) => Object.assign({}, song, {
        sheets: (song.sheets || []).filter((sh) => sheetInPlate(song, sh, st.plate)),
      }))
    : all;
  const pages = Math.max(1, Math.ceil(view.length / SONG_PAGE_SIZE));
  st.pages = pages;
  st.page = Math.min(Math.max(1, st.page), pages);
  const from = (st.page - 1) * SONG_PAGE_SIZE;
  const slice = view.slice(from, from + SONG_PAGE_SIZE);
  renderCardPage(box, slice);
  box.classList.toggle("is-empty", !slice.length);
  if (!slice.length) box.innerHTML = '<p class="note">没有匹配的曲子。</p>';
  const ssCount = $("ss-count");
  if (ssCount) ssCount.textContent = st.q ? "命中 " + list.length + " 首" : "";
  renderSongPagers(pages, from, slice.length, view.length);
  renderSongStats(statView);
  fitSongCards();
  if (scrollTop) {
    // 只有用户已经滚过工具栏时才回到工具栏；在顶部改筛选时保持不动（避免每次往下拉一点）
    const bar = document.querySelector(".songbar");
    if (bar) {
      const barTop = bar.getBoundingClientRect().top + window.scrollY;
      if (window.scrollY > barTop + 8) bar.scrollIntoView({ block: "start" });
    }
  }
}

/* 版本按钮：显示「选中版本的贴图」；选了牌子就显示牌子名 */
/* v62/v63：翻页条（顶部 + 底部同一套结构）。跳至第 N 页 —— 越界夹到 1..总页数，空/非数字忽略 */
function renderSongPagers(pages, from, count, total) {
  renderPagers("data-pager", SONG_STATE, total, SONG_PAGE_SIZE);
}

function jumpSongPage(el) {
  if (!el) return;
  const n = parseInt(el.value, 10);
  el.value = "";
  if (!Number.isFinite(n)) { el.focus(); return; }
  const target = Math.min(Math.max(1, n), SONG_STATE.pages || 1);
  if (target === SONG_STATE.page) { el.focus(); return; }
  SONG_STATE.page = target;
  renderSongs(true);
}

function renderSongVerBtn() {
  const btn = $("song-verbtn");
  if (!btn) return;
  const st = SONG_STATE;
  if (st.plate) {
    btn.innerHTML = '<span class="songver-label">牌子 · ' + st.plate.name + '</span>';
    btn.title = (st.plate.ver || "") + " · " + (st.plate.cond || "");
  } else if (st.version) {
    btn.innerHTML = '<span class="songver-label">' + st.version + '</span>';   // v56：选完也只显示文字
    btn.title = st.version;
  } else {
    btn.innerHTML = '<span class="songver-label">全部版本</span>';
    btn.title = "版本 / 牌子条件";
  }
  btn.classList.toggle("on", !!(st.plate || st.version));
}

function renderSfOrderBtn() {
  const btn = $("sf-order");
  if (btn) btn.textContent = SONG_STATE.verDesc ? "最新 → maimai" : "maimai → 最新";
}

function renderSfVersions() {
  const box = $("sf-versions");
  if (!box) return;
  const pick = SONG_FILTER_PICK;
  const versions = pick ? pick.versions : SONG_STATE.versions;
  const current = pick ? pick.current : SONG_STATE.version;
  const names = (!pick && SONG_STATE.verDesc) ? versions.slice().reverse() : versions;
  box.innerHTML = names.map((name) => {
    const code = VERSION_IMAGES[name];
    const inner = code
      ? '<img src="/ui/version/UI_CMN_TabTitle_MaimaiTitle_' + code + '.png" alt="">'
      : '<span>' + name + '</span>';
    const on = current === name ? " selected" : "";
    return '<button class="versionbtn' + on + '" data-ver="' + name + '" title="' + name + '">' + inner + '</button>';
  }).join("");
  const all = $("sf-ver-all");
  if (all) {
    all.textContent = pick ? "不限版本" : "全部版本";
    all.classList.toggle("on", !current);
  }
  renderSfOrderBtn();
}

/* 牌子分组用版本排序：maimai～FiNALE 紧跟 FiNALE；CiRCLE PLUS 的牌子表里带「maimai でらっくす」前缀 */
function plateGroupKey(ver, versions = SONG_STATE.versions) {
  const idx = versions;
  if (ver === "maimai～FiNALE") {
    const i = idx.indexOf("FiNALE");
    return (i < 0 ? 12 : i) + 0.5;
  }
  const i = idx.indexOf(ver);
  return i < 0 ? 999 : i;
}

function plateChip(p) {
  const png = p.nameplate || ("UI_Plate_" + String(p.num || 0).padStart(6, "0") + ".png");
  const pick = SONG_FILTER_PICK;
  const selectedId = pick ? pick.current : (SONG_STATE.plate && SONG_STATE.plate.id);
  const on = selectedId === p.id ? " on" : "";
  const tip = p.name || "";                              // 悬停只显示牌子名
  return '<button class="sf-plate' + on + '" data-plate="' + p.id + '" title="' + tip + '">'
    + '<img src="/plate/' + png + '" alt="' + (p.name || "") + '" loading="lazy"></button>';
}

function renderPlateList() {
  const box = $("sf-plates");
  if (!box) return;
  const qEl = $("sf-plate-q");
  const q = plateSearchKey(qEl && qEl.value);
  const pick = SONG_FILTER_PICK;
  const plates = pick ? pick.plates : SONG_STATE.plates;
  const versions = pick ? pick.versions : SONG_STATE.versions;
  const byVer = new Map();
  for (const p of plates) {
    if (q) {
      const nameHit = plateSearchKey(p.name).indexOf(q) >= 0;          // 牌子名：子串
      const verHit = plateVerTokens(p.ver).indexOf(q) >= 0;            // 版本：按版本名整体
      if (!nameHit && !verHit) continue;
    }
    if (!byVer.has(p.ver)) byVer.set(p.ver, []);
    byVer.get(p.ver).push(p);
  }
  const vers = [...byVer.keys()].sort((a, b) => plateGroupKey(a, versions) - plateGroupKey(b, versions));
  if (!pick && SONG_STATE.verDesc) vers.reverse();
  let html = "";
  for (const ver of vers) {
    const list = byVer.get(ver).slice().sort(
      (a, b) => (SONG_PLATE_TIER_RANK[a.cond] == null ? 9 : SONG_PLATE_TIER_RANK[a.cond])
        - (SONG_PLATE_TIER_RANK[b.cond] == null ? 9 : SONG_PLATE_TIER_RANK[b.cond]));
    let cells;
    if (q) {
      cells = list.map(plateChip);                    // 搜索时只列命中的牌，不插空位
    } else {
      cells = SONG_PLATE_TIERS.map((tier) => {
        const hit = list.find((p) => p.cond === tier);
        return hit ? plateChip(hit) : '<span class="sf-plate blank"></span>';   // 缺档（如 maimai PLUS 的「真将」）留空位
      });
      for (const p of list) if (SONG_PLATE_TIERS.indexOf(p.cond) < 0) cells.push(plateChip(p));   // 覇者这类排在最后
    }
    const label = SONG_PLATE_GROUP_LABEL[ver] || ver || "（未知版本）";
    html += '<div class="sf-group"><h4>' + label + '</h4><div class="sf-chips">'
      + cells.join("") + '</div></div>';
  }
  box.innerHTML = html || '<p class="sf-empty">没有匹配的牌子。</p>';
  renderSfOrderBtn();
}

async function loadPlates() {
  if (SONG_FILTER_PICK) { renderPlateList(); return; }
  const st = SONG_STATE;
  if (st.platesLoaded) { renderPlateList(); return; }
  const box = $("sf-plates");
  if (box) box.innerHTML = '<p class="sf-empty">读取牌子条件…</p>';
  try {
    const data = await api("/api/plates");
    st.plates = data.plates || [];
    st.platesLoaded = true;
    renderPlateList();
  } catch (err) {
    if (box) box.innerHTML = '<p class="sf-empty">读取失败：' + err.message + "</p>";
  }
}

function setFilterTab(name) {
  document.querySelectorAll("#songfilter .sf-tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll("#songfilter .sf-page").forEach((p) => { p.hidden = p.dataset.page !== name; });
  if (name === "plate") loadPlates();
}

function openSongFilter(tab) {
  const pop = $("songfilter");
  if (!pop) return;
  pop.hidden = false;
  setFilterTab(tab || "version");
  renderSfVersions();
}

function closeSongFilter() {
  const pop = $("songfilter");
  if (pop) pop.hidden = true;
  SONG_FILTER_PICK = null;
  const order = $("sf-order");
  if (order) order.hidden = false;
  document.querySelectorAll("#songfilter .sf-tab").forEach((b) => { b.hidden = false; });
  const plateAll = $("sf-plate-all");
  if (plateAll) plateAll.textContent = "返回";
}

export function openSongFilterPicker(options) {
  const opts = options || {};
  SONG_FILTER_PICK = {
    kind: opts.kind === "plate" ? "plate" : "version",
    versions: opts.versions || [],
    plates: opts.plates || [],
    current: String(opts.current || ""),
    onPick: typeof opts.onPick === "function" ? opts.onPick : null,
  };
  const order = $("sf-order");
  if (order) order.hidden = true;
  const plateAll = $("sf-plate-all");
  if (plateAll) plateAll.textContent = "不限牌子";
  document.querySelectorAll("#songfilter .sf-tab").forEach((b) => {
    b.hidden = b.dataset.tab !== SONG_FILTER_PICK.kind;
  });
  const q = $("sf-plate-q");
  if (q) q.value = "";
  openSongFilter(SONG_FILTER_PICK.kind);
  setFilterTab(SONG_FILTER_PICK.kind);
}

let SONG_SEARCH_SNAPSHOT = null;

/* 搜索按钮：没搜索时是「搜索」，搜索生效后变「返回」（点一下清掉搜索筛选） */
function renderSongSearchBtn() {
  const btn = $("song-searchbtn");
  if (!btn) return;
  const back = !!SONG_STATE.q;
  btn.textContent = back ? "返回" : "搜索";
  btn.classList.toggle("back", back);
  btn.title = back ? "清除搜索筛选" : "搜索（曲名 / 别名 / 艺术家 / id-数字）";
}

function openSongSearch() {
  const st = SONG_STATE;
  SONG_SEARCH_SNAPSHOT = { family: st.family, version: st.version, plate: st.plate, q: st.q, page: st.page };
  st.family = ""; st.version = ""; st.plate = null; st.q = ""; st.page = 1;   // 搜索时回到「全部」
  syncSongFamilyControls();
  renderSongVerBtn();
  const ov = $("songsearch");
  if (!ov) return;
  ov.hidden = false;
  void ov.offsetWidth;
  ov.classList.add("show");
  const input = $("ss-input");
  if (input) { input.value = ""; setTimeout(() => input.focus(), 40); }
  renderSongs();
}

function hideSongSearch() {
  const ov = $("songsearch");
  if (!ov || ov.hidden) return;
  ov.classList.remove("show");
  setTimeout(() => { ov.hidden = true; }, 170);
}

function commitSongSearch() {
  const input = $("ss-input");
  SONG_STATE.q = input ? input.value.trim().toLowerCase() : "";
  SONG_STATE.page = 1;
  SONG_SEARCH_SNAPSHOT = null;
  hideSongSearch();
  renderSongSearchBtn();
  renderSongs(true);
}

function cancelSongSearch() {
  if (SONG_SEARCH_SNAPSHOT) {
    Object.assign(SONG_STATE, SONG_SEARCH_SNAPSHOT);
    SONG_SEARCH_SNAPSHOT = null;
  }
  syncSongFamilyControls();
  renderSongVerBtn();
  hideSongSearch();
  renderSongSearchBtn();
  renderSongs(true);
}

/* 曲库只拉一次，歌曲浏览和卡池预览共用（同一份 /api/songs 数据，字段不会两页不一致） */
async function ensureSongLibrary() {
  if (SONG_STATE.loaded && SONG_STATE.user === (currentUser || "")) return SONG_STATE.songs;
  if (!Object.keys(usersById).length) { try { await loadUsers(); } catch (err) {} }
  const data = await api("/api/songs?user=" + encodeURIComponent(currentUser || ""));
  SONG_STATE.songs = data.songs || [];
  SONG_STATE.versions = data.versions || [];
  SONG_STATE.user = currentUser || "";
  SONG_STATE.loaded = true;
  return SONG_STATE.songs;
}

async function loadSongs() {
  const box = $("song-cards");
  if (!box) return;
  // 谱面页要显示「个人拟合」（有效定数），所以歌曲浏览也激活一次分析脚本
  const analysis = api("/api/analysis?run=1&user=" + encodeURIComponent(currentUser || "")).catch(() => null);
  if (SONG_STATE.loaded && SONG_STATE.user === (currentUser || "")) { renderSongs(); await analysis; return; }
  box.innerHTML = '<div class="loading">正在加载曲库…</div>';
  try {
    await Promise.all([ensureSongLibrary(), analysis]);
    renderSongVerBtn();
    renderSongSearchBtn();
    renderSongIncompleteBtn();
    syncSongFamilyControls();
    syncSongCriteriaControls();
    renderSongs();
  } catch (err) {
    box.innerHTML = '<div class="loading">读取失败：' + escapeHtml(err.message) + "</div>";
  }
}

function bindSongControls() {
  const on = (id, ev, fn) => { const el = $(id); if (el) el.addEventListener(ev, fn); };
  on("song-family", "change", (e) => {
    SONG_STATE.family = e.target.value;
    SONG_STATE.page = 1;
    syncSongFamilyControls();
    renderSongs(true);
  });
  on("song-sort", "change", (e) => { SONG_STATE.sort = e.target.value; SONG_STATE.page = 1; renderSongs(true); });
  fillPagers("data-pager");
  attachPagers("data-pager", SONG_STATE, renderSongs);
    // 完成条件（分数线 / 连击）
  on("song-incomplete", "click", () => {
    SONG_STATE.incomplete = !SONG_STATE.incomplete;
    SONG_STATE.page = 1;
    renderSongIncompleteBtn();
    renderSongs(true);
  });
  on("song-crit-btn", "click", () => { const m = $("songcrit"); if (m) m.hidden = false; });
  on("crit-close", "click", () => { const m = $("songcrit"); if (m) m.hidden = true; });
  on("songcrit", "click", (e) => { if (e.target.id === "songcrit") $("songcrit").hidden = true; });
  on("crit-score", "change", (e) => {
    SONG_STATE.criteria.score = Number(e.target.value);
    renderSongs();
  });
  on("crit-combo", "change", (e) => {
    SONG_STATE.criteria.combo = Number(e.target.value);
    renderSongs();
  });
  on("crit-sync", "change", (e) => {
    SONG_STATE.criteria.sync = Number(e.target.value);
    renderSongs();
  });

  // 版本 / 牌子 弹出框
  on("song-verbtn", "click", () => openSongFilter("version"));
  on("sf-close", "click", () => closeSongFilter());
  on("sf-order", "click", () => {
    SONG_STATE.verDesc = !SONG_STATE.verDesc;
    renderSfOrderBtn();
    renderSfVersions();
    renderPlateList();
  });
  on("songfilter", "click", (e) => { if (e.target.id === "songfilter") closeSongFilter(); });
  document.querySelectorAll("#songfilter .sf-tab").forEach((b) => {
    b.addEventListener("click", () => setFilterTab(b.dataset.tab));
  });
  on("sf-ver-all", "click", () => {
    const pick = SONG_FILTER_PICK;
    if (pick && pick.kind !== "version") return;
    if (pick) { closeSongFilter(); pick.onPick && pick.onPick("", ""); return; }
    SONG_STATE.version = ""; SONG_STATE.plate = null; SONG_STATE.page = 1;
    renderSongVerBtn(); closeSongFilter(); renderSongs(true);
  });
  on("sf-versions", "click", (e) => {
    const btn = e.target.closest("button[data-ver]");
    if (!btn) return;
    const pick = SONG_FILTER_PICK;
    if (pick && pick.kind !== "version") return;
    if (pick) { closeSongFilter(); pick.onPick && pick.onPick(btn.dataset.ver, btn.dataset.ver); return; }
    SONG_STATE.version = btn.dataset.ver; SONG_STATE.plate = null; SONG_STATE.page = 1;
    renderSongVerBtn(); closeSongFilter(); renderSongs(true);
  });
  on("sf-plate-all", "click", () => {
    const pick = SONG_FILTER_PICK;
    if (pick && pick.kind !== "plate") return;
    if (pick) { closeSongFilter(); pick.onPick && pick.onPick("", ""); return; }
    SONG_STATE.plate = null; SONG_STATE.version = ""; SONG_STATE.page = 1;
    syncSongFamilyControls();
    syncSongCriteriaControls();
    renderSongVerBtn(); closeSongFilter(); renderSongs(true);
  });
  on("sf-plate-q", "input", () => renderPlateList());
  on("sf-plates", "click", (e) => {
    const btn = e.target.closest("button[data-plate]");
    if (!btn) return;
    const pick = SONG_FILTER_PICK;
    if (pick && pick.kind !== "plate") return;
    if (pick) {
      const plate = (pick.plates || []).find((p) => p.id === btn.dataset.plate) || null;
      closeSongFilter();
      pick.onPick && pick.onPick(plate ? plate.id : "", plate ? plate.name : "");
      return;
    }
    SONG_STATE.plate = SONG_STATE.plates.find((p) => p.id === btn.dataset.plate) || null;
    SONG_STATE.version = ""; SONG_STATE.page = 1;
    syncSongFamilyControls();
    syncSongCriteriaControls();
    renderSongVerBtn(); closeSongFilter(); renderSongs(true);
  });

  // 搜索浮层：按钮开浮层；确定提交；取消还原；生效后按钮变「返回」
  on("song-searchbtn", "click", () => {
    const ov = $("songsearch");
    if (ov && !ov.hidden) { cancelSongSearch(); return; }
    if (SONG_STATE.q) {
      SONG_STATE.q = ""; SONG_STATE.page = 1;
      renderSongSearchBtn();
      renderSongs(true);
      return;
    }
    openSongSearch();
  });
  on("ss-ok", "click", () => commitSongSearch());
  on("ss-cancel", "click", () => cancelSongSearch());
  on("ss-input", "keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commitSongSearch(); } });
  // v71：输入期间不过滤，点「确定」（或回车）才提交
  on("ss-input", "input", () => { clearTimeout(SONG_SEARCH_TIMER); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const ov = $("songsearch");
    if (ov && !ov.hidden) { cancelSongSearch(); return; }
    const pop = $("songfilter");
    if (pop && !pop.hidden) closeSongFilter();
  });
}
if ($("song-cards")) bindSongControls();

/* ── 歌曲卡片 v4：按示意图逐条修（九宫格 0/40、字段对齐、白底打底、贴住 base） ── */
/* v20：一排 4 个——卡片是固定 744×494，按网格列宽整体 scale 成缩略图 */
function fitSongCards() { fitGrid($("song-cards")); }
window.addEventListener("resize", fitSongCards);

/* ── v26：删除用户（删除模式 + 确认对话框） ── */
var deleteMode = false;
var pendingDelete = null;

function setDeleteMode(on) {
  deleteMode = !!on;
  const btn = $("view-del");
  if (btn) {
    btn.textContent = deleteMode ? "退出删除" : "删除用户";
    btn.classList.toggle("danger", deleteMode);
  }
  renderUserCards();
}

function askDeleteUser(user) {
  const box = $("delmodal");
  if (!box) return;
  pendingDelete = user;
  const img = $("del-avatar");
  if (img) {
    const src = avatarSrc(user);
    if (src) { img.src = src; img.hidden = false; } else { img.removeAttribute("src"); img.hidden = true; }
  }
  const s = user.summary || {};
  $("del-name").textContent = user.name || user.id;
  $("del-info").innerHTML = `rating ${s.rating ?? "—"}<br>FC ${s.fullCombo ?? "—"} · AP ${s.allPerfect ?? "—"}` +
    `<br>${fmtTime(user.updatedAt)}`;
  $("del-text").textContent = "确定删除「" + (user.name || user.id) +
    "」？头像、成绩、统计会一起删掉，不能恢复。";
  box.hidden = false;
}

async function doDeleteUser() {
  if (!pendingDelete) return;
  const id = pendingDelete.id;
  const box = $("delmodal");
  try {
    const data = await api("/api/user-delete", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user: id}),
    });
    box.hidden = true;
    pendingDelete = null;
    if (data.info) setNote("view-new-note", data.info);
    if (currentUser === id) setCurrentUser(null);
    setDeleteMode(deleteMode);
    await loadUsers();
  } catch (err) {
    $("del-text").textContent = "删除失败：" + err.message;
  }
}

if ($("view-del")) {
  $("view-del").onclick = () => setDeleteMode(!deleteMode);
  $("del-cancel").onclick = () => { $("delmodal").hidden = true; pendingDelete = null; };
  $("del-ok").onclick = doDeleteUser;
  $("delmodal").onclick = (e) => {
    if (e.target.id === "delmodal") { $("delmodal").hidden = true; pendingDelete = null; }
  };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("delmodal").hidden) { $("delmodal").hidden = true; pendingDelete = null; }
  });
}

/* ── v27：大卡加载态 / 导入成绩按钮 / 新建用户弹窗 ─────────────────────────── */

/* 占位卡和真卡同高：真卡渲染出来后把高度记到占位卡上 */
function syncBigHeight() {
  const now = $("view-now"), box = $("view-loading");
  if (!now || !box || now.hidden) return;
  const h = now.offsetHeight;
  if (h > 120) box.style.minHeight = h + "px";
}

/* 大卡两种状态：ready = 真卡；其它 = 占位卡（显示传入的文案） */
function setBigState(state, msg, showImport) {
  const now = $("view-now"), box = $("view-loading");
  if (!now || !box) return;
  if (state === "ready") {
    now.hidden = false; box.hidden = true;
    syncBigHeight();                               // 记住真卡高度，占位卡按它撑开
    return;
  }
  now.hidden = true;
  box.hidden = false;
  const text = $("loading-text");
  if (text) text.textContent = msg || "统计中…";
  const btn = $("loading-import");
  if (btn) btn.hidden = !showImport;      // 没成绩的用户也能直接导入（按钮在大卡里时会被藏起来）
}

/* 导入成绩：选文件 → 跑导入链 → 等脚本结束 → 重新统计（全程大卡显示加载中） */
async function importScores() {
  const btn = $("import-btn");
  if (btn) btn.disabled = true;
  try {
    setBigState("loading", "请选择 MuNET 存档…");
    const picked = await api("/api/pick-file", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({mode: "score"}),
    });
    if (!picked.path) { await loadStats(); return; }        // 取消：恢复当前用户
    setBigState("loading", "正在导入成绩…");
    await api("/api/build", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({chain: scoresChainName(), scores: picked.path, user: currentUser}),
    });
    await waitRunDone((sec) => setBigState("loading", "正在导入成绩… " + sec + "s"));
    await loadUsers();
    await loadStats();
  } catch (err) {
    setBigState("loading", "导入失败：" + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function waitRunDone(onTick) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    setTimeout(() => {
      const timer = setInterval(async () => {
        let st;
        try { st = await api("/api/run"); } catch (e) { return; }
        if (st.running) { if (onTick) onTick(Math.round((Date.now() - t0) / 1000)); return; }
        clearInterval(timer);
        if (st.result && st.result.ok === false) reject(new Error(st.result.reason || "脚本失败"));
        else resolve(st);
      }, 800);
    }, 700);
  });
}

if ($("import-btn")) $("import-btn").onclick = importScores;
if ($("loading-import")) $("loading-import").onclick = importScores;

/* 新建用户：弹窗式 */
function openNewUser() {
  const box = $("newmodal");
  if (!box) return;
  $("new-name").value = "";
  setNote("new-note", "");
  box.hidden = false;
  $("new-name").focus();
}

async function createUserFromModal() {
  const name = $("new-name").value.trim();
  if (!name) { setNote("new-note", "先填个名字。"); return; }
  try {
    const data = await api("/api/user-create", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });
    $("newmodal").hidden = true;
    setNote("view-new-note", data.info || "完成");
    await loadUsers();
    if (data.user && data.user.id) selectUser(data.user.id);
  } catch (err) {
    setNote("new-note", "新建失败：" + err.message);
  }
}

if ($("view-new")) {
  $("view-new").onclick = openNewUser;
  $("new-cancel").onclick = () => { $("newmodal").hidden = true; };
  $("new-ok").onclick = createUserFromModal;
  $("new-name").addEventListener("keydown", (e) => { if (e.key === "Enter") createUserFromModal(); });
  $("newmodal").onclick = (e) => { if (e.target.id === "newmodal") $("newmodal").hidden = true; };
}

/* 首屏就停在歌曲页时补一次加载：必须放在所有 const 之后，避免 TDZ */
if ($("song-cards")) loadSongs();

/* ── v94：卡池预览（analysis.json 的可视化） ─────────────────────────────
   基本照搬歌曲浏览页：同一套 songcard / 翻页条。
   关掉统计与筛选，只留分析标签（熟练 / 推b35 / 推b15 / AP推分 / 弱点练习 / 准度练习 / 有挑战 / 待开荒），精确到谱。 */
const POOL_STATE = { labels: new Map(), eff: new Map(), label: "mastered", page: 1, pages: 1,
                     loaded: false, user: null, total: 0, sort: "dataid-desc", favorites: new Set(), blacklist: new Set(),
                     model: null };
const POOL_LABEL_NAMES = { mastered: "熟练", pushable_b35: "推b35", pushable_b15: "推b15",
                           ap: "AP推分", practice: "弱点练习", accuracy: "准度练习",
                           challenge: "有挑战", unplayed: "待开荒",
                           favorite: "收藏", blacklist: "黑名单" };
const POOL_PAGE_SIZE = 100;
const POOL_LABEL_DESC = {
  mastered: "个人拟合在能力区间内，且达成率 ≥100.6%",
  pushable_b35: "旧曲池里还能把 rating 推高的谱",
  pushable_b15: "新曲池里还能把 rating 推高的谱",
  ap: "推到 AP/AP+ 能提高 DX Rating 的谱",
  practice: "能力范围内、带弱项标签的练习谱",
  accuracy: "能力范围内、成绩已高但还没 AP 的准度练习",
  challenge: "比能力范围略高、仍在可信范围的谱",
  unplayed: "能力范围内还没打过的谱",
  favorite: "收藏的歌",
  blacklist: "不想打的歌",
};

function poolLabelDesc(label) {
  const fallback = POOL_LABEL_DESC[label] || "说明待定";
  if (label === "favorite" || label === "blacklist") return fallback;
  const model = POOL_STATE.model || {};
  const ab = model.abilityRange;
  const conf = model.range;
  if (!Array.isArray(ab) || ab.length !== 2 || !Array.isArray(conf) || conf.length !== 2) {
    return fallback;
  }
  const num = (v) => Number(v);
  const f = (v) => num(v).toFixed(3);
  const abLo = num(ab[0]), abHi = num(ab[1]), confHi = num(conf[1]);
  const mid = (abLo + abHi) / 2;
  const abText = f(abLo) + "~" + f(abHi);
  const midHigh = f(mid) + "~" + f(abHi);
  const lowMid = f(abLo) + "~" + f(mid);
  const highConf = f(abHi) + "~" + f(confHi);
  switch (label) {
    case "mastered": return "个人拟合在 " + abText + " 内 达成率 ≥100.6%";
    case "pushable_b35": return "推b35";
    case "pushable_b15": return "推b15";
    case "ap": return "推AP分";
    case "practice": return "个人拟合在 " + midHigh + " 内 有弱项标签";
    case "accuracy": return "个人拟合在 " + lowMid + " 内 高分歌曲";
    case "challenge": return "个人拟合在 " + highConf + " 内 略有难度";
    case "unplayed": return "个人拟合在 " + midHigh + " 内 还没打过的";
    default: return fallback;
  }
}

function fitPoolCards() { fitGrid($("pool-cards")); }

async function loadPool() {
  const box = $("pool-cards");
  if (!box) return;
  if (!Object.keys(usersById).length) { try { await loadUsers(); } catch (err) {} }
  // 加载动画延迟出现：命中「数据没变」时几十毫秒就回来，不闪
  const poolToken = (POOL_STATE.token = (POOL_STATE.token || 0) + 1);
  POOL_STATE.model = null;
  clearTimeout(POOL_STATE.loadingTimer);
  POOL_STATE.loadingTimer = setTimeout(() => {
    if (poolToken !== POOL_STATE.token) return;
    box.innerHTML = '<div class="loading">正在分析成绩…</div>';
    $("pool-count").textContent = "";
  }, 150);
  try {
    const [doc] = await Promise.all([
      api("/api/analysis?run=1&user=" + encodeURIComponent(currentUser || "")),
      ensureSongLibrary(),
    ]);
    clearTimeout(POOL_STATE.loadingTimer);        // 秒回就别再插占位了
    POOL_STATE.labels = new Map();
    POOL_STATE.eff = new Map();
    POOL_STATE.model = doc.fitModel || null;
    for (const c of doc.charts || []) {
      const labels = c.labels || [];
      if (!labels.length) continue;
      const key = c.internalId + ":" + c.chartid;
      POOL_STATE.labels.set(key, labels);
      POOL_STATE.eff.set(key, c.effectiveLevel || 0);
    }
    try {
      const fv = await api("/api/favorites?user=" + encodeURIComponent(currentUser || ""));
      POOL_STATE.favorites = new Set((fv.entries || []).map((e) => e.internalId + ":" + e.chartid));
    } catch (err) {
      POOL_STATE.favorites = new Set();
    }
    try {
      const bl = await api("/api/blacklist?user=" + encodeURIComponent(currentUser || ""));
      POOL_STATE.blacklist = new Set((bl.entries || []).map((e) => e.internalId + ":" + e.chartid));
    } catch (err) {
      POOL_STATE.blacklist = new Set();
    }
    POOL_STATE.loaded = true;
    POOL_STATE.user = currentUser || "";
    POOL_STATE.page = 1;
    renderPool();
  } catch (err) {
    clearTimeout(POOL_STATE.loadingTimer);
    box.innerHTML = '<div class="loading">分析失败：' + escapeHtml(err.message) + "</div>";
    $("pool-count").textContent = "";
  }
}

function renderPoolPagers(total, from, count) {
  renderPagers("data-ppager", POOL_STATE, total, POOL_PAGE_SIZE);
}

function poolJumpPage(el) {
  if (!el) return;
  const n = parseInt(el.value, 10);
  el.value = "";
  if (!Number.isFinite(n)) { el.focus(); return; }
  const target = Math.min(Math.max(1, n), POOL_STATE.pages || 1);
  if (target === POOL_STATE.page) { el.focus(); return; }
  POOL_STATE.page = target;
  renderPool();
}

export async function refreshPoolMarks() {
  const poolPage = document.querySelector('[data-page="pool"]');
  const onPool = poolPage && !poolPage.hidden;
  const y = window.scrollY;
  const page = POOL_STATE.page;
  const user = currentUser || "";
  try {
    const fv = await api("/api/favorites?user=" + encodeURIComponent(user));
    POOL_STATE.favorites = new Set((fv.entries || []).map((e) => e.internalId + ":" + e.chartid));
  } catch (err) { /* 忽略：保持旧集合 */ }
  try {
    const bl = await api("/api/blacklist?user=" + encodeURIComponent(user));
    POOL_STATE.blacklist = new Set((bl.entries || []).map((e) => e.internalId + ":" + e.chartid));
  } catch (err) { /* 同上 */ }
  if (!onPool || !POOL_STATE.loaded) return;
  POOL_STATE.page = page;
  renderPool();
  requestAnimationFrame(function () { window.scrollTo(0, y); });
}

function renderPool() {
  const box = $("pool-cards");
  const count = $("pool-count");
  if (!box) return;
  const label = POOL_STATE.label;

  // 直接拿歌曲浏览的曲库数据，按分析结果的标签过滤 —— 不另建一份卡片数据
  const songs = [];
  let chartHits = 0;
  for (const song of SONG_STATE.songs) {
    const sheets = [];
    for (const sh of song.sheets || []) {
      const key = song.internalId + ":" + sh.chartid;
      const labels = POOL_STATE.labels.get(key);
      if (label === "favorite") {
        if (!POOL_STATE.favorites.has(key)) continue;
      } else if (label === "blacklist") {
        if (!POOL_STATE.blacklist.has(key)) continue;
      } else if (!labels || labels.indexOf(label) < 0) continue;
      sheets.push(sh);
      chartHits += 1;
    }
    if (!sheets.length) continue;                     // 这标签下这首一张谱都不命中
    const eff = Math.max.apply(null, sheets.map(
      (sh) => POOL_STATE.eff.get(song.internalId + ":" + sh.chartid) || 0));
    songs.push(Object.assign({}, song, { sheets, _eff: eff }));
  }
  const lv = (s) => Math.max.apply(null, (s.sheets || []).map((sh) => Number(sh.level) || 0));
  const byIdDesc = (a, b) => (b.dataid - a.dataid) || (lv(b) - lv(a));
  const byIdAsc = (a, b) => (a.dataid - b.dataid) || (lv(b) - lv(a));
  const sorters = {
    "dataid-desc": byIdDesc,
    "dataid-asc": byIdAsc,
    "level-desc": (a, b) => (lv(b) - lv(a)) || byIdDesc(a, b),
    "level-asc": (a, b) => (lv(a) - lv(b)) || byIdDesc(a, b),
    "title": (a, b) => String(a.title || "").localeCompare(String(b.title || ""), "ja") || byIdDesc(a, b),
  };
  songs.sort(sorters[POOL_STATE.sort] || byIdDesc);

  POOL_STATE.pages = Math.max(1, Math.ceil(songs.length / POOL_PAGE_SIZE));
  POOL_STATE.page = Math.min(Math.max(1, POOL_STATE.page), POOL_STATE.pages);
  const from = (POOL_STATE.page - 1) * POOL_PAGE_SIZE;
  const slice = songs.slice(from, from + POOL_PAGE_SIZE);

  renderCardPage(box, slice);
  box.classList.toggle("is-empty", !slice.length);

  if (count) {
    count.textContent = POOL_LABEL_NAMES[label] + " · " + chartHits + " 谱 / " + songs.length + " 首";
    const desc = $("pool-desc");
    if (desc) desc.textContent = poolLabelDesc(label);
  }
  renderPoolPagers(songs.length, from, slice.length);
  document.querySelectorAll("#pool-chips .poolchip").forEach((b) => {
    b.classList.toggle("on", b.dataset.label === label);
  });
}


document.querySelectorAll("#pool-chips .poolchip").forEach((b) => {
  b.onclick = () => { POOL_STATE.label = b.dataset.label; POOL_STATE.page = 1; renderPool(); };
});
{
  const sel = $("pool-sort");
  if (sel) sel.onchange = (e) => { POOL_STATE.sort = e.target.value; POOL_STATE.page = 1; renderPool(); };
}
fillPagers("data-ppager");
attachPagers("data-ppager", POOL_STATE, renderPool);
window.addEventListener("resize", () => { if (!$("pool-cards")?.hidden) fitPoolCards(); });



export { BASE_BY_FAMILY, deleteMode, COMBO_FILE, DIFF_BTN, DIFF_BTN_FILE, DIFF_SHEET, PLATE_CRITERIA, POOL_LABEL_NAMES, POOL_PAGE_SIZE, POOL_STATE, RANK_FILE, SLOT, SONG_COMBO_NAMES, SONG_DIFFS, SONG_FAMILY_RANK, SONG_PAGE_SIZE, SONG_PLATE_GROUP_LABEL, SONG_PLATE_TIERS, SONG_PLATE_TIER_RANK, SONG_PLATE_VER_TOKENS, SONG_SEARCH_SNAPSHOT, SONG_SEARCH_TIMER, SONG_STATE, SONG_SYNC_NAMES, SYNC_FILE, TYPE_BADGE, activeCriteria, askDeleteUser, bindSongControls, cancelSongSearch, chartDone, closeSongFilter, commitSongSearch, createUserFromModal, criteriaText, doDeleteUser, fitPoolCards, fitSongCards, glyph, glyphIndex, glyphs, hideSongSearch, importScores, jumpSongPage, loadPlates, loadPool, loadSongs, openNewUser, openSongFilter, openSongSearch, plateChip, plateCriteria, plateGroupKey, plateSearchKey, plateVerTokens, renderPlateList, renderPool, renderSfOrderBtn, renderSfVersions, renderSongPagers, renderSongSearchBtn, renderSongStats, renderSongVerBtn, renderSongs, scoreSlots, setBigState, setDeleteMode, setFilterTab, sheetInPlate, songCard, songDiffIndex, songFilteredList, songInPlate, songMatches, songMaxLevel, songSorter, songStats, songViewSheets, syncBigHeight, syncSongCriteriaControls, syncSongFamilyControls, waitRunDone };
