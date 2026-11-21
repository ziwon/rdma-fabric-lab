// Screenshots of the cel1 site model in the NetBox guest, for the learning site (ADR 0008).
//
//   NETBOX_PASSWORD=... node netbox-screenshots.mjs <out dir> [base url]
//
// make netbox-screenshots reads the password from the guest over SSH and passes it in the environment
// only. playwright-core must be resolvable, for example with NODE_PATH. CHROMIUM names a browser binary
// when playwright's own is not installed.
import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';

// ESM ignores NODE_PATH, so playwright-core is resolved through it explicitly.
const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright-core', { paths: (process.env.NODE_PATH || '').split(':').filter(Boolean) }));

const [out, base = 'http://127.0.0.1:22352'] = process.argv.slice(2);
const password = process.env.NETBOX_PASSWORD;
if (!out || !password) {
  console.error('usage: NETBOX_PASSWORD=... node netbox-screenshots.mjs <out dir> [base url]');
  process.exit(2);
}
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.addInitScript(() => localStorage.setItem('netbox-color-mode', 'dark'));

await page.goto(`${base}/login/`);
await page.fill('#id_username', 'admin');
await page.fill('#id_password', password);
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

async function api(path) {
  const r = await page.request.get(`${base}/api/${path}`);
  if (!r.ok()) throw new Error(`${path}: HTTP ${r.status()}`);
  return (await r.json()).results;
}
const site = (await api('dcim/sites/?slug=cel1'))[0];
const dgx = (await api('dcim/devices/?name=cel1-dgx-01'))[0];
const rail0 = (await api(`dcim/interfaces/?device_id=${dgx.id}&name=rail0`))[0];

const shots = [
  ['site', `/dcim/sites/${site.id}/`],
  ['devices', `/dcim/devices/?site_id=${site.id}&per_page=50&ordering=name`],
  ['dgx-interfaces', `/dcim/devices/${dgx.id}/interfaces/`],
  ['rail0-trace', `/dcim/interfaces/${rail0.id}/trace/`],
  ['prefixes', `/ipam/prefixes/?tag=rfl-site-model&per_page=50&ordering=prefix`],
  ['ip-addresses', `/ipam/ip-addresses/?q=10.142.0.&per_page=50`],
  ['connections', `/dcim/interface-connections/?device_id=${dgx.id}&per_page=50`],
];
for (const [name, path] of shots) {
  await page.goto(base + path, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/netbox-${name}.png` });
  console.log(`${name}: ${path} -> ${out}/netbox-${name}.png`);
}
await browser.close();
