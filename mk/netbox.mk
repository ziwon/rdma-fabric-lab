# NetBox (ADR 0008): the cel1 site model exported to a NetBox that runs only inside the guest of the
# netbox harness profile. Its web port reaches the host on 127.0.0.1:22352. Every target that talks to
# NetBox reads the API token over SSH from the guest and keeps it in memory only.
NETBOX_PY := PYTHONPATH=scripts $(BIN)/python -m netbox
NETBOX_INVENTORIES = $(foreach i,$(shell env LAB_PROFILE=netbox $(if $(LAB_DIR),LAB_DIR=$(LAB_DIR)) $(LAB_HARNESS) inventories),-i $(i))

.PHONY: netbox-lock netbox-guest netbox-render netbox-sync netbox-diff netbox-test netbox-screenshots

netbox-lock: venv
	$(RFL) versions --scope netbox

netbox-guest: netbox-lock ## Boot or reuse the NetBox guest and start NetBox in it with ansible/playbooks/netbox-guest.yml
	LAB_PROFILE=netbox $(LAB_HARNESS) up
	$(BIN)/ansible-playbook $(NETBOX_INVENTORIES) ansible/playbooks/netbox-guest.yml

netbox-render: venv ## Offline: print the NetBox objects sites/cel1.yaml asks for, as JSON
	$(NETBOX_PY) render $(CEL1_SITE)

netbox-sync: venv ## Make NetBox in the guest hold exactly the objects of sites/cel1.yaml that carry the managed tag
	$(NETBOX_PY) sync $(CEL1_SITE) $(NETBOX_INVENTORIES)

netbox-diff: venv ## Read-only: list how NetBox in the guest differs from sites/cel1.yaml. Exit 1 on a difference
	$(NETBOX_PY) diff $(CEL1_SITE) $(NETBOX_INVENTORIES)

netbox-test: venv ## Run the cel1-netbox scenario through rfl against the NetBox guest
	PYTHONPATH=scripts $(BIN)/python -m run.netbox $(NETBOX_INVENTORIES) $(if $(RUN),--run-id $(RUN)) $(if $(CONFIG),--config $(CONFIG))

# NODE_PATH must reach playwright-core, and CHROMIUM may name a browser binary. The admin password goes
# from the guest to the browser through the environment of one command and is never written.
NETBOX_SHOTS ?= .site/src/assets/screenshots/netbox
netbox-screenshots: venv ## Screenshots of the site model in the NetBox guest into NETBOX_SHOTS, for the learning site
	@NETBOX_PASSWORD=$$(PYTHONPATH=scripts $(BIN)/python -c 'import sys; from pathlib import Path; from netbox import client; print(client.secrets([Path(p) for p in sys.argv[1:]])["admin_password"])' $(subst -i ,,$(NETBOX_INVENTORIES))) \
	  node tests/integration/qemu-lab/netbox-screenshots.mjs $(NETBOX_SHOTS)
