// The studio page. The server owns the project; this page draws on its own copy for speed, sends every
// change as an operation, and takes over what others change (an agent, another tab) as it arrives.
(() => {
"use strict";
const $ = id => document.getElementById(id);
const TOOLS = [
  ["pencil", "✎", "B", "Pencil"], ["eraser", "◻", "E", "Eraser"], ["fill", "◆", "G", "Fill"], ["picker", "◉", "I", "Pick a colour"],
  ["line", "╱", "L", "Line"], ["rect", "▭", "R", "Rectangle (Shift fills it)"], ["select", "⬚", "M", "Select a box"],
  ["lasso", "◌", "Q", "Lasso: draw around an area"], ["polygon", "⬠", "W", "Polygon: click the corners, Enter closes"],
  ["move", "✥", "V", "Move the selection or the whole cel"], ["hand", "✋", "H", "Pan (or hold Space)"]];
const S = { doc: null, meta: {}, cels: [], frame: 0, layer: 0, tool: "pencil", colour: "#f2a65a", size: 1, mirror: false,
  zoom: 8, panX: 0, panY: 0, sel: null, float: null, clip: null, playing: false, timer: 0, pending: 0, drawing: false,
  known: 0, preview: null, hover: null, space: false, fitted: false };
const SIZES = [1, 2, 3, 4, 6, 8, 12, 16];
const view = $("view"), over = $("over"), stage = $("stage");

// ---------------------------------------------------------------- talking to the server
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "The studio did not answer");
  return data;
}
let line = Promise.resolve();
function send(path, body) { const run = line.then(() => api(path, body)); line = run.catch(() => {}); return run; }
function toast(text, bad) {
  const t = $("toast"); t.textContent = text; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.hidden = true, bad ? 5000 : 2200);
}
const fail = error => toast(error.message, true);
function blank() { const c = document.createElement("canvas"); c.width = S.doc.width; c.height = S.doc.height; return c; }
async function decode(data) {
  const image = new Image(); image.src = "data:image/png;base64," + data; await image.decode();
  const c = blank(); c.getContext("2d").drawImage(image, 0, 0); return c;
}
async function load(flash) {
  const state = await api("/api/state");
  S.doc = state.doc; S.meta = state.meta; S.known = Math.max(S.known, state.meta.rev);
  S.cels = await Promise.all(state.doc.layers.map(layer => Promise.all(layer.cels.map(c => c ? decode(c) : null))));
  S.frame = Math.min(S.frame, S.doc.frames.length - 1); S.layer = Math.min(S.layer, S.doc.layers.length - 1);
  view.width = S.doc.width; view.height = S.doc.height;
  if (!S.fitted) { fit(); S.fitted = true; }
  if (!S.chatShown) { state.chat.forEach(showMessage); S.chatShown = true; }
  chrome(); timeline(); draw();
  if (flash) { stage.classList.remove("flash"); void stage.offsetWidth; stage.classList.add("flash"); }
}
async function op(operation) {             // a change to the structure: send it, then take the result over
  drop(); if (S.proposal) endProposal(); S.pending++;
  try { const done = await send("/api/op", { op: operation }); S.known = Math.max(S.known, done.rev); await load(); if (S.findings) runCheck(); return done; }
  catch (error) { fail(error); } finally { S.pending--; }
}
async function commit() {                  // the current cel changed under the pencil: send it as it is
  const layer = S.layer, frame = S.frame, cel = S.cels[layer][frame];
  if (!cel) return;
  thumbs(layer, frame); S.pending++;
  try {
    const everywhere = $("spread").checked && S.doc.frames.length > 1;
    const done = await send("/api/op", { op: { op: "set-cel", layer, frame, png: cel.toDataURL("image/png").split(",")[1], ...(everywhere ? { spread: { frames: "all" } } : {}) } });
    S.known = Math.max(S.known, done.rev); S.meta.undo = (S.meta.undo || 0) + 1; S.meta.redo = 0; S.meta.last = [done.note, "you"]; chrome();
    if (everywhere || S.doc.locked) await load();           // other frames changed, or the palette moved the colours
  } catch (error) { fail(error); await load(); } finally { S.pending--; }
}
function whenIdle(run) { S.drawing || S.pending ? setTimeout(() => whenIdle(run), 120) : run(); }

