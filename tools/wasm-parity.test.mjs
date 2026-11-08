// Run from the repository root after a web wasm-pack build and:
// cargo build --release --example wasm_reference
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import init, { Engine, Pack, listParts, specOps, start } from '../crates/draftwright-wasm/pkg/draftwright_wasm.js';

await init({ module_or_path: readFileSync(new URL('../crates/draftwright-wasm/pkg/draftwright_wasm_bg.wasm', import.meta.url)) });
start(); // Also called automatically by wasm-bindgen's start hook; idempotent.
const decoder = new TextDecoder();
const root = fileURLToPath(new URL('../', import.meta.url));
function documents(engine, edit) {
  const svg = engine.renderSvg();
  assert.ok(svg instanceof Uint8Array);
  return { edit, spec: engine.spec(), plan: engine.plan(), svg: decoder.decode(svg) };
}
function equalBytes(actual, expected, label) {
  const a = Buffer.from(actual), b = Buffer.from(expected);
  if (a.equals(b)) return;
  let offset = 0;
  while (offset < Math.min(a.length, b.length) && a[offset] === b[offset]) offset++;
  assert.fail(`${label} byte ${offset}: WASM ${a.subarray(offset, offset + 100)}; native ${b.subarray(offset, offset + 100)}`);
}

function invalid(action, message) {
  assert.throws(action, error => {
    assert.equal(typeof error, 'string');
    const parsed = JSON.parse(error);
    assert.equal(parsed.code, 'invalid');
    assert.equal(typeof parsed.message, 'string');
    if (message) assert.match(parsed.message, message);
    return true;
  });
}

function unappliedDimension(engine, path) {
  const plan = JSON.parse(engine.plan());
  const warnings = plan.problems.filter(problem =>
    problem.code === 'dimension_edit_unapplied' && problem.subjects.includes(path));
  assert.equal(warnings.length, 1, `one warning naming ${path}`);
  assert.equal(warnings[0].severity, 'warning');
  assert.deepEqual(warnings[0].subjects, [path]);
  assert.equal(warnings[0].message,
    'A dimension named by the saved Spec has no current measurement; its edit is retained but cannot be applied.');
  assert.match(decoder.decode(engine.renderSvg()), /<svg/);
  return plan;
}

function replayPlan(previous, delta) {
  const plan = structuredClone(previous);
  const id = item => item.id ?? `sheet:${item.number}`;
  for (const [name, value] of Object.entries(delta.fields)) {
    if (value === null) delete plan[name];
    else plan[name] = value;
  }
  for (const [name, patch] of Object.entries(delta.collections)) {
    if (!patch.present) { delete plan[name]; continue; }
    const records = new Map((plan[name] ?? []).map(item => [id(item), item]));
    const order = patch.order ?? (plan[name] ?? []).map(id);
    for (const key of patch.removed) records.delete(key);
    for (const [key, value] of Object.entries(patch.set)) records.set(key, value);
    plan[name] = order.map(key => {
      assert.ok(records.has(key), `delta is missing ${key}`);
      const value = records.get(key);
      records.delete(key);
      return value;
    });
    assert.equal(records.size, 0, 'delta records are missing from order');
  }
  return plan;
}

