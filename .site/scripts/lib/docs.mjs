import path from 'node:path';

// The repository documents the site copies, by directory under docs/.
export const DOC_DIRS = ['docs', 'docs/adr'];
// The ignored copies of run evidence that documents cite by path. They stay on the machine that ran the lab.
export const EVIDENCE_PREFIX = '.lab/evidence/';

// docs/adr/0002-execution-environments.md is served at /docs/adr/0002-execution-environments/, so a route
// mirrors the repository path.
export function routeOf(docPath) {
  return `/${docPath.replace(/\.md$/, '').toLowerCase()}/`;
}

export function outputOf(docPath) {
  return path.join('src/content/docs', docPath.toLowerCase());
}

// The operator's documents lead the sidebar in this order. Other documents keep name order after them.
const LEADING_DOCS = ['docs/runbook.md', 'docs/verification.md', 'docs/troubleshooting.md'];

export function sidebarOrder(docPath) {
  const lead = LEADING_DOCS.indexOf(docPath);
  return lead === -1 ? 100 : lead + 1;
}

function codeOrLink(code, docRoutes) {
  const route = docRoutes.get(code.replace(/^\.\//, ''));
  return route ? `[\`${code}\`](${route})` : `\`${code}\``;
}

function rewriteLink(text, target, fromDir, docRoutes, repoUrl) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('#') || target.startsWith('/')) {
    return `[${text}](${target})`;
  }
  const [file, hash] = target.split('#');
  const resolved = path.posix.normalize(path.posix.join(fromDir, file));
  if (resolved.startsWith('.lab/') || resolved.startsWith('results/')) {
    return text === resolved || text === `\`${resolved}\`` ? `\`${resolved}\`` : `${text} (\`${resolved}\`)`;
  }
  const route = docRoutes.get(resolved);
  if (route) return `[${text}](${route}${hash ? `#${hash}` : ''})`;
  return `[${text}](${repoUrl}/blob/main/${resolved}${hash ? `#${hash}` : ''})`;
}

// One line outside fenced code: links get site routes, evidence paths stay code, a code span naming
// another copied document becomes a link to it, and a bare < cannot open an HTML tag.
function rewriteLine(line, fromDir, docRoutes, repoUrl) {
  const links = [];
  const held = line.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, text, target) => {
    links.push(rewriteLink(text, target, fromDir, docRoutes, repoUrl));
    return `\u0000${links.length - 1}\u0000`;
  });
  return held
    .split(/(`[^`]*`)/)
    .map((part, i) => (i % 2 === 1 ? codeOrLink(part.slice(1, -1), docRoutes) : part.replace(/<(?=[A-Za-z/!])/g, '&lt;')))
    .join('')
    .replace(/\u0000(\d+)\u0000/g, (_, i) => links[Number(i)]);
}

export function transformDoc(docPath, text, docRoutes, repoUrl) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const heading = lines.findIndex((line) => line.startsWith('# '));
  if (heading === -1) throw new Error(`${docPath} has no level-one heading to use as the page title`);
  const title = lines[heading].slice(2).trim();
  const body = lines.slice(heading + 1);
  const fromDir = path.posix.dirname(docPath);
  let fenced = false;
  const rewritten = body.map((line) => {
    if (/^\s*```/.test(line)) {
      fenced = !fenced;
      return line;
    }
    return fenced ? line : rewriteLine(line, fromDir, docRoutes, repoUrl);
  });
  const frontmatter = [
    '---',
    `title: ${JSON.stringify(title)}`,
    `description: ${JSON.stringify(`The repository document ${docPath}, copied into the site at build time.`)}`,
    'sidebar:',
    `  order: ${sidebarOrder(docPath)}`,
    '---',
    '',
    `> This page is \`${docPath}\` of the repository, copied at build time. Paths under \`.lab/\`, its evidence copies in \`${EVIDENCE_PREFIX}\` among them, and \`results/\` name files that stay on the machine that ran them, so they appear as code, not as links.`,
  ];
  return `${frontmatter.join('\n')}\n${rewritten.join('\n')}`;
}
