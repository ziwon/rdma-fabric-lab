# The virtual GPU region cel1. sites/cel1.yaml is its single source of truth. The offline
# targets validate the model, render its inventory and its Helm releases. The CONFIRM=cel1
# targets change hosts.

FLEET_PY := PYTHONPATH=scripts $(BIN)/python -m fleet
INVENTORY_PY := PYTHONPATH=scripts $(BIN)/python -m inventory

CEL1_SITE := sites/cel1.yaml
CEL1_INVENTORY := ansible/inventories/cel1
CEL1_KUBECONFIG ?= $(or $(XDG_STATE_HOME),$(HOME)/.local/state)/rdma-fabric-lab/cel1/kubeconfig
# FLEET_INVENTORY may name a private site's inventory outside the repository instead.
# cel1 sets no ansible_host, so FLEET_CONNECT names a second inventory that says how to reach
# each host, such as the port forwards of the simulation guests.
FLEET_INVENTORY ?= $(CEL1_INVENTORY)
FLEET_CONNECT ?=
FLEET_PLAYBOOK = $(BIN)/ansible-playbook -i $(FLEET_INVENTORY) $(if $(FLEET_CONNECT),-i $(FLEET_CONNECT))
CEL1_GATE = test "$(CONFIRM)" = cel1 || { echo "make $@ changes every cel1 host. Nothing was changed. Rerun with CONFIRM=cel1." >&2; exit 2; }
CONNECT_GATE = test "$(FLEET_INVENTORY)" != "$(CEL1_INVENTORY)" -o -n "$(FLEET_CONNECT)" || \
	{ echo "make $@: $(CEL1_INVENTORY) sets no ansible_host. Pass FLEET_CONNECT=<inventory> with the address of each host." >&2; exit 2; }
INVENTORY_DIR = $(if $(wildcard ansible/inventories/$(INVENTORY)/hosts.yml),ansible/inventories/$(INVENTORY),$(INVENTORY))

.PHONY: fleet-site-check fleet-render fleet-render-check inventory-check values-check fleet-check
fleet-site-check: venv ## Offline: validate sites/cel1.yaml, its names, address plan and fabric cabling
	$(FLEET_PY) site-check $(CEL1_SITE) --lab-plan ansible/inventories/lab/group_vars/all

fleet-render: venv ## Write ansible/inventories/cel1 from sites/cel1.yaml
	$(FLEET_PY) render $(CEL1_SITE) --out $(CEL1_INVENTORY)

fleet-render-check: venv ## Offline: fail when ansible/inventories/cel1 differs from what sites/cel1.yaml renders
	$(FLEET_PY) render $(CEL1_SITE) --out $(CEL1_INVENTORY) --check

# The lab may lean on the role defaults of the contract. Any other inventory sets every variable.
inventory-check: venv ## Offline: check INVENTORY=<lab|cel1|dir> against the inventory contract of ADR 0001 section 4
	@test -n "$(INVENTORY)" || { echo "make inventory-check needs INVENTORY=<lab|cel1|dir>" >&2; exit 2; }
	$(INVENTORY_PY) check -i $(INVENTORY_DIR) $(if $(filter lab,$(INVENTORY)),,--explicit)

values-check: tools ## Offline: every key in the values files of ENV=<env> must exist in its chart's values.yaml at the locked version
	@test -n "$(ENV)" || { echo "make values-check needs ENV=<env>" >&2; exit 2; }
	$(FLEET_PY) values-keys --env $(ENV) --helm $(TOOLS_BIN)/helm

fleet-check: venv ## Offline: site model, rendered inventory, contract, values keys and addons-template of cel1
	$(MAKE) --no-print-directory fleet-site-check fleet-render-check
	$(MAKE) --no-print-directory inventory-check INVENTORY=cel1
	$(MAKE) --no-print-directory values-check addons-template ENV=cel1

.PHONY: fleet-k8s fleet-addons fleet-rsyslog
fleet-k8s: ## Install Kubernetes on cel1 with ansible/playbooks/kubernetes.yml. Refuses without CONFIRM=cel1. FLEET_CONNECT=<inventory>
	@$(CEL1_GATE)
	@$(CONNECT_GATE)
	$(MAKE) --no-print-directory versions SCOPE=fleet
	$(FLEET_PLAYBOOK) ansible/playbooks/kubernetes.yml $(ARGS)

fleet-addons: ## helm upgrade --install the cel1 releases with the cel1 kubeconfig. Refuses without CONFIRM=cel1
	@$(CEL1_GATE)
	@test -f "$(CEL1_KUBECONFIG)" || { echo "make $@: no kubeconfig at $(CEL1_KUBECONFIG). Run make fleet-k8s first or set CEL1_KUBECONFIG." >&2; exit 2; }
	$(MAKE) --no-print-directory versions SCOPE=fleet
	$(MAKE) --no-print-directory addons ENV=cel1 KUBECONFIG_FILE=$(CEL1_KUBECONFIG)

fleet-rsyslog: ## Forward syslog of every cel1 node to the site syslog server. Refuses without CONFIRM=cel1. FLEET_CONNECT=<inventory>
	@$(CEL1_GATE)
	@$(CONNECT_GATE)
	$(MAKE) --no-print-directory venv
	$(FLEET_PLAYBOOK) ansible/playbooks/fleet/rsyslog-client.yml $(ARGS)
