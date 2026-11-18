import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const coreSource = await readFile(new URL('../housesync/static/plan-core.js', import.meta.url), 'utf8');
const coreURL = 'data:text/javascript;base64,' + Buffer.from(coreSource).toString('base64');
const { normalizeBoqSettings, RATE_ITEMS, WASTAGE_DEFAULTS, defaultOpenings, describeChanges, DraftHistory } = await import(coreURL);
const fixture = () => ({
  project_name: 'Legacy design', length_mm: 10000, width_mm: 8000, height_mm: 3000,
  wall_thickness_mm: 200, paint_colour: 'Light blue', paint_hex: '#ADD8E6', currency: 'SGD',
  rates: { floor_m2: 80, wall_m3: 300, roof_m2: 120, paint_m2: 12, opening_each: 500 },
});
const modern = () => normalizeBoqSettings({ ...fixture(), currency: 'AUD', rate_overrides: {} });
const library = (sha = 'a'.repeat(64), floorRate = 95) => ({ sha256: sha, currency: 'AUD', rows: {
  FLOOR: { code: 'FLOOR', description: 'Floor slab', unit: 'm2', rate: floorRate, currency: 'AUD', source: 'Illustrative allowance', effective_date: '2026-11-17', notes: 'Not a supplier quote' },
  DOOR: { code: 'DOOR', description: 'Door allowance', unit: 'each', rate: 650, currency: 'AUD', source: 'Door allowance source', effective_date: '2026-11-18', notes: '' },
} });

