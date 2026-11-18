import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// Import the actual browser render and download-gating functions without a 3D renderer.
const coreSource = await readFile(new URL('../housesync/static/plan-core.js', import.meta.url), 'utf8');
const coreURL = 'data:text/javascript;base64,' + Buffer.from(coreSource).toString('base64');
class Element {
  constructor() { this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.textContent = ''; this.classList = { toggle() {} }; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  set href(value) { this.setAttribute('href', value); }
  get href() { return this.getAttribute('href'); }
}
const elements = new Map();
const get = selector => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); };
const descendants = element => [element, ...element.children.flatMap(descendants)];
const links = () => [get('#purchase-orders-all'), ...descendants(get('#purchasing-body'))].filter(element => Object.hasOwn(element.dataset, 'purchaseOrderCode'));
get('#purchase-orders-all').dataset.purchaseOrderCode = 'all';
globalThis.document = {
  querySelector: get,
  querySelectorAll: selector => selector === '[data-purchase-order-code]' ? links() : [],
  createElement: () => new Element(),
};
const source = (await readFile(new URL('../housesync/static/app.js', import.meta.url), 'utf8'))
  .replace("import * as THREE from 'three';", 'const THREE = { Vector3: class {} };')
  .replace("import { OrbitControls } from '/vendor/OrbitControls.js';", '')
  .replace("import { PlanEditor } from './plan-editor.js';", '')
  .replace("from './plan-core.js'", "from '" + coreURL + "'")
  .split("form.addEventListener('submit', buildHouse);")[0]
  + '\nexport { renderPurchasing, setPurchaseOrderLinks, setDownloadLinks, state };';
const { renderPurchasing, setPurchaseOrderLinks, setDownloadLinks, state } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const fixture = () => ({
  revision: 'abcdef123456', settings: { currency: 'AUD' }, totals: { currency: 'AUD', subtotal: 9489.2 },
  boq: [
    { code: 'FLOOR_FINISH', description: 'Floor finish from clear internal area', net_quantity: 80, gross_quantity: 88, quantity: 88, unit: 'm2', rate: 65, amount: 5720, rate_origin: 'library', rate_source: 'illustrative', rate_effective_date: '2026-11-17' },
    { code: 'PAINT', description: '209.4 m2 measured net area x2 coats', measured_quantity: 209.4, net_quantity: 418.8, gross_quantity: 418.8, quantity: 418.8, unit: 'm2', rate: 9, amount: 3769.2, rate_origin: 'override', rate_source: 'Project allowance', rate_effective_date: '2026-11-18' },
    { code: 'DOOR', description: 'No doors modelled', gross_quantity: 0, quantity: 0, unit: 'each', rate: 650, amount: 0 },
  ],
});
const display = result => { state.result = result; state.dirty = false; state.busy = false; renderPurchasing(result); return get('#purchasing-body').children; };

test('Purchasing renders one draft per saved BOQ item using gross quantities without pack conversion', () => {
  const result = fixture(); const before = structuredClone(result); const rows = display(result);
  assert.equal(rows.length, 3); assert.equal(get('#purchasing-count').textContent, '3 draft purchase orders');
  assert.equal(rows[0].children[0].children[0].textContent, 'FLOOR_FINISH');
  assert.equal(rows[0].children[1].textContent, '88'); assert.equal(rows[0].children[2].textContent, 'm2');
  assert.equal(rows[1].children[1].textContent, '418.8'); assert.equal(rows[1].children[2].textContent, 'm2');
  assert.equal(rows[2].children[1].textContent, '0'); assert.match(rows[0].children[3].textContent, /AUD 65/);
  assert.equal(rows[0].children[4].textContent, 'AUD 5,720.00');
  assert.equal(get('#purchasing-total').textContent, 'AUD 9,489.20');
  assert.equal(get('#purchasing-revision').textContent, 'Source revision abcdef123456');
  assert.equal(get('#purchasing-empty').hidden, true); assert.deepEqual(result, before);
});

test('each PDF and bulk ZIP are bound to the displayed saved revision and item code', () => {
  display(fixture()); const actions = links(); assert.equal(actions.length, 4);
  assert.equal(actions[0].href, '/purchase-orders/abcdef123456/all.zip');
  const floor = actions.find(link => link.dataset.purchaseOrderCode === 'FLOOR_FINISH');
  assert.equal(floor.href, '/purchase-orders/abcdef123456/FLOOR_FINISH.pdf');
  assert.equal(floor.getAttribute('aria-disabled'), 'false'); assert.equal(floor.tabIndex, 0);
  assert.equal(floor.getAttribute('aria-label'), 'Download FLOOR_FINISH draft purchase order PDF');
  assert.equal(floor.textContent, 'Download draft PO PDF');
});