for (const part of ['prismatic', 'polygonal', 'schemes']) {
  test(`${part}: WASM and native plan, spec and SVG bytes match throughout a session`, () => {
    const replay = JSON.parse(readFileSync(new URL(`../tests/fixtures/wasm/${part}.json`, import.meta.url)));
    const plain = readFileSync(new URL(`../${replay.part}`, import.meta.url));
    const bytes = replay.gzip ? gzipSync(plain) : plain;
    const engine = new Engine(replay.name, bytes);
    try {
      const snapshot = JSON.parse(engine.snapshot());
      assert.deepEqual(snapshot.plan, JSON.parse(engine.plan()));
      for (const finding of snapshot.problems) {
        assert.ok(["incomplete", "decision", "readability", "advisory"].includes(finding.impact));
        assert.equal(typeof finding.group, "string");
        assert.ok(finding.group.length > 0);
      }
      assert.deepEqual(snapshot.spec, JSON.parse(engine.spec()));
      assert.deepEqual(snapshot.placementOptions, JSON.parse(engine.placementOptions()));
      assert.deepEqual(snapshot.pmi, JSON.parse(engine.pmi()));
      assert.equal(snapshot.svg, decoder.decode(engine.renderSvg()));
      assert.ok(engine.faceCount() > 0);
      assert.ok(engine.featureCount() > 0);
      assert.ok(JSON.parse(engine.fragments()));
      invalid(() => engine.undo());
      invalid(() => engine.redo());
      // Exercise recognition correction, bound face anchors, delta replay and history on
      // the existing planar fixtures; exports and comparison tolerances stay unchanged.
      const fixtureStates = replay.steps.length + 1;
      const corrected = snapshot.analysis.features.find(feature => feature.faces.length > 0);
      assert.ok(corrected);
      replay.steps.push(
        { edit: 'suppress recognition', action: 'fragments', ops: [{ op: 'set', path: `features/${corrected.id}/suppress`, value: true }] },
        { edit: 'undo recognition', action: 'undo' },
        { edit: 'redo recognition', action: 'redo' },
      );
      const actual = [documents(engine, 'open')];
      for (const step of replay.steps) {
        const ops = JSON.stringify(step.ops ?? []);
        const before = engine.planCount();
        const previousPlan = JSON.parse(engine.plan());
        if (step.action === 'undo') JSON.parse(engine.undo());
        else if (step.action === 'redo') JSON.parse(engine.redo());
        else if (step.action === 'preview') {
          const saved = documents(engine, 'saved');
          const preview = JSON.parse(engine.preview(ops));
          assert.deepEqual(documents(engine, 'saved'), saved);
          JSON.parse(engine.apply(ops, false));
          assert.deepEqual(documents(engine, 'saved'), saved);
          assert.equal(engine.planCount(), before);
          actual.push({ edit: step.edit, spec: JSON.stringify(preview.spec), plan: JSON.stringify(preview.plan), svg: preview.svg });
          continue;
        } else if (step.action === 'spec') {
          const spec = JSON.parse(engine.spec());
          // Fixture's whole-spec edit deliberately exercises the wrapper's diff/migration.
          spec.sheet.title.material = step.ops[0].value;
          engine.setSpec(JSON.stringify(spec));
        } else if (step.action === 'fragments') {
          const packet = JSON.parse(engine.applyFragments(ops));
          assert.ok(packet.delta && packet.fragments && packet.pmi);
          assert.deepEqual(replayPlan(previousPlan, packet.delta.plan), JSON.parse(engine.plan()));
          const complete = JSON.parse(engine.fragments());
          for (const [id, svg] of Object.entries(packet.fragments)) {
            assert.equal(svg, complete[id], `changed fragment ${id}`);
          }
          if (step.ops.some(op => op.path.startsWith('views') || op.path.startsWith('dimension_schemes'))) {
            assert.deepEqual(packet.placementOptions, JSON.parse(engine.placementOptions()));
          }
          for (const [name, patch] of Object.entries(packet.delta.plan.collections)) {
            assert.equal(patch.present, Object.hasOwn(JSON.parse(engine.plan()), name));
          }
        } else JSON.parse(engine.apply(ops, true));
        assert.equal(engine.planCount() - before, 1, step.edit);
        actual.push(documents(engine, step.edit));
      }
      if (part === 'schemes') {
        // Two distinct analytic feature stations exercise real chains, baselines and
        // signed running coordinates. Keep every native/WASM byte comparison below.
        for (const state of actual.slice(1, fixtureStates - 1)) {
          const plan = JSON.parse(state.plan);
          const generated = plan.dimensions.filter(d => d.id.startsWith('d:scheme:'));
          assert.equal(generated.length, state.edit === 'undo scheme' ? 0 : 2, `${state.edit}: both feature stations remain owned unless undone`);
          assert.ok(generated.every(d => d.from_spec.includes('dimension_schemes/inspection')));
          if (state.edit === 'signed ordinates') {
            assert.ok(generated.every(d => d.value < 0 && d.placed.arrows.length === 1));
          }
        }
        // The fixture restores automatic placement before main appends recognition edits.
        // Check that restored state and every subsequent correction/history state.
        for (const state of actual.slice(fixtureStates - 1)) {
          assert.ok(!JSON.parse(state.plan).dimensions.some(d => d.id.startsWith('d:scheme:')), state.edit);
        }
      }
      const native = spawnSync(process.env.DRAFTWRIGHT_WASM_REFERENCE ?? 'target/release/examples/wasm_reference', [], {
        cwd: root, input: JSON.stringify(replay), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
      });
      assert.equal(native.status, 0, native.error?.message ?? native.stderr);
      const expected = native.stdout.trim().split('\n').map(line => JSON.parse(line));
      assert.equal(actual.length, expected.length);
      for (let i = 0; i < actual.length; i++) {
        assert.equal(actual[i].edit, expected[i].edit);
        for (const document of ['spec', 'plan', 'svg']) {
          if (i > 0 && replay.steps[i - 1].action === 'preview' && document !== 'svg') {
            // Preview embeds JSON documents in a snapshot; JS reserialization changes
            // object order and float spelling. Compare those documents structurally.
            assert.deepEqual(JSON.parse(actual[i][document]), JSON.parse(expected[i][document]));
          } else equalBytes(actual[i][document], expected[i][document], `${part}: ${actual[i].edit} ${document}`);
        }
      }
      invalid(() => engine.apply('{', true));
      invalid(() => engine.applyFragments('{'));
      invalid(() => engine.preview('{'));
      invalid(() => engine.setSpec('{'));
      invalid(() => engine.setPmi('another source', bytes));
      invalid(() => engine.setPmiOps('another source', bytes, '[]'));
      engine.setPmi(snapshot.provenance.source_sha256, bytes);
      engine.setPmiOps(snapshot.provenance.source_sha256, bytes, '[]');
      const pdf = engine.renderPdf();
      assert.ok(pdf instanceof Uint8Array);
      assert.equal(decoder.decode(pdf.subarray(0, 5)), '%PDF-');
      console.log(`${part}: ${actual.length} states, ${actual.length * 3 - 2} byte comparisons, 2 preview document comparisons`);
    } finally { engine.free(); }
  });
}
test('bad STEP is thrown as a JSON api::Error string', () => {
  invalid(() => new Engine('bad.step', new Uint8Array([0, 1, 2])));
});

