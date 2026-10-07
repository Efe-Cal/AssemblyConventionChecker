const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {parseReport, sourceRange, RequestLedger} = require('../out/host/model');
const {CheckerRuntime, Cancelled, runProcess} = require('../out/host/runtime');
const bundle = path.resolve(__dirname, '../python');
const runtime = new CheckerRuntime(bundle, () => {});
const python = process.env.ACC_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const check = (source, entries=[]) => runtime.analyze(source, entries, python, new AbortController().signal);

test('Python buffer analysis preserves existing errors and related locations', async () => {
  const {report} = await check('.globl f\nf: movq $42,%rbx\ncall helper\nstd\nret\n');
  assert.equal(report.schema_version, 1);
  assert.deepEqual(new Set(report.diagnostics.map(d=>d.rule_id)), new Set(['ABI_STACK_ALIGNMENT','ABI_CALLEE_SAVED','ABI_DIRECTION_FLAG']));
  assert.ok(report.diagnostics.some(d=>d.related_locations.length));
  assert.equal(report.filename,'<stdin>');
});
test('clean unsaved source and Unicode pipe content', async () => {
  const {report} = await check('# 😀 π\n.globl f\nf: ret\n');
  assert.equal(report.complete,true); assert.deepEqual(report.diagnostics,[]);
});
test('exit status 2 carries input diagnostics rather than process failure', async () => {
  const {report} = await check('.globl f\nf: movq %rax,\n');
  assert.equal(report.input_errors,true); assert.ok(report.diagnostics.length);
});
test('entry selections flow through the existing CLI', async () => {
  const source = '.globl f,g\nf: ret\ng: std; ret\n';
  const {report} = await check(source,['f']);
  assert.deepEqual(report.functions.map(f=>f.name),['f']); assert.equal(report.diagnostics.length,0);
  const missing = await check(source,['missing']); assert.equal(missing.report.input_errors,true);
});

test('includes preserve their locations and use unsaved source buffers', async () => {
  const root = path.resolve(__dirname, '../../checker/tests/fixtures/user_programs/snake/main.s');
  const child = path.join(path.dirname(root), 'handle_terminal.s');
  const source = '.include "handle_terminal.s"\n.globl f\nf: subq $8,%rsp; call helper; addq $8,%rsp; ret\n';
  const {report, sources} = await runtime.analyze(source, [], python, new AbortController().signal, root, {[child]: 'helper: std; ret\n'});
  assert.deepEqual(report.functions.map(f => f.name), ['helper', 'f']);
  assert.equal(report.diagnostics[0].location.filename, child);
  assert.equal(report.diagnostics[0].location.line, 1);
  assert.equal(sources[child], 'helper: std; ret\n');
  assert.equal(report.filename, root);
});
test('missing interpreter gives actionable setup failure', async () => {
  await assert.rejects(runtime.discover(path.join(bundle,'definitely-missing-python')), /selected executable.*Python 3\.11/);
});
test('malformed, future, and ambiguous report envelopes are rejected', async () => {
  const {report} = await check('.globl f\nf: ret\n');
  for (const value of ['no json', JSON.stringify({schema_version:2,reports:[report]}), JSON.stringify({schema_version:1,reports:[report,report]}), JSON.stringify({schema_version:1,reports:[{...report,functions:[{...report.functions[0],analyzed_instructions:-1}]}]})]) {
    assert.throws(()=>parseReport(value), /invalid or unsupported/);
  }
});
test('malformed diagnostic categories and locations are rejected', async () => {
  const {report} = await check('.globl f\nf: std; ret\n');
  for (const change of [{category:'success'}, {location:{filename:'x',line:0,column:1}}, {related_locations:[{}]}]) {
    assert.throws(()=>parseReport(JSON.stringify({schema_version:1,reports:[{...report,diagnostics:[{...report.diagnostics[0],...change}]}]})));
  }
});
test('one-based Unicode columns become UTF-16 editor ranges; tabs remain one character', () => {
  assert.deepEqual(sourceRange('😀\tcall helper',{line:1,column:3}),{line:0,start:3,end:7});
  assert.deepEqual(sourceRange('label: ret\r\n',{line:1,column:8}),{line:0,start:7,end:10});
  assert.deepEqual(sourceRange('ret',{line:999,column:999}),{line:0,start:3,end:3});
});
test('generation guards discard older results, same-version reruns, and closed documents', () => {
  const ledger=new RequestLedger(); const a=ledger.begin('a',1), b=ledger.begin('a',2);
  assert.equal(ledger.accepts('a',a,1),false); assert.equal(ledger.accepts('a',b,2),true);
  const c=ledger.begin('a',2); assert.equal(ledger.accepts('a',b,2),false); assert.equal(ledger.accepts('a',c,2),true);
  ledger.invalidate('a'); assert.equal(ledger.accepts('a',c,2),false);
});
test('superseded subprocesses are cancelled', async () => {
  const abort=new AbortController();
  const promise=runProcess(process.execPath,['-e','setInterval(()=>{},1000)'],'',abort.signal);
  setTimeout(()=>abort.abort(),60);
  await assert.rejects(promise,Cancelled);
});
test('timeout terminates an unresponsive checker', async () => {
  await assert.rejects(runProcess(process.execPath,['-e','setInterval(()=>{},1000)'],'',undefined,60),/exceeded/);
});
test('UTF-8 boundaries in process output are preserved', async () => {
  const result=await runProcess(process.execPath,['-e',"const b=Buffer.from('😀');process.stdout.write(b.subarray(0,2));setTimeout(()=>process.stdout.write(b.subarray(2)),20)"]);
  assert.equal(result.stdout,'😀');
});
