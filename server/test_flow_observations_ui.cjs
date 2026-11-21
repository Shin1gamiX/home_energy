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
    assert.deepEqual(pageErrors, [], 'Overview browser exceptions');
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
