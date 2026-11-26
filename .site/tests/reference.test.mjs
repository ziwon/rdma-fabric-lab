import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { routeOf } from '../scripts/lib/docs.mjs';
import { listFiles, repoRoot, siteDir } from '../scripts/lib/repo.mjs';
import { verifyReference } from '../scripts/lib/verify-reference.mjs';

const adrRoutes = listFiles(path.join(repoRoot, 'docs/adr'), /\.md$/).map((file) => routeOf(path.relative(repoRoot, file)));
const group = (label, open = false) => `<details${open ? ' open' : ''}><summary><span>${label}</span></summary></details>`;
function pages(base = '') {
  return new Map([
    ['/reference/', `${group('Repository documents')}${group('Adr')}<main>${adrRoutes.map((route) => `<a href="${base}${route}">ADR</a>`).join('')}</main>`],
    ...adrRoutes.map((route) => [route, `${group('Repository documents', true)}${group('Adr', true)}<a href="${base}${route}" aria-current="page">ADR</a>`]),
  ]);
}
const verify = (pages, base = '') => verifyReference({ pages, adrRoutes, base });

test('the Reference landing page links every repository ADR exactly once, with decisions before operating documents', () => {
  const source = readFileSync(path.join(siteDir, 'src/content/docs/reference/index.mdx'), 'utf8');
  const links = [...source.matchAll(/\]\((\/docs\/adr\/[^)]+)\)/g)].map(([, route]) => route);
  assert.ok(adrRoutes.length > 0);
  assert.deepEqual(links.toSorted(), adrRoutes.toSorted());
  assert.ok(source.indexOf('## Decisions') < source.indexOf('## Running and proving the lab'));
});

test('built Reference checks accept collapsed groups and open, highlighted ADRs, including a base path', () => {
  for (const base of ['', '/lab']) assert.deepEqual(verify(pages(base), base), []);
});

test('the ADR and repository groups must be collapsed by default, and expand for a current ADR', () => {
  for (const label of ['Repository documents', 'Adr']) {
    const built = pages();
    built.set('/reference/', built.get('/reference/').replace(group(label), group(label, true)));
    assert.deepEqual(verify(built), [`/reference/: ${label} must be collapsed by default`]);
    const current = pages();
    const route = adrRoutes[0];
    current.set(route, current.get(route).replace(group(label, true), group(label)));
    assert.deepEqual(verify(current), [`${route}: ${label} must expand for the current ADR`]);
  }
});

test('every ADR retains its route, one landing-page link, and a current-page highlight', () => {
  for (const route of adrRoutes) {
    const missing = pages();
    missing.delete(route);
    assert.deepEqual(verify(missing), [`${route}: ADR was not built`]);
    for (const replacement of ['', `<a href="${route}">ADR</a>`.repeat(2)]) {
      const built = pages();
      built.set('/reference/', built.get('/reference/').replace(`<a href="${route}">ADR</a>`, replacement));
      assert.deepEqual(verify(built), [`/reference/: must link ${route} exactly once in the page content`]);
    }
    const current = pages();
    current.set(route, current.get(route).replace('aria-current="page"', ''));
    assert.deepEqual(verify(current), [`${route}: current ADR must be highlighted`]);
  }
});
