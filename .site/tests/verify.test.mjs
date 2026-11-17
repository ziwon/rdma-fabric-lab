import assert from 'node:assert/strict';
import { test } from 'node:test';
import { withBase } from '../src/plugins/remark-base-links.mjs';
import { brokenLinks, verifySite } from '../scripts/lib/verify.mjs';

const table = (spec, ids) => `<table data-scenario="${spec}"><tbody>${ids.map((id) => `<tr data-check="${id}"><td>${id}</td></tr>`).join('')}</tbody></table>`;
const targets = [{ target: 'lint' }];
const makePage = '<table data-make-targets><tr data-target="lint"></tr></table>';
const scenario = (checks) => ({ 'a/s1': { path: 'tracks/a/scenarios/s1.yaml', checks: checks.map((id) => ({ id })) } });
const site = (pages) => new Map([['/reference/make-targets/', makePage], ['/docs/x/', ''], ...pages]);
const run = (scenarios, pages) =>
  verifySite({ scenarios, targets, docRoutes: new Map([['docs/x.md', '/docs/x/']]), pages: site(pages), makeTargetsRoute: '/reference/make-targets/' });

test('a site that shows every declared check passes', () => {
  assert.deepEqual(run(scenario(['c1', 'c2']), [['/labs/s1/', table('a/s1', ['c1', 'c2'])]]), []);
});

test('a scenario that gains a check the site does not show fails', () => {
  assert.deepEqual(run(scenario(['c1', 'c2', 'c3']), [['/labs/s1/', table('a/s1', ['c1', 'c2'])]]), ['/labs/s1/: the table of a/s1 lacks c3']);
});

test('a scenario that no page shows fails', () => {
  assert.match(run(scenario(['c1']), [])[0], /no page shows the checks of scenario a\/s1/);
});

test('a page that shows a check or a scenario the specs do not declare fails', () => {
  const problems = run(scenario(['c1']), [['/labs/s1/', table('a/s1', ['c1', 'old']) + table('a/gone', ['x'])]]);
  assert.equal(problems.length, 2);
});

test('a make target or a copied document missing from the site fails', () => {
  const problems = verifySite({
    scenarios: {},
    targets: [{ target: 'lint' }, { target: 'site' }],
    docRoutes: new Map([['docs/y.md', '/docs/y/']]),
    pages: new Map([['/reference/make-targets/', makePage]]),
    makeTargetsRoute: '/reference/make-targets/',
  });
  assert.deepEqual(problems, ['/reference/make-targets/ lacks make site', 'docs/y.md has no page at /docs/y/']);
});

test('an internal link to a page or file that was not built fails', () => {
  const pages = new Map([['/a/', '<a href="/base/a/">x</a><a href="/base/b/#h">y</a><img src="/base/i.svg"><a href="/base/f.svg">f</a>']]);
  assert.deepEqual(brokenLinks(pages, '/base', (f) => f === '/f.svg'), ['/a/: link /base/b/#h leads nowhere']);
});

test('page routes get the base path once, other links stay', () => {
  assert.equal(withBase('/labs/', '/base'), '/base/labs/');
  assert.equal(withBase('/base/labs/', '/base'), '/base/labs/');
  assert.equal(withBase('https://x/', '/base'), 'https://x/');
  assert.equal(withBase('//cdn/x', '/base'), '//cdn/x');
  assert.equal(withBase('#h', '/base'), '#h');
});
