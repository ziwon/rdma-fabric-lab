import assert from 'node:assert/strict';
import { test } from 'node:test';
import rehypeCodeBreaks from '../src/plugins/rehype-code-breaks.mjs';

const code = (value) => ({ type: 'element', tagName: 'code', properties: {}, children: [{ type: 'text', value }] });
const text = (node) => node.children.filter((c) => c.type === 'text').map((c) => c.value).join('');
const run = (tree) => (rehypeCodeBreaks()(tree), tree);

test('inline code gets break points after separators and keeps its text', () => {
  const node = code('docs/adr/0002.md');
  run({ type: 'root', children: [node] });
  assert.equal(text(node), 'docs/adr/0002.md');
  assert.equal(node.children.filter((c) => c.tagName === 'wbr').length, 3);
  assert.equal(node.properties.className, undefined);
});

test('a long token without separators may break anywhere', () => {
  const node = code('sha256:cb3e3550f4f60de5414abe55dbe81e45fd68106ef5ecc4cfa4d701b6083ee279');
  run({ type: 'root', children: [node] });
  assert.deepEqual(node.properties.className, ['rfl-long']);
});

test('code inside pre is left alone', () => {
  const inner = code('a/b');
  run({ type: 'root', children: [{ type: 'element', tagName: 'pre', properties: {}, children: [inner] }] });
  assert.equal(inner.children.length, 1);
});
