// ── 终端增强工具 ──────────────────────────────────────────
// 适用于项目内嵌终端和多 Tab 终端，统一管理：
//   - WebLinksAddon（可点击 URL）
//   - SearchAddon（Ctrl+F 搜索）
//   - 选中自动复制
//   - Ctrl+Shift+C/V 复制粘贴
//   - 右键 context menu

function createEnhancedTerminal(mountEl) {
  const terminal = new window.Terminal({
    cursorBlink:       true,
    fontFamily:        "'SF Mono', 'Fira Code', 'Cascadia Code', monospace",
    fontSize:          13,
    scrollback:        5000,
    allowProposedApi:  true,
    theme: {
      background:          '#111827',
      foreground:          '#e5e7eb',
      cursor:              '#f97316',
      selectionBackground: '#334155',
      selectionForeground: '#ffffff',
    },
  });

  const fitAddon = new window.FitAddon.FitAddon();
  terminal.loadAddon(fitAddon);

  // 可点击 URL
  if (window.WebLinksAddon) {
    try { terminal.loadAddon(new window.WebLinksAddon.WebLinksAddon()); } catch (_) {}
  }

  // 搜索支持
  let searchAddon = null;
  if (window.SearchAddon) {
    try {
      searchAddon = new window.SearchAddon.SearchAddon();
      terminal.loadAddon(searchAddon);
    } catch (_) {}
  }

  terminal.open(mountEl);

  // ── 选中自动复制（Linux 终端惯例）──────────────────────
  terminal.onSelectionChange(() => {
    const text = terminal.getSelection();
    if (text) navigator.clipboard.writeText(text).catch(() => {});
  });

  // ── 键盘快捷键 ─────────────────────────────────────────
  terminal.attachCustomKeyEventHandler(e => {
    if (e.type !== 'keydown') return true;

    // Ctrl+Shift+C：显式复制
    if (e.ctrlKey && e.shiftKey && e.key === 'C') {
      const sel = terminal.getSelection();
      if (sel) navigator.clipboard.writeText(sel).catch(() => {});
      return false;
    }

    // Ctrl+Shift+V：从剪贴板粘贴
    if (e.ctrlKey && e.shiftKey && e.key === 'V') {
      navigator.clipboard.readText()
        .then(text => { if (text) _termPaste(terminal, text); })
        .catch(() => {});
      return false;
    }

    // Ctrl+F：打开搜索栏
    if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key === 'f' && searchAddon) {
      e.preventDefault();
      _showTermSearch(terminal, searchAddon, mountEl);
      return false;
    }

    return true;
  });

  // ── 右键 context menu ──────────────────────────────────
  mountEl.addEventListener('contextmenu', e => {
    e.preventDefault();
    _closeTermCtxMenu();

    const hasSel = !!terminal.getSelection();
    const menu = document.createElement('div');
    menu.id = '_term-ctx-menu';
    menu.className = 'term-ctx-menu';
    menu.innerHTML = `
      <button class="term-ctx-item${hasSel ? '' : ' disabled'}" data-action="copy">
        <span>复制</span><kbd>Ctrl+Shift+C</kbd>
      </button>
      <button class="term-ctx-item" data-action="paste">
        <span>粘贴</span><kbd>Ctrl+Shift+V</kbd>
      </button>
      <div class="term-ctx-sep"></div>
      <button class="term-ctx-item" data-action="selectall">全选</button>
      <button class="term-ctx-item" data-action="search" ${searchAddon ? '' : 'disabled'}>
        <span>查找</span><kbd>Ctrl+F</kbd>
      </button>
      <div class="term-ctx-sep"></div>
      <button class="term-ctx-item" data-action="clear">清屏</button>
    `;

    document.body.appendChild(menu);

    // 定位（防止溢出视口）
    let x = e.clientX, y = e.clientY;
    if (x + 210 > window.innerWidth)  x = window.innerWidth  - 214;
    if (y + 200 > window.innerHeight) y = window.innerHeight - 204;
    menu.style.cssText = `left:${x}px;top:${y}px`;

    menu.addEventListener('click', ev => {
      ev.stopPropagation();
      const btn = ev.target.closest('[data-action]');
      if (!btn || btn.classList.contains('disabled')) return;
      _closeTermCtxMenu();
      const action = btn.dataset.action;
      if (action === 'copy') {
        const t = terminal.getSelection();
        if (t) navigator.clipboard.writeText(t).catch(() => {});
      } else if (action === 'paste') {
        navigator.clipboard.readText()
          .then(text => { if (text) _termPaste(terminal, text); })
          .catch(() => {});
      } else if (action === 'selectall') {
        terminal.selectAll();
        const t = terminal.getSelection();
        if (t) navigator.clipboard.writeText(t).catch(() => {});
      } else if (action === 'search') {
        _showTermSearch(terminal, searchAddon, mountEl);
      } else if (action === 'clear') {
        terminal.clear();
      }
    });

    setTimeout(() => document.addEventListener('click', _closeTermCtxMenu, { once: true }), 0);
  });

  return { terminal, fitAddon, searchAddon };
}

