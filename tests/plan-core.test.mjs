import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../housesync/static/plan-core.js', import.meta.url), 'utf8');
const core = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const { defaultOpenings, cloneSettings, hostLength, openingOffset, openingSegment, pointToOffset, snap,
  auditPlan, nextOpeningId, describeChanges, DraftHistory, moveOpening, resizeShell } = core;
const fixture = () => ({
  project_name: 'Plan geometry test', length_mm: 10000, width_mm: 8000, height_mm: 3000,
  wall_thickness_mm: 200, paint_colour: 'Light blue', paint_hex: '#ADD8E6', currency: 'SGD',
  rates: { floor_m2: 80, wall_m3: 300, roof_m2: 120, paint_m2: 12, opening_each: 500 },
  openings: defaultOpenings(),
});

test('legacy settings receive independently cloned default openings without mutating input', () => {
  const original = fixture(); delete original.openings;
  const one = cloneSettings(original), two = cloneSettings(original);
  assert.equal(original.openings, undefined);
  assert.deepEqual(one.openings.map(o => o.id), ['D1', 'N1', 'N2']);
  one.openings[0].width_mm = 1200;
  one.rates.floor_m2 = 95;
  assert.equal(two.openings[0].width_mm, 1000);
  assert.equal(original.rates.floor_m2, 80);
  assert.deepEqual(auditPlan(two), []);
});

test('host lengths and all four directional opening coordinates use inner reference lines', () => {
  const house = fixture();
  const expected = {
    W1: { start: { x: 2000, y: 0 }, end: { x: 3000, y: 0 }, center: { x: 2500, y: 0 } },
    W2: { start: { x: 10000, y: 2000 }, end: { x: 10000, y: 3000 }, center: { x: 10000, y: 2500 } },
    W3: { start: { x: 8000, y: 8000 }, end: { x: 7000, y: 8000 }, center: { x: 7500, y: 8000 } },
    W4: { start: { x: 0, y: 6000 }, end: { x: 0, y: 5000 }, center: { x: 0, y: 5500 } },
  };
  for (const host of Object.keys(expected)) {
    const opening = { host, width_mm: 1000, offset_mm: 2000 };
    assert.deepEqual(openingSegment(house, opening), expected[host]);
    assert.equal(pointToOffset(house, host, expected[host].center, opening.width_mm), 2000);
    assert.equal(hostLength(house, host), host === 'W1' || host === 'W3' ? 10000 : 8000);
  }
});

test('centred openings follow host resize and fixed offsets remain fixed', () => {
  const house = fixture();
  assert.equal(openingOffset(house, house.openings[0]), 4500);
  const larger = resizeShell(house, 12000, 8000);
  assert.equal(openingOffset(larger, larger.openings[0]), 5500);
  const fixed = moveOpening(larger, 'D1', 2349, 100);
  assert.equal(fixed.openings[0].offset_mm, 2300);
  assert.equal(openingOffset(resizeShell(fixed, 14000, 8000), fixed.openings[0]), 2300);
  assert.equal(house.length_mm, 10000);
  assert.equal(house.openings[0].offset_mm, null);
});

test('snapping supports precision choices and disabled snapping without clamping', () => {
  assert.equal(snap(2349, 100), 2300);
  assert.equal(snap(2351, 100), 2400);
  assert.equal(snap(2314, 10), 2310);
  assert.equal(snap(2314, 0), 2314);
  assert.equal(snap(-249, 100), -200);
  assert.equal(snap(-20, 100), 0);
  assert.ok(Number.isNaN(snap(NaN, 100)));
});

test('audit never throws for partially typed or malformed settings', () => {
  for (const input of [null, [], {}, { length_mm: NaN }, { ...fixture(), openings: null }, { ...fixture(), openings: [null, 1] }]) {
    assert.doesNotThrow(() => auditPlan(input));
    assert.ok(auditPlan(input).length > 0);
  }
  const partial = fixture(); partial.width_mm = NaN;
  partial.openings[0].offset_mm = Infinity;
  assert.ok(auditPlan(partial).some(message => message.includes('finite')));
});

