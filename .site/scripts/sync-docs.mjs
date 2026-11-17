// Copies the repository documents into the site and writes the data the pages render, at every build.
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { parse } from 'yaml';
import path from 'node:path';
import { DOC_DIRS, outputOf, routeOf, transformDoc } from './lib/docs.mjs';
import { readMakefiles, parseMakeTargets } from './lib/make.mjs';
import { listFiles, loadScenarios, repoRoot, repoUrl, siteDir } from './lib/repo.mjs';

const docs = DOC_DIRS.flatMap((dir) => listFiles(path.join(repoRoot, dir), /\.md$/)).map((file) => path.relative(repoRoot, file));
const docRoutes = new Map(docs.map((doc) => [doc, routeOf(doc)]));

rmSync(path.join(siteDir, 'src/content/docs/docs'), { recursive: true, force: true });
for (const doc of docs) {
  const out = path.join(siteDir, outputOf(doc));
  mkdirSync(path.dirname(out), { recursive: true });
  writeFileSync(out, transformDoc(doc, readFileSync(path.join(repoRoot, doc), 'utf8'), docRoutes, repoUrl));
}

const generated = path.join(siteDir, 'src/generated');
mkdirSync(generated, { recursive: true });
const scenarios = loadScenarios(repoRoot);
const targets = parseMakeTargets(readMakefiles(repoRoot));
writeFileSync(path.join(generated, 'scenarios.json'), `${JSON.stringify(scenarios, null, 2)}\n`);
writeFileSync(path.join(generated, 'make-targets.json'), `${JSON.stringify(targets, null, 2)}\n`);
writeFileSync(path.join(generated, 'lock.json'), `${JSON.stringify(parse(readFileSync(path.join(repoRoot, 'versions.lock.yaml'), 'utf8')), null, 2)}\n`);
writeFileSync(path.join(generated, 'docs.json'), `${JSON.stringify(Object.fromEntries(docRoutes), null, 2)}\n`);

console.log(`sync: ${docs.length} documents, ${Object.keys(scenarios).length} scenario specs, ${targets.length} make targets`);