// Run the real app form, reset and table functions against a small DOM without a renderer.
class Element {
  constructor() { this.dataset = {}; this.value = ''; this.children = []; this.options = []; this.textContent = ''; this.classList = { toggle() {}, add() {}, remove() {} }; this.listeners = {}; this.attributes = {}; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  set id(value) { this._id = value; elements.set('#' + value, this); }
  get id() { return this._id; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'href') delete this.href; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  trigger(name) { this.listeners[name]?.({ target: this }); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
const elements = new Map();
const html = await readFile(new URL('../housesync/static/index.html', import.meta.url), 'utf8');
for (const match of html.matchAll(/<input\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const element = new Element(); element.value = /\bvalue="([^"]*)"/.exec(match[0])?.[1] ?? '';
  elements.set('#' + match[1], element);
}
const currency = new Element(); currency.value = 'AUD'; currency.options = ['AUD', 'SGD', 'MYR', 'USD', 'GBP'].map(value => ({ value })); elements.set('#currency', currency);
globalThis.document = {
  querySelector(selector) { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); },
  querySelectorAll(selector) { if (selector === '[data-change-file]') return [document.querySelector('#export-client-quote'), document.querySelector('#export-variation-pdf')]; if (selector === '[data-purchase-order-code]') return [document.querySelector('#purchase-orders-all'), ...descendants(document.querySelector('#purchasing-body'))].filter(node => Object.hasOwn(node.dataset, 'purchaseOrderCode')); return []; }, createElement() { return new Element(); },
};
const appSource = (await readFile(new URL('../housesync/static/app.js', import.meta.url), 'utf8'))
  .replace("import * as THREE from 'three';", 'const THREE = { Vector3: class {} };')
  .replace("import { OrbitControls } from '/vendor/OrbitControls.js';", '')
  .replace("import { PlanEditor } from './plan-editor.js';", '')
  .replace("from './plan-core.js'", "from '" + coreURL + "'")
  .split("form.addEventListener('submit', buildHouse);")[0]
  + '\nexport { fillForm, settingsFromForm, comparableSettings, stableSettings, addAllowance, removeAllowance, renderClientQuote, clientQuoteMatches, setComparisonDownloads, selectedComparisonChanged, compareSavedRevisions, comparison, state, renderBOQ, renderPurchasing, setPurchaseOrderLinks, setDownloadLinks }; export function connectPlan(editor) { planEditor = editor; }';
const { fillForm, settingsFromForm, comparableSettings, stableSettings, addAllowance, removeAllowance, renderClientQuote, clientQuoteMatches, setComparisonDownloads, selectedComparisonChanged, compareSavedRevisions, comparison, state, connectPlan, renderBOQ, renderPurchasing, setPurchaseOrderLinks, setDownloadLinks } = await import('data:text/javascript;base64,' + Buffer.from(appSource).toString('base64'));


globalThis.window = { location: { origin: 'http://127.0.0.1:8766' } };
const clientLink = document.querySelector('#export-client-quote'); clientLink.dataset = { changeFile: 'client_quote_pdf', clientQuote: 'true' };
const internalLink = document.querySelector('#export-variation-pdf'); internalLink.dataset = { changeFile: 'internal_breakdown_pdf' };
const defaults = () => ({ ...fixture(), currency: 'AUD', rate_overrides: {} });
const childrenText = node => [node.textContent, ...node.children.flatMap(child => childrenText(child))].join(' ');
const allowanceField = (card, key) => card.children.flatMap(child => child.children).find(child => child.dataset?.allowanceField === key);
const before = 'aaaaaaaaaaaa', after = 'bbbbbbbbbbbb';
const quoteResult = () => ({
  before_revision: before, after_revision: after, comparison_id: before + '_' + after + '_abc123', currency: 'AUD',
  files: { client_quote_pdf: '/changes/' + before + '_' + after + '_abc123/client-variation-quote.pdf', internal_breakdown_pdf: '/changes/' + before + '_' + after + '_abc123/internal-variation-breakdown.pdf' },
  quote: { public: { before_revision: before, after_revision: after, currency: 'AUD', status: 'draft', quote_reference: 'VQ-001', builder_name: 'Test Builder', client_description: 'Extend the house.', issued_on: '2026-11-18', valid_until: '2026-12-02', scope: ['Extend the house length.'], credit_policy: 'Credits at cost.', markup_label: 'Combined overhead and margin (markup on cost)', totals: { additions_at_cost: 1000, credits_at_cost: 100, at_cost_delta: 900, additions_markup: 200, credits_markup: 0, markup_amount: 200, admin_fee: 50, subtotal_ex_gst: 1150, gst_rate: 10, gst_amount: 115, total_incl_gst: 1265 }, pc_allowances: [{ id: 'AL1', description: 'Tapware allowance', before: 0, after: 250, movement: 250, notes: 'Selection pending.' }], ps_allowances: [] }, internal: { supplier_costs: 'INTERNAL-SUPPLIER-CANARY', provenance: 'PRIVATE-RATES-CANARY' } },
});
const choosePair = () => { document.querySelector('#compare-before').value = before; document.querySelector('#compare-after').value = after; };


const descendants = node => [node, ...node.children.flatMap(descendants)];
document.querySelector('#purchase-orders-all').dataset.purchaseOrderCode='all';
const partitioned = () => ({ ...defaults(),
  openings: [...defaultOpenings(), { id:'D2', kind:'door', host:'P1', offset_mm:1500, width_mm:900, height_mm:2100, sill_mm:0 }],
  partitions: [{id:'P1',start_mm:[4000,0],end_mm:[4000,8000],thickness_mm:100,height_mm:3000,wall_type:'partition'}],
  rooms: [{id:'ROOM_AAAAAAAAAAAA',name:'Living room',finishes:{floor:'Timber',ceiling:'White board',skirting:null,paint:'Blue'}}], min_room_width_mm:900,
  builder_pricing:{overhead_margin_percent:25,admin_fee_aud:100,gst_percent:10,credit_treatment:'at_cost',validity_days:21,builder_name:'Test Builder',client_description:'Extend the living room.'},
  allowances:[{id:'AL31',kind:'prime_cost',description:'Tapware allowance',amount:250,note:'Selection pending.'}],
});

test('merged room, allowance and builder fields survive import/reopen and independent estimator edits', () => {
  const saved=partitioned(); fillForm(saved); const first=settingsFromForm();
  assert.deepEqual(first.partitions,saved.partitions); assert.equal(first.rooms[0].finishes.floor,'Timber');
  assert.equal(first.min_room_width_mm,900); assert.deepEqual(first.builder_pricing,saved.builder_pricing); assert.deepEqual(first.allowances,saved.allowances);
  assert.equal(stableSettings(first),stableSettings(comparableSettings(saved)));
  document.querySelector('#quote-markup').value='30'; document.querySelector('#override-floor-finish').value='75';
  const amount=allowanceField(document.querySelector('#quote-allowances').children[0],'amount'); amount.value='300'; amount.trigger('input');
  const edited=settingsFromForm(); assert.deepEqual(edited.partitions,first.partitions); assert.deepEqual(edited.rooms,first.rooms);
  assert.equal(edited.rate_overrides.FLOOR_FINISH,75); assert.equal(edited.builder_pricing.overhead_margin_percent,30); assert.equal(edited.allowances[0].amount,300);
  fillForm(JSON.parse(JSON.stringify(edited))); assert.deepEqual(settingsFromForm(),edited);
});

test('actual partition controller commits and undo keep the independent quote and allowance state', async () => {
  const controllerSource=(await readFile(new URL('../housesync/static/plan-editor.js',import.meta.url),'utf8')).replace("from './plan-core.js'", "from '"+coreURL+"'");
  const {PlanEditor}=await import('data:text/javascript;base64,'+Buffer.from(controllerSource).toString('base64'));
  const {movePartition}=await import(coreURL);
  fillForm(partitioned()); const initial=settingsFromForm(); const editor=Object.create(PlanEditor.prototype);
  Object.assign(editor,{draft:structuredClone(initial),baseline:structuredClone(initial),history:new DraftHistory(initial),reservedIds:new Set(initial.openings.map(row=>row.id)),reservedPartitionIds:new Set(['P1']),selected:{kind:'partition',id:'P1'},busy:false,gesture:null,feedback:{textContent:''},callbacks:{onChange(settings){fillForm(settings);}},render(){},renderCanvas(){},renderInspector(){},renderAudit(){},renderControls(){},fit(){}});
  connectPlan(editor); assert.equal(editor.commit(movePartition(initial,'P1',-200,0,100),'Moved partition'),true);
  assert.equal(settingsFromForm().partitions[0].start_mm[0],3800); assert.deepEqual(settingsFromForm().builder_pricing,initial.builder_pricing); assert.deepEqual(settingsFromForm().allowances,initial.allowances);
  const renamed=settingsFromForm(); renamed.rooms[0].name='Lounge'; renamed.rooms[0].finishes.paint='Sage'; assert.equal(editor.commit(renamed,'Room metadata changed'),true);
  assert.equal(settingsFromForm().rooms[0].name,'Lounge'); assert.equal(settingsFromForm().allowances[0].id,'AL31');
  document.querySelector('#quote-markup').value='35'; editor.setDraft(settingsFromForm());
  editor.action('undo'); assert.equal(settingsFromForm().builder_pricing.overhead_margin_percent,25); assert.equal(settingsFromForm().rooms[0].name,'Lounge');
  editor.action('undo'); assert.equal(settingsFromForm().rooms[0].name,'Living room'); assert.equal(settingsFromForm().partitions[0].start_mm[0],3800);
  editor.action('undo'); assert.equal(settingsFromForm().partitions[0].start_mm[0],4000); assert.deepEqual(settingsFromForm().allowances,initial.allowances);
  editor.action('redo'); assert.equal(settingsFromForm().partitions[0].start_mm[0],3800); connectPlan(null);
});

test('grouped room BOQ and purchasing retain base-rate identity, scope names and revision-specific links', () => {
  const settings=partitioned(); settings.rate_overrides={FLOOR_FINISH:11.125};
  const code='ROOM_AAAAAAAAAAAA_FLOOR_FINISH';
  const result={settings,revision:after,totals:{currency:'AUD',subtotal:980.37},boq_groups:[{id:'ROOM_AAAAAAAAAAAA',name:'Living room',subtotal:980.37}],boq:[{code,rate_code:'FLOOR_FINISH',group_id:'ROOM_AAAAAAAAAAAA',group_name:'Living room',description:'Floor finish - Timber',net_quantity:80.112233,wastage_percent:10,gross_quantity:88.123456,quantity:88.123456,unit:'m2',rate:11.125,amount:980.37}]};
  state.result=result; state.dirty=false; state.busy=false; renderBOQ(result); renderPurchasing(result);
  assert.match(childrenText(document.querySelector('#boq-body')),/Living room/); assert.match(childrenText(document.querySelector('#boq-body')),/Override/);
  const row=document.querySelector('#purchasing-body').children[0]; assert.match(childrenText(row),/Scope: Living room/); assert.equal(row.children[1].textContent,'88.123456'); assert.equal(row.children[3].textContent,'AUD 11.125');
  const link=descendants(row).find(node=>node.dataset.purchaseOrderCode===code); assert.match(link.href,new RegExp('/purchase-orders/'+after+'/'+code+'\\.pdf$'));
  assert.match(document.querySelector('#purchase-orders-all').href,/all.zip$/);
  choosePair(); comparison.result=quoteResult(); comparison.busy=false; setComparisonDownloads(); assert.equal(clientLink.getAttribute('aria-disabled'),'false');
  state.dirty=true; setDownloadLinks(); setComparisonDownloads(); assert.equal(link.getAttribute('aria-disabled'),'true'); assert.equal(clientLink.getAttribute('aria-disabled'),'true'); assert.equal(internalLink.getAttribute('aria-disabled'),'false');
  state.result=null; state.dirty=false;
});

test('merged UI retains all feature panels and uses the 4 MiB design import policy', async () => {
  const app=await readFile(new URL('../housesync/static/app.js',import.meta.url),'utf8');
  assert.match(app,/file.size > 4194304/); assert.match(app,/no larger than 4 MiB/);
  for (const id of ['tab-plan','tab-purchasing','tab-changes','client-quote-summary','quote-allowances','purchasing-body']) assert.ok(html.includes('id="'+id+'"'));
  const core=await readFile(new URL('../housesync/static/plan-core.js',import.meta.url),'utf8'); assert.match(core,/describeChanges/); assert.match(core,/builder_pricing/); assert.match(core,/partitionList/);
  for(const file of ['app.js','index.html','plan-core.js','plan-editor.js','style.css','plan-editor.css']) {const text=await readFile(new URL('../housesync/static/'+file,import.meta.url),'utf8');assert.doesNotMatch(text,/^<{7}|^={7}|^>{7}/m);}
});
