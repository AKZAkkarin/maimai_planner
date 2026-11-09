import { api } from "./core.js";
import { VERSION_IMAGES, currentUser } from "./stats.js";
import { BASE_BY_FAMILY, COMBO_FILE, DIFF_BTN_FILE, DIFF_SHEET, RANK_FILE, SONG_COMBO_NAMES, SONG_SYNC_NAMES, SYNC_FILE, TYPE_BADGE, glyphIndex, refreshPoolMarks } from "./songlist.js";

// 用途：单张歌曲卡（songCard / 难度行 / 药丸底图）+ 谱面页 / 曲目页的渲染与交互
// 状态：现行（从 app.js 拆出来；index.html 在 app.js 之后加载本文件）
// 依赖：app.js 里的全局（$ / api / currentUser / escapeHtml 等），调用时才查找，所以只要两个文件都加载完就行。
function glyph(sheet, index, h, extraClass) {
  const w = (h * 48) / 60;
  const x = -(index % 4) * w;
  const y = -Math.floor(index / 4) * h;
  return `<span class="gly ${extraClass || ""}" style="width:${w}px;height:${h}px;`
    + `background-image:url('/ui/Number/${sheet}.png');`
    + `background-size:${w * 4}px ${h * 4}px;background-position:${x}px ${y}px"></span>`;
}

function glyphs(sheet, text, h) {
  return [...String(text)].map((ch) => { const i = glyphIndex(ch); return i < 0 ? "" : glyph(sheet, i, h); }).join("");
}

// 白底 + 贴图盖上去（贴图加载失败就只剩白底）
function slot(inner, cls) {
  return `<span class="fslot ${cls || ""}">${inner || ""}</span>`;
}

function scoreCells(sheet, info) {
  const rank = info.rank == null ? "" 
    : `<img src="/ui/rank/UI_GAM_Rank_${RANK_FILE[info.rank]}.png" alt="">`;
  const dx = info.dxscore == null ? ""
    : `${glyph(sheet, 10, 26, "plus")}<img src="/ui/dxScoreSmall/UI_MSS_Rival_DXscoreIcon_0${info.dxscore}.png" alt="">`;
  const combo = COMBO_FILE[info.combo] ? `<img src="/ui/status/UI_MSS_MBase_Icon_${COMBO_FILE[info.combo]}.png" alt="">` : "";
  const sync = SYNC_FILE[info.sync] ? `<img src="/ui/status/UI_MSS_MBase_Icon_${SYNC_FILE[info.sync]}.png" alt="">` : "";
  return [slot(rank, "f-rank"), slot(dx, "f-dx"), slot(combo, "f-combo"), slot(sync, "f-sync")].join("");
}

/* ── 歌曲卡片 v5：药丸三层背景（中间拉伸、左右帽不被拉），字段列对齐 ── */
function rowHtml(bg, sheet, level, info) {
  const lv = level == null ? "" : Number(level).toFixed(1);
  const btnStyle = '<div class="sc-row" style="--btn:url(\'/ui/musicLib/' + bg + '\')">';
  return btnStyle
    + '<span class="f-lv">' + glyph(sheet, 14, 22) + '<span class="lvnum">' + glyphs(sheet, lv, 30) + '</span></span>'
    + scoreCells(sheet, info)
    + "</div>";
}

/* ── 歌曲卡片 v6：逐条按要求重做（药丸三层 canvas 拉伸 / 字段对齐 / 白底垫底 / 紧凑定数） ── */
const G6_INK = {
  "0": [3, 42, 4, 56], "1": [7, 33, 6, 55], "2": [5, 42, 4, 55], "3": [3, 41, 5, 56], "4": [3, 43, 5, 55],
  "5": [4, 42, 6, 56], "6": [3, 42, 5, 56], "7": [5, 42, 6, 56], "8": [3, 43, 5, 56], "9": [3, 42, 5, 56],
  "+": [9, 36, 2, 28], "-": [12, 35, 21, 34], ",": [17, 31, 29, 51], ".": [17, 30, 30, 44], "LV": [5, 43, 27, 55],
};

// 按字形墨迹裁格：数字紧凑不粘连
function g6(sheet, ch, h) {
  const ink = G6_INK[ch];
  if (!ink) return "";
  const idx = "0123456789+-,.LV".indexOf(ch);
  const col = idx % 4, row = Math.floor(idx / 4);
  const iw = ink[1] - ink[0] + 1, ih = ink[3] - ink[2] + 1;
  const s = h / 52;
  const w = iw * s, gh = ih * s;
  const px = -(col * 48 + ink[0]) * s, py = -(row * 60 + ink[2]) * s;
  return '<span class="gly" style="width:' + w + 'px;height:' + gh + 'px;background-image:url(\'/ui/Number/' + sheet + '.png\');'
    + 'background-size:' + (192 * s) + 'px ' + (240 * s) + 'px;background-position:' + px + 'px ' + py + 'px"></span>';
}

function g6s(sheet, text, h) {
  let out = "";
  for (const ch of String(text)) out += g6(sheet, ch, h);
  return out;
}

// 药丸三层：左帽 / 中间 1 像素拉伸 / 右帽（Btn 墨迹 66×53，帽宽 27）
const pillCache = {};
const pillBusy = {};
function pillDataUrl(name) {
  if (pillCache[name]) return pillCache[name];
  if (pillBusy[name]) return null;              // 同一张 btn 正在加载时别重复建 canvas（100 张卡会重复 400 次）
  pillBusy[name] = true;
  const c = document.createElement("canvas");
  c.width = 380; c.height = 55;
  const g = c.getContext("2d");
  const im = new Image();
  im.onload = () => {
    const s = 55 / 53;
    g.drawImage(im, 33, 12, 13, 53, 0, 1, 380 - 28, 55);      // 左端直切（藏进 base 下面）
    g.drawImage(im, 45, 12, 27, 53, 380 - 28, 1, 28, 55);    // 只有右帽
    pillCache[name] = c.toDataURL();
    pillBusy[name] = false;
    document.querySelectorAll(".sc-row[data-btn='" + name + "']").forEach((r) => {
      r.style.backgroundImage = "url(" + pillCache[name] + ")";
    });
  };
  im.onerror = () => { pillBusy[name] = false; };
  im.src = "/ui/musicLib/" + name;
  return null;
}

