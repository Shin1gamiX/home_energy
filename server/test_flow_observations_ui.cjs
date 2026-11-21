// Standalone browser regression using an installed Playwright and Chromium.
// PYTHON and CHROMIUM_EXECUTABLE may select existing local executables.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.join(__dirname, '..');
const origin = 'https://overview.test';
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const packets = JSON.parse(execFileSync(python, ['-B', path.join(__dirname, 'flow_observations_fixture.py')], { encoding: 'utf8' }));

const daytime = { solarHouse: 482, batteryHouse: 37 };
const nighttime = { gridHouse: 580, gridBattery: 300 };
const steps = [
  ['initial', 'live', daytime],
  ['steady', 'live', daytime],
  ['silentBattery', 'partial', {}],
  ['unavailableBattery', 'partial', {}],
  ['independentGrid', 'partial', { gridHouse: 580 }],
  ['night', 'live', nighttime],
  ['heldZero', 'live', nighttime],
  ['linkLost', 'stale', {}],
  ['recovered', 'live', daytime],
];

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    const page = await browser.newPage({ viewport: { width: 1365, height: 1000 }, reducedMotion: 'no-preference' });
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    let packet = packets.initial;
    // Fulfill every request in-process. The Overview loads its real HTML, CSS and scripts.
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin || route.request().method() !== 'GET') return route.abort();
      if (url.pathname === '/api/energy') return route.fulfill({ json: packet });
      if (url.pathname === '/reports/index.json') return route.fulfill({ json: { reports: [] } });
      if (url.pathname === '/history/index.json') return route.fulfill({ json: { days: [] } });
      if (url.pathname === '/') return route.fulfill({ path: path.join(root, 'index.html') });
      if (/^\/(?:[\w-]+\.(?:js|css)|themes\/(?:[\w-]+\/)*[\w-]+\.(?:js|css)|assets\/solar-home\.webp)$/.test(url.pathname)) {
        return route.fulfill({ path: path.join(root, url.pathname.slice(1)) });
      }
      return route.fulfill({ status: 404 });
    });

    for (const [scenario, status, expectedRoutes] of steps) {
      packet = packets[scenario];
      assert.equal(packet.status, status, scenario + ' exporter status');
      await page.clock.setFixedTime(new Date(packet.generated_at * 1000));
      const response = page.waitForResponse(result => result.url() === origin + '/api/energy');
      if (scenario === 'initial') {
        await page.goto(origin);
      } else {
        await page.evaluate(() => window.dispatchEvent(new Event('online')));
      }
      await (await response).finished();
      await page.locator('.energy-scene').scrollIntoViewIfNeeded();
      const motion = status === 'stale' ? 'stopped' : Object.keys(expectedRoutes).length ? 'running' : 'idle';
      await page.waitForFunction(([status, motion, updated]) => {
        const time = new Date(updated * 1000).toLocaleTimeString('en-GB', { timeZone: 'Europe/Athens' });
        return document.body.dataset.connection === status && document.querySelector('.comet-layer')?.dataset.motion === motion
          && document.querySelector('#freshness').textContent.includes(time);
      }, [status, motion, packet.updated_at], { timeout: 5000 });
      const actual = await page.locator('.comet-layer').evaluate(layer => {
        const visible = getComputedStyle(layer).display !== 'none';
        const routes = [...layer.querySelectorAll('[data-comet-route]')];
        const visibleRoutes = routes.filter(route => visible && route.querySelector('.comet-track').getTotalLength() > 0);
        return {
          watts: Object.fromEntries(visibleRoutes.map(route => [route.dataset.cometRoute, Number(route.dataset.watts)])),
          hasActiveBeams: routes.some(route => Number(route.dataset.activeBeams) > 0),
          visible,
        };
      });
      assert.deepEqual(actual.watts, expectedRoutes, scenario + ' rendered SVG routes');
      assert.equal(actual.visible, status !== 'stale', scenario + ' SVG visibility');
      if (motion === 'stopped') assert.equal(actual.hasActiveBeams, false, scenario + ' stopped particles');
      if (motion === 'running') {
        await Promise.all(Object.keys(expectedRoutes).map(key => page.waitForFunction(route => {
          const particle = document.querySelector(`[data-comet-route="${route}"] [data-phase="travel"] .comet-transfer`);
          return particle && getComputedStyle(particle).display !== 'none' && particle.querySelector('path').getTotalLength() > 1;
        }, key, { timeout: 5000 })));
      }
      if (scenario === 'unavailableBattery') {
        assert.equal(await page.locator('[data-value="battery"]').first().textContent(), '—');
      }
      console.log(scenario, status, motion, actual.watts);
    }
    const snapshot = packets.recovered;
    const notice = page.locator('#charging-discrepancy');
    async function showCharging(routing, status = 'live') {
      const timestamp = packet.generated_at + 1;
      packet = { ...snapshot, updated_at: timestamp, generated_at: timestamp, status, charging_routing: routing,
        values: { ...snapshot.values, grid: 1037, grid_house: 965, grid_battery: 50, bridge: -72,
          pv: 996, battery: 1046, load: 992, solar_house: 0, battery_house: 0, solar_battery: 996 } };
      await page.clock.setFixedTime(new Date(timestamp * 1000));
      const response = page.waitForResponse(result => result.url() === origin + '/api/energy');
      await page.evaluate(() => window.dispatchEvent(new Event('online')));
      await (await response).finished();
      await page.waitForFunction(time => document.querySelector('#freshness').textContent.includes(
        new Date(time * 1000).toLocaleTimeString('en-GB', { timeZone: 'Europe/Athens' })), timestamp, { timeout: 5000 });
    }
    const pvOnly = { mains_active: false, solar_active: true, discrepancy_w: 50, persistent: false };
    await showCharging(pvOnly);
    await page.locator('[data-comet-route="solarBattery"]').waitFor({ state: 'attached' });
    assert.equal(await notice.isVisible(), false);
    assert.equal(await page.locator('[data-value="grid_battery"]').textContent(), '0');
    assert.equal(await page.locator('[data-comet-route="gridBattery"]').count(), 0);
    assert.equal(await page.locator('[data-comet-route="solarBattery"]').count(), 1);
    assert.equal(await page.locator('[data-comet-route="gridHouse"]').count(), 1);
    const sustained = { ...pvOnly, discrepancy_w: 420, persistent: true };
    await showCharging({ ...sustained, persistent: false });
    assert.equal(await notice.isVisible(), false, 'A transient mismatch leaves the notice hidden');
    await showCharging(sustained);
    await notice.waitFor({ state: 'visible' });
    assert.match(await notice.textContent(), /sources leave about 420\sW/);
    await page.setViewportSize({ width: 320, height: 720 });
    for (const [lang, title] of [['el', 'Απόκλιση στις μετρήσεις φόρτισης'], ['ru', 'Расхождение в показаниях зарядки']]) {
      await page.locator('#language').click();
      await page.locator(`[data-language="${lang}"]`).click();
      assert.match(await notice.textContent(), new RegExp(title));
      assert.doesNotMatch(await notice.textContent(), /sources leave/);
      assert.match(await notice.textContent(), /420\sW/);
      assert.ok(await notice.evaluate(el => el.scrollWidth <= el.clientWidth), lang + ' notice fits a narrow screen');
    }
    await showCharging(sustained, 'stale');
    assert.equal(await notice.isVisible(), false, 'Hide a saved disagreement during an outage');
    await showCharging({ mains_active: true, solar_active: true, discrepancy_w: 0, persistent: false });
    await page.locator('[data-comet-route="gridBattery"]').waitFor({ state: 'attached' });
    assert.equal(await notice.isVisible(), false, 'A fresh resolved sample clears the notice');
    assert.equal(await page.locator('[data-comet-route="gridBattery"]').count(), 1);
    assert.equal(await page.locator('[data-comet-route="solarBattery"]').count(), 1);
    console.log('Charging source gates, simultaneous charging, transient and persistent discrepancies, languages, narrow layout and recovery passed.');
    assert.deepEqual(pageErrors, [], 'Overview browser exceptions');
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
