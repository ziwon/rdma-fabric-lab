// The talk deck in docs/slides is a standalone HTML file, so nothing renders its claims from the repository. These
// tests tie what it names (checks, files, runs, source lines) to the scenario spec, the break script and the stored
// excerpts, so the deck fails here when the repository moves under it.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { parse } from 'yaml';
import { repoRoot, siteDir } from '../scripts/lib/repo.mjs';

const DECK = 'docs/slides/kubernetes-rdma-wiring.html';
const SPEC = 'tracks/a-soft-roce/scenarios/k8s-multus-net1.yaml';
const BREAKS = 'tests/integration/qemu-lab/breaks-k8s-rdma.sh';
const BREAK = 'cilium-file-back-in-cni-dir-on-worker2';

const read = (rel) => readFileSync(path.join(repoRoot, rel), 'utf8');
const deck = read(DECK);
const slides = deck.split(/<section id="slide-\d+"/).slice(1);
const parts = (html, prefix) => [...html.matchAll(new RegExp(`data-part="(${prefix}[^"]*)"[^>]*>([^<]*)<`, 'g'))].map((m) => [m[1], m[2]]);
const seconds = (clock) => clock.split(':').reduce((minutes, s) => minutes * 60 + Number(s), 0);

// The lines of the break script that hold one break: its `breaks <name>` line and the continuation lines after it.
function breakLines(name) {
  const lines = read(BREAKS).split('\n');
  const first = lines.findIndex((line) => line.startsWith(`breaks ${name} `));
  assert.notEqual(first, -1, `${BREAKS} has no break ${name}`);
  let last = first;
  while (lines[last].endsWith('\\')) last += 1;
  return { first: first + 1, last: last + 1, text: lines.slice(first, last + 1).join('\n') };
}

test('the slides are numbered in order and each has a heading', () => {
  assert.ok(slides.length > 0);
  slides.forEach((html, i) => {
    const number = parts(html, 'slide-number')[0][1];
    assert.equal(number, `${String(i + 1).padStart(2, '0')} / ${String(slides.length).padStart(2, '0')}`);
    assert.match(html, new RegExp(`aria-labelledby="title-${i + 1}"`));
    assert.match(html, new RegExp(`<h2 id="title-${i + 1}" class="sr-only">[^<]+</h2>`));
  });
});

test('the speaker notes run without a gap to the length the README states', () => {
  const notes = [...deck.matchAll(/<summary>Speaker notes · (\d+:\d\d)–(\d+:\d\d) \((\d+:\d\d)\)<\/summary>/g)];
  assert.equal(notes.length, slides.length);
  let clock = 0;
  for (const [, start, end, length] of notes) {
    assert.equal(seconds(start), clock, `a note starts at ${start}, the one before ended at ${clock} s`);
    assert.equal(seconds(end) - seconds(start), seconds(length), `${start}–${end} is not ${length}`);
    clock = seconds(end);
  }
  const readme = read('docs/slides/README.md');
  const words = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];
  assert.match(readme, new RegExp(`${words[slides.length]}-slide, ${words[clock / 60]}-minute`));
});

test('every source link into this repository names a file that exists', () => {
  const links = [...deck.matchAll(/href="https:\/\/github\.com\/[^/]+\/rdma-fabric-lab\/(?:blob|tree)\/main\/([^"#]+)/g)].map((m) => m[1]);
  assert.ok(links.length > 0);
  for (const rel of new Set(links)) assert.ok(existsSync(path.join(repoRoot, rel)), `${rel} is linked from the deck and missing`);
});

test('a link to the break script points at the lines of the break the deck shows', () => {
  const want = breakLines(BREAK);
  const anchors = [...deck.matchAll(new RegExp(`${BREAKS.replaceAll('.', '\\.')}#L(\\d+)-L(\\d+)`, 'g'))];
  assert.ok(anchors.length > 0);
  for (const [, first, last] of anchors) assert.deepEqual([Number(first), Number(last)], [want.first, want.last]);
});

test('the demo slide shows exactly the checks that the break must fail, by their names in the spec', () => {
  const shown = parts(deck, 'check-').map(([, text]) => text);
  const spec = parse(read(SPEC)).checks.map((check) => check.id);
  for (const id of shown) assert.ok(spec.includes(id), `${id} is on the demo slide and not in ${SPEC}`);
  const named = /^breaks \S+ \S+ "([^"]+)"/.exec(breakLines(BREAK).text)[1].split(' ');
  assert.deepEqual(shown, named);
});

test('the CNI files on the boot order slide are the ones the spec and the break name', () => {
  const files = parts(deck, 'cni-file-').map(([, text]) => text);
  assert.deepEqual(files, ['00-multus.conf', '05-cilium.conflist']);
  const description = parse(read(SPEC)).checks.find((check) => check.id === 'cni-dir-holds-only-multus').description;
  for (const file of files) assert.ok(description.includes(file));
  assert.ok(breakLines(BREAK).text.includes(`/etc/cni/net.d/${files[1]}`));
});

test('the runs on the demo slide are the stored excerpts, with the verdicts the slide shows', () => {
  const runs = parts(deck, 'run-').map(([part, text]) => [part, /\d{8}T\d{6}Z/.exec(text)?.[0]]);
  assert.deepEqual(runs.map(([part]) => part), ['run-break', 'run-repair']);
  const shown = parts(deck, 'check-').map(([, text]) => text);
  for (const [part, status, word] of [['run-break', 'failed', 'FAIL'], ['run-repair', 'passed', 'PASS']]) {
    const excerpt = JSON.parse(readFileSync(path.join(siteDir, 'src/excerpts', `slides-net1-${part.slice(4)}.json`), 'utf8'));
    assert.equal(runs.find(([p]) => p === part)[1], excerpt.run);
    const rows = excerpt.text.split('\n').slice(2).map((row) => row.split('|').map((cell) => cell.trim()));
    assert.deepEqual(rows.map((row) => [row[1], row[2]]), shown.map((id) => [id, status]));
    const verdicts = parts(deck, word.toLowerCase() + '-').filter(([p]) => !p.endsWith('-bg')).map(([, text]) => text);
    assert.deepEqual(verdicts, shown.map(() => word));
  }
});

test('every boot time and count the deck quotes comes from the stored excerpts of the two runs', () => {
  const stored = ['break', 'repair'].map((name) => JSON.parse(readFileSync(path.join(siteDir, 'src/excerpts', `slides-net1-${name}.json`), 'utf8')));
  const text = stored.map((excerpt) => excerpt.text).join('\n');
  const quoted = new Set([...deck.matchAll(/boot\+\d+(?:\.\d+)? s|\d+ of \d+ samples|all \d+ samples/g)].map((m) => m[0]));
  assert.ok(quoted.size >= 4);
  for (const quote of quoted) assert.ok(text.includes(quote), `the deck quotes "${quote}", which neither run recorded`);
  const runs = new Set([...deck.matchAll(/\b\d{8}T\d{6}Z\b/g)].map((m) => m[0]));
  for (const excerpt of stored) assert.ok(runs.has(excerpt.run));
  const spec = parse(read(SPEC)).checks.length;
  const shown = parts(deck, 'check-').length;
  assert.equal(Number(/The (\d+) other checks passed/.exec(parts(deck, 'other-checks')[0][1])[1]), spec - shown);
});
