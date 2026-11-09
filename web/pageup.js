// 用途：右下角固定的「回到顶端」按钮（各页面共用；按钮本身写在 index.html，带 data-pageup）
// 状态：现行

export function attachPageUp() {
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-pageup]");
    if (!btn) return;
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}