function slot6(inner) {
  return '<span class="fslot">' + (inner || "") + '</span>';
}

function marqueeIfLong(box) {
  if (!box) return;
  const span = box.querySelector("span");
  if (!span) return;
  if (span.scrollWidth > box.clientWidth) {
    const text = span.textContent;
    span.innerHTML = text + '<i></i>' + text;
    span.classList.add("marq");
    box.classList.add("marqbox");                          // 让文字从左边开始滚，而不是居中溢出
    const dist = span.scrollWidth / 2 + 32;               // 一个文本块 + 间隔（见 @keyframes marq）
    span.style.animationDuration = Math.max(7, Math.round(dist / 46)) + "s";   // ≈46 原生 px/s
  }
}

/* ── 歌曲卡片 v7：白底形状贴图标（rank 大圆角、dx 方、连击/同步圆形）、层级与对齐 ──
   注：g6 的 h 是「52 单位字形 em」的高度，不是墨迹高度。数字墨迹高≈53/52，基本等于 h；
   LV 墨迹只有 29/52，所以要让 LV 看起来 19 原生高，h 得给 19*52/29≈34。 */
const LV_EM = 31;     // LV 字样（墨迹≈17.3 原生高，正好是数字 60%：LV墨迹=29/52·EM，数字墨迹=53/52·EM）
const DIGIT_EM = 28;  // 定数数字（墨迹约 28 原生高）
// v45：裁切框改成贴图的真实 alpha 墨迹 (4,8)-(39,49)，原来 7/36/11/47 会切掉右边缘和上下角
const UTG_Q = { file: "UI_NUM_UTG_Question", W: 48, H: 60, x0: 4, x1: 39, y0: 8, y1: 49 };
// UTG 定数后缀的尺寸（按根目录参考图 参考图-非常干净.png；它和贴图 1:1，已按 alpha 核过）：
//   "+" 墨迹 23×23 = 贴图原尺寸 → 和数字同一 em，直接用 g6(sheet, "+", DIGIT_EM)
//   "?" 墨迹 25×31 = 贴图 28×35 的 0.89 → qGlyph 的参数是 alpha 框高，给 20
const UTG_Q_INK = 20;
function qGlyph(inkH) {                        // 按贴图真实墨迹裁，inkH 直接给墨迹高
  const iw = UTG_Q.x1 - UTG_Q.x0 + 1, ih = UTG_Q.y1 - UTG_Q.y0 + 1;
  const s = inkH / ih;
  const url = "/ui/Number/" + UTG_Q.file + ".png";   // 文件名里没有空格/括号，url() 不用引号（带引号会把 style 属性截断）
  const style = "width:" + (iw * s).toFixed(2) + "px;height:" + (ih * s).toFixed(2) + "px;"
    + "background-image:url(" + url + ");"
    + "background-size:" + (UTG_Q.W * s).toFixed(2) + "px " + (UTG_Q.H * s).toFixed(2) + "px;"
    + "background-position:" + (-UTG_Q.x0 * s).toFixed(2) + "px " + (-UTG_Q.y0 * s).toFixed(2) + "px";
  return '<span class="lvcell q" style="' + style + '"></span>';
}

function levelHtml(sheet, level) {
  if (level == null || level === "") return "";
  // 普通难度是数字 → "13.7"；utage 是 "12?""14+?" 这种文本 → 去掉问号按 12 / 14+ 画
  const rawLevel = typeof level === "number" ? level.toFixed(1) : String(level).trim();
  const hasQ = /[?？]\s*$/.test(rawLevel);        // utage 的 "12?"：问号用 dataUI/Number 的问号贴图
  let text = rawLevel.replace(/[?？]\s*$/, "");
  if (!/^\d{1,2}(\+)?(\.\d)?$/.test(text)) text = Number(level).toFixed(1);
  const parts = text.split(".");
  const plus = /\+$/.test(parts[0]);
  const intPart = plus ? parts[0].replace("+", "") : parts[0];
  const ints = intPart.length < 2 ? ["", intPart] : intPart.split("");   // 高位留空，不补 0
  // v16：每格宽 = 该字形墨迹宽（按 DIGIT_EM 换算），数字紧贴 LV；十位留空只占一个格间距（.lvfield 的 gap）
  const cell = (ch, cls) => {
    if (!ch) return '<span class="lvcell blank ' + (cls || "") + '"></span>';
    const ink = G6_INK[ch] || [0, 51, 0, 51];
    const w = ((ink[1] - ink[0] + 1) * DIGIT_EM / 52).toFixed(2);
    return '<span class="lvcell ' + (cls || "") + '" style="width:' + w + 'px">' + g6(sheet, ch, DIGIT_EM) + '</span>';
  };
  let html = ints.map((c) => cell(c)).join("");
  // utage 的 "+" / "?"：占「小数点 + 小数位」那一条（宽 = dot 宽 + 数字宽），
  // 这样个位/十位和普通谱的 digit 位置完全一致；+ 在上、? 在下（参考图）
  const dotW = (G6_INK["."][1] - G6_INK["."][0] + 1) * DIGIT_EM / 52;
  const digitW = (G6_INK["7"][1] - G6_INK["7"][0] + 1) * DIGIT_EM / 52;
  if (plus || hasQ) {
    // suff 高 = 个位字形格高（不是写死的 28）：这样 + / ? 的 top/bottom 都相对同一个「个位顶 / 基线」
    const lastInk = G6_INK[ints[ints.length - 1]] || G6_INK["0"];
    const hDigit = (lastInk[3] - lastInk[2] + 1) * DIGIT_EM / 52;
    html += '<span class="lvcell suff" style="width:' + (dotW + 1.5 + digitW).toFixed(2) + 'px;height:' + hDigit.toFixed(2) + 'px">'
      + (plus ? '<span class="splus">' + g6(sheet, "+", DIGIT_EM) + '</span>' : "")
      + (hasQ ? qGlyph(UTG_Q_INK) : "")
      + '</span>';
  }
  if (parts.length > 1) html += cell(".", "dot") + cell(parts[1] || "0");
  return '<span class="lvfield">' + html + '</span>';
}

