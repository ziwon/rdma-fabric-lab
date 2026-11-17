// Pages link to each other by route, such as /labs/. The site is served under a base path.
export function withBase(url, base) {
  if (typeof url !== 'string' || !url.startsWith('/') || url.startsWith('//')) return url;
  if (url === base || url.startsWith(`${base}/`)) return url;
  return `${base}${url}`;
}

function rewrite(node, base) {
  if (node.type === 'link' || node.type === 'definition') {
    node.url = withBase(node.url, base);
  } else if ((node.type === 'mdxJsxFlowElement' || node.type === 'mdxJsxTextElement') && node.name === 'a') {
    for (const attr of node.attributes ?? []) {
      if (attr.name === 'href' && typeof attr.value === 'string') attr.value = withBase(attr.value, base);
    }
  }
  for (const child of node.children ?? []) rewrite(child, base);
}

export default function remarkBaseLinks({ base }) {
  return (tree) => rewrite(tree, base);
}
