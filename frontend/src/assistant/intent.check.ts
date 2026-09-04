/* Run: npm run check:intent
   Guards the two things that actually matter about the parser: it resolves the
   commands the UI advertises, and it fails closed on everything else. */
import assert from 'node:assert/strict';
import { parseIntent } from './intent.ts';

const ok = (text: string, expect: Record<string, unknown>) => {
  const got = parseIntent(text);
  assert.ok(got, `expected an intent for: ${text}`);
  for (const [k, v] of Object.entries(expect)) {
    assert.deepEqual((got as Record<string, unknown>)[k], v, `${text} -> ${k}`);
  }
};
const closed = (text: string) =>
  assert.equal(parseIntent(text), null, `expected NO intent for: ${text}`);

// The advertised verbs resolve, with every argument read off the phrase.
ok('hold N-S green at J2 for 20 seconds',
   { fn: 'force_phase', junction: 'J2', axis: 'ns', durationS: 20 });
ok('hold east-west at junction 1', { fn: 'force_phase', junction: 'J1', axis: 'ew' });
ok('skip to N-S at J3 now', { fn: 'force_phase', junction: 'J3', axis: 'ns', oneShot: true });
ok('release the hold on J2', { fn: 'clear_override', junction: 'J2' });
ok('clear override', { fn: 'clear_override', junction: undefined });
ok('switch to manual', { fn: 'set_mode', mode: 'manual' });
ok('go back to auto', { fn: 'set_mode', mode: 'auto' });
ok('corridor status', { fn: 'get_stats' });
ok('prioritise lane 2 at J1 for 300 seconds',
   { fn: 'set_lane_bias', junction: 'J1', laneSlot: 2, durationS: 300 });

// control_api's ranges are enforced here, not left to the server.
ok('bias lane 1 at J2 weight 99 for 9000 seconds', { weight: 10, durationS: 900 });
ok('bias lane 1 at J2 weight 0.01 for 1 second', { weight: 0.1, durationS: 10 });

// Fails closed: a missing junction or axis is never guessed.
closed('hold the green');
closed('hold N-S green');
closed('hold at J2');
closed('prioritise lane 2');
closed('open the gate');
closed('');

console.log('intent parser: all checks pass');