function row6(sh, family) {
  const raw = String(sh.difficulty || "").toLowerCase();
  const isUtage = family === "utage" || family === "utage2p" || raw.indexOf("utage") === 0;
  const key = isUtage ? "utage" : raw;                 // utage 的难度名是【表】【裏】这种，统一映射
  const sheet = DIFF_SHEET[key] || "UI_NUM_MAS";
  const bg = DIFF_BTN_FILE[key] || "Btn_MAS.png";
  const levelArg = (isUtage && sh.levelText) ? sh.levelText : sh.level;
  const rank = sh.rank == null ? "" : '<img class="sc-icon rk" src="/ui/rank/UI_GAM_Rank_' + RANK_FILE[sh.rank] + '.png" alt="">';
  const dx = (sh.dxscore >= 1 && sh.dxscore <= 5)
    ? '<img class="sc-icon dx" src="/ui/dxScoreSmall/UI_MSS_Rival_DXscoreIcon_0' + sh.dxscore + '.png" alt="">' : "";
  const combo = COMBO_FILE[sh.combo] ? '<img class="sc-icon" src="/ui/status/UI_MSS_MBase_Icon_' + COMBO_FILE[sh.combo] + '.png" alt="">' : "";
  const sync = SYNC_FILE[sh.sync] ? '<img class="sc-icon" src="/ui/status/UI_MSS_MBase_Icon_' + SYNC_FILE[sh.sync] + '.png" alt="">' : "";
  pillDataUrl(bg);
  return '<div class="sc-row" data-chartid="' + (sh.chartid == null ? "" : sh.chartid) + '" data-btn="' + bg + '" data-diff="' + (sh.difficulty || "") + '" data-level="' + (sh.level == null ? "" : sh.level) + '">'
    + '<span class="f-lv">' + g6(sheet, "LV", LV_EM) + levelHtml(sheet, levelArg) + '</span>'
    + '<span class="f-rank"><span class="rslot">' + rank + '</span></span>'
    + '<span class="f-dx"><span class="fslot dxslot">' + dx + '</span></span>'
    + '<span class="f-combo"><span class="fslot circle">' + combo + '</span></span>'
    + '<span class="f-sync"><span class="fslot circle">' + sync + '</span></span>'
    + '</div>';
}

/* v48：宴谱底板上的难度汉字。数据里宴谱难度是【協】【宴】【蔵】这种，取括号里的字 */
function utageKanjiOf(song) {
  if (song.family !== "utage" && song.family !== "utage2p") return "";
  const d = String(((song.sheets || [])[0] || {}).difficulty || "");
  const m = d.match(/[【\[]\s*([^】\]]+?)\s*[】\]]/);
  return m ? m[1] : "";
}

function songCard(song) {
  const card = document.createElement("div");
  card.className = "songcard";
  const base = '/ui/musicLib/' + (BASE_BY_FAMILY[song.family] || "Base_DUMMY.png");
  const rows = (song.sheets || []).map((sh) => row6(sh, song.family)).join("")
    // v96：以「5 个难度」为标尺 —— 不足 5 行的补空行占位（4 难度留第 5 位；筛选后只剩几行也自动往上补）
    + '<div class="sc-row sc-row-empty"></div>'.repeat(Math.max(0, 5 - (song.sheets || []).length));
  const kanji = utageKanjiOf(song);
  // v49：utage2p 的类型徽标是两块叠出来的 —— 蓝底板 Type_UTG 在上、Type_UTG_2p 在其下面往右伸出
  const pair = song.family === "utage2p";
  const typeSrc = pair ? "Type_UTG.png" : TYPE_BADGE[song.family];
  card.dataset.title = song.title || "";
  card.dataset.artist = song.artist || "";
  card.dataset.dataid = song.dataid == null ? "" : song.dataid;
  card.dataset.internalId = song.internalId == null ? "" : song.internalId;
  card.__song = song;
  card.innerHTML = '<div class="songwrap"><div class="songunit">'
    + '<div class="songframe"></div>'
    + '<div class="sc-base" style="background-image:url(\'' + base + '\')">'
    + (song.jacket ? '<img class="sc-jacket" src="' + song.jacket + '" alt="">' : '<span class="sc-jacket"></span>')
    + '<div class="sc-title"><span>' + (song.title || "") + '</span></div>'
    + '<div class="sc-artist"><span>' + (song.artist || "") + '</span></div></div>'
    + '<div class="sc-rows">'
    + '<div class="sc-type-row"><span class="sc-type-badge' + (pair ? ' pair' : '') + '">'
    + (pair ? '<img class="sc-type2p" src="/ui/musicLib/Type_UTG_2p.png" alt="">' : '')
    + '<img class="sc-type" src="/ui/musicLib/' + typeSrc + '" alt="">'
    + (kanji ? '<span class="sc-type-kanji">' + kanji + '</span>' : '')
    + '</span></div>'
    + rows + '</div>'
    + '</div></div>'
    + (song.internalId == null ? "" : '<span class="sc-id">' + song.internalId + '</span>');
  return card;
}

/* ── 谱面详情 / 曲目详情（v69）：点难度行 → 单谱页；点 base 图 → 曲目大页 ── */
const SONG_DETAIL_CACHE = new Map();
const DIFF_LABEL = { basic: "BAS", advanced: "ADV", expert: "EXP", master: "MAS", remaster: "REM" };
const DIFF_CLASS = { basic: "bas", advanced: "adv", expert: "exp", master: "mas", remaster: "rem" };
const SERVER_LABEL = { jp: "日服", cn: "国服", intl: "国际服", usa: "美服", local: "本地" };
let SONG_PAGE = null;                       // 曲目大页当前加载的详情

function diffOf(sheet, family) {
  if (family === "utage" || family === "utage2p") return { name: "UTG", cls: "utage" };
  const key = String((sheet && sheet.difficulty) || "").toLowerCase();
  return { name: DIFF_LABEL[key] || key.toUpperCase(), cls: DIFF_CLASS[key] || "mas" };
}

async function fetchSongDetail(internalId) {
  const key = String(internalId) + "|" + (currentUser || "");
  if (SONG_DETAIL_CACHE.has(key)) return SONG_DETAIL_CACHE.get(key);
  const data = await api("/api/song?internalId=" + encodeURIComponent(internalId)
    + "&user=" + encodeURIComponent(currentUser || ""));
  SONG_DETAIL_CACHE.set(key, data.song);
  if (data.song && data.song.fitModel) LAST_FIT_MODEL = data.song.fitModel;
  return data.song;
}

