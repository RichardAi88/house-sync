/** Pure millimetre geometry, validation and undo history for the rectangular plan editor. */
export const HOSTS = Object.freeze(['W1', 'W2', 'W3', 'W4']);
export const HOST_LABELS = Object.freeze({ W1: 'south', W2: 'east', W3: 'north', W4: 'west' });
const EPSILON = 1e-6;

export const RATE_ITEMS = Object.freeze([
  ['FLOOR', 'Floor slab', 'm2', 'floor_m2'], ['WALL', 'Wall construction', 'm3', 'wall_m3'],
  ['ROOF', 'Roof', 'm2', 'roof_m2'], ['PAINT', 'Paint per coat', 'm2', 'paint_m2'],
  ['DOOR', 'Doors', 'each', 'opening_each'], ['WINDOW', 'Windows', 'each', 'opening_each'],
  ['FLOOR_FINISH', 'Floor finish', 'm2', 'floor_finish_m2'], ['CEILING', 'Ceiling', 'm2', 'ceiling_m2'],
  ['SKIRTING', 'Skirting', 'm', 'skirting_m'], ['INTERNAL_PLASTER', 'Internal plaster/render', 'm2', 'internal_plaster_m2'],
  ['EXTERNAL_RENDER', 'External render', 'm2', 'external_render_m2'],
]);
export const WASTAGE_DEFAULTS = Object.freeze({
  floor: 0, wall: 0, roof: 0, paint: 0, door: 0, window: 0,
  floor_finish: 10, ceiling: 5, skirting: 10, internal_plaster: 5, external_render: 5,
});
export const RATE_SOURCE_DEFAULT = 'Illustrative AUD rates, not a tender estimate; no supplier quotes';
export const RATE_DATE_DEFAULT = '2026-11-17';

export const BUILDER_PRICING_DEFAULTS = Object.freeze({ overhead_margin_percent: 20, admin_fee_aud: 0, gst_percent: 10, credit_treatment: 'at_cost', validity_days: 14, builder_name: '', client_description: '' });

/** Fill absent pricing fields without replacing historical currency or entered rates. */
export function normalizeBoqSettings(settings) {
  const result = {
    ...settings, currency: settings.currency ?? 'AUD', paint_coats: settings.paint_coats ?? 2,
    exterior_finish: settings.exterior_finish ? { ...settings.exterior_finish }
      : { kind: 'rendered_paint', name: settings.paint_colour ?? 'Light blue', hex: settings.paint_hex ?? '#ADD8E6' },
    inner_leaf_finish: settings.inner_leaf_finish ?? 'plaster_paint', inner_brick_hex: settings.inner_brick_hex ?? '#B88465',
    rate_overrides: settings.rate_overrides === undefined
      ? Object.fromEntries(RATE_ITEMS.filter(([, , , key]) => Object.hasOwn(settings.rates ?? {}, key)).map(([code, , , key]) => [code, settings.rates[key]]))
      : { ...settings.rate_overrides },
    wastage: { ...WASTAGE_DEFAULTS, ...settings.wastage },
    builder_pricing: { ...BUILDER_PRICING_DEFAULTS, ...settings.builder_pricing },
    allowances: Array.isArray(settings.allowances) ? settings.allowances.map(item => ({ note: '', ...item })) : [],
    rate_source: settings.rate_source ?? RATE_SOURCE_DEFAULT, rate_date: settings.rate_date ?? RATE_DATE_DEFAULT,
  };
  // The old rates mirror must never pin library-driven new designs to defaults.
  delete result.rates;
  return result;
}

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

const partitionList = settings => Array.isArray(settings?.partitions) ? settings.partitions.filter(item => item && typeof item === 'object' && !Array.isArray(item)) : [];
const pointValue = value => Array.isArray(value) ? { x: value.length === 2 ? value[0] : NaN, y: value.length === 2 ? value[1] : NaN } : { x: value?.x, y: value?.y };
const finitePoint = point => Number.isFinite(point?.x) && Number.isFinite(point?.y);

