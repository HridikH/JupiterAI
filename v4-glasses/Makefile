# Glasses — laptop-side convenience targets. Run from v4-glasses/.
PY ?= ./.venv/bin/python
PIP ?= ./.venv/bin/pip
PYTHON311 ?= python3.11

.PHONY: setup test run server ui doctor validate validate-strict \
        calibrate freeze trial wipe fuzz sim clean

## one-command laptop setup: venv (py3.11) + pinned deps (no npm; UI is plain HTML)
setup:
	$(PYTHON311) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r server/requirements.txt
	@echo "setup done. Next: make test, then make run"

## full test suite (no hardware)
test:
	$(PY) -m pytest -q

## launch server + UI window together (single command)
run:
	$(PY) ui/desktop.py

## server only (for the simulator / headless)
server:
	$(PY) -m server.app

doctor:
	$(PY) -m tools.doctor

## self-signed cert so a phone can use its camera over HTTPS (/mobile on :8443)
cert:
	$(PY) -m tools.make_cert

validate:
	$(PY) -m catalog.validate
validate-strict:
	$(PY) -m catalog.validate --strict

## calibrate on the held-out set, then freeze before trials
calibrate:
	$(PY) -m eval.calibrate --dataset eval/datasets/calibration.yaml
freeze:
	$(PY) -m eval.freeze

## blinded trial (refuses unfrozen thresholds). DATASET / SEED overridable.
DATASET ?= eval/datasets/trials.yaml
SEED ?= 7
trial:
	$(PY) -m eval.blinded_trial --dataset $(DATASET) --seed $(SEED)

## privacy wipe (captures + logs)
wipe:
	$(PY) -m server.privacy wipe

## V1 payload fuzzer against a running server (make server first)
fuzz:
	$(PY) -m tools.fuzzer --token $${GLASSES_TOKEN:-CHANGE_ME_dev_token}

## replay one JPEG through the real protocol (IMAGE=... SCENARIO=normal|duplicate|…)
IMAGE ?= eval/datasets/samples/blank.jpg
SCENARIO ?= normal
sim:
	$(PY) -m tools.fake_device --token $${GLASSES_TOKEN:-CHANGE_ME_dev_token} \
		--image $(IMAGE) --scenario $(SCENARIO)

clean:
	rm -rf .pytest_cache **/__pycache__
