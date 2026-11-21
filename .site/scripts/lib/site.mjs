const url = new URL(process.env.SITE_URL || 'https://rdma-fabric.restack.tech/');

export const site = url.origin;
export const base = url.pathname.replace(/\/$/, '');
