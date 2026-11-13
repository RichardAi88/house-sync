/** Pure millimetre geometry, validation and undo history for the rectangular plan editor. */
export const HOSTS = Object.freeze(['W1', 'W2', 'W3', 'W4']);
export const HOST_LABELS = Object.freeze({ W1: 'south', W2: 'east', W3: 'north', W4: 'west' });
const EPSILON = 1e-6;

function copy(value) {
  if (Array.isArray(value)) return value.map(copy);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item)]));
  return value;
}

export function defaultOpenings() {
  return [
    { id: 'D1', kind: 'door', host: 'W1', offset_mm: null, width_mm: 1000, height_mm: 2100, sill_mm: 0 },
    { id: 'N1', kind: 'window', host: 'W3', offset_mm: null, width_mm: 1500, height_mm: 1200, sill_mm: 900 },
    { id: 'N2', kind: 'window', host: 'W2', offset_mm: null, width_mm: 1500, height_mm: 1200, sill_mm: 900 },
  ];
}

export function cloneSettings(settings) {
  const result = settings && typeof settings === 'object' && !Array.isArray(settings) ? copy(settings) : {};
  if (result.openings === undefined) result.openings = defaultOpenings();
  return result;
}

export function hostLength(settings, host) {
  if (host === 'W1' || host === 'W3') return settings?.length_mm;
  if (host === 'W2' || host === 'W4') return settings?.width_mm;
  return NaN;
}

export function openingOffset(settings, opening) {
  return opening?.offset_mm === null || opening?.offset_mm === undefined
    ? (hostLength(settings, opening?.host) - opening?.width_mm) / 2 : opening.offset_mm;
}

