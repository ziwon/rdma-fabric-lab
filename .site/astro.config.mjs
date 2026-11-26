import sitemap from '@astrojs/sitemap';
import starlight from '@astrojs/starlight';
import mermaid from 'astro-mermaid';
import { defineConfig } from 'astro/config';
import { fileURLToPath } from 'node:url';
import rehypeKatex from 'rehype-katex';
import remarkMath from 'remark-math';
import starlightGitHubAlerts from 'starlight-github-alerts';
import starlightImageZoom from 'starlight-image-zoom';
import starlightLinksValidator from 'starlight-links-validator';
import starlightSidebarTopics from 'starlight-sidebar-topics';
import rehypeCodeBreaks from './src/plugins/rehype-code-breaks.mjs';
import remarkBaseLinks from './src/plugins/remark-base-links.mjs';
import { repoUrl } from './scripts/lib/repo.mjs';
import { base, site } from './scripts/lib/site.mjs';

// Components that show a repository file read it from here at build time.
process.env.RFL_REPO_ROOT ??= fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  site,
  base: base || '/',
  trailingSlash: 'always',
  markdown: {
    remarkPlugins: [remarkMath, [remarkBaseLinks, { base }]],
    rehypePlugins: [rehypeKatex, rehypeCodeBreaks],
  },
  integrations: [
    mermaid(),
    sitemap(),
    starlight({
      title: 'RDMA Fabric Lab',
      description:
        'A software-only lab for RDMA, InfiniBand management and RoCE congestion control: the concepts, the experiments, and how to run them.',
      favicon: '/favicon.svg',
      logo: { dark: './src/assets/logo.svg', light: './src/assets/logo-light.svg', alt: '' },
      customCss: ['./src/styles/custom.css'],
      routeMiddleware: './src/route-data.ts',
      components: {
        Hero: './src/components/Hero.astro',
        ThemeProvider: './src/components/ThemeProvider.astro',
        ThemeSelect: './src/components/ThemeSelect.astro',
        SiteTitle: './src/components/SiteTitle.astro',
      },
      social: [{ icon: 'github', label: 'GitHub', href: repoUrl }],
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      plugins: [
        starlightGitHubAlerts(),
        starlightImageZoom(),
        starlightLinksValidator(),
        starlightSidebarTopics(
          [
            {
              label: 'Start',
              link: '/start/',
              icon: 'rocket',
              items: [{ autogenerate: { directory: 'start' } }],
            },
            {
              label: 'Concepts',
              link: '/concepts/',
              icon: 'open-book',
              items: [{ autogenerate: { directory: 'concepts' } }],
            },
            {
              label: 'Labs',
              link: '/labs/',
              icon: 'laptop',
              items: [
                { label: 'All labs', link: '/labs/' },
                { label: 'Track A: Soft-RoCE and Kubernetes', items: [{ autogenerate: { directory: 'labs/track-a' } }] },
                { label: 'Track B: InfiniBand management', items: [{ autogenerate: { directory: 'labs/track-b' } }] },
                { label: 'Track C: RoCE congestion control', items: [{ autogenerate: { directory: 'labs/track-c' } }] },
                { label: 'cel1 simulation', items: [{ autogenerate: { directory: 'labs/cel1' } }] },
              ],
            },
            {
              label: 'Talks',
              link: '/slides/',
              icon: 'document',
              items: [{ label: 'All talks', link: '/slides/' }],
            },
            {
              label: 'Repository',
              link: '/repository/',
              icon: 'seti:folder',
              items: [{ autogenerate: { directory: 'repository' } }],
            },
            {
              label: 'Reference',
              link: '/reference/',
              icon: 'document',
              items: [
                { autogenerate: { directory: 'reference' } },
                {
                  label: 'Repository documents',
                  collapsed: true,
                  items: [{ autogenerate: { directory: 'docs', collapsed: true } }],
                },
              ],
            },
          ],
          { exclude: ['/'] },
        ),
      ],
    }),
  ],
});