function fmtAch(v) { return v == null ? "-" : (Number(v) / 10000).toFixed(4) + "%"; }
function fmtLevel(v) { return v == null ? "-" : Number(v).toFixed(1); }
function escapeHtml(text) {
  return String(text == null ? "" : text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* 历史定数：historyLevelValue 是旧→新；只保留「定数发生变化」的版本，再按新→旧排成两列 */
function historyHtml(sheet) {
  const hist = (sheet.historyLevelValue || []).filter((h) => h && h.value != null);
  if (!hist.length) return "";
  const changed = [];
  let prev = null;
  for (const h of hist) {
    if (prev !== null && Number(h.value) === Number(prev)) continue;   // 与上一版相同：跳过
    changed.push(h);
    prev = h.value;
  }
  changed.reverse();                                                   // 新 → 旧
  const rows = changed.map((h) => '<span class="hv">' + escapeHtml(h.version || "-") + "</span>"
    + "<b>" + fmtLevel(h.value) + "</b>").join("");
  return '<div class="sm-hist"><div class="sm-hist-title">历史定数</div>'
    + '<div class="sm-hist-rows">' + rows + "</div></div>";
}

function tagChips(arr) {
  if (!arr || !arr.length) return '<i>无</i>';
  return arr.map((t) => '<span class="sm-tag">' + escapeHtml(t.zh || t.id) + "</span>").join("");
}

/* 单谱页：定数(本地)/连击/双人/成绩 · DX Rating 定数/可用服 · 三组标签 · 谱师/加入时间 */
function renderChartBody(song, sheet) {
  const d = diffOf(sheet, song.family);
  const servers = (sheet.serverIds || []).map((k) => SERVER_LABEL[k] || k).join(" / ") || "-";
  const combo = sheet.combo ? (SONG_COMBO_NAMES[sheet.combo] || "") : "";
  const sync = sheet.sync ? (SONG_SYNC_NAMES[sheet.sync] || "") : "";
  return '<div class="sm-chart">'
    + '<div class="sm-chart-head">'
    + '<span class="sm-diff q-' + d.cls + '">' + d.name + "</span>"
    + '<span class="sm-chart-lv">' + escapeHtml(sheet.levelText || "") + "</span>"
    + '<span class="sm-chart-song">' + escapeHtml(song.title) + "<small>" + escapeHtml(song.artist) + "</small></span>"
    + "</div>"
    + '<div class="sm-line">'
    + "<span>成绩 <b>" + fmtAch(sheet.achievement) + "</b></span>"
    + "<span>连击 <b>" + (combo || "-") + "</b></span>"
    + "<span>双人 <b>" + (sync || "-") + "</b></span>"
    + "<span>DX分 <b>" + (sheet.deluxscoreMax == null ? "-" : sheet.deluxscoreMax) + "</b></span>"
    + "<span>游玩次数 <b>" + (sheet.playCount == null ? "-" : sheet.playCount) + "</b></span>"
    + "</div>"
    + '<div class="sm-line">'
    + "<span>定数（本地）<b>" + fmtLevel(sheet.localLevel) + "</b></span>"
    + "<span>DX Rating 定数<b>" + fmtLevel(sheet.dxratingLevel) + "</b></span>"
    + "</div>"
    + historyHtml(sheet)
    + '<div class="sm-line">'
    + '<span>个人拟合<b>'
    + (sheet.fitLevel == null ? "—"
        : '<button type="button" class="sm-fitbtn" data-act="fit-info">' + Number(sheet.fitLevel).toFixed(3) + "</button>")
    + "</b></span>"
    + '<span>水鱼拟合<b>' + (sheet.fitDiff == null ? "—" : Number(sheet.fitDiff).toFixed(3)) + "</b></span>"
    + "</div>"
    + '<div class="sm-line sm-tags">'
    + '<span class="sm-taggroup t-pat"><b>配置</b>' + tagChips(sheet.PatternsTag) + "</span>"
    + '<span class="sm-taggroup t-diff"><b>难度</b>' + tagChips(sheet.DifficultyTag) + "</span>"
    + '<span class="sm-taggroup t-eval"><b>评价</b>' + tagChips(sheet.EvaluationTag) + "</span>"
    + "</div>"
    + '<div class="sm-line">'
    + "<span>谱师<b>" + escapeHtml(sheet.noteDesigner || "-") + "</b></span>"
    + "<span>加入时间<b>" + escapeHtml(sheet.releaseDate || "-") + "</b></span>"
    + "</div>"
    + '<div class="sm-line">'
    + "<span>可用服<b>" + escapeHtml(servers) + "</b></span>"
    + "</div>"
    + '<div class="sm-line">'
    + "<span>黑名单<b>" + (sheet.blacklisted ? "已加入（" + blDurText(sheet.blacklist) + "）" : "未加入") + "</b></span>"
    + (sheet.blacklisted ? '<button class="sm-bl" data-act="bl-remove">移出黑名单</button>'
                        : '<button class="sm-bl" data-act="bl-add">加入黑名单</button>')
    + '<div class="sm-action-row"><button class="sm-bl" data-act="score-edit">编辑成绩</button>'
    + '<button class="sm-bl sm-fav' + (sheet.favorite ? " on" : "") + '" data-act="fav-toggle">'
    + (sheet.favorite ? "取消收藏" : "加入收藏") + "</button>"
    + '<button class="sm-bl sm-push" data-act="push-machine">推送到机台</button></div>'
    + "</div>"
    + "</div>";
}

/* 曲目大页：左上角 internalId + 曲绘 + 版本贴图 + 曲名/艺术家/类别/类型/BPM + 难度列表 + 别名 */
/* 定数拆成三格：十位 / 个位 / +，保证竖着对齐（"4" 落在个位格、"13+" 落在十位+个位+加号） */
function levelCells(text) {
  const m = /^(\d{0,1})(\d)(\+?)$/.exec(String(text == null ? "" : text).trim());
  if (!m) return escapeHtml(text == null ? "" : String(text));
  return "<i>" + m[1] + "</i><i>" + m[2] + "</i><i>" + m[3] + "</i>";
}

function renderSongBody(song) {
  const verCode = VERSION_IMAGES[song.version];
  const verImg = verCode
    ? '<img class="sf2-ver" src="/ui/version/UI_CMN_TabTitle_MaimaiTitle_' + verCode + '.png" alt="' + escapeHtml(song.version) + '">'
    : '<span class="sf2-ver-text">' + escapeHtml(song.version || "") + "</span>";
  const diffs = (song.sheets || []).map((sh) => {
    const d = diffOf(sh, song.family);
    const ach = sh.achievement == null ? "" : fmtAch(sh.achievement);
    const combo = sh.combo ? (SONG_COMBO_NAMES[sh.combo] || "") : "";
    const sync = sh.sync ? (SONG_SYNC_NAMES[sh.sync] || "") : "";
    return '<button class="sf2-diff" data-chartid="' + sh.chartid + '">'
      + '<span class="q-' + d.cls + '">' + d.name + "</span>"
      + '<span class="sf2-lv">' + levelCells(sh.levelText) + "</span>"
      + '<span class="sf2-ach">' + ach + "</span>"
      + (combo ? '<span class="sf2-mark">' + combo + "</span>" : "<span></span>")
      + (sync ? '<span class="sf2-mark">' + sync + "</span>" : "<span></span>")
      + "</button>";
  }).join("");
  const aliases = (song.aliasesTag || []).length
    ? escapeHtml(song.aliasesTag.join(" / "))
    : "（无）";
  return '<div class="sf2-top">'
    + '<img class="sf2-jacket" src="' + (song.jacket || "") + '" alt="">'
    + '<div class="sf2-info">'
    + '<span class="sf2-id">' + escapeHtml(song.internalId) + "</span>"
    + '<div class="sf2-title" data-title="' + escapeHtml(song.title || "") + '" title="点击复制曲名"><span>' + escapeHtml(song.title) + "</span></div>"
    + '<div class="sf2-artist">' + escapeHtml(song.artist) + "</div>"
    + '<div class="sf2-meta">' + escapeHtml(song.category || "-") + "</div>"
    + '<div class="sf2-meta">类型：' + escapeHtml(String(song.family || "").toUpperCase()) + "</div>"
    + '<div class="sf2-meta">BPM ' + (song.bpm == null ? "-" : escapeHtml(song.bpm)) + "</div>"
    + "</div>"
    + '<div class="sf2-verline">' + verImg + "</div>"
    + "</div>"
    + '<div class="sf2-diffs">' + (diffs || '<p class="sm-hint">没有谱面数据</p>') + "</div>"
    + '<div class="sf2-alias"><b>别名</b>' + aliases + "</div>";
}

/* v98：黑名单（1d/3d/7d/30d/永久），存 dataUser/<uuid>/blacklist.json */
const BL_DUR_TEXT = { "1d": "1 天", "3d": "3 天", "7d": "7 天", "30d": "30 天", "forever": "永久" };
let BL_PENDING = null;                       // 当前谱面页 {song, sheet}
function blDurText(entry) {
  if (!entry) return "-";
  if (entry.duration === "forever" || entry.expiresAt === null) return "永久";
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(entry.expiresAt || ""))) return "到期 " + entry.expiresAt;
  const t = entry.expiresAt ? new Date(entry.expiresAt) : null;
  if (!t || isNaN(t.getTime())) return BL_DUR_TEXT[entry.duration] || entry.duration || "-";
  const pad = (n) => String(n).padStart(2, "0");
  return "到期 " + t.getFullYear() + "-" + pad(t.getMonth() + 1) + "-" + pad(t.getDate());
}

