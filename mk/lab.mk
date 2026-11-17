# Track A lab. The harness targets boot QEMU guests on this host; everything else works on
# any Ubuntu guests the lab inventory can reach. LAB_PROFILE=<name> picks a harness profile
# under tests/integration/qemu-lab/profiles (default lab: cp1, worker1 and worker2).
LAB_PROFILE ?= lab
export LAB_PROFILE
LAB_HARNESS := tests/integration/qemu-lab/lab.sh
# LAB_DIR moves the guests' files of the profile, as for lab.sh.
LAB_STATE := $(or $(LAB_DIR),.lab$(if $(filter-out lab,$(LAB_PROFILE)),/$(LAB_PROFILE)))
# make 4.3 exports no variable to $(shell), so a LAB_DIR or LAB_PROFILE given on the command line reaches the
# harness only through this prefix.
LAB_HARNESS_ENV = env LAB_PROFILE=$(LAB_PROFILE)$(if $(LAB_DIR), LAB_DIR=$(LAB_DIR))
LAB_PAIR = $(shell $(LAB_HARNESS_ENV) $(LAB_HARNESS) inventories)
LAB_INVENTORY = $(word 1,$(LAB_PAIR))
LAB_OVERRIDE ?= $(wildcard $(word 2,$(LAB_PAIR)))
LAB_INVENTORIES = $(LAB_INVENTORY) $(LAB_OVERRIDE)
LAB_PLAYBOOK = $(BIN)/ansible-playbook $(foreach i,$(LAB_INVENTORIES),-i $(i))
# The harness forwards the API of the first control plane to 127.0.0.1. A VMware lab reaches
# the endpoint directly and leaves this empty.
LAB_API_URL ?= $(if $(wildcard $(LAB_STATE)/.listing.json),$(shell $(LAB_HARNESS_ENV) $(LAB_HARNESS) api-url))

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

preflight: lab-lock ## Read-only checks on the workers
	$(LAB_PLAYBOOK) ansible/playbooks/preflight.yml

bootstrap: lab-lock ## Locked packages, rdma_rxe module and RDMA NIC address on the workers
	$(LAB_PLAYBOOK) ansible/playbooks/bootstrap.yml

rxe-up: lab-lock ## Preflight checks, then rxe0 on the RDMA NIC through rfl-rxe.service
	$(LAB_PLAYBOOK) ansible/playbooks/rxe.yml

.PHONY: k8s-lock k8s
k8s-lock: venv
	$(RFL) versions --scope m2

k8s: k8s-lock ## kubeadm cluster of ansible/playbooks/kubernetes.yml, admin kubeconfig to .lab/kubeconfig. Cilium, Multus and Whereabouts follow with make addons ENV=lab
	$(LAB_PLAYBOOK) ansible/playbooks/kubernetes.yml $(if $(LAB_API_URL),-e rfl_kubeconfig_server=$(LAB_API_URL))

.PHONY: cni-migrate
cni-migrate: k8s-lock ## After make addons ENV=lab on a cluster built before Cilium got its own CNI directory: remove its old conflist from /etc/cni/net.d on every node
	$(LAB_PLAYBOOK) ansible/playbooks/cni-migrate.yml

.PHONY: doctor
doctor: venv ## Read-only host check: virtualization, tools, resource budget, address plan overlaps. LAB_PLAN=<file> tries another plan
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

.PHONY: k8s-hostnet-test
k8s-hostnet-test: k8s-lock ## Run k8s-hostnet-rdma: the vm-rxe-baseline tests between hostNetwork pods on the two workers
	$(RFL) versions --scope m3
	$(LAB_RUN) run.hostnet $(foreach i,$(LAB_INVENTORIES),-i $(i)) $(if $(RUN),--run-id $(RUN))

.PHONY: k8s-multus-test
k8s-multus-test: k8s-lock ## Run k8s-multus-net1: Multus net1 with Whereabouts beside Cilium, pod recreation and a worker reboot
	$(RFL) versions --scope m3
	$(LAB_RUN) run.multus test $(foreach i,$(LAB_INVENTORIES),-i $(i)) $(if $(RUN),--run-id $(RUN))

