SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-builtin-rules

VENV := .venv
BIN := $(VENV)/bin
# Must equal tooling.python in versions.lock.yaml. make versions fails when they differ.
PYTHON_VERSION := 3.12
VENV_STAMP := $(VENV)/.built-python$(PYTHON_VERSION)
RFL := PYTHONPATH=scripts $(BIN)/python -m rfl
ANSIBLE_DIR := ansible
export ANSIBLE_CONFIG := $(CURDIR)/$(ANSIBLE_DIR)/ansible.cfg
export PATH := $(CURDIR)/$(BIN):$(PATH)

.PHONY: help venv lint test versions validate report

help: ## List the targets that exist today
	@awk -F':.*## ' '/^[a-zA-Z0-9_-]+:.*## /{printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST) | sort

# The stamp names the interpreter version, so a venv built on another Python is rebuilt.
$(VENV_STAMP): requirements.txt
	rm -rf $(VENV)
	uv venv -q $(VENV) --python $(PYTHON_VERSION)
	uv pip install -q -r requirements.txt --python $(BIN)/python
	touch $@

$(BIN)/ansible-playbook: $(VENV_STAMP)

venv: $(BIN)/ansible-playbook ## Build .venv with the pinned control-node tooling

lint: venv ## yamllint, ansible-lint, shellcheck, scenario specs, version lock
	$(BIN)/yamllint --strict .
	cd $(ANSIBLE_DIR) && $(CURDIR)/$(BIN)/ansible-lint --offline
	git ls-files -co --exclude-standard -z -- '*.sh' | xargs -0 -r shellcheck
	$(RFL) specs $$(git ls-files -co --exclude-standard -- 'tracks/*/scenarios/*.yaml')
	$(RFL) versions --environment
	@! git grep --untracked -nE '\bP''LAN\b' -- . ':!.gitignore' ':!docs/milestones/evidence' || { echo "tracked files must not point at the local design draft" >&2; exit 1; }

test: venv ## Unit tests
	$(BIN)/pytest -q tests/unit

versions: venv ## Validate versions.lock.yaml against this environment. SCOPE=<name> also refuses undecided components
	$(RFL) versions --environment $(if $(SCOPE),--scope $(SCOPE))

validate: venv ## Check result directories against the result contract. RUN=<run-id> narrows it
	$(RFL) validate $(if $(RUN),--run-id $(RUN))

report: venv ## Tally validated results, required and optional runs apart
	$(RFL) report $(if $(RUN),--run-id $(RUN))

-include mk/*.mk