export function openingSegment(settings, opening) {
  const start = openingOffset(settings, opening);
  const end = start + opening?.width_mm;
  const length = settings?.length_mm;
  const width = settings?.width_mm;
  let a, b;
  switch (opening?.host) {
    case 'W1': a = { x: start, y: 0 }; b = { x: end, y: 0 }; break;
    case 'W2': a = { x: length, y: start }; b = { x: length, y: end }; break;
    case 'W3': a = { x: length - start, y: width }; b = { x: length - end, y: width }; break;
    case 'W4': a = { x: 0, y: width - start }; b = { x: 0, y: width - end }; break;
    default: a = { x: NaN, y: NaN }; b = { x: NaN, y: NaN };
  }
  return { start: a, end: b, center: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
}

export function pointToOffset(settings, host, point, openingWidth) {
  let distance;
  switch (host) {
    case 'W1': distance = point?.x; break;
    case 'W2': distance = point?.y; break;
    case 'W3': distance = settings?.length_mm - point?.x; break;
    case 'W4': distance = settings?.width_mm - point?.y; break;
    default: return NaN;
  }
  return distance - openingWidth / 2;
}

export function snap(value, spacing = 100) {
  if (!Number.isFinite(value)) return value;
  if (!Number.isFinite(spacing) || spacing <= 0) return value;
  const result = Math.round(value / spacing) * spacing;
  return Object.is(result, -0) ? 0 : result;
}

export function auditPlan(settings) {
  const issues = [];
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return ['Enter valid house settings before editing the plan.'];
  const bounds = [
    ['length_mm', 'Internal length', 3000, 30000], ['width_mm', 'Internal width', 3000, 30000],
    ['height_mm', 'Wall height', 2400, 5000], ['wall_thickness_mm', 'Wall thickness', 100, 500],
  ];
  for (const [key, label, minimum, maximum] of bounds) {
    if (!Number.isFinite(settings[key]) || settings[key] < minimum || settings[key] > maximum) issues.push(label + ' must be between ' + minimum + ' and ' + maximum + ' mm.');
  }
  const openings = settings.openings === undefined ? defaultOpenings() : settings.openings;
  if (!Array.isArray(openings)) return [...issues, 'Openings must be a list of doors and windows.'];
  if (openings.length > 40) issues.push('Use no more than 40 openings.');
  const ids = new Set();
  const spans = Object.fromEntries(HOSTS.map(host => [host, []]));
  openings.forEach((opening, index) => {
    if (!opening || typeof opening !== 'object' || Array.isArray(opening)) {
      issues.push('Opening ' + (index + 1) + ' must have valid opening properties.');
      return;
    }
    const label = typeof opening.id === 'string' ? opening.id : 'Opening ' + (index + 1);
    if (typeof opening.id !== 'string' || !/^[DN][1-9]\d{0,2}$/.test(opening.id)) issues.push(label + ': use a stable D1-D999 or N1-N999 ID.');
    if (ids.has(opening.id)) issues.push(label + ': opening IDs must be unique.');
    ids.add(opening.id);
    if (!['door', 'window'].includes(opening.kind)) issues.push(label + ': choose door or window.');
    else if (!String(opening.id ?? '').startsWith(opening.kind === 'door' ? 'D' : 'N')) issues.push(label + ': door IDs start with D; window IDs start with N.');
    if (!HOSTS.includes(opening.host)) issues.push(label + ': choose a host wall W1-W4.');
    const widthOK = Number.isFinite(opening.width_mm) && opening.width_mm >= 300 && opening.width_mm <= 30000;
    const heightOK = Number.isFinite(opening.height_mm) && opening.height_mm >= 300 && opening.height_mm <= 4900;
    const sillOK = Number.isFinite(opening.sill_mm) && opening.sill_mm >= 0 && opening.sill_mm <= 4500;
    if (!widthOK) issues.push(label + ': opening width must be between 300 and 30000 mm.');
    if (!heightOK) issues.push(label + ': opening height must be between 300 and 4900 mm.');
    if (!sillOK) issues.push(label + ': sill must be between 0 and 4500 mm.');
    if (opening.kind === 'door' && opening.sill_mm !== 0) issues.push(label + ': doors must start at floor level (sill 0 mm).');
    if (heightOK && sillOK && Number.isFinite(settings.height_mm) && opening.sill_mm + opening.height_mm > settings.height_mm - 100 + EPSILON) {
      issues.push(label + ': leave at least 100 mm between the opening head and wall top.');
    }
    const offset = openingOffset(settings, opening);
    const length = hostLength(settings, opening.host);
    if (opening.offset_mm !== null && opening.offset_mm !== undefined && !Number.isFinite(opening.offset_mm)) {
      issues.push(label + ': opening start must be a finite distance or centred.');
    } else if (widthOK && Number.isFinite(length) && (!Number.isFinite(offset) || offset < 100 - EPSILON || offset + opening.width_mm > length - 100 + EPSILON)) {
      issues.push(label + ': leave at least 100 mm from both ends of ' + opening.host + '.');
    }
    if (spans[opening.host] && widthOK && Number.isFinite(offset)) spans[opening.host].push({ id: label, start: offset, end: offset + opening.width_mm });
  });
  for (const host of HOSTS) {
    const intervals = spans[host].sort((a, b) => a.start - b.start || a.end - b.end);
    let previous = intervals[0];
    for (const current of intervals.slice(1)) {
      if (current.start < previous.end + 100 - EPSILON) issues.push(previous.id + ' and ' + current.id + ' on ' + host + ' overlap or are less than 100 mm apart.');
      if (current.end > previous.end) previous = current;
    }
  }
  return issues;
}

export function nextOpeningId(settings, kind, reservedIds = []) {
  const prefix = kind === 'door' ? 'D' : kind === 'window' ? 'N' : null;
  if (!prefix) throw new Error('Choose door or window before creating an opening.');
  const ids = [...(Array.isArray(settings?.openings) ? settings.openings.map(item => item?.id) : defaultOpenings().map(item => item.id)), ...reservedIds];
  const maximum = ids.reduce((largest, id) => {
    const match = typeof id === 'string' && id.match(new RegExp('^' + prefix + '([1-9]\\d*)$'));
    return match ? Math.max(largest, Number(match[1])) : largest;
  }, 0);
  if (maximum >= 999) throw new Error('All ' + prefix + ' opening IDs have been used. Reopen a design with available IDs.');
  return prefix + (maximum + 1);
}

const format = value => Number.isFinite(value) ? value.toLocaleString('en-GB', { maximumFractionDigits: 3 }) : String(value);
const same = (a, b) => Number.isFinite(a) && Number.isFinite(b) ? Math.abs(a - b) <= EPSILON : Object.is(a, b);
const openingList = settings => Array.isArray(settings.openings) ? settings.openings : [];
const location = (settings, opening) => opening.host + ' (' + (HOST_LABELS[opening.host] || 'unknown') + '), start ' + format(openingOffset(settings, opening)) + ' mm';

export function describeChanges(before, after) {
  if (!before || !after) return [];
  const a = cloneSettings(before), b = cloneSettings(after);
  const descriptions = [];
  for (const [key, name] of [['length_mm', 'Internal length'], ['width_mm', 'Internal width'], ['height_mm', 'Wall height'], ['wall_thickness_mm', 'Wall thickness']]) {
    if (!same(a[key], b[key])) descriptions.push(name + ': ' + format(a[key]) + ' to ' + format(b[key]) + ' mm.');
  }
  for (const [key, name] of [['project_name', 'Project name'], ['paint_colour', 'Paint finish'], ['paint_hex', 'Paint colour reference'], ['currency', 'Currency']]) {
    if (a[key] !== b[key]) descriptions.push(name + ': ' + String(a[key] ?? 'Not set') + ' to ' + String(b[key] ?? 'Not set') + '.');
  }
  const old = new Map(openingList(a).filter(item => item && typeof item === 'object').map(item => [item.id, item]));
  const current = new Map(openingList(b).filter(item => item && typeof item === 'object').map(item => [item.id, item]));
  for (const [id, opening] of old) if (!current.has(id)) descriptions.push('Removed ' + opening.kind + ' ' + id + ' from ' + location(a, opening) + '.');
  for (const [id, opening] of current) {
    const previous = old.get(id);
    if (!previous) {
      descriptions.push('Added ' + opening.kind + ' ' + id + ' on ' + location(b, opening) + ': ' + format(opening.width_mm) + ' x ' + format(opening.height_mm) + ' mm.');
      continue;
    }
    if (previous.kind !== opening.kind) descriptions.push(id + ': type changed from ' + previous.kind + ' to ' + opening.kind + '.');
    if (previous.host !== opening.host || !same(openingOffset(a, previous), openingOffset(b, opening))) descriptions.push(id + ': moved from ' + location(a, previous) + ' to ' + location(b, opening) + '.');
    else if ((previous.offset_mm === null) !== (opening.offset_mm === null)) descriptions.push(id + ': position changed to ' + (opening.offset_mm === null ? 'automatically centred' : 'a fixed start offset') + '.');
    for (const [key, name] of [['width_mm', 'width'], ['height_mm', 'height'], ['sill_mm', 'sill']]) {
      if (!same(previous[key], opening[key])) descriptions.push(id + ' ' + name + ': ' + format(previous[key]) + ' to ' + format(opening[key]) + ' mm.');
    }
  }
  const rates = new Set([...Object.keys(a.rates || {}), ...Object.keys(b.rates || {})]);
  for (const key of rates) if (!same(a.rates?.[key], b.rates?.[key])) descriptions.push(key.replaceAll('_', ' ') + ' rate: ' + format(a.rates?.[key]) + ' to ' + format(b.rates?.[key]) + '.');
  return descriptions;
}

function stable(value) {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stable(value[key])).join(',') + '}';
  if (typeof value === 'number' && !Number.isFinite(value)) return String(value);
  return JSON.stringify(value);
}

