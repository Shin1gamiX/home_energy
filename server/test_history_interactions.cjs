const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {findingHistoryLink} = require('../insights.js');
const source = fs.readFileSync(path.join(__dirname, '../history.js'), 'utf8');
function read(name, context = {}) {
  const start = source.indexOf(`function ${name}(`), end = source.indexOf('\n}', start) + 2;
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(source.slice(start, end) + `\nresult = ${name};`, context);
  return context.result;
}
const parse = read('parseHistoryLink', {URLSearchParams});
const from = Date.UTC(2026, 9, 16, 19, 31) / 1000, to = from + 74 * 60, now = to + 86400;
for (const [metric, expected] of [['load','load'], ['pv','pv'], ['soc_max','soc'], [null,'mode'], ['battery_charged','battery']]) {
  const finding = {metric, start: from, end: to}, original = JSON.stringify(finding);
  const link = findingHistoryLink(finding), parsed = parse(new URL(link, 'https://home.example').search, now);
  assert.equal(parsed.metric, expected); assert.equal(parsed.from, from); assert.equal(parsed.to, to);
  assert.equal(JSON.stringify(finding), original, 'Evidence must not be mutated');
}
for (const bad of ['__proto__', 'constructor', 'javascript:alert(1)', 'load&to=1', '<script>']) {
  assert.equal(findingHistoryLink({metric: bad, start: from, end: to}), null);
  assert.equal(parse(`?from=${from}&to=${to}&metric=${encodeURIComponent(bad)}`, now), null);
}
for (const query of ['', '?from=1&to=2&metric=load', `?from=${to}&to=${from}&metric=load`,
  `?from=${from}&to=${from + 32 * 86400}&metric=load`, `?from=${from}&from=${from}&to=${to}&metric=load`,
  `?from=${from}&to=NaN&metric=load`, `?from=${now+1}&to=${now+120}&metric=load`]) assert.equal(parse(query, now), null);
assert.equal(parse(`?from=${from}&to=${now+120}&metric=load`, now).to, now);
assert.equal(findingHistoryLink({metric:'load', start:from, end:from}), null);
const athensDay = read('athensDay', {timezone:'Europe/Athens'});
assert.equal(athensDay(Date.UTC(2026, 9, 16, 22) / 1000), '2026-10-17');
assert.equal(athensDay(Date.UTC(2026, 9, 25, 22) / 1000), '2026-10-26', 'Athens winter offset');

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
console.log('History links: bounds, metric allowlist, exact intervals, Athens dates, immutable evidence; focus and retry recovery passed.');
