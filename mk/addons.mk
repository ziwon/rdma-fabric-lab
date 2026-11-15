# Helm releases of helm/releases.yaml and the pinned tools they need.
LOCK_VALUE = $(shell $(BIN)/python -c "import yaml,sys; print(yaml.safe_load(open('versions.lock.yaml'))['components'][sys.argv[1]]['value'])" $(1))
TOOLS_BIN := .tools/bin
HELM_VERSION = $(call LOCK_VALUE,tooling.helm)
KUBECONFORM_VERSION = $(call LOCK_VALUE,tooling.kubeconform)
ADDONS := PYTHONPATH=scripts $(BIN)/python -m addons

$(TOOLS_BIN)/.helm-%: | venv
	scripts/install-tool.sh helm https://get.helm.sh/helm-$*-linux-amd64.tar.gz $(call LOCK_VALUE,tooling.helm_archive) linux-amd64/helm
	touch $@

$(TOOLS_BIN)/.kubeconform-%: | venv
	scripts/install-tool.sh kubeconform https://github.com/yannh/kubeconform/releases/download/$*/kubeconform-linux-amd64.tar.gz \
		$(call LOCK_VALUE,tooling.kubeconform_archive) kubeconform
	touch $@

.PHONY: tools addons addons-template
tools: venv ## Install helm and kubeconform at their locked versions into .tools/bin
	$(MAKE) --no-print-directory $(TOOLS_BIN)/.helm-$(HELM_VERSION) $(TOOLS_BIN)/.kubeconform-$(KUBECONFORM_VERSION)

addons: tools ## helm upgrade --install the releases of ENV=<lab|cel1>. KUBECONFIG_FILE=<path>, RELEASE=<name> narrows it
	@test -n "$(ENV)" -a -n "$(KUBECONFIG_FILE)" || { echo "make addons needs ENV=<env> KUBECONFIG_FILE=<path>" >&2; exit 2; }
	$(ADDONS) apply --env $(ENV) --kubeconfig $(KUBECONFIG_FILE) $(if $(RELEASE),--release $(RELEASE)) $(if $(FORCE),--force)

addons-template: tools ## Offline: render the releases of ENV=<lab|cel1> and validate them with kubeconform
	@test -n "$(ENV)" || { echo "make addons-template needs ENV=<env>" >&2; exit 2; }
	$(ADDONS) template --env $(ENV) --out .tools/rendered $(if $(RELEASE),--release $(RELEASE))
