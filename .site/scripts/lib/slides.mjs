import { readFileSync } from 'node:fs';
import path from 'node:path';
import { repoRoot } from './repo.mjs';

// The shared status is recorded in docs/slides/proposals.md; slides.test.mjs checks it against that source.
export const proposalStatus = 'Proposal draft for NeoCloudCon Europe 2027, not submitted or accepted';

export function parseDecks(readme) {
  return [...readme.matchAll(/^\| (.+?) \| (\d+) slides, (\d+) minutes \| \[Browser slides\]\(https:\/\/[^/]+\/slides\/([a-z0-9-]+)\/\) \| \[Standalone HTML\]\(([a-z0-9-]+\.html)\) \|$/gm)]
    .map(([, title, count, minutes, slug, file]) => ({ title, count: Number(count), minutes: Number(minutes), slug, file }));
}

export function loadDecks() {
  return parseDecks(readFileSync(path.join(repoRoot, 'docs/slides/README.md'), 'utf8'));
}
