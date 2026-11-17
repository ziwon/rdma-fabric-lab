import assert from 'node:assert/strict';
import { test } from 'node:test';
import { routeOf, transformDoc } from '../scripts/lib/docs.mjs';

const routes = new Map([
  ['docs/milestones/M1.md', '/docs/milestones/m1/'],
  ['docs/adr/0002-execution-environments.md', '/docs/adr/0002-execution-environments/'],
]);
const repoUrl = 'https://github.com/example/repo';
const body = (text) => transformDoc('docs/milestones/M3.md', `# M3: title\n\n${text}\n`, routes, repoUrl).split('\n---\n')[1];

test('a route mirrors the repository path in lower case', () => {
  assert.equal(routeOf('docs/milestones/M1.md'), '/docs/milestones/m1/');
});

test('the level-one heading becomes the title and leaves the body', () => {
  const out = transformDoc('docs/milestones/M3.md', '# M3: hostNetwork pods\n\ntext\n', routes, repoUrl);
  assert.match(out, /^---\ntitle: "M3: hostNetwork pods"\n/);
  assert.doesNotMatch(out.split('\n---\n')[1], /# M3/);
});

test('a document without a title is refused', () => {
  assert.throws(() => transformDoc('docs/x.md', 'no heading\n', routes, repoUrl), /level-one heading/);
});

test('a relative link to another copied document becomes its route', () => {
  assert.match(body('see [M1](M1.md#5-bandwidth-record)'), /\[M1\]\(\/docs\/milestones\/m1\/#5-bandwidth-record\)/);
});

test('a link into the evidence directory becomes code, never a link', () => {
  const out = body('the [log](evidence/m3/probe.log) of the probe');
  assert.match(out, /the log \(`docs\/milestones\/evidence\/m3\/probe.log`\) of the probe/);
  assert.doesNotMatch(out, /\]\(.*evidence/);
});

test('a link to another repository file points at the repository', () => {
  assert.match(body('[lab.sh](../../tests/integration/qemu-lab/lab.sh)'), /\(https:\/\/github.com\/example\/repo\/blob\/main\/tests\/integration\/qemu-lab\/lab.sh\)/);
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
  assert.match(body('```text\n[a](M1.md) <tag> `docs/milestones/M1.md`\n```'), /\n\[a\]\(M1\.md\) <tag> `docs\/milestones\/M1\.md`\n/);
});

test('the repository owner comes from GITHUB_REPOSITORY when CI sets it', async () => {
  const { repositoryOwner } = await import('../scripts/lib/repo.mjs');
  assert.equal(repositoryOwner({ GITHUB_REPOSITORY: 'some-owner/rdma-fabric-lab' }), 'some-owner');
});
