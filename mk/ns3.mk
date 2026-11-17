# Track C: the ns-3-alibabacloud packet model in one pinned image. ADR 0002 builds and runs that image
# in the Docker of the guest of the ns3 harness profile, never on this host. Every target reaches the
# guest over its 127.0.0.1 SSH forward.
TRACK_C_PY := PYTHONPATH=scripts $(BIN)/python -m ns3
TRACK_C_SCENARIOS := $(basename $(notdir $(wildcard tracks/c-ns3/scenarios/*.yaml)))
TRACK_C_INVENTORIES = $(foreach i,$(shell env LAB_PROFILE=ns3 $(if $(LAB_DIR),LAB_DIR=$(LAB_DIR)) $(LAB_HARNESS) inventories),-i $(i))

.PHONY: track-c-lock track-c-guest track-c-image track-c-smoke track-c-matrix track-c track-c-breaks

track-c-lock: venv
	$(RFL) versions --scope m6

track-c-guest: track-c-lock ## Boot or reuse the Track C guest and prepare it with ansible/playbooks/track-c-guest.yml
	LAB_PROFILE=ns3 $(LAB_HARNESS) up
	$(BIN)/ansible-playbook $(TRACK_C_INVENTORIES) ansible/playbooks/track-c-guest.yml

# CLEAN=1 deletes the tag, the base image and all build cache first. ARCHIVE=<file> saves the image there.
track-c-image: track-c-lock ## Build rfl-ns3:m6 inside the Track C guest. Fails when its ID is not ns3.image. CLEAN=1 builds from nothing
	$(TRACK_C_PY) image $(TRACK_C_INVENTORIES) $(if $(CLEAN),--clean) $(if $(BUILD_LOG),--log $(BUILD_LOG)) \
		$(if $(ARCHIVE),--archive $(ARCHIVE))

TRACK_C_SMOKE := c-01-example c-02-single-flow
TRACK_C_MATRIX := $(filter-out $(TRACK_C_SMOKE),$(TRACK_C_SCENARIOS))

# The recipe of the scenarios $(1) under one run id. Each scenario still runs when an earlier one failed.
define track_c_run
@run=$${RUN:-$$(date -u +%Y%m%dT%H%M%SZ)}; failed=""; \
for scenario in $(1); do \
  $(TRACK_C_PY) run --scenario $$scenario $(TRACK_C_INVENTORIES) --run-id $$run || failed="$$failed $$scenario"; \
done; \
echo "make $@: run $$run, failed:$${failed:- none}"; \
test -z "$$failed"
endef

track-c-smoke: track-c-guest track-c-image ## c-01-example and c-02-single-flow in the Track C guest under one run id: the pinned SimAI example and the single-flow baseline
	$(call track_c_run,$(TRACK_C_SMOKE))

track-c-matrix: track-c-guest track-c-image ## The incast scenarios c-03 to c-06 under one run id: four congestion profiles on the same incast traffic, and the kmin sensitivity run
	$(call track_c_run,$(TRACK_C_MATRIX))

track-c: track-c-guest track-c-image ## track-c-smoke and track-c-matrix under one run id. RUN=<run-id> sets it
	$(call track_c_run,$(TRACK_C_SMOKE) $(TRACK_C_MATRIX))

track-c-breaks: track-c-guest track-c-image ## Run every deliberate break of every Track C scenario. Fails unless each run fails on its checks
	$(TRACK_C_PY) breaks $(TRACK_C_INVENTORIES)