// ---------------------------------------------------------------- drawing the picture
function cel(create) {
  if (!S.cels[S.layer][S.frame] && create) S.cels[S.layer][S.frame] = blank();
  return S.cels[S.layer][S.frame];
}
function paintFrame(ctx, frame, alpha) {
  S.doc.layers.forEach((layer, i) => {
    if (!layer.visible) return;
    ctx.globalAlpha = alpha * layer.opacity;
    const tried = S.proposal && S.proposal.after && !S.proposal.before && i === S.proposal.layer && alpha === 1 ? S.proposal.after[frame] : null;
    if (tried) ctx.drawImage(tried, 0, 0); else if (S.cels[i][frame]) ctx.drawImage(S.cels[i][frame], 0, 0);
    if (S.float && i === S.layer && frame === S.frame && alpha === 1) ctx.drawImage(S.float.canvas, S.float.x, S.float.y);
  });
  ctx.globalAlpha = 1;
}
function draw() {
  if (!S.doc) return;
  const ctx = view.getContext("2d"); ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, view.width, view.height);
  if ($("onion").checked && !S.playing) {
    if (S.frame > 0) paintFrame(ctx, S.frame - 1, 0.3);
    if (S.frame < S.doc.frames.length - 1) paintFrame(ctx, S.frame + 1, 0.16);
  }
  paintFrame(ctx, S.frame, 1);
  view.style.width = S.doc.width * S.zoom + "px"; view.style.height = S.doc.height * S.zoom + "px";
  view.style.left = S.panX + "px"; view.style.top = S.panY + "px";
  $("hint").hidden = S.cels.some(row => row.some(Boolean)) || !!S.float;
  overlay();
}
function edge(ctx, mask, left, top, w, h) {  // stroke the outline of the marked cells of a mask
  const z = S.zoom, has = (x, y) => x >= 0 && y >= 0 && x < w && y < h && mask[y * w + x];
  ctx.beginPath();
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    if (!has(x, y)) continue; const l = S.panX + (left + x) * z + 0.5, t = S.panY + (top + y) * z + 0.5;
    if (!has(x, y - 1)) { ctx.moveTo(l, t); ctx.lineTo(l + z, t); } if (!has(x, y + 1)) { ctx.moveTo(l, t + z); ctx.lineTo(l + z, t + z); }
    if (!has(x - 1, y)) { ctx.moveTo(l, t); ctx.lineTo(l, t + z); } if (!has(x + 1, y)) { ctx.moveTo(l + z, t); ctx.lineTo(l + z, t + z); }
  }
  ctx.stroke();
}
function overlay() {
  const ratio = devicePixelRatio || 1, w = stage.clientWidth, h = stage.clientHeight;
  if (over.width !== w * ratio || over.height !== h * ratio) { over.width = w * ratio; over.height = h * ratio; }
  const ctx = over.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, w, h);
  const z = S.zoom, X = x => S.panX + x * z, Y = y => S.panY + y * z;
  if ($("grid").checked && z >= 6) {
    ctx.strokeStyle = "rgba(255,255,255,0.07)"; ctx.lineWidth = 1; ctx.beginPath();
    for (let x = 1; x < S.doc.width; x++) { ctx.moveTo(Math.round(X(x)) + 0.5, Y(0)); ctx.lineTo(Math.round(X(x)) + 0.5, Y(S.doc.height)); }
    for (let y = 1; y < S.doc.height; y++) { ctx.moveTo(X(0), Math.round(Y(y)) + 0.5); ctx.lineTo(X(S.doc.width), Math.round(Y(y)) + 0.5); }
    ctx.stroke();
  }
  if (S.mirror) { ctx.strokeStyle = "rgba(94,196,200,0.6)"; ctx.beginPath(); ctx.moveTo(X(S.doc.width / 2), Y(0)); ctx.lineTo(X(S.doc.width / 2), Y(S.doc.height)); ctx.stroke(); }
  const box = S.float ? { x: S.float.x, y: S.float.y, w: S.float.canvas.width, h: S.float.canvas.height, mask: S.float.mask } : S.sel;
  if (box) {
    ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
    for (const [colour, offset] of [["#000", 0], ["#fff", 4]]) {
      ctx.strokeStyle = colour; ctx.lineDashOffset = offset;
      if (!box.mask) { ctx.strokeRect(X(box.x) + 0.5, Y(box.y) + 0.5, box.w * z, box.h * z); continue; }
      edge(ctx, box.mask, box.x, box.y, box.w, box.h);
    }
    ctx.setLineDash([]);
  }
  const outline = (drag && drag.path) || S.poly;   // a lasso being drawn, or a polygon being clicked together
  if (outline && outline.length) {
    ctx.strokeStyle = "#5ec4c8"; ctx.lineWidth = 1.5; ctx.beginPath(); outline.forEach(([x, y], i) => i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y)));
    if (S.poly && S.hoverAt) ctx.lineTo(X(S.hoverAt[0]), Y(S.hoverAt[1])); ctx.stroke();
    if (S.poly) { ctx.fillStyle = "#5ec4c8"; for (const [x, y] of S.poly) ctx.fillRect(X(x) - 2, Y(y) - 2, 4, 4); }
  }
  if (!$("loopPanel").hidden) {             // what is painted to move, each kind in its colour, the one in hand stronger
    const w = S.doc.width;
    for (const region of S.regions) {       // a tint, and an outline that shows on any picture
      ctx.fillStyle = MOVES[region.kind][0]; ctx.globalAlpha = region === S.paint ? 0.45 : 0.22;
      region.mask.forEach((on, i) => { if (on) ctx.fillRect(X(i % w), Y(Math.floor(i / w)), z, z); });
      ctx.globalAlpha = 1; ctx.lineWidth = region === S.paint ? 3 : 2;
      ctx.strokeStyle = "#0b0c10"; edge(ctx, region.mask, 0, 0, w, S.doc.height);
      ctx.lineWidth = region === S.paint ? 1.5 : 1; ctx.strokeStyle = MOVES[region.kind][0]; edge(ctx, region.mask, 0, 0, w, S.doc.height);
    }
    ctx.lineWidth = 1;
  }
  if (S.findings) {                         // what the check found in this frame, each kind in its colour
    for (const [kind, [colour]] of Object.entries(KINDS)) {
      ctx.fillStyle = colour; const size = Math.max(2, Math.round(z / 3));
      for (const [x, y] of S.findings.cells[kind][S.frame] || []) ctx.fillRect(X(x) + (z - size) / 2, Y(y) + (z - size) / 2, size, size);
    }
  }
  if (S.proposal) {                         // the cells of this frame that the proposed change would touch
    ctx.strokeStyle = "#5ec4c8"; ctx.lineWidth = 1.5;
    for (const [x, y] of S.proposal.marks[S.frame] || []) ctx.strokeRect(X(x) + 1.5, Y(y) + 1.5, z - 3, z - 3);
  }
  if (S.preview) {
    ctx.fillStyle = S.tool === "eraser" ? "rgba(255,255,255,0.35)" : S.colour;
    for (const [x, y] of S.preview) ctx.fillRect(X(x), Y(y), z, z);
  } else if (S.hover && ["pencil", "eraser", "fill", "line", "rect"].includes(S.tool)) {
    ctx.strokeStyle = "rgba(255,255,255,0.7)"; const half = Math.floor((S.size - 1) / 2);
    const n = S.tool === "pencil" || S.tool === "eraser" ? S.size : 1;
    ctx.strokeRect(X(S.hover[0] - (n > 1 ? half : 0)) + 0.5, Y(S.hover[1] - (n > 1 ? half : 0)) + 0.5, n * z, n * z);
  }
}
function taken() {                          // the width an open panel takes from the left of the stage
  const open = [...stage.querySelectorAll(".findings")].find(panel => !panel.hidden);
  return open && stage.clientWidth > 2.5 * open.offsetWidth ? open.offsetWidth + 24 : 0;
}
function fit() {
  const room = Math.min((stage.clientWidth - taken() - 60) / S.doc.width, (stage.clientHeight - 60) / S.doc.height);
  S.zoom = Math.max(1, Math.min(40, Math.floor(room))); centre();
}
function centre() { const left = taken(); S.panX = left + Math.round((stage.clientWidth - left - S.doc.width * S.zoom) / 2); S.panY = Math.round((stage.clientHeight - S.doc.height * S.zoom) / 2); }
function panel(id, open) {                  // one panel at a time; the picture moves out from under it unless the person placed it
  for (const other of stage.querySelectorAll(".findings")) other.hidden = other.id !== id || !open;
  if ($("findings").hidden) S.findings = null;
  if ($("loopPanel").hidden) S.paint = null;
  if (S.doc && !S.moved) { fit(); chrome(); }
  draw();
}

