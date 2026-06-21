import assert from "node:assert/strict";
import { observeSourceHoldZero } from "./sourceHoldExit.js";

let observation = observeSourceHoldZero(undefined, 100);
assert.equal(observation.counted, true);
assert.deepEqual(observation.state, { misses: 1, lastConfirmedSlot: 100 });

observation = observeSourceHoldZero(observation.state, 100);
assert.equal(observation.counted, false, "the same confirmed bank must not count twice");
assert.equal(observation.state.misses, 1);

observation = observeSourceHoldZero(observation.state, 99);
assert.equal(observation.counted, false, "an older bank must not count as another zero");
assert.equal(observation.state.misses, 1);

observation = observeSourceHoldZero(observation.state, 101);
assert.equal(observation.counted, true);
assert.deepEqual(observation.state, { misses: 2, lastConfirmedSlot: 101 });

console.log("Source-hold exit state tests passed.");
