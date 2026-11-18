import assert from 'node:assert/strict';
import { test } from 'node:test';
import { routeOf, sidebarOrder, transformDoc } from '../scripts/lib/docs.mjs';

const routes = new Map([
  ['docs/runbook.md', '/docs/runbook/'],
  ['docs/adr/0002-execution-environments.md', '/docs/adr/0002-execution-environments/'],
]);
const repoUrl = 'https://github.com/example/repo';
const body = (text) => transformDoc('docs/verification.md', `# Verification\n\n${text}\n`, routes, repoUrl).split('\n---\n')[1];

test('a route mirrors the repository path in lower case', () => {
  assert.equal(routeOf('docs/adr/0002-Execution-Environments.md'), '/docs/adr/0002-execution-environments/');
});

test('the level-one heading becomes the title and leaves the body', () => {
  const out = transformDoc('docs/verification.md', '# Verification: how the lab proves itself\n\ntext\n', routes, repoUrl);
  assert.match(out, /^---\ntitle: "Verification: how the lab proves itself"\n/);
  assert.doesNotMatch(out.split('\n---\n')[1], /# Verification/);
});

test('a document without a title is refused', () => {
  assert.throws(() => transformDoc('docs/x.md', 'no heading\n', routes, repoUrl), /level-one heading/);
});

test('the operator documents lead the sidebar and the rest keep name order', () => {
  assert.deepEqual(['docs/runbook.md', 'docs/verification.md', 'docs/troubleshooting.md'].map(sidebarOrder), [1, 2, 3]);
  assert.equal(sidebarOrder('docs/adr/0001-kubernetes-stack.md'), 100);
  assert.match(transformDoc('docs/runbook.md', '# Runbook\n', routes, repoUrl), /\n {2}order: 1\n/);
});

test('a relative link to another copied document becomes its route', () => {
  assert.match(body('see [cleanup](runbook.md#8-cleanup)'), /\[cleanup\]\(\/docs\/runbook\/#8-cleanup\)/);
});

test('a link into the evidence directory becomes code, never a link', () => {
  const out = body('the [log](../.lab/evidence/m1/probe.log) of the probe');
  assert.match(out, /the log \(`\.lab\/evidence\/m1\/probe.log`\) of the probe/);
  assert.doesNotMatch(out, /\]\(.*evidence/);
});

test('a link to another repository file points at the repository', () => {
  assert.match(body('[lab.sh](../tests/integration/qemu-lab/lab.sh)'), /\(https:\/\/github.com\/example\/repo\/blob\/main\/tests\/integration\/qemu-lab\/lab.sh\)/);
});

test('an image among the site assets keeps working where the copied page lands', () => {
  const out = transformDoc('docs/adr/0007-x.md', '# X\n\n![A node](../../.site/src/assets/diagrams/node.svg)\n', routes, repoUrl);
  assert.match(out, /!\[A node\]\(\.\.\/\.\.\/\.\.\/\.\.\/assets\/diagrams\/node\.svg\)/);
  assert.match(body('![Top](../.site/src/assets/diagrams/top.svg)'), /!\[Top\]\(\.\.\/\.\.\/\.\.\/assets\/diagrams\/top\.svg\)/);
});

test('a code span naming a copied document links to it, other code stays code', () => {
  const out = body('`docs/adr/0002-execution-environments.md` and `results/<run-id>/`');
  assert.match(out, /\[`docs\/adr\/0002-execution-environments.md`\]\(\/docs\/adr\/0002-execution-environments\/\)/);
  assert.match(out, /`results\/<run-id>\/`/);
});

test('a bare tag-like word outside code cannot vanish as HTML', () => {
  assert.match(body('run make cleanup RUN=<run-id> now'), /RUN=&lt;run-id>/);
});

test('fenced code passes through unchanged', () => {
  assert.match(body('```text\n[a](runbook.md) <tag> `docs/runbook.md`\n```'), /\n\[a\]\(runbook\.md\) <tag> `docs\/runbook\.md`\n/);
});

test('the repository owner comes from GITHUB_REPOSITORY when CI sets it', async () => {
  const { repositoryOwner } = await import('../scripts/lib/repo.mjs');
  assert.equal(repositoryOwner({ GITHUB_REPOSITORY: 'some-owner/rdma-fabric-lab' }), 'some-owner');
});
