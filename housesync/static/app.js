import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';
import { PlanEditor } from './plan-editor.js';
import { HOSTS, normalizeBoqSettings, wallFinishColours, RATE_ITEMS, WASTAGE_DEFAULTS } from './plan-core.js';

const $ = (selector) => document.querySelector(selector);
const form = $('#house-form');
const state = { result: null, dirty: false, busy: false, selected: null, settingsURL: null };
let planEditor = null;
const legacyOpenings = () => [
  { id: 'D1', kind: 'door', host: 'W1', offset_mm: null, width_mm: 1000, height_mm: 2100, sill_mm: 0 },
  { id: 'N1', kind: 'window', host: 'W3', offset_mm: null, width_mm: 1500, height_mm: 1200, sill_mm: 900 },
  { id: 'N2', kind: 'window', host: 'W2', offset_mm: null, width_mm: 1500, height_mm: 1200, sill_mm: 900 },
];
let draftOpenings = legacyOpenings();
let draftAllowances = [];
const reservedAllowanceIds = new Set();
const interiorSettings = settings => ({
  partitions: structuredClone((settings.partitions ?? []).map(wall => ({
    thickness_mm: 100, height_mm: 3000, wall_type: 'partition', ...wall,
  }))),
  rooms: (settings.rooms ?? []).map(room => ({
    id: room.id, name: room.name,
    finishes: Object.fromEntries(Object.entries(room.finishes ?? {}).filter(([, value]) => value != null && value !== '')),
  })),
  min_room_width_mm: settings.min_room_width_mm ?? 900,
});
let draftInterior = interiorSettings({});
const stableSettings = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
const comparableSettings = settings => normalizeBoqSettings({ ...settings, ...interiorSettings(settings), openings: settings.openings ?? legacyOpenings() });
const comparison = { revisions: [], result: null, busy: false, request: 0, refresh: 0, selection: 0 };
const rateLibraryState = { current: null, error: '', request: 0, busy: false };
const overrideFields = Object.fromEntries(RATE_ITEMS.map(([code]) => [code, '#override-' + code.toLowerCase().replaceAll('_', '-')]));
const wastageFields = Object.fromEntries(Object.keys(WASTAGE_DEFAULTS).map(key => [key, '#wastage-' + key.replaceAll('_', '-')]));
const fields = ['#project-name', '#length', '#width', '#height', '#thickness', '#exterior-kind', '#exterior-name', '#exterior-hex', '#inner-leaf-finish', '#inner-brick-hex', '#paint-colour', '#paint-hex', '#paint-coats', '#currency', '#rate-source', '#rate-date', '#builder-name', '#quote-markup', '#quote-admin', '#quote-credit-treatment', '#quote-validity', '#client-description', ...Object.values(overrideFields), ...Object.values(wastageFields)];
const viewer = { scene: null, camera: null, renderer: null, controls: null, group: null, parts: [], selected: null, selectedFace: null, finishKey: null, size: 12, target: new THREE.Vector3() };
const number = (value, digits = 2) => Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const money = (value, currency = 'AUD') => `${currency} ${number(value)}`;

function settingsFromForm() {
  const wallFinishes = {
    exterior_finish: { kind: $('#exterior-kind').value, name: $('#exterior-name').value.trim(), hex: $('#exterior-hex').value.toUpperCase() },
    inner_leaf_finish: $('#inner-leaf-finish').value, inner_brick_hex: $('#inner-brick-hex').value.toUpperCase(),
  };
  return { ...wallFinishes, project_name: $('#project-name').value.trim(), length_mm: Math.round(Number($('#length').value) * 1000), width_mm: Math.round(Number($('#width').value) * 1000), height_mm: Math.round(Number($('#height').value) * 1000), wall_thickness_mm: Math.round(Number($('#thickness').value) * 1000), paint_colour: $('#paint-colour').value.trim(), paint_hex: $('#paint-hex').value.toUpperCase(), paint_coats: Number($('#paint-coats').value), currency: $('#currency').value, rate_overrides: Object.fromEntries(Object.entries(overrideFields).filter(([, id]) => $(id).value.trim() !== '').map(([code, id]) => [code, Number($(id).value)])), wastage: Object.fromEntries(Object.entries(wastageFields).map(([key, id]) => [key, Number($(id).value)])), rate_source: $('#rate-source').value.trim(), rate_date: $('#rate-date').value, openings: structuredClone(draftOpenings), ...interiorSettings(draftInterior), builder_pricing: { overhead_margin_percent: Number($('#quote-markup').value), admin_fee_aud: Number($('#quote-admin').value), gst_percent: 10, credit_treatment: $('#quote-credit-treatment').value, validity_days: Number($('#quote-validity').value), builder_name: $('#builder-name').value.trim(), client_description: $('#client-description').value.trim().replaceAll('\r\n', '\n') }, allowances: draftAllowances.map(item => ({ ...item, description: item.description.trim().replaceAll('\r\n', '\n'), note: item.note.trim().replaceAll('\r\n', '\n') })) };
}

function fillForm(settings) {
  settings = normalizeBoqSettings(settings);
  draftOpenings = structuredClone(settings.openings ?? legacyOpenings());
  draftAllowances = structuredClone(settings.allowances); for (const item of draftAllowances) reservedAllowanceIds.add(item.id);
  draftInterior = interiorSettings(settings);
  if (settings.currency && ![...$('#currency').options].some(option => option.value === settings.currency)) {
    const option = document.createElement('option'); option.value = settings.currency; option.textContent = settings.currency; $('#currency').append(option);
  }
  const map = { '#exterior-kind': settings.exterior_finish.kind, '#exterior-name': settings.exterior_finish.name,
    '#exterior-hex': settings.exterior_finish.hex, '#inner-leaf-finish': settings.inner_leaf_finish,
    '#inner-brick-hex': settings.inner_brick_hex, '#project-name': settings.project_name, '#length': settings.length_mm / 1000, '#width': settings.width_mm / 1000, '#height': settings.height_mm / 1000, '#thickness': settings.wall_thickness_mm / 1000, '#paint-colour': settings.paint_colour, '#paint-hex': settings.paint_hex, '#paint-coats': settings.paint_coats, '#currency': settings.currency, '#rate-source': settings.rate_source, '#rate-date': settings.rate_date, '#builder-name': settings.builder_pricing.builder_name, '#quote-markup': settings.builder_pricing.overhead_margin_percent, '#quote-admin': settings.builder_pricing.admin_fee_aud, '#quote-credit-treatment': settings.builder_pricing.credit_treatment, '#quote-validity': settings.builder_pricing.validity_days, '#client-description': settings.builder_pricing.client_description };
  for (const [id, value] of Object.entries(map)) if (value !== undefined && value !== null) $(id).value = value;
  for (const [code, id] of Object.entries(overrideFields)) $(id).value = Object.hasOwn(settings.rate_overrides, code) ? settings.rate_overrides[code] : '';
  for (const [key, id] of Object.entries(wastageFields)) $(id).value = settings.wastage[key];
  renderAllowances(); updateOverrideHints();
  updateSwatches();
}

function renderAllowances() {
  const list = $('#quote-allowances'); list.replaceChildren();
  $('#allowances-empty').hidden = draftAllowances.length > 0;
  $('#add-pc-allowance').disabled = state.busy || draftAllowances.length >= 100;
  $('#add-ps-allowance').disabled = state.busy || draftAllowances.length >= 100;
  for (const item of draftAllowances) {
    const card = document.createElement('div'); card.className = 'allowance-editor'; card.dataset.allowanceId = item.id;
    const title = document.createElement('strong'); title.textContent = item.id; card.append(title);
    const fields = [ ['kind', 'Allowance type', 'select'], ['description', 'Description', 'textarea'], ['amount', 'Allowance amount (AUD)', 'number'], ['note', 'Allowance note (client visible)', 'textarea'] ];
    for (const [key, labelText, type] of fields) {
      const label = document.createElement('label'); label.className = 'field'; label.textContent = labelText;
      const input = document.createElement(type === 'select' ? 'select' : type === 'textarea' ? 'textarea' : 'input');
      input.dataset.allowanceField = key; input.setAttribute('aria-label', `${item.id} ${labelText}`);
      if (type === 'select') for (const [value, text] of [['prime_cost', 'Prime cost (PC)'], ['provisional_sum', 'Provisional sum (PS)']]) { const option = document.createElement('option'); option.value = value; option.textContent = text; input.append(option); }
      if (type === 'number') { input.type = 'number'; input.min = '0'; input.max = '10000000'; input.step = '0.01'; }
      if (type === 'textarea') { input.rows = 2; input.maxLength = key === 'description' ? 240 : 1000; }
      input.required = key !== 'note'; input.value = item[key] ?? ''; input.disabled = state.busy;
      input.addEventListener('input', () => { item[key] = type === 'number' ? Number(input.value) : input.value; setDirty(); });
      label.append(input); card.append(label);
    }
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button allowance-remove'; remove.textContent = 'Remove ' + item.id; remove.disabled = state.busy;
    remove.addEventListener('click', () => removeAllowance(item.id)); card.append(remove); list.append(card);
  }
}