function rerenderChartPage() {
  const body = document.getElementById("sm-body");
  if (!body || !BL_PENDING) return;
  body.innerHTML = renderChartBody(BL_PENDING.song, BL_PENDING.sheet);
}

function openBlacklistDialog() {
  const dlg = document.getElementById("bldlg");
  if (!dlg || !BL_PENDING) return;
  const t = document.getElementById("bl-title");
  if (t) t.textContent = BL_PENDING.song.title + " · " + (BL_PENDING.sheet.levelText || "") + "：选一个时长";
  dlg.hidden = false;
}

function fitScoreWeight(ach) {
  const pts = [[0, 1.5], [98, 1.5], [99, 1.0], [100, 0.5], [100.4, 0], [100.6, 0], [101, -1], [102, -1]];
  if (ach <= pts[0][0]) return pts[0][1];
  for (let i = 0; i + 1 < pts.length; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    if (ach >= x0 && ach <= x1) return x1 === x0 ? y1 : y0 + (ach - x0) * (y1 - y0) / (x1 - x0);
  }
  return pts[pts.length - 1][1];
}

function renderFitInfo(song, sheet, model) {
  model = model || {};
  const names = (arr) => (arr || []).map((t) => (typeof t === "string" ? t : (t.zh || t.name || ""))).filter(Boolean);
  const all = names(sheet.PatternsTag).concat(names(sheet.DifficultyTag)).concat(names(sheet.EvaluationTag));
  const gamma = model.tagU || {};
  const tier = sheet.fitTier || 0;
  const tierMult = (g) => {
    if (tier === 2) {
      if (g <= -0.05) return 1.5;
      if (g < 0) return 1.2;
      return 1.0;
    }
    if (tier === 3) return g < 0 ? 0.5 : 1.0;
    return 1.0;
  };
  const rows = [];
  const fmt = (v, n) => (v >= 0 ? "+" : "") + Number(v).toFixed(n == null ? 3 : n);
  const clip = (v) => Math.max(-0.3, Math.min(0.3, v));

  const official = Number(sheet.localLevel || sheet.levelText || 0);
  const water = sheet.fitDiff == null ? null : Number(sheet.fitDiff);
  const evalLevel = sheet.fitEvalLevel == null ? null : Number(sheet.fitEvalLevel);
  const evalText = evalLevel == null ? "—" : evalLevel.toFixed(3);
  const waterKind = sheet.waterKind || "";
  const waterPrior = sheet.waterPrior == null ? null : Number(sheet.waterPrior);
  const priorName = waterKind === "water" ? "水" : (waterKind === "fake" ? "诈称" : (waterKind === "both" ? "水+诈称" : ""));
  const priorSub = priorName && waterPrior != null
    ? " · 水/诈称先验 " + fmt(waterPrior) + "（" + priorName + "）" : "";
  const evalSub = "官标 " + official.toFixed(2) + (water == null ? "" : " · 水鱼 " + water.toFixed(3)) + priorSub;
  rows.push(["评价定数", evalText, evalSub]);

  const range = model.range || [0, 99];
  const inRange = evalLevel != null && evalLevel >= range[0] && evalLevel <= range[1];
  rows.push(["置信区间", Number(range[0]).toFixed(2) + " ~ " + Number(range[1]).toFixed(2),
             inRange ? "" : "区间外，不参与修正"]);

  const ach = sheet.achievement == null ? null
    : (Number(sheet.achievement) > 101 ? Number(sheet.achievement) / 10000 : Number(sheet.achievement));
  const contributions = all.map((n) => {
    if (gamma[n] == null || Math.abs(gamma[n]) < 1e-9) return null;
    const m = tierMult(Number(gamma[n]));
    return { name: n, gamma: Number(gamma[n]), mult: m, value: Number(gamma[n]) * m };
  }).filter(Boolean).sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  let scorePiece = 0, tagPiece = 0, total = 0;
  let finalFit = evalLevel;
  let tagDetails = "";
  if (inRange) {
    let scoreSub = "";
    if (sheet.playCount > 0 && ach) {
      const coef = sheet.playCount >= 10 ? 0.2 : 0.06;
      const w = fitScoreWeight(ach);
      scorePiece = coef * w;
      scoreSub = ach.toFixed(4) + "% → 权重 " + w.toFixed(3) + " × 系数 " + coef;
    }
    rows.push(["成绩修正", fmt(scorePiece), scoreSub]);

    const gammaSum = contributions.reduce((s, x) => s + x.value, 0);
    const tagCorrField = sheet.tagCorr == null ? null : Number(sheet.tagCorr);
    const tagKField = sheet.tagK == null ? null : Number(sheet.tagK);
    const tagFactorField = sheet.tagFactor == null ? null : Number(sheet.tagFactor);
    const tagBalanceField = sheet.tagBalance == null ? null : Number(sheet.tagBalance);
    const tagScopeField = sheet.tagScope == null ? null : Number(sheet.tagScope);
    const tagSampleField = sheet.tagSample == null ? null : Number(sheet.tagSample);
    tagPiece = tagCorrField == null ? clip(-gammaSum) : tagCorrField;
    const tagBits = [];
    if (tagKField != null) tagBits.push(tagKField + " 个标签");
    if (tagFactorField != null) tagBits.push("数量系数 " + tagFactorField.toFixed(2));
    if (tagBalanceField != null) tagBits.push("均衡系数 " + tagBalanceField.toFixed(2));
    if (tagScopeField != null && tagScopeField !== 1) tagBits.push("区间系数 " + tagScopeField.toFixed(2));
    if (tagSampleField != null && tagSampleField !== 1) tagBits.push("样本系数 " + tagSampleField.toFixed(2));
    const tagSummary = tagBits.join(" · ");
    if (contributions.length) {
      const tone = (g) => g >= 0.05 ? "fit-tag-good" : (g <= -0.05 ? "fit-tag-bad" : "");
      const lines = contributions.map((x) => {
        const cls = tone(x.gamma);
        const text = x.name + " " + fmt(x.value) + (x.mult !== 1.0 ? " ×" + x.mult : "");
        return cls ? '<span class="' + cls + '">' + escapeHtml(text) + "</span>" : escapeHtml(text);
      }).join("<br>");
      tagDetails = '<details class="fit-tags"><summary>展开全部 ' + contributions.length
        + ' 个标签（<span class="fit-tag-good">优势</span>、<span class="fit-tag-bad">劣势</span>）</summary><div>' + lines + "</div></details>";
    }
    rows.push(["标签修正", fmt(tagPiece), tagSummary, tagDetails]);
    const tagSampleInfo = model.tagSample || {};
    if (tagSampleInfo.n != null) {
      rows.push(["标签样本", String(tagSampleInfo.n) + " 条",
                 tagSampleInfo.factor != null && Number(tagSampleInfo.factor) < 1
                   ? "不足 300，标签修正整体 ×" + Number(tagSampleInfo.factor).toFixed(2)
                   : "≥300，按正常强度"]);
    }

    total = clip(scorePiece + tagPiece);
    finalFit = sheet.fitLevel == null ? (evalLevel == null ? null : evalLevel + total) : Number(sheet.fitLevel);
    rows.push(["个人拟合", finalFit == null ? "—" : finalFit.toFixed(3),
               "评价 " + evalText + " + 总修正 " + fmt(total)]);
  } else {
    finalFit = evalLevel;
    rows.push(["个人拟合", finalFit == null ? "—" : finalFit.toFixed(3), "区间外，直接使用评价定数"]);
  }

  const dlg = document.getElementById("fitdlg");
  const body = document.getElementById("fit-body");
  const title = document.getElementById("fit-title");
  if (!dlg || !body) return;
  if (title) title.textContent = song.title + " · " + (sheet.difficulty || "") + " " + (sheet.levelText || "");
  let html = '<table class="fit-table">'
    + rows.map((r) => {
        let row = '<tr><th>' + escapeHtml(r[0]) + "</th><td>" + escapeHtml(r[1]) + "</td></tr>";
        if (r[2]) row += '<tr class="fit-sub"><th></th><td>' + escapeHtml(r[2]) + "</td></tr>";
        if (r[3]) row += '<tr class="fit-sub"><th></th><td>' + r[3] + "</td></tr>";
        return row;
      }).join("")
    + "</table>";
  body.innerHTML = html;
  dlg.hidden = false;
}

