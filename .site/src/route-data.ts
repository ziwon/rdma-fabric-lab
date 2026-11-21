import { defineRouteMiddleware } from '@astrojs/starlight/route-data';
import { withBase } from './plugins/remark-base-links.mjs';

export const onRequest = defineRouteMiddleware((context) => {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  for (const action of context.locals.starlightRoute.entry.data.hero?.actions ?? []) {
    action.link = withBase(action.link, base);
  }
});
