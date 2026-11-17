# The learning site in .site/, built with Astro and Starlight. npm installs exactly what .site/package-lock.json
# pins, and only when the lock changed since the last install.
SITE_DIR := .site
SITE_INSTALLED := $(SITE_DIR)/node_modules/.package-lock.json

.PHONY: site site-dev

$(SITE_INSTALLED): $(SITE_DIR)/package-lock.json
	cd $(SITE_DIR) && npm ci

site: $(SITE_INSTALLED) ## Build the learning site into .site/dist. Fails on a broken link or a scenario check no page shows
	cd $(SITE_DIR) && npm test && npm run build

site-dev: $(SITE_INSTALLED) ## Serve the learning site with live reload at http://localhost:4321/rdma-fabric-lab/
	cd $(SITE_DIR) && npm run dev
