PY ?= python

all: test backtest validate report

test:
	$(PY) -m pytest tests/ -q

backtest:
	$(PY) run_backtest.py

validate:
	$(PY) run_validation.py

report:
	$(PY) report.py
