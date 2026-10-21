# Thin wrappers around `python -m modelgate <command>`. Where make is not
# installed (python:3.11-slim, Windows), run those commands directly.
# scan and carbon targets arrive with Checkpoint B (see STATUS.md).

PY ?= python
MG := $(PY) -m modelgate

.PHONY: help data train metrics gate evaluate reports test lint all clean \
        scenario-baseline scenario-good scenario-bad scenario-bad-auc

help:
	@echo "make data | train | evaluate | reports | test | lint | all | clean"
	@echo "make scenario-baseline | scenario-good | scenario-bad | scenario-bad-auc"

data:
	$(MG) data

train:
	$(MG) train

metrics:
	$(MG) metrics

gate:
	$(MG) gate

evaluate:
	$(MG) evaluate

# data -> train -> metrics -> gate, with a timing summary
reports:
	$(MG) reports

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