function addAllowance(kind) {
  if (state.busy || draftAllowances.length >= 100) return;
  const ids = [...draftAllowances.map(item => item.id), ...reservedAllowanceIds, ...comparison.revisions.flatMap(record => (record.settings?.allowances || []).map(item => item.id))];
  const next = ids.reduce((maximum, id) => /^AL[1-9]\d{0,5}$/.test(id) ? Math.max(maximum, Number(id.slice(2))) : maximum, 0) + 1;
  if (next > 999999) { errorMessage('No allowance IDs remain.'); return; }
  const item = { id: 'AL' + next, kind, description: kind === 'prime_cost' ? 'Prime cost allowance' : 'Provisional sum allowance', amount: 0, note: '' };
  reservedAllowanceIds.add(item.id); draftAllowances.push(item); renderAllowances(); setDirty();
}

function removeAllowance(id) {
  if (state.busy) return;
  draftAllowances = draftAllowances.filter(item => item.id !== id); renderAllowances(); setDirty();
}

for (const entry of document.querySelectorAll('#brick-catalogue-products tr')) {
  const option = document.createElement('option');
  option.value = entry.querySelector('a').textContent.trim();
  option.label = `${entry.cells[0].textContent} · ${entry.cells[2].textContent}`;
  $('#brick-product-names').append(option);
}

function swatchInputs(button) {
  return button.closest('[data-side]').dataset.side === 'outside'
    ? [$('#exterior-hex'), $('#exterior-name')] : [$('#paint-hex'), $('#paint-colour')];
}

