'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { validReport, validateFeed, localizedText, localizeReport, readBoundedJson, dateTime, MAX_BYTES } = require('../insights.js');
const translated = (en, el, ru) => ({ en, el, ru });

function report(id = 'a', published = 1791889200) {
  return {
    id: id.repeat(64), published_at: published,
    analysis: {
      schema_version: 2, generated_at: published - 30,
      window: { start: published - 86460, end: published - 60 },
      baseline: { start: published - 691260, end: published - 86460, days: 7 },
      data_through: published - 120, coverage_pct: 96.2, severity: 'notice',
      metrics: [{ key: 'solar', label: translated('Solar generated', 'Ηλιακή παραγωγή', 'Солнечная выработка'), unit: 'kWh', value: 8.4, baseline: 16.8,
        change_pct: -50, coverage_pct: 96.2, baseline_days: 7 }],
      findings: [{ id: 'solar-low', category: 'solar', severity: 'notice', title: translated('Lower solar generation', 'Χαμηλότερη ηλιακή παραγωγή', 'Снижение солнечной выработки'),
        observation: translated('Recorded solar generation was below the comparison baseline.', 'Η καταγεγραμμένη ηλιακή παραγωγή ήταν κάτω από τη βάση σύγκρισης.', 'Записанная солнечная выработка была ниже базы сравнения.'), start: published - 86460,
        end: published - 60, metric: 'solar' }],
      limitations: [translated('Minute averages may hide short events.', 'Οι μέσοι όροι ανά λεπτό μπορεί να κρύβουν σύντομα συμβάντα.', 'Средние значения за минуту могут скрывать кратковременные события.')]
    },
    narrative: { summary: translated('Solar generation was lower than the recent baseline.', 'Η ηλιακή παραγωγή ήταν χαμηλότερη από τη βάση σύγκρισης.', 'Солнечная выработка была ниже недавней базы сравнения.'), notes: [{ finding_id: 'solar-low', commentary: translated('No weather observations were used.', 'Δεν χρησιμοποιήθηκαν μετεωρολογικές παρατηρήσεις.', 'Метеонаблюдения не использовались.') }] }
  };
}