test('geometry-authored dimensions replay with identical native/WASM references and ink', () => {
  const replay = JSON.parse(readFileSync(new URL('../tests/fixtures/wasm/prismatic.json', import.meta.url)));
  const engine = new Engine(replay.name, readFileSync(new URL(`../${replay.part}`, import.meta.url)));
  try {
    const refs = Object.entries(JSON.parse(engine.snapshot()).analysis.edge_ink.references)
      .filter(([, records]) => records.length === 1 && records[0].kind === 'vertex');
    const pair = refs.flatMap(p => refs.map(q => [p, q])).find(([[, [p]], [, [q]]]) =>
      Math.abs(p.point[1] - q.point[1]) < 1e-7 && Math.abs(p.point[0] - q.point[0]) > 1e-3 && Math.abs(p.point[2] - q.point[2]) > 1e-3);
    assert.ok(pair, 'principal-plane vertex diagonal');
    const ops = [{ op: 'set', path: 'dimensions/d:authored:diagonal', value: {
      between: pair.map(([key]) => key), along: 'true', view: 'front', tolerance: '±0.1',
    } }];
    replay.steps = [{ edit: 'author diagonal', action: 'apply', ops },
      { edit: 'undo authored', action: 'undo' }, { edit: 'redo authored', action: 'redo' }];
    const actual = [documents(engine, 'open')];
    for (const step of replay.steps) {
      if (step.action === 'undo') engine.undo();
      else if (step.action === 'redo') engine.redo();
      else engine.apply(JSON.stringify(step.ops), true);
      actual.push(documents(engine, step.edit));
    }
    const native = spawnSync(process.env.DRAFTWRIGHT_WASM_REFERENCE ?? 'target/release/examples/wasm_reference', [], {
      cwd: root, input: JSON.stringify(replay), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
    });
    assert.equal(native.status, 0, native.error?.message ?? native.stderr);
    const expected = native.stdout.trim().split('\n').map(line => JSON.parse(line));
    assert.equal(actual.length, expected.length);
    for (let i = 0; i < actual.length; i++) {
      for (const document of ['spec', 'plan', 'svg']) equalBytes(actual[i][document], expected[i][document], `authored ${i} ${document}`);
    }
  } finally { engine.free(); }
});

