# Thin wrappers around `python -m modelgate <command>`. Where make is not
# installed (python:3.11-slim, Windows), run those commands directly.

PY ?= python
MG := $(PY) -m modelgate

.PHONY: help data train scan metrics carbon sci gate evaluate reports register \
        test lint all clean \
        scenario-baseline scenario-good scenario-bad scenario-bad-auc

help:
	@echo "make data | train | scan | evaluate | carbon | reports | register"
	@echo "make test | lint | all | clean"
	@echo "make scenario-baseline | scenario-good | scenario-bad | scenario-bad-auc"

data:
	$(MG) data

train:
	$(MG) train

# picklescan on the model file, pip-audit on requirements.txt -> reports/scan.json
scan:
	$(MG) scan

metrics:
	$(MG) metrics

gate:
	$(MG) gate

# metrics -> gate
evaluate:
	$(MG) evaluate

# data, train, scan and metrics under CodeCarbon -> reports/energy.json, reports/sci.json
carbon:
	$(MG) carbon

sci:
	$(MG) sci

# carbon, then the gate verdict, with a timing summary (what CI's train_evaluate runs)
reports:
	$(MG) reports

# Model Registry or generic package; skipped outside GitLab CI
register:
	$(MG) register

test:
	$(PY) -m pytest --junitxml=reports/junit.xml

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

all: reports test

# Expected verdicts assume the v0.1.0 baseline champion.
scenario-baseline:
	$(MG) scenario baseline

scenario-good:
	$(MG) scenario good

scenario-bad:
	$(MG) scenario bad

scenario-bad-auc:
	$(MG) scenario bad-auc

clean:
	rm -rf data reports models/challenger