export function planHosts(settings) {
  return [...HOSTS, ...partitionList(settings).map(partition => partition.id).filter(id => typeof id === 'string')];
}

export function hostGeometry(settings, host) {
  const length = settings?.length_mm, width = settings?.width_mm;
  let start, end;
  switch (host) {
    case 'W1': start = { x: 0, y: 0 }; end = { x: length, y: 0 }; break;
    case 'W2': start = { x: length, y: 0 }; end = { x: length, y: width }; break;
    case 'W3': start = { x: length, y: width }; end = { x: 0, y: width }; break;
    case 'W4': start = { x: 0, y: width }; end = { x: 0, y: 0 }; break;
    default: {
      const partition = partitionList(settings).find(item => item.id === host);
      start = pointValue(partition?.start_mm); end = pointValue(partition?.end_mm);
    }
  }
  const span = finitePoint(start) && finitePoint(end) ? Math.hypot(end.x - start.x, end.y - start.y) : NaN;
  const partition = partitionList(settings).find(item => item.id === host);
  return { start, end, length: span, direction: { x: (end.x - start.x) / span, y: (end.y - start.y) / span },
    height_mm: partition ? (partition.height_mm ?? 3000) : settings?.height_mm,
    thickness_mm: partition ? (partition.thickness_mm ?? 100) : settings?.wall_thickness_mm };
}

export function hostDirection(settings, host) { return hostGeometry(settings, host).direction; }

export function hostLength(settings, host) {
  if (host === 'W1' || host === 'W3') return settings?.length_mm;
  if (host === 'W2' || host === 'W4') return settings?.width_mm;
  return hostGeometry(settings, host).length;
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
    default: {
      const host = hostGeometry(settings, opening?.host);
      a = { x: host.start.x + host.direction.x * start, y: host.start.y + host.direction.y * start };
      b = { x: host.start.x + host.direction.x * end, y: host.start.y + host.direction.y * end };
    }
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
    default: {
      const frame = hostGeometry(settings, host);
      distance = (point?.x - frame.start.x) * frame.direction.x + (point?.y - frame.start.y) * frame.direction.y;
    }
  }
  return distance - openingWidth / 2;
}

export function snap(value, spacing = 100) {
  if (!Number.isFinite(value)) return value;
  if (!Number.isFinite(spacing) || spacing <= 0) return value;
  const result = Math.round(value / spacing) * spacing;
  return Object.is(result, -0) ? 0 : result;
}

function intersection(one, two) {
  if (![one.start, one.end, two.start, two.end].every(finitePoint)) return null;
  const denominator = one.direction.x * two.direction.y - one.direction.y * two.direction.x;
  if (Math.abs(denominator) < EPSILON) return null;
  const dx = two.start.x - one.start.x, dy = two.start.y - one.start.y;
  const along = (dx * two.direction.y - dy * two.direction.x) / denominator;
  const across = (dx * one.direction.y - dy * one.direction.x) / denominator;
  if (along < -EPSILON || along > one.length + EPSILON || across < -EPSILON || across > two.length + EPSILON) return null;
  return { x: one.start.x + one.direction.x * along, y: one.start.y + one.direction.y * along, offset: along };
}

