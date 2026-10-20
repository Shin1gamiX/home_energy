'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const css = fs.readFileSync(path.join(__dirname, '../interface.css'), 'utf8');
for (const selector of ['.quick-series button', '.history-workspace .peak-row button']) {
  const rule = css.slice(css.indexOf(`${selector} {`)).split('}')[0];
  assert.match(rule, /padding: 10px 12px/);
  assert.match(rule, /min-height: 44px/);
  assert.match(rule, /border-radius: 8px/);
}
assert.match(css, /\.quick-series button::before \{[^}]*flex: none/);
assert.doesNotMatch(css, /\.quick-series button \{[^}]*padding: 5px 2px/);
assert.doesNotMatch(css, /\.peak-row button \{[^}]*padding: 8px 0/);
assert.match(css, /\.chart-views \{ width: 100%; flex-wrap: wrap;/);
assert.match(css, /\.chart-views button \{[^}]*overflow-wrap: normal;/);
assert.match(css, /\.history-workspace \.mode-controls button \{ min-height: 44px; padding: 8px 12px;/);
console.log('Control spacing: padded peaks/series, stable swatches, 44px targets and wrapping chart tabs passed.');
