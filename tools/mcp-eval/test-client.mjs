import { checkEfficiency } from './check-efficiency.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { serverUrl } from './client.mjs';

test('server startup waits for a complete line across every chunk boundary', () => {
  const line = 'draftwright MCP listening on http://127.0.0.1:43210/mcp\n';
  for (let end = 0; end < line.length; end++) {
    assert.equal(serverUrl(line.slice(0, end)), undefined, `prefix ${end}`);
    assert.equal(serverUrl(line.slice(0, end) + line.slice(end)), 'http://127.0.0.1:43210/mcp');
  }
  assert.equal(serverUrl(line.replace('\n', '\r\n') + 'later log\n'), 'http://127.0.0.1:43210/mcp');
});

const run = args => new Promise(done => {
  const child = spawn(process.execPath, ['tools/mcp-eval/call.mjs', ...args]);
  let stdout = '', stderr = '';
  child.stdout.on('data', x => stdout += x);
  child.stderr.on('data', x => stderr += x);
  child.on('exit', code => done({ code, stdout, stderr }));
});

test('one call logs exact UTF-8 wire and logical sizes, including refusals', async () => {
  const dir = mkdtempSync(resolve(tmpdir(), 'd1-client-test-'));
  const received = [];
  const server = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    assert.equal(req.headers.accept, 'application/json, text/event-stream');
    const request = JSON.parse(body);
    const text = JSON.stringify(request.params.arguments.fail ? { error: { message: 'refused' } } : { label: '⌀8 ±0.02' });
    const reply = JSON.stringify({ jsonrpc: '2.0', id: request.id, result: {
      isError: !!request.params.arguments.fail, content: [{ type: 'text', text }], structuredContent: JSON.parse(text)
    } });
    received.push({ body, reply, text, args: request.params.arguments });
    res.setHeader('Content-Type', 'application/json');
    res.end(reply);
  });
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  try {
    const common = ['--url', `http://127.0.0.1:${server.address().port}/mcp`, '--log', resolve(dir, 'calls.jsonl'), '--task', 'demo'];
    const ok = await run(['get_item', '{"id":"⌀8"}', ...common]);
    assert.equal(ok.code, 0, ok.stderr);
    assert.deepEqual(JSON.parse(ok.stdout), { label: '⌀8 ±0.02' });
    const bad = await run(['get_item', '{"fail":true}', ...common]);
    assert.equal(bad.code, 2, bad.stderr);
    const entries = readFileSync(resolve(dir, 'calls.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(received.length, 2);
    for (const [i, entry] of entries.entries()) {
      assert.equal(entry.task, 'demo');
      assert.equal(entry.method, 'tools/call');
      assert.equal(entry.wire.request, Buffer.byteLength(received[i].body));
      assert.equal(entry.wire.response, Buffer.byteLength(received[i].reply));
      assert.equal(entry.content.request, Buffer.byteLength(JSON.stringify(received[i].args)));
      assert.equal(entry.content.response, Buffer.byteLength(received[i].text));
      assert.equal(entry.isError, i === 1);
    }
  } finally {
    await new Promise(done => server.close(done));
    rmSync(dir, { recursive: true, force: true });
  }
});

