/* PenguinTerm — xterm.js wrapper with WebSocket transport.
 *
 * Binary WebSocket frames  = raw terminal I/O (both directions).
 * Text WebSocket frames    = JSON control messages (resize, ping, meta...).
 * The first binary frame after connect is the scrollback replay.
 */
'use strict';

class TerminalTab {
  constructor(session, options) {
    this.session = session;                  // session descriptor from the server
    this.options = options || {};
    this.onLatency = this.options.onLatency || (() => {});
    this.onStatus = this.options.onStatus || (() => {});
    this.onMeta = this.options.onMeta || (() => {});
    this.onExit = this.options.onExit || (() => {});
    this.onViewers = this.options.onViewers || (() => {});
    this.compatEnabled = !!this.options.compatEnabled;

    this.pane = document.createElement('div');
    this.pane.className = 'terminal-pane';
    this.pane.dataset.sessionId = session.id;

    this.term = new Terminal({
      fontFamily: getComputedStyle(document.body).getPropertyValue('--font-mono')
        || 'Consolas, monospace',
      fontSize: this.options.fontSize || 13,
      cursorStyle: this.options.cursorStyle || 'block',
      cursorBlink: this.options.cursorBlink !== false,
      scrollback: this.options.scrollback || 5000,
      allowProposedApi: true,
      convertEol: false,
      theme: {
        background: '#14161a', foreground: '#d7dade', cursor: '#e8e6e3',
        selectionBackground: '#3a4150',
        black: '#181a1f', red: '#d95f5f', green: '#6fbf73', yellow: '#d9b44a',
        blue: '#6da3d9', magenta: '#b48ead', cyan: '#6fd3d3', white: '#d7dade',
        brightBlack: '#555c68', brightRed: '#ff8a8a', brightGreen: '#93e39a',
        brightYellow: '#f0d078', brightBlue: '#93c5fd', brightMagenta: '#d8b4e2',
        brightCyan: '#9ef0f0', brightWhite: '#ffffff',
      },
    });

    this.fitAddon = new FitAddon.FitAddon();
    this.searchAddon = new SearchAddon.SearchAddon();
    this.webLinksAddon = new WebLinksAddon.WebLinksAddon();
    this.term.loadAddon(this.fitAddon);
    this.term.loadAddon(this.searchAddon);
    this.term.loadAddon(this.webLinksAddon);

    this.ws = null;
    this.pingTimer = null;
    this.reconnectTimer = null;
    this.intentionalClose = false;
    this.exited = false;
    this.lineBuffer = '';
    this.attachPromise = null;
  }

  /* Mount into the host element, open the socket, wire events. */
  mount(host) {
    host.appendChild(this.pane);
    this.term.open(this.pane);
    this.fitSoon();
    this.connect();

    this.term.onData((data) => this._handleInput(data));
    this.term.onTitleChange((title) => {
      if (typeof this.options.onTitle === 'function') this.options.onTitle(title);
    });

    this.resizeObserver = new ResizeObserver(() => this.fitSoon());
    this.resizeObserver.observe(this.pane);
    return this;
  }

