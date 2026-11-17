import { EVIDENCE_PREFIX } from './docs.mjs';

// "3-12" or, for table rows, "3-4,7,9-12". Line numbers start at 1 and include both ends.
export function parseRanges(spec) {
  return String(spec)
    .split(',')
    .map((part) => {
      const [a, b = a] = part.trim().split('-').map(Number);
      if (!Number.isInteger(a) || !Number.isInteger(b) || a < 1 || b < a) throw new Error(`bad line range ${spec}`);
      return [a, b];
    });
}

function compress(numbers) {
  const ranges = [];
  for (const n of numbers) {
    const last = ranges.at(-1);
    if (last && n === last[1] + 1) last[1] = n;
    else ranges.push([n, n]);
  }
  return ranges.map(([a, b]) => (a === b ? `${a}` : `${a}-${b}`)).join(',');
}

const firstCell = (line) => line.trim().replace(/^\|/, '').split('|')[0].trim();

// An entry names its lines by number ("lines: 3-12"), by a pattern with or without the header line
// ("grep: '^\\d+\\t0\\t', header: true"), or as the block that starts with a given line and ends before the
// next blank line ("block: '| check |'"), optionally cut to the table rows whose first cell is listed
// ("rows: [rdma-read]"). Only table rows may skip lines. Returns the 1-based numbers of the chosen lines.
export function chooseLines(lines, entry) {
  if (entry.lines) {
    const ranges = parseRanges(entry.lines);
    if (!entry.table && ranges.some(([a], i) => i > 0 && a !== ranges[i - 1][1] + 1)) {
      throw new Error(`lines ${entry.lines}: only table rows may skip lines, output text must stay contiguous`);
    }
    const numbers = ranges.flatMap(([a, b]) => Array.from({ length: b - a + 1 }, (_, i) => a + i));
    if (numbers.at(-1) > lines.length) throw new Error(`lines ${entry.lines}: the source has ${lines.length} lines`);
    return numbers;
  }
  if (entry.grep) {
    const pattern = new RegExp(entry.grep);
    const numbers = lines.flatMap((line, i) => ((entry.header && i === 0) || pattern.test(line) ? [i + 1] : []));
    if (numbers.length <= (entry.header ? 1 : 0)) throw new Error(`no line matches ${entry.grep}`);
    return numbers;
  }
  const start = lines.findIndex((line) => line.startsWith(entry.block));
  if (start === -1) throw new Error(`no line starts with ${JSON.stringify(entry.block)}`);
  let end = start;
  while (end + 1 < lines.length && lines[end + 1].trim() !== '') end += 1;
  let numbers = Array.from({ length: end - start + 1 }, (_, i) => start + 1 + i);
  if (entry.rows) {
    const wanted = new Set(entry.rows);
    const found = new Set();
    numbers = numbers.filter((n, i) => {
      if (i < 2) return true;
      const cell = firstCell(lines[n - 1]);
      if (!wanted.has(cell)) return false;
      found.add(cell);
      return true;
    });
    const absent = entry.rows.filter((row) => !found.has(row));
    if (absent.length) throw new Error(`rows ${absent.join(', ')} are not in the block`);
  }
  return numbers;
}

// Local paths and names of the machine that ran the lab are replaced, longest first.
export function makeRedactor({ roots = [], home, host, user }) {
  const pairs = [
    ...[...new Set(roots)].sort((a, b) => b.length - a.length).map((root) => [root, '<repo>']),
    ...(home ? [[home, '~']] : []),
    ...(host ? [[host, '<host>']] : []),
  ];
  const userPattern = user ? new RegExp(`\\b${user.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'g') : null;
  return (text) => {
    let out = text;
    for (const [from, to] of pairs) out = out.split(from).join(to);
    return userPattern ? out.replace(userPattern, '<user>') : out;
  };
}

// Markdown tables and TSV render as tables, anything else as text. TSV may keep only named columns.
export function formatOf(entry) {
  if (entry.format) return entry.format;
  return entry.block?.startsWith('|') ? 'md-table' : 'text';
}

function keepColumns(rows, columns) {
  const header = rows[0].split('\t');
  const index = columns.map((name) => {
    const i = header.indexOf(name);
    if (i === -1) throw new Error(`no column ${name} in ${header.join(', ')}`);
    return i;
  });
  return rows.map((row) => {
    const cells = row.split('\t');
    return index.map((i) => cells[i] ?? '').join('\t');
  });
}

export function buildExcerpt(id, entry, sourceText, redact) {
  const lines = sourceText.replace(/\r\n/g, '\n').split('\n');
  const format = formatOf(entry);
  if (format === 'text' && (entry.grep || entry.rows || entry.columns)) {
    throw new Error(`${id}: output text stays contiguous and whole. Use format tsv or a markdown table block to pick rows`);
  }
  const numbers = chooseLines(lines, { ...entry, table: format !== 'text' });
  let rows = numbers.map((n) => lines[n - 1]);
  if (entry.columns) rows = keepColumns(rows, entry.columns);
  const text = redact(rows.join('\n')).replace(/\s+$/, '');
  if (/\bP[L]AN\b/.test(text)) throw new Error(`${id}: the excerpt names the local design draft. Choose other lines`);
  return {
    id,
    run: String(entry.run),
    source: entry.source,
    lines: compress(numbers),
    ...(entry.columns ? { columns: entry.columns } : {}),
    format,
    text,
  };
}

export function sourcePath(source, { repoRoot, evidenceDir }) {
  return source.startsWith(EVIDENCE_PREFIX)
    ? `${evidenceDir}/${source.slice(EVIDENCE_PREFIX.length)}`
    : `${repoRoot}/${source}`;
}
