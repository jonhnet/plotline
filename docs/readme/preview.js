/* Local README review: image inspection, media playback and live reload. */
const viewer = document.createElement('dialog');
viewer.className = 'image-viewer';
viewer.setAttribute('aria-labelledby', 'image-viewer-title');
viewer.innerHTML = `
  <header class="viewer-toolbar">
    <strong id="image-viewer-title"></strong>
    <button type="button" data-action="fit" aria-pressed="true">Fit</button>
    <button type="button" data-action="actual" aria-pressed="false">1:1</button>
    <button type="button" data-action="close" aria-label="Close image preview">×</button>
  </header>
  <div class="viewer-stage"><img alt="" draggable="false"></div>
  <p class="viewer-hint" aria-live="polite">Click the image for 1:1.</p>`;
document.body.append(viewer);
const stage = viewer.querySelector('.viewer-stage');
const photo = stage.querySelector('img');
const hint = viewer.querySelector('.viewer-hint');
const fitButton = viewer.querySelector('[data-action="fit"]');
const actualButton = viewer.querySelector('[data-action="actual"]');
let zoomed = false, scale = 1, x = 0, y = 0, source = null;
let pointer = null, moved = false, oldOverflow = '', generation = 0;

function constrain(position, imageSize, stageSize) {
  return imageSize <= stageSize ? (stageSize - imageSize) / 2
    : Math.min(0, Math.max(stageSize - imageSize, position));
}

function paint() {
  x = constrain(x, photo.naturalWidth * scale, stage.clientWidth);
  y = constrain(y, photo.naturalHeight * scale, stage.clientHeight);
  photo.style.width = `${photo.naturalWidth}px`;
  photo.style.height = `${photo.naturalHeight}px`;
  photo.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  stage.classList.toggle('is-zoomed', zoomed);
  fitButton.setAttribute('aria-pressed', String(!zoomed));
  actualButton.setAttribute('aria-pressed', String(zoomed));
  hint.textContent = zoomed ? '1:1 · Drag to pan. Click to fit. Esc to close.'
    : 'Click the image for 1:1. Esc to close.';
}

function fit() {
  if (!photo.naturalWidth) return;
  zoomed = false;
  scale = Math.min(1, stage.clientWidth / photo.naturalWidth, stage.clientHeight / photo.naturalHeight);
  x = (stage.clientWidth - photo.naturalWidth * scale) / 2;
  y = (stage.clientHeight - photo.naturalHeight * scale) / 2;
  paint();
}

function actual(clientX, clientY) {
  if (!photo.naturalWidth) return;
  const bounds = stage.getBoundingClientRect();
  const px = clientX == null ? stage.clientWidth / 2 : clientX - bounds.left;
  const py = clientY == null ? stage.clientHeight / 2 : clientY - bounds.top;
  x = px - (px - x) / scale;
  y = py - (py - y) / scale;
  scale = 1;
  zoomed = true;
  paint();
}

for (const figure of document.querySelectorAll('.sample-still')) {
  const link = figure.querySelector('a:has(img)');
  link.classList.add('inspect-image');
  link.setAttribute('aria-haspopup', 'dialog');
  link.addEventListener('click', async event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    source = link;
    const title = link.querySelector('img').alt;
    viewer.querySelector('#image-viewer-title').textContent = title;
    photo.alt = title;
    photo.style.visibility = 'hidden';
    hint.textContent = 'Loading image…';
    oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    viewer.showModal();
    viewer.querySelector('[data-action="close"]').focus();
    const request = ++generation;
    photo.src = link.href;
    try {
      await photo.decode();
      if (!viewer.open || request !== generation) return;
      fit();
      photo.style.visibility = 'visible';
    } catch {
      if (viewer.open && request === generation) hint.textContent = 'Could not load this image. Close and try again.';
    }
  });
}

fitButton.addEventListener('click', fit);
actualButton.addEventListener('click', () => actual());
viewer.querySelector('[data-action="close"]').addEventListener('click', () => viewer.close());
viewer.addEventListener('close', () => {
  generation++;
  pointer = null;
  document.body.style.overflow = oldOverflow;
  source?.focus({preventScroll: true});
});
viewer.addEventListener('click', event => {
  if (event.target !== viewer) return;
  const b = viewer.getBoundingClientRect();
  if (event.clientX < b.left || event.clientX > b.right || event.clientY < b.top || event.clientY > b.bottom) viewer.close();
});
stage.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  moved = false;
  pointer = {id: event.pointerId, startX: event.clientX, startY: event.clientY,
    x: event.clientX, y: event.clientY};
  if (zoomed) stage.setPointerCapture(event.pointerId);
});
stage.addEventListener('pointermove', event => {
  if (!pointer || pointer.id !== event.pointerId) return;
  if (Math.hypot(event.clientX - pointer.startX, event.clientY - pointer.startY) > 4) moved = true;
  if (zoomed && moved) {
    x += event.clientX - pointer.x;
    y += event.clientY - pointer.y;
    paint();
  }
  pointer.x = event.clientX;
  pointer.y = event.clientY;
});
stage.addEventListener('pointerup', () => {pointer = null;});
stage.addEventListener('pointercancel', () => {pointer = null; moved = true;});
stage.addEventListener('click', event => {
  if (moved) {moved = false; return;}
  if (zoomed) fit(); else actual(event.clientX, event.clientY);
});
stage.addEventListener('wheel', event => {
  if (!zoomed) return;
  event.preventDefault();
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1;
  x -= event.deltaX * unit;
  y -= event.deltaY * unit;
  paint();
}, {passive: false});
viewer.addEventListener('keydown', event => {
  if (!zoomed || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
  event.preventDefault();
  x += event.key === 'ArrowLeft' ? 50 : event.key === 'ArrowRight' ? -50 : 0;
  y += event.key === 'ArrowUp' ? 50 : event.key === 'ArrowDown' ? -50 : 0;
  paint();
});
new ResizeObserver(() => {
  if (viewer.open && photo.naturalWidth) {if (zoomed) paint(); else fit();}
}).observe(stage);

if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.querySelectorAll('video.sample-video').forEach(video => video.play().catch(() => {}));
}
let version;
setInterval(async () => {
  try {
    const latest = await (await fetch('/__version')).text();
    if (version && latest !== version) {
      sessionStorage.setItem('readme-scroll', scrollY);
      location.reload();
    }
    version = latest;
  } catch {}
}, 2500);
const scroll = sessionStorage.getItem('readme-scroll');
if (scroll) {window.scrollTo(0, Number(scroll)); sessionStorage.removeItem('readme-scroll');}
