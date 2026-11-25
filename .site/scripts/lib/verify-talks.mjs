const textOf = (html) => html.replace(/<[^>]*>/g, '').replaceAll('&amp;', '&').replaceAll('&lt;', '<').replaceAll('&gt;', '>').replace(/\s+/g, ' ').trim();

export function verifyTalks({ pages, decks, status, base = '', site }) {
  const problems = [];
  const index = pages.get('/slides/') ?? '';
  if (!/<h1\b[^>]*>Talks<\/h1>/.test(index)) problems.push('/slides/: missing Talks page title');
  const entries = new Map([...index.matchAll(/<li\b[^>]*data-deck="([^"]+)"[^>]*>([\s\S]*?)<\/li>/g)].map(([, slug, html]) => [slug, html]));
  for (const deck of decks) {
    const html = entries.get(deck.slug) ?? '';
    const text = textOf(html);
    for (const value of [deck.title, `${deck.count} slides`, `${deck.minutes} minutes`, status]) {
      if (!text.includes(value)) problems.push(`/slides/: ${deck.slug} lacks ${value}`);
    }
    if (!html.includes(`href="${base}/slides/${deck.slug}/"`)) problems.push(`/slides/: missing browser link for ${deck.slug}`);
    if (!pages.has(`/slides/${deck.slug}/`)) problems.push(`/slides/${deck.slug}/: deck was not built`);
  }
  const home = pages.get('/') ?? '';
  if (/\bdata-has-sidebar(?:[=\s>])/.test(home)) problems.push('/: the home splash must not have a sidebar');
  const cards = [...home.matchAll(/<div\b[^>]*class="rfl-card-grid"[^>]*>([\s\S]*?)<\/div>/g)].map(([, html]) => html).join('');
  const cardLinks = [...cards.matchAll(/<a\b[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)];
  if (!cardLinks.some(([, href, label]) => href === `${base}/slides/` && textOf(label) === 'Talks')) {
    problems.push('/: navigation cards lack the Talks link');
  }
  for (const route of ['/start/', '/slides/']) {
    const html = pages.get(route) ?? '';
    const topics = /<ul\b[^>]*class="[^"]*\bstarlight-sidebar-topics\b[^"]*"[^>]*>([\s\S]*?)<\/ul>/.exec(html)?.[1] ?? '';
    const links = [...topics.matchAll(/<a\b[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)];
    if (!links.some(([, href, label]) => href === `${base}/slides/` && textOf(label) === 'Talks')) {
      problems.push(`${route}: sidebar lacks the Talks link`);
    } else {
      const index = links.findIndex(([, href]) => href === `${base}/slides/`);
      if (links[index - 1]?.[1] !== `${base}/labs/`) problems.push(`${route}: Talks must follow Labs in the sidebar`);
    }
  }
  const legacy = pages.get('/slides/kubernetes-rdma-wiring/') ?? '';
  const destination = `${base}/slides/multus-boot-race/`;
  if (!legacy.includes(`http-equiv="refresh" content="0; url=${destination}"`)) problems.push('legacy talk: missing meta refresh');
  if (!legacy.includes(`rel="canonical" href="${new URL(destination, site).href}"`)) problems.push('legacy talk: missing canonical link');
  if (!legacy.includes(`<a href="${destination}">`)) problems.push('legacy talk: missing plain link');
  return problems;
}
