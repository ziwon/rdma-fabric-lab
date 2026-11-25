import type { APIRoute } from 'astro';
import deck from '../../../../../docs/slides/kubernetes-rdma-wiring.html?raw';
import dmSans from '@fontsource/dm-sans/files/dm-sans-latin-400-normal.woff2?inline';
import dmSansBold from '@fontsource/dm-sans/files/dm-sans-latin-700-normal.woff2?inline';
import plexMono from '@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2?inline';

export const prerender = true;

const face = (family: string, weight: number, url: string) =>
  `@font-face{font-family:"${family}";font-weight:${weight};font-style:normal;font-display:swap;src:url("${url}") format("woff2")}`;

export const GET: APIRoute = () =>
  new Response(deck.replace('</head>', `<style>${[
    face('DM Sans', 400, dmSans),
    face('DM Sans', 700, dmSansBold),
    face('IBM Plex Mono', 400, plexMono),
  ].join('')}</style></head>`), {
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
