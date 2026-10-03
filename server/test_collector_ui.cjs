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
  let state = { enabled: true, lockout_seconds: 0, cooldown_seconds: 0 };
  let now = 1000000;
  let tick;
  let post = async () => ({ ok: true, json: async () => ({ state: 'requested', cooldown_seconds: 300, baseline_updated_at: now / 1000 }) });
  const document = { documentElement: { lang: 'en' }, querySelector: selector => nodes[selector.slice(1)] };
  const context = { document, window: { addEventListener() {} }, AbortSignal, Date: { now: () => now },
    setInterval(fn) { tick = fn; return 1; }, clearInterval() { tick = null; },
    async fetch(url, options) {
      calls.push({ url, options });
      if (url.endsWith('/status')) return { ok: true, json: async () => ({ ...state }) };
      if (url.endsWith('/restart')) return post();
      return { ok: true, json: async () => ({ status: 'live', updated_at: now / 1000, generated_at: now / 1000 }) };
    }
  };
  vm.runInNewContext(source, context);
  return { nodes, calls, state,
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
    await f.submit('test-only-password');
    const request = f.calls.find(call => call.url.endsWith('/restart'));
    assert.equal(request.options.method, 'POST');
    assert.equal(request.options.credentials, 'omit');
    assert.equal(request.options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(request.options.body), { password: 'test-only-password' });
    assert.equal(f.nodes['collector-password'].value, '');
    assert.match(f.feedback(), /Restart requested/);
    f.state.cooldown_seconds = 295;
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
    f.state.lockout_seconds = 299;
    f.nodes['collector-cancel'].fire('click');
    await f.open();
    assert.equal(f.nodes['collector-submit'].disabled, true);
    assert.match(f.feedback(), /Too many wrong/);
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
    f.state.enabled = false;
    await f.open();
    assert.match(f.feedback(), /not been configured/);
    await f.submit('test-only-password');
    assert.equal(f.calls.filter(call => call.url.endsWith('/restart')).length, 0);
  }
  {
    const f = fixture();
    await f.open();
    f.setPost(async () => { throw new Error('Simulated lost response'); });
    await f.submit('test-only-password');
    assert.match(f.feedback(), /may have been sent/);
    assert.equal(f.nodes['collector-submit'].disabled, true);
    assert.equal(f.nodes['collector-password'].value, '');
  }
  console.log('5 collector dialog scenarios passed: restart/recovery, lockout, global budget, disabled setup, lost response.');
})().catch(error => { console.error(error); process.exitCode = 1; });
