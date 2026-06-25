/**
 * grid.js — CSS grid layout for 1–6 terminal panes.
 *
 * Exposes window.voxGrid:
 *   .addPane(paneId, accentColor)   — create a pane cell, return its container element
 *   .removePane(paneId)             — remove cell and recompute grid
 *   .paneIds()                      — ordered list of active pane IDs
 */
(function () {
  'use strict';

  var grid = document.getElementById('terminal-grid');
  var cells = {};   // paneId -> div.pane-wrap

  // Grid template areas for 1-6 panes (rows x cols)
  var LAYOUTS = [
    null,
    '"a"',                              // 1
    '"a b"',                            // 2
    '"a b" "a c"',                      // 3
    '"a b" "c d"',                      // 4
    '"a b" "c d" "e e"',               // 5
    '"a b" "c d" "e f"',               // 6
  ];

  function recompute() {
    var ids = Object.keys(cells);
    var n = Math.min(ids.length, 6);

    if (n === 0) {
      grid.style.gridTemplateAreas = '';
      grid.style.gridTemplateColumns = '';
      grid.style.gridTemplateRows = '';
      return;
    }

    // Assign area letters a-f
    var letters = 'abcdef';
    ids.forEach(function (id, i) {
      cells[id].style.gridArea = letters[i] || 'f';
    });

    grid.style.gridTemplateAreas = LAYOUTS[n];
    grid.style.gridTemplateColumns = n === 1 ? '1fr' : '1fr 1fr';
    grid.style.gridTemplateRows = 'repeat(' + Math.ceil(n / 2) + ', 1fr)';

    // Re-fit terminals after layout change
    setTimeout(function () { window.voxTerminal.fitAll(); }, 50);
  }

  function addPane(paneId, accentColor) {
    var wrap = document.createElement('div');
    wrap.className = 'pane-wrap';
    wrap.dataset.paneId = paneId;
    if (accentColor) wrap.style.borderColor = accentColor;
    grid.appendChild(wrap);
    cells[paneId] = wrap;
    recompute();
    return wrap;
  }

  function removePane(paneId) {
    var wrap = cells[paneId];
    if (!wrap) return;
    wrap.remove();
    delete cells[paneId];
    recompute();
  }

  function paneIds() {
    return Object.keys(cells);
  }

  window.voxGrid = { addPane: addPane, removePane: removePane, paneIds: paneIds };
})();
