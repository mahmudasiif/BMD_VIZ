/**
 * BMD Viz smoke driver — Playwright-based headless browser harness.
 *
 * Usage (from project root /home/mics02/bmd_viz):
 *   node .claude/skills/run-bmd-viz/driver.mjs [command] [options]
 *
 * Commands:
 *   screenshot [step]   Take screenshots of all layers (optionally at a given step index)
 *   health              Hit /health and print JSON
 *   point <lat> <lon>   Point-query a location at current step
 *
 * Screenshots land in .claude/skills/run-bmd-viz/screenshots/
 */

import { chromium } from 'playwright';
import { mkdir } from 'fs/promises';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const BASE    = process.env.BMD_BASE ?? 'http://localhost:8000';
const DIR     = dirname(fileURLToPath(import.meta.url));
const SS_DIR  = resolve(DIR, 'screenshots');

const [,, cmd = 'screenshot', arg1, arg2] = process.argv;

async function waitForApp(page) {
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30_000 });
  await page.waitForSelector('#loading', { state: 'hidden', timeout: 25_000 });
  await page.waitForTimeout(2000);  // let wind particles settle
}

async function launch() {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-gpu'] });
  const page    = await browser.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });
  return { browser, page };
}

// ── health ────────────────────────────────────────────────────────────────────
if (cmd === 'health') {
  const { browser, page } = await launch();
  await page.goto(BASE + '/health');
  const body = await page.textContent('body');
  console.log(body);
  await browser.close();
  process.exit(0);
}

// ── point query ───────────────────────────────────────────────────────────────
if (cmd === 'point') {
  const lat = parseFloat(arg1 ?? '23.72');
  const lon = parseFloat(arg2 ?? '90.41');
  const { browser, page } = await launch();
  await waitForApp(page);
  const run = await page.evaluate(() => window.state?.run);
  if (!run) { console.error('App not ready'); await browser.close(); process.exit(1); }
  const step = parseInt(await page.evaluate(() => window.state?.step ?? 0));
  const url  = `${BASE}/point/${run.run_date}/${run.run_hour}/${step}?lat=${lat}&lon=${lon}`;
  const result = await page.evaluate(async (u) => {
    const r = await fetch(u); return r.json();
  }, url);
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
  process.exit(0);
}

// ── screenshot (default) ──────────────────────────────────────────────────────
await mkdir(SS_DIR, { recursive: true });

const stepTarget = arg1 !== undefined ? parseInt(arg1) : null;

const { browser, page } = await launch();
await waitForApp(page);

// Optionally jump to a specific step
if (stepTarget !== null) {
  await page.evaluate((s) => {
    const sl = document.getElementById('step-slider');
    sl.value = Math.min(s, parseInt(sl.max));
    sl.dispatchEvent(new Event('input'));
  }, stepTarget);
  await page.waitForTimeout(1500);
}

const ts   = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
const snap = async (name) => {
  const p = resolve(SS_DIR, `${ts}-${name}.png`);
  await page.screenshot({ path: p });
  console.log(`  saved: ${p}`);
};

// Layers are mutually exclusive — clicking one deselects the others.

// 1. Wind (default): speed colour field + white streamlines
await snap('01-wind');

// Jump to step ~16 (≈ T+48h) so scalar fields show more spatial variation
await page.evaluate(() => {
  const sl = document.getElementById('step-slider');
  sl.value = Math.min(16, parseInt(sl.max));
  sl.dispatchEvent(new Event('input'));
});
await page.waitForTimeout(800);

// 2. Rain (blue precipitation)
await page.click('#btn-rain');
await page.waitForTimeout(1200);
await snap('02-rain');

// 3. Temperature
await page.click('#btn-temp');
await page.waitForTimeout(1200);
await snap('03-temp');

// 4. Humidity
await page.click('#btn-humidity');
await page.waitForTimeout(1200);
await snap('04-humidity');

// 5. Cloud
await page.click('#btn-cloud');
await page.waitForTimeout(1200);
await snap('05-cloud');

// 6. Point query — Dhaka area
await page.click('#btn-cloud');   // toggle cloud off to see pin clearly
const mapBox = await page.evaluate(() => {
  const r = document.getElementById('map').getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
});
// Click approximately over Dhaka (~23.7°N 90.4°E) at zoom 7 centred on 23.8/90.35
await page.mouse.click(mapBox.x + mapBox.w * 0.522, mapBox.y + mapBox.h * 0.473);
await page.waitForSelector('#info-card.visible', { timeout: 8_000 });
await page.waitForTimeout(1500);
await snap('06-point-query');

// 7. Play one cycle (5 steps)
await page.click('#play-btn');
await page.waitForTimeout(4000);
await page.click('#play-btn');   // pause
await snap('07-playback');

await browser.close();
console.log('\nDone. Latest screenshots in:', SS_DIR);