# Every required Track A scenario, target:scenario, in the order they run. vm-rxe-reboot runs last because
# k8s-baseline reads the Cilium agents once, and a rebooted node's agent turns Ready up to 30 s after its boot.
TRACK_A_STEPS := rxe-test:vm-rxe-baseline rxe-test:vm-rxe-rerun k8s-test:k8s-baseline k8s-hostnet-test:k8s-hostnet-rdma \
	k8s-multus-test:k8s-multus-net1 rxe-test:vm-rxe-reboot

.PHONY: track-a
track-a: lab-lock ## Every required Track A scenario in order, on a lab with Kubernetes, the addons and the RDMA test image. Each still runs when an earlier one failed
	@failed=""; \
	for step in $(TRACK_A_STEPS); do \
	  $(MAKE) --no-print-directory $${step%%:*} SCENARIO=$${step#*:} || failed="$$failed $${step#*:}"; \
	done; \
	echo "make track-a: failed:$${failed:- none}"; \
	test -z "$$failed"

.PHONY: cleanup
cleanup: venv ## Remove what run RUN=<run-id> left on the guests of LAB_PROFILE and on this host: its processes, files, namespaces and containers
	@test -n "$(RUN)" || { echo "make cleanup needs RUN=<run-id>" >&2; exit 2; }
	$(LAB_RUN) cleanup --run-id $(RUN) $(foreach i,$(LAB_INVENTORIES),-i $(i))

.PHONY: collect
collect: lab-lock ## Snapshot host and workers as a run and compare every lab.* lock value with the workers
	$(LAB_RUN) collect $(foreach i,$(LAB_INVENTORIES),-i $(i)) $(if $(RUN),--run-id $(RUN))

.PHONY: reset-lab
reset-lab: venv ## Delete rxe0, rfl-rxe.service and the RDMA NIC address on the workers. Refuses without CONFIRM=lab
	@test "$(CONFIRM)" = lab || { echo "make reset-lab deletes rxe0 and the RDMA NIC address on every worker. Nothing was changed. Rerun with CONFIRM=lab." >&2; exit 2; }
	$(LAB_PLAYBOOK) ansible/playbooks/reset.yml -e lab_reset_confirm=$(CONFIRM)

# The RDMA test image of the hostNetwork RDMA pods. The Track B guest builds it, so the host starts no
# container. BUILDER_LAB_DIR moves that guest's files, by default next to LAB_DIR when that is set.
RDMA_TOOLS_ARCHIVE ?= $(LAB_STATE)/images/rdma-tools.tar
BUILDER_LAB_DIR ?= $(if $(LAB_DIR),$(LAB_DIR)/builder)
BUILDER_ENV = env LAB_PROFILE=ibsim $(if $(BUILDER_LAB_DIR),LAB_DIR=$(BUILDER_LAB_DIR))

.PHONY: rdma-tools-image rdma-tools-load
rdma-tools-image: venv ## Build images/rdma-tools inside the Track B guest into RDMA_TOOLS_ARCHIVE. Fails when its digest is not lab.rdma_tools_image
	$(BUILDER_ENV) $(MAKE) --no-print-directory track-b-guest $(if $(BUILDER_LAB_DIR),LAB_DIR=$(BUILDER_LAB_DIR))
	$(LAB_RUN) run.image $(foreach i,$(shell $(BUILDER_ENV) $(LAB_HARNESS) inventories),-i $(i)) --out $(RDMA_TOOLS_ARCHIVE)

rdma-tools-load: lab-lock ## Import RDMA_TOOLS_ARCHIVE into containerd on every worker without a registry, by its locked digest
	$(RFL) versions --scope m3
	$(LAB_PLAYBOOK) ansible/playbooks/rdma-tools.yml -e rdma_tools_archive=$(abspath $(RDMA_TOOLS_ARCHIVE))
