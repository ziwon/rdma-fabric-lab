# The virtual GPU region cel1. sites/cel1.yaml is its single source of truth. The offline
# targets validate the model, render its inventory, its simulation profile and its Helm releases.
# The CONFIRM=cel1 targets change hosts. cel1 is invented, so they reach only the guests of its
# simulation: FLEET_CONNECT names the inventory.yml that lab.sh up wrote for LAB_PROFILE=cel1.

FLEET_PY := PYTHONPATH=scripts $(BIN)/python -m fleet
INVENTORY_PY := PYTHONPATH=scripts $(BIN)/python -m inventory

CEL1_SITE := sites/cel1.yaml
CEL1_INVENTORY := ansible/inventories/cel1
CEL1_KUBECONFIG ?= $(or $(XDG_STATE_HOME),$(HOME)/.local/state)/rdma-fabric-lab/cel1/kubeconfig
CEL1_SIM_PROFILE := tests/integration/qemu-lab/profiles/cel1.tsv
# The values of the repo-local chart that holds the rail networks of cel1 (ADR 0007).
CEL1_RAIL_VALUES := helm/rail-networks/values-cel1.yaml
# FLEET_INVENTORY may name a private site's inventory outside the repository instead.
# cel1 sets no ansible_host, so FLEET_CONNECT names a second inventory that says how to reach
# each host, such as the port forwards of the simulation guests.
FLEET_INVENTORY ?= $(CEL1_INVENTORY)
FLEET_CONNECT ?=
FLEET_INVENTORIES = -i $(FLEET_INVENTORY) $(if $(FLEET_CONNECT),-i $(FLEET_CONNECT))
FLEET_PLAYBOOK = $(BIN)/ansible-playbook $(FLEET_INVENTORIES)
CEL1_GATE = test "$(CONFIRM)" = cel1 || { echo "make $@ changes every cel1 host. Nothing was changed. Rerun with CONFIRM=cel1." >&2; exit 2; }
# Reads only local files. A host of an invented site must point at a simulation guest on 127.0.0.1.
SIM_GATE = $(MAKE) -s --no-print-directory venv && $(FLEET_PY) sim-gate --target $@ $(foreach s,$(wildcard sites/*.yaml),--site $(s)) $(FLEET_INVENTORIES)
INVENTORY_DIR = $(if $(wildcard ansible/inventories/$(INVENTORY)/hosts.yml),ansible/inventories/$(INVENTORY),$(INVENTORY))

.PHONY: fleet-site-check fleet-render fleet-render-check fleet-sim-profile fleet-sim-profile-check inventory-check values-check fleet-check
fleet-site-check: venv ## Offline: validate sites/cel1.yaml, its names, address plan and fabric cabling
	$(FLEET_PY) site-check $(CEL1_SITE) --lab-plan ansible/inventories/lab/group_vars/all

fleet-render: venv ## Write ansible/inventories/cel1 and the rail network values from sites/cel1.yaml
	$(FLEET_PY) render $(CEL1_SITE) --out $(CEL1_INVENTORY) --rail-values $(CEL1_RAIL_VALUES)

fleet-render-check: venv ## Offline: fail when ansible/inventories/cel1 or the rail network values differ from what sites/cel1.yaml renders
	$(FLEET_PY) render $(CEL1_SITE) --out $(CEL1_INVENTORY) --rail-values $(CEL1_RAIL_VALUES) --check

fleet-sim-profile: venv ## Write the QEMU harness profile of the cel1 simulation from sites/cel1.yaml
	$(FLEET_PY) sim-profile $(CEL1_SITE) --inventory $(CEL1_INVENTORY) --out $(CEL1_SIM_PROFILE)

fleet-sim-profile-check: venv ## Offline: fail when the cel1 simulation profile differs from what sites/cel1.yaml renders
	$(FLEET_PY) sim-profile $(CEL1_SITE) --inventory $(CEL1_INVENTORY) --out $(CEL1_SIM_PROFILE) --check

# The lab may lean on the role defaults of the contract. Any other inventory sets every variable.
inventory-check: venv ## Offline: check INVENTORY=<lab|cel1|dir> against the inventory contract of ADR 0001 section 4
	@test -n "$(INVENTORY)" || { echo "make inventory-check needs INVENTORY=<lab|cel1|dir>" >&2; exit 2; }
	$(INVENTORY_PY) check -i $(INVENTORY_DIR) $(if $(filter lab,$(INVENTORY)),,--explicit)

values-check: tools ## Offline: every key in the values files of ENV=<env> must exist in its chart's values.yaml at the locked version
	@test -n "$(ENV)" || { echo "make values-check needs ENV=<env>" >&2; exit 2; }
	$(FLEET_PY) values-keys --env $(ENV) --helm $(TOOLS_BIN)/helm

fleet-check: venv ## Offline: site model, rendered inventory, simulation profile, contract, values keys and addons-template of cel1
	$(MAKE) --no-print-directory fleet-site-check fleet-render-check fleet-sim-profile-check
	$(MAKE) --no-print-directory inventory-check INVENTORY=cel1
	$(MAKE) --no-print-directory values-check addons-template ENV=cel1

.PHONY: fleet-sim-stand-ins fleet-sim-test fleet-k8s fleet-addons fleet-rsyslog
# The power commands of the simulation. LAB_DIR, when set, moves the guests' files as for lab.sh.
CEL1_SIM_HARNESS = env LAB_PROFILE=cel1 $(LAB_HARNESS)

fleet-sim-test: venv ## Run the cel1-sim-baseline scenario through rfl, VIP holder power cut included. Refuses without FLEET_CONNECT=<simulation inventory>
	@$(SIM_GATE)
	PYTHONPATH=scripts $(BIN)/python -m run.cel1 $(FLEET_INVENTORIES) $(if $(RUN),--run-id $(RUN)) \
		--power-off '$(CEL1_SIM_HARNESS) kill {guest}' --power-on '$(CEL1_SIM_HARNESS) start {guest}'

fleet-sim-stand-ins: ## Simulation only: the NVIDIA runtime stand-in on nvidia nodes and the syslog server stand-in. Refuses without CONFIRM=cel1 and FLEET_CONNECT=<simulation inventory>
	@$(CEL1_GATE)
	@$(SIM_GATE)
	$(FLEET_PLAYBOOK) ansible/playbooks/simulation-stand-ins.yml $(ARGS)

fleet-k8s: ## Install Kubernetes on cel1 with ansible/playbooks/kubernetes.yml. Refuses without CONFIRM=cel1 and FLEET_CONNECT=<simulation inventory>
	@$(CEL1_GATE)
	@$(SIM_GATE)
	$(MAKE) --no-print-directory versions SCOPE=fleet
	$(FLEET_PLAYBOOK) ansible/playbooks/kubernetes.yml $(ARGS)

fleet-addons: ## helm upgrade --install the cel1 releases with the cel1 kubeconfig. Refuses without CONFIRM=cel1 and FLEET_CONNECT=<simulation inventory>
	@$(CEL1_GATE)
	@test -f "$(CEL1_KUBECONFIG)" || { echo "make $@: no kubeconfig at $(CEL1_KUBECONFIG). Run make fleet-k8s first or set CEL1_KUBECONFIG." >&2; exit 2; }
	@$(SIM_GATE) --kubeconfig $(CEL1_KUBECONFIG)
	$(MAKE) --no-print-directory versions SCOPE=fleet
	$(MAKE) --no-print-directory addons ENV=cel1 KUBECONFIG_FILE=$(CEL1_KUBECONFIG)

fleet-rsyslog: ## Forward syslog of every cel1 node to the site syslog server. Refuses without CONFIRM=cel1 and FLEET_CONNECT=<simulation inventory>
	@$(CEL1_GATE)
	@$(SIM_GATE)
	$(FLEET_PLAYBOOK) ansible/playbooks/fleet/rsyslog-client.yml $(ARGS)