let LAST_FIT_MODEL = null;

function openFitInfo() {
  if (!BL_PENDING) return;
  renderFitInfo(BL_PENDING.song, BL_PENDING.sheet, LAST_FIT_MODEL);
}

function fillScoreSelect(sel, names) {
  if (!sel || sel.options.length) return;
  sel.innerHTML = names.map((name, i) => '<option value="' + i + '">' + escapeHtml(name) + "</option>").join("");
}

function openScoreDialog() {
  if (!BL_PENDING) return;
  const { song, sheet } = BL_PENDING;
  const dlg = document.getElementById("scoredlg");
  if (!dlg) return;
  const d = diffOf(sheet, song.family);
  document.getElementById("score-title").textContent = song.title + " · " + d.name;
  document.getElementById("score-ach").value = sheet.achievement == null
    ? "" : (Number(sheet.achievement) / 10000).toFixed(4);
  document.getElementById("score-dx").value = sheet.deluxscoreMax == null ? "" : sheet.deluxscoreMax;
  document.getElementById("score-play").value = sheet.playCount == null ? "" : sheet.playCount;
  fillScoreSelect(document.getElementById("score-combo"), SONG_COMBO_NAMES);
  fillScoreSelect(document.getElementById("score-sync"), SONG_SYNC_NAMES);
  document.getElementById("score-combo").value = String(sheet.combo || 0);
  document.getElementById("score-sync").value = String(sheet.sync || 0);
  document.getElementById("score-note").textContent = "";
  const btn = document.getElementById("score-save");
  btn.disabled = false;
  btn.textContent = "保存并重算";
  dlg.hidden = false;
}

