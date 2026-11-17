import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseMakeTargets } from '../scripts/lib/make.mjs';

test('targets are the rule lines with a ## comment, as make help reads them', () => {
  const text = [
    'venv: $(BIN)/x ## Build .venv',
    'hidden: venv',
    '# lint: not a rule ## no',
    'lint: venv ## yamllint ## then the rest',
    '\tcd x ## inside a recipe',
  ].join('\n');
  assert.deepEqual(parseMakeTargets([{ file: 'Makefile', text }]), [
    { target: 'lint', description: 'then the rest', file: 'Makefile' },
    { target: 'venv', description: 'Build .venv', file: 'Makefile' },
  ]);
});
