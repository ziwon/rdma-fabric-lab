// Checks on the built site that keep it from drifting from the repository.

const TABLE = /<table[^>]*\bdata-scenario="([^"]+)"[^>]*>([\s\S]*?)<\/table>/g;
const CHECK_ROW = /<tr[^>]*\bdata-check="([^"]+)"/g;
const TARGET_ROW = /<tr[^>]*\bdata-target="([^"]+)"/g;
const HREF = /\bhref="([^"]+)"/g;

// Internal links anywhere in the HTML, components included, which the links validator does not see.
export function brokenLinks(pages, base, assetExists) {
  const problems = [];
  for (const [route, html] of pages) {
    for (const [, href] of html.matchAll(HREF)) {
      if (!href.startsWith(`${base}/`)) continue;
      const target = href.slice(base.length).split(/[?#]/)[0];
      const ok = target.endsWith('/') ? pages.has(target) : assetExists(target);
      if (!ok) problems.push(`${route}: link ${href} leads nowhere`);
    }
  }
  return [...new Set(problems)];
}

// pages maps a route to its HTML. Returns, per scenario, every table that shows it as { route, checks }.
export function scenarioTables(pages) {
  const shown = new Map();
  for (const [route, html] of pages) {
    for (const [, spec, body] of html.matchAll(TABLE)) {
      const checks = [...body.matchAll(CHECK_ROW)].map((m) => m[1]);
      if (!shown.has(spec)) shown.set(spec, []);
      shown.get(spec).push({ route, checks });
    }
  }
  return shown;
}

export function verifySite({ scenarios, targets, docRoutes, pages, makeTargetsRoute }) {
  const problems = [];
  const shown = scenarioTables(pages);
  for (const [key, spec] of Object.entries(scenarios)) {
    const tables = shown.get(key);
    if (!tables) {
      problems.push(`${spec.path}: no page shows the checks of scenario ${key}`);
      continue;
    }
    const want = spec.checks.map((c) => c.id);
    for (const { route, checks } of tables) {
      const missing = want.filter((id) => !checks.includes(id));
      const extra = checks.filter((id) => !want.includes(id));
      if (missing.length) problems.push(`${route}: the table of ${key} lacks ${missing.join(', ')}`);
      if (extra.length) problems.push(`${route}: the table of ${key} shows ${extra.join(', ')}, which ${spec.path} does not declare`);
    }
  }
  for (const key of shown.keys()) {
    if (!(key in scenarios)) problems.push(`a page shows scenario ${key}, which has no spec under tracks/*/scenarios/`);
  }
  const html = pages.get(makeTargetsRoute);
  if (!html) {
    problems.push(`${makeTargetsRoute} was not built`);
  } else {
    const listed = new Set([...html.matchAll(TARGET_ROW)].map((m) => m[1]));
    const declared = new Set(targets.map((t) => t.target));
    for (const t of declared) if (!listed.has(t)) problems.push(`${makeTargetsRoute} lacks make ${t}`);
    for (const t of listed) if (!declared.has(t)) problems.push(`${makeTargetsRoute} lists make ${t}, which no ## comment declares`);
  }
  for (const [doc, route] of docRoutes) {
    if (!pages.has(route)) problems.push(`${doc} has no page at ${route}`);
  }
  return problems;
}