async function saveScoreEdit() {
  if (!BL_PENDING) return;
  const { song, sheet } = BL_PENDING;
  const dlg = document.getElementById("scoredlg");
  const note = document.getElementById("score-note");
  const btn = document.getElementById("score-save");
  const achText = document.getElementById("score-ach").value.trim();
  const ach = Number(achText);
  if (!achText || !Number.isFinite(ach) || ach < 0 || ach > 101) {
    note.textContent = "达成率需在 0~101 之间";
    return;
  }
  const combo = Number(document.getElementById("score-combo").value) || 0;
  const sync = Number(document.getElementById("score-sync").value) || 0;
  const dxText = document.getElementById("score-dx").value.trim();
  const dx = dxText === "" ? 0 : Number(dxText);
  if (!Number.isInteger(dx) || dx < 0) {
    note.textContent = "DX 分必须是非负整数";
    return;
  }
  const playText = document.getElementById("score-play").value.trim();
  const play = playText === "" ? 0 : Number(playText);
  if (!Number.isInteger(play) || play < 0) {
    note.textContent = "游玩次数必须是非负整数";
    return;
  }
  btn.disabled = true;
  btn.textContent = "保存中，正在重算…";
  note.textContent = "正在更新统计、分析与 rating…";
  try {
    const data = await api("/api/score/edit", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        user: currentUser, internalId: song.internalId, chartid: sheet.chartid,
        achievement: ach, comboStatus: combo, syncStatus: sync,
        deluxscoreMax: dx, playCount: play,
      }),
    });
    if (data.error) throw new Error(data.error);
    const row = data.score || {};
    sheet.achievement = row.achievement != null ? row.achievement : Math.round(ach * 10000);
    sheet.combo = row.comboStatus != null ? row.comboStatus : combo;
    sheet.sync = row.syncStatus != null ? row.syncStatus : sync;
    sheet.deluxscoreMax = row.deluxscoreMax != null ? row.deluxscoreMax : dx;
    if (row.dxscore != null) sheet.dxscore = row.dxscore;
    if (row.playCount != null) sheet.playCount = row.playCount;
    SONG_DETAIL_CACHE.delete(String(song.internalId) + "|" + (currentUser || ""));
    dlg.hidden = true;
    rerenderChartPage();
    refreshPoolMarks().catch(() => {});
    if (data.relatedError) alert("成绩已保存，但重算失败：" + data.relatedError);
  } catch (err) {
    note.textContent = "保存失败：" + (err.message || err);
  } finally {
    btn.disabled = false;
    btn.textContent = "保存并重算";
  }
}

async function favoriteToggle() {  if (!BL_PENDING) return;
  const { song, sheet } = BL_PENDING;
  if (!currentUser) return;
  const path = sheet.favorite ? "/api/favorite/remove" : "/api/favorite";
  try {
    await api(path, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user: currentUser, internalId: song.internalId, chartid: sheet.chartid}),
    });
    sheet.favorite = !sheet.favorite;
    rerenderChartPage();
    refreshPoolMarks().catch(() => {});
  } catch (err) {
    console.error("收藏失败", err);
  }
}

async function pushToMachine() {
  if (!BL_PENDING) return;
  const { song, sheet } = BL_PENDING;
  const isUtage = song.family === "utage" || song.family === "utage2p";
  const chartType = song.family === "dx" ? "dx" : (song.family === "std" ? "std" : "");
  try {
    const data = await api("/api/assistant/push", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        internalId: song.internalId,
        // 宴谱在游戏里走单独的 UtageDifficulty，内部 chartid 10 不能直接当 0..4 的 diff
        chartid: isUtage ? 0 : sheet.chartid,
        type: chartType,
      }),
    });
    if (data && data.ok === false) throw new Error(data.error || data.info || "推送失败");
  } catch (err) {
    const msg = String(err.message || err);
    alert(msg.indexOf("局域网IP") >= 0 ? msg : "推送失败：" + msg);
  }
}

async function blacklistAdd(duration) {
  if (!BL_PENDING) return;
  const { song, sheet } = BL_PENDING;
  const r = await api("/api/blacklist", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: currentUser || "", internalId: song.internalId,
                           chartid: sheet.chartid, duration }),
  });
  sheet.blacklisted = true;
  sheet.blacklist = (r && r.entry) || { duration };
  document.getElementById("bldlg").hidden = true;
  rerenderChartPage();
  refreshPoolMarks().catch(() => {});
}

async function blacklistRemove() {
  if (!BL_PENDING) return;
  const { song, sheet } = BL_PENDING;
  await api("/api/blacklist/remove", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: currentUser || "", internalId: song.internalId, chartid: sheet.chartid }),
  });
  sheet.blacklisted = false;
  sheet.blacklist = null;
  rerenderChartPage();
  refreshPoolMarks().catch(() => {});
}

async function showChartPage(song, sheet) {
  const body = document.getElementById("sm-body");
  const modal = document.getElementById("songmodal");
  if (!body || !modal) return;
  body.innerHTML = '<p class="sm-hint">读取中…</p>';
  modal.hidden = false;
  try {
    const full = await fetchSongDetail(song.internalId);
    const hit = (full.sheets || []).find((x) => String(x.chartid) === String(sheet.chartid)) || sheet;
    BL_PENDING = { song: full, sheet: hit };
    body.innerHTML = renderChartBody(full, hit);
  } catch (err) {
    body.innerHTML = '<p class="sm-hint">读取失败：' + escapeHtml(err.message) + "</p>";
  }
}

