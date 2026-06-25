/**
 * terminal.js — xterm.js pane lifecycle.
 *
 * Exposes window.voxTerminal:
 *   .create(paneId, container, accentColor) -> Terminal instance
 *   .destroy(paneId)
 *   .write(paneId, b64data)
 *   .fit(paneId)
 *   .fitAll()
 */
(function () {
  'use strict';

  var panes = {};  // paneId -> { term, fitAddon, container, resizeObserver }

  function create(paneId, container, accentColor) {
    var term = new Terminal({
      theme: {
        background:  '#000000',
        foreground:  '#c9d1d9',
        cursor:      accentColor || '#4a9eff',
        selectionBackground: '#264f78',
      },
      fontFamily: '"Cascadia Code", Consolas, "Courier New", monospace',
      fontSize: 13,
      scrollback: 5000,
      allowTransparency: false,
    });

    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);

    // Fit after first render
    setTimeout(function () { fitAddon.fit(); sendResize(paneId, term); }, 0);

    // Re-fit on container resize
    var ro = new ResizeObserver(function () {
      fitAddon.fit();
      sendResize(paneId, term);
    });
    ro.observe(container);

    // Forward keystrokes to server
    term.onData(function (data) {
      window.voxWS.send({
        type: 'pty_input',
        pane_id: paneId,
        data: btoa(data),
      });
    });

    panes[paneId] = { term: term, fitAddon: fitAddon, container: container, ro: ro };
    return term;
  }

  function sendResize(paneId, term) {
    window.voxWS.send({
      type: 'pty_resize',
      pane_id: paneId,
      cols: term.cols,
      rows: term.rows,
    });
  }

  function destroy(paneId) {
    var p = panes[paneId];
    if (!p) return;
    p.ro.disconnect();
    p.term.dispose();
    delete panes[paneId];
  }

  function write(paneId, b64data) {
    var p = panes[paneId];
    if (p) p.term.write(Uint8Array.from(atob(b64data), function (c) { return c.charCodeAt(0); }));
  }

  function fit(paneId) {
    var p = panes[paneId];
    if (p) { p.fitAddon.fit(); sendResize(paneId, p.term); }
  }

  function fitAll() {
    Object.keys(panes).forEach(fit);
  }

  // Route server messages
  window.voxWS.on('pty_output', function (msg) { write(msg.pane_id, msg.data); });
  window.voxWS.on('pty_closed', function (msg) {
    var p = panes[msg.pane_id];
    if (p) p.term.writeln('\r\n\x1b[90m[process exited]\x1b[0m');
  });

  window.voxTerminal = { create: create, destroy: destroy, write: write, fit: fit, fitAll: fitAll };
})();
