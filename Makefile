.PHONY: eval
# The six properties, and the tests for the rules around them. The tests of the
# DynamoDB store and the tools Lambda need boto3 (`pip install boto3`); without it
# they are skipped and say so.
eval:
	PYTHONPATH=src python3 -m pytest tests/eval_rbac_isolation.py tests/test_*.py -v -rs