test('audit validates all dimensional bounds and exact boundary values', () => {
  const house = fixture();
  for (const [key, value] of [['length_mm', 2999], ['width_mm', 30001], ['height_mm', 5001], ['wall_thickness_mm', 99]]) {
    const invalid = { ...house, [key]: value };
    assert.ok(auditPlan(invalid).some(message => message.includes('must be between')));
  }
  const minimum = { ...fixture(), length_mm: 3000, width_mm: 3000, height_mm: 2400, wall_thickness_mm: 100 };
  assert.deepEqual(auditPlan(minimum), []);
});

test('opening containment requires 100 mm ends and does not silently shift on shell resize', () => {
  const house = fixture(); house.openings = [{ ...defaultOpenings()[0], offset_mm: 100 }];
  assert.deepEqual(auditPlan(house), []);
  house.openings[0].offset_mm = 99;
  assert.ok(auditPlan(house).some(message => message.includes('both ends')));
  house.openings[0].offset_mm = 8500;
  assert.deepEqual(auditPlan(house), []);
  const reduced = resizeShell(house, 9000, 8000);
  assert.equal(reduced.openings[0].offset_mm, 8500);
  assert.ok(auditPlan(reduced).some(message => message.includes('both ends')));
});

test('same-host overlap and spacing rules include windows at different sill heights', () => {
  const house = fixture();
  house.openings = [
    { id: 'D1', kind: 'door', host: 'W1', offset_mm: 1000, width_mm: 1000, height_mm: 2100, sill_mm: 0 },
    { id: 'N3', kind: 'window', host: 'W1', offset_mm: 2100, width_mm: 1000, height_mm: 500, sill_mm: 2200 },
  ];
  assert.deepEqual(auditPlan(house), []);
  house.openings[1].offset_mm = 2099;
  assert.ok(auditPlan(house).some(message => message.includes('100 mm apart')));
  house.openings[1].host = 'W3';
  assert.deepEqual(auditPlan(house), []);
});

test('opening audit checks types, IDs, max count, sill and head clearance', () => {
  const house = fixture();
  house.openings[0].sill_mm = 1;
  assert.ok(auditPlan(house).some(message => message.includes('floor level')));
  house.openings[0].sill_mm = 0;
  house.openings[0].height_mm = 2900;
  assert.deepEqual(auditPlan(house), []);
  house.openings[0].height_mm = 2901;
  assert.ok(auditPlan(house).some(message => message.includes('wall top')));
  house.openings[0].height_mm = 2100;
  house.openings.push({ ...house.openings[0] });
  assert.ok(auditPlan(house).some(message => message.includes('unique')));
  house.openings[3].id = 'N4';
  assert.ok(auditPlan(house).some(message => message.includes('door IDs')));
  house.openings = Array.from({ length: 41 }, (_, i) => ({ ...defaultOpenings()[0], id: 'D' + (i + 1) }));
  assert.ok(auditPlan(house).some(message => message.includes('40 openings')));
});

test('next IDs preserve gaps and optionally remember deleted IDs', () => {
  const house = fixture();
  house.openings.push({ ...defaultOpenings()[0], id: 'D5' });
  assert.equal(nextOpeningId(house, 'door'), 'D6');
  house.openings.pop();
  assert.equal(nextOpeningId(house, 'door', new Set(['D5', 'D8'])), 'D9');
  assert.equal(nextOpeningId(house, 'window'), 'N3');
  assert.throws(() => nextOpeningId(house, 'other'));
  assert.throws(() => nextOpeningId(house, 'door', ['D999']));
});

