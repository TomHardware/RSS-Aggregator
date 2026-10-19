/* ==========================================================================
 * RSS Aggregator —— 前端交互模块 v2.0.9
 *
 * 由 base.html 以 defer 方式加载，负责：
 *  1. 外观偏好（主题 / 强调色 / 密度）—— 持久化到 localStorage；
 *  2. 命令面板（Ctrl/Cmd+K）、键盘快捷键；
 *  3. 无限滚动、即时搜索（都复用 /api/articles）；
 *  4. SSE 实时状态（失败自动退回轮询）；
 *  5. Toast、批量选择与批量操作。
 *
 * 设计上刻意不依赖任何框架：全部用原生 DOM API，便于自托管离线使用。
 * ========================================================================== */
(function () {
    'use strict';

    var PREF = {
        theme: 'rss-theme',
        accent: 'rss-accent',
        density: 'rss-density'
    };

    function store(key, value) {
        try {
            if (value === null) localStorage.removeItem(key);
            else localStorage.setItem(key, value);
        } catch (e) { /* 隐私模式忽略 */ }
    }

    function read(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }

    /* ------------------------------------------------------------------ */
    /* 1. 外观偏好                                                          */
    /* ------------------------------------------------------------------ */
    var root = document.documentElement;

    function effectiveTheme() {
        var explicit = root.getAttribute('data-theme');
        if (explicit) return explicit;
        return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
            ? 'dark' : 'light';
    }

    function applyTheme(theme) {
        if (theme === 'light' || theme === 'dark') root.setAttribute('data-theme', theme);
        else root.removeAttribute('data-theme');
        var button = document.getElementById('theme-toggle');
        if (button) {
            var now = effectiveTheme();
            button.textContent = now === 'dark' ? '☀︎' : '☾';
            var label = now === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
            button.setAttribute('aria-label', label);
            button.title = label;
            button.setAttribute('aria-pressed', now === 'dark' ? 'true' : 'false');
        }
    }

    function applyAccent(accent) {
        if (accent && accent !== 'blue') root.setAttribute('data-accent', accent);
        else root.removeAttribute('data-accent');
        document.querySelectorAll('.swatch').forEach(function (swatch) {
            swatch.setAttribute('aria-pressed',
                (swatch.dataset.accentChoice === (accent || 'blue')) ? 'true' : 'false');
        });
    }

    function applyDensity(density) {
        if (density === 'compact' || density === 'comfortable') {
            root.setAttribute('data-density', density);
        } else {
            root.removeAttribute('data-density');
        }
        var button = document.getElementById('density-toggle');
        if (button) {
            var compact = root.getAttribute('data-density') === 'compact';
            button.textContent = compact ? '⇕' : '≡';
            button.setAttribute('aria-pressed', compact ? 'true' : 'false');
            button.title = compact ? 'Comfortable density' : 'Compact density';
        }
    }

    applyTheme(read(PREF.theme));
    applyAccent(read(PREF.accent));
    applyDensity(read(PREF.density));

    if (window.matchMedia) {
        var query = window.matchMedia('(prefers-color-scheme: dark)');
        var onChange = function () { if (!read(PREF.theme)) applyTheme(null); };
        if (query.addEventListener) query.addEventListener('change', onChange);
        else if (query.addListener) query.addListener(onChange);
    }

    /* ------------------------------------------------------------------ */
    /* 2. Toast                                                            */
    /* ------------------------------------------------------------------ */
    var toastTimer = null;
    function notify(text) {
        var toast = document.getElementById('toast');
        if (!toast) return;
        toast.textContent = text;
        toast.classList.add('is-visible');
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toast.classList.remove('is-visible'); }, 1900);
    }
    window.rssNotify = notify;

    /* ------------------------------------------------------------------ */
    /* 3. 命令面板                                                         */
    /* ------------------------------------------------------------------ */
    function initPalette() {
        var mask = document.getElementById('palette-mask');
        var input = document.getElementById('palette-input');
        var list = document.getElementById('palette-list');
        if (!mask || !input || !list) return;

        var items = [];
        var seed = document.getElementById('palette-data');
        if (seed) {
            try { items = JSON.parse(seed.textContent); } catch (e) { items = []; }
        }
        document.querySelectorAll('article.article').forEach(function (article) {
            var link = article.querySelector('h3 a');
            if (!link) return;
            items.push({label: link.textContent.trim(), hint: 'article', href: link.getAttribute('href')});
        });

        var matches = items.slice(0, 12);
        var active = 0;

        function render() {
            list.innerHTML = '';
            if (!matches.length) {
                var empty = document.createElement('li');
                empty.textContent = '—';
                empty.setAttribute('aria-disabled', 'true');
                list.appendChild(empty);
                return;
            }
            matches.forEach(function (item, index) {
                var li = document.createElement('li');
                li.setAttribute('role', 'option');
                li.setAttribute('aria-selected', index === active ? 'true' : 'false');
                li.textContent = item.label;
                var hint = document.createElement('span');
                hint.className = 'hint';
                hint.textContent = item.hint || '';
                li.appendChild(hint);
                li.addEventListener('click', function () { window.location = item.href; });
                list.appendChild(li);
            });
        }

        function filter() {
            var query = input.value.trim().toLowerCase();
            matches = (query ? items.filter(function (item) {
                return (item.label || '').toLowerCase().indexOf(query) !== -1;
            }) : items).slice(0, 30);
            active = 0;
            render();
        }

        function open() {
            mask.hidden = false;
            mask.style.display = '';   // 清掉可能残留的内联样式
            input.value = '';
            filter();
            input.focus();
        }

        function close() {
            mask.hidden = true;
            mask.style.display = 'none';  // 双保险：即使样式表未生效也能关掉
            input.blur();
        }

        // 面板初始必须是关闭状态
        close();

        document.addEventListener('keydown', function (event) {
            var isOpen = !mask.hidden;
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
                event.preventDefault();
                if (isOpen) close(); else open();
                return;
            }
            if (!isOpen) return;
            if (event.key === 'Escape') close();
            else if (event.key === 'ArrowDown') {
                event.preventDefault();
                active = Math.min(active + 1, matches.length - 1);
                render();
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                active = Math.max(active - 1, 0);
                render();
            } else if (event.key === 'Enter' && matches[active]) {
                window.location = matches[active].href;
            }
        });

        input.addEventListener('input', filter);
        mask.addEventListener('click', function (event) {
            // 只有点在遮罩本身（不是面板内部）时才关闭
            if (!event.target.closest('.palette')) close();
        });
        // 面板内的空白区域点击也不应把事件冒泡到遮罩之外
        var panel = mask.querySelector('.palette');
        if (panel) panel.addEventListener('click', function (event) {
            event.stopPropagation();
        });
        document.querySelectorAll('[data-open-palette]').forEach(function (trigger) {
            trigger.addEventListener('click', function (event) {
                event.preventDefault();
                open();
            });
        });
    }

    /* ------------------------------------------------------------------ */
    /* 4. 键盘快捷键                                                       */
    /* ------------------------------------------------------------------ */
    function initShortcuts() {
        var articles = Array.prototype.slice.call(document.querySelectorAll('article.article'));
        var cursor = -1;

        function focusArticle(index) {
            if (!articles.length) return;
            cursor = Math.max(0, Math.min(articles.length - 1, index));
            articles.forEach(function (item) { item.classList.remove('is-cursor'); });
            var target = articles[cursor];
            target.classList.add('is-cursor');
            target.scrollIntoView({block: 'center', behavior: 'smooth'});
        }

        function current() { return cursor >= 0 ? articles[cursor] : null; }

        document.addEventListener('keydown', function (event) {
            if (event.target.matches('input, textarea, select')) return;
            if (event.ctrlKey || event.metaKey || event.altKey) return;
            var key = event.key;
            if (key === 'j') focusArticle(cursor + 1);
            else if (key === 'k') focusArticle(cursor - 1);
            else if (key === 'o' && current()) {
                var link = current().querySelector('h3 a');
                if (link) window.location = link.href;
            } else if (key === 's' && current()) {
                var star = current().querySelector('.bookmark-toggle');
                if (star) star.click();
            } else if (key === 'm' && current()) {
                var form = current().querySelector('form[action*="/read"]');
                if (form) form.submit();
            } else if (key === '/') {
                event.preventDefault();
                var search = document.querySelector('input[type=search]');
                if (search) search.focus();
            } else if (key === '?') {
                var help = document.getElementById('shortcut-help');
                if (help) help.hidden = !help.hidden;
            }
        });
    }

    /* ------------------------------------------------------------------ */
    /* 5. 无限滚动                                                         */
    /* ------------------------------------------------------------------ */
    function initInfiniteScroll() {
        var sentinel = document.getElementById('scroll-sentinel');
        if (!sentinel || !window.IntersectionObserver) return;

        var page = parseInt(sentinel.dataset.page || '1', 10);
        var hasNext = sentinel.dataset.hasNext === '1';
        var loading = false;

        function loadMore() {
            if (loading || !hasNext) return;
            loading = true;
            sentinel.classList.add('is-loading');
            var params = new URLSearchParams(sentinel.dataset.filters || '');
            params.set('page', String(page + 1));
            params.set('html', '1');
            fetch(sentinel.dataset.api + '?' + params.toString(), {
                headers: {'Accept': 'text/html'},
                credentials: 'same-origin'
            }).then(function (resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.text();
            }).then(function (markup) {
                if (!markup.trim()) { hasNext = false; return; }
                sentinel.insertAdjacentHTML('beforebegin', markup);
                page += 1;
                sentinel.dataset.page = String(page);
                hasNext = /article-\d+/.test(markup);
                sentinel.dataset.hasNext = hasNext ? '1' : '0';
                bindSelection();
            }).catch(function () {
                hasNext = false;   // 出错就停，避免死循环
            }).finally(function () {
                loading = false;
                sentinel.classList.remove('is-loading');
            });
        }

        new IntersectionObserver(function (entries) {
            if (entries.some(function (entry) { return entry.isIntersecting; })) loadMore();
        }, {rootMargin: '400px'}).observe(sentinel);

        var trigger = document.querySelector('[data-load-more]');
        if (trigger) trigger.addEventListener('click', function (event) {
            event.preventDefault();
            loadMore();
        });
    }

    /* ------------------------------------------------------------------ */
    /* 6. 即时搜索                                                         */
    /* ------------------------------------------------------------------ */
    function initLiveSearch() {
        var input = document.querySelector('input[type=search]');
        var box = document.querySelector('.live-search');
        var sentinel = document.getElementById('scroll-sentinel');
        if (!input || !box || !sentinel) return;

        var timer = null;
        var seq = 0;

        function close() { box.hidden = true; box.innerHTML = ''; }

        function query(value) {
            var mine = ++seq;
            var params = new URLSearchParams({q: value, limit: '6'});
            fetch(sentinel.dataset.api + '?' + params.toString(), {
                headers: {'Accept': 'application/json'},
                credentials: 'same-origin'
            }).then(function (resp) {
                return resp.ok ? resp.json() : null;
            }).then(function (data) {
                if (mine !== seq || !data) return;
                if (!data.items || !data.items.length) { close(); return; }
                box.innerHTML = '';
                data.items.forEach(function (item) {
                    var row = document.createElement('a');
                    row.className = 'live-row';
                    row.href = '/article/' + item.id;
                    var title = document.createElement('span');
                    title.textContent = item.title || '(untitled)';
                    var meta = document.createElement('small');
                    meta.textContent = (item.published || '').slice(0, 10);
                    row.appendChild(title);
                    row.appendChild(meta);
                    box.appendChild(row);
                });
                box.hidden = false;
            }).catch(function () { /* 网络抖动忽略 */ });
        }

        input.addEventListener('input', function () {
            var value = input.value.trim();
            if (timer) clearTimeout(timer);
            if (value.length < 2) { close(); return; }
            timer = setTimeout(function () { query(value); }, 250);
        });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') close();
        });
        document.addEventListener('click', function (event) {
            if (!box.contains(event.target) && event.target !== input) close();
        });
    }

    /* ------------------------------------------------------------------ */
    /* 7. SSE 实时状态（失败退回轮询）                                      */
    /* ------------------------------------------------------------------ */
    function initLiveStatus() {
        var box = document.getElementById('task-status');
        var dot = document.getElementById('live-dot');
        var bar = document.getElementById('task-progress');
        if (!box) return;

        function render(payload) {
            var counter = document.getElementById('unread-total');
            if (counter && typeof payload.unread_total === 'number') {
                counter.textContent = payload.unread_total;
            }
            var running = payload.running || [];
            if (running.length) {
                box.textContent = running.map(function (task) {
                    return task.source_name || ('#' + task.source_id);
                }).join(' · ');
                box.classList.add('is-active');
                if (bar) bar.hidden = false;
            } else {
                box.classList.remove('is-active');
                if (bar) bar.hidden = true;
            }
            if (typeof payload.unread_total === 'number') {
                var base = document.title.replace(/^\(\d+\+?\)\s*/, '');
                document.title = payload.unread_total ? '(' + payload.unread_total + ') ' + base : base;
            }
        }

        // 轮询兜底：SSE 不可用或断开时仍然保持状态可读
        var pollTimer = setInterval(function () {
            if (window.__rssSseOpen) return;
            fetch(box.dataset.endpoint, {
                headers: {'Accept': 'application/json'}, credentials: 'same-origin', cache: 'no-store'
            }).then(function (resp) { return resp.ok ? resp.json() : null; })
              .then(function (data) { if (data) render(data); })
              .catch(function () {});
        }, 5000);

        if (!window.EventSource) return;

        var retries = 0;
        function connect() {
            var source = new EventSource('/api/events');
            source.onopen = function () {
                window.__rssSseOpen = true;
                retries = 0;
                if (dot) dot.classList.add('is-on');
            };
            source.onmessage = function (event) {
                var data;
                try { data = JSON.parse(event.data); } catch (e) { return; }
                render(data);
            };
            source.onerror = function () {
                window.__rssSseOpen = false;
                if (dot) dot.classList.remove('is-on');
                source.close();
                if (retries++ < 5) setTimeout(connect, 2000 * retries);
                else clearInterval(pollTimer);
            };
        }
        connect();
    }

    /* ------------------------------------------------------------------ */
    /* 8. 批量选择与批量操作                                                */
    /* ------------------------------------------------------------------ */
    var batchBar = null;
    var batchUrl = '/api/articles/batch';

    function selectedIds() {
        return Array.prototype.slice.call(document.querySelectorAll('.select-box:checked'))
            .map(function (box) { return box.value; });
    }

    function syncBatchBar() {
        if (!batchBar) return;
        var ids = selectedIds();
        var label = batchBar.querySelector('[data-batch-count]');
        if (label) label.textContent = String(ids.length);
        batchBar.classList.toggle('is-visible', ids.length > 0);
    }

    function bindSelection() {
        document.querySelectorAll('.select-box').forEach(function (box) {
            if (box.dataset.bound) return;
            box.dataset.bound = '1';
            box.addEventListener('change', syncBatchBar);
        });
        syncBatchBar();
    }

    function initBatch() {
        batchBar = document.getElementById('batch-bar');
        if (!batchBar) return;
        batchUrl = batchBar.dataset.endpoint || batchUrl;

        var toggleAll = document.getElementById('select-all');
        if (toggleAll) toggleAll.addEventListener('change', function () {
            document.querySelectorAll('.select-box').forEach(function (box) {
                box.checked = toggleAll.checked;
            });
            syncBatchBar();
        });

        batchBar.querySelectorAll('[data-batch-action]').forEach(function (button) {
            button.addEventListener('click', function () {
                var ids = selectedIds();
                if (!ids.length) return;
                button.disabled = true;
                fetch(batchUrl, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                    credentials: 'same-origin',
                    body: JSON.stringify({action: button.dataset.batchAction, ids: ids})
                }).then(function (resp) { return resp.json(); }).then(function (data) {
                    if (data && data.ok) {
                        notify((button.dataset.doneLabel || 'done') + ' · ' + (data.updated || 0));
                        setTimeout(function () { window.location.reload(); }, 400);
                    } else {
                        notify('failed');
                    }
                }).catch(function () { notify('failed'); })
                  .finally(function () { button.disabled = false; });
            });
        });

        bindSelection();
    }

    /* ------------------------------------------------------------------ */
    /* 9. 收藏按钮                                                         */
    /* ------------------------------------------------------------------ */
    function initBookmarks() {
        document.addEventListener('click', function (event) {
            var button = event.target.closest('.bookmark-toggle');
            if (!button) return;
            event.preventDefault();
            button.disabled = true;
            fetch(button.dataset.url, {
                method: 'POST',
                headers: {'Accept': 'application/json'},
                credentials: 'same-origin'
            }).then(function (resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            }).then(function (data) {
                button.textContent = data.bookmarked ? '★' : '☆';
                button.setAttribute('aria-pressed', data.bookmarked ? 'true' : 'false');
                notify(data.bookmarked ? (button.dataset.onLabel || '★') : (button.dataset.offLabel || '☆'));
            }).catch(function () {
                notify('failed');
            }).finally(function () {
                button.disabled = false;
            });
        });
    }

    /* ------------------------------------------------------------------ */
    /* 10. 外观控件                                                        */
    /* ------------------------------------------------------------------ */
    function initAppearance() {
        var themeButton = document.getElementById('theme-toggle');
        if (themeButton) themeButton.addEventListener('click', function () {
            var next = effectiveTheme() === 'dark' ? 'light' : 'dark';
            store(PREF.theme, next);
            applyTheme(next);
        });

        var densityButton = document.getElementById('density-toggle');
        if (densityButton) densityButton.addEventListener('click', function () {
            var next = root.getAttribute('data-density') === 'compact' ? 'comfortable' : 'compact';
            store(PREF.density, next);
            applyDensity(next);
            notify(next === 'compact' ? '≡' : '⇕');
        });

        document.querySelectorAll('.swatch').forEach(function (swatch) {
            swatch.addEventListener('click', function () {
                var choice = swatch.dataset.accentChoice;
                store(PREF.accent, choice);
                applyAccent(choice);
            });
        });
    }


    /* ------------------------------------------------------------------ */
    /* 11. 阅读视图：进度条、自动已读、j/k 翻篇                             */
    /* ------------------------------------------------------------------ */
    function initReader() {
        var reader = document.getElementById('reader');
        if (!reader) return;

        var bar = document.querySelector('#reading-progress > span');
        function onScroll() {
            var height = document.documentElement.scrollHeight - window.innerHeight;
            var ratio = height > 0 ? Math.min(1, window.scrollY / height) : 0;
            if (bar) bar.style.width = (ratio * 100) + '%';
        }
        window.addEventListener('scroll', onScroll, {passive: true});
        onScroll();

        // 打开即标记已读：用 POST 完成，页面本身仍是安全的 GET
        if (reader.dataset.markReadUrl) {
            fetch(reader.dataset.markReadUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: {'Accept': 'application/json'}
            }).catch(function () { /* 未登录或离线时忽略 */ });
        }

        document.addEventListener('keydown', function (event) {
            if (event.target.matches('input, textarea, select')) return;
            if (event.key === 'j' && reader.dataset.older) {
                window.location = '/article/' + reader.dataset.older;
            } else if (event.key === 'k' && reader.dataset.newer) {
                window.location = '/article/' + reader.dataset.newer;
            } else if (event.key === 'Escape' && reader.dataset.indexUrl) {
                window.location = reader.dataset.indexUrl;
            }
        });
    }


    /* ------------------------------------------------------------------ */
    /* 12. 文件选择器：显示所选文件名（原生控件的文案无法翻译）            */
    /* ------------------------------------------------------------------ */
    function initFilePickers() {
        document.querySelectorAll('.file-picker').forEach(function (picker) {
            var input = picker.querySelector('.file-input');
            var label = picker.querySelector('[data-file-name]');
            if (!input || !label) return;
            var idle = label.textContent;
            input.addEventListener('change', function () {
                var file = input.files && input.files[0];
                if (file) {
                    label.textContent = file.name;
                    picker.classList.add('has-file');
                } else {
                    label.textContent = idle;
                    picker.classList.remove('has-file');
                }
            });
        });
    }

    /* ------------------------------------------------------------------ */
    /* 启动                                                                */
    /* ------------------------------------------------------------------ */
    function boot() {
        applyTheme(read(PREF.theme));
        applyAccent(read(PREF.accent));
        applyDensity(read(PREF.density));
        initAppearance();
        initPalette();
        initShortcuts();
        initInfiniteScroll();
        initLiveSearch();
        initLiveStatus();
        initBatch();
        initBookmarks();
        initReader();
        initFilePickers();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