  connect() {
    this._clearReconnect();
    this._stopPing();
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws/term/${encodeURIComponent(this.session.id)}`;
    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      this._scheduleReconnect();
      return;
    }
    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => {
      this._send({ type: 'resize', cols: this.term.cols, rows: this.term.rows });
      this.onStatus('connected');
      this._startPing();
    };
    this.ws.onmessage = (event) => this._handleMessage(event);
    this.ws.onclose = (event) => {
      this._stopPing();
      if (this.intentionalClose || this.exited) return;
      if (event.code === 4401 || event.code === 4403 || event.code === 4404) {
        // Refused by the server (auth gone / forbidden / session ended) — never retry.
        this.exited = true;
        const reason = event.code === 4401 ? 'authentication expired'
          : event.code === 4403 ? 'this session is not shared with you'
          : 'the session no longer exists';
        this.term.write(`\r\n\x1b[31m[PenguinTerm] ${reason}.\x1b[0m\r\n`);
        this.onStatus('closed');
        return;
      }
      this.onStatus('lost');
      this._scheduleReconnect();
    };
    this.ws.onerror = () => { this.onStatus('error'); };
  }

  _handleMessage(event) {
    if (event.data instanceof ArrayBuffer) {
      this.term.write(new Uint8Array(event.data));
      return;
    }
    if (typeof event.data !== 'string') return;
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    switch (msg.type) {
      case 'meta':
        this.session = Object.assign({}, this.session, msg);
        this.onMeta(this.session);
        this.focus();
        break;
      case 'replay_done':
        break;
      case 'exit':
        this.exited = true;
        this.term.write('\r\n\x1b[2m─── process exited ───\x1b[0m\r\n');
        this.onExit(msg.exit_code);
        break;
      case 'viewers':
        this.onViewers(msg.viewers || []);
        break;
      case 'denied':
        this.term.write('\r\n\x1b[31m[PenguinTerm] input not permitted in this session\x1b[0m\r\n');
        break;
      case 'compat':
        this.term.write(`\r\n\x1b[2m[compat] ${msg.note}\x1b[0m\r\n`);
        break;
      case 'pong': {
        const rtt = Math.round(performance.now() - this._pingSentAt);
        this.onLatency(rtt);
        break;
      }
      case 'error':
        this.term.write(`\r\n\x1b[31m[error] ${msg.detail || 'unknown'}\x1b[0m\r\n`);
        break;
    }
  }

  _handleInput(data) {
    if (this.exited) return;
    if (!this.compatEnabled) { this._sendBinary(data); return; }
    this._compatRoute(data);
  }

  /* Compat layer: complete single-line commands are translated server-side.
   * Tracks a best-effort line buffer; arrows/escape clear it (documented). */
  _compatRoute(data) {
    let pending = '';
    for (const ch of data) {
      if (ch === '\r') {
        const line = this.lineBuffer + pending;
        this.lineBuffer = ''; pending = '';
        const trimmed = line.trim();
        if (trimmed) {
          this._send({ type: 'complete_command', line });
        } else {
          this._sendBinary('\r');
        }
        continue;
      }
      if (ch === '\x7f' || ch === '\b') { this.lineBuffer = this.lineBuffer.slice(0, -1); pending = ''; continue; }
      if (ch === '\x03' || ch === '\x1b') { this.lineBuffer = ''; pending = ''; this._sendBinary(ch); continue; }
      if (ch.codePointAt(0) < 32) { this._sendBinary(ch); continue; }
      // printable: buffer only if we're at the start of a command line
      if (this.lineBuffer.length < 512) this.lineBuffer += ch; else pending += ch;
    }
    if (pending) { this._sendBinary(pending); this.lineBuffer = ''; }
  }

  _sendBinary(chunk) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(new TextEncoder().encode(chunk));
    }
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  _startPing() {
    this._stopPing();
    this.pingTimer = setInterval(() => {
      this._pingSentAt = performance.now();
      this._send({ type: 'ping' });
    }, 15000);
  }

  _stopPing() {
    if (this.pingTimer) { clearInterval(this.pingTimer); this.pingTimer = null; }
  }

  _scheduleReconnect() {
    if (this.reconnectTimer || this.intentionalClose || this.exited) return;
    this.onStatus('reconnecting');
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      // reset so replayed scrollback doesn't duplicate
      this.term.reset();
      this.connect();
    }, 1500);
  }

  _clearReconnect() {
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
  }

  fitSoon() {
    requestAnimationFrame(() => {
      try {
        this.fitAddon.fit();
        this._send({ type: 'resize', cols: this.term.cols, rows: this.term.rows });
      } catch (_) { /* pane not visible yet */ }
    });
  }

  setFontSize(px) { this.term.options.fontSize = px; this.fitSoon(); }
  setCursor(style, blink) {
    this.term.options.cursorStyle = style; this.term.options.cursorBlink = blink;
  }
  setScrollback(lines) { this.term.options.scrollback = lines; }
  focus() { setTimeout(() => this.term.focus(), 30); }
  show() { this.pane.classList.add('active'); this.fitSoon(); this.focus(); }
  hide() { this.pane.classList.remove('active'); }
  dispose() {
    this.intentionalClose = true;
    this._clearReconnect();
    this._stopPing();
    if (this.resizeObserver) this.resizeObserver.disconnect();
    if (this.ws) { try { this.ws.close(); } catch (_) {} }
    this.term.dispose();
    this.pane.remove();
  }
}

window.TerminalTab = TerminalTab;
