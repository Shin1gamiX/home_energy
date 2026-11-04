const assert = require('node:assert/strict');
const D = require('../trends-data.js');
const today = '2026-11-03';
function payload() {
  return {version:1, completed_before:today, days:Array.from({length:60},(_,i)=>({
    day:D.shiftDate(today,i-60), seconds:86400,
    totals:Object.fromEntries(['load','pv','grid','battery','battery_charged'].map(k=>[k,{kwh:10,seconds:86400}])),
    soc:{minimum:20,maximum:i%2?80:100,seconds:86400}
  }))};
}
let input=payload(), rows=D.normalize(input,today), data=D.buildComparison(7,rows);
assert.equal(data.metrics.house.now.total,70);
assert.equal(data.metrics.house.delta,0);
assert.equal(data.current[0].date,'2026-10-27');
assert.equal(data.previous[6].date,'2026-10-26');
assert.equal(data.metrics.soc.now.total,620/7);
assert.ok(Math.abs(data.metrics.soc.delta + 20/7) < 1e-10);
assert.equal(D.athensDay(new Date('2026-11-02T23:10:00Z')),today);
assert.equal(D.athensDay(new Date('2026-10-24T22:10:00Z')),'2026-10-25');
input.days[55].seconds=90000;
input.days[55].totals.load.seconds=90000;
input.days[55].soc.seconds=90000;
data=D.buildComparison(7,D.normalize(input,today));
assert.equal(data.metrics.house.now.coverage,100);
assert.equal(data.metrics.soc.now.total,620/7); // Peaks are equally weighted, even across DST.
input.days[59].totals.load.seconds=0;
input.days[59].soc={minimum:null,maximum:null,seconds:0};
rows=D.normalize(input,today); data=D.buildComparison(7,rows);
assert.equal(data.metrics.house.now.total,60);
assert.equal(data.metrics.house.comparable,false);
assert.equal(data.metrics.house.delta,null);
assert.equal(rows.at(-1).house,null);
assert.equal(D.dailyDelta(rows.at(-1),rows[52],'house'),null);
assert.equal(D.extreme(data.current,'house',true),null);
assert.equal(D.ratio(data,'now'),null);
assert.equal(D.roundedDelta(null,10),null);
input=payload(); input.days[59].totals.load.kwh=0; input.days[59].soc={minimum:0,maximum:0,seconds:86400};
rows=D.normalize(input,today);
assert.equal(rows.at(-1).house,0); assert.equal(rows.at(-1).soc,0);
input.days[59].soc.maximum=101; assert.equal(D.normalize(input,today).at(-1).soc,null);
assert.throws(()=>D.normalize(input,'2026-11-04'));
input.days[59].day='<script>'; assert.throws(()=>D.normalize(input,today));
assert.equal(D.changeTone('house',{percent:-20}),'favorable');
assert.equal(D.changeTone('solar',{percent:-20}),'unfavorable');
assert.equal(D.changeTone('grid',{percent:-2}),'small');
assert.equal(D.changeTone('soc',{percent:20}),'neutral');
for(const days of [1,7,14,30]) assert.equal(D.buildComparison(days,D.normalize(payload(),today)).current.length,days);
data=D.buildComparison(1,D.normalize(payload(),today));
assert.equal(data.current[0].date,'2026-11-02');
assert.equal(data.previous[0].date,'2026-11-01');
assert.equal(data.metrics.house.now.total,10);
assert.equal(data.metrics.soc.now.total,80);
assert.equal(data.metrics.soc.before.total,100);
assert.equal(data.metrics.soc.delta,-20);
input=payload();
input.days[59].seconds=90000;
input.days[59].totals.load.seconds=90000;
data=D.buildComparison(1,D.normalize(input,today));
assert.equal(data.metrics.house.now.coverage,100);
input.days[59].totals.load.seconds=0;
data=D.buildComparison(1,D.normalize(input,today));
assert.equal(data.metrics.house.now.total,null);
assert.equal(data.metrics.house.comparable,false);
console.log('PASS: calendar windows, DST, peaks, energy, coverage, missing/zero values, invalid input and comparison colors');
