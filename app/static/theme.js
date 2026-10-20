/* 预渲染主题：在 <head> 里同步执行，避免深色模式下闪一帧白底。
 *
 * 只做一件事——把用户存过的主题/强调色/密度立刻写到 <html data-*> 上。
 * 其余交互（切换按钮、命令面板、SSE 等）都在 app.js 里。
 */
(function () {
    var root = document.documentElement;
    var stored = null;
    try { stored = localStorage.getItem('rss-theme'); } catch (e) { stored = null; }
    if (stored === 'light' || stored === 'dark') {
        root.setAttribute('data-theme', stored);
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        // 未手动选择时跟随系统；不写 data-theme，交给 CSS 的媒体查询
        root.removeAttribute('data-theme');
    }
    try {
        var accent = localStorage.getItem('rss-accent');
        if (accent && accent !== 'blue') root.setAttribute('data-accent', accent);
        var density = localStorage.getItem('rss-density');
        if (density === 'compact' || density === 'comfortable') {
            root.setAttribute('data-density', density);
        }
    } catch (e) { /* 隐私模式忽略 */ }
})();