function auditPartitions(settings) {
  const issues = [], source = settings.partitions === undefined ? [] : settings.partitions;
  if (!Array.isArray(source)) return ['Partitions must be a list of internal walls.'];
  if (source.length > 40) issues.push('Use no more than 40 partitions.');
  const ids = new Set(), valid = [];
  for (const [index, partition] of source.entries()) {
    if (!partition || typeof partition !== 'object' || Array.isArray(partition)) { issues.push('Partition ' + (index + 1) + ' needs valid properties.'); continue; }
    const label = typeof partition.id === 'string' ? partition.id : 'Partition ' + (index + 1);
    if (!/^P[1-9]\d{0,2}$/.test(String(partition.id ?? ''))) issues.push(label + ': use a stable P1-P999 ID.');
    if (ids.has(partition.id)) issues.push(label + ': partition IDs must be unique.');
    ids.add(partition.id);
    const a = pointValue(partition.start_mm), b = pointValue(partition.end_mm);
    if (!finitePoint(a) || !finitePoint(b)) { issues.push(label + ': endpoints must contain finite millimetre coordinates.'); continue; }
    const horizontal = Math.abs(a.y - b.y) <= EPSILON, vertical = Math.abs(a.x - b.x) <= EPSILON;
    if ((!horizontal && !vertical) || Math.hypot(b.x - a.x, b.y - a.y) <= EPSILON) { issues.push(label + ': draw a non-zero horizontal or vertical partition.'); continue; }
    const thickness = partition.thickness_mm ?? 100, height = partition.height_mm ?? 3000;
    if (!Number.isFinite(thickness) || thickness < 50 || thickness > 500) issues.push(label + ': thickness must be between 50 and 500 mm.');
    if (!Number.isFinite(height) || height < 300 || height > 5000 || height > settings.height_mm + EPSILON) issues.push(label + ': height must be between 300 mm and the shell wall height.');
    if (partition.wall_type !== undefined && (typeof partition.wall_type !== 'string' || !partition.wall_type.trim() || partition.wall_type.length > 80 || /[\x00-\x1F]/.test(partition.wall_type))) issues.push(label + ': enter a single-line wall type of up to 80 characters.');
    const x0 = Math.min(a.x, b.x) - (vertical ? thickness / 2 : 0), x1 = Math.max(a.x, b.x) + (vertical ? thickness / 2 : 0);
    const y0 = Math.min(a.y, b.y) - (horizontal ? thickness / 2 : 0), y1 = Math.max(a.y, b.y) + (horizontal ? thickness / 2 : 0);
    if (x0 < -EPSILON || y0 < -EPSILON || x1 > settings.length_mm + EPSILON || y1 > settings.width_mm + EPSILON) issues.push(label + ': partition thickness and endpoints must stay inside the clear shell.');
    valid.push({ partition, a, b, horizontal, vertical, x0, x1, y0, y1 });
  }
  for (let index = 0; index < valid.length; index++) for (const other of valid.slice(index + 1)) {
    const one = valid[index];
    const overlapX = Math.min(one.x1, other.x1) - Math.max(one.x0, other.x0), overlapY = Math.min(one.y1, other.y1) - Math.max(one.y0, other.y0);
    const joined = intersection(hostGeometry(settings, one.partition.id), hostGeometry(settings, other.partition.id));
    if (overlapX > EPSILON && overlapY > EPSILON && !joined) issues.push(one.partition.id + ' and ' + other.partition.id + ': wall bodies overlap without a perpendicular centreline junction.');
    if (one.horizontal === other.horizontal && ((one.horizontal && Math.abs(one.a.y - other.a.y) <= EPSILON && overlapX > EPSILON) || (one.vertical && Math.abs(one.a.x - other.a.x) <= EPSILON && overlapY > EPSILON))) issues.push(one.partition.id + ' and ' + other.partition.id + ': collinear partitions overlap.');
  }
  const minimum = settings.min_room_width_mm === undefined ? 900 : settings.min_room_width_mm;
  if (!Number.isFinite(minimum) || minimum < 300 || minimum > 10000) issues.push('Minimum room width must be between 300 and 10000 mm.');
  const rooms = settings.rooms === undefined ? [] : settings.rooms;
  if (!Array.isArray(rooms)) issues.push('Room metadata must be a list.');
  else {
    if (rooms.length > 200) issues.push('Use no more than 200 room metadata entries.');
    const roomIds = new Set();
    for (const room of rooms) {
      if (!room || typeof room !== 'object' || Array.isArray(room)) { issues.push('Room metadata needs a valid room ID and name.'); continue; }
      if (!/^ROOM(?:[1-9]\d*|_[A-F0-9]{12})$/.test(String(room.id ?? ''))) issues.push('Use a derived stable room ID for room metadata.');
      if (roomIds.has(room.id)) issues.push('Room metadata IDs must be unique.');
      roomIds.add(room.id);
      if (typeof room.name !== 'string' || !room.name.trim() || room.name.length > 120 || /[\x00-\x1F]/.test(room.name)) issues.push(String(room.id) + ': enter a single-line room name of up to 120 characters.');
      if (room.finishes !== undefined && (!room.finishes || typeof room.finishes !== 'object' || Array.isArray(room.finishes))) issues.push(String(room.id) + ': finishes must be an object.');
      else for (const [key, value] of Object.entries(room.finishes ?? {})) if (!['floor', 'ceiling', 'skirting', 'paint'].includes(key) || (value !== null && (typeof value !== 'string' || !value.trim() || value.length > 120 || /[\x00-\x1F]/.test(value)))) issues.push(String(room.id) + ': enter a valid single-line ' + key + ' finish or leave it unset.');
    }
  }
  return issues;
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
  issues.push(...auditPartitions(settings));
  const openings = settings.openings === undefined ? defaultOpenings() : settings.openings;
  if (!Array.isArray(openings)) return [...issues, 'Openings must be a list of doors and windows.'];
  if (openings.length > 40) issues.push('Use no more than 40 openings.');
  const ids = new Set();
  const hosts = planHosts(settings), spans = Object.fromEntries(hosts.map(host => [host, []]));
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
    if (!hosts.includes(opening.host)) issues.push(label + ': choose an existing shell wall or partition host.');
    const widthOK = Number.isFinite(opening.width_mm) && opening.width_mm >= 300 && opening.width_mm <= 30000;
    const heightOK = Number.isFinite(opening.height_mm) && opening.height_mm >= 300 && opening.height_mm <= 4900;
    const sillOK = Number.isFinite(opening.sill_mm) && opening.sill_mm >= 0 && opening.sill_mm <= 4500;
    if (!widthOK) issues.push(label + ': opening width must be between 300 and 30000 mm.');
    if (!heightOK) issues.push(label + ': opening height must be between 300 and 4900 mm.');
    if (!sillOK) issues.push(label + ': sill must be between 0 and 4500 mm.');
    if (opening.kind === 'door' && opening.sill_mm !== 0) issues.push(label + ': doors must start at floor level (sill 0 mm).');
    const hostHeight = hostGeometry(settings, opening.host).height_mm;
    if (heightOK && sillOK && Number.isFinite(hostHeight) && opening.sill_mm + opening.height_mm > hostHeight - 100 + EPSILON) {
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
    if (widthOK && Number.isFinite(offset)) for (const partition of partitionList(settings)) {
      if (partition.id === opening.host) continue;
      const joined = intersection(hostGeometry(settings, opening.host), hostGeometry(settings, partition.id));
      const exclusion = (partition.thickness_mm ?? 100) / 2 + 100;
      if (joined && offset < joined.offset + exclusion - EPSILON && offset + opening.width_mm > joined.offset - exclusion + EPSILON) issues.push(label + ': leave at least 100 mm clear of the ' + partition.id + ' junction footprint.');
    }
  });
  for (const host of hosts) {
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
const location = (settings, opening) => opening.host + ' (' + (HOST_LABELS[opening.host] || (planHosts(settings).includes(opening.host) ? 'partition' : 'unknown')) + '), start ' + format(openingOffset(settings, opening)) + ' mm';

export function describeChanges(before, after) {
  if (!before || !after) return [];
  const a = normalizeBoqSettings(cloneSettings(before)), b = normalizeBoqSettings(cloneSettings(after));
  const descriptions = [];
  for (const [key, name] of [['length_mm', 'Internal length'], ['width_mm', 'Internal width'], ['height_mm', 'Wall height'], ['wall_thickness_mm', 'Wall thickness']]) {
    if (!same(a[key], b[key])) descriptions.push(name + ': ' + format(a[key]) + ' to ' + format(b[key]) + ' mm.');
  }
  for (const [key, name] of [['project_name', 'Project name'], ['paint_colour', 'Inside · Paint finish'], ['paint_hex', 'Internal paint colour'], ['inner_brick_hex', 'Bare internal brick colour'], ['currency', 'Currency']]) {
    if (a[key] !== b[key]) descriptions.push(name + ': ' + String(a[key] ?? 'Not set') + ' to ' + String(b[key] ?? 'Not set') + '.');
  }
  const treatment = { facebrick: 'exposed facebrick', rendered_paint: 'render and paint', bare_brick: 'bare brick', plaster_paint: 'plaster and paint' };
  if (a.inner_leaf_finish !== b.inner_leaf_finish) descriptions.push(`Internal brick leaf: ${treatment[a.inner_leaf_finish]} to ${treatment[b.inner_leaf_finish]}.`);
  for (const [key, name] of [['kind', 'treatment'], ['name', 'finish'], ['hex', 'colour']]) {
    const old = a.exterior_finish[key], next = b.exterior_finish[key];
    if (old !== next) descriptions.push(`External brick leaf ${name}: ${key === 'kind' ? treatment[old] : old} to ${key === 'kind' ? treatment[next] : next}.`);
  }
  if (!same(a.min_room_width_mm ?? 900, b.min_room_width_mm ?? 900)) descriptions.push('Minimum room width: ' + format(a.min_room_width_mm ?? 900) + ' to ' + format(b.min_room_width_mm ?? 900) + ' mm.');
  const oldPartitions = new Map(partitionList(a).map(item => [item.id, item])), newPartitions = new Map(partitionList(b).map(item => [item.id, item]));
  const pointText = point => '(' + format(point?.[0]) + ', ' + format(point?.[1]) + ')';
  for (const [id] of oldPartitions) if (!newPartitions.has(id)) descriptions.push('Removed partition ' + id + '.');
  for (const [id, partition] of newPartitions) {
    const previous = oldPartitions.get(id);
    if (!previous) { descriptions.push('Added partition ' + id + ': ' + pointText(partition.start_mm) + ' to ' + pointText(partition.end_mm) + ', ' + format(partition.thickness_mm ?? 100) + ' mm thick and ' + format(partition.height_mm ?? 3000) + ' mm high.'); continue; }
    for (const [key, name] of [['start_mm', 'start'], ['end_mm', 'end']]) if (stable(previous[key]) !== stable(partition[key])) descriptions.push(id + ' ' + name + ': ' + pointText(previous[key]) + ' to ' + pointText(partition[key]) + ' mm.');
    for (const [key, name, fallback] of [['thickness_mm', 'thickness', 100], ['height_mm', 'height', 3000]]) if (!same(previous[key] ?? fallback, partition[key] ?? fallback)) descriptions.push(id + ' ' + name + ': ' + format(previous[key] ?? fallback) + ' to ' + format(partition[key] ?? fallback) + ' mm.');
    if ((previous.wall_type ?? 'partition') !== (partition.wall_type ?? 'partition')) descriptions.push(id + ' wall type: ' + String(previous.wall_type ?? 'partition') + ' to ' + String(partition.wall_type ?? 'partition') + '.');
  }
  const roomList = value => Array.isArray(value.rooms) ? value.rooms.filter(item => item && typeof item === 'object') : [];
  const oldRooms = new Map(roomList(a).map(item => [item.id, item])), newRooms = new Map(roomList(b).map(item => [item.id, item]));
  for (const [id, room] of oldRooms) if (!newRooms.has(id)) descriptions.push('Removed room metadata ' + id + ' (' + String(room.name) + ').');
  for (const [id, room] of newRooms) {
    const previous = oldRooms.get(id);
    if (!previous) descriptions.push('Named room ' + id + ': ' + String(room.name) + '.');
    else if (previous.name !== room.name) descriptions.push(id + ' name: ' + String(previous.name) + ' to ' + String(room.name) + '.');
    for (const key of ['floor', 'ceiling', 'skirting', 'paint']) if ((previous?.finishes?.[key] ?? null) !== (room.finishes?.[key] ?? null)) descriptions.push(id + ' ' + key + ' finish: ' + String(previous?.finishes?.[key] ?? 'default') + ' to ' + String(room.finishes?.[key] ?? 'default') + '.');
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
  const overrides = new Set([...Object.keys(a.rate_overrides), ...Object.keys(b.rate_overrides)]);
  for (const code of overrides) if (!same(a.rate_overrides[code], b.rate_overrides[code])) {
    descriptions.push(code.replaceAll('_', ' ') + ' rate: ' + (Object.hasOwn(a.rate_overrides, code) ? format(a.rate_overrides[code]) + ' override' : 'library') + ' to ' + (Object.hasOwn(b.rate_overrides, code) ? format(b.rate_overrides[code]) + ' override' : 'library') + '.');
  }
  if (!same(a.paint_coats, b.paint_coats)) descriptions.push('Paint coats: ' + format(a.paint_coats) + ' to ' + format(b.paint_coats) + '.');
  const wastage = new Set([...Object.keys(a.wastage), ...Object.keys(b.wastage)]);
  for (const key of wastage) if (!same(a.wastage[key], b.wastage[key])) descriptions.push(key.replaceAll('_', ' ') + ' wastage: ' + format(a.wastage[key]) + '% to ' + format(b.wastage[key]) + '%.');
  for (const [key, label] of [['rate_source', 'Rate source'], ['rate_date', 'Rate date']]) {
    if (a[key] !== b[key]) descriptions.push(label + ': ' + a[key] + ' to ' + b[key] + '.');
  }
  for (const [key, label] of [['overhead_margin_percent', 'Combined overhead and margin'], ['admin_fee_aud', 'Variation administration fee'], ['credit_treatment', 'Credit treatment'], ['validity_days', 'Quote validity days'], ['builder_name', 'Builder name'], ['client_description', 'Client description']]) {
    if (!same(a.builder_pricing[key], b.builder_pricing[key])) descriptions.push(label + ': ' + String(a.builder_pricing[key]) + ' to ' + String(b.builder_pricing[key]) + '.');
  }
  const previousAllowances = new Map(a.allowances.map(item => [item.id, item]));
  const nextAllowances = new Map(b.allowances.map(item => [item.id, item]));
  for (const id of new Set([...previousAllowances.keys(), ...nextAllowances.keys()])) {
    const previous = previousAllowances.get(id), current = nextAllowances.get(id);
    if (!previous || !current) descriptions.push((current ? 'Added ' : 'Removed ') + 'allowance ' + id + ': ' + (current || previous).description + '.');
    else for (const key of ['kind', 'description', 'amount', 'note']) if (!same(previous[key], current[key])) descriptions.push('Allowance ' + id + ' ' + key + ': ' + String(previous[key] ?? '') + ' to ' + String(current[key] ?? '') + '.');
  }
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

export function nextPartitionId(settings, reservedIds = []) {
  const ids = [...partitionList(settings).map(item => item.id), ...reservedIds];
  const maximum = ids.reduce((largest, id) => {
    const match = typeof id === 'string' && id.match(/^P([1-9]\d*)$/);
    return match ? Math.max(largest, Number(match[1])) : largest;
  }, 0);
  if (maximum >= 999) throw new Error('All P partition IDs have been used. Reopen a design with available IDs.');
  return 'P' + (maximum + 1);
}

export function snapPoint(settings, point, spacing = 100, tolerance = 150, excludeIds = []) {
  const input = pointValue(point);
  if (!finitePoint(input) || spacing === 0) return input;
  const grid = { x: snap(input.x, spacing), y: snap(input.y, spacing) };
  const candidates = [];
  for (const host of planHosts(settings)) {
    if (excludeIds.includes(host)) continue;
    const frame = hostGeometry(settings, host);
    if (!Number.isFinite(frame.length) || frame.length <= EPSILON) continue;
    candidates.push({ point: frame.start, priority: 0 }, { point: frame.end, priority: 0 });
    const along = Math.max(0, Math.min(frame.length, (grid.x - frame.start.x) * frame.direction.x + (grid.y - frame.start.y) * frame.direction.y));
    candidates.push({ point: { x: frame.start.x + frame.direction.x * along, y: frame.start.y + frame.direction.y * along }, priority: 1 });
  }
  const close = candidates.map(({ point: candidate, priority }) => ({ candidate, priority, distance: Math.hypot(candidate.x - input.x, candidate.y - input.y) }))
    .filter(item => item.distance <= tolerance + EPSILON).sort((a, b) => a.priority - b.priority || a.distance - b.distance);
  return close.length ? { ...close[0].candidate } : grid;
}

export function movePartition(settings, id, dx, dy, spacing = 0) {
  const next = cloneSettings(settings), partition = partitionList(next).find(item => item.id === id);
  if (!partition) throw new Error('Partition ' + id + ' was not found.');
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) throw new Error('Partition movement must use finite distances.');
  const frame = hostGeometry(settings, id), horizontal = Math.abs(frame.direction.x) > .5;
  const delta = horizontal ? { x: 0, y: snap(dy, spacing) } : { x: snap(dx, spacing), y: 0 };
  const attached = point => {
    const along = (point[0] - frame.start.x) * frame.direction.x + (point[1] - frame.start.y) * frame.direction.y;
    const normal = (point[0] - frame.start.x) * frame.direction.y - (point[1] - frame.start.y) * frame.direction.x;
    return Math.abs(normal) <= EPSILON && along >= -EPSILON && along <= frame.length + EPSILON;
  };
  for (const other of partitionList(next)) {
    const direction = hostDirection(settings, other.id);
    const perpendicular = Math.abs(direction.x * frame.direction.x + direction.y * frame.direction.y) < EPSILON;
    for (const key of ['start_mm', 'end_mm']) if (other.id === id || (perpendicular && Array.isArray(other[key]) && attached(other[key]))) {
      other[key] = [other[key][0] + delta.x, other[key][1] + delta.y];
    }
  }
  return next;
}

export function resizePartition(settings, id, endpoint, point, spacing = 0) {
  if (!['start', 'end'].includes(endpoint)) throw new Error('Choose the start or end partition grip.');
  const next = cloneSettings(settings), partition = partitionList(next).find(item => item.id === id);
  if (!partition) throw new Error('Partition ' + id + ' was not found.');
  const target = pointValue(point);
  if (!finitePoint(target)) throw new Error('Partition endpoint must use finite coordinates.');
  const frame = hostGeometry(settings, id), horizontal = Math.abs(frame.direction.x) > .5;
  const fixed = endpoint === 'start' ? partition.end_mm : partition.start_mm;
  const candidate = horizontal ? { x: snap(target.x, spacing), y: fixed[1] } : { x: fixed[0], y: snap(target.y, spacing) };
  const joined = snapPoint(settings, candidate, spacing, 150, [id]);
  partition[endpoint + '_mm'] = horizontal ? [joined.x, fixed[1]] : [fixed[0], joined.y];
  return next;
}

export function deletePartition(settings, id) {
  const next = cloneSettings(settings);
  if (!partitionList(next).some(item => item.id === id)) throw new Error('Partition ' + id + ' was not found.');
  next.partitions = next.partitions.filter(item => item.id !== id);
  next.openings = next.openings.filter(item => item?.host !== id);
  return next;
}

export const removePartition = deletePartition;
