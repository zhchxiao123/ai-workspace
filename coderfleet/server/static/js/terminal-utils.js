// ── 终端增强工具 ──────────────────────────────────────────
// 适用于项目内嵌终端和多 Tab 终端，统一管理：
//   - WebLinksAddon（可点击 URL）
//   - SearchAddon（Ctrl+F 搜索）
//   - 选中自动复制
//   - Ctrl+Shift+C/V 复制粘贴
//   - 右键 context menu

function createEnhancedTerminal(mountEl, options = {}) {
  const interceptScroll = options.interceptScroll !== false;
  const onScrollGesture = typeof options.onScrollGesture === 'function'
    ? options.onScrollGesture
    : null;
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

  // ── 滚轮 & 触摸屏始终滚动视口，不转发给后端 ────────────
  // xterm.js 在鼠标追踪模式（tmux / vim 等程序开启 \e[?1000h）下会把
  // 滚轮/触摸事件转成鼠标转义码发往后端，bash 将其解释为上/下方向键
  // 从而滚动命令历史，而不是滚动终端内容。
  // 使用 capture:true 在 xterm 处理之前拦截事件，手动调用 scrollLines。
  const _termEl = terminal.element;
  if (_termEl && interceptScroll) {
    // 鼠标滚轮 & 触摸板（touchpad 生成 wheel 事件，不是 touch 事件）
    // 触摸板：deltaMode=0（像素），每次 deltaY 很小（1-5px），需要累加到一行阈值再滚动
    // 物理鼠标滚轮：deltaMode=1（行）或 deltaMode=0 但 deltaY 较大（100px+）
    let _wheelPixelAccum = 0;
    _termEl.addEventListener('wheel', (e) => {
      e.preventDefault();
      e.stopPropagation();
      let lines;
      if (e.deltaMode === WheelEvent.DOM_DELTA_LINE) {
        // 行模式：deltaY 直接是行数（物理鼠标滚轮常见）
        lines = Math.round(e.deltaY);
      } else if (e.deltaMode === WheelEvent.DOM_DELTA_PAGE) {
        lines = Math.round(e.deltaY) * (terminal.rows || 24);
      } else {
        // 像素模式（触摸板）：累加像素，满一行（20px）才实际滚动，保证顺滑
        _wheelPixelAccum += e.deltaY;
        lines = Math.trunc(_wheelPixelAccum / 20);
        _wheelPixelAccum -= lines * 20;
      }
      if (lines !== 0) {
        if (onScrollGesture) onScrollGesture(lines);
        else terminal.scrollLines(lines);
      }
    }, { passive: false, capture: true });

    // 触摸屏：记录起始 Y，在 touchmove 中计算位移后滚动
    let _touchStartY = 0;
    let _touchLastY  = 0;
    _termEl.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      _touchStartY = e.touches[0].clientY;
      _touchLastY  = _touchStartY;
    }, { passive: true, capture: true });

    _termEl.addEventListener('touchmove', (e) => {
      if (e.touches.length !== 1) return;
      e.preventDefault();
      e.stopPropagation();
      const currentY = e.touches[0].clientY;
      const deltaY   = _touchLastY - currentY;   // 向上滑 → deltaY > 0 → 内容向上（视口向下）
      _touchLastY    = currentY;
      const lines = Math.round(Math.abs(deltaY) / 20) || 1;
      if (Math.abs(deltaY) >= 2) {
        const signedLines = deltaY > 0 ? lines : -lines;
        if (onScrollGesture) onScrollGesture(signedLines);
        else terminal.scrollLines(signedLines);
      }
    }, { passive: false, capture: true });

    _termEl.addEventListener('touchend', () => {
      _touchStartY = 0;
      _touchLastY  = 0;
    }, { passive: true, capture: true });
  }

  // ── IME 中文/日文/韩文输入法支持 ──────────────────────
  // xterm.js 在部分浏览器/IME（fcitx、ibus、搜狗等）下存在竞态：
  // xterm 自身的 compositionend 处理可能未能通过 onData 发出最终组合字符。
  //
  // 两步去重策略：
  //   1. imeFilter 记录 xterm 自己触发的 onData（_imeXtermSent）并放行
  //   2. compositionend 的 rAF 里检查：若 xterm 已发则跳过；
  //      若未发则直接调用外层发送回调，避免 terminal.paste() 再触发
  //      xterm 输入链造成重复。
  let _imeComposing       = false;
  let _imeLastData        = '';
  let _imeSendData        = null;
  const _imeXtermSent     = [];   // [{text, ts}]

  const _imeXtermClearOld = () => {
    const cutoff = Date.now() - 300;
    while (_imeXtermSent.length && _imeXtermSent[0].ts < cutoff) _imeXtermSent.shift();
  };

  const _imeRememberSent = (text) => {
    if (!/[^\x00-\x7F]/.test(text)) return;
    _imeXtermClearOld();
    _imeXtermSent.push({ text, ts: Date.now() });
  };

  // textarea 在 terminal.open() 之后同步创建，可以直接访问
  const _ta = terminal.textarea;
  if (_ta) {
    _ta.addEventListener('compositionstart', () => {
      _imeComposing = true;
      _imeLastData  = '';
    }, true);

    _ta.addEventListener('compositionupdate', (e) => {
      _imeLastData = e.data || '';
    }, true);

    _ta.addEventListener('compositionend', (e) => {
      _imeComposing = false;
      const composed = e.data || _imeLastData;
      _imeLastData = '';
      if (!composed) return;

      // rAF 让 xterm 自身的 compositionend → setTimeout(0) → input 事件链先跑完
      // 注意：浏览器的 input 事件有时会在 rAF 之后才到（可达 100ms+），
      // 所以这里【不删除】_imeXtermSent 里的记录，让它在 300ms 后自然过期。
      // 这样 imeFilter 的 300ms 窗口才能拦截住所有迟到的重复 onData 调用。
      requestAnimationFrame(() => {
        _imeXtermClearOld();
        const found = _imeXtermSent.some(p => p.text === composed);
        if (found) {
          // xterm 已发出（记录保留 300ms，用于拦截迟到的 input 事件重复）
          return;
        }
        // xterm 未发送（IME 竞态失败）— 由我们直接兜底发送；
        // 也先加入 sent 列表，防止后续迟到的 xterm onData 造成二次发送。
        // 不使用 terminal.paste()，因为 paste 会再次进入 xterm 输入链，
        // 在部分浏览器/输入法组合下会和原生 composition/input 事件互相重复。
        _imeRememberSent(composed);
        if (typeof _imeSendData === 'function') _imeSendData(composed);
      });
    }, true);
  }

  // ── 选中自动复制（Linux 终端惯例）──────────────────────
  terminal.onSelectionChange(() => {
    const text = terminal.getSelection();
    if (text) navigator.clipboard.writeText(text).catch(() => {});
  });

  // ── 键盘快捷键 ─────────────────────────────────────────
  terminal.attachCustomKeyEventHandler(e => {
    if (e.type !== 'keydown') return true;

    // 屏蔽 IME 组合期间的 keydown，防止拼音原始字母被发送到后端
    // e.isComposing：W3C 标准，主流浏览器支持
    // keyCode 229：部分浏览器在 IME 处理键时发送的 Process 键值
    // _imeComposing：自己追踪的补充标志（Firefox 等顺序不同时保底）
    if (e.isComposing || e.keyCode === 229 || _imeComposing) return false;

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

  // imeFilter(fn) 包装 onData 回调，与上面 IME 逻辑配合去重
  const imeFilter = (fn) => {
    _imeSendData = fn;
    return (data) => {
      // 这是 xterm 自身触发的 onData
      if (/[^\x00-\x7F]/.test(data)) {
        _imeXtermClearOld();
        // 浏览器会对同一次 compositionend 触发多次 onData（compositionend + input 事件）
        // 且 input 事件可能比 rAF 更晚到（100ms+），所以用 300ms 窗口去重。
        // 关键：_imeXtermSent 里的记录在 rAF 里【不删除】，保留到自然过期（300ms）。
        const existing = _imeXtermSent.find(p => p.text === data && Date.now() - p.ts < 300);
        if (existing) return;   // 300ms 内同文本已发过 → 重复，丢弃
        _imeRememberSent(data);
      }
      fn(data);
    };
  };

  return { terminal, fitAddon, searchAddon, imeFilter };
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
