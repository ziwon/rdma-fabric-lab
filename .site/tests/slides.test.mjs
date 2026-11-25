// The talk decks in docs/slides are standalone HTML files, so nothing renders their claims from the repository. These
// tests tie what they name (checks, files, runs, source lines) to the README table, the scenario spec, the break
// script and the stored excerpts, so a deck fails here when the repository moves under it.
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { parse } from 'yaml';
import { repoRoot, siteDir } from '../scripts/lib/repo.mjs';
import { loadDecks, proposalStatus } from '../scripts/lib/slides.mjs';

const DECK = 'docs/slides/multus-boot-race.html';
const SPEC = 'tracks/a-soft-roce/scenarios/k8s-multus-net1.yaml';
const BREAKS = 'tests/integration/qemu-lab/breaks-k8s-rdma.sh';
const BREAK = 'cilium-file-back-in-cni-dir-on-worker2';

const read = (rel) => readFileSync(path.join(repoRoot, rel), 'utf8');
const slidesOf = (html) => html.split(/<section id="slide-\d+"/).slice(1);
const deck = read(DECK);
// The decks of the README table: title, slide count, minutes, the name in the site URL and the file.
const decks = loadDecks().map((deck) => ({ ...deck, html: read(`docs/slides/${deck.file}`) }));
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

test('the README table lists every deck in docs/slides under its title and the name the site serves', () => {
  const files = readdirSync(path.join(repoRoot, 'docs/slides')).filter((name) => name.endsWith('.html')).sort();
  assert.deepEqual(decks.map((d) => d.file).sort(), files);
  for (const d of decks) {
    assert.equal(`${d.slug}.html`, d.file);
    assert.equal(/<title>(.*?)<\/title>/.exec(d.html)[1], d.title);
    assert.equal(/<h1 class="sr-only">(.*?)<\/h1>/.exec(d.html)[1], d.title);
  }
});

test('the slides of every deck are numbered in order, each with a heading, as many as the README states', () => {
  for (const d of decks) {
    const slides = slidesOf(d.html);
    assert.equal(slides.length, d.count, d.file);
    slides.forEach((html, i) => {
      const number = parts(html, 'slide-number')[0][1];
      assert.equal(number, `${String(i + 1).padStart(2, '0')} / ${String(slides.length).padStart(2, '0')}`, d.file);
      assert.match(html, new RegExp(`aria-labelledby="title-${i + 1}"`));
      assert.match(html, new RegExp(`<h2 id="title-${i + 1}" class="sr-only">[^<]+</h2>`));
    });
  }
});

test('the speaker notes of every deck run without a gap to the length the README states', () => {
  for (const d of decks) {
    const notes = [...d.html.matchAll(/<summary>Speaker notes · (\d+:\d\d)–(\d+:\d\d) \((\d+:\d\d)\)<\/summary>/g)];
    assert.equal(notes.length, d.count, d.file);
    let clock = 0;
    for (const [, start, end, length] of notes) {
      assert.equal(seconds(start), clock, `${d.file}: a note starts at ${start}, the one before ended at ${clock} s`);
      assert.equal(seconds(end) - seconds(start), seconds(length), `${d.file}: ${start}–${end} is not ${length}`);
      clock = seconds(end);
    }
    assert.equal(clock, d.minutes * 60, d.file);
  }
});

