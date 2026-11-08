// Read-only checks against the independent README server, never Studio APIs.
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {chromium} from '../../controller/node_modules/playwright/index.mjs';

const base = process.argv[2] || 'http://127.0.0.1:9195';
const browser = await chromium.launch({headless: true});
await mkdir('build/readme-preview', {recursive: true});
try {
  const context = await browser.newContext({viewport: {width: 1280, height: 900}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);});
  await page.goto(base, {waitUntil: 'networkidle'});
  await page.waitForFunction(() => [...document.querySelectorAll('.sample img')].every(image => image.complete && image.naturalWidth));
  assert.equal(await page.locator('.sample').count(), 16);
  assert.equal(await page.locator('.sample-still').count(), 15);
  const sizes = await page.locator('.sample').evaluateAll(figures => figures.map(figure => ({
    frame: figure.getBoundingClientRect().width,
    caption: figure.querySelector('figcaption').getBoundingClientRect().width,
    image: figure.querySelector('img,video').getBoundingClientRect().width,
  })));
  for (const size of sizes) {
    assert.equal(size.frame, 440);
    assert.equal(size.caption, size.image);
  }
  const spider = await page.locator('[data-sample="spider-man"] figcaption').innerText();
  assert.ok(spider.indexOf('In this illustration') < spider.indexOf('Editable SVG'));
  assert.ok(!await page.locator('main').innerText().then(text => text.includes('Packaging work')));
  assert.equal(await page.locator('video').evaluate(video => video.paused), true);
  await page.screenshot({path: 'build/readme-preview/compact-desktop.png'});

  const thumbnail = page.locator('[data-sample="snoqualmie"] .inspect-image');
  await thumbnail.click();
  const modal = page.locator('.image-viewer');
  const image = modal.locator('img');
  await image.waitFor({state: 'visible'});
  await page.waitForFunction(() => document.querySelector('.viewer-hint').textContent.startsWith('Click'));
  const stage = modal.locator('.viewer-stage');
  const stageBox = await stage.boundingBox();
  let imageBox = await image.boundingBox();
  assert.ok(imageBox.width <= stageBox.width + .1 && imageBox.height <= stageBox.height + .1);
  await page.screenshot({path: 'build/readme-preview/inspector-fit.png'});
  const cx = stageBox.x + stageBox.width / 2, cy = stageBox.y + stageBox.height / 2;
  await page.mouse.click(cx, cy);
  assert.equal(await modal.locator('[data-action="actual"]').getAttribute('aria-pressed'), 'true');
  imageBox = await image.boundingBox();
  assert.equal(Math.round(imageBox.width), await image.evaluate(image => image.naturalWidth));
  const before = imageBox.x;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 160, cy + 70, {steps: 6});
  await page.mouse.up();
  assert.ok((await image.boundingBox()).x > before + 100, 'Drag moves the full-size image');
  assert.equal(await modal.locator('[data-action="actual"]').getAttribute('aria-pressed'), 'true', 'Drag must not toggle zoom');
  await page.screenshot({path: 'build/readme-preview/inspector-actual.png'});
  await page.mouse.click(cx, cy);
  assert.equal(await modal.locator('[data-action="fit"]').getAttribute('aria-pressed'), 'true');
  await page.keyboard.press('Escape');
  assert.equal(await modal.isVisible(), false);
  assert.equal(await thumbnail.evaluate(link => document.activeElement === link), true);
  assert.equal(await page.locator('body').evaluate(body => body.style.overflow), '');

  // The same inspector must load the newly selected image, not stale dimensions.
  await page.locator('[data-sample="paine-field"] .inspect-image').click();
  await page.waitForFunction(() => document.querySelector('.viewer-stage img').style.visibility === 'visible');
  assert.match(await image.getAttribute('src'), /paine-field/);
  await modal.locator('[data-action="close"]').click();

  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => window.scrollTo(0, 0));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const mobile = await page.locator('[data-sample="plotter"]').boundingBox();
  assert.equal(mobile.width, 350);
  await page.screenshot({path: 'build/readme-preview/compact-mobile.png'});
  await page.locator('[data-sample="sierpinski"] .inspect-image').click();
  await page.waitForFunction(() => document.querySelector('.viewer-stage img').style.visibility === 'visible');
  const bounds = await modal.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 844);
  await modal.locator('[data-action="actual"]').click();
  assert.equal(Math.round((await image.boundingBox()).width), await image.evaluate(image => image.naturalWidth));
  await modal.locator('[data-action="close"]').click();
  const markdown = await (await context.request.get(base + '/README.md')).text();
  assert.ok(markdown.includes('width="440"'));
  assert.ok(!markdown.includes('{{links:'));
  assert.deepEqual(errors, []);
  console.log('PASS compact frames/captions, source-link order, fit/1:1/drag, Escape/focus, image switching, mobile and reduced motion');
} finally {
  await browser.close();
}