test('checker scores unchanged task conditions and fails a drawn hidden width', async () => {
  const dir = mkdtempSync(resolve(tmpdir(), 'd1-check-test-'));
  const width = { id: 'd:envelope/width.length' }, hole = { id: 'd:f:hole:1/bore.diameter' };
  const files = { spec: { dimensions: { 'd:envelope/width.length': { omit: true } } },
    baseline: { dimensions: [width, hole] }, plan: { dimensions: [hole] } };
  const judge = () => new Promise(done => {
    const child = spawn('tools/mcp-eval/check', ['m10127-hide-width', ...Object.keys(files).flatMap(key => [`--${key}`, resolve(dir, `${key}.json`)])]);
    let stdout = '', stderr = '';
    child.stdout.on('data', x => stdout += x); child.stderr.on('data', x => stderr += x);
    child.on('error', e => done({ code: -1, stderr: e.message }));
    child.on('exit', code => done({ code, stdout, stderr }));
  });
  try {
    for (const [key, doc] of Object.entries(files)) writeFileSync(resolve(dir, `${key}.json`), JSON.stringify(doc));
    let result = await judge();
    assert.equal(result.code, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).success, true);
    writeFileSync(resolve(dir, 'plan.json'), JSON.stringify(files.baseline));
    result = await judge();
    assert.equal(result.code, 1, result.stderr);
    assert.equal(JSON.parse(result.stdout).success, false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('real HTTP adapter accepts a call and checker scores final and initial versions', async () => {
  const dir = mkdtempSync(resolve(tmpdir(), 'd1-live-test-'));
  const child = spawn(resolve(process.env.DRAFTWRIGHT_BINARY ?? 'target/release/draftwright'), ['mcp-http', '127.0.0.1:0'],
    { env: { ...process.env, DRAFTWRIGHT_CACHE: resolve(dir, 'cache') }, stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    const url = await new Promise((done, fail) => {
      let stderr = '';
      child.stderr.on('data', text => { stderr += text; const url = serverUrl(stderr); if (url) done(url); });
      child.on('error', fail); child.on('exit', code => fail(new Error(`server exited ${code}: ${stderr}`)));
    });
    const common = ['--url', url, '--log', resolve(dir, 'calls.jsonl'), '--task', 'plate-a4-title-no-iso'];
    const initialCall = await run(['inspect_part', JSON.stringify({ step: resolve('tests/fixtures/parts/mounting_plate.step'), drawing_ref: 'plate-a4-title-no-iso' }), ...common]);
    assert.equal(initialCall.code, 0, initialCall.stderr);
    const initial = JSON.parse(initialCall.stdout);
    const ops = [['views/iso/omit', true], ['sheet/size', 'A4'], ['sheet/scale', '1:1'],
      ['sheet/title/title', 'MOUNTING PLATE'], ['sheet/title/number', 'DW-0001']].map(([path, value]) => ({ op: 'set', path, value }));
    const applied = await run(['apply_ops', JSON.stringify({ part_ref: initial.part_ref, parent: initial.spec_version, drawing_ref: 'plate-a4-title-no-iso', ops }), ...common]);
    assert.equal(applied.code, 0, applied.stderr);
    const final = JSON.parse(applied.stdout);
    const judge = (version, part = initial.part_ref) => new Promise(done => {
      const check = spawn('tools/mcp-eval/check', ['plate-a4-title-no-iso', '--url', url, '--part-ref', part, '--spec-version', version]);
      let stdout = '', stderr = '';
      check.stdout.on('data', x => stdout += x); check.stderr.on('data', x => stderr += x);
      check.on('error', e => done({ code: -1, stderr: e.message }));
      check.on('exit', code => done({ code, stdout, stderr }));
    });
    let verdict = await judge(final.spec_version);
    assert.equal(verdict.code, 0, verdict.stderr);
    assert.equal(JSON.parse(verdict.stdout).success, true);
    verdict = await judge(initial.spec_version);
    assert.equal(verdict.code, 1, verdict.stderr);
    assert.equal(JSON.parse(verdict.stdout).success, false);
    verdict = await judge(final.spec_version, '0'.repeat(64) + '-' + initial.part_ref.split('-')[1]);
    assert.equal(verdict.code, 2);
    assert.match(verdict.stderr, /part_ref must name plate/);
    const imagePath = resolve(dir, 'sheet.png');
    const image = await run(['view_sheet', JSON.stringify({ part_ref: initial.part_ref, spec_version: final.spec_version, dpi: 30, region: [10, 10, 30, 30] }), '--image-out', imagePath, ...common]);
    assert.equal(image.code, 0, image.stderr);
    assert.deepEqual(readFileSync(imagePath).subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    assert.equal(readFileSync(resolve(dir, 'calls.jsonl'), 'utf8').trim().split('\n').length, 3, 'judge must not inflate model call counts');
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const ended = new Promise(done => child.once('exit', done)); child.kill(); await ended;
    }
    rmSync(dir, { recursive: true, force: true });
  }
});


test('efficiency gate requires fewer calls and retains every previous success', () => {
  const before = JSON.parse(readFileSync('tools/mcp-eval/s7-result.json', 'utf8'));
  assert.equal(checkEfficiency(before, before).pass, false);
  const after = structuredClone(before);
  const edited = after.results.find(result => result.success && result.tool_calls > 1);
  edited.calls.pop(); edited.tool_calls--;
  assert.equal(checkEfficiency(before, after).pass, true);
  const sequential = after.results.find(result => result.task === 'grm03-sheet-scale-keep-intent');
  const applyIndex = sequential.calls.findIndex(call => call.tool === 'apply_ops');
  const removed = sequential.calls.splice(applyIndex, 1)[0]; sequential.tool_calls--;
  assert.equal(checkEfficiency(before, after).pass, false, 'batching must not weaken keep-intent');
  sequential.calls.splice(applyIndex, 0, removed); sequential.tool_calls++;
  edited.success = false;
  assert.equal(checkEfficiency(before, after).pass, false);
  edited.success = true;
  after.results.push(structuredClone(edited));
  assert.equal(checkEfficiency(before, after).pass, false);
});

test('scripted sheet change follows the committed tolerance and omission', async () => {
  const { keepIntent } = await import('./scripted-ops.mjs');
  const batches = [];
  let committed = false;
  await keepIntent(async ops => {
    if (batches.length) assert.equal(committed, true);
    batches.push(ops);
    await Promise.resolve();
    committed = true;
  }, 'd:f:turned_step:f2/step.diameter');
  assert.deepEqual(batches.map(ops => ops.map(op => op.path)), [
    ['dimensions/d:f:turned_step:f2~1step.diameter/tolerance', 'views/iso/omit'],
    ['sheet/size', 'sheet/scale'],
  ]);
  await assert.rejects(keepIntent(async () => { throw new Error('first commit refused'); }, 'd:test'), /first commit refused/);
});

test('scripted plate request requires every task callout despite a shortened index', async () => {
  const { pinCallouts } = await import('./scripted-ops.mjs');
  const task = JSON.parse(readFileSync('tools/mcp-eval/tasks.json', 'utf8')).tasks[0];
  const items = task.success.filter(c => c.type === 'placed').map(c => ({ id: 'd:' + c.quantity }));
  const ops = pinCallouts(task, items);
  assert.equal(ops.length, 3);
  assert.deepEqual(ops.map(op => op.value.lane), [0, 1, 2]);
  for (let i = 0; i < items.length; i++) {
    assert.throws(() => pinCallouts(task, items.filter((_, n) => n !== i)), /missing required callout/);
  }
});

test('report-only extension repair is an explicit script edit derived from geometry', async () => {
  const { oppositeExtension } = await import('./scripted-ops.mjs');
  const item = { item: { id: 'd:test/span', view: 'v:plan' },
    placement: { kind: 'dimension', geometry: { rotation: 0, lines: [[[1,2],[1,8]]] } } };
  assert.deepEqual(oppositeExtension(item, 0), { op: 'set', path: 'dimensions/d:test~1span/place',
    value: { view: 'plan', side: 'below', lane: 0 } });
  item.placement.geometry.rotation = 90;
  item.placement.geometry.lines = [[[8,2],[1,2]]];
  assert.equal(oppositeExtension(item, 0).value.side, 'right');
  assert.throws(() => oppositeExtension(item, 1), /line is missing/);
  assert.throws(() => oppositeExtension({ item: item.item }, 0), /no dimension geometry/);
});