// ---------------------------------------------------------------- tools
function dots(x, y) {                       // the cells one touch of the brush covers
  const out = [], half = Math.floor((S.size - 1) / 2);
  for (let dy = 0; dy < S.size; dy++) for (let dx = 0; dx < S.size; dx++) {
    out.push([x - half + dx, y - half + dy]);
    if (S.mirror) out.push([S.doc.width - 1 - (x - half + dx), y - half + dy]);
  }
  return out;
}
function between(x0, y0, x1, y1) {          // every cell on the way from one to the other
  const out = [], steps = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0), 1);
  for (let i = 0; i <= steps; i++) out.push([Math.round(x0 + (x1 - x0) * i / steps), Math.round(y0 + (y1 - y0) * i / steps)]);
  return out;
}
function inSel(x, y) {                       // drawing stays inside the selection, when there is one
  const s = S.sel; if (!s) return true;
  return x >= s.x && y >= s.y && x < s.x + s.w && y < s.y + s.h && (!s.mask || !!s.mask[(y - s.y) * s.w + x - s.x]);
}
function shapeFrom(points) {                // the cells whose centre lies inside a drawn outline
  if (points.length < 3) return null;
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const l = Math.max(0, Math.floor(Math.min(...xs))), t = Math.max(0, Math.floor(Math.min(...ys))), r = Math.min(S.doc.width, Math.ceil(Math.max(...xs))), b = Math.min(S.doc.height, Math.ceil(Math.max(...ys)));
  if (r <= l || b <= t) return null;
  const w = r - l, h = b - t, mask = new Uint8Array(w * h); let any = false;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const px = l + x + 0.5, py = t + y + 0.5; let inside = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
      const [x1, y1] = points[i], [x2, y2] = points[j];
      if ((y1 > py) !== (y2 > py) && px < (x2 - x1) * (py - y1) / (y2 - y1) + x1) inside = !inside;
    }
    if (inside) { mask[y * w + x] = 1; any = true; }
  }
  return any ? { x: l, y: t, w, h, mask } : null;
}
function put(cells, erase) {
  const ctx = cel(true).getContext("2d"); ctx.fillStyle = S.colour;
  for (const [x, y] of cells) if (inSel(x, y)) erase ? ctx.clearRect(x, y, 1, 1) : ctx.fillRect(x, y, 1, 1);
}
function boxCells(x0, y0, x1, y1, filled) {
  const out = [], l = Math.min(x0, x1), r = Math.max(x0, x1), t = Math.min(y0, y1), b = Math.max(y0, y1);
  for (let y = t; y <= b; y++) for (let x = l; x <= r; x++) if (filled || x === l || x === r || y === t || y === b) out.push([x, y]);
  return out;
}
function flood(x, y) {
  const c = cel(true), ctx = c.getContext("2d"), w = c.width, h = c.height, image = ctx.getImageData(0, 0, w, h), d = image.data;
  if (x < 0 || y < 0 || x >= w || y >= h) return;
  const at = (x, y) => (y * w + x) * 4, start = at(x, y), from = [d[start], d[start + 1], d[start + 2], d[start + 3]];
  const to = [parseInt(S.colour.slice(1, 3), 16), parseInt(S.colour.slice(3, 5), 16), parseInt(S.colour.slice(5, 7), 16), 255];
  if (from.every((v, i) => v === to[i])) return;
  const stack = [[x, y]];
  while (stack.length) {
    const [px, py] = stack.pop(); if (px < 0 || py < 0 || px >= w || py >= h || !inSel(px, py)) continue;
    const i = at(px, py); if (d[i] !== from[0] || d[i + 1] !== from[1] || d[i + 2] !== from[2] || d[i + 3] !== from[3]) continue;
    d[i] = to[0]; d[i + 1] = to[1]; d[i + 2] = to[2]; d[i + 3] = 255;
    stack.push([px + 1, py], [px - 1, py], [px, py + 1], [px, py - 1]);
  }
  ctx.putImageData(image, 0, 0);
}
function pick(x, y) {
  if (x < 0 || y < 0 || x >= S.doc.width || y >= S.doc.height) return;
  const flat = blank(); paintFrame(flat.getContext("2d"), S.frame, 1);
  const d = flat.getContext("2d").getImageData(x, y, 1, 1).data;
  if (d[3]) setColour("#" + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, "0")).join(""));
}
function cut(source, area, remove) {         // the selected cells as a small canvas of their own; optionally taken out of the source
  const canvas = document.createElement("canvas"); canvas.width = area.w; canvas.height = area.h;
  const ctx = canvas.getContext("2d"); ctx.drawImage(source, -area.x, -area.y);
  const from = source.getContext("2d");
  if (!area.mask) { if (remove) from.clearRect(area.x, area.y, area.w, area.h); return canvas; }
  for (let y = 0; y < area.h; y++) for (let x = 0; x < area.w; x++) {
    if (!area.mask[y * area.w + x]) ctx.clearRect(x, y, 1, 1); else if (remove) from.clearRect(area.x + x, area.y + y, 1, 1);
  }
  return canvas;
}
function lift() {
  if (S.float) return;
  const source = cel(true), area = S.sel || { x: 0, y: 0, w: S.doc.width, h: S.doc.height };
  S.float = { canvas: cut(source, area, true), x: area.x, y: area.y, whole: !S.sel, mask: area.mask };
}
function drop() {
  if (!S.float) return;
  cel(true).getContext("2d").drawImage(S.float.canvas, S.float.x, S.float.y);
  if (!S.float.whole) S.sel = { x: S.float.x, y: S.float.y, w: S.float.canvas.width, h: S.float.canvas.height, mask: S.float.mask };
  S.float = null; draw(); commit();
}
function usable() {
  const layer = S.doc.layers[S.layer];
  if (layer.locked) { toast("This layer is locked"); return false; }
  if (!layer.visible) { toast("This layer is hidden"); return false; }
  return true;
}
function cellAt(event) {
  const r = stage.getBoundingClientRect();
  return [Math.floor((event.clientX - r.left - S.panX) / S.zoom), Math.floor((event.clientY - r.top - S.panY) / S.zoom)];
}
function pointAt(event) {                   // the same place, not rounded to a cell: for outlines
  const r = stage.getBoundingClientRect();
  return [(event.clientX - r.left - S.panX) / S.zoom, (event.clientY - r.top - S.panY) / S.zoom];
}
function closePolygon() { const shape = S.poly && shapeFrom(S.poly); S.poly = null; if (shape) S.sel = shape; draw(); context(); reportView(); }
let drag = null;
stage.addEventListener("pointerdown", event => {
  if (!S.doc || event.button === 2) return;
  stage.setPointerCapture(event.pointerId); stopPlaying();
  const [x, y] = cellAt(event), tool = S.space || event.button === 1 ? "hand" : event.altKey ? "picker" : S.tool;
  drag = { tool, x, y, lastX: x, lastY: y, startX: event.clientX, startY: event.clientY, panX: S.panX, panY: S.panY, shift: event.shiftKey };
  if (tool === "hand") { S.panned = S.moved = true; return; }
  if (S.paint && ["pencil", "eraser", "fill"].includes(tool)) {
    drag.region = true;
    if (tool === "fill") { wand(x, y); drag = null; } else paintRegion(dots(x, y), tool === "pencil");
    loopRows(); overlay(); return;
  }
  if (tool === "picker") { pick(x, y); return; }
  if (tool === "select") { drop(); S.sel = null; draw(); return; }
  if (tool === "lasso") { drop(); S.sel = null; drag.path = [pointAt(event)]; draw(); return; }
  if (tool === "polygon") {
    drop(); drag = null; const at = pointAt(event);
    if (S.poly && S.poly.length >= 3 && Math.hypot((at[0] - S.poly[0][0]) * S.zoom, (at[1] - S.poly[0][1]) * S.zoom) < 8) return closePolygon();
    if (!S.poly) { S.poly = []; S.sel = null; } S.poly.push(at); draw(); return;
  }
  if (!usable()) { drag = null; return; }
  if (tool === "move") { lift(); drag.fx = S.float.x; drag.fy = S.float.y; draw(); return; }
  drop(); S.drawing = true;
  if (tool === "pencil" || tool === "eraser") { put(dots(x, y), tool === "eraser"); draw(); }
  if (tool === "fill") { flood(x, y); draw(); }
  if (tool === "line" || tool === "rect") { S.preview = [[x, y]]; overlay(); }
});
stage.addEventListener("pointermove", event => {
  if (!S.doc) return;
  const [x, y] = cellAt(event);
  S.hover = x >= 0 && y >= 0 && x < S.doc.width && y < S.doc.height ? [x, y] : null;
  $("where").textContent = S.hover ? `${x}, ${y}` + (S.sel ? `   selection ${S.sel.w} × ${S.sel.h}` : "") : "";
  S.hoverAt = pointAt(event);
  if (!drag) { overlay(); return; }
  if (drag.path) { drag.path.push(pointAt(event)); overlay(); return; }
  if (drag.region) { paintRegion(between(drag.lastX, drag.lastY, x, y).flatMap(([px, py]) => dots(px, py)), drag.tool === "pencil"); drag.lastX = x; drag.lastY = y; overlay(); return; }
  if (drag.tool === "hand") { S.panX = drag.panX + event.clientX - drag.startX; S.panY = drag.panY + event.clientY - drag.startY; draw(); return; }
  if (drag.tool === "picker") { pick(x, y); return; }
  if (drag.tool === "pencil" || drag.tool === "eraser") {
    put(between(drag.lastX, drag.lastY, x, y).flatMap(([px, py]) => dots(px, py)), drag.tool === "eraser"); drag.lastX = x; drag.lastY = y; draw();
  } else if (drag.tool === "line") { S.preview = between(drag.x, drag.y, x, y); overlay(); }
  else if (drag.tool === "rect") { S.preview = boxCells(drag.x, drag.y, x, y, event.shiftKey); overlay(); }
  else if (drag.tool === "select") {
    const l = Math.max(0, Math.min(drag.x, x)), t = Math.max(0, Math.min(drag.y, y)), r = Math.min(S.doc.width - 1, Math.max(drag.x, x)), b = Math.min(S.doc.height - 1, Math.max(drag.y, y));
    S.sel = r >= l && b >= t ? { x: l, y: t, w: r - l + 1, h: b - t + 1 } : null; overlay(); context();
  } else if (drag.tool === "move" && S.float) { S.float.x = drag.fx + x - drag.x; S.float.y = drag.fy + y - drag.y; draw(); }
});
stage.addEventListener("pointerup", event => {
  if (!drag) return;
  const tool = drag.tool, path = drag.path, region = drag.region; drag = null;
  if (region) { loopRows(); return; }
  if (path) { S.sel = shapeFrom(path); draw(); context(); reportView(); return; }
  if (S.preview) { put(S.preview, false); S.preview = null; }
  if (S.drawing) { S.drawing = false; draw(); commit(); }
  if (tool === "select") { context(); reportView(); }
  if (tool === "move" && S.float && S.float.whole) drop();
});
stage.addEventListener("pointerleave", () => { S.hover = null; overlay(); });
stage.addEventListener("wheel", event => {
  event.preventDefault();
  const r = stage.getBoundingClientRect(), mx = event.clientX - r.left, my = event.clientY - r.top, before = S.zoom; S.moved = true;
  S.zoom = Math.max(1, Math.min(48, S.zoom + (event.deltaY < 0 ? 1 : -1) * Math.max(1, Math.round(S.zoom / 8))));
  S.panX = Math.round(mx - (mx - S.panX) * S.zoom / before); S.panY = Math.round(my - (my - S.panY) * S.zoom / before); $("zoom").textContent = S.zoom * 100 + "%"; draw();
}, { passive: false });
stage.addEventListener("contextmenu", event => event.preventDefault());