export class DraftHistory {
  constructor(initial) { this.reset(initial); }
  get current() { return cloneSettings(this._current); }
  get canUndo() { return this._past.length > 0; }
  get canRedo() { return this._future.length > 0; }
  get() { return this.current; }
  reset(settings) {
    this._current = cloneSettings(settings);
    this._past = [];
    this._future = [];
    return this.current;
  }
  push(settings) {
    const value = cloneSettings(settings);
    if (stable(value) === stable(this._current)) return this.current;
    this._past.push(this._current);
    if (this._past.length > 100) this._past.shift();
    this._current = value;
    this._future = [];
    return this.current;
  }
  undo() {
    if (!this.canUndo) return null;
    this._future.push(this._current);
    this._current = this._past.pop();
    return this.current;
  }
  redo() {
    if (!this.canRedo) return null;
    this._past.push(this._current);
    this._current = this._future.pop();
    return this.current;
  }
}

export function moveOpening(settings, id, offset, spacing = 0) {
  const next = cloneSettings(settings);
  const opening = openingList(next).find(item => item?.id === id);
  if (!opening) throw new Error('Opening ' + id + ' was not found.');
  opening.offset_mm = snap(offset, spacing);
  return next;
}

export function resizeShell(settings, length, width, spacing = 0) {
  const next = cloneSettings(settings);
  next.length_mm = snap(length, spacing);
  next.width_mm = snap(width, spacing);
  return next;
}
