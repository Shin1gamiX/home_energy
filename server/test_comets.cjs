// Dependency-free regression checks for the exact production animation engine.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8').split('// Shared localization')[0];
const context = { window: {} };
vm.runInNewContext(source, context);
const { roundedOutline, borderBranches, cubic, timings, phaseAt, cadence, beamStateAt, BorderPassCoalescer } = context.window.energyComets;
const near=(a,b)=>assert.ok(Math.hypot(a.x-b.x,a.y-b.y)<1e-6);
for(const shape of [{x:10,y:20,width:190,height:160,radius:17},{x:3,y:5,width:128,height:227,radius:14}]) {
  const outline=roundedOutline(shape);
  for(const side of ['top','right','bottom','left']) for(const fraction of [.25,.5,.8]) {
    const port=outline.port(side,fraction);
    const source=borderBranches(outline,port,true),target=borderBranches(outline,port,false);
    near(source[0].point(0),source[1].point(0));
    near(source[0].point(source[0].length),port);
    near(source[1].point(source[1].length),port);
    near(target[0].point(0),port);near(target[1].point(0),port);
    near(target[0].point(target[0].length),target[1].point(target[1].length));
    for(let i=0;i<200;i++) {
      const p=outline.point(i*outline.length/199);
      assert.ok(p.x>=shape.x-1e-8&&p.x<=shape.x+shape.width+1e-8);
      assert.ok(p.y>=shape.y-1e-8&&p.y<=shape.y+shape.height+1e-8);
    }
  }
}
const curve=cubic({x:0,y:0},{x:0,y:100},{x:100,y:100},{x:100,y:0});
near(curve.point(0),{x:0,y:0});near(curve.point(curve.length),{x:100,y:0});
assert.ok(curve.length>190&&curve.length<210);
const timing=timings(320);
assert.equal(timing.travel,320/450);
assert.equal(timings(10).travel,.4);
assert.equal(timings(600).travel,1.2);
assert.equal(timings(2000).travel,1.2);
assert.equal(timing.gather,.8);
assert.equal(timing.spread,.8);
assert.equal(timing.fade,.35);
assert.equal(timing.rest,.45);
for(const length of [0,10,180,320,540,600,2000]) {
  assert.ok(timings(length).travel>=.4&&timings(length).travel<=1.2);
}
assert.equal(phaseAt(0,timing).phase,'gather');
assert.equal(phaseAt(timing.gather+.001,timing).phase,'travel');
assert.equal(phaseAt(timing.gather+timing.travel+.001,timing).phase,'spread');
assert.equal(phaseAt(timing.total+.01,timing).phase,'gather');
for(const [watts,beams] of [[-1,0],[0,0],[NaN,0],[Infinity,0],[1,1],[299.99,1],[300,2],[999.99,2],[1000,2],[1000.01,3],[2000,3]]) {
  const schedule=cadence(watts,timing);
  assert.equal(schedule.beams,beams,`tier at ${watts} W`);
  assert.equal(schedule.interval,beams ? timing.total/beams : 0);
}
// Test exact staggered starts and repeat boundaries at short and long lengths.
for(const length of [10,320,600]) for(const watts of [250,550,1200]) {
  const t=timings(length),schedule=cadence(watts,t),n=schedule.beams;
  for(let index=0;index<n;index++) {
    const launch=index*schedule.interval;
    assert.equal(beamStateAt(launch-.001,t,index,n).phase,'waiting');
    assert.equal(beamStateAt(launch+.001,t,index,n).phase,'gather');
    assert.equal(beamStateAt(launch+t.total-.001,t,index,n).phase,'rest');
    assert.equal(beamStateAt(launch+t.total+.001,t,index,n).phase,'gather');
  }
  for(let step=0;step<1000;step++) {
    const time=step*t.total/200;
    const active=Array.from({length:n},(_,index)=>beamStateAt(time,t,index,n)).filter(state=>!['waiting','rest'].includes(state.phase));
    assert.ok(active.length<=n);
    if(n===1&&time<t.total) assert.notEqual(beamStateAt(time,t,0,n).phase,'waiting');
  }
}
assert.equal(beamStateAt(1,timing,0,0).phase,'waiting');
assert.equal(beamStateAt(timing.total+.1,timing,0,3).cycle,1);
const merged = new BorderPassCoalescer();
const first={id:'solar-house:0:0:source',box:'solar',phase:'gather',progress:.1};
const overlap={id:'solar-battery:0:0:source',box:'solar',phase:'gather',progress:0};
const receiver={id:'solar-house:0:0:target',box:'load',phase:'spread',progress:.2};
assert.equal(merged.select([first]).get('solar').id,first.id);
let selection=merged.select([first,overlap,receiver]);
assert.equal(selection.size,2); // One pair per box, not one pair per request.
assert.equal(selection.get('solar').id,first.id); // No preemption mid-pass.
selection=merged.select([overlap,receiver]);
assert.equal(selection.has('solar'),false); // Absorbed overlap is never replayed.
assert.equal(selection.get('load').id,receiver.id);
assert.equal(merged.select([{...receiver,phase:'fade'}]).get('load').phase,'fade');
assert.equal(merged.select([]).size,0);
assert.equal(merged.seen.size,0);
const next={...first,id:'solar-house:0:1:source'};
assert.equal(merged.select([next,overlap]).get('solar').id,next.id);
merged.reset();
assert.equal(merged.select([overlap]).get('solar').id,overlap.id);
// Exercise shared source/receiver cards across asynchronous, repeated routes.
for(const shared of ['source','target']) {
  merged.reset();
  for(let frame=0;frame<2400;frame++) {
    const requests=[];
    for(const [route,length,delay] of [['a',10,0],['b',400,.7]]) {
      const t=timings(length);
      for(let index=0;index<3;index++) {
        const state=beamStateAt(frame/60-delay,t,index,3);
        if(shared==='source'&&state.phase==='gather'||shared==='target'&&['spread','fade'].includes(state.phase)) {
          requests.push({id:`${route}:${index}:${state.cycle}:${shared}`,box:'shared',...state});
        }
      }
    }
    const chosen=merged.select(requests);
    assert.ok(chosen.size<=1);
    assert.ok(merged.seen.size<=6);
    if(chosen.size) assert.ok(requests.some(request=>request.id===chosen.get('shared').id));
  }
}
console.log('PASS: geometry, 0.8 s borders, 0.4–1.2 s travel, power tiers, staggered cycles and uninterrupted, non-queued shared border passes.');

