const textOf = (html) => html.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();

function groupIsOpen(html, label) {
  const groups = [...html.matchAll(/<details\b([^>]*)>\s*<summary\b[^>]*>([\s\S]*?)<\/summary>/g)];
  const group = groups.find(([, , summary]) => textOf(summary).toLowerCase() === label.toLowerCase());
  return group ? /\bopen(?:[\s=]|$)/.test(group[1]) : undefined;
}

export function verifyReference({ pages, adrRoutes, base = '' }) {
  const problems = [];
  const landing = pages.get('/reference/') ?? '';
  const content = /<main\b[^>]*>([\s\S]*?)<\/main>/.exec(landing)?.[1] ?? '';
  const links = [...content.matchAll(/\bhref="([^"]+)"/g)].map(([, href]) => href);
  for (const label of ['Repository documents', 'Adr']) {
    if (groupIsOpen(landing, label) !== false) problems.push(`/reference/: ${label} must be collapsed by default`);
  }
  for (const route of adrRoutes) {
    if (links.filter((href) => href === `${base}${route}`).length !== 1) {
      problems.push(`/reference/: must link ${route} exactly once in the page content`);
    }
    const html = pages.get(route);
    if (!html) {
      problems.push(`${route}: ADR was not built`);
      continue;
    }
    for (const label of ['Repository documents', 'Adr']) {
      if (groupIsOpen(html, label) !== true) problems.push(`${route}: ${label} must expand for the current ADR`);
    }
    const currentLinks = [...html.matchAll(/<a\b([^>]*)>/g)].map(([, attrs]) => attrs);
    if (!currentLinks.some((attrs) => attrs.includes(`href="${base}${route}"`) && attrs.includes('aria-current="page"'))) {
      problems.push(`${route}: current ADR must be highlighted`);
    }
  }
  return problems;
}