// ---------------------------------------------------------------- the panels
function setColour(value) { S.colour = value.toLowerCase(); $("colour").value = S.colour; $("hex").textContent = S.colour; palette(); context(); }
function setTool(name) { if (name !== "move") drop(); S.poly = null; S.tool = name; S.preview = null; tools(); overlay(); stage.style.cursor = name === "hand" ? "grab" : name === "move" ? "move" : "crosshair"; }
function tools() {
  const nav = $("tools"); nav.replaceChildren();
  for (const [name, glyph, key, title] of TOOLS) {
    const b = document.createElement("button"); b.innerHTML = `${glyph}<small>${key}</small>`; b.title = `${title} (${key})`; b.className = S.tool === name ? "on" : ""; b.dataset.tool = name;
    b.onclick = () => setTool(name); nav.append(b);
  }
  nav.append(document.createElement("hr"));
  const size = document.createElement("button"); size.textContent = S.size; size.title = "Brush size ([ and ])"; size.id = "brush";
  size.onclick = () => { S.size = SIZES[(SIZES.indexOf(S.size) + 1) % SIZES.length]; tools(); }; nav.append(size);
  const mirror = document.createElement("button"); mirror.textContent = "⇋"; mirror.title = "Mirror what you draw left and right"; mirror.className = S.mirror ? "on" : ""; mirror.id = "mirror";
  mirror.onclick = () => { S.mirror = !S.mirror; tools(); overlay(); }; nav.append(mirror);
  const fitButton = document.createElement("button"); fitButton.textContent = "⤢"; fitButton.title = "Fit the picture to the window (0)"; fitButton.onclick = () => { S.moved = false; fit(); chrome(); draw(); }; nav.append(fitButton);
}
function palette() {
  const box = $("palette"); box.replaceChildren();
  for (const colour of S.doc ? S.doc.palette : []) {
    const b = document.createElement("button"); b.style.background = colour; b.title = colour + " (Shift+click removes it)"; b.className = colour === S.colour ? "on" : "";
    b.onclick = event => event.shiftKey ? op({ op: "palette", colors: S.doc.palette.filter(c => c !== colour) }) : setColour(colour);
    box.append(b);
  }
  $("lock").textContent = S.doc && S.doc.locked ? "locked" : "lock"; $("lock").classList.toggle("active", !!(S.doc && S.doc.locked));
  $("lock").title = S.doc && S.doc.locked ? "The palette is locked: whatever is drawn takes its nearest colour. Click to unlock." : "Lock the palette: whatever is drawn takes its nearest colour";
  if (S.doc && !S.doc.palette.length) box.textContent = "No palette yet. Add colours, or take them from the art.", box.className = "palette quiet"; else box.className = "palette";
}
function layers() {
  const list = $("layerList"); list.replaceChildren();
  S.doc.layers.map((layer, i) => [layer, i]).reverse().forEach(([layer, i]) => {
    const row = document.createElement("div"); row.className = "layer" + (i === S.layer ? " on" : "");
    const eye = document.createElement("button"); eye.textContent = layer.visible ? "●" : "○"; eye.title = layer.visible ? "Hide" : "Show"; eye.className = layer.visible ? "active" : "";
    eye.onclick = event => { event.stopPropagation(); op({ op: "set-layer", layer: i, visible: !layer.visible }); };
    const lock = document.createElement("button"); lock.textContent = layer.locked ? "🔒" : "·"; lock.title = layer.locked ? "Unlock" : "Lock"; lock.className = layer.locked ? "active" : "";
    lock.onclick = event => { event.stopPropagation(); op({ op: "set-layer", layer: i, locked: !layer.locked }); };
    const title = document.createElement("span"); title.className = "title"; title.textContent = layer.name; title.title = "Double-click to rename";
    title.ondblclick = () => {
      const input = document.createElement("input"); input.value = layer.name; title.replaceWith(input); input.focus(); input.select();
      const done = () => input.value.trim() && input.value !== layer.name ? op({ op: "set-layer", layer: i, name: input.value.trim() }) : layers();
      input.onblur = done; input.onkeydown = event => { if (event.key === "Enter") input.blur(); if (event.key === "Escape") { input.value = layer.name; input.blur(); } };
    };
    const opacity = document.createElement("input"); opacity.type = "range"; opacity.min = 0; opacity.max = 100; opacity.value = Math.round(layer.opacity * 100); opacity.title = "Opacity";
    opacity.onclick = event => event.stopPropagation(); opacity.onchange = () => op({ op: "set-layer", layer: i, opacity: opacity.value / 100 });
    row.append(eye, lock, title, opacity); row.onclick = () => go(S.frame, i); list.append(row);
  });
}
function chrome() {
  if (!S.doc) return;
  $("name").textContent = S.meta.name; $("size").textContent = `${S.doc.width} × ${S.doc.height}`; $("zoom").textContent = S.zoom * 100 + "%";
  $("undo").disabled = !S.meta.undo; $("redo").disabled = !S.meta.redo;
  $("last").innerHTML = ""; if (S.meta.last) { const who = document.createElement("b"); who.textContent = S.meta.last[1] === "agent" ? "agent" : "you"; $("last").append("last: ", who, " " + S.meta.last[0]); }
  $("frameNo").textContent = `${S.frame + 1} of ${S.doc.frames.length}`; $("duration").value = S.doc.frames[S.frame].duration;
  const agent = $("agent"); agent.className = "status" + (S.meta.agent ? (S.meta.listening ? " listening" : " busy") : "");
  agent.textContent = !S.meta.agent ? "not connected" : S.meta.listening ? "listening" : "working or away";
  layers(); palette(); context();
}
function context() {
  if (!S.doc) return;
  const chips = [`frame ${S.frame + 1}`, S.doc.layers[S.layer].name, S.colour];
  if (S.sel) chips.push(`selection ${S.sel.w} × ${S.sel.h}`);
  $("context").replaceChildren("the agent sees: ", ...chips.map(text => { const s = document.createElement("span"); s.textContent = text; return s; }));
}
function viewNow() {
  return { frame: S.frame, layer: S.layer, layerName: S.doc.layers[S.layer].name, color: S.colour, tool: S.tool, ...(S.sel ? { selection: [S.sel.x, S.sel.y, S.sel.w, S.sel.h] } : {}) };
}
function reportView() { clearTimeout(reportView.timer); reportView.timer = setTimeout(() => api("/api/view", viewNow()).catch(() => {}), 300); }

