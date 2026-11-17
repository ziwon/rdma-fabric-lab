// Inline code may wrap after a separator such as / or _ instead of inside a word. A token that stays longer than
// LONG characters between separators, such as a digest, is marked so that it alone may break anywhere.
const SEPARATOR = /(?<=[/_.=:,@-])/;
const LONG = 24;

function breakCode(code) {
  const children = [];
  let long = false;
  for (const child of code.children) {
    if (child.type !== 'text') {
      children.push(child);
      continue;
    }
    child.value.split(SEPARATOR).forEach((part, i) => {
      if (part.length > LONG) long = true;
      if (i > 0) children.push({ type: 'element', tagName: 'wbr', properties: {}, children: [] });
      children.push({ type: 'text', value: part });
    });
  }
  code.children = children;
  if (long) code.properties.className = [...(code.properties.className ?? []), 'rfl-long'];
}

function walk(node, parent) {
  if (node.type === 'element' && node.tagName === 'code' && parent?.tagName !== 'pre') breakCode(node);
  for (const child of node.children ?? []) walk(child, node);
}

export default function rehypeCodeBreaks() {
  return (tree) => walk(tree, null);
}
