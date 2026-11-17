import { readFileSync } from 'node:fs';
import path from 'node:path';
import { listFiles } from './repo.mjs';

// The same lines `make help` prints: awk -F':.*## ' '/^[a-zA-Z0-9_-]+:.*## /'. The field separator is greedy,
// so the description is the text after the last "## " of the line.
const TARGET_LINE = /^([a-zA-Z0-9_-]+):.*## (.*)$/;

export function parseMakeTargets(files) {
  const targets = [];
  for (const { file, text } of files) {
    for (const line of text.split('\n')) {
      const match = TARGET_LINE.exec(line);
      if (match) targets.push({ target: match[1], description: match[2].trim(), file });
    }
  }
  return targets.sort((a, b) => (a.target < b.target ? -1 : a.target > b.target ? 1 : 0));
}

export function readMakefiles(root) {
  const files = [path.join(root, 'Makefile'), ...listFiles(path.join(root, 'mk'), /\.mk$/)];
  return files.map((file) => ({ file: path.relative(root, file), text: readFileSync(file, 'utf8') }));
}
