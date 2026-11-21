// Test the pure routing rules without dependencies or a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8').split('// Shared localization')[0], context);
const state = (values, mode = 'Off-Grid') => JSON.parse(JSON.stringify(context.window.energyFlowState(values, mode)));
const base = { grid: 0, grid_house: 0, grid_battery: 0, pv: 0, load: 500, battery: 0, soc: 20 };
const gridToHouse = watts => ({ ...base, grid: watts, grid_house: watts });
const active = sample => Object.entries(state(sample).routes).filter(([, on]) => on).map(([name]) => name).sort();
assert.deepEqual(active(gridToHouse(500)), ['gridHouse']);
assert.equal(state(base, 'Mains').batteryLabel, 'Waiting to charge');
assert.equal(state({ ...base, soc: 40 }, 'Mains').batteryLabel, 'Standby');
assert.equal(state({ ...base, soc: null }, 'Mains').batteryLabel, 'Standby');
assert.equal(state({ ...base, battery: null }, 'Mains').batteryLabel, 'No report');
assert.deepEqual(active({ ...base, pv: 1000, battery: 500 }), ['solarBattery', 'solarHouse']);
assert.deepEqual(active({ ...base, pv: 200, battery: -300 }), ['batteryHouse', 'solarHouse']);
assert.deepEqual(active({ ...base, pv: 500, load: 0, battery: 500 }), ['solarBattery']);
assert.equal(state({ ...base, pv: 0, battery: -500 }).routes.batteryHouse, true);
assert.equal(state({ ...base, pv: null, grid: null }).routes.solarHouse, false);
assert.equal(state(gridToHouse(0.1)).routes.gridHouse, false);
assert.equal(state({ ...base, battery: -0.1 }, 'Mains').batteryLabel, 'Waiting to charge');
assert.equal(state({ ...gridToHouse(500), pv: 100 }).routes.solarHouse, false);
assert.deepEqual(state({ ...base, pv: 1500, battery: 1000 }).routeWatts,
  { gridHouse: 0, gridBattery: 0, solarHouse: 500, batteryHouse: 0, solarBattery: 1000 });
assert.equal(state({ ...base, battery: -1500 }).routeWatts.batteryHouse, 500);
assert.equal(state(gridToHouse(2000)).routeWatts.gridHouse, 500);
assert.equal(state({ ...base, battery: 2500, pv: 2000 }).routeWatts.solarBattery, 2000);
assert.equal(context.window.energyModeLabel('Off-Grid'), 'Off-Grid');
assert.equal(context.window.energyModeLabel('Mains'), 'Grid');
assert.equal(context.window.energyModeLabel('Fault'), 'Fault');
assert.equal(context.window.energySupplyLabel({...base, battery: -500}), 'Battery');
assert.equal(context.window.energySupplyLabel({...base, pv: 500}), 'Solar');
assert.equal(context.window.energySupplyLabel({...base, pv: 200, battery: -300}), 'Mixed');
assert.equal(context.window.energySupplyLabel(gridToHouse(420)), 'Grid');
assert.equal(context.window.energySupplyLabel({...base, pv: null}), 'Unknown supply');

// Preserve the routes supplied by earlier snapshots.
const mixedCharge = { ...base, grid: 1750, grid_battery: 1750, pv: 2971, load: 1723, battery: 2835 };
assert.deepEqual(state(mixedCharge).routeWatts,
  { gridHouse: 0, gridBattery: 1750, solarHouse: 1723, batteryHouse: 0, solarBattery: 1085 });
assert.equal(context.window.energySupplyLabel(mixedCharge), 'Solar');
assert.deepEqual(active({ ...mixedCharge, grid: 2000, grid_house: 250 }),
  ['gridBattery', 'gridHouse', 'solarBattery', 'solarHouse']);
assert.deepEqual(active({ ...gridToHouse(100), grid: 600, grid_battery: 500, battery: 500 }), ['gridBattery', 'gridHouse']);
// PV can run the house while all battery charging comes from the grid.
assert.deepEqual(active({ ...mixedCharge, battery: 1500 }), ['gridBattery', 'solarHouse']);
assert.equal(state({ ...mixedCharge, battery: -50 }).routes.gridBattery, false);
assert.equal(state({ ...mixedCharge, grid_battery: null }).routes.solarBattery, false);
const legacy = { grid: 1000, pv: 1000, load: 1000, battery: 1000 };
assert.deepEqual(active(legacy), []);
assert.equal(context.window.energySupplyLabel(legacy), 'Unknown supply');
const solarCharge = { grid: 720, grid_house: 652, grid_battery: 0, bridge: -68,
  pv: 3125, battery: 2964, load: 662, solar_house: 0, battery_house: 0, solar_battery: 2964 };
assert.deepEqual(state(solarCharge).routeWatts,
  { gridHouse: 652, gridBattery: 0, solarHouse: 0, batteryHouse: 0, solarBattery: 2964 });
assert.equal(context.window.energySupplyLabel(solarCharge), 'Grid');
const jointCharge = { grid: 2000, grid_house: 400, grid_battery: 1600, bridge: -1600,
  pv: 2400, battery: 4000, load: 400, solar_house: 0, battery_house: 0, solar_battery: 2400 };
assert.deepEqual(state(jointCharge).routeWatts,
  { gridHouse: 400, gridBattery: 1600, solarHouse: 0, batteryHouse: 0, solarBattery: 2400 });
const missingSplit = { ...solarCharge, grid_house: null, grid_battery: null, bridge: null,
  solar_house: null, battery_house: null, solar_battery: null };
assert.deepEqual(active(missingSplit), []);
assert.equal(context.window.energySupplyLabel(missingSplit), 'Unknown supply');
assert.deepEqual(active({ ...missingSplit, battery: -200 }), []);
assert.deepEqual(active({ ...solarCharge, solar_battery: null }), ['gridHouse'], 'Ignore stale display watts on unknown routes');
const inverterSupply = { pv: 1000, battery: -400, load: 800, grid: 120, grid_house: 100,
  grid_battery: 0, bridge: 700, solar_house: 330, battery_house: 370 };
assert.deepEqual(state(inverterSupply).routeWatts,
  { gridHouse: 100, gridBattery: 0, solarHouse: 330, batteryHouse: 370, solarBattery: 0 });
console.log('Overview routing, split charging, legacy snapshots, battery states and supply labels passed.');
