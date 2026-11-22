.PHONY: eval
eval:
	PYTHONPATH=src python3 -m pytest tests/eval_rbac_isolation.py -v