function updateSwatches() {
  const facebrick = $('#exterior-kind').value === 'facebrick';
  $('#brick-catalogue-note').hidden = !facebrick;
  if (facebrick) $('#exterior-name').setAttribute('list', 'brick-product-names');
  else $('#exterior-name').removeAttribute('list');
  const bare = $('#inner-leaf-finish').value === 'bare_brick';
  const hasInternalPaint = !bare || draftInterior.partitions.length > 0;
  const hasPaint = hasInternalPaint || $('#exterior-kind').value === 'rendered_paint';
  $('#bare-brick-controls').hidden = !bare;
  $('#interior-paint-controls').hidden = !hasInternalPaint;
  $('#paint-colour').required = hasInternalPaint;
  $('#paint-coats-field').hidden = $('#paint-coats-note').hidden = !hasPaint;
  document.querySelectorAll('.paint-swatch').forEach(button => {
    const [input] = swatchInputs(button);
    const selected = button.dataset.colour.toLowerCase() === input.value.toLowerCase();
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}

function setDirty(syncPlan = true) {
  const settings = settingsFromForm();
  if (syncPlan) planEditor?.setDraft(settings);
  previewWallFinishes(settings);
  state.dirty = !state.result || libraryDiffersFromRevision() || stableSettings(settings) !== stableSettings(comparableSettings(state.result.settings));
  $('#sync-status').textContent = state.dirty ? 'Draft changes · update to save' : 'Model & documents in sync';
  $('#sync-status').classList.toggle('pending', state.dirty);
  $('#form-status').textContent = libraryDiffersFromRevision() ? 'Rate library changed. Update to save a newly priced revision; earlier outputs stay unchanged.' : state.dirty ? 'Review the draft, then update the house and documents to save this revision.' : 'Saved locally. All outputs share this revision.';
  setDownloadLinks();
  $('#comparison-draft-note').hidden = !state.dirty; setComparisonDownloads();
}

function setBusy(value) {
  state.busy = value;
  form.querySelectorAll('input, select, textarea, button').forEach(element => { element.disabled = value; });
  $('#load-settings').disabled = value;
  planEditor?.setBusy(value);
  document.body.classList.toggle('busy', value);
  $('#build-button').disabled = value;
  $('#add-pc-allowance').disabled = value || draftAllowances.length >= 100; $('#add-ps-allowance').disabled = value || draftAllowances.length >= 100;
  $('#build-label').textContent = value ? 'Updating house & documents…' : 'Update house & documents';
  $('#house-form').setAttribute('aria-busy', String(value));
  if (value) {
    $('#form-status').textContent = 'Building geometry and generating your document set…';
    $('#sync-status').textContent = 'Updating all outputs';
    $('#sync-status').classList.add('pending');
  }
  setDownloadLinks();
  setComparisonDownloads();
  updateOverrideHints();
  renderRates();
}

function errorMessage(message) {
  const element = $('#error-message');
  element.textContent = message || '';
  element.hidden = !message;
}

function downloadURL(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin ? url.href : null;
  } catch { return null; }
}

function setDownloadLinks() {
  document.querySelectorAll('[data-file]').forEach(link => {
    const url = downloadURL(state.result?.files?.[link.dataset.file]);
    const enabled = Boolean(url && !state.dirty && !state.busy);
    link.classList.toggle('disabled', !enabled);
    link.setAttribute('aria-disabled', String(!enabled));
    if (enabled) { link.href = url; link.setAttribute('download', ''); link.tabIndex = 0; }
    else { link.removeAttribute('href'); link.removeAttribute('download'); link.tabIndex = -1; }
  });
  $('#save-settings').disabled = !state.result || state.dirty || state.busy;
  setPurchaseOrderLinks();
}

function resultReceived(result, populate = false) {
  if (!result || !result.settings || !result.revision) throw new Error('The server returned an incomplete house revision.');
  const previousRevision = state.result?.revision;
  state.result = result;
  state.dirty = false;
  if (populate) fillForm(result.settings);
  if (populate) planEditor?.setDraft(settingsFromForm(), { resetHistory: true });
  planEditor?.setBaseline(comparableSettings(result.settings), result.revision);
  $('#project-title').textContent = result.settings.project_name;
  $('#revision-label').textContent = `Revision ${result.revision}`;
  $('#sync-status').textContent = 'Model & documents in sync';
  $('#sync-status').classList.remove('pending');
  $('#form-status').textContent = 'Saved locally. All outputs share this revision.';
  $('#download-revision').textContent = `Revision ${result.revision} · generated together`;
  $('#model-dimensions').textContent = `${number(result.settings.length_mm / 1000, 1)} × ${number(result.settings.width_mm / 1000, 1)} m clear`;
  const quantities = result.quantities || {};
  metric('#metric-floor', quantities.floor_finish_area_m2 ?? quantities.floor_area_m2, 'm²');
  metric('#metric-paint', quantities.paint_area_m2, 'm²');
  $('#metric-openings').textContent = `${quantities.door_count ?? 1} door · ${quantities.window_count ?? 2} windows`;
  const currency = result.totals?.currency || result.settings.currency;
  const total = result.totals?.subtotal ?? result.totals?.total ?? 0;
  $('#metric-cost').textContent = money(total, currency);
  $('#boq-total').textContent = money(total, currency);
  renderBOQ(result);
  renderPurchasing(result);
  renderSpec(result);
  renderRates();
  updateOverrideHints();
  const drawing = downloadURL(result.files?.drawings_svg);
  if (drawing) $('#drawing-preview').src = `${drawing}${drawing.includes('?') ? '&' : '?'}revision=${encodeURIComponent(result.revision)}`;
  renderModel(result.mesh);
  setDownloadLinks();
  $('#comparison-draft-note').hidden = true;
  if (libraryDiffersFromRevision()) setDirty(false);
  void refreshComparisons(result, previousRevision, !populate);
  void refreshRateLibrary();
}

function setPurchaseOrderLinks() {
  const result = state.result;
  const rows = Array.isArray(result?.boq) ? result.boq : [];
  const ready = Boolean(result?.revision && rows.length && !state.dirty && !state.busy);
  document.querySelectorAll('[data-purchase-order-code]').forEach(link => {
    const code = link.dataset.purchaseOrderCode;
    const hasItem = code === 'all' || rows.some(row => row.code === code);
    const enabled = Boolean(ready && hasItem && link.dataset.sourceRevision === result.revision);
    link.classList.toggle('disabled', !enabled);
    link.setAttribute('aria-disabled', String(!enabled));
    if (enabled) {
      link.href = `/purchase-orders/${encodeURIComponent(result.revision)}/${code === 'all' ? 'all.zip' : encodeURIComponent(code) + '.pdf'}`;
      link.setAttribute('download', ''); link.tabIndex = 0;
    } else {
      link.removeAttribute('href'); link.removeAttribute('download'); link.tabIndex = -1;
    }
  });
  $('#purchasing-status').textContent = !result?.revision || !rows.length
    ? 'Generate a saved BOQ to prepare draft purchase orders.'
    : state.busy ? 'Updating house and documents. Purchase order downloads will be available when the revision is saved.'
    : state.dirty ? 'Pending edits: update house & documents before downloading purchase orders.'
    : `Draft purchase orders use saved revision ${result.revision}. Complete the PDF fields and review before issue.`;
}

function renderPurchasing(result) {
  const rows = Array.isArray(result?.boq) ? result.boq : [];
  const currency = result?.totals?.currency || result?.settings?.currency || '';
  const recordedNumber = (value, digits) => value === undefined || value === null ? 'Not recorded' : Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
  const recordedMoney = value => value === undefined || value === null ? 'Not recorded' : currency ? money(value, currency) : `${number(value)} (currency not recorded)`;
  const recordedRate = value => value === undefined || value === null ? 'Not recorded' : `${currency || '(currency not recorded)'} ${recordedNumber(value, 20)}`;
  const body = $('#purchasing-body'); body.replaceChildren();
  $('#purchasing-revision').textContent = result?.revision ? `Source revision ${result.revision}` : 'No saved revision';
  $('#purchasing-count').textContent = `${rows.length} draft purchase order${rows.length === 1 ? '' : 's'}`;
  $('#purchasing-empty').hidden = Boolean(rows.length);
  $('#purchasing-total').textContent = rows.length ? recordedMoney(result?.totals?.subtotal) : '\u2014';
  $('#purchase-orders-all').dataset.sourceRevision = result?.revision || '';
  for (const row of rows) {
    const tr = document.createElement('tr');
    const item = document.createElement('td'); const code = document.createElement('strong'); code.textContent = row.code || 'Item code not recorded'; item.append(code);
    const description = document.createElement('p'); description.textContent = row.description || 'Description not recorded'; item.append(description);
    if (row.group_name) { const scope = document.createElement('small'); scope.textContent = 'Scope: ' + row.group_name; item.append(scope); }
    if (row.rate_origin || row.rate_source || row.rate_effective_date) {
      const basis = document.createElement('small'); basis.textContent = [row.rate_origin, row.rate_source, row.rate_effective_date].filter(Boolean).join(' | '); item.append(basis);
    }
    tr.append(item);
    for (const value of [recordedNumber(row.gross_quantity ?? row.quantity, 6), row.unit || 'Not recorded', recordedRate(row.rate), recordedMoney(row.amount)]) {
      const cell = document.createElement('td'); cell.textContent = value; tr.append(cell);
    }
    const action = document.createElement('td');
    if (typeof row.code === 'string' && row.code) {
      const link = document.createElement('a'); link.className = 'download-link disabled'; link.textContent = 'Download draft PO PDF';
      link.dataset.purchaseOrderCode = row.code; link.dataset.sourceRevision = result?.revision || '';
      link.setAttribute('aria-label', `Download ${row.code} draft purchase order PDF`);
      link.setAttribute('aria-describedby', 'purchasing-status');
      link.addEventListener('click', event => { if (link.getAttribute('aria-disabled') === 'true') event.preventDefault(); });
      action.append(link);
    }
    tr.append(action); body.append(tr);
  }
  setPurchaseOrderLinks();
}

function metric(selector, value, unit) {
  const element = $(selector);
  element.replaceChildren(document.createTextNode(`${number(value)} `));
  const small = document.createElement('small');
  small.textContent = unit;
  element.append(small);
}

function libraryDiffersFromRevision() {
  return Boolean(rateLibraryState.current?.sha256 && state.result?.rate_library?.sha256 && rateLibraryState.current.sha256 !== state.result.rate_library.sha256);
}

function updateOverrideHints() {
  const library = rateLibraryState.current || state.result?.rate_library;
  const label = rateLibraryState.current ? 'Current library' : 'Saved library';
  for (const [code, id] of Object.entries(overrideFields)) {
    const input = $(id); const rate = library?.rows?.[code];
    const override = input.value.trim() !== '';
    input.placeholder = rate ? `${number(rate.rate)} ${rate.currency}/${rate.unit}` : 'Use library';
    $('#library-rate-' + code.toLowerCase().replaceAll('_', '-')).textContent = rate ? `${override ? 'Override entered. ' : ''}${label}: ${number(rate.rate)} ${rate.currency}/${rate.unit}` : `${override ? 'Override entered. ' : ''}Library not loaded.`;
    $('#reset-' + code.toLowerCase().replaceAll('_', '-')).disabled = state.busy || !override;
  }
  syncRatePanelOverrides();
}

function syncRatePanelOverrides() {
  for (const [code, id] of Object.entries(overrideFields)) {
    const key = code.toLowerCase().replaceAll('_', '-'); const input = $('#rates-override-' + key);
    if (!input) continue;
    const value = $(id).value;
    if (input.value !== value) input.value = value;
    input.disabled = state.busy;
    const currency = $('#rates-override-currency-' + key);
    if (currency) currency.textContent = `${$('#currency').value}/${RATE_ITEMS.find(([itemCode]) => itemCode === code)[2]}`;
    const button = $('#rates-reset-' + key);
    if (button) button.disabled = state.busy || value.trim() === '';
  }
}

function setRateOverrideValue(code, value) {
  if (state.busy || !Object.hasOwn(overrideFields, code)) return;
  $(overrideFields[code]).value = value;
  updateOverrideHints();
  setDirty();
}

function resetRateOverride(code) {
  setRateOverrideValue(code, '');
}

function renderRates() {
  const library = rateLibraryState.current;
  $('#rate-library-hash').textContent = library ? `${rateLibraryState.error ? 'Last successful read' : 'Current server library'}: ${library.sha256} (${library.currency})` : 'Current server library has not been loaded.';
  $('#rate-library-saved').textContent = state.result?.rate_library?.sha256 ? `Saved revision ${state.result.revision} used library ${state.result.rate_library.sha256} (${state.result.rate_library.currency}). Its BOQ retains the captured rates.` : 'This saved revision has no captured rate library. Its recorded BOQ rates remain unchanged.';
  $('#rate-library-difference').textContent = libraryDiffersFromRevision() ? 'The current library differs from the saved revision. Update house & documents to apply current library rates to items without overrides and save a new revision.' : 'Library values are read-only. Design override fields below edit the pending design; Update house & documents saves their effect.';
  $('#rate-library-error').hidden = !rateLibraryState.error;
  $('#rate-library-error').textContent = rateLibraryState.error;
  $('#refresh-rate-library').disabled = rateLibraryState.busy || state.busy;
  const body = $('#rate-library-body'); body.replaceChildren();
  for (const row of Object.values(library?.rows || {})) {
    const tr = document.createElement('tr');
    for (const value of [row.code, row.description, row.unit, number(row.rate), row.currency, row.source, row.effective_date, row.notes]) {
      const cell = document.createElement('td'); cell.textContent = String(value ?? ''); tr.append(cell);
    }
    if (Object.hasOwn(overrideFields, row.code)) {
      const key = row.code.toLowerCase().replaceAll('_', '-');
      const overrideCell = document.createElement('td'); overrideCell.className = 'library-override-cell';
      const input = document.createElement('input'); input.id = 'rates-override-' + key; input.type = 'number'; input.min = '0'; input.max = '1000000'; input.step = 'any'; input.placeholder = 'Use library'; input.value = $(overrideFields[row.code]).value;
      input.setAttribute('aria-label', row.code + ' design override rate'); input.addEventListener('input', () => setRateOverrideValue(row.code, input.value));
      const currency = document.createElement('span'); currency.id = 'rates-override-currency-' + key; currency.className = 'library-override-currency';
      overrideCell.append(input, currency); tr.append(overrideCell);
      const resetCell = document.createElement('td');
      const button = document.createElement('button'); button.id = 'rates-reset-' + key; button.type = 'button'; button.className = 'reset-rate'; button.textContent = 'Use library'; button.setAttribute('aria-label', 'Reset ' + row.code + ' to library'); button.addEventListener('click', () => resetRateOverride(row.code)); resetCell.append(button); tr.append(resetCell);
    }
    body.append(tr);
  }
  syncRatePanelOverrides();
}

async function refreshRateLibrary() {
  const request = ++rateLibraryState.request;
  rateLibraryState.busy = true; renderRates();
  try {
    const library = await fetchJSON('/api/rates');
    if (!library || typeof library.sha256 !== 'string' || !/^[0-9a-f]{64}$/i.test(library.sha256) || !library.rows || typeof library.rows !== 'object' || Array.isArray(library.rows)) throw new Error('The server returned an incomplete rate library.');
    if (request !== rateLibraryState.request) return;
    rateLibraryState.current = library; rateLibraryState.error = '';
  } catch (error) {
    if (request !== rateLibraryState.request) return;
    rateLibraryState.error = `Rate library could not be refreshed: ${error.message}`;
  } finally {
    if (request === rateLibraryState.request) {
      rateLibraryState.busy = false; renderRates(); updateOverrideHints();
      if (state.result) setDirty(false);
    }
  }
}

function rateBasis(row, result) {
  const origin = row.rate_origin || (Object.hasOwn(comparableSettings(result.settings).rate_overrides, row.rate_code ?? row.code) ? 'override' : result.rate_library ? 'library' : 'legacy');
  return [origin === 'override' ? 'Override' : origin === 'library' ? 'Library' : 'Saved legacy rate', row.rate_source, row.rate_effective_date].filter(Boolean).join(' | ');
}

function renderBOQ(result) {
  const body = $('#boq-body');
  body.replaceChildren();
  const line = row => {
    const tr = document.createElement('tr');
    const values = [row.code, row.description, number(row.net_quantity ?? row.quantity, 3), `${number(row.wastage_percent ?? 0)}%`, number(row.gross_quantity ?? row.quantity, 3), row.unit, number(row.rate), rateBasis(row, result), number(row.amount)];
    for (const value of values) { const td = document.createElement('td'); td.textContent = String(value ?? ''); tr.append(td); }
    body.append(tr);
  };
  if (result.boq_groups?.length) {
    const grouped = new Map(result.boq_groups.map(group => [group.id, []]));
    for (const row of result.boq) grouped.get(row.group_id)?.push(row);
    for (const group of result.boq_groups) {
      const tr = document.createElement('tr');
      const title = document.createElement('th');
      title.colSpan = 9; title.scope = 'rowgroup'; title.textContent = group.name;
      tr.append(title); body.append(tr);
      for (const row of grouped.get(group.id) ?? []) line(row);
      const subtotal = document.createElement('tr');
      const label = document.createElement('th'); label.colSpan = 8; label.textContent = group.name + ' subtotal';
      const value = document.createElement('td'); value.textContent = money(group.subtotal, result.settings.currency);
      subtotal.append(label, value); body.append(subtotal);
    }
  } else {
    for (const row of result.boq || []) line(row);
  }
  $('#measurement-note').textContent = [result.measurement_rule || result.measurement_rules || 'Quantities use compiled geometry. Paint net quantity is measured area multiplied by coats. Gross quantity includes item wastage and is the priced quantity.', 'Rate basis identifies each saved item as library or design override, with its recorded source and effective date. The subtotal covers only modelled scope.'].join(' ');
}

function renderSpec(result) {
  const settings = normalizeBoqSettings(result.settings);
  const drawing = result.drawing || {};
  $('#spec-title').textContent = settings.project_name;
  $('#spec-revision').textContent = `Design revision ${result.revision} · dimensions in millimetres`;
  const container = $('#spec-content');
  container.replaceChildren();
  const section = (title, paragraphs) => {
    const part = document.createElement('section'); part.className = 'spec-section';
    const heading = document.createElement('h4'); heading.textContent = title; part.append(heading);
    paragraphs.forEach(text => { const p = document.createElement('p'); p.textContent = text; part.append(p); });
    container.append(part);
    return part;
  };
  section('01 · Scope & setting out', [`Single-storey rectangular house with clear internal dimensions of ${number(settings.length_mm, 0)} × ${number(settings.width_mm, 0)} mm. Wall height ${number(settings.height_mm, 0)} mm; wall thickness ${number(settings.wall_thickness_mm, 0)} mm.`, 'Refer to the coordinated floor plan and elevations for element locations and overall dimensions.']);
  const outside = settings.exterior_finish, bare = settings.inner_leaf_finish === 'bare_brick';
  const finish = section('02 · Wall finishes', [
    `External brick leaf · ${outside.kind === 'facebrick' ? 'Exposed facebrick' : 'Render and paint'} · ${outside.name} (${outside.hex}).`,
    bare ? `Internal brick leaf · Bare common brick (${settings.inner_brick_hex}), ready for finishes.` : `Internal brick leaf · Plaster and paint · ${settings.paint_colour} (${settings.paint_hex}).`,
  ]);
  const colours = [outside.hex, bare ? settings.inner_brick_hex : settings.paint_hex];
  finish.querySelectorAll('p').forEach((p, i) => {
    const swatch = document.createElement('span'); swatch.className = 'spec-colour'; swatch.style.backgroundColor = colours[i]; p.prepend(swatch);
  });
  const paint = result.partitions?.length ? null : result.boq?.find(row => row.code === 'PAINT');
  if (paint?.net_quantity !== undefined) { const paragraph = document.createElement('p'); paragraph.textContent = `${number(paint.measured_quantity, 3)} m2 measured net wall area x ${settings.paint_coats} coats = ${number(paint.net_quantity, 3)} m2 before ${number(paint.wastage_percent)}% wastage; priced gross quantity ${number(paint.gross_quantity, 3)} m2.`; finish.append(paragraph); }
  const openingDescriptions = (drawing.openings || []).map(opening => `${opening.id} on ${opening.host}: ${number(opening.width_mm, 0)} mm wide × ${number(opening.height_mm, 0)} mm high; start ${number(opening.start_mm, 0)} mm from the host inner reference${opening.sill_mm ? `; sill ${number(opening.sill_mm, 0)} mm` : ''}.`);
  section('03 · Openings', openingDescriptions.length ? openingDescriptions : ['No doors or windows are included in this saved revision.']);
  const trades = new Set(['FLOOR_FINISH', 'CEILING', 'SKIRTING', 'INTERNAL_PLASTER', 'EXTERNAL_RENDER']);
  if (result.rooms?.length) {
    section('Room finishes schedule', result.rooms.map(room =>
      `${room.name} (${room.id}) · ${number(room.area_m2)} m². ` +
      Object.entries(room.finishes).filter(([trade]) => trade !== 'paint' || !bare || room.paint_area_m2 > 0)
        .map(([trade, value]) => `${trade === 'paint' && bare ? 'partition paint' : trade}: ${value}`).join(' · ')));
  }
  for (const row of result.boq || []) if (trades.has(row.rate_code || row.code)) {
    const trade = (row.rate_code || row.code).replaceAll('_', ' ').toLowerCase();
    section(row.group_name ? row.group_name + ' · ' + trade : trade, [row.description, `Net ${number(row.net_quantity, 3)} ${row.unit}; wastage ${number(row.wastage_percent)}%; gross ${number(row.gross_quantity, 3)} ${row.unit}.`]);
  }
  section('04 · Quantities & pricing', [result.measurement_rule || 'Quantities are derived from the compiled house geometry. Paint excludes openings and reveals.', `BOQ values use saved unit rates in ${settings.currency}, with each item recording its rate source and effective date. The subtotal covers only modelled scope. All issued files should retain design revision ${result.revision}.`]);
}

function signedMoney(value, currency) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'Not separated';
  const amount = Number(value);
  return `${amount > 0 ? '+' : amount < 0 ? '\u2212' : ''}${money(Math.abs(amount), currency)}`;
}

