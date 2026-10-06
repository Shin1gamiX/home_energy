const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'theme.js'), 'utf8');

function fixture({saved=null, systemDark=false, blocked=false, ready='loading'} = {}) {
  const root = {dataset:{},lang:'en'};
  const events = {}, documentEvents = {}, writes = [], dispatched = [];
  const button = {hidden:true, attributes:{}, listeners:{}, setAttribute(key,value) {this.attributes[key]=value;}, addEventListener(key,fn) {this.listeners[key]=fn;}};
  const meta = {setAttribute(key,value) {this[key]=value;}};
  const media = {matches:systemDark, addEventListener(name,fn) {this[name]=fn;}};
  let observer;
  const document = {documentElement:root,readyState:ready,
    querySelector(selector) {assert.equal(selector,'meta[name="theme-color"]');return meta;},
    querySelectorAll(selector) {assert.equal(selector,'[data-theme-toggle]');return [button];},
    addEventListener(name,fn) {documentEvents[name]=fn;},
  };
  const context = {document, localStorage:{
    getItem(key) {assert.equal(key,'homeenergy-theme');if(blocked)throw new Error('Blocked');return saved;},
    setItem(key,value) {assert.equal(key,'homeenergy-theme');if(blocked)throw new Error('Blocked');writes.push(value);},
  },window:{matchMedia(query) {assert.equal(query,'(prefers-color-scheme: dark)');return media;},
    addEventListener(name,fn) {events[name]=fn;}, dispatchEvent(event) {dispatched.push(event.type);}},
    Event:class {constructor(type) {this.type=type;}},
    MutationObserver:class {constructor(fn) {observer=fn;} observe(target,options) {assert.equal(target,root);assert.equal(options.attributeFilter[0],'lang');}},
  };
  vm.runInNewContext(script,context);
  return {root,button,meta,media,events,writes,dispatched,init:()=>documentEvents.DOMContentLoaded?.(),language:lang=>{root.lang=lang;observer();}};
}
for(const [saved, systemDark, expected] of [[null,false,'light'],[null,true,'dark'],['light',true,'light'],['dark',false,'dark'],['invalid',true,'dark']]) {
  const f=fixture({saved,systemDark});
  assert.equal(f.root.dataset.theme,expected,'theme is resolved before DOMContentLoaded');
  assert.equal(f.button.hidden,true);
  f.init(); assert.equal(f.button.hidden,false);
  assert.equal(f.meta.content,expected==='dark'?'#0f1918':'#eff7f6');
  f.button.listeners.click();
  assert.equal(f.root.dataset.theme,expected==='dark'?'light':'dark');
  assert.equal(f.writes.length,1);
  assert.equal(f.dispatched.at(-1),'themechange');
  const chosen=f.root.dataset.theme;
  f.media.matches=!f.media.matches;f.media.change();assert.equal(f.root.dataset.theme,chosen);
}
const automatic=fixture();automatic.init();automatic.media.matches=true;automatic.media.change();assert.equal(automatic.root.dataset.theme,'dark');
automatic.events.storage({key:'unrelated',newValue:'light'});assert.equal(automatic.root.dataset.theme,'dark');
automatic.events.storage({key:'homeenergy-theme',newValue:'light'});assert.equal(automatic.root.dataset.theme,'light');
automatic.events.storage({key:'homeenergy-theme',newValue:null});assert.equal(automatic.root.dataset.theme,'dark');
automatic.events.storage({key:null,newValue:null});assert.equal(automatic.root.dataset.theme,'dark');
assert.equal(automatic.writes.length,0,'storage/system events never write back in a loop');
automatic.language('el');assert.match(automatic.button.attributes['aria-label'],/φωτεινής/);
automatic.language('ru');assert.match(automatic.button.title,/светлую/);
automatic.language('unknown');assert.equal(automatic.button.title,'Switch to light mode');
const blocked=fixture({blocked:true,systemDark:true,ready:'complete'});
blocked.button.listeners.click();assert.equal(blocked.root.dataset.theme,'light');assert.equal(blocked.writes.length,0);
for(const name of ['index.html','history.html','404.html']) {
  const html=fs.readFileSync(path.join(root,name),'utf8');
  assert.equal((html.match(/data-theme-toggle/g)||[]).length,1,name);
  assert.ok(html.indexOf('/theme.js?')<html.indexOf('rel="stylesheet"'),`${name}: theme script runs before CSS/paint`);
  assert.match(html,/<script src="\/theme\.js\?[^\"]+"><\/script>/);
  assert.match(html,/<link rel="stylesheet" href="\/theme\.css\?/);
  assert.match(html,/<button[^>]+data-theme-toggle[^>]+hidden>/);
}
assert.doesNotMatch(script,/\bfetch\s*\(|XMLHttpRequest|document\.cookie|innerHTML/);
console.log('PASS: pre-paint theme, saved/system preferences, blocked storage, tab sync, localized accessible toggle, no-JS fallback and no network/credential access.');

const css = fs.readFileSync(path.join(root, 'theme.css'), 'utf8');
const dark = css.match(/:root\[data-theme=dark\] \{([\s\S]*?)\}/)[1];
const tokens = Object.fromEntries([...dark.matchAll(/--theme-([\w-]+): (#[\da-f]{6});/g)].map(([, name, value]) => [name, value]));
function luminance(hex) {
  const channels = [1, 3, 5].map(start => {
    const value = parseInt(hex.slice(start, start + 2), 16) / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  });
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
}
for (const [foreground, background] of [
  [tokens.text, tokens.surface], [tokens.muted, tokens.elevated],
  [tokens.grid, '#234238'], [tokens.solar, '#234238'],
  [tokens.house, '#192b27'], [tokens.danger, '#371f25'],
  [tokens['on-accent'], tokens.accent],
]) {
  const [high, low] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  assert.ok((high + .05) / (low + .05) >= 4.5, `${foreground} on ${background}: normal-text contrast`);
}
console.log('PASS: dark normal-text contrast on shared cards, active sources, warnings and primary buttons.');