// ── 内部工具函数 ────────────────────────────────────────────

function _termPaste(terminal, text) {
  if (typeof terminal.paste === 'function') {
    terminal.paste(text);
  } else {
    // xterm.js 较旧版本回退：触发 onData
    const handler = terminal.onData(d => { handler.dispose(); });
    const event = new KeyboardEvent('paste');
    Object.defineProperty(event, 'clipboardData', {
      value: { getData: () => text, items: [] },
    });
    terminal.element?.dispatchEvent(event);
  }
}

function _closeTermCtxMenu() {
  document.getElementById('_term-ctx-menu')?.remove();
}

// ── 搜索栏 ──────────────────────────────────────────────────

function _showTermSearch(terminal, searchAddon, mountEl) {
  // 已经存在时聚焦
  const existing = mountEl.parentElement?.querySelector('.term-search-bar');
  if (existing) { existing.querySelector('.term-search-input')?.focus(); return; }

  const bar = document.createElement('div');
  bar.className = 'term-search-bar';
  bar.innerHTML = `
    <svg class="term-search-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input class="term-search-input" type="text" placeholder="在终端中搜索..." autocomplete="off" spellcheck="false">
    <span class="term-search-count"></span>
    <button class="term-search-btn" data-dir="prev" title="上一个 (Shift+Enter)">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
    </button>
    <button class="term-search-btn" data-dir="next" title="下一个 (Enter)">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
    </button>
    <button class="term-search-close" title="关闭 (Esc)">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  `;

  // 插入到 mountEl 之前（在同一父容器中）
  mountEl.parentElement?.insertBefore(bar, mountEl);

  const input    = bar.querySelector('.term-search-input');
  const countEl  = bar.querySelector('.term-search-count');
  let   _lastTerm = '';

  const doSearch = (dir = 'next') => {
    const term = input.value;
    if (!term) {
      if (searchAddon.clearActiveDecoration) searchAddon.clearActiveDecoration();
      countEl.textContent = '';
      return;
    }
    const opts = { caseSensitive: false, incremental: dir === 'next' && term !== _lastTerm };
    _lastTerm = term;
    const found = dir === 'next'
      ? searchAddon.findNext(term, opts)
      : searchAddon.findPrevious(term, { caseSensitive: false });
    countEl.textContent = found ? '' : '未找到';
    countEl.style.color = found ? '' : 'var(--red)';
  };

  input.addEventListener('input', () => doSearch('next'));
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); doSearch(e.shiftKey ? 'prev' : 'next'); }
    if (e.key === 'Escape') closeSearch();
  });

  bar.querySelectorAll('[data-dir]').forEach(btn =>
    btn.addEventListener('click', () => doSearch(btn.dataset.dir))
  );

  const closeSearch = () => {
    if (searchAddon.clearActiveDecoration) searchAddon.clearActiveDecoration();
    bar.remove();
    terminal.focus();
  };
  bar.querySelector('.term-search-close').addEventListener('click', closeSearch);

  input.focus();
}