function deltaClass(value) {
  return Number(value) > 0 ? 'delta-positive' : Number(value) < 0 ? 'delta-negative' : 'delta-zero';
}

function comparisonPair() {
  return { before: $('#compare-before').value, after: $('#compare-after').value };
}

function rememberComparison() {
  try { localStorage.setItem('house-sync-comparison', JSON.stringify(comparisonPair())); } catch { /* Storage is optional. */ }
}

function savedComparisonPair() {
  try { return JSON.parse(localStorage.getItem('house-sync-comparison')) || {}; } catch { return {}; }
}

function comparisonError(message) {
  $('#comparison-error').textContent = message || '';
  $('#comparison-error').hidden = !message;
}

function clientQuoteMatches(result, pair = comparisonPair()) {
  const quote = result?.quote?.public;
  if (!quote || quote.currency !== 'AUD' || result.currency !== 'AUD' || result.before_revision !== pair.before || result.after_revision !== pair.after || quote.before_revision !== pair.before || quote.after_revision !== pair.after) return false;
  const url = downloadURL(result.files?.client_quote_pdf);
  if (!url || typeof result.comparison_id !== 'string' || !result.comparison_id.startsWith(pair.before + '_' + pair.after + '_')) return false;
  return new URL(url).pathname === '/changes/' + result.comparison_id + '/client-variation-quote.pdf';
}

