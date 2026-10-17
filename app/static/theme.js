/* 主题切换：用户的选择存在 localStorage，优先级高于系统偏好。
 *
 * 在 <head> 里同步执行（不 defer）：否则深色模式下会先闪一帧白底。
 * 只依赖 prefers-color-scheme 与 localStorage，不引入任何外部资源。
 */
(function () {
    var KEY = 'rss-theme';
    var root = document.documentElement;

    function stored() {
        try { return localStorage.getItem(KEY); } catch (e) { return null; }
    }

    function apply(theme) {
        if (theme === 'light' || theme === 'dark') {
            root.setAttribute('data-theme', theme);
        } else {
            root.removeAttribute('data-theme');  // 跟随系统
        }
        var button = document.getElementById('theme-toggle');
        if (button) {
            var effective = theme || (window.matchMedia
                && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
            button.textContent = effective === 'dark' ? '☀︎' : '☾';
            button.setAttribute('aria-label', effective === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
            button.title = button.getAttribute('aria-label');
        }
    }

    apply(stored());

    document.addEventListener('DOMContentLoaded', function () {
        apply(stored());
        var button = document.getElementById('theme-toggle');
        if (!button) return;
        button.addEventListener('click', function () {
            var now = root.getAttribute('data-theme');
            var systemDark = window.matchMedia
                && window.matchMedia('(prefers-color-scheme: dark)').matches;
            var effective = now || (systemDark ? 'dark' : 'light');
            var next = effective === 'dark' ? 'light' : 'dark';
            try { localStorage.setItem(KEY, next); } catch (e) { /* 隐私模式忽略 */ }
            apply(next);
        });
    });

    // 系统主题变化时，若用户没有手动选择则跟着变
    if (window.matchMedia) {
        var query = window.matchMedia('(prefers-color-scheme: dark)');
        var listener = function () { if (!stored()) apply(null); };
        if (query.addEventListener) query.addEventListener('change', listener);
        else if (query.addListener) query.addListener(listener);
    }
})();
