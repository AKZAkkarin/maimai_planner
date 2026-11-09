// 用途：歌曲卡列表的公共显示层 —— 一页卡片（含马赛曲名、药丸底图、网格缩放）+ 上下两条翻页条
// 状态：现行（v110 从 songlist.js 摘出来，歌曲浏览和卡池预览共用）
// 依赖：songcard.js 的 songCard / marqueeIfLong / pillDataUrl（调用时解析）
import { songCard, marqueeIfLong, pillDataUrl } from "./songcard.js";

const CARD_W = 664.8;      // 卡片外框在 .songwrap 里的物理宽（和 CSS 的 calc 一致）
const COLS = 4;            // 永远 4 列，不满也不放大

/* 按容器宽度设置 --cols / --ps（卡片整体缩放）；colsOverride 用于预览三卡等自定义列数 */
export function fitGrid(box, colsOverride) {
  if (!box) return;
  const gap = parseFloat(getComputedStyle(box).columnGap) || 8;
  const w = box.clientWidth;
  if (!w || w < 200) return;                  // 页签没显示时先不算
  const cols = Number(colsOverride) || (w < 900 ? 2 : COLS);
  const s = Math.min(1, (w - gap * (cols - 1)) / cols / CARD_W);
  box.style.setProperty("--cols", cols);
  box.style.setProperty("--ps", Math.max(0.15, s).toFixed(4));
}

/* 渲染一页卡片：songs 里每首歌自带 sheets（调用方已按需过滤） */
export function renderCardPage(container, songs, colsOverride) {
  if (!container) return;
  container.innerHTML = "";
  for (const song of songs) {
    const cardEl = songCard(song);
    container.appendChild(cardEl);
    cardEl.querySelectorAll(".sc-title, .sc-artist").forEach(marqueeIfLong);
    // 药丸底图是 canvas 异步生成的缓存：新建的行要手动把缓存贴回去
    cardEl.querySelectorAll(".sc-row").forEach((r) => {
      const u = pillDataUrl(r.dataset.btn);
      if (u) r.style.backgroundImage = "url(" + u + ")";
    });
  }
  fitGrid(container, colsOverride);
}

/* 更新一组翻页条（attr 是 data-xxx 属性名，如 "data-pager" / "data-ppager"） */
export function renderPagers(attr, state, total, pageSize) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  state.pages = pages;
  state.page = Math.min(Math.max(1, state.page), pages);
  const from = (state.page - 1) * pageSize;
  const count = Math.min(pageSize, Math.max(0, total - from));
  const show = total > pageSize;
  document.querySelectorAll("[" + attr + "]").forEach((pager) => {
    pager.hidden = false;                              // v117：不需要翻页时也保留，只是变暗
    pager.classList.toggle("pager-dim", !show);
    const info = pager.querySelector('[data-role="info"]');
    if (info) info.textContent = "第 " + state.page + " / " + pages + " 页 · " + (from + 1) + "–" + (from + count) + " 首";
    const prev = pager.querySelector('[data-act="prev"]');
    const next = pager.querySelector('[data-act="next"]');
    if (prev) prev.disabled = state.page <= 1;
    if (next) next.disabled = state.page >= pages;
    const jump = pager.querySelector('[data-role="jump"]');
    const jumpBtn = pager.querySelector('[data-act="jump"]');
    if (jump) jump.disabled = !show;
    if (jumpBtn) jumpBtn.disabled = !show;
  });
  return { pages, from, count };
}

/* 绑定一组翻页条：上一页/下一页/跳转（回车也行） */
export function attachPagers(attr, state, render) {
  document.querySelectorAll("[" + attr + "]").forEach((pager) => {
    pager.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      if (act === "prev") { state.page -= 1; render(true); }
      else if (act === "next") { state.page += 1; render(true); }
      else if (act === "jump") {
        const el = pager.querySelector('[data-role="jump"]');
        if (!el) return;
        const n = parseInt(el.value, 10);
        el.value = "";
        if (!Number.isFinite(n)) { el.focus(); return; }
        const target = Math.min(Math.max(1, n), state.pages || 1);
        if (target === state.page) { el.focus(); return; }
        state.page = target;
        render(true);
      }
    });
    const input = pager.querySelector('[data-role="jump"]');
    if (input) input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); const b = pager.querySelector('[data-act="jump"]'); if (b) b.click(); }
    });
  });
}

/* 翻页条的内部结构只写一份：把空的 .songpager 容器填充好（设计语言统一） */
export function fillPagers(attr) {
  const inner = '<button data-act="prev">‹ 上一页</button>'
    + '<span data-role="info"></span>'
    + '<button data-act="next">下一页 ›</button>'
    + '<span class="songjump">跳至 <input data-role="jump" type="number" min="1" step="1" inputmode="numeric" placeholder="页码"> 页'
    + '<button data-act="jump">跳转</button></span>';
  document.querySelectorAll("[" + attr + "]").forEach((pager) => {
    if (pager.dataset.ready) return;
    pager.dataset.ready = "1";
    pager.innerHTML = inner;
  });
}