test('pending edits, a running build or mismatched displayed revision disable every PO download', () => {
  display(fixture());
  for (const next of [{ dirty: true, busy: false }, { dirty: false, busy: true }]) {
    Object.assign(state, next); setDownloadLinks();
    for (const link of links()) { assert.equal(link.getAttribute('aria-disabled'), 'true'); assert.equal(link.href, null); assert.equal(link.getAttribute('download'), null); assert.equal(link.tabIndex, -1); }
    assert.match(get('#purchasing-status').textContent, next.busy ? /Updating/ : /Pending edits/);
  }
  state.busy = false; setDownloadLinks(); assert.equal(links()[0].getAttribute('aria-disabled'), 'false');
  state.result = { ...state.result, revision: '123456abcdef' }; setPurchaseOrderLinks();
  assert.ok(links().every(link => link.getAttribute('aria-disabled') === 'true'));
});

test('disabled individual draft links prevent click navigation', () => {
  display(fixture()); state.dirty = true; setPurchaseOrderLinks();
  const item = links().find(link => link.dataset.purchaseOrderCode === 'PAINT'); let prevented = false;
  item.listeners.click({ preventDefault() { prevented = true; } }); assert.equal(prevented, true);
});

test('legacy rows use their saved quantity and rate without inventing provenance or currency', () => {
  const result = { revision: '123456abcdef', settings: { currency: 'SGD' }, totals: { currency: 'SGD', subtotal: 8600 }, boq: [
    { code: 'FLOOR', description: 'Historical slab', quantity: 80, unit: 'm2', rate: 95, amount: 7600 },
    { code: 'WINDOW', description: 'Historical windows', quantity: 2, unit: 'each', rate: 500, amount: 1000 },
  ] };
  const rows = display(result);
  assert.equal(rows[0].children[1].textContent, '80'); assert.equal(rows[0].children[3].textContent, 'SGD 95');
  assert.equal(rows[0].children[0].children.length, 2); assert.equal(get('#purchasing-total').textContent, 'SGD 8,600.00');
  assert.equal(links()[1].href, '/purchase-orders/123456abcdef/FLOOR.pdf');
  const unknown = display({ revision: '123456abcdef', boq: [{ code: 'FLOOR', description: '<script>text only</script>', quantity: 80, rate: 95, amount: 7600 }] });
  assert.equal(unknown[0].children[0].children[1].textContent, '<script>text only</script>');
  assert.match(unknown[0].children[3].textContent, /currency not recorded/); assert.equal(get('#purchasing-total').textContent, 'Not recorded');
});

test('fractional gross quantity and unit rate retain precision and reconcile to the saved amount', () => {
  const result = { revision: 'abcdef123456', totals: { currency: 'AUD', subtotal: 980.37 }, boq: [
    { code: 'FLOOR_FINISH', description: 'Fractional take-off', gross_quantity: 88.123456, quantity: 88.123456, unit: 'm2', rate: 11.125, amount: 980.37 },
  ] };
  const cells = display(result)[0].children;
  assert.equal(cells[1].textContent, '88.123456'); assert.equal(cells[3].textContent, 'AUD 11.125');
  const displayedAmount = Math.round(Number(cells[1].textContent) * Number(cells[3].textContent.replace('AUD ', '')) * 100) / 100;
  assert.equal(displayedAmount, result.boq[0].amount); assert.equal(cells[4].textContent, 'AUD 980.37');
});

test('no saved result and empty BOQs clear prior rows, totals and bulk links', () => {
  display(fixture());
  for (const result of [null, { revision: 'abcdef123456', boq: [] }, { revision: 'abcdef123456' }]) {
    display(result); assert.equal(get('#purchasing-body').children.length, 0);
    assert.equal(get('#purchasing-empty').hidden, false); assert.equal(get('#purchasing-total').textContent, '\u2014');
    assert.equal(get('#purchasing-count').textContent, '0 draft purchase orders');
    assert.equal(get('#purchase-orders-all').getAttribute('aria-disabled'), 'true'); assert.equal(get('#purchase-orders-all').href, null);
    assert.match(get('#purchasing-status').textContent, /Generate a saved BOQ/);
  }
});
