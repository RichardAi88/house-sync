import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const coreSource = await readFile(new URL('../housesync/static/plan-core.js', import.meta.url), 'utf8');
const coreURL = 'data:text/javascript;base64,' + Buffer.from(coreSource).toString('base64');
const { normalizeBoqSettings, RATE_DEFAULTS, WASTAGE_DEFAULTS, defaultOpenings, describeChanges, DraftHistory } = await import(coreURL);
const fixture = () => ({
  project_name: 'Legacy design', length_mm: 10000, width_mm: 8000, height_mm: 3000,
  wall_thickness_mm: 200, paint_colour: 'Light blue', paint_hex: '#ADD8E6', currency: 'SGD',
  rates: { floor_m2: 80, wall_m3: 300, roof_m2: 120, paint_m2: 12, opening_each: 500 },
});

// Run the actual app form/BOQ functions against a small DOM; no 3D renderer or server substitution.
class Element {
  constructor() { this.value = ''; this.children = []; this.options = []; this.textContent = ''; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
const elements = new Map();
const html = await readFile(new URL('../housesync/static/index.html', import.meta.url), 'utf8');
for (const match of html.matchAll(/<input\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const element = new Element();
  element.value = /\bvalue="([^"]*)"/.exec(match[0])?.[1] ?? '';
  elements.set('#' + match[1], element);
}
const currency = new Element(); currency.value = 'AUD'; currency.options = ['AUD', 'SGD', 'MYR', 'USD', 'GBP'].map(value => ({ value })); elements.set('#currency', currency);
globalThis.document = {
  querySelector(selector) { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); },
  querySelectorAll() { return []; },
  createElement() { return new Element(); },
};
const appSource = (await readFile(new URL('../housesync/static/app.js', import.meta.url), 'utf8'))
  .replace("import * as THREE from 'three';", 'const THREE = { Vector3: class {} };')
  .replace("import { OrbitControls } from '/vendor/OrbitControls.js';", '')
  .replace("import { PlanEditor } from './plan-editor.js';", '')
  .replace("from './plan-core.js'", "from '" + coreURL + "'")
  .split("form.addEventListener('submit', buildHouse);")[0]
  + '\nexport { fillForm, settingsFromForm, comparableSettings, stableSettings, renderBOQ };';
const { fillForm, settingsFromForm, comparableSettings, stableSettings, renderBOQ } = await import('data:text/javascript;base64,' + Buffer.from(appSource).toString('base64'));

test('pricing defaults normalize legacy settings without altering saved currency, rates or input', () => {
  const legacy = fixture(); const normalized = normalizeBoqSettings(legacy);
  assert.equal(normalized.currency, 'SGD'); assert.equal(normalized.rates.floor_m2, 80);
  assert.equal(normalized.paint_coats, 2); assert.equal(normalized.rates.floor_finish_m2, 65);
  assert.deepEqual(normalized.wastage, WASTAGE_DEFAULTS); assert.equal(legacy.paint_coats, undefined);
  normalized.rates.floor_m2 = 999; normalized.wastage.floor_finish = 30;
  assert.equal(legacy.rates.floor_m2, 80); assert.equal(WASTAGE_DEFAULTS.floor_finish, 10);
});

test('opening a legacy JSON resets all absent pricing controls and has no false draft changes', () => {
  fillForm({ ...fixture(), paint_coats: 7, wastage: { floor_finish: 90 }, rates: { ...RATE_DEFAULTS, ceiling_m2: 999 }, rate_source: 'Previous file', rate_date: '2025-01-01' });
  const legacy = fixture(); fillForm(legacy); const submitted = settingsFromForm();
  assert.equal(submitted.paint_coats, 2); assert.equal(submitted.wastage.floor_finish, 10);
  assert.equal(submitted.rates.ceiling_m2, 45); assert.equal(submitted.currency, 'SGD');
  assert.equal(submitted.rate_date, '2026-11-17'); assert.match(submitted.rate_source, /no supplier quotes/);
  assert.equal(stableSettings(submitted), stableSettings(comparableSettings({ ...legacy, openings: defaultOpenings() })));
  assert.deepEqual(describeChanges(legacy, submitted), []);
});

test('sidebar serializes every rate, per-item wastage, coat count and source/date', () => {
  fillForm({ ...fixture(), currency: 'AUD' });
  document.querySelector('#paint-coats').value = 3;
  document.querySelector('#wastage-ceiling').value = 7.5;
  document.querySelector('#wastage-window').value = 2;
  document.querySelector('#rate-floor-finish').value = 82.125;
  document.querySelector('#rate-source').value = 'User-entered allowance';
  document.querySelector('#rate-date').value = '2026-11-18';
  const submitted = settingsFromForm();
  assert.equal(submitted.paint_coats, 3); assert.equal(submitted.wastage.ceiling, 7.5);
  assert.equal(submitted.wastage.window, 2); assert.equal(submitted.rates.floor_finish_m2, 82.125);
  assert.deepEqual(Object.keys(submitted.rates).sort(), Object.keys(RATE_DEFAULTS).sort());
  assert.deepEqual(Object.keys(submitted.wastage).sort(), Object.keys(WASTAGE_DEFAULTS).sort());
  assert.equal(submitted.rate_source, 'User-entered allowance'); assert.equal(submitted.rate_date, '2026-11-18');
});

test('BOQ displays net, wastage and gross alongside the rate and amount', () => {
  renderBOQ({ settings: { rate_source: 'Illustrative allowance', rate_date: '2026-11-17' }, boq: [
    { code: 'FLOOR_FINISH', description: 'Clear internal floor finish', net_quantity: 80, wastage_percent: 10, gross_quantity: 88, quantity: 88, unit: 'm2', rate: 65, amount: 5720 },
  ] });
  const cells = document.querySelector('#boq-body').children[0].children.map(cell => cell.textContent);
  assert.equal(cells.length, 8); assert.match(cells[2], /80/); assert.match(cells[3], /10/); assert.match(cells[4], /88/);
  assert.equal(cells[5], 'm2'); assert.match(cells[6], /65/); assert.match(cells[7], /5.?720/);
  assert.match(document.querySelector('#measurement-note').textContent, /illustrative AUD rates, not a tender estimate/);
});

test('plan review describes coats and wastage and undo restores complete pricing settings', () => {
  const before = normalizeBoqSettings(fixture()); const after = structuredClone(before);
  after.paint_coats = 3; after.wastage.skirting = 15; after.rates.external_render_m2 = 42;
  after.rate_source = 'Edited allowance'; after.rate_date = '2026-11-18';
  const descriptions = describeChanges(before, after);
  assert.ok(descriptions.some(text => /Paint coats: 2 to 3/.test(text)));
  assert.ok(descriptions.some(text => /skirting wastage: 10% to 15%/.test(text)));
  assert.ok(descriptions.some(text => /external render m2 rate: 40 to 42/.test(text)));
  assert.ok(descriptions.some(text => /Rate source/.test(text))); assert.ok(descriptions.some(text => /Rate date/.test(text)));
  const history = new DraftHistory(before); history.push(after);
  assert.deepEqual(history.undo(), { ...before, openings: defaultOpenings() });
  assert.deepEqual(history.redo(), { ...after, openings: defaultOpenings() });
});