function renderClientQuote(result) {
  const quote = result?.quote?.public; const section = $('#client-quote-summary');
  const valid = quote && quote.currency === 'AUD' && result.currency === 'AUD' && quote.before_revision === result.before_revision && quote.after_revision === result.after_revision;
  section.hidden = !valid;
  if (!valid) return;
  const totals = quote.totals || {}; const currency = quote.currency;
  $('#quote-saved-revisions').textContent = `Saved A ${quote.before_revision} to B ${quote.after_revision}; pricing is from saved revision B.`;
  $('#quote-reference').textContent = quote.quote_reference || 'Draft variation quote';
  $('#quote-builder').textContent = quote.builder_name || 'Builder name not supplied';
  const description = (quote.client_description || '').trim();
  $('#quote-description').textContent = description; $('#quote-description').hidden = !description;
  $('#quote-credit-policy').textContent = quote.credit_policy || '';
  $('#quote-dates').textContent = `Issued ${quote.issued_on || 'not set'}; valid until ${quote.valid_until || 'not set'}.`;
  const scope = $('#quote-scope'); scope.replaceChildren();
  const scopeLines = [...new Set((quote.scope || []).map(text => String(text).trim()).filter(text => text && text !== description))]; scope.hidden = scopeLines.length === 0;
  for (const text of scopeLines) { const item = document.createElement('li'); item.textContent = text; scope.append(item); }
  const waterfall = $('#quote-waterfall'); waterfall.replaceChildren();
  for (const [key, label] of [
    ['additions_at_cost', 'Changes at cost - additions'], ['credits_at_cost', 'Changes at cost - credits'],
    ['at_cost_delta', 'Net changes at cost'], ['additions_markup', 'Overhead and margin on additions'],
    ['credits_markup', 'Overhead and margin credit'], ['markup_amount', (quote.markup_label || 'Combined overhead and margin (markup on cost)') + (quote.overhead_margin_percent === undefined ? '' : ' (' + quote.overhead_margin_percent + '%)')],
    ['admin_fee', 'Administration fee'], ['subtotal_ex_gst', 'Quote subtotal excluding GST'],
    ['gst_amount', 'GST (' + (totals.gst_rate ?? 10) + '%)'], ['total_incl_gst', 'Client quote total including GST'],
  ]) {
    const term = document.createElement('dt'); term.textContent = label;
    const value = document.createElement('dd');
    const deduction = key === 'credits_at_cost' || key === 'credits_markup';
    const amount = deduction && totals[key] > 0 ? -totals[key] : totals[key];
    value.textContent = amount === undefined ? 'Not recorded' : money(amount, currency);
    if (key === 'total_incl_gst') { term.className = 'quote-total'; value.className = 'quote-total'; }
    waterfall.append(term, value);
  }
  const allowanceRows = [...(quote.pc_allowances || []).map(row => ({ ...row, label: 'PC' })), ...(quote.ps_allowances || []).map(row => ({ ...row, label: 'PS' }))];
  const lines = $('#quote-allowance-lines'); lines.replaceChildren(); $('#quote-no-allowances').hidden = allowanceRows.length > 0;
  for (const row of allowanceRows) {
    const line = document.createElement('tr');
    for (const value of [`${row.label} / ${row.id}`, row.description, money(row.before, currency), money(row.after, currency), signedMoney(row.movement, currency), row.notes || '']) { const cell = document.createElement('td'); cell.textContent = value; line.append(cell); }
    lines.append(line);
  }
}

function setComparisonDownloads() {
  const pair = comparisonPair();
  const matches = comparison.result && comparison.result.before_revision === pair.before && comparison.result.after_revision === pair.after;
  document.querySelectorAll('[data-change-file]').forEach(link => {
    const url = downloadURL(comparison.result?.files?.[link.dataset.changeFile]);
    const isClientQuote = link.dataset.clientQuote === 'true';
    const enabled = Boolean(matches && !comparison.busy && !state.busy && url && (!isClientQuote || (!state.dirty && clientQuoteMatches(comparison.result, pair))));
    link.classList.toggle('disabled', !enabled);
    link.setAttribute('aria-disabled', String(!enabled));
    if (enabled) { link.href = url; link.setAttribute('download', ''); link.tabIndex = 0; }
    else { link.removeAttribute('href'); link.removeAttribute('download'); link.tabIndex = -1; }
  });
  $('#compare-button').disabled = comparison.busy || !pair.before || !pair.after;
  $('#compare-button').textContent = comparison.busy ? 'Comparing...' : 'Compare revisions';
  $('#client-quote-export-note').textContent = state.dirty ? 'Pending edits must be saved as a new revision before exporting the client quote. The summary still uses the selected saved A and B.' : !matches ? 'Compare the selected saved revisions to prepare the client quote.' : clientQuoteMatches(comparison.result, pair) ? 'Client PDF uses only the selected saved revisions and the client quote summary; internal itemised records are separate.' : 'Client quote PDF is unavailable for this comparison. Client quotes require saved AUD revisions and a matching client export.';
}

function selectedComparisonChanged() {
  comparison.selection += 1;
  comparison.request += 1;
  comparison.busy = false;
  comparison.result = null;
  $('#comparison-result').hidden = true; $('#client-quote-summary').hidden = true;
  $('#change-ready-dot').hidden = true;
  $('#comparison-status').textContent = 'Select Compare revisions to capture this A to B change.';
  comparisonError('');
  rememberComparison();
  setComparisonDownloads();
}

function revisionLabel(record) {
  const settings = record.settings || {};
  const dimensions = settings.length_mm && settings.width_mm ? `${number(settings.length_mm / 1000, 1)} x ${number(settings.width_mm / 1000, 1)} m` : '';
  const date = record.created_at ? new Date(record.created_at) : null;
  const time = date && !Number.isNaN(date.getTime()) ? date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
  return [record.revision, dimensions, settings.paint_colour, time].filter(Boolean).join(' | ');
}

async function refreshComparisons(result, previousRevision, fromBuild = false) {
  const refresh = ++comparison.refresh;
  const selection = comparison.selection;
  comparison.request += 1;
  comparison.busy = false;
  const previousPair = comparisonPair();
  try {
    const revisions = await fetchJSON('/api/revisions');
    if (refresh !== comparison.refresh || state.result?.revision !== result.revision) return;
    comparison.revisions = Array.isArray(revisions) ? revisions : revisions.revisions || [];
    const valid = new Set(comparison.revisions.map(record => record.revision));
    const preferredPair = selection === comparison.selection ? previousPair : comparisonPair();
    for (const id of ['#compare-before', '#compare-after']) {
      const select = $(id); select.replaceChildren();
      for (const record of comparison.revisions) { const option = document.createElement('option'); option.value = record.revision; option.textContent = revisionLabel(record); select.append(option); }
      if (!select.options.length) { const option = document.createElement('option'); option.value = ''; option.textContent = 'No saved revision yet'; select.append(option); }
    }
    const remembered = savedComparisonPair();
    const autoBefore = result.previous_revision || previousRevision;
    const lastChange = result.last_change;
    const automatic = fromBuild && selection === comparison.selection && autoBefore && valid.has(autoBefore) && autoBefore !== result.revision;
    let before, after;
    if (automatic) { before = autoBefore; after = result.revision; }
    else if (valid.has(preferredPair.before) && valid.has(preferredPair.after)) { before = preferredPair.before; after = preferredPair.after; }
    else if (valid.has(remembered.before) && valid.has(remembered.after)) { before = remembered.before; after = remembered.after; }
    else if (lastChange && valid.has(lastChange.before_revision) && valid.has(lastChange.after_revision)) { before = lastChange.before_revision; after = lastChange.after_revision; }
    else {
      after = valid.has(result.revision) ? result.revision : comparison.revisions[0]?.revision;
      const candidates = comparison.revisions.filter(record => record.revision !== after && record.settings?.project_name === result.settings.project_name && record.settings?.currency === result.settings.currency);
      before = candidates[0]?.revision || comparison.revisions.find(record => record.revision !== after)?.revision || after;
    }
    $('#compare-before').value = before || '';
    $('#compare-after').value = after || '';
    rememberComparison();
    comparisonError('');
    setComparisonDownloads();
    if (lastChange && lastChange.before_revision === before && lastChange.after_revision === after) renderComparison(lastChange);
    else if (before && after) await compareSavedRevisions();
    else $('#comparison-status').textContent = 'Update the house to save a revision, then compare its changes here.';
  } catch (error) {
    if (refresh !== comparison.refresh) return;
    comparisonError(`Saved revisions could not be loaded: ${error.message}`);
    setComparisonDownloads();
  }
}

