# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.

PYTHON ?= python3.12
O?=.
F?=*

.PHONY: venv
venv:
	rm -rf .venv
	$(PYTHON) -m venv .venv
	sed -n '/^\[packages\]$$/,/^\[dev-packages\]$$/p' Pipfile | grep -v '^\[' | sed 's/ =.*$$//' | xargs .venv/bin/pip install

.PHONY: run
run:
	@mkdir -p "$(O)/out" "$(O)/save"
	.venv/bin/python -m kconfigs.main config.ini \
		--state "$(O)/state.json" \
		--download-dir "$(O)/save" \
		--output-dir "$(O)/out" \
		--filter "$(F)"
	[ "$(F)" != "*" ] || .venv/bin/python -m kconfigs.cleanup config.ini \
		--input-dir "$(O)/out" \
		--filter "$(F)"
	.venv/bin/python -m kconfigs.analyzer config.ini \
		--input-dir "$(O)/out" \
		--output-file "$(O)/out/summary.json" \
		--filter "$(F)"

.PHONY: dev
dev:
	@rm -rf .venv && mkdir -p .venv  # ensure that pipenv sees .venv
	pipenv install --dev
	.venv/bin/pre-commit install --install-hooks

.PHONY: upgrade-requirements
upgrade-requirements:
	pipenv upgrade
