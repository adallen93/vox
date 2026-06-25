/**
 * panel.js — vox layer UI: state ring, play/pause button, transcript.
 */
(function () {
  'use strict';

  var ring       = document.getElementById('ring');
  var btn        = document.getElementById('toggle-btn');
  var stateLabel = document.getElementById('state-label');
  var idleCue    = document.getElementById('idle-cue');
  var transcript = document.getElementById('transcript');

  var active = false;  // true after vox_activate sent; false after vox_deactivate

  // --- button ---
  btn.addEventListener('click', function () {
    if (!active) {
      active = true;
      idleCue.style.display = 'none';
      stateLabel.textContent = 'initializing…';
      window.voxWS.send({ type: 'vox_activate' });
    } else {
      window.voxWS.send({ type: 'trigger' });
    }
  });

  // --- state ---
  window.voxWS.on('state', function (msg) {
    var s = msg.state || 'idle';
    ring.setAttribute('data-state', s);
    stateLabel.textContent = s;

    var labels = { idle: '▶', recording: '■', transcribing: '…', responding: '❙❙' };
    btn.textContent = labels[s] || '▶';
  });

  // --- transcript helpers ---
  function append(text, cls) {
    var span = document.createElement('span');
    span.className = cls;
    span.textContent = text;
    transcript.appendChild(span);
    transcript.scrollTop = transcript.scrollHeight;
  }

  window.voxWS.on('user_text', function (msg) {
    append('you> ', 't-user-pfx');
    append(msg.text + '\n', 't-user');
  });

  window.voxWS.on('delta', function (msg) {
    append(msg.text, 't-asst');
    transcript.scrollTop = transcript.scrollHeight;
  });

  window.voxWS.on('tool', function (msg) {
    append('[tool: ' + msg.name + ']\n', 't-tool');
  });

  window.voxWS.on('turn_complete', function () {
    append('\n', 't-asst');
  });

  // Server-side vox session ended (task crashed or session closed cleanly)
  window.voxWS.on('vox_ended', function () {
    active = false;
    idleCue.textContent = 'Click ► to activate voice';
    idleCue.style.display = '';
    btn.textContent = '►';
  });
})();