async function compareSavedRevisions() {
  const pair = comparisonPair();
  if (!pair.before || !pair.after) return;
  const request = ++comparison.request;
  comparison.busy = true;
  $('#comparison-result').hidden = true; $('#client-quote-summary').hidden = true;
  $('#change-ready-dot').hidden = true;
  comparisonError('');
  $('#comparison-status').textContent = 'Comparing descriptions, quantities and unit rates...';
  setComparisonDownloads();
  try {
    const result = await fetchJSON(`/api/compare?before=${encodeURIComponent(pair.before)}&after=${encodeURIComponent(pair.after)}`);
    const current = comparisonPair();
    if (request !== comparison.request || current.before !== pair.before || current.after !== pair.after) return;
    if (result.before_revision !== pair.before || result.after_revision !== pair.after) throw new Error('The comparison returned different revisions. Compare again to refresh the report.');
    renderComparison(result);
  } catch (error) {
    if (request !== comparison.request) return;
    comparison.result = null;
    $('#comparison-result').hidden = true;
    $('#change-ready-dot').hidden = true;
    $('#comparison-status').textContent = 'Comparison needs attention.';
    comparisonError(error.message);
  } finally {
    if (request === comparison.request) { comparison.busy = false; setComparisonDownloads(); }
  }
}

function renderComparison(result) {
  comparison.result = result; renderClientQuote(result);
  const totals = result.totals || {};
  const currency = result.currency || 'AUD';
  $('#comparison-result').hidden = false;
  $('#compare-cost-before').textContent = money(totals.before, currency);
  $('#compare-cost-after').textContent = money(totals.after, currency);
  $('#compare-cost-delta').textContent = signedMoney(totals.net_change, currency);
  $('#compare-cost-delta').className = deltaClass(totals.net_change);
  $('#compare-net-label').textContent = totals.net_change > 0 ? 'Net estimate increase' : totals.net_change < 0 ? 'Net estimate saving' : 'No estimate change';
  $('#compare-additions').textContent = money(totals.additions, currency);
  $('#compare-omissions').textContent = money(Math.abs(totals.omissions || 0), currency);
  $('#comparison-revisions').textContent = `A ${result.before_revision} \u2192 B ${result.after_revision}`;
  $('#comparison-status').textContent = result.has_changes ? 'Change captured. The report describes the selected saved revisions.' : 'Comparison complete. No changes between these saved revisions.';
  $('#comparison-no-changes').hidden = Boolean(result.has_changes);
  $('#change-ready-dot').hidden = !result.has_changes;
  const summaries = Array.isArray(result.library_changes) ? (result.setting_changes || []).map(item => item.description || `${item.label}: ${item.before} \u2192 ${item.after}`) : Array.isArray(result.summary) && result.summary.length ? result.summary : (result.setting_changes || []).map(item => item.description || `${item.label}: ${item.before} \u2192 ${item.after}`);
  const list = $('#comparison-summary'); list.replaceChildren();
  for (const text of summaries) { const item = document.createElement('li'); item.textContent = String(text); list.append(item); }
  const libraryChanges = Array.isArray(result.library_changes) ? result.library_changes : [];
  $('#comparison-library-section').hidden = !libraryChanges.length;
  const libraryList = $('#comparison-library-summary'); libraryList.replaceChildren();
  for (const change of libraryChanges) { const item = document.createElement('li'); item.textContent = change.description; libraryList.append(item); }
  if (libraryChanges.length && !summaries.length) { $('#comparison-no-changes').hidden = false; $('#comparison-no-changes').textContent = 'No design or override settings changed. The library changes are recorded separately below.'; }
  else $('#comparison-no-changes').textContent = 'No design, specification or BOQ changes between these revisions.';
  const body = $('#comparison-items'); body.replaceChildren();
  for (const item of result.items || []) {
    const row = document.createElement('tr');
    const description = document.createElement('td');
    const title = document.createElement('strong'); title.textContent = item.code || 'BOQ item'; description.append(title);
    const status = document.createElement('span'); status.className = 'change-item-status'; status.textContent = String(item.status || 'modified').replaceAll('_', ' '); description.append(status);
    const detail = document.createElement('p'); detail.textContent = item.description || item.after_description || item.before_description || ''; description.append(detail);
    if (item.before_description && item.after_description && item.before_description !== item.after_description) {
      const before = document.createElement('p'); before.className = 'change-description-before'; before.textContent = `A: ${item.before_description}`; description.append(before);
      const after = document.createElement('p'); after.className = 'change-description-after'; after.textContent = `B: ${item.after_description}`; description.append(after);
    }
    if (item.element_ids?.length) { const ids = document.createElement('small'); ids.textContent = `Elements: ${item.element_ids.join(', ')}`; description.append(ids); }
    if (item.before_rate_origin || item.after_rate_origin) {
      const provenance = document.createElement('small');
      provenance.textContent = `Rate A: ${item.before_rate_origin || 'legacy'} | ${item.before_rate_source || 'source not recorded'} | ${item.before_rate_effective_date || 'date not recorded'}. Rate B: ${item.after_rate_origin || 'legacy'} | ${item.after_rate_source || 'source not recorded'} | ${item.after_rate_effective_date || 'date not recorded'}.`;
      description.append(provenance);
    }
    if (item.library_rate_effect !== undefined || item.override_rate_effect !== undefined) {
      const effects = document.createElement('small'); effects.textContent = `Library rate effect ${signedMoney(item.library_rate_effect, currency)}; override rate effect ${signedMoney(item.override_rate_effect, currency)}.`; description.append(effects);
    }
    row.append(description);
    const formatQuantity = value => value === null || value === undefined ? '\u2014' : number(value, 3);
    const formatRate = value => value === null || value === undefined ? '\u2014' : number(value);
    const beforeUnit = item.before_unit ?? item.unit ?? '';
    const afterUnit = item.after_unit ?? item.unit ?? '';
    const quantityChange = beforeUnit === afterUnit ? `${formatQuantity(item.before_quantity)} \u2192 ${formatQuantity(item.after_quantity)} ${afterUnit}` : `${formatQuantity(item.before_quantity)} ${beforeUnit} \u2192 ${formatQuantity(item.after_quantity)} ${afterUnit}`;
    const values = [quantityChange, `${formatRate(item.before_rate)} \u2192 ${formatRate(item.after_rate)}`, money(item.before_amount, currency), money(item.after_amount, currency), signedMoney(item.cost_delta, currency)];
    for (const [index, value] of values.entries()) { const cell = document.createElement('td'); cell.textContent = value; if (index === 4) cell.className = deltaClass(item.cost_delta); row.append(cell); }
    body.append(row);
  }
  if (!body.children.length) { const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = 6; cell.className = 'no-boq-changes'; cell.textContent = result.has_changes ? 'No BOQ price, quantity or description changes. See the design changes above.' : 'No BOQ changes.'; row.append(cell); body.append(row); }
  $('#comparison-table-total').textContent = signedMoney(totals.net_change, currency);
  $('#comparison-table-total').className = deltaClass(totals.net_change);
  for (const [id, key] of [['#compare-quantity-effect', 'quantity_effect'], ['#compare-rate-effect', 'rate_effect'], ['#compare-rounding-effect', 'rounding_effect']]) $(id).textContent = signedMoney(totals[key], currency);
  for (const [id, key] of [['#compare-library-effect', 'library_rate_effect'], ['#compare-override-effect', 'override_rate_effect'], ['#compare-unattributed-effect', 'unattributed_rate_effect']]) $(id).textContent = signedMoney(totals[key], currency);
  const notes = $('#comparison-notes'); notes.replaceChildren();
  const noteTexts = Array.isArray(result.notes) ? result.notes : result.notes ? [result.notes] : [];
  for (const text of noteTexts) { const item = document.createElement('li'); item.textContent = String(text); notes.append(item); }
  rememberComparison();
  setComparisonDownloads();
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { throw new Error(`The server did not return a valid response (${response.status}).`); }
  if (!response.ok) {
    let message = data.error || data.detail || `Request failed (${response.status}).`;
    if (Array.isArray(message)) message = message.map(item => `${(item.loc || []).join('.')}: ${item.msg || item}`).join('; ');
    if (typeof message === 'object') message = JSON.stringify(message);
    throw new Error(message);
  }
  return data;
}

