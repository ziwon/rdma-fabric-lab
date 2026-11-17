// npm run excerpts pull   copies every excerpt of excerpts.yaml from its source into src/excerpts/.
// npm run excerpts check  fails when a stored excerpt differs from its source. A source under the ignored
//                         evidence directory exists only on the machine that ran the lab, so it is skipped elsewhere.
import { existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parse } from 'yaml';
import { buildExcerpt, makeRedactor, sourcePath } from './lib/excerpts.mjs';
import { evidenceDir, repoRoot, siteDir } from './lib/repo.mjs';

const mode = process.argv[2];
if (mode !== 'pull' && mode !== 'check') {
  console.error('usage: node scripts/excerpts.mjs pull|check');
  process.exit(2);
}

const manifest = parse(readFileSync(path.join(siteDir, 'excerpts.yaml'), 'utf8'));
const outDir = path.join(siteDir, 'src/excerpts');
// The evidence directory is <checkout>/.lab/evidence, so its checkout is two levels up.
const evidenceRepo = existsSync(evidenceDir) ? path.resolve(realpathSync(evidenceDir), '../..') : null;
const redact = makeRedactor({
  roots: [repoRoot, realpathSync(repoRoot), ...(evidenceRepo ? [evidenceRepo] : [])],
  home: os.homedir(),
  host: os.hostname(),
  user: os.userInfo().username,
});

const problems = [];
let verified = 0;
let skipped = 0;
mkdirSync(outDir, { recursive: true });
for (const [id, entry] of Object.entries(manifest)) {
  const file = sourcePath(entry.source, { repoRoot, evidenceDir });
  const stored = path.join(outDir, `${id}.json`);
  if (!existsSync(file)) {
    if (mode === 'pull') problems.push(`${id}: source ${entry.source} is missing`);
    else if (!existsSync(stored)) problems.push(`${id}: src/excerpts/${id}.json is missing`);
    else skipped += 1;
    continue;
  }
  const excerpt = buildExcerpt(id, entry, readFileSync(file, 'utf8'), redact);
  const json = `${JSON.stringify(excerpt, null, 2)}\n`;
  if (mode === 'pull') {
    writeFileSync(stored, json);
  } else if (!existsSync(stored) || readFileSync(stored, 'utf8') !== json) {
    problems.push(`${id}: src/excerpts/${id}.json differs from ${entry.source} lines ${entry.lines}`);
  }
  verified += 1;
}
for (const name of readdirSync(outDir)) {
  if (!(path.basename(name, '.json') in manifest)) problems.push(`src/excerpts/${name} is not in excerpts.yaml`);
}
for (const problem of problems) console.error(problem);
console.log(`excerpts ${mode}: ${verified} from their source, ${skipped} skipped because the evidence is absent, ${problems.length} problems`);
process.exit(problems.length ? 1 : 0);