test('each sheet has its own fragments and SVG, and the PDF a page per sheet', () => {
  const engine = new Engine('prismatic.step', readFileSync(new URL('../tests/fixtures/parts/prismatic.step', import.meta.url)));
  try {
    invalid(() => engine.sheetFragments(2));
    engine.apply(JSON.stringify([{ op: 'set', path: 'sheets', value: { max: 2 } }, { op: 'set', path: 'views/iso/sheet', value: 2 }]), true);
    const views = fragments => Object.keys(JSON.parse(fragments)).filter(id => id.startsWith('v:'));
    assert.ok(!views(engine.fragments()).includes('v:iso'));
    assert.deepEqual(views(engine.sheetFragments(1)), views(engine.fragments()));
    assert.deepEqual(views(engine.sheetFragments(2)), ['v:iso']);
    assert.equal(decoder.decode(engine.sheetSvg(1)), decoder.decode(engine.renderSvg()));
    assert.match(decoder.decode(engine.sheetSvg(2)), /^<\?xml/);
    invalid(() => engine.sheetSvg(3));
    const pdf = Buffer.from(engine.renderPdf()).toString('latin1');
    assert.equal(pdf.match(/\/Type\s*\/Page\b/g)?.length, 2);
  } finally { engine.free(); }
});

// Regression for #134: a shallow ops document can construct a recursively dropped
// JSON value through its path. This used to trap and leave Engine mutably borrowed.
test('hostile edit path refuses and the same engine still answers', () => {
  const engine = new Engine('prismatic.step', readFileSync(new URL('../tests/fixtures/parts/prismatic.step', import.meta.url)));
  const before = engine.plan();
  invalid(() => engine.apply(JSON.stringify([{ op: 'set', path: `sheet/${'nested/'.repeat(20000)}value`, value: 1 }]), true), /64 components/);
  assert.equal(engine.plan(), before);
  assert.ok(engine.renderSvg().length > 0);
  engine.free();
});

// Every refusal is followed by real planning/rendering on the SAME engine. A trap
// (RuntimeError/RangeError) is not an api::Error JSON string and fails invalid().
test('hostile browser inputs refuse transactionally and leave the engine usable', () => {
  const bytes = readFileSync(new URL('../tests/fixtures/parts/prismatic.step', import.meta.url));
  const engine = new Engine('prismatic.step', bytes);
  const before = engine.plan();
  const encode = text => new TextEncoder().encode(text);
  const source = JSON.parse(engine.snapshot()).provenance.source_sha256;
  const ops = value => JSON.stringify(value);
  const set = (path, value) => ops([{ op: 'set', path, value }]);
  const truncatedRecognition = '{"format":"draftwright.recognition/1","quiddity":{';
  const cases = [
    ['malformed STEP', () => new Engine('bad.step', encode('ISO-10303-21;HEADER;broken'))],
    // No recognition JSON setter is exported: bytes on either file input must refuse it.
    ['truncated recognition JSON file', () => new Engine('recognition.json', encode(truncatedRecognition))],
    ['truncated recognition JSON as PMI', () => engine.setPmi(source, encode(truncatedRecognition))],
    ['deep STEP parameters', () => new Engine('nested.step', encode(decoder.decode(bytes).split('DATA;')[0] + 'DATA;#1=X(' + '('.repeat(20000) + '0' + ')'.repeat(20000) + ');ENDSEC;END-ISO-10303-21;'))],
    ['large malformed STEP', () => new Engine('large.step', encode('ISO-10303-21;' + ' '.repeat(8 * 1024 * 1024)))],
    ['truncated ops JSON', () => engine.apply('[{"op":"set",', true)],
    ['deep JSON', () => engine.apply('['.repeat(10000) + '0' + ']'.repeat(10000), true)],
    ['large malformed JSON', () => engine.setSpec('{"schema":"' + 'x'.repeat(8 * 1024 * 1024))],
    ['out-of-range sheets', () => engine.apply(set('sheets/max', 4294967295), true)],
    ['out-of-range lane', () => engine.apply(set('dimensions/d:envelope~1width.length/place', { lane: 4294967295 }), true)],
    ['out-of-range precision', () => engine.apply(set('precision/d:envelope~1width.length', 255), true)],
    ['out-of-range scale', () => engine.apply(set('iso_scale_factor', -1), true)],
    ['unknown annotation', () => engine.applyFragments(set('annotations/unknown/omit', true))],
    ['unknown feature', () => engine.preview(set('features/f:unknown/suppress', true))],
    ['unknown callout offset', () => engine.calloutOffset('d:unknown')],
    ['unknown label offset', () => engine.labelOffset('d:unknown')],
    ['out-of-range sheet rendering', () => engine.sheetSvg(4294967295)],
    ['out-of-range sheet fragments', () => engine.sheetFragments(4294967295)],
  ];
  try {
    for (const [label, action] of cases) {
      invalid(action);
      assert.equal(engine.plan(), before, `${label}: refusal is transactional`);
      const preview = JSON.parse(engine.preview(set('sheet/title/material', 'Steel')));
      assert.equal(preview.spec.sheet.title.material, 'Steel', `${label}: next valid edit`);
      assert.match(preview.svg, /<svg/);
      assert.ok(JSON.parse(engine.placementOptions()));
      console.log(`hostile input refused; next edit/render succeeded: ${label}`);
    }
  } finally { engine.free(); }
});

