/* Dan's Investment Dashboard · 看板主题脚本（独立仓库用，同步副本，勿手改）
   真源：Dan's Investment Dashboard / theme/board-theme.js
   作用：在样式生效前先落到正确的明暗档（避免闪一下），并驱动 .theme-toggle。
   为什么切档要 reload：看板里的图是加载时按令牌取色画的（SVG 属性与 canvas 都不认
   var()），重画一遍比分页挂重绘钩子可靠；代价是丢滚动位置。 */
(function () {
  'use strict';
  var KEY = 'dan-dash-theme';
  var root = document.documentElement;

  function read() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function apply(mode) {
    if (mode === 'dark' || mode === 'light') root.setAttribute('data-theme', mode);
    else root.removeAttribute('data-theme');
  }

  // 看板默认浅色（纸本），不跟系统走：系统跟随只在本站研究页里保留。
  // 没存过偏好就显式写 light，于是 tokens.css 里那条 prefers-color-scheme 不会生效。
  var saved = read();
  if (saved !== 'dark' && saved !== 'light') saved = 'light';
  apply(saved);

  function isDark() {
    return root.getAttribute('data-theme') === 'dark' ||
      (!root.getAttribute('data-theme') && window.matchMedia &&
        window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function sync() {
    var dark = isDark();
    Array.prototype.forEach.call(document.querySelectorAll('.theme-toggle'), function (btn) {
      btn.setAttribute('aria-pressed', String(dark));
      btn.title = dark ? '切换到浅色' : '切换到深色';
      var lab = btn.querySelector('.tt-lab');
      if (lab) lab.textContent = dark ? '浅色' : '深色';
    });
  }

  function boot() {
    sync();
    document.addEventListener('click', function (ev) {
      var btn = ev.target.closest && ev.target.closest('.theme-toggle');
      if (!btn) return;
      ev.preventDefault();
      var next = isDark() ? 'light' : 'dark';
      try { localStorage.setItem(KEY, next); } catch (e) {}
      apply(next);
      window.location.reload();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
