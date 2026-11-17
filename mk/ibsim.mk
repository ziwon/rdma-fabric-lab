# Track B: ibsim, OpenSM and infiniband-diags in one pinned image. ADR 0002 runs that image in the
# Docker of the guest of the ibsim harness profile, never on this host. The render targets are
# offline. Every other target reaches the guest over its 127.0.0.1 SSH forward.
TRACK_B_PY := PYTHONPATH=scripts $(BIN)/python -m ibsim
TRACK_B_NET := tracks/b-ibsim/topologies/cel1-compute.net
TRACK_B_SCENARIOS := $(basename $(notdir $(wildcard tracks/b-ibsim/scenarios/*.yaml)))
TRACK_B_INVENTORIES = $(foreach i,$(shell env LAB_PROFILE=ibsim $(if $(LAB_DIR),LAB_DIR=$(LAB_DIR)) $(LAB_HARNESS) inventories),-i $(i))

.PHONY: track-b-render track-b-render-check track-b-lock track-b-guest track-b-image track-b track-b-breaks

track-b-render: venv ## Write tracks/b-ibsim/topologies/cel1-compute.net from sites/cel1.yaml fabrics.compute
	$(TRACK_B_PY) render --site $(CEL1_SITE) --fabric compute --out $(TRACK_B_NET)

track-b-render-check: venv ## Offline: fail when cel1-compute.net differs from what sites/cel1.yaml renders
	$(TRACK_B_PY) render --site $(CEL1_SITE) --fabric compute --out $(TRACK_B_NET) --check

track-b-lock: venv
	$(RFL) versions --scope m4

track-b-guest: track-b-lock ## Boot or reuse the Track B guest and prepare it with ansible/playbooks/track-b-guest.yml
	LAB_PROFILE=ibsim $(LAB_HARNESS) up
	$(BIN)/ansible-playbook $(TRACK_B_INVENTORIES) ansible/playbooks/track-b-guest.yml

track-b-image: track-b-lock ## Build or reuse rfl-ibsim:m4 inside the Track B guest. Fails when its ID is not ibsim.image
	$(TRACK_B_PY) image $(TRACK_B_INVENTORIES) $(if $(BUILD_LOG),--log $(BUILD_LOG))

# One run id for every scenario. Each scenario still runs when an earlier one failed.
track-b: track-b-guest track-b-image ## Run every Track B scenario inside the Track B guest under one run id. RUN=<run-id> sets it
	@run=$${RUN:-$$(date -u +%Y%m%dT%H%M%SZ)}; failed=""; \
	for scenario in $(TRACK_B_SCENARIOS); do \
	  $(TRACK_B_PY) run --scenario $$scenario $(TRACK_B_INVENTORIES) --run-id $$run || failed="$$failed $$scenario"; \
	done; \
	echo "make track-b: run $$run, failed:$${failed:- none}"; \
	test -z "$$failed"

track-b-breaks: track-b-guest track-b-image ## Run every Track B scenario without its break link. Fails unless every run ends failed
	@run=$${RUN:-$$(date -u +%Y%m%dT%H%M%SZ)}; passed=""; \
	for scenario in $(TRACK_B_SCENARIOS); do \
	  rc=0; $(TRACK_B_PY) run --scenario $$scenario $(TRACK_B_INVENTORIES) --run-id $$run --break || rc=$$?; \
	  test "$$rc" -eq 1 || passed="$$passed $$scenario(exit $$rc)"; \
	done; \
	echo "make track-b-breaks: run $$run, runs that did not end failed:$${passed:- none}"; \
	test -z "$$passed"
