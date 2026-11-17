import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';

export const siteDir = fileURLToPath(new URL('../..', import.meta.url));
export const repoRoot = process.env.RFL_REPO_ROOT ?? path.resolve(siteDir, '..');
export const evidenceDir = process.env.RFL_EVIDENCE_DIR ?? path.join(repoRoot, '.lab/evidence');

// The account that owns the repository on GitHub. CI names it in GITHUB_REPOSITORY, and a local build reads it
// from the origin remote, so no tracked file has to spell it.
export function repositoryOwner(env = process.env) {
  const fromCi = env.GITHUB_REPOSITORY?.split('/')[0];
  if (fromCi) return fromCi;
  const remote = execFileSync('git', ['-C', siteDir, 'remote', 'get-url', 'origin'], { encoding: 'utf8' }).trim();
  const match = /github\.com[:/]([^/]+)\//.exec(remote);
  if (!match) throw new Error(`cannot read the GitHub owner from the origin remote ${remote}. Set GITHUB_REPOSITORY=<owner>/<repository>`);
  return match[1];
}

export const repoUrl = `https://github.com/${repositoryOwner()}/rdma-fabric-lab`;

export function listFiles(dir, pattern) {
  let names;
  try {
    names = readdirSync(dir);
  } catch {
    return [];
  }
  return names
    .filter((name) => pattern.test(name) && statSync(path.join(dir, name)).isFile())
    .sort()
    .map((name) => path.join(dir, name));
}

export function loadScenarios(root = repoRoot) {
  const tracks = readdirSync(path.join(root, 'tracks')).sort();
  const scenarios = {};
  for (const track of tracks) {
    for (const file of listFiles(path.join(root, 'tracks', track, 'scenarios'), /\.ya?ml$/)) {
      const spec = parse(readFileSync(file, 'utf8'));
      const key = `${spec.track}/${spec.scenario}`;
      scenarios[key] = { key, path: path.relative(root, file), ...spec };
    }
  }
  return scenarios;
}
