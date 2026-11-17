// Runs after astro build. It reads the scenario specs and Makefiles again, not the data the pages rendered,
// and fails the build when a page lacks a declared check, a make target or a copied document.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { DOC_DIRS, routeOf } from './lib/docs.mjs';
import { parseMakeTargets, readMakefiles } from './lib/make.mjs';
import { listFiles, loadScenarios, repoRoot, siteDir } from './lib/repo.mjs';
import { brokenLinks, verifySite } from './lib/verify.mjs';

const dist = path.join(siteDir, 'dist');

function htmlPages(dir, route = '/') {
  const pages = new Map();
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      for (const [r, html] of htmlPages(path.join(dir, entry.name), `${route}${entry.name}/`)) pages.set(r, html);
    } else if (entry.name === 'index.html') {
      pages.set(route, readFileSync(path.join(dir, entry.name), 'utf8'));
    }
  }
  return pages;
}

const base = '/rdma-fabric-lab';
const pages = htmlPages(dist);
const docs = DOC_DIRS.flatMap((dir) => listFiles(path.join(repoRoot, dir), /\.md$/)).map((file) => path.relative(repoRoot, file));
const problems = verifySite({
  scenarios: loadScenarios(repoRoot),
  targets: parseMakeTargets(readMakefiles(repoRoot)),
  docRoutes: new Map(docs.map((doc) => [doc, routeOf(doc)])),
  pages,
  makeTargetsRoute: '/reference/make-targets/',
}).concat(brokenLinks(pages, base, (target) => existsSync(path.join(dist, target))));
for (const problem of problems) console.error(`verify-dist: ${problem}`);
console.log(`verify-dist: ${pages.size} pages, ${problems.length} problems`);
process.exit(problems.length ? 1 : 0);
