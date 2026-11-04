const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const D = require('../trends-data.js');
const source = fs.readFileSync(require.resolve('../trends.js'), 'utf8');
async function main() {
  const events = {}, windowEvents = {};
  const node = {dataset:{},after(){},focus(){},setAttribute(){}};
  const host = {innerHTML:'',addEventListener:(name,fn)=>events[name]=fn,
    querySelector:()=>node,querySelectorAll:()=>[]};
  let mode='error', requests=0, pendingSignal;
  const today=D.athensDay();
  const empty={version:1,completed_before:today,days:Array.from({length:60},(_,i)=>({
    day:D.shiftDate(today,i-60),seconds:86400,totals:{},soc:{minimum:null,maximum:null,seconds:0}
  }))};
  const context={console,Intl,Number,Math,AbortController,setTimeout,clearTimeout,
    document:{documentElement:{lang:'en'},querySelector:selector=>selector==='#energy-trends'?host:node,
      createElement:()=>({...node}),addEventListener(){}},
    window:{EnergyTrendsData:D,addEventListener:(name,fn)=>windowEvents[name]=fn},
    fetch:async(url,options)=>{
      requests++; pendingSignal=options.signal;
      assert.equal(url,'/history/index.json'); assert.equal(options.credentials,'omit');
      if(mode==='error') throw new Error('offline');
      return {ok:true,text:async()=>JSON.stringify({energy_summary:{trends:empty}})};
    }};
  vm.runInNewContext(source,context);
  await new Promise(setImmediate);
  assert.match(host.innerHTML,/could not be loaded/);
  assert.match(host.innerHTML,/Try again/);
  mode='empty';
  events.click({target:{closest:()=>({id:'trends-retry'})}});
  await new Promise(setImmediate);
  assert.equal(requests,2);
  assert.match(host.innerHTML,/Insufficient coverage/);
  assert.ok(!/NaN|Infinity|undefined/.test(host.innerHTML));
  events.click({target:{closest:()=>({dataset:{metric:'soc'}})}});
  assert.match(host.innerHTML,/bar-missing/);
  assert.ok(!/NaN|Infinity|undefined/.test(host.innerHTML));
  assert.equal(requests,2); // Chart selection is computed in memory, never daily fan-out.
  events.click({target:{closest:()=>({dataset:{metric:'soc'}})}});
  assert.match(host.innerHTML,/id="trend-details" hidden/);
  // One-day comparison is calculated from the same snapshot, including real values.
  for (const [i,row] of empty.days.slice(-2).entries()) {
    row.totals = Object.fromEntries(['load','pv','grid','battery','battery_charged']
      .map(key=>[key,{kwh:10+i,seconds:row.seconds}]));
    row.soc = {minimum:20,maximum:90+i,seconds:row.seconds};
  }
  events.click({target:{closest:()=>({id:'trends-retry'})}});
  await new Promise(setImmediate);
  events.click({target:{closest:()=>({dataset:{period:'1'}})}});
  events.click({target:{closest:()=>({dataset:{metric:'soc'}})}});
  assert.match(host.innerHTML,/data-period="1" aria-pressed="true">1 day/);
  assert.equal((host.innerHTML.match(/class="bar-pair"/g)||[]).length,1);
  assert.ok(!/NaN|Infinity|undefined|class="trend-spark"|class="spark-key"/.test(host.innerHTML));
  const yesterday=D.shiftDate(today,-1).split('-').reverse().join('/');
  const dayBefore=D.shiftDate(today,-2).split('-').reverse().join('/');
  assert.ok(host.innerHTML.includes(`<strong>${yesterday}</strong><span>compared with ${dayBefore}</span>`));
  assert.equal(requests,3);
  for (const [lang,label] of [['el','1 ημέρα'],['ru','1 день'],['en','1 day']]) {
    context.document.documentElement.lang=lang;
    windowEvents.languagechange();
    assert.ok(host.innerHTML.includes(label));
  }
  assert.equal(requests,3);
  windowEvents.pagehide();
  assert.equal(pendingSignal.aborted,true);
  console.log('PASS: offline/retry, empty data, missing bars, toggle, no request fan-out and navigation cancellation');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