test('every source link into this repository names a file that exists', () => {
  for (const d of decks) {
    const links = [...d.html.matchAll(/href="https:\/\/github\.com\/[^/]+\/rdma-fabric-lab\/(?:blob|tree)\/main\/([^"#]+)/g)].map((m) => m[1]);
    assert.ok(links.length > 0, d.file);
    for (const rel of new Set(links)) assert.ok(existsSync(path.join(repoRoot, rel)), `${rel} is linked from ${d.file} and missing`);
  }
});

test('each proposal names a deck of the README table under the same title', () => {
  const proposals = read('docs/slides/proposals.md');
  assert.doesNotMatch(proposals, /PROPOSAL_\d+_|PROVISIONAL/);
  const titles = [...proposals.matchAll(/^## \d+\. (.+)$/gm)].map((m) => m[1]);
  assert.deepEqual(titles, decks.map((d) => d.title));
  for (const d of decks) assert.ok(proposals.includes(`](${d.file})`), `proposals.md does not link ${d.file}`);
});

test('the Talks status matches the proposal drafts and their submission status', () => {
  const proposals = read('docs/slides/proposals.md');
  assert.match(proposals, /Drafts for .*NeoCloudCon Europe 2027/);
  assert.match(proposals, /Neither proposal has been submitted or accepted\./);
  assert.equal(proposalStatus, 'Proposal draft for NeoCloudCon Europe 2027, not submitted or accepted');
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

// The scope slide counts the pod starts on the rebooted worker that came without net1, per layout, from the summary of
// tests/integration/qemu-lab/probe-boot-window.sh: one row per label and kind of pod, keepers and waiters.
const bootWindow = () => {
  const excerpt = JSON.parse(readFileSync(path.join(siteDir, 'src/excerpts/slides-boot-window.json'), 'utf8'));
  const [header, ...rows] = excerpt.text.split('\n').map((line) => line.split('\t'));
  const cell = (row, name) => {
    assert.ok(header.includes(name), `slides-boot-window has no column ${name}`);
    return row[header.indexOf(name)];
  };
  const of = (label) => rows.filter((row) => cell(row, 'label') === label);
  const lost = (label) => {
    assert.deepEqual(of(label).map((row) => cell(row, 'kind')).sort(), ['keepers', 'waiters'], label);
    const sum = (name) => of(label).reduce((n, row) => n + Number(cell(row, name)), 0);
    return `${sum('without_net1')} of ${sum('pod_starts')}`;
  };
  const ends = (labels, name) => labels.flatMap((label) => of(label).flatMap((row) => cell(row, name).split('-').map(Number)));
  return { rows, cell, lost, ends };
};
const scopeSlide = () => slidesOf(deck).find((html) => html.includes('data-part="td-'));

test('the counts on the scope slide are the stored summary of the boot-window probe', () => {
  const { lost, ends } = bootWindow();
  const cells = Object.fromEntries(parts(scopeSlide(), 't[hd]-'));
  assert.deepEqual([cells['th-1'], cells['th-2']], ['STOCK PATHS', 'LAB LAYOUT']);
  assert.deepEqual([cells['td-1-1'], cells['td-1-2'], cells['td-2-1'], cells['td-2-2']],
    [lost('stock'), lost('lab-layout'), lost('stock-slow'), lost('lab-layout-slow')].map((count) => `${count} lost it`));
  const note = parts(scopeSlide(), 'scope-note').map(([, text]) => text).find((text) => text.startsWith('Multus wrote its file'));
  const [, low, high, cel1] = /(\d+) to (\d+) s after boot here, and (\d+) s after boot on a 13-guest simulation/.exec(note);
  const born = ends(['stock', 'lab-layout'], 'multus_file_s');
  assert.deepEqual([Number(low), Number(high)], [Math.round(Math.min(...born)), Math.round(Math.max(...born))]);
  const fleet = /`00-multus\.conf` at boot\+(\d+\.\d) s/.exec(read('docs/fleet.md'));
  assert.ok(fleet, 'docs/fleet.md no longer records when Multus wrote its file on the cel1 simulation');
  assert.equal(Number(cel1), Math.round(Number(fleet[1])));
});

test('every count and time in the notes of the scope slide is in the stored summary or docs/fleet.md', () => {
  const { rows, cell, lost } = bootWindow();
  const notes = /<details class="notes">([\s\S]*?)<p>Sources<\/p>/.exec(scopeSlide())[1];
  const counts = new Set(['stock', 'lab-layout', 'stock-slow', 'lab-layout-slow'].map(lost));
  const quoted = [...notes.matchAll(/\b\d+ of \d+\b/g)].map((m) => m[0]);
  assert.ok(quoted.length >= 2);
  for (const count of quoted) assert.ok(counts.has(count), `the notes quote "${count}", which no layout of the probe counted`);
  const slow = rows.filter((row) => cell(row, 'label') === 'stock-slow');
  const slowLost = (kind) => Number(cell(slow.find((row) => cell(row, 'kind') === kind), 'without_net1'));
  assert.equal(Number(/all (\d+) pods came back Ready without net1/.exec(notes)[1]), slowLost('keepers') + slowLost('waiters'));
  assert.equal(Number(/The (\d+) restarted pods kept the network-status/.exec(notes)[1]), slowLost('keepers'));
  assert.equal(Number(/The (\d+) waiting pods had no annotation/.exec(notes)[1]), slowLost('waiters'));
  const recorded = new Set(rows.flatMap((row) => ['sandbox_first_s', 'multus_file_s'].flatMap((name) => cell(row, name).split('-'))));
  recorded.add(/`00-multus\.conf` at boot\+(\d+\.\d) s/.exec(read('docs/fleet.md'))[1]);
  const times = [...notes.matchAll(/\b\d+\.\d\b/g)].map((m) => m[0]);
  assert.ok(times.length >= 6);
  for (const time of times) assert.ok(recorded.has(time), `the notes quote ${time} s, which neither the probe nor docs/fleet.md recorded`);
});

test('the reboot abstract uses the pod counts recorded by the boot-window probe', () => {
  const { rows, cell, lost } = bootWindow();
  const abstract = read('docs/slides/proposals.md').split('## 1. ')[1].split('## 2. ')[0];
  const description = abstract.split('**Description**\n\n')[1].split('\n\n**')[0];
  const [without, total] = lost('stock').split(' of ').map(Number);
  const kept = /kept net1 in (\d+) of (\d+) starts/.exec(description);
  assert.deepEqual([Number(kept[1]), Number(kept[2])], [total - without, total]);
  assert.equal(/(\d+ of \d+) pods came up Ready without net1/.exec(description)[1], lost('stock-slow'));
  const lab = rows.filter((row) => ['lab-layout', 'lab-layout-slow'].includes(cell(row, 'label')));
  const sum = (name) => lab.reduce((n, row) => n + Number(cell(row, name)), 0);
  assert.equal(/(\d+ of \d+) pod starts lost net1/.exec(description)[1], `${sum('without_net1')} of ${sum('pod_starts')}`);
});
