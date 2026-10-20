'use strict';
// Placement changes the composed page only; the original image and its candidates stay intact.
let positionEdit = null;
const clampPosition = n => Math.max(-2, Math.min(2, n));
function positionValues() {
  return {x: Number($('position-x').value) / 100, y: Number($('position-y').value) / 100, scale: Number($('position-scale').value) / 100};
}
function setPositionValues(v) {
  $('position-x').value = (v.x * 100).toFixed(1);
  $('position-y').value = (v.y * 100).toFixed(1);
  $('position-scale').value = Math.round(v.scale * 100);
  drawPosition();
}
function drawPosition() {
  if (!positionEdit) return;
  const {image, rect: [x, y, w, h], polygon, crop} = positionEdit;
  const canvas = $('position-canvas'), ctx = canvas.getContext('2d'), t = positionValues();
  $('position-scale-label').textContent = `${Math.round(t.scale * 100)}%`;
  const ratio = canvas.width / w;
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#e5e8e3'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.save(); ctx.scale(ratio, ratio); ctx.beginPath();
  polygon.forEach(([px, py], i) => i ? ctx.lineTo(px-x, py-y) : ctx.moveTo(px-x, py-y)); ctx.closePath();
  ctx.save(); ctx.clip(); ctx.fillStyle = 'white'; ctx.fillRect(0, 0, w, h);
  const scale = Math.max(w / image.naturalWidth, h / image.naturalHeight) * t.scale;
  const dw = image.naturalWidth * scale, dh = image.naturalHeight * scale;
  ctx.drawImage(image, (w-dw)*crop[0]+w*t.x, (h-dh)*crop[1]+h*t.y, dw, dh);
  ctx.restore(); ctx.strokeStyle = '#15191e'; ctx.lineWidth = 4; ctx.stroke(); ctx.restore();
}
async function openPosition() {
  if (!current()?.image || isWhole()) return;
  await save();
  const panel = current(), page = project.pages[pageIndex];
  // Use current template geometry, even if the preview PNG has not been rebuilt yet.
  const layout = meta.layouts.find(l => l.id === page.template);
  const polygon = layout.pixel_polygons[page.panels.findIndex(p => p.id === panel.id)];
  const xs = polygon.map(p => p[0]), ys = polygon.map(p => p[1]);
  const rect = [Math.min(...xs), Math.min(...ys), Math.max(...xs)-Math.min(...xs), Math.max(...ys)-Math.min(...ys)];
  const image = new Image(); image.src = asset(panel.image); await image.decode();
  positionEdit = {projectId: project.id, panelId: panel.id, page: pageIndex + 1, revision: project.revision, image, rect, polygon, crop: [...(panel.crop || [.5, .5])]};
  const ratio = Math.min(1, 760 / rect[2], 470 / rect[3]);
  $('position-canvas').width = Math.round(rect[2] * ratio); $('position-canvas').height = Math.round(rect[3] * ratio);
  $('position-title').textContent = `コマ ${panel.id} · 画像の位置調整`;
  $('position-error').textContent = '';
  setPositionValues(panel.image_transform || {x: 0, y: 0, scale: 1});
  $('position-dialog').showModal();
}
$('adjust-position').onclick = () => action(openPosition);
$('panel-image').ondblclick = () => action(openPosition);
for (const id of ['position-x', 'position-y', 'position-scale']) $(id).oninput = drawPosition;
$('position-reset').onclick = () => { positionEdit.crop = [.5, .5]; setPositionValues({x: 0, y: 0, scale: 1}); };
$('position-contain').onclick = () => {
  const {image, rect: [, , w, h]} = positionEdit;
  positionEdit.crop = [.5, .5];
  setPositionValues({x: 0, y: 0, scale: Math.max(.1, Math.min(w/image.naturalWidth, h/image.naturalHeight) / Math.max(w/image.naturalWidth, h/image.naturalHeight))});
};
let positionDrag = null;
$('position-canvas').onpointerdown = e => {
  if (busy || !positionEdit) return;
  positionDrag = {x: e.clientX, y: e.clientY, value: positionValues()};
  e.currentTarget.setPointerCapture(e.pointerId);
};
$('position-canvas').onpointermove = e => {
  if (!positionDrag || busy) return;
  const r = e.currentTarget.getBoundingClientRect(), {x, y, value} = positionDrag;
  setPositionValues({...value, x: clampPosition(value.x + (e.clientX-x)/r.width), y: clampPosition(value.y + (e.clientY-y)/r.height)});
};
for (const name of ['onpointerup', 'onpointercancel', 'onlostpointercapture']) $('position-canvas')[name] = () => { positionDrag = null; };
$('position-cancel').onclick = () => $('position-dialog').close();
$('position-dialog').onclose = () => { positionEdit = null; positionDrag = null; };
$('position-dialog').oncancel = e => { if (busy) e.preventDefault(); };
$('position-form').onsubmit = e => {
  e.preventDefault();
  action(async () => {
    const edit = positionEdit;
    try {
      await api('/api/panel-position', {project_id: edit.projectId, revision: edit.revision, panel_id: edit.panelId, transform: positionValues(), crop: edit.crop});
    } catch (error) { $('position-error').textContent = error.message; throw error; }
    $('position-dialog').close();
    status('画像位置を保存しました。このページをPNGで組版しています…');
    try { await api('/api/compose-page', {project_id: edit.projectId, page_number: edit.page}); }
    catch (error) { await load(edit.projectId); throw new Error('位置は保存済みですがPNG組版に失敗しました。「再組版」で再実行してください: ' + error.message); }
    await load(edit.projectId);
    status(`画像位置を保存し、PAGE ${edit.page} をPNGで組版しました`);
  });
};
