/**
 * ws.js — WebSocket client with auto-reconnect and pub/sub message dispatch.
 *
 * Exposes window.voxWS:
 *   .send(msg: object)          – JSON-encode and send when connected
 *   .on(type: string, fn)       – register a handler for a message type
 *   .off(type: string, fn)      – unregister a handler
 */
(function () {
  'use strict';

  const handlers = {};
  let ws = null;
  let reconnectDelay = 500;

  function connect() {
    ws = new WebSocket('ws://' + location.host + '/ws');

    ws.addEventListener('open', function () {
      reconnectDelay = 500;
      ws.send(JSON.stringify({ type: 'hello' }));
    });

    ws.addEventListener('message', function (evt) {
      let msg;
      try { msg = JSON.parse(evt.data); } catch (_) { return; }
      const fns = handlers[msg.type];
      if (fns) fns.forEach(function (fn) { fn(msg); });
    });

    ws.addEventListener('close', function () {
      ws = null;
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    });

    ws.addEventListener('error', function () {
      ws && ws.close();
    });
  }

  window.voxWS = {
    send: function (msg) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
      }
    },
    on: function (type, fn) {
      if (!handlers[type]) handlers[type] = [];
      handlers[type].push(fn);
    },
    off: function (type, fn) {
      if (handlers[type]) {
        handlers[type] = handlers[type].filter(function (f) { return f !== fn; });
      }
    },
  };

  connect();
})();
