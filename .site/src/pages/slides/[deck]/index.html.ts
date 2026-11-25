// Serves every deck of docs/slides at /slides/<file name>/, with the slide fonts embedded.
import type { APIRoute, GetStaticPaths } from 'astro';
import { basename } from 'node:path';
import dmSans from '@fontsource/dm-sans/files/dm-sans-latin-400-normal.woff2?inline';
import dmSansBold from '@fontsource/dm-sans/files/dm-sans-latin-700-normal.woff2?inline';
import plexMono from '@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2?inline';

const files = import.meta.glob<string>('../../../../../docs/slides/*.html', { query: '?raw', import: 'default', eager: true });
const decks = new Map(Object.entries(files).map(([file, html]) => [basename(file, '.html'), html]));

export const prerender = true;

export const getStaticPaths: GetStaticPaths = () => [...decks.keys()].map((deck) => ({ params: { deck } }));

const face = (family: string, weight: number, url: string) =>
  `@font-face{font-family:"${family}";font-weight:${weight};font-style:normal;font-display:swap;src:url("${url}") format("woff2")}`;

export const GET: APIRoute = ({ params }) => {
  const html = params.deck === undefined ? undefined : decks.get(params.deck);
  if (html === undefined) return new Response('Deck not found', { status: 404 });
  return new Response(html.replace('</head>', `<style>${[
    face('DM Sans', 400, dmSans),
    face('DM Sans', 700, dmSansBold),
    face('IBM Plex Mono', 400, plexMono),
  ].join('')}</style></head>`), {
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
};