async function main() {
  assert.equal(validReport(report()), true);
  assert.deepEqual(validateFeed({ schema_version: 2, reports: [] }), []);
  const older = report('a', 1791802800), newer = report('b');
  older.analysis.severity = 'warning'; newer.analysis.severity = 'routine';
  assert.deepEqual(validateFeed({ schema_version: 2, reports: [older, newer] }).map(r => r.id), [newer.id, older.id], 'Newest first, not severity first');
  assert.throws(() => validateFeed({ schema_version: 2, reports: [older, older] }), /Duplicate/);
  assert.throws(() => validateFeed({ schema_version: 2, reports: Array(101).fill(older) }), /Invalid/);
  assert.throws(() => validateFeed({ schema_version: 1, reports: [] }), /Invalid/);
  for (const mutate of [
    r => r.analysis.severity = 'healthy',
    r => r.analysis.metrics[0].value = Infinity,
    r => r.analysis.metrics[0].coverage_pct = 101,
    r => r.analysis.metrics[0].baseline_days = 8,
    r => r.analysis.coverage_pct = -1,
    r => r.analysis.baseline.end = r.analysis.window.end,
    r => r.analysis.findings[0].end = 1,
    r => r.analysis.findings.push(r.analysis.findings[0]),
    r => r.narrative.summary.en = 'x'.repeat(1201),
    r => r.analysis.schema_version = 1,
    r => r.narrative.notes[0].finding_id = 'unknown',
    r => r.id = '../../private',
    r => r.id = ['a'.repeat(64)],
    r => r.published_at = NaN,
  ]) {
    const invalid = report(); mutate(invalid); assert.equal(validReport(invalid), false);
  }
  const localizedFields = r => [r.analysis.metrics[0].label, r.analysis.findings[0].title,
    r.analysis.findings[0].observation, r.analysis.limitations[0], r.narrative.summary, r.narrative.notes[0].commentary];
  for (const language of ['en', 'el', 'ru']) {
    for (let index = 0; index < localizedFields(report()).length; index++) {
      const missingLocale = report(); delete localizedFields(missingLocale)[index][language];
      assert.equal(validReport(missingLocale), false, `Missing ${language} in field ${index} must fail`);
      assert.throws(() => localizeReport(missingLocale, 'en'), /Incomplete/, 'No fallback even when English exists');
      const emptyLocale = report(); localizedFields(emptyLocale)[index][language] = '  ';
      assert.equal(validReport(emptyLocale), false, `Empty ${language} in field ${index} must fail`);
    }
    const original = report(), selected = localizeReport(original, language);
    assert.deepEqual(localizedFields(selected), localizedFields(original).map(field => field[language]), 'Every text field follows the chosen language');
    assert.deepEqual(selected.analysis.window, original.analysis.window);
    assert.deepEqual(selected.analysis.baseline, original.analysis.baseline);
    for (const key of ['value', 'baseline', 'change_pct', 'coverage_pct', 'baseline_days', 'unit']) {
      assert.equal(selected.analysis.metrics[0][key], original.analysis.metrics[0][key], `Evidence ${key} unchanged`);
    }
    assert.equal(typeof original.narrative.summary, 'object', 'Locale projection must not mutate the validated report');
  }
  assert.equal(localizedText({ en: 'a', el: 'b', ru: 'c', fr: 'd' }, 10), false, 'Only the exact locale keys are accepted');
  assert.equal(localizedText('Legacy text', 100), false);
  assert.throws(() => localizeReport(report(), 'fr'), /Incomplete/);
  const missing = report(); missing.analysis.metrics[0].value = null; missing.analysis.metrics[0].baseline = null;
  missing.analysis.metrics[0].change_pct = null; missing.analysis.data_through = null;
  assert.equal(validReport(missing), true, 'Missing values must remain distinct from zero');
  const zero = report(); zero.analysis.metrics[0].value = 0; assert.equal(validReport(zero), true);
  assert.equal(dateTime(Date.UTC(2026, 9, 12, 23, 12) / 1000), '13/10/2026 · 02:12', 'Athens dd/mm/yyyy across midnight');
  const valid = { schema_version: 2, reports: [report()] };
  assert.deepEqual(await readBoundedJson(new Response(JSON.stringify(valid))), valid);
  await assert.rejects(readBoundedJson(new Response('{}', { headers: { 'Content-Length': String(MAX_BYTES + 1) } })), /too large/);
  await assert.rejects(readBoundedJson(new Response(' '.repeat(MAX_BYTES + 1))), /too large/);
  await assert.rejects(readBoundedJson(new Response('not json')), SyntaxError);
  const source = fs.readFileSync(path.join(__dirname, '../insights.js'), 'utf8');
  assert.doesNotMatch(source, /\.innerHTML|\.outerHTML|insertAdjacentHTML|\beval\s*\(/, 'Report text must never enter an HTML sink');
  assert.match(source, /node\.textContent = content/);
  assert.match(source, /cache: 'no-store'/);
  assert.match(source, /credentials: 'omit'/);
  assert.match(source, /setTimeout\(\(\) => controller\.abort\(\), 10000\)/);
  assert.match(source, /report = localizeReport\(report, locale\(\)\)/);
  assert.match(source, /details\.lang = locale\(\)/);
  assert.doesNotMatch(source, /Report text is in English\.|\.lang = 'en'/);
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  assert.ok(html.indexOf('id="energy-insights"') > html.indexOf('class="energy-scene"'));
  assert.ok(html.indexOf('id="energy-insights"') < html.indexOf('class="reading-details"'));
  const nginx = fs.readFileSync(path.join(__dirname, '../deploy/nginx.conf.example'), 'utf8');
  assert.match(nginx, /location = \/reports\/index\.json \{\s+alias \/var\/lib\/homeenergy-reports\/public\/index\.json;\s+default_type application\/json;\s+limit_except GET \{ deny all; \}/);
  assert.doesNotMatch(nginx, /location \/reports\/\s*\{/);
  console.log('Energy insights UI contract, bounded reads, dates, ordering and safe rendering tests passed.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
