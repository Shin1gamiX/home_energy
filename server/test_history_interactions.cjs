const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../history.js'), 'utf8');
function read(name, context = {}) {
  const start = source.indexOf(`function ${name}(`), end = source.indexOf('\n}', start) + 2;
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(source.slice(start, end) + `\nresult = ${name};`, context);
  return context.result;
}
const refresh = {hasError:false, retryAt:12000, nextRefreshAt:300000};
const wait = read('refreshWait', refresh);
assert.equal(wait(10000), 290);
refresh.hasError = true;
assert.equal(wait(10000), 2); assert.equal(wait(12000), 0, 'Recovery is not blocked by an earlier successful refresh');

const focus = [];
const button = {dataset:{historyFocus:'Zoom in'}, disabled:false, focus:() => focus.push('button')};
const fallback = {focus:() => focus.push('chart')};
const old = {dataset:{historyFocus:'Zoom in'}, isConnected:true, closest:() => ({id:'chart-supply'})};
const context = {document:{activeElement:old, getElementById:() => ({querySelectorAll:() => [button], querySelector:() => fallback})},
  renderHistoryContext() {}, renderView() {old.isConnected = false;} };
const render = read('render', context);
render(); assert.equal(focus.at(-1), 'button');
button.disabled = true; render(); assert.equal(focus.at(-1), 'chart', 'Disabled reset falls back to its chart, not body');
context.document.activeElement = {isConnected:true, dataset:{}, closest:() => null};
render(); assert.equal(focus.length, 2, 'Unrelated controls do not lose focus');
assert.doesNotMatch(source, /tip\.setAttribute\('aria-live'/, 'Synchronized charts share one announcement surface');
const css = fs.readFileSync(path.join(__dirname, '../history.css'), 'utf8');
assert.match(css, /#history-inspection\s*\{[^}]*position:\s*absolute;[^}]*clip-path:\s*inset\(50%\)/,
  'The live announcement stays available to assistive technology without adding visible text');
console.log('History focus, single hidden announcement and retry recovery passed.');