// ---------------------------------------------------------------- the timeline
function thumb(canvas, source) {
  const ctx = canvas.getContext("2d"); ctx.imageSmoothingEnabled = false; ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!source) return;
  const scale = Math.min(canvas.width / source.width, canvas.height / source.height), w = source.width * scale, h = source.height * scale;
  ctx.drawImage(source, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
}
function timeline() {
  const sheet = document.createElement("div"); sheet.className = "sheet"; sheet.style.gridTemplateColumns = `auto repeat(${S.doc.frames.length}, 34px)`;
  sheet.append(document.createElement("span"));
  S.doc.frames.forEach((frame, f) => {
    const head = document.createElement("div"); head.className = "head" + (f === S.frame ? " on" : ""); head.dataset.frame = f;
    head.innerHTML = `${f + 1}<small>${frame.duration}</small>`; head.title = `Frame ${f + 1}, ${frame.duration} ms`; head.onclick = () => go(f, S.layer); sheet.append(head);
  });
  S.doc.layers.map((layer, i) => [layer, i]).reverse().forEach(([layer, i]) => {
    const row = document.createElement("div"); row.className = "row" + (i === S.layer ? " on" : ""); row.textContent = layer.name; row.onclick = () => go(S.frame, i); sheet.append(row);
    S.doc.frames.forEach((_, f) => {
      const button = document.createElement("button"), source = S.cels[i][f];
      button.className = "cel" + (source ? "" : " empty") + (f === S.frame ? " column" : "") + (f === S.frame && i === S.layer ? " on" : "");
      button.dataset.layer = i; button.dataset.frame = f; button.title = `${layer.name}, frame ${f + 1}`;
      if (source) { const c = document.createElement("canvas"); c.width = c.height = 30; thumb(c, source); button.append(c); }
      button.onclick = () => go(f, i); sheet.append(button);
    });
  });
  $("timeline").replaceChildren(sheet);
}
function thumbs(layer, frame) {
  const button = document.querySelector(`.cel[data-layer="${layer}"][data-frame="${frame}"]`); if (!button) return;
  let c = button.querySelector("canvas"); if (!c) { c = document.createElement("canvas"); c.width = c.height = 30; button.append(c); button.classList.remove("empty"); }
  thumb(c, S.cels[layer][frame]);
}
function go(frame, layer, quiet) {
  if (frame !== S.frame || layer !== S.layer) drop();
  S.frame = Math.max(0, Math.min(S.doc.frames.length - 1, frame)); S.layer = Math.max(0, Math.min(S.doc.layers.length - 1, layer));
  document.querySelectorAll(".cel, .head, .sheet .row").forEach(n => n.classList.remove("on", "column"));
  document.querySelectorAll(`.cel[data-frame="${S.frame}"]`).forEach(n => n.classList.add("column"));
  document.querySelector(`.cel[data-layer="${S.layer}"][data-frame="${S.frame}"]`)?.classList.add("on");
  document.querySelector(`.head[data-frame="${S.frame}"]`)?.classList.add("on");
  if (!quiet) { chrome(); timelineRows(); reportView(); }
  draw();
}
function timelineRows() { const rows = [...document.querySelectorAll(".sheet .row")]; rows.forEach((row, n) => row.classList.toggle("on", S.doc.layers.length - 1 - n === S.layer)); }
function stopPlaying() { if (!S.playing) return; S.playing = false; clearTimeout(S.timer); $("play").innerHTML = "&#9654;"; chrome(); draw(); }
function play() {
  if (S.playing) return stopPlaying();
  drop(); S.playing = true; $("play").innerHTML = "&#10074;&#10074;";
  const step = () => { if (!S.playing) return; S.timer = setTimeout(() => { go((S.frame + 1) % S.doc.frames.length, S.layer, true); step(); }, S.doc.frames[S.frame].duration); };
  step();
}

// ---------------------------------------------------------------- proposals: look before a change is made
// A proposal shows which cells a change would touch, frame by frame, before anything is sent.
function pixels(canvas) { return canvas ? canvas.getContext("2d").getImageData(0, 0, S.doc.width, S.doc.height).data : null; }
function selectedCells() {                  // the cells of the selection, as [x, y] pairs
  const out = []; if (!S.sel) return out;
  for (let y = S.sel.y; y < S.sel.y + S.sel.h; y++) for (let x = S.sel.x; x < S.sel.x + S.sel.w; x++) if (!S.sel.mask || S.sel.mask[(y - S.sel.y) * S.sel.w + x - S.sel.x]) out.push([x, y]);
  return out;
}
function propose(text, marks, apply) {       // marks: per frame, the cells that would change
  S.proposal = { marks, apply };
  const total = marks.reduce((n, cells) => n + cells.length, 0), frames = marks.filter(cells => cells.length).length;
  $("proposalText").innerHTML = ""; $("proposalText").append(text + " ");
  const b = document.createElement("b"); b.textContent = total ? `${total} cell${total > 1 ? "s" : ""} change in ${frames} frame${frames > 1 ? "s" : ""}.` : "Nothing would change."; $("proposalText").append(b, total ? " Step through the frames to see them." : "");
  $("proposalApply").disabled = !total; $("proposal").hidden = false; overlay();
}
function endProposal() { S.proposal = null; $("proposal").hidden = true; $("proposalFlip").hidden = true; draw(); }
$("proposalFlip").onclick = () => { S.proposal.before = !S.proposal.before; $("proposalFlip").textContent = S.proposal.before ? "show after" : "show before"; draw(); };
async function proposeOp(text, operation) {   // ask the server what an operation would do, and show that first
  drop();
  let seen; try { seen = await api("/api/preview", { op: operation }); } catch (error) { return fail(error); }
  const after = {}; for (const [frame, png] of Object.entries(seen.after)) after[frame] = await decode(png);
  propose(text, seen.marks, () => op(operation));
  S.proposal.layer = seen.layer; S.proposal.after = after; S.proposal.before = false;
  $("proposalFlip").hidden = !Object.keys(after).length; $("proposalFlip").textContent = "show before"; draw();
}
$("proposalApply").onclick = async () => { const run = S.proposal.apply; endProposal(); await run(); };
$("proposalCancel").onclick = endProposal;
function proposeHold() {
  drop();
  if (!S.sel) return toast("Select the area that should stand still first (M)");
  if (S.doc.frames.length < 2) return toast("There is only one frame");
  const cells = selectedCells(), w = S.doc.width, from = S.frame, layer = S.layer, source = pixels(S.cels[layer][from]);
  const marks = S.doc.frames.map((_, f) => {
    if (f === from) return [];
    const other = pixels(S.cels[layer][f]);
    return cells.filter(([x, y]) => { const i = (y * w + x) * 4; for (let k = 0; k < 4; k++) if ((source ? source[i + k] : 0) !== (other ? other[i + k] : 0)) return true; return false; });
  });
  const where = S.sel.mask ? { cells } : { rect: [S.sel.x, S.sel.y, S.sel.w, S.sel.h] };
  propose(`Hold the selection as it is in frame ${from + 1}, in all ${S.doc.frames.length} frames.`, marks, () => op({ op: "hold", layer, from, ...where }));
}

