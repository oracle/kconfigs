# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.

PYTHON ?= 3.12
UV ?= uv
UV_FLAGS ?= --no-python-downloads
UV_SYNC_FLAGS ?= --frozen $(UV_FLAGS)
O?=.
F?=*

.PHONY: venv
venv:
	$(UV) sync --python "$(PYTHON)" --no-dev $(UV_SYNC_FLAGS)

.PHONY: run
run:
	@mkdir -p "$(O)/out" "$(O)/save"
	$(UV) run --python "$(PYTHON)" --no-dev $(UV_SYNC_FLAGS) python -m kconfigs.main config.ini \
		--state "$(O)/state.json" \
		--download-dir "$(O)/save" \
		--output-dir "$(O)/out" \
		--filter "$(F)"
	[ "$(F)" != "*" ] || $(UV) run --python "$(PYTHON)" --no-dev $(UV_SYNC_FLAGS) python -m kconfigs.cleanup config.ini \
		--input-dir "$(O)/out" \
		--filter "$(F)"
	$(UV) run --python "$(PYTHON)" --no-dev $(UV_SYNC_FLAGS) python -m kconfigs.analyzer config.ini \
		--input-dir "$(O)/out" \
		--output-file "$(O)/out/summary.json" \
		--filter "$(F)"

.PHONY: dev
dev:
	$(UV) sync --python "$(PYTHON)" --all-groups $(UV_SYNC_FLAGS)
	$(UV) run --python "$(PYTHON)" $(UV_SYNC_FLAGS) pre-commit install --install-hooks

.PHONY: upgrade-requirements
upgrade-requirements:
	$(UV) lock --upgrade --python "$(PYTHON)" $(UV_FLAGS)
	$(UV) sync --python "$(PYTHON)" --all-groups $(UV_FLAGS)
