// Isolated DOM/API contract tests. No browser secrets, network or hardware access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../collector-control.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor() { this.listeners = {}; this.dataset = {}; this.textContent = ''; this.value = ''; this.disabled = false; this.open = false; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute() {}
  querySelectorAll() { return []; }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  fire(name) { return this.listeners[name]?.({ preventDefault() {} }); }
}
function fixture() {
  const ids = ['restart-dongle', 'collector-dialog', 'collector-form', 'collector-password', 'collector-submit', 'collector-feedback', 'collector-close', 'collector-cancel'];
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  const calls = [];
  let now = 1000000;
  let tick;
  let post = async () => ({ ok: true, json: async () => ({ state: 'requested', cooldown_seconds: 300, baseline_updated_at: now / 1000 }) });
  const document = { documentElement: { lang: 'en' }, querySelector: selector => nodes[selector.slice(1)] };
  const listeners = {};
  const context = { document, window: { addEventListener(name, handler) { listeners[name] = handler; } }, AbortSignal, Date: { now: () => now },
    setInterval(fn) { tick = fn; return 1; }, clearInterval() { tick = null; },
    async fetch(url, options) {
      calls.push({ url, options });
      assert.notEqual(url, '/api/collector/status', 'The UI must never poll private status');
      if (url.endsWith('/restart')) return post();
      return { ok: true, json: async () => ({ status: 'live', updated_at: now / 1000, generated_at: now / 1000 }) };
    }
  };
  vm.runInNewContext(source, context);
  return { nodes, calls,
    language(lang) { document.documentElement.lang = lang; listeners.languagechange(); },
    pagehide() { listeners.pagehide(); },
    setPost(fn) { post = fn; },
    async open() { nodes['restart-dongle'].fire('click'); await flush(); },
    async advance(seconds) { for (let i = 0; i < seconds; i++) { now += 1000; tick?.(); await flush(); } },
    async submit(value) { nodes['collector-password'].value = value; await nodes['collector-form'].fire('submit'); },
    feedback() { return nodes['collector-feedback'].textContent; }
  };
}

