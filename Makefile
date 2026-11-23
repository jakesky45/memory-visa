.PHONY: eval
eval:
	PYTHONPATH=src python3 -m pytest tests/eval_rbac_isolation.py tests/test_visa_credentials.py tests/test_grant_rules.py -v