test('an unknown dimension omit is retained with a path-specific warning and leaves the drawing intact', () => {
  const bytes = readFileSync(new URL('../tests/fixtures/parts/prismatic.step', import.meta.url));
  for (const method of ['apply', 'setSpec', 'openSpec']) {
    const engine = new Engine('prismatic.step', bytes);
    try {
      const before = JSON.parse(engine.plan());
      const dimensions = { 'd:unknown': { omit: true } };
      const saved = JSON.stringify({ dimensions });
      if (method === 'apply') {
        engine.apply(JSON.stringify([{ op: 'set', path: 'dimensions/d:unknown/omit', value: true }]), true);
      } else if (method === 'setSpec') {
        engine.setSpec(saved);
      } else {
        const opened = JSON.parse(engine.openSpec(saved));
        assert.deepEqual(opened.set_aside, []);
        assert.deepEqual(opened.spec, JSON.parse(engine.spec()));
      }
      assert.deepEqual(JSON.parse(engine.spec()).dimensions, dimensions, method);
      const plan = unappliedDimension(engine, 'dimensions/d:unknown');
      assert.deepEqual(plan.dimensions, before.dimensions, `${method}: no invented measurement`);
      assert.deepEqual(plan.views, before.views, `${method}: drawing retained`);
    } finally { engine.free(); }
  }
});

// On alpha.10 this setting reached Vec capacity_overflow, trapped with
// RuntimeError: unreachable, and left the engine's mutable borrow held.
test('a huge view gutter returns a refusal instead of aborting WASM', () => {
  const engine = new Engine('prismatic.step', readFileSync(new URL('../tests/fixtures/parts/prismatic.step', import.meta.url)));
  const before = engine.plan();
  const spec = JSON.parse(engine.spec());
  spec.sheet.view_gutter = 1e308;
  invalid(() => engine.setSpec(JSON.stringify(spec)), /view_gutter.*1189/);
  assert.equal(engine.plan(), before);
  engine.apply('[{"op":"set","path":"sheet/title/material","value":"Steel"}]', true);
  assert.equal(JSON.parse(engine.spec()).sheet.title.material, 'Steel');
  assert.ok(engine.renderSvg().length > 0);
  engine.free();
});


test('group callout members match native and the dragged arrow follows the nearest member', () => {
  const part = 'tests/fixtures/samples/editor-plate.step';
  const name = 'editor-plate.step';
  const id = 'd:f:hole_pattern:1/bore.diameter';
  const engine = new Engine(name, readFileSync(new URL(`../${part}`, import.meta.url)));
  try {
    const count = engine.planCount();
    const at = JSON.parse(engine.calloutOffset(id));
    assert.equal(engine.planCount(), count);
    assert.equal(at.members.length, 6);
    for (const m of at.members) {
      assert.equal(m.centre.length, 2);
      assert.ok(m.centre.every(Number.isFinite));
      assert.ok(m.radius > 0);
      assert.ok(!Object.hasOwn(m, 'span'));
    }
    const ops = [{ op: 'set', path: `dimensions/${id.replaceAll('~', '~0').replaceAll('/', '~1')}/place`,
      value: { offset: [90, 0], pinned: true } }];
    engine.apply(JSON.stringify(ops), true);
    const moved = JSON.parse(engine.calloutOffset(id));
    assert.deepEqual(moved.members, at.members);
    assert.deepEqual(moved.tip, at.tip);
    const p = JSON.parse(engine.plan()).dimensions.find(d => d.id === id).placed;
    const [tip, elbow] = p.lines[0];
    const nearest = at.members.toSorted((a, b) =>
      Math.hypot(a.centre[0] - elbow[0], a.centre[1] - elbow[1]) - a.radius -
      (Math.hypot(b.centre[0] - elbow[0], b.centre[1] - elbow[1]) - b.radius))[0];
    assert.equal(nearest.centre[0], Math.max(...at.members.map(m => m.centre[0])));
    assert.ok(Math.abs(Math.hypot(tip[0] - nearest.centre[0], tip[1] - nearest.centre[1]) - nearest.radius) < 1e-8);
    const native = spawnSync(process.env.DRAFTWRIGHT_WASM_REFERENCE ?? 'target/release/examples/wasm_reference', [], {
      cwd: root, input: JSON.stringify({ name, part, steps: [
        { edit: id, action: 'callout_offset' },
        { edit: 'drag', action: 'apply', ops },
        { edit: id, action: 'callout_offset' },
      ] }), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
    });
    assert.equal(native.status, 0, native.error?.message ?? native.stderr);
    const rows = native.stdout.trim().split('\n').map(line => JSON.parse(line));
    assert.deepEqual(at, rows[1]);
    assert.deepEqual(moved, rows[3]);
  } finally { engine.free(); }
});

