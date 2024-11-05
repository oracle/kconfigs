# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.

PYTHON ?= python3.13

.PHONY: venv
venv:
	@mkdir -p .venv  # ensure that pipenv sees .venv
	$(PYTHON) -m pipenv install

.PHONY: run
run:
	.venv/bin/python -m kconfigs.main config.ini
	.venv/bin/python -m kconfigs.cleanup config.ini
	.venv/bin/python -m kconfigs.analyzer config.ini

.PHONY: dev
dev:
	@mkdir -p .venv  # ensure that pipenv sees .venv
	$(PYTHON) -m pipenv install --dev
	.venv/bin/pre-commit install --install-hooks

.PHONY: upgrade-requirements
upgrade-requirements:
	$(PYTHON) -m pipenv upgrade