async function buildHouse(event) {
  event?.preventDefault();
  if (state.busy || !form.reportValidity()) return;
  const planIssues = planEditor?.audit() || [];
  if (planIssues.length) { errorMessage('Plan needs attention: ' + planIssues.join(' ')); showView('plan'); return; }
  errorMessage('');
  const submitted = settingsFromForm();
  setBusy(true);
  try {
    const result = await fetchJSON('/api/build', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(submitted) });
    const current = JSON.stringify(settingsFromForm());
    resultReceived(result);
    if (current !== JSON.stringify(submitted)) setDirty();
  } catch (error) {
    errorMessage(error.message);
    $('#form-status').textContent = 'The previous revision is preserved. Check the values and try again.';
    $('#sync-status').textContent = 'Update needs attention';
    $('#sync-status').classList.add('pending');
  } finally { setBusy(false); }
}

function initializeViewer() {
  const viewport = $('#viewport');
  try {
    viewer.scene = new THREE.Scene();
    viewer.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
    viewer.camera.up.set(0, 0, 1);
    viewer.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    viewer.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    viewer.renderer.outputColorSpace = THREE.SRGBColorSpace;
    viewer.renderer.shadowMap.enabled = true;
    viewer.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    viewport.append(viewer.renderer.domElement);
    viewer.scene.add(new THREE.AmbientLight(0xffffff, 1.9));
    const sun = new THREE.DirectionalLight(0xfff7e7, 3.1);
    sun.position.set(-9, -13, 19); sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.left = -40; sun.shadow.camera.right = 40; sun.shadow.camera.top = 40; sun.shadow.camera.bottom = -40;
    sun.shadow.normalBias = .025; viewer.scene.add(sun);
    const fill = new THREE.DirectionalLight(0xc8e6ee, 1.25); fill.position.set(12, 8, 10); viewer.scene.add(fill);
    const grid = new THREE.GridHelper(150, 150, 0xc5d4cc, 0xdbe5df); grid.rotation.x = Math.PI / 2; grid.position.z = -.20; grid.material.transparent = true; grid.material.opacity = .28; viewer.scene.add(grid);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(160, 160), new THREE.ShadowMaterial({ opacity: .12 })); ground.position.z = -.17; ground.receiveShadow = true; viewer.scene.add(ground);
    viewer.controls = new OrbitControls(viewer.camera, viewer.renderer.domElement);
    viewer.controls.enableDamping = true; viewer.controls.dampingFactor = .08; viewer.controls.maxPolarAngle = Math.PI / 2 - .02;
    viewer.controls.minDistance = 2; viewer.controls.maxDistance = 160;
    const resize = () => { const width = viewport.clientWidth, height = viewport.clientHeight; if (width && height) { viewer.renderer.setSize(width, height); viewer.camera.aspect = width / height; viewer.camera.updateProjectionMatrix(); } };
    new ResizeObserver(resize).observe(viewport);
    viewer.renderer.setAnimationLoop(() => { viewer.controls.update(); viewer.renderer.render(viewer.scene, viewer.camera); });
    let pointerDown = null;
    viewport.addEventListener('pointerdown', event => { pointerDown = [event.clientX, event.clientY]; });
    viewport.addEventListener('pointerup', event => {
      if (!pointerDown || Math.hypot(event.clientX - pointerDown[0], event.clientY - pointerDown[1]) > 5) return;
      const rect = viewport.getBoundingClientRect();
      const ray = new THREE.Raycaster(); ray.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), viewer.camera);
      const hit = ray.intersectObjects(viewer.parts.filter(part => part.visible), false)[0];
      if (viewer.selected) viewer.selected.material.emissive.set(0x000000);
      viewer.selected = hit?.object || null;
      viewer.selectedFace = hit?.faceIndex;
      if (viewer.selected) viewer.selected.material.emissive.set(0x143d36);
      describeSelectedElement(settingsFromForm());
    });
    resetCamera();
  } catch (error) {
    viewer.renderer = null;
    $('#viewer-message').textContent = '3D display is unavailable in this browser. You can still edit the house and use the drawings and documents.';
    console.error('House Sync viewer:', error);
  }
}

function resetCamera() {
  if (!viewer.camera) return;
  const size = viewer.size;
  viewer.camera.position.copy(viewer.target).add(new THREE.Vector3(size * .95, -size * 1.32, size * 1.08));
  viewer.controls?.target.copy(viewer.target);
  viewer.controls?.update();
}

function describeSelectedElement(settings) {
  const part = viewer.selected;
  if (!part) { $('#selected-element').textContent = 'Select an element to inspect'; return; }
  const { id, name, kind, faceSlots, partition } = part.userData;
  let label = name || kind;
  if (faceSlots) {
    const slot = faceSlots[viewer.selectedFace], outside = settings.exterior_finish;
    if (slot === 0) label = `External · ${outside?.kind === 'facebrick' ? 'Exposed facebrick' : 'Render and paint'} · ${outside?.name ?? settings.paint_colour}`;
    if (slot === 1) label = settings.inner_leaf_finish === 'bare_brick' ? 'Internal · Bare common brick' : `Internal · Plaster and paint · ${settings.paint_colour}`;
    if (slot === 2) label = 'Exposed brick core';
  } else if (partition) label = `Partition · ${settings.paint_colour}`;
  $('#selected-element').textContent = `${id} · ${label}`;
}

// Older saved meshes predate face slots. Use the saved geometry's boundary planes in metres.
function legacyWallFaceSlots(item, vertices, indices, drawing) {
  if (item.kind !== 'wall' || !HOSTS.includes(item.id) || !drawing) return null;
  const { length_mm, width_mm, wall_thickness_mm } = drawing;
  if (![length_mm, width_mm, wall_thickness_mm].every(Number.isFinite)) return null;
  const t = wall_thickness_mm / 1000, length = length_mm / 1000, width = width_mm / 1000;
  const outer = [[0, -t], [0, length + t], [1, -t], [1, width + t]];
  const inner = [[0, 0], [0, length], [1, 0], [1, width]];
  const slots = [];
  for (let start = 0; start < indices.length; start += 3) {
    const triangle = indices.slice(start, start + 3);
    const onPlane = ([axis, coordinate]) => triangle.every(index => Math.abs(vertices[3 * index + axis] - coordinate) < 1e-5);
    slots.push(outer.some(onPlane) ? 0 : inner.some(onPlane) ? 1 : 2);
  }
  return slots;
}

function colourWallFaces(attribute, slots, hexColours) {
  const palette = hexColours.map(hex => new THREE.Color(hex));
  slots.forEach((slot, face) => {
    for (let vertex = 0; vertex < 3; vertex++) palette[slot].toArray(attribute.array, (face * 3 + vertex) * 3);
  });
  attribute.needsUpdate = true;
}

function previewWallFinishes(settings) {
  if (!viewer.parts.length || !state.result) return;
  if (viewer.selected) describeSelectedElement(settings);
  const draft = wallFinishColours(settings);
  const key = JSON.stringify(draft);
  if (viewer.finishKey === key) return;
  const saved = wallFinishColours(state.result.settings);
  for (const part of viewer.parts) {
    const { faceSlots, savedColours, partition } = part.userData;
    if (faceSlots) {
      const palette = ['outside', 'inside', 'brick'].map((side, i) => draft[side] === saved[side] ? savedColours[i] : draft[side]);
      colourWallFaces(part.geometry.getAttribute('color'), faceSlots, palette);
    } else if (partition) {
      part.material.color.set(draft.paint === saved.paint ? part.userData.savedColour : draft.paint);
    }
  }
  viewer.finishKey = key;
}