// ---------------------------------------------------------------- the check: where to look
const KINDS = { flicker: ["#ff00dc", "flicker"], fragments: ["#ffdc00", "fragments"], holes: ["#00e6ff", "holes"], palette: ["#ff2828", "off the palette"] };
function span(frames) { return frames.length > 1 && frames[frames.length - 1] - frames[0] === frames.length - 1 ? `frames ${frames[0] + 1} to ${frames[frames.length - 1] + 1}` : "frame" + (frames.length > 1 ? "s " : " ") + frames.map(f => f + 1).join(", "); }
async function runCheck() {
  drop();
  try { S.findings = await api("/api/check?fragment=3" + (S.doc.palette.length ? "&palette=1" : "")); } catch (error) { return fail(error); }
  const box = $("findings"), total = Object.values(S.findings.total).reduce((a, b) => a + b, 0);
  box.replaceChildren();
  const head = document.createElement("h3"); head.textContent = total ? "Worth a look" : "Nothing suspicious"; const close = document.createElement("button"); close.textContent = "×"; close.className = "quiet"; close.title = "Close";
  close.onclick = () => panel("findings", false); head.append(close); box.append(head);
  const kinds = document.createElement("div"); kinds.className = "kinds";
  for (const [kind, [colour, label]] of Object.entries(KINDS)) { const chip = document.createElement("span"), dot = document.createElement("i"); dot.style.background = colour; chip.append(dot, `${S.findings.total[kind]} ${label}`); kinds.append(chip); }
  box.append(kinds);
  const hint = document.createElement("p"); hint.textContent = total ? "Hints, not verdicts: a star may be a fragment, a lamp may be meant to blink. Select an area, then hold it or fix it by hand." : "No flicker, no stray fragments, no holes."; box.append(hint);
  for (const area of S.findings.areas) {
    const row = document.createElement("div"), text = document.createElement("div"), pick = document.createElement("button"); row.className = "area";
    text.textContent = `${KINDS[area.kind][1]}: ${area.cells} cell${area.cells > 1 ? "s" : ""}`; const where = document.createElement("small"); where.textContent = `${span(area.frames)}, at ${area.rect[0]}, ${area.rect[1]} (${area.rect[2]} × ${area.rect[3]})`; text.append(where);
    pick.textContent = "select"; pick.onclick = () => { const [x, y, w, h] = area.rect; setTool("select"); S.sel = { x, y, w, h }; go(area.frames.includes(S.frame) ? S.frame : area.frames[0], S.layer); context(); reportView(); };
    row.append(text, pick); box.append(row);
  }
  panel("findings", true);
}

// ---------------------------------------------------------------- clean up, with a look at the result first
function tidyForm() {
  const step = $("tidyStep").value; $("tidyThin").hidden = step !== "thin"; $("tidyStrength").hidden = step === "thin";
  const how = $("tidyHow"); how.replaceChildren();
  const choices = step === "halo" ? [["gentle", "gentle"], ["stronger", "stronger"]] : step === "fringe" ? [["2", "bits up to 2 cells"], ["4", "bits up to 4 cells"], ["8", "bits up to 8 cells"]] : [["gentle", "gentle"], ["balanced", "balanced"], ["strong", "strong"]];
  for (const [value, label] of choices) { const o = document.createElement("option"); o.value = value; o.textContent = label; how.append(o); }
  if (step !== "halo") how.selectedIndex = 1;
  $("tidyArea").hidden = !S.sel; $("tidyWhere").hidden = !!S.sel;
  $("tidyWhere").textContent = "On the whole layer. Select an area first to keep the step to it, or to protect it.";
}
$("tidy").onclick = () => { panel("cleanup", $("cleanup").hidden); tidyForm(); };
$("cleanupClose").onclick = () => panel("cleanup", false); $("tidyStep").onchange = tidyForm;
$("tidyGo").onclick = () => {
  const kind = $("tidyStep").value, step = { op: kind };
  if (kind === "thin") { step.width = +$("tidyWidth").value; step.gap = +$("tidyGap").value; if ($("tidyInk").value === "colour") step.color = S.colour; }
  else if (kind === "fringe") step.max = +$("tidyHow").value; else step.strength = $("tidyHow").value;
  if (S.sel) Object.assign(step, S.sel.mask ? { cells: selectedCells() } : { rect: [S.sel.x, S.sel.y, S.sel.w, S.sel.h] }, $("tidyProtect").value === "outside" ? { protect: true } : {});
  const all = $("tidyFrames").value === "all";
  proposeOp(`${$("tidyStep").selectedOptions[0].textContent}, on ${all ? "all frames" : "frame " + (S.frame + 1)}.`, { op: "edits", layer: S.layer, frames: all ? "all" : [S.frame], steps: [step] });
};

// ---------------------------------------------------------------- loops: paint what moves
const MOVES = { clouds: ["#ffffff", "clouds drift"], water: ["#5ec4c8", "water ripples"], sway: ["#6fd08c", "leaves and grass sway"], twinkle: ["#ffe680", "lights twinkle"],
  rise: ["#b8bcc8", "smoke rises"], fireflies: ["#f2a65a", "fireflies"], leaves: ["#e0703a", "falling leaves"], birds: ["#c084fc", "birds"] };
S.regions = [];
function regionCells(region) { const out = [], w = S.doc.width; region.mask.forEach((on, i) => { if (on) out.push([i % w, Math.floor(i / w)]); }); return out; }
function loopRows() {
  const box = $("loopRegions"); box.replaceChildren();
  S.regions.forEach((region, n) => {
    const row = document.createElement("div"), dot = document.createElement("i"), kind = document.createElement("select"), amount = document.createElement("select"), paint = document.createElement("button"), gone = document.createElement("button"), note = document.createElement("small");
    row.className = "region"; dot.style.background = MOVES[region.kind][0];
    for (const [value, [, label]] of Object.entries(MOVES)) { const o = document.createElement("option"); o.value = value; o.textContent = label; kind.append(o); }
    kind.value = region.kind; kind.onchange = () => { region.kind = kind.value; loopRows(); overlay(); };
    for (const [value, label] of [[0.5, "a little"], [1, "normal"], [2, "more"], [4, "a lot"]]) { const o = document.createElement("option"); o.value = value; o.textContent = label; amount.append(o); }
    amount.value = region.amount; amount.onchange = () => region.amount = +amount.value;
    paint.textContent = "paint"; paint.className = S.paint === region ? "on" : ""; paint.title = "Paint this area on the picture";
    paint.onclick = () => { S.paint = S.paint === region ? null : region; if (S.paint && !["pencil", "eraser", "fill"].includes(S.tool)) setTool("pencil"); loopRows(); overlay(); };
    gone.textContent = "×"; gone.title = "Remove"; gone.onclick = () => { S.regions.splice(n, 1); if (S.paint === region) S.paint = null; loopRows(); overlay(); };
    const cells = region.mask.reduce((a, b) => a + b, 0); note.textContent = cells ? `${cells} cells painted` : "area guessed (paint to set it)";
    row.append(dot, kind, amount, paint, gone, note); box.append(row);
  });
}
function paintRegion(cells, on) { const w = S.doc.width, h = S.doc.height; for (const [x, y] of cells) if (x >= 0 && y >= 0 && x < w && y < h) S.paint.mask[y * w + x] = on ? 1 : 0; }
function wand(x, y) {                        // the touching cells whose colour is close to the one clicked
  const w = S.doc.width, h = S.doc.height; if (x < 0 || y < 0 || x >= w || y >= h) return;
  const flat = blank(); paintFrame(flat.getContext("2d"), S.frame, 1);
  const d = flat.getContext("2d").getImageData(0, 0, w, h).data, slack = +$("loopWand").value, start = (y * w + x) * 4, seen = new Uint8Array(w * h), stack = [[x, y]];
  while (stack.length) {
    const [px, py] = stack.pop(); if (px < 0 || py < 0 || px >= w || py >= h || seen[py * w + px]) continue;
    const i = (py * w + px) * 4; seen[py * w + px] = 1;
    if (Math.max(Math.abs(d[i] - d[start]), Math.abs(d[i + 1] - d[start + 1]), Math.abs(d[i + 2] - d[start + 2])) > slack || (d[i + 3] < 128) !== (d[start + 3] < 128)) continue;
    S.paint.mask[py * w + px] = 1; stack.push([px + 1, py], [px - 1, py], [px, py + 1], [px, py - 1]);
  }
}
$("loop").onclick = () => { panel("loopPanel", $("loopPanel").hidden); loopRows(); };
$("loopClose").onclick = () => panel("loopPanel", false);
$("loopAdd").onclick = () => { const used = S.regions.map(r => r.kind), kind = Object.keys(MOVES).find(k => !used.includes(k)) || "clouds"; const region = { kind, amount: 1, mask: new Uint8Array(S.doc.width * S.doc.height) }; S.regions.push(region); S.paint = region; if (!["pencil", "eraser", "fill"].includes(S.tool)) setTool("pencil"); loopRows(); overlay(); };
$("loopGo").onclick = async () => {
  if (!S.regions.length) return toast("Add something that moves first");
  drop(); S.paint = null; toast("Making the loop …"); S.pending++;
  const regions = S.regions.map(r => ({ kind: r.kind, amount: r.amount, ...(r.mask.some(Boolean) ? { area: { cells: regionCells(r) } } : {}) }));
  try {
    const done = await send("/api/loop", { frame: S.frame, loop: { seconds: +$("loopSeconds").value, fps: +$("loopFps").value, regions } });
    S.known = Math.max(S.known, done.rev); S.regions = []; panel("loopPanel", false); S.frame = 0; S.layer = 0; await load(true); play();
  } catch (error) { fail(error); } finally { S.pending--; }
};