// Minimal SVG/RAF host tests the real renderer without a DOM package or network.
class Node {
  constructor(rect = {}) {
    this.rect = rect; this.children = []; this.dataset = {}; this.attributes = {};
    this.style = { setProperty(name, value) { this[name] = value; } };
  }
  append(...nodes) { nodes.forEach(node => { node.parent = this; this.children.push(node); }); }
  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
  getBoundingClientRect() { return this.rect; }
}
const scene = new Node({left:0, top:0, width:740, height:500});
const cards = {
  grid: new Node({left:30, top:45, width:190, height:145}),
  solar: new Node({left:520, top:45, width:190, height:210}),
  battery: new Node({left:520, top:280, width:190, height:175}),
  load: new Node({left:30, top:300, width:190, height:140}),
};
scene.querySelector = selector => cards[selector.slice(1).replace('-label', '')];
const events = {}, media = {matches:false, addEventListener(_, fn) {this.change = fn;}, removeEventListener() {this.change = null;}};
let resize, disconnected = false, sequence = 0, timestamp = 0;
const frames = new Map();
const document = {
  hidden:false, createElementNS:() => new Node(),
  addEventListener(name, fn) {events[name] = fn;}, removeEventListener(name) {delete events[name];},
};
const windowEvents = {};
let themeColor = '#169779';
const host = {window:{innerWidth:1280, addEventListener(name, fn) {windowEvents[name] = fn;}, removeEventListener(name) {delete windowEvents[name];}}, document, matchMedia:() => media,
  getComputedStyle:() => ({borderTopLeftRadius:'18px', getPropertyValue:() => themeColor}),
  requestAnimationFrame(fn) { const id = ++sequence; frames.set(id, fn); return id; },
  cancelAnimationFrame(id) { frames.delete(id); },
  ResizeObserver: class {constructor(fn) {resize = fn;} observe() {} disconnect() {disconnected = true;}},
};
vm.runInNewContext(source, host);
const engine = host.window.energyComets.create(scene);
const layer = scene.children[0];
function frame() {
  const scheduled = [...frames.values()]; frames.clear(); timestamp += 1000 / 60;
  scheduled.forEach(fn => fn(timestamp));
}
function routes() { return layer.children.filter(node => node.dataset.cometRoute); }
function setFlow(watts) { engine.setState({routes:{solarHouse:true, solarBattery:true}, routeWatts:{solarHouse:watts, solarBattery:watts}}); }
setFlow(550); engine.setEnabled(true); frame();
assert.equal(routes().length, 2);
assert.ok(routes().every(route => route.dataset.beamLimit === 2));
const originalRoute = routes()[0];
for (let i = 0; i < 30; i++) frame();
setFlow(650); frame();
assert.equal(routes()[0], originalRoute, 'same-tier poll retains SVG and animation progress');
assert.equal(routes()[0].dataset.watts, 650);
for (let i = 0; i < 400; i++) {
  frame();
  assert.ok(frames.size <= 1, 'one pending animation frame');
  assert.ok(routes().every(route => Number(route.dataset.activeBeams) <= 2));
  assert.ok(layer.children.filter(node => node.dataset.borderBox).length <= 3);
}
setFlow(1200); frame();
assert.notEqual(routes()[0], originalRoute, 'tier change rebuilds bounded beam slots');
assert.ok(routes().every(route => route.dataset.beamLimit === 3));
engine.setEnabled(false);
assert.equal(frames.size, 0);
assert.equal(layer.style.display, 'none', 'non-live readings hide all tracks and particles');
assert.ok(routes().every(route => route.dataset.activeBeams === '0'));
engine.setEnabled(true);
assert.equal(layer.dataset.motion, 'running');
media.matches = true; media.change();
assert.equal(frames.size, 0);
assert.equal(layer.style.display, '');
assert.equal(layer.dataset.motion, 'reduced');
assert.ok(routes().every(route => route.dataset.activeBeams === '0'));
media.matches = false; media.change();
document.hidden = true; events.visibilitychange();
assert.equal(frames.size, 0);
assert.equal(layer.dataset.motion, 'hidden');
document.hidden = false; events.visibilitychange(); frame();
assert.equal(layer.dataset.motion, 'running');
const beforeResize = routes()[0];
cards.solar.rect.height += 10; resize(); frame();
assert.notEqual(routes()[0], beforeResize);
const beforeTheme = routes()[0];
themeColor = '#5fd4bb'; windowEvents.themechange();
assert.equal(routes()[0], beforeTheme, 'theme change preserves active geometry and cadence');
assert.equal(routes()[0].children[0].attributes.stroke, themeColor);
host.window.innerWidth = 490;
engine.setState({routes:{batteryHouse:true}, routeWatts:{batteryHouse:550}}); frame();
const link = routes()[0].children[0].attributes.d;
const points = [...link.matchAll(/[ML](-?[\d.]+) (-?[\d.]+)/g)].map(match => ({x:Number(match[1]), y:Number(match[2])}));
assert.ok(points.length > 2);
assert.ok(points.every(point => Math.abs(point.y - points[0].y) < .02), 'narrow battery link stays level rather than hanging below cards');
assert.ok(points.every(point => point.y >= cards.load.rect.top && point.y <= cards.load.rect.top + cards.load.rect.height));
engine.setState({routes:{}, routeWatts:{}}); frame();
assert.equal(routes().length, 0);
assert.equal(frames.size, 0);
assert.equal(layer.dataset.motion, 'idle');
engine.destroy();
assert.equal(scene.children.length, 0);
assert.equal(disconnected, true);
assert.equal(events.visibilitychange, undefined);
assert.equal(media.change, null);
assert.equal(windowEvents.themechange, undefined);
console.log('PASS: renderer lifecycle, same-tier polling, tier/geometry rebuilds, bounded SVG/RAF, non-live shutdown, hidden tabs, reduced motion, idle and cleanup.');
