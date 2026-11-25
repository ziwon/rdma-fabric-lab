import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { repoRoot } from '../scripts/lib/repo.mjs';
import { loadDecks, parseDecks, proposalStatus } from '../scripts/lib/slides.mjs';
import { verifyTalks } from '../scripts/lib/verify-talks.mjs';

const decks = loadDecks();
const site = 'https://rdma.restack.tech';
const sidebar = (base) => `<ul class="starlight-sidebar-topics astro-scoped"><li><a href="${base}/labs/">Labs</a></li><li><a href="${base}/slides/"><div>Talks</div></a></li></ul>`;
const home = (base) => `<html data-has-hero><div class="rfl-card-grid"><ul><li><a href="${base}/slides/"><strong>Talks</strong></a></li></ul></div></html>`;
const entry = (deck, base) => `<li data-deck="${deck.slug}"><h2>${deck.title}</h2><p>${deck.count} slides · ${deck.minutes} minutes</p><p>${proposalStatus}</p><a href="${base}/slides/${deck.slug}/">Open browser deck</a></li>`;
function pages(base = '') {
  const destination = `${base}/slides/multus-boot-race/`;
  return new Map([
    ['/', home(base)],
    ['/start/', sidebar(base)],
    ['/slides/', `${sidebar(base)}<h1>Talks</h1><ul>${decks.map((deck) => entry(deck, base)).join('')}</ul>`],
    ['/slides/kubernetes-rdma-wiring/', `<meta http-equiv="refresh" content="0; url=${destination}"><link rel="canonical" href="${site}${destination}"><a href="${destination}">Open deck</a>`],
    ...decks.map((deck) => [`/slides/${deck.slug}/`, '<html>Deck</html>']),
  ]);
}
const verify = (pages, base = '') => verifyTalks({ pages, decks, status: proposalStatus, base, site });

test('adding a README row adds a deck to the index data without editing the page', () => {
  const readme = readFileSync(path.join(repoRoot, 'docs/slides/README.md'), 'utf8');
  const row = '| Another Talk | 6 slides, 15 minutes | [Browser slides](https://rdma.restack.tech/slides/another-talk/) | [Standalone HTML](another-talk.html) |';
  const updated = parseDecks(`${readme}\n${row}\n`);
  assert.deepEqual(updated.slice(0, -1), decks);
  assert.deepEqual(updated.at(-1), { title: 'Another Talk', count: 6, minutes: 15, slug: 'another-talk', file: 'another-talk.html' });
});

test('the built Talks checks accept every README deck, its sidebar and legacy redirect, including a site base path', () => {
  for (const base of ['', '/lab']) assert.deepEqual(verify(pages(base), base), []);
});

test('the built index must list every README deck with title, count, length, status and browser link', () => {
  for (const deck of decks) {
    for (const value of [deck.title, `${deck.count} slides`, `${deck.minutes} minutes`, proposalStatus, `href="/slides/${deck.slug}/"`]) {
      const built = pages();
      built.set('/slides/', built.get('/slides/').replace(entry(deck, ''), entry(deck, '').replace(value, '')));
      assert.ok(verify(built).some((problem) => problem.includes(deck.slug)), value);
    }
    const built = pages();
    built.set('/slides/', built.get('/slides/').replace(entry(deck, ''), ''));
    assert.ok(verify(built).some((problem) => problem.includes(deck.slug)));
  }
});

test('a missing index, deck or sidebar entry fails verification', () => {
  for (const route of ['/slides/', ...decks.map((deck) => `/slides/${deck.slug}/`)]) {
    const built = pages();
    built.delete(route);
    assert.ok(verify(built).length > 0, route);
  }
  for (const route of ['/start/', '/slides/']) {
    const built = pages();
    built.set(route, built.get(route).replace(sidebar(''), ''));
    assert.deepEqual(verify(built), [`${route}: sidebar lacks the Talks link`]);
  }
});

test('the home page retains its splash layout and links Talks in its navigation cards', () => {
  const built = pages();
  built.set('/', built.get('/').replace('<html ', '<html data-has-sidebar '));
  assert.deepEqual(verify(built), ['/: the home splash must not have a sidebar']);
  built.set('/', '<html data-has-hero></html>');
  assert.deepEqual(verify(built), ['/: navigation cards lack the Talks link']);
});

test('Talks follows Labs in the left menu on Start and the index', () => {
  for (const route of ['/start/', '/slides/']) {
    const built = pages();
    built.set(route, built.get(route).replace('href="/labs/"', 'href="/reference/"'));
    assert.deepEqual(verify(built), [`${route}: Talks must follow Labs in the sidebar`]);
  }
});

test('the old URL must point to the new deck by meta refresh, canonical and plain link', () => {
  for (const [tag, problem] of [['meta', 'meta refresh'], ['link', 'canonical link'], ['a', 'plain link']]) {
    const built = pages();
    const route = '/slides/kubernetes-rdma-wiring/';
    built.set(route, built.get(route).replace(new RegExp(`<${tag}\\b[^>]*>`), ''));
    assert.deepEqual(verify(built), [`legacy talk: missing ${problem}`]);
  }
});