test('openSpec retains stale dimension edits, migrates printer settings and sets aside refused views', () => {
  const bytes = readFileSync(new URL('../tests/fixtures/parts/mounting_plate.step', import.meta.url));
  const engine = new Engine('mounting_plate.step', bytes);
  const direct = new Engine('mounting_plate.step', bytes);
  try {
    const dimensions = {
      'd:f:hole:4/bore.diameter': { omit: true }, 'd:f:hole:1/bore.diameter': { omit: true } };
    const saved = JSON.stringify({ sheet: { preferred_printer: 'A3' }, dimensions });
    const before = engine.plan();
    invalid(() => engine.setSpec(saved), /preferred_printer.*preferred_max_size/);
    assert.equal(engine.plan(), before, 'obsolete printer field refuses transactionally');
    const opened = JSON.parse(engine.openSpec(saved));
    assert.deepEqual(opened.set_aside, []);
    assert.deepEqual(opened.spec, JSON.parse(engine.spec()));
    assert.deepEqual(opened.spec.dimensions, dimensions);
    assert.equal(opened.spec.sheet.preferred_max_size, 'A3');
    const plan = unappliedDimension(engine, 'dimensions/d:f:hole:1~1bore.diameter');
    assert.ok(!plan.dimensions.some(dimension => dimension.id === 'd:f:hole:1/bore.diameter'));
    assert.ok(plan.omitted.some(item => item.id === 'd:f:hole:4/bore.diameter'
      && item.state.kind === 'omitted_by_user'), 'the live dimension omit is applied');
    // A fresh engine proves setSpec validates the same stale entry; this is not a no-op.
    direct.setSpec(JSON.stringify({ sheet: { preferred_max_size: 'A3' }, dimensions }));
    assert.deepEqual(JSON.parse(direct.spec()), opened.spec);
    assert.deepEqual(unappliedDimension(direct, 'dimensions/d:f:hole:1~1bore.diameter'), plan);
    const unknownView = JSON.stringify({ ...opened.spec, views: { ...opened.spec.views, gone: { omit: true } } });
    const refusal = (() => { try { engine.setSpec(unknownView); } catch (e) { return JSON.parse(e); } })();
    assert.equal(refusal.code, 'invalid');
    assert.equal(refusal.path, 'views/gone');
    assert.deepEqual(JSON.parse(engine.spec()), opened.spec, 'unknown view refusal is transactional');
    const recovered = JSON.parse(engine.openSpec(unknownView));
    assert.deepEqual(recovered.set_aside, [{ path: 'views/gone', entry: { omit: true }, reason_code: 'no_view' }]);
    assert.deepEqual(recovered.spec, opened.spec);
    assert.deepEqual(unappliedDimension(engine, 'dimensions/d:f:hole:1~1bore.diameter'), plan);
    assert.deepEqual(JSON.parse(specOps(JSON.stringify({ schema: 'a', x: { 'b/c': 1, d: 1 } }), JSON.stringify({ schema: 'b', x: { 'b/c': [2] } }))),
      [{ op: 'set', path: 'x/b~1c', value: [2] }, { op: 'remove', path: 'x/d' }]);
    invalid(() => specOps('{', '{}'));
  } finally { engine.free(); direct.free(); }
});