// ---------------------------------------------------------------- chat
function showMessage(message) {
  const box = $("messages"), line = document.createElement("div"); line.className = "msg " + message.from;
  for (const [i, part] of String(message.text).split("`").entries()) { if (i % 2) { const c = document.createElement("code"); c.textContent = part; line.append(c); } else line.append(part); }
  box.append(line); box.scrollTop = box.scrollHeight; return line;
}
let burst = null;                            // changes an agent makes in quick succession share one line and one Undo
function agentChanged(text) {
  if (burst && Date.now() - burst.at < 5000 && burst.line.isConnected && !burst.button.disabled) {
    burst.count++; burst.at = Date.now(); burst.text.textContent = `The agent made ${burst.count} changes, the last: ${text}.`; burst.button.textContent = "Undo all";
    return;
  }
  const line = showMessage({ from: "note", text: "" }), words = document.createElement("span"), button = document.createElement("button");
  words.textContent = `The agent ${text}.`; button.textContent = "Undo";
  const mine = burst = { line, text: words, button, count: 1, at: Date.now() };
  button.onclick = async () => { button.disabled = true; for (let i = 0; i < mine.count; i++) await step("/api/undo"); };
  line.append(words, button);
}
async function step(path) { drop(); S.pending++; try { const done = await send(path, {}); S.known = Math.max(S.known, done.rev); await load(); } catch (error) { fail(error); } finally { S.pending--; } }
$("composer").addEventListener("submit", async event => {
  event.preventDefault(); const text = $("say").value.trim(); if (!text) return;
  $("say").value = ""; try { await api("/api/chat", { text, context: viewNow() }); } catch (error) { fail(error); $("say").value = text; }
});
$("say").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("composer").requestSubmit(); } });
function listen() {
  const events = new EventSource("/api/events");
  events.onmessage = event => {
    const e = JSON.parse(event.data);
    if (e.type === "chat") showMessage(e.message);
    if (e.type === "agent") { S.meta.listening = e.listening; chrome(); }
    if (e.type === "changed") {
      if (e.by === "you" && (S.pending > 0 || e.rev <= S.known)) return;
      if (e.by === "agent") agentChanged(e.note);
      whenIdle(() => load(true).catch(fail));
    }
  };
}