(async () => {
  {
    const f = fixture();
    await f.open();
    assert.equal(f.nodes['collector-submit'].disabled, false);
    assert.equal(f.calls.length, 0);
    await f.submit('test-only-password');
    const request = f.calls.find(call => call.url.endsWith('/restart'));
    assert.equal(request.options.method, 'POST');
    assert.equal(request.options.credentials, 'omit');
    assert.equal(request.options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(request.options.body), { password: 'test-only-password' });
    assert.equal(f.nodes['collector-password'].value, '');
    assert.match(f.feedback(), /Restart requested/);
    await f.advance(5);
    assert.match(f.feedback(), /Fresh inverter readings/);
    assert.equal(f.nodes['collector-submit'].disabled, true);
    await f.submit('test-only-password');
    assert.equal(f.calls.filter(call => call.url.endsWith('/restart')).length, 1);
    f.nodes['collector-cancel'].fire('click');
    assert.equal(f.nodes['collector-password'].value, '');
    await f.open();
    assert.match(f.feedback(), /Everyone must wait/);
  }
  {
    const f = fixture();
    await f.open();
    f.setPost(async () => ({ ok: false, json: async () => ({ error: 'invalid_password' }) }));
    await f.submit('wrong-password');
    assert.match(f.feedback(), /Incorrect password/);
    assert.equal(f.nodes['collector-submit'].disabled, false);
    f.setPost(async () => ({ ok: false, json: async () => ({ error: 'locked', retry_after_seconds: 300 }) }));
    await f.submit('wrong-password');
    assert.equal(f.nodes['collector-submit'].disabled, true);
    f.nodes['collector-cancel'].fire('click');
    await f.open();
    assert.equal(f.nodes['collector-submit'].disabled, true);
    assert.match(f.feedback(), /Too many wrong/);
    await f.advance(300);
    assert.equal(f.nodes['collector-submit'].disabled, false);
    assert.match(f.feedback(), /Enter your password/);
    assert.equal(f.calls.length, 2); // No auto-retry or status fetch at expiry.
  }
  {
    const f = fixture();
    await f.open();
    f.setPost(async () => ({ ok: false, json: async () => ({ error: 'busy', retry_after_seconds: 300 }) }));
    await f.submit('test-only-password');
    await f.advance(5);
    assert.match(f.feedback(), /temporarily limited/);
    assert.equal(f.nodes['collector-submit'].disabled, true);
  }
  {
    const f = fixture();
    await f.open();
    assert.match(f.feedback(), /Enter your password/);
    f.setPost(async () => ({ ok: false, json: async () => ({ error: 'unavailable' }) }));
    await f.submit('test-only-password');
    assert.match(f.feedback(), /not been configured/);
    assert.equal(f.calls.filter(call => call.url.endsWith('/restart')).length, 1);
  }
  {
    const f = fixture();
    await f.open();
    f.setPost(async () => { throw new Error('Simulated lost response'); });
    await f.submit('test-only-password');
    assert.match(f.feedback(), /may have been sent/);
    assert.equal(f.nodes['collector-submit'].disabled, true);
    assert.equal(f.nodes['collector-password'].value, '');
    f.nodes['collector-cancel'].fire('click');
    await f.open();
    assert.match(f.feedback(), /may have been sent/);
    assert.equal(f.nodes['collector-submit'].disabled, true);
  }
  {
    const f = fixture();
    await f.open();
    f.setPost(async () => ({ ok: false, json: async () => ({ error: 'cooldown', retry_after_seconds: 120 }) }));
    await f.submit('test-only-password');
    assert.match(f.feedback(), /Everyone must wait 2m 0s/);
    await f.submit('test-only-password');
    assert.equal(f.calls.length, 1);
    await f.advance(120);
    assert.equal(f.nodes['collector-submit'].disabled, false);
    assert.match(f.feedback(), /Enter your password/);
    assert.equal(f.calls.length, 1);
  }
  {
    const f = fixture();
    await f.open();
    let complete;
    f.setPost(() => new Promise(resolve => { complete = resolve; }));
    const submitted = f.submit('test-only-password');
    assert.equal(f.nodes['collector-password'].value, '');
    f.nodes['collector-cancel'].fire('click');
    await f.open();
    assert.equal(f.nodes['collector-submit'].disabled, true);
    assert.match(f.feedback(), /Sending restart/);
    await f.submit('test-only-password');
    assert.equal(f.calls.length, 1);
    complete({ ok: true, json: async () => ({ state: 'requested', cooldown_seconds: 300 }) });
    await submitted;
    assert.match(f.feedback(), /Restart requested/);
    assert.equal(f.nodes['collector-submit'].disabled, true);
    f.nodes['collector-cancel'].fire('click');
    await f.open();
    assert.match(f.feedback(), /Everyone must wait/);
  }
  {
    const f = fixture();
    await f.open();
    let complete;
    f.setPost(() => new Promise(resolve => { complete = resolve; }));
    const submitted = f.submit('test-only-password');
    f.nodes['collector-cancel'].fire('click');
    complete({ ok: true, json: async () => ({ state: 'requested', cooldown_seconds: 300 }) });
    await submitted;
    await f.open();
    assert.match(f.feedback(), /Everyone must wait/);
    assert.equal(f.nodes['collector-submit'].disabled, true);
  }
  {
    const f = fixture();
    await f.open();
    let reject;
    f.setPost(() => new Promise((_, fail) => { reject = fail; }));
    const submitted = f.submit('test-only-password');
    f.nodes['collector-cancel'].fire('click');
    reject(new Error('Lost while dialog closed'));
    await submitted;
    await f.open();
    assert.match(f.feedback(), /may have been sent/);
    assert.equal(f.nodes['collector-submit'].disabled, true);
    await f.advance(301);
    assert.equal(f.nodes['collector-submit'].disabled, false);
    assert.equal(f.calls.filter(call => call.url.endsWith('/restart')).length, 1);
  }
  {
    for (const [lang, ready, blocked] of [['en', /Enter your password/, /Everyone must wait/],
      ['ru', /Введите пароль/, /Всем нужно подождать/], ['el', /Εισαγάγετε τον κωδικό/, /Όλοι πρέπει να περιμένουν/]]) {
      const f = fixture();
      f.language(lang);
      await f.open();
      assert.match(f.feedback(), ready);
      f.setPost(async () => ({ ok: false, json: async () => ({ error: 'cooldown', retry_after_seconds: 300 }) }));
      await f.submit('test-only-password');
      assert.match(f.feedback(), blocked);
      assert.equal(f.nodes['collector-submit'].disabled, true);
      assert.doesNotMatch(f.feedback(), /undefined|\{time\}/);
    }
  }
  {
    const f = fixture();
    await f.open();
    f.nodes['collector-password'].value = 'test-only-password';
    f.pagehide();
    assert.equal(f.nodes['collector-password'].value, '');
    f.nodes['collector-password'].value = 'test-only-password';
    f.nodes['collector-dialog'].close(); // Native Escape closes the same dialog.
    assert.equal(f.nodes['collector-password'].value, '');
    assert.equal(f.calls.length, 0);
  }
  console.log('11 collector dialog scenarios passed, including all 3 languages; no public status requests or automatic restart retries.');
})().catch(error => { console.error(error); process.exitCode = 1; });