test('Pack: gzip assembly, native equality, pages, transactional rebuild and disposal', () => {
  const part = 'tests/fixtures/assembly/pack-small.step.gz';
  const bytes = readFileSync(new URL(`../${part}`, import.meta.url));
  const pack = new Pack(part, bytes);
  const native = (op, extra = {}) => {
    const result = spawnSync(process.env.DRAFTWRIGHT_BIN ?? 'target/release/draftwright', ['serve'], {
      cwd: root, input: JSON.stringify({op, step: part, ...extra}) + '\n', encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
    });
    assert.equal(result.status, 0, result.error?.message ?? result.stderr);
    const reply = JSON.parse(result.stdout);
    assert.equal(reply.ok, true, JSON.stringify(reply));
    return reply.result;
  };
  try {
    assert.deepEqual(JSON.parse(listParts(part, bytes)), native('parts'));
    const report = JSON.parse(pack.reportJson());
    assert.equal(report.schema, 'draftwright.pack/1');
    assert.equal(report.general_assembly.sheet, 1);
    assert.equal(pack.pageCount(), report.pages);
    for (let page = 1; page <= pack.pageCount(); page++) {
      const svg = pack.pageSvg(page);
      assert.equal(typeof svg, 'string');
      assert.match(svg, /<svg/);
      assert.ok(svg.includes(`>${page}/${report.pages}<`));
    }
    for (const row of report.rows) {
      for (const page of row.sheets) {
        assert.ok(pack.pageSvg(page).includes(`>${row.name}<`));
      }
    }
    invalid(() => pack.pageSvg(0));
    invalid(() => pack.pageSvg(pack.pageCount() + 1));
    assert.equal(pack.pageSvg(1.9), pack.pageSvg(1)); // wasm-bindgen truncates to u32
    invalid(() => pack.pageSvg(-1));
    assert.equal(pack.pageSvg(-4294967295), pack.pageSvg(1)); // modulo 2^32

    const reference = native('pack');
    assert.deepEqual(report, reference.report);
    for (const file of reference.files) {
      const actual = file.name.endsWith('.pdf') ? pack.pdf() :
        new TextEncoder().encode(file.name.endsWith('.csv') ? pack.csv() : pack.reportJson());
      equalBytes(actual, Buffer.from(file.blob, 'base64'), file.name);
    }
    const before = pack.reportJson(), spec = pack.specJson();
    invalid(() => pack.setSpec('{'));
    invalid(() => pack.setSpec('{"schema":"wrong"}'));
    assert.equal(pack.reportJson(), before);
    assert.equal(pack.specJson(), spec);
    pack.setSpec('{"ga":{"include":false}}');
    assert.equal(JSON.parse(pack.specJson()).ga.include, false);
    assert.equal(pack.pageCount(), report.pages - 1);
    assert.deepEqual(JSON.parse(pack.reportJson()), native('pack', {spec: {ga: {include: false}}}).report);
  } finally { pack.free(); }
  invalid(() => new Pack(part, bytes, '{"schema":"wrong"}'));
  invalid(() => listParts('bad.step', new Uint8Array([1, 2, 3])));
});

// Migration packets are the browser editor's contract; compare native and WASM structurally.
test('openSpec migration report has native parity', () => {
  const replay = JSON.parse(readFileSync(new URL('../tests/fixtures/wasm/prismatic.json', import.meta.url)));
  const engine = new Engine(replay.name, readFileSync(new URL(`../${replay.part}`, import.meta.url)));
  try {
    const dimension = JSON.parse(engine.plan()).dimensions.find(d => d.id.startsWith('d:envelope/'));
    assert.ok(dimension);
    const old = `${dimension.id}@front`;
    const spec = {schema: 'draftwright.spec/1', dimensions: {[old]: {omit: true}}};
    const opened = JSON.parse(engine.openSpec(JSON.stringify(spec)));
    assert.deepEqual(opened.migrated, [{path: `dimensions/${old.replaceAll('/', '~1')}`, from: old, to: dimension.id}]);
    assert.equal(typeof opened.ids, 'object');
    const native = spawnSync(process.env.DRAFTWRIGHT_WASM_REFERENCE ?? 'target/release/examples/wasm_reference', [], {
      cwd: root, input: JSON.stringify({...replay, steps: [{edit: 'migration', action: 'open_spec', spec}]}), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
    });
    assert.equal(native.status, 0, native.stderr);
    assert.deepEqual(opened, JSON.parse(native.stdout.trim().split('\n').at(-1)));
  } finally { engine.free(); }
});
