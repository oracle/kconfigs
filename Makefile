# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.

PYTHON ?= python3.11

.PHONY: venv
venv:
	rm -rf venv
	$(PYTHON) -m venv venv
	venv/bin/pip install -r requirements.txt

.PHONY: run
run:
	venv/bin/python -m kconfigs.main config.ini
	venv/bin/python -m kconfigs.analyzer config.ini

.PHONY: dev
dev:
	rm -rf venv
	$(PYTHON) -m venv venv
	venv/bin/pip install -r requirements.txt -r requirements-dev.txt
	pre-commit install --install-hooks

.PHONY: upgrade-requirements
upgrade-requirements:
	venv/bin/upgrade-requirements