test('draft descriptions explain add, remove, resize, position and zero-cost metadata changes', () => {
  const before = fixture(), after = cloneSettings(before);
  after.length_mm = 12000;
  after.paint_colour = 'Warm white';
  after.openings = after.openings.filter(opening => opening.id !== 'N2');
  after.openings[0].offset_mm = 1500;
  after.openings[0].width_mm = 1100;
  after.openings.push({ id: 'D2', kind: 'door', host: 'W4', offset_mm: 2000, width_mm: 900, height_mm: 2100, sill_mm: 0 });
  const description = describeChanges(before, after).join('\n');
  assert.match(description, /Internal length: 10,000 to 12,000 mm/);
  assert.match(description, /Removed window N2/);
  assert.match(description, /Added door D2/);
  assert.match(description, /D1: moved/);
  assert.match(description, /D1 width: 1,000 to 1,100 mm/);
  assert.match(description, /Paint finish: Light blue to Warm white/);
  assert.deepEqual(describeChanges(before, before), []);
});

test('undo and redo restore full settings without sharing mutable references', () => {
  const before = fixture(), history = new DraftHistory(before);
  const moved = moveOpening(before, 'D1', 2000);
  history.push(moved);
  moved.openings[0].offset_mm = 99;
  assert.equal(history.current.openings[0].offset_mm, 2000);
  const current = history.current; current.rates.floor_m2 = 999;
  assert.equal(history.get().rates.floor_m2, 80);
  assert.equal(history.canUndo, true);
  assert.equal(history.undo().openings[0].offset_mm, null);
  assert.equal(history.undo(), null);
  assert.equal(history.canRedo, true);
  assert.equal(history.redo().openings[0].offset_mm, 2000);
  assert.equal(history.redo(), null);
  history.undo();
  history.push(resizeShell(before, 11000, 8000));
  assert.equal(history.canRedo, false);
  assert.equal(before.length_mm, 10000);
});

test('history deduplicates equivalent settings and resets saved drafts', () => {
  const before = fixture(), history = new DraftHistory(before);
  const reordered = Object.fromEntries(Object.entries(before).reverse());
  history.push(reordered);
  assert.equal(history.canUndo, false);
  for (let i = 1; i <= 110; i++) history.push({ ...before, length_mm: 10000 + i });
  let count = 0;
  while (history.undo()) count++;
  assert.equal(count, 100);
  history.reset(before);
  assert.equal(history.canUndo, false);
  assert.equal(history.canRedo, false);
  assert.deepEqual(history.current, before);
});

test('a cancelled transient gesture leaves committed history unchanged', () => {
  const before = fixture(), history = new DraftHistory(before);
  const transient = resizeShell(history.current, 12500, 9200, 100);
  assert.equal(transient.length_mm, 12500);
  assert.equal(history.canUndo, false);
  assert.deepEqual(history.current, before);
  const committed = resizeShell(history.current, 12000, 9000, 100);
  history.push(committed);
  assert.equal(history.canUndo, true);
  assert.equal(history.undo().length_mm, 10000);
});

test('frontend accepts backend-valid wide and tall opening bounds', () => {
  const house = fixture();
  house.length_mm = 30000; house.height_mm = 5000;
  house.openings = [{ id: 'N1', kind: 'window', host: 'W1', offset_mm: 100, width_mm: 6000, height_mm: 4900, sill_mm: 0 }];
  assert.deepEqual(auditPlan(house), []);
  house.openings[0].width_mm = 29800;
  assert.deepEqual(auditPlan(house), []);
  house.openings[0].width_mm = 30001;
  assert.ok(auditPlan(house).some(message => message.includes('30000 mm')));
  house.openings[0].width_mm = 6000;
  house.openings[0].height_mm = 4901;
  assert.ok(auditPlan(house).some(message => message.includes('4900 mm')));
  house.openings[0].height_mm = 300;
  house.openings[0].sill_mm = 4500;
  assert.deepEqual(auditPlan(house), []);
  house.openings[0].sill_mm = 4501;
  assert.ok(auditPlan(house).some(message => message.includes('4500 mm')));
});
