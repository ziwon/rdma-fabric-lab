# The lab on VMware Workstation through Vagrant, the second hypervisor path beside the QEMU harness.
# docs/setup-vagrant.md installs VMware, Vagrant, the VMware Utility and the plugin. Each target that
# needs one of them checks first and refuses with the step that is missing.
#
# VAGRANT_DATA holds the large files: the image download, the box, Vagrant's home and the guest disks.
# VAGRANT_STATE holds the small ones in the gitignored .lab: keys, seeds and the override inventory.
VAGRANT_DATA ?= /data/rfl-vagrant
VAGRANT_HOME ?= $(VAGRANT_DATA)/home
VAGRANT_STATE := .lab/vagrant
VAGRANT_INVENTORY := $(VAGRANT_STATE)/inventory.yml
VAGRANT_ENV = env VAGRANT_CWD=$(CURDIR)/vagrant VAGRANT_HOME=$(VAGRANT_HOME) \
	VAGRANT_DOTFILE_PATH=$(VAGRANT_DATA)/dotfile VAGRANT_VMWARE_CLONE_DIRECTORY=$(VAGRANT_DATA)/machines \
	VAGRANT_DEFAULT_PROVIDER=vmware_desktop VAGRANT_CHECKPOINT_DISABLE=1 RFL_VAGRANT_STATE=$(CURDIR)/$(VAGRANT_STATE)
VAGRANT_PY = $(VAGRANT_ENV) PYTHONPATH=scripts $(BIN)/python -m vagrant
VAGRANT_CHECK = $(VAGRANT_PY) check --target $@ --need
VAGRANT = $(VAGRANT_ENV) vagrant

# The Vagrant guests reach the API at its endpoint through the host-only vmnet. The forward of the QEMU
# harness must not leak into make k8s when a harness lab of this checkout left its listing behind.
# Only a LAB_OVERRIDE the user set is compared, so parsing never runs lab.sh.
ifneq ($(origin LAB_OVERRIDE),file)
ifeq ($(abspath $(LAB_OVERRIDE)),$(abspath $(VAGRANT_INVENTORY)))
LAB_API_URL :=
endif
endif

.PHONY: vagrant-check vagrant-box vagrant-up vagrant-inventory vagrant-status vagrant-ssh vagrant-down vagrant-wipe

vagrant-check: venv ## Vagrant lab: report what make vagrant-up still lacks on this host
	@$(VAGRANT_CHECK) boot

vagrant-box: venv ## Vagrant lab: build the VMware box from lab.guest_image under VAGRANT_DATA. Needs no VMware
	$(MAKE) --no-print-directory versions SCOPE=vagrant
	$(VAGRANT_PY) box --data $(VAGRANT_DATA)

vagrant-up: venv ## Vagrant lab: boot cp1, worker1 and worker2 and write .lab/vagrant/inventory.yml. Refuses while a QEMU guest runs
	@$(VAGRANT_CHECK) boot
	$(MAKE) --no-print-directory vagrant-box
	$(VAGRANT_PY) box-add --data $(VAGRANT_DATA)
	$(VAGRANT_PY) seeds --state $(VAGRANT_STATE)
	$(VAGRANT) up
	$(VAGRANT_PY) inventory --state $(VAGRANT_STATE)

vagrant-inventory: venv ## Vagrant lab: rewrite .lab/vagrant/inventory.yml from the running guests
	@$(VAGRANT_CHECK) provider
	$(VAGRANT_PY) seeds --state $(VAGRANT_STATE)
	$(VAGRANT_PY) inventory --state $(VAGRANT_STATE)

vagrant-status: venv ## Vagrant lab: show the state of each guest
	@$(VAGRANT_CHECK) provider
	$(VAGRANT_PY) seeds --state $(VAGRANT_STATE)
	$(VAGRANT) status

vagrant-ssh: venv ## Vagrant lab: SSH into GUEST=<name>
	@test -n "$(GUEST)" || { echo "make vagrant-ssh needs GUEST=<cp1|worker1|worker2>" >&2; exit 2; }
	@$(VAGRANT_CHECK) provider
	$(VAGRANT_PY) seeds --state $(VAGRANT_STATE)
	$(VAGRANT) ssh $(GUEST)

vagrant-down: venv ## Vagrant lab: shut the guests down and keep their disks
	@$(VAGRANT_CHECK) provider
	$(VAGRANT_PY) seeds --state $(VAGRANT_STATE)
	$(VAGRANT) halt

vagrant-wipe: venv ## Vagrant lab: destroy the guests and their disks, seeds and inventory. The box and the image stay
	@$(VAGRANT_CHECK) provider
	$(VAGRANT_PY) seeds --state $(VAGRANT_STATE)
	$(VAGRANT) destroy --force
	rm -rf -- $(CURDIR)/$(VAGRANT_STATE)/guests
	rm -f -- $(CURDIR)/$(VAGRANT_INVENTORY) $(CURDIR)/$(VAGRANT_STATE)/ssh/known_hosts