function renderModel(mesh) {
  if (!viewer.renderer) return;
  const keepView = Boolean(viewer.group);
  const previousTarget = viewer.target.clone();
  const previousSize = viewer.size;
  if (viewer.group) {
    viewer.scene.remove(viewer.group);
    viewer.group.traverse(object => { object.geometry?.dispose(); if (Array.isArray(object.material)) object.material.forEach(material => material.dispose()); else object.material?.dispose(); });
  }
  viewer.group = new THREE.Group(); viewer.parts = []; viewer.selected = null; viewer.selectedFace = null;
  viewer.finishKey = JSON.stringify(wallFinishColours(state.result.settings));
  for (const item of mesh?.objects || []) {
    if (!item.vertices?.length || !item.faces?.length) continue;
    const vertices = Array.isArray(item.vertices[0]) ? item.vertices.flat() : item.vertices;
    let faces = Array.isArray(item.faces[0]) ? item.faces.flat() : item.faces;
    const isRoom = item.kind === 'space';
    if (isRoom) {
      // Pick the floor footprint without covering the walls or their openings.
      let floorZ = Infinity;
      for (let i = 2; i < vertices.length; i += 3) floorZ = Math.min(floorZ, vertices[i]);
      const floorFaces = [];
      for (let i = 0; i < faces.length; i += 3) {
        const triangle = faces.slice(i, i + 3);
        if (triangle.every(index => Math.abs(vertices[3 * index + 2] - floorZ) < 1e-6)) floorFaces.push(...triangle);
      }
      faces = floorFaces;
    }
    let geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3)); geometry.setIndex(faces);
    const faceSlots = item.face_slots ?? legacyWallFaceSlots(item, vertices, faces, state.result.drawing ?? state.result.settings);
    let savedColours;
    if (faceSlots) {
      const indexed = geometry;
      geometry = indexed.toNonIndexed(); indexed.dispose();
      savedColours = item.face_colours ?? Array(3).fill(item.colour);
      const attribute = new THREE.BufferAttribute(new Float32Array(faces.length * 3), 3);
      colourWallFaces(attribute, faceSlots, savedColours);
      geometry.setAttribute('color', attribute);
    }
    geometry.computeVertexNormals();
    const isGlass = /glaz|glass/i.test(`${item.kind} ${item.id}`);
    const material = new THREE.MeshStandardMaterial({ color: faceSlots ? '#FFFFFF' : (item.colour || '#d5ded6'), vertexColors: Boolean(faceSlots), roughness: isGlass ? .14 : .83, metalness: isGlass ? .05 : 0, side: THREE.DoubleSide, transparent: isGlass || isRoom, opacity: isRoom ? .38 : isGlass ? .48 : 1 });
    const part = new THREE.Mesh(geometry, material); part.userData = { id: item.id, kind: item.kind, name: isRoom ? (state.result?.rooms?.find(room => room.id === item.id)?.name || item.name) : null };
    if (faceSlots) Object.assign(part.userData, { faceSlots, savedColours });
    else if (item.kind === 'wall' && (item.internal || item.partition_id)) Object.assign(part.userData, { partition: true, savedColour: item.colour });
    if (isRoom) part.position.z = .003;
    part.castShadow = !isGlass && !isRoom; part.receiveShadow = true;
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 35), new THREE.LineBasicMaterial({ color: 0x485e55, transparent: true, opacity: .18 })); part.add(edges);
    viewer.parts.push(part); viewer.group.add(part);
  }
  viewer.scene.add(viewer.group);
  const bounds = new THREE.Box3().setFromObject(viewer.group);
  if (!bounds.isEmpty()) { const size = bounds.getSize(new THREE.Vector3()); viewer.target.copy(bounds.getCenter(new THREE.Vector3())); viewer.target.z = size.z * .35; viewer.size = Math.max(size.x, size.y, size.z, 5); }
  roofVisibility();
  if (!keepView) resetCamera();
  else {
    const offset = viewer.camera.position.clone().sub(previousTarget).multiplyScalar(viewer.size / previousSize);
    viewer.camera.position.copy(viewer.target).add(offset);
    viewer.controls.target.copy(viewer.target);
    viewer.controls.update();
  }
  $('#viewer-message').hidden = Boolean(viewer.parts.length);
  $('#selected-element').textContent = 'Select an element to inspect';
}

function roofVisibility() {
  for (const part of viewer.parts) if (/roof/i.test(`${part.userData.kind} ${part.userData.id}`)) part.visible = $('#roof-toggle').checked;
}

form.addEventListener('submit', buildHouse);
$('#compare-before').addEventListener('change', selectedComparisonChanged);
$('#compare-after').addEventListener('change', selectedComparisonChanged);
$('#compare-button').addEventListener('click', compareSavedRevisions);
document.querySelectorAll('[data-change-file]').forEach(link => link.addEventListener('click', event => { if (link.getAttribute('aria-disabled') === 'true') event.preventDefault(); }));
$('#add-pc-allowance').addEventListener('click', () => addAllowance('prime_cost'));
$('#add-ps-allowance').addEventListener('click', () => addAllowance('provisional_sum'));
fields.forEach(id => $(id).addEventListener('input', () => { updateSwatches(); syncRatePanelOverrides(); setDirty(); }));
Object.values(overrideFields).forEach(id => $(id).addEventListener('input', updateOverrideHints));
document.querySelectorAll('[data-reset-override]').forEach(button => button.addEventListener('click', () => resetRateOverride(button.dataset.resetOverride)));
$('#refresh-rate-library').addEventListener('click', refreshRateLibrary);
document.querySelectorAll('.paint-swatch').forEach(button => button.addEventListener('click', () => {
  const [colour, name] = swatchInputs(button); colour.value = button.dataset.colour; name.value = button.dataset.name;
  updateSwatches(); setDirty();
}));
function showView(name) {
  document.querySelectorAll('.view-tab').forEach(tab => { const active = tab.dataset.panel === name; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); });
  document.querySelectorAll('.view-panel').forEach(panel => { panel.hidden = panel.id !== 'panel-' + name; });
  if (name === 'plan') planEditor?.focus();
}
document.querySelectorAll('.view-tab').forEach(button => button.addEventListener('click', () => showView(button.dataset.panel)));

document.querySelectorAll('[data-file]').forEach(link => link.addEventListener('click', event => { if (link.getAttribute('aria-disabled') === 'true') event.preventDefault(); }));
$('#purchase-orders-all').addEventListener('click', event => { if (event.currentTarget.getAttribute('aria-disabled') === 'true') event.preventDefault(); });
$('#roof-toggle').addEventListener('change', roofVisibility);
$('#reset-camera').addEventListener('click', resetCamera);
$('#save-settings').addEventListener('click', () => {
  if (!state.result || state.dirty || state.busy) return;
  if (state.settingsURL) URL.revokeObjectURL(state.settingsURL);
  state.settingsURL = URL.createObjectURL(new Blob([JSON.stringify(state.result.settings, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = state.settingsURL; link.download = `house-sync-${state.result.revision}.json`; link.click();
});
$('#load-settings').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    if (file.size > 4194304) throw new Error('Please choose a House Sync design JSON no larger than 4 MiB.');
    const parsed = JSON.parse(await file.text()); const settings = parsed.settings || parsed;
    if (!settings.project_name || !Number.isFinite(settings.length_mm) || !Number.isFinite(settings.width_mm) || !Number.isFinite(settings.height_mm) || !Number.isFinite(settings.wall_thickness_mm) || !/^#[0-9a-f]{6}$/i.test(settings.paint_hex)) throw new Error('This file is missing valid house dimensions or a paint colour.');
    const finishes = normalizeBoqSettings(settings), outside = finishes.exterior_finish;
    if (!['facebrick', 'rendered_paint'].includes(outside?.kind) || typeof outside.name !== 'string' || !outside.name.trim() ||
        outside.name.length > 80 || !/^#[0-9a-f]{6}$/i.test(outside.hex) || !/^#[0-9a-f]{6}$/i.test(finishes.inner_brick_hex) ||
        !['bare_brick', 'plaster_paint'].includes(finishes.inner_leaf_finish)) throw new Error('Choose a valid treatment and colour for each brick leaf.');
    if (settings.allowances !== undefined && !Array.isArray(settings.allowances)) throw new Error('Allowances must be a list of PC/PS items.');
    if (settings.openings !== undefined && !Array.isArray(settings.openings)) throw new Error('Opening data must be an array of doors/windows.');
    fillForm(settings); setDirty(); errorMessage(''); $('#form-status').textContent = 'Design loaded. Review the plan and update to generate its model and documents.';
  } catch (error) { errorMessage(error.message); }
  event.target.value = '';
});

function initialFormLoading(value) {
  form.querySelectorAll('input, select, textarea, button').forEach(element => { element.disabled = value; });
  $('#load-settings').disabled = value;
}

planEditor = new PlanEditor($('#plan-editor'), {
  previewLayout: settings => fetchJSON('/api/layout', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings),
  }),
  onChange(settings) { if (state.busy) return; fillForm(settings); setDirty(false); },
  onApply() { void buildHouse(); },
  onReviewSaved() { showView('changes'); },
});
planEditor.setDraft(settingsFromForm(), { resetHistory: true });
initialFormLoading(true);
initializeViewer();
setBusy(true);
try { resultReceived(await fetchJSON('/api/state'), true); }
catch (error) { errorMessage(error.message); $('#form-status').textContent = 'Use Update house & documents to try generating your house.'; $('#sync-status').textContent = 'Ready to generate'; }
finally { initialFormLoading(false); setBusy(false); }