async function showSongPage(song) {
  const body = document.getElementById("sf2-body");
  const modal = document.getElementById("songfull");
  if (!body || !modal) return;
  body.innerHTML = '<p class="sm-hint">读取中…</p>';
  modal.hidden = false;
  try {
    const full = await fetchSongDetail(song.internalId);
    SONG_PAGE = full;
    body.innerHTML = renderSongBody(full);
    marqueeIfLong(body.querySelector(".sf2-title"));   // 歌名只准一行，太长就滚动
  } catch (err) {
    body.innerHTML = '<p class="sm-hint">读取失败：' + escapeHtml(err.message) + "</p>";
  }
}

/* v75：点曲名复制到剪贴板 */
function copyText(text, onDone) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onDone).catch(function () { fallbackCopy(text, onDone); });
    return;
  }
  fallbackCopy(text, onDone);
}

function fallbackCopy(text, onDone) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); onDone(); } catch (err) { /* 忽略 */ }
  ta.remove();
}

function copySongTitle(el) {
  if (!el) return;
  const text = el.dataset.title || el.textContent || "";
  if (!text) return;
  copyText(text, function () {
    el.classList.add("copied");
    setTimeout(function () { el.classList.remove("copied"); }, 1200);
  });
}

function openChartModal(row) {
  const card = row.closest(".songcard");
  if (!card || !card.__song) return;
  const sheet = (card.__song.sheets || []).find((x) => String(x.chartid) === row.dataset.chartid);
  if (sheet) showChartPage(card.__song, sheet);
}

function openSongFull(base) {
  const card = base.closest(".songcard");
  if (card && card.__song) showSongPage(card.__song);
}

function closeSongModal() {
  const modal = document.getElementById("songmodal");
  if (modal) modal.hidden = true;
}
function closeSongFull() {
  const modal = document.getElementById("songfull");
  if (modal) modal.hidden = true;
}

document.addEventListener("click", function (e) {
  const t = e.target;
  if (!t || !t.closest) return;
  const diffBtn = t.closest("#songfull .sf2-diff");
  if (diffBtn && SONG_PAGE) {
    const sheet = (SONG_PAGE.sheets || []).find((x) => String(x.chartid) === diffBtn.dataset.chartid);
    if (sheet) { showChartPage(SONG_PAGE, sheet); return; }
  }
  const fitBtn = t.closest("[data-act='fit-info']");
  if (fitBtn) { openFitInfo(); return; }
  if (t.closest("#fit-close") || t.id === "fitdlg") {
    const d = document.getElementById("fitdlg");
    if (d) d.hidden = true;
    return;
  }
  const scoreBtn = t.closest("[data-act='score-edit']");
  if (scoreBtn) { openScoreDialog(); return; }
  if (t.closest("#score-cancel") || t.id === "scoredlg") {
    const d = document.getElementById("scoredlg");
    if (d) d.hidden = true;
    return;
  }
  if (t.closest("#score-save")) { saveScoreEdit().catch(() => {}); return; }
  const favBtn = t.closest("[data-act='fav-toggle']");
  if (favBtn) { favoriteToggle().catch(() => {}); return; }
  const pushBtn = t.closest("[data-act='push-machine']");
  if (pushBtn) { pushToMachine().catch(() => {}); return; }
  const blBtn = t.closest("[data-act='bl-add']");
  if (blBtn) { openBlacklistDialog(); return; }
  const blRm = t.closest("[data-act='bl-remove']");
  if (blRm) { blacklistRemove().catch(() => {}); return; }
  const durBtn = t.closest("#bldlg [data-dur]");
  if (durBtn) { blacklistAdd(durBtn.dataset.dur).catch(() => {}); return; }
  if (t.closest("#bl-cancel") || t.id === "bldlg") { document.getElementById("bldlg").hidden = true; return; }
  const titleEl = t.closest("#songfull .sf2-title");
  if (titleEl) { copySongTitle(titleEl); return; }
  const row = t.closest(".sc-row");
  if (row && row.closest(".songcard") && !row.classList.contains("sc-row-empty") && !row.classList.contains("sc-type-row")) {
    openChartModal(row); return;
  }
  const base = t.closest(".sc-base");
  if (base && base.closest(".songcard")) {
    // .sc-base 的元素盒比卡片大（1.2 缩放 + 744 画布），只认落在卡片可见范围内的点击
    const card = base.closest(".songcard").getBoundingClientRect();
    const inCard = e.clientX >= card.left && e.clientX <= card.right
      && e.clientY >= card.top && e.clientY <= card.bottom;
    if (inCard) { openSongFull(base); return; }
  }
  if (t.id === "songmodal" || (t.closest && t.closest("#sm-close"))) { closeSongModal(); return; }
  if (t.id === "songfull" || (t.closest && t.closest("#sf2-close"))) { closeSongFull(); return; }
});
document.addEventListener("keydown", function (e) {
  if (e.key !== "Escape") return;
  const score = document.getElementById("scoredlg");
  if (score && !score.hidden) { score.hidden = true; return; }
  const chart = document.getElementById("songmodal");
  if (chart && !chart.hidden) { closeSongModal(); return; }
  const full = document.getElementById("songfull");
  if (full && !full.hidden) closeSongFull();
});




export { BL_DUR_TEXT, BL_PENDING, DIFF_CLASS, DIFF_LABEL, DIGIT_EM, G6_INK, LV_EM, SERVER_LABEL, SONG_DETAIL_CACHE, SONG_PAGE, UTG_Q, UTG_Q_INK, blDurText, blacklistAdd, blacklistRemove, closeSongFull, closeSongModal, copySongTitle, copyText, diffOf, escapeHtml, fallbackCopy, fetchSongDetail, fmtAch, fmtLevel, g6, g6s, glyph, glyphs, historyHtml, levelHtml, marqueeIfLong, openBlacklistDialog, openChartModal, openSongFull, pillBusy, pillCache, pillDataUrl, qGlyph, renderChartBody, renderSongBody, rerenderChartPage, row6, rowHtml, scoreCells, showChartPage, showSongPage, slot, slot6, songCard, tagChips, utageKanjiOf };