// ---------------------------------------------------------------- buttons and keys
$("undo").onclick = () => step("/api/undo"); $("redo").onclick = () => step("/api/redo");
$("colour").oninput = event => setColour(event.target.value);
$("addColour").onclick = () => S.doc.palette.includes(S.colour) ? toast("That colour is in the palette already") : op({ op: "palette", colors: [...S.doc.palette, S.colour] });
$("fromArt").onclick = () => op({ op: "palette-from-art" });
$("lock").onclick = () => op({ op: "palette", lock: !S.doc.locked });
api("/api/palettes").then(all => {            // the ready-made palettes, smallest first within their kind
  for (const [name, colours] of Object.entries(all)) { const o = document.createElement("option"); o.value = name; o.textContent = `${name} (${colours.length})`; $("named").append(o); }
}).catch(() => {});
$("named").onchange = () => { const name = $("named").value; $("named").value = ""; if (name) op({ op: "palette", name }); };
$("addLayer").onclick = async () => { const done = await op({ op: "add-layer", at: S.layer + 1 }); if (done) go(S.frame, S.layer + 1); };
$("delLayer").onclick = () => op({ op: "remove-layer", layer: S.layer });
$("mergeLayer").onclick = async () => { const done = await op({ op: "merge-down", layer: S.layer }); if (done) go(S.frame, S.layer - 1); };
$("upLayer").onclick = async () => { if (S.layer < S.doc.layers.length - 1 && await op({ op: "move-layer", layer: S.layer, to: S.layer + 1 })) go(S.frame, S.layer + 1); };
$("downLayer").onclick = async () => { if (S.layer > 0 && await op({ op: "move-layer", layer: S.layer, to: S.layer - 1 })) go(S.frame, S.layer - 1); };
$("addFrame").onclick = async () => { if (await op({ op: "add-frame", at: S.frame + 1 })) go(S.frame + 1, S.layer); };
$("copyFrame").onclick = async () => { if (await op({ op: "add-frame", at: S.frame + 1, copy: S.frame })) go(S.frame + 1, S.layer); };
$("delFrame").onclick = () => op({ op: "remove-frame", frame: S.frame });
$("leftFrame").onclick = async () => { if (S.frame > 0 && await op({ op: "move-frame", frame: S.frame, to: S.frame - 1 })) go(S.frame - 1, S.layer); };
$("rightFrame").onclick = async () => { if (S.frame < S.doc.frames.length - 1 && await op({ op: "move-frame", frame: S.frame, to: S.frame + 1 })) go(S.frame + 1, S.layer); };
$("duration").onchange = event => op({ op: "set-frame", frame: S.frame, duration: +event.target.value });
$("durationAll").onclick = () => op({ op: "set-frame", frame: "all", duration: +$("duration").value });
$("first").onclick = () => go(0, S.layer); $("lastFrame").onclick = () => go(S.doc.frames.length - 1, S.layer);
$("prev").onclick = () => go((S.frame - 1 + S.doc.frames.length) % S.doc.frames.length, S.layer); $("next").onclick = () => go((S.frame + 1) % S.doc.frames.length, S.layer);
$("play").onclick = play; $("onion").onchange = draw; $("grid").onchange = overlay; $("hold").onclick = proposeHold; $("check").onclick = runCheck;
$("size").onclick = () => {
  const answer = prompt("Canvas size in cells, width x height", `${S.doc.width}x${S.doc.height}`); if (!answer) return;
  const [w, h] = answer.toLowerCase().split("x").map(n => parseInt(n, 10)); if (w && h) op({ op: "resize", width: w, height: h }).then(() => { fit(); chrome(); draw(); });
};
$("export").onclick = event => { event.stopPropagation(); $("exportMenu").hidden = !$("exportMenu").hidden; };
document.addEventListener("click", () => $("exportMenu").hidden = true);
$("exportMenu").onclick = event => {
  const format = event.target.dataset.format; if (!format) { event.stopPropagation(); return; }
  drop(); whenIdle(() => { const a = document.createElement("a"); a.href = `/api/export?format=${format}&scale=${$("exportScale").value}&frame=${S.frame}`; a.download = ""; document.body.append(a); a.click(); a.remove(); });
};
let incoming = null;
function offer(file) {
  incoming = file; $("importName").textContent = file.name;
  const empty = !S.cels.some(row => row.some(Boolean));
  $("importHow").value = empty ? "project" : "layer"; $("importMode").value = empty ? "recommended" : "fit"; $("importDialog").showModal();
}
$("import").onclick = () => $("file").click();
$("file").onchange = event => { if (event.target.files[0]) offer(event.target.files[0]); event.target.value = ""; };
$("importDialog").addEventListener("close", async () => {
  if ($("importDialog").returnValue !== "go" || !incoming) return;
  const mode = $("importMode").value, settings = {};
  if (mode === "recommended") settings.convert = { mode: "recommended" };
  else if (mode === "native") settings.convert = { mode: "grid", grid: "native", sampling: "exact", colors: 256 };
  else settings.convert = { mode: "grid", grid: Math.max(S.doc.width, S.doc.height), sampling: $("importSampling").value, colors: +$("importColours").value };
  if ($("importCutout").checked) settings.edits = [{ op: "cutout" }];
  toast("Converting " + incoming.name + " …"); drop(); S.pending++;
  const how = $("importHow").value;
  try {
    const response = await fetch(`/api/import?name=${encodeURIComponent(incoming.name)}&how=${$("importHow").value}&settings=${encodeURIComponent(JSON.stringify(settings))}`, { method: "POST", body: incoming });
    const done = await response.json(); if (!response.ok) throw new Error(done.error);
    S.known = Math.max(S.known, done.rev); S.fitted = false; await load(true);
    if (how === "layer") { S.layer = S.doc.layers.length - 1; S.sel = null; setTool("move"); layers(); timeline(); context(); draw(); }   // in hand, ready to be moved into place
    toast(`${done.summary.width} × ${done.summary.height} cells, ${done.summary.frames} frame${done.summary.frames > 1 ? "s" : ""}, ${done.summary.colors} colours`);
  } catch (error) { fail(error); } finally { S.pending--; incoming = null; }
});
function pasteClip() {                      // the piece copied in the studio, floating where it came from
  if (!S.clip || !usable()) return; drop();
  const canvas = document.createElement("canvas"); canvas.width = S.clip.canvas.width; canvas.height = S.clip.canvas.height; canvas.getContext("2d").drawImage(S.clip.canvas, 0, 0);
  S.float = { canvas, x: S.clip.x, y: S.clip.y, mask: S.clip.mask }; setTool("move"); draw();
}
async function sameAsClip(file) {           // is the picture on the clipboard the piece that was copied here?
  if (!S.clip) return false;
  try {
    const picture = await createImageBitmap(file), mine = S.clip.canvas;
    if (picture.width !== mine.width || picture.height !== mine.height) return false;
    const other = document.createElement("canvas"); other.width = mine.width; other.height = mine.height; other.getContext("2d").drawImage(picture, 0, 0);
    const a = mine.getContext("2d").getImageData(0, 0, mine.width, mine.height).data, b = other.getContext("2d").getImageData(0, 0, mine.width, mine.height).data;
    for (let i = 0; i < a.length; i += 4) if (a[i + 3] !== b[i + 3] || (a[i + 3] && (a[i] !== b[i] || a[i + 1] !== b[i + 1] || a[i + 2] !== b[i + 2]))) return false;
    return true;
  } catch { return false; }
}
document.addEventListener("paste", async event => {
  if (event.target.matches?.("input, textarea, select") || $("importDialog").open) return;
  event.preventDefault();
  const file = [...(event.clipboardData?.files || [])].find(f => f.type.startsWith("image/"));
  if (!file || S.clipOnlyHere || await sameAsClip(file)) return pasteClip();
  offer(new File([file], "picture from the clipboard.png", { type: file.type }));   // a picture from elsewhere comes in like a dropped one
  try { const picture = await createImageBitmap(file); if (picture.width <= S.doc.width && picture.height <= S.doc.height) { $("importMode").value = "native"; $("importHow").value = "layer"; } } catch { /* the dialog's own choice stands */ }
});
stage.addEventListener("dragover", event => { event.preventDefault(); stage.classList.add("drop"); });
stage.addEventListener("dragleave", () => stage.classList.remove("drop"));
stage.addEventListener("drop", event => { event.preventDefault(); stage.classList.remove("drop"); if (event.dataTransfer.files[0]) offer(event.dataTransfer.files[0]); });
window.addEventListener("resize", () => { if (S.doc && !S.moved) fit(); chrome(); draw(); });   // keep the picture in view until the person moves it
document.addEventListener("keydown", event => {
  if (event.target.matches?.("input, textarea, select") || $("importDialog").open) return;
  const key = event.key.toLowerCase(), command = event.metaKey || event.ctrlKey;
  if (command && key === "z") { event.preventDefault(); return step(event.shiftKey ? "/api/redo" : "/api/undo"); }
  if (command && key === "y") { event.preventDefault(); return step("/api/redo"); }
  if (command && key === "c" && (S.sel || S.float)) {
    const area = S.float ? null : S.sel, canvas = document.createElement("canvas");
    if (S.float) { canvas.width = S.float.canvas.width; canvas.height = S.float.canvas.height; canvas.getContext("2d").drawImage(S.float.canvas, 0, 0); S.clip = { canvas, x: S.float.x, y: S.float.y, mask: S.float.mask }; }
    else if (cel()) { S.clip = { canvas: cut(cel(), area, false), x: area.x, y: area.y, mask: area.mask }; }
    else return;
    S.clipOnlyHere = true;                  // until the system clipboard holds it too, a paste means this piece
    try {
      const picture = new Promise(done => S.clip.canvas.toBlob(done, "image/png"));
      navigator.clipboard.write([new ClipboardItem({ "image/png": picture })]).then(() => { S.clipOnlyHere = false; }, () => {});
    } catch { /* no clipboard access here: copy and paste still work inside the studio */ }
    return toast("Copied");
  }
  if (command && key === "a") { event.preventDefault(); drop(); S.sel = { x: 0, y: 0, w: S.doc.width, h: S.doc.height }; context(); return draw(); }
  if (command && key === "d") { event.preventDefault(); drop(); S.sel = null; context(); return draw(); }
  if (command) return;
  if (S.poly && key === "enter") return closePolygon();
  if (S.poly && key === "backspace") { S.poly.pop(); if (!S.poly.length) S.poly = null; return overlay(); }
  if (S.tool === "move" && (S.sel || S.float) && key.startsWith("arrow") && usable()) {   // nudge the selection by one cell, ten with Shift
    event.preventDefault(); lift(); const n = event.shiftKey ? 10 : 1;
    S.float.x += key === "arrowright" ? n : key === "arrowleft" ? -n : 0; S.float.y += key === "arrowdown" ? n : key === "arrowup" ? -n : 0; return draw();
  }
  if (key === " ") { event.preventDefault(); if (!event.repeat) { S.space = true; S.panned = false; } return; }
  const tool = TOOLS.find(t => t[2].toLowerCase() === key); if (tool) return setTool(tool[0]);
  if (key === "enter") return drop();
  if (key === "escape") { if (S.poly) { S.poly = null; return overlay(); } if (S.proposal) return endProposal(); if (S.float) { S.float = null; load(); } S.sel = null; return draw(); }
  if (key === "delete" || key === "backspace") {
    if (S.float) { S.float = null; S.sel = null; draw(); return commit(); }
    if (S.sel && cel() && usable()) { cut(cel(), S.sel, true); draw(); return commit(); }
  }
  if (key === "arrowright") return go((S.frame + 1) % S.doc.frames.length, S.layer);
  if (key === "arrowleft") return go((S.frame - 1 + S.doc.frames.length) % S.doc.frames.length, S.layer);
  if (key === "arrowup") return go(S.frame, S.layer + 1);
  if (key === "arrowdown") return go(S.frame, S.layer - 1);
  if (key === "[") { S.size = SIZES[Math.max(0, SIZES.indexOf(S.size) - 1)]; return tools(); }
  if (key === "]") { S.size = SIZES[Math.min(SIZES.length - 1, SIZES.indexOf(S.size) + 1)]; return tools(); }
  if (key === "0") { S.moved = false; fit(); chrome(); return draw(); }
  if (key === "p") return play();
  if (key === "x") { S.mirror = !S.mirror; tools(); return overlay(); }
});
document.addEventListener("keyup", event => { if (event.key === " " && S.space) { S.space = false; if (!S.panned) play(); } });

tools(); load().then(listen).catch(fail);
window.studio = S;
})();
