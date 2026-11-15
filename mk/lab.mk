# Track A lab. The harness targets boot QEMU guests on this host; everything else works on
# any Ubuntu guests the lab inventory can reach. LAB_PROFILE=<name> picks a harness profile
# under tests/integration/qemu-lab/profiles (default lab, the PLAN 3.2 guests).
LAB_PROFILE ?= lab
export LAB_PROFILE
LAB_HARNESS := tests/integration/qemu-lab/lab.sh
LAB_STATE := .lab$(if $(filter-out lab,$(LAB_PROFILE)),/$(LAB_PROFILE))
LAB_PAIR = $(shell $(LAB_HARNESS) inventories)
LAB_INVENTORY = $(word 1,$(LAB_PAIR))
LAB_OVERRIDE ?= $(wildcard $(word 2,$(LAB_PAIR)))
LAB_INVENTORIES = $(LAB_INVENTORY) $(LAB_OVERRIDE)
LAB_PLAYBOOK = $(BIN)/ansible-playbook $(foreach i,$(LAB_INVENTORIES),-i $(i))
# The harness forwards the API of the first control plane to 127.0.0.1. A VMware lab reaches
# the endpoint directly and leaves this empty.
LAB_API_URL ?= $(if $(wildcard $(LAB_STATE)/.listing.json),$(shell $(LAB_HARNESS) api-url))

.PHONY: lab-up lab-down lab-status lab-wipe lab-lock preflight bootstrap rxe-up

lab-up: venv ## QEMU harness: boot the guests of LAB_PROFILE and write their override inventory
	$(LAB_HARNESS) up

lab-down: ## QEMU harness: power off the guests and switches the harness started
	$(LAB_HARNESS) down

lab-status: ## QEMU harness: show which guests and switches run
	$(LAB_HARNESS) status

lab-wipe: ## QEMU harness: delete guest disks and seeds so the next lab-up starts from nothing
	$(LAB_HARNESS) wipe

lab-lock: venv
	$(RFL) versions --scope m1

preflight: lab-lock ## PLAN A0 read-only checks on the workers
	$(LAB_PLAYBOOK) ansible/playbooks/preflight.yml

bootstrap: lab-lock ## Locked packages, rdma_rxe module and RDMA NIC address on the workers
	$(LAB_PLAYBOOK) ansible/playbooks/bootstrap.yml

rxe-up: lab-lock ## PLAN A0 checks, then rxe0 on the RDMA NIC through rfl-rxe.service
	$(LAB_PLAYBOOK) ansible/playbooks/rxe.yml

.PHONY: k8s-lock k8s
k8s-lock: venv
	$(RFL) versions --scope m2

k8s: k8s-lock ## kubeadm cluster of ansible/playbooks/kubernetes.yml, admin kubeconfig to .lab/kubeconfig. Cilium follows with make addons ENV=lab
	$(LAB_PLAYBOOK) ansible/playbooks/kubernetes.yml $(if $(LAB_API_URL),-e rfl_kubeconfig_server=$(LAB_API_URL))

.PHONY: doctor
doctor: venv ## Read-only host check: virtualization, tools, PLAN 3.1 resources, address plan overlaps. LAB_PLAN=<file> tries another plan
	PYTHONPATH=scripts $(BIN)/python -m doctor $(if $(LAB_PLAN),--plan $(LAB_PLAN))

SCENARIO ?= vm-rxe-baseline
LAB_RUN = PYTHONPATH=scripts $(BIN)/python -m

.PHONY: rxe-test
rxe-test: lab-lock ## Run a Track A scenario through rfl. SCENARIO=vm-rxe-baseline (default), vm-rxe-reboot or vm-rxe-rerun
	$(LAB_RUN) run.rxe --scenario $(SCENARIO) $(foreach i,$(LAB_INVENTORIES),-i $(i)) $(if $(RUN),--run-id $(RUN))

.PHONY: k8s-test
k8s-test: k8s-lock ## Run the k8s-baseline scenario through rfl: nodes, Cilium, pod traffic, DNS, Service, NetworkPolicy, VXLAN path
	$(LAB_RUN) run.k8s $(foreach i,$(LAB_INVENTORIES),-i $(i)) $(if $(RUN),--run-id $(RUN))

.PHONY: k8s-ha-test
k8s-ha-test: k8s-lock ## LAB_PROFILE=ha: cut the power of the VIP holder and show the API answers on the VIP again
	$(LAB_RUN) run.ha $(foreach i,$(LAB_INVENTORIES),-i $(i)) $(if $(RUN),--run-id $(RUN)) \
		--power-off '$(LAB_HARNESS) kill {guest}' --power-on '$(LAB_HARNESS) start {guest}'

.PHONY: cleanup
cleanup: venv ## Stop the processes and delete the guest files of run RUN=<run-id>. The device and the guests stay
	@test -n "$(RUN)" || { echo "make cleanup needs RUN=<run-id>" >&2; exit 2; }
	$(LAB_RUN) cleanup --run-id $(RUN) $(foreach i,$(LAB_INVENTORIES),-i $(i))

.PHONY: collect
collect: lab-lock ## Snapshot host and workers as a run and compare every lab.* lock value with the workers
	$(LAB_RUN) collect $(foreach i,$(LAB_INVENTORIES),-i $(i)) $(if $(RUN),--run-id $(RUN))

.PHONY: reset-lab
reset-lab: venv ## Delete rxe0, rfl-rxe.service and the RDMA NIC address on the workers. Refuses without CONFIRM=lab
	@test "$(CONFIRM)" = lab || { echo "make reset-lab deletes rxe0 and the RDMA NIC address on every worker. Nothing was changed. Rerun with CONFIRM=lab." >&2; exit 2; }
	$(LAB_PLAYBOOK) ansible/playbooks/reset.yml -e lab_reset_confirm=$(CONFIRM)
