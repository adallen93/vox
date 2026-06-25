/**
 * layers.js — layer sidebar: vox layer + terminal layers.
 *
 * Layer model (localStorage key "vox_layers"):
 *   [{ id, name, accent, type: "vox"|"terminal", paneIds: [] }]
 *
 * The built-in "vox" layer is always first and is not deletable.
 * Entering it sends vox_activate; leaving sends vox_deactivate.
 * Terminal layers show the xterm.js grid.
 */
(function () {
  'use strict';

  var STORAGE_KEY  = 'vox_layers';
  var VOX_LAYER_ID = '__vox__';

  var list       = document.getElementById('layer-list');
  var addBtn     = document.getElementById('add-layer-btn');
  var panel      = document.getElementById('panel');
  var transcript = document.getElementById('transcript');
  var grid       = document.getElementById('terminal-grid');

  var availableProfiles = [];
  var activeLayerId = VOX_LAYER_ID;

  // --- persistence ---

  function loadLayers() {
    var vox = { id: VOX_LAYER_ID, name: 'vox', accent: '#4a9eff', type: 'vox', paneIds: [] };
    try {
      var stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      var terminals = stored.filter(function (l) { return l.type === 'terminal'; });
      return [vox].concat(terminals);
    } catch (_) {
      return [vox];
    }
  }

  function saveLayers(layers) {
    var terminals = layers.filter(function (l) { return l.type === 'terminal'; });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(terminals));
  }

  var layers = loadLayers();

  // --- view ---

  function render() {
    list.innerHTML = '';
    layers.forEach(function (layer) {
      var btn = document.createElement('button');
      btn.className = 'layer-btn' + (layer.id === activeLayerId ? ' active' : '');
      btn.textContent = layer.name;
      btn.style.setProperty('--accent', layer.accent);
      btn.addEventListener('click', function () { activateLayer(layer.id); });

      if (layer.type === 'terminal') {
        btn.addEventListener('contextmenu', function (e) {
          e.preventDefault();
          removeLayer(layer.id);
        });
      }

      list.appendChild(btn);
    });
  }

  // --- layer switching ---

  function activateLayer(id) {
    if (id === activeLayerId) return;

    var prev = layers.find(function (l) { return l.id === activeLayerId; });
    var next = layers.find(function (l) { return l.id === id; });
    if (!next) return;

    // Deactivate previous
    if (prev && prev.type === 'vox') {
      window.voxWS.send({ type: 'vox_deactivate' });
    }

    activeLayerId = id;

    // Show/hide content areas
    if (next.type === 'vox') {
      panel.style.display = '';
      transcript.style.display = '';
      grid.classList.remove('visible');
    } else {
      panel.style.display = 'none';
      transcript.style.display = 'none';
      grid.classList.add('visible');
    }

    render();
  }

  // --- terminal layer management ---

  function addTerminalLayer() {
    var accent = randomAccent();
    var layer = {
      id: 'layer-' + Date.now(),
      name: 'terminal',
      accent: accent,
      type: 'terminal',
      paneIds: [],
    };
    layers.push(layer);
    saveLayers(layers);
    render();
    activateLayer(layer.id);
    spawnPane(layer, availableProfiles[0] || { name: 'Windows PowerShell', commandline: 'powershell.exe' });
  }

  function removeLayer(id) {
    var layer = layers.find(function (l) { return l.id === id; });
    if (!layer || layer.type === 'vox') return;

    layer.paneIds.forEach(function (paneId) {
      window.voxWS.send({ type: 'pty_close', pane_id: paneId });
      window.voxTerminal.destroy(paneId);
      window.voxGrid.removePane(paneId);
    });

    layers = layers.filter(function (l) { return l.id !== id; });
    saveLayers(layers);

    if (activeLayerId === id) activateLayer(VOX_LAYER_ID);
    else render();
  }

  // --- pane spawning ---

  function spawnPane(layer, profile) {
    window.voxWS.send({ type: 'pty_spawn', profile: profile });
    // pane_opened message → onPaneOpened will attach the terminal
    layer._pendingProfile = profile;
    layer._pendingAccent  = layer.accent;
  }

  function onPaneOpened(paneId) {
    var layer = layers.find(function (l) { return l.id === activeLayerId && l.type === 'terminal'; });
    if (!layer) return;

    layer.paneIds.push(paneId);
    saveLayers(layers);

    var container = window.voxGrid.addPane(paneId, layer.accent);
    window.voxTerminal.create(paneId, container, layer.accent);
  }

  // --- helpers ---

  var ACCENTS = ['#4a9eff', '#ff4a4a', '#ffa54a', '#4aff9e', '#c084fc', '#fb923c'];
  var _accentIdx = 1;
  function randomAccent() {
    return ACCENTS[(_accentIdx++) % ACCENTS.length];
  }

  // --- wire up ---

  addBtn.addEventListener('click', addTerminalLayer);

  window.voxWS.on('profiles', function (msg) {
    availableProfiles = msg.profiles || [];
  });

  window.voxWS.on('pane_opened', function (msg) {
    onPaneOpened(msg.pane_id);
  });

  render();
})();
