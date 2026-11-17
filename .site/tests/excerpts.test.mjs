import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { parse } from 'yaml';
import { buildExcerpt, chooseLines, makeRedactor, sourcePath } from '../scripts/lib/excerpts.mjs';
import { evidenceDir, repoRoot, siteDir } from '../scripts/lib/repo.mjs';

const keep = (text) => text;
const summary = ['# title', '', '| check | status |', '|---|---|', '| a | passed |', '| b | failed |', '', '| metric |'].join('\n');

test('a block runs from its first line to the next blank line', () => {
  assert.deepEqual(chooseLines(summary.split('\n'), { block: '| check |' }), [3, 4, 5, 6]);
});

test('rows keep the header and separator and the named rows', () => {
  assert.deepEqual(chooseLines(summary.split('\n'), { block: '| check |', rows: ['b'] }), [3, 4, 6]);
  assert.throws(() => chooseLines(summary.split('\n'), { block: '| check |', rows: ['z'] }), /rows z/);
});

test('output text must stay contiguous and whole', () => {
  assert.throws(() => buildExcerpt('x', { run: 'r', source: 's', lines: '1,3' }, summary, keep), /contiguous/);
  assert.throws(() => buildExcerpt('x', { run: 'r', source: 's', lines: '1-2', grep: 'a' }, summary, keep), /contiguous and whole/);
});

test('grep with a header picks rows of a TSV and columns keep the named ones', () => {
  const tsv = 'run\tbreak\tcheck\n1\ta\tx\n2\tb\ty\n3\ta\tz';
  const e = buildExcerpt('x', { run: 'r', source: 's', format: 'tsv', grep: '\\ta\\t', header: true, columns: ['run', 'check'] }, tsv, keep);
  assert.equal(e.text, 'run\tcheck\n1\tx\n3\tz');
  assert.equal(e.lines, '1-2,4');
});

test('local paths and names of the machine are replaced', () => {
  const redact = makeRedactor({ roots: ['/w/repo'], home: '/home/u1', host: 'u1-box', user: 'u1' });
  assert.equal(redact('/w/repo/x /home/u1/y u1-box u1 u10'), '<repo>/x ~/y <host> <user> u10');
});

test('an excerpt that names the local design draft is refused', () => {
  assert.throws(() => buildExcerpt('x', { run: 'r', source: 's', lines: '1' }, `P${'LAN'} A1 baseline`, keep), /design draft/);
});

test('every stored excerpt whose source is here still equals its source lines', () => {
  const manifest = parse(readFileSync(path.join(siteDir, 'excerpts.yaml'), 'utf8'));
  let checked = 0;
  for (const [id, entry] of Object.entries(manifest)) {
    const file = sourcePath(entry.source, { repoRoot, evidenceDir });
    if (!existsSync(file) || !entry.source.startsWith('tests/fixtures/')) continue;
    const stored = JSON.parse(readFileSync(path.join(siteDir, 'src/excerpts', `${id}.json`), 'utf8'));
    assert.equal(stored.text, buildExcerpt(id, entry, readFileSync(file, 'utf8'), keep).text, id);
    checked += 1;
  }
  assert.ok(checked > 0, 'no excerpt from a tracked fixture was checked');
});
