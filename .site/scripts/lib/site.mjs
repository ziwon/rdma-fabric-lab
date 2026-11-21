const url = new URL(process.env.SITE_URL || 'https://rdma.restack.tech/');

export const site = url.origin;
export const base = url.pathname.replace(/\/$/, '');
