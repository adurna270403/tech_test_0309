PY ?= python

all: test backtest validate protocol report

test:
	$(PY) -m pytest tests/ -q

backtest:
	$(PY) run_backtest.py

validate:
	$(PY) run_validation.py

protocol:
	$(PY) run_protocol.py

report:
	$(PY) report.py
