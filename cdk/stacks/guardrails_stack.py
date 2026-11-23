"""A Bedrock Guardrail doing exactly what Eval 3 proves: redact an AWS access
key, a private key, and a Postgres URI before anything reaches memory, plus
the built-in PII entity filters. Regex and PII filters are the free tier of
Guardrails content policies - no per-request charge beyond the standard
Guardrail invocation cost.
"""
from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_bedrock as bedrock
from constructs import Construct


class GuardrailsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.guardrail = bedrock.CfnGuardrail(
            self,
            "MemoryVisaGuardrail",
            name="memory-visa-pre-write",
            blocked_input_messaging="This input contains content that can't be stored.",
            blocked_outputs_messaging="This response contains content that can't be returned.",
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                regexes_config=[
                    # Two actions, on purpose (src/memory/guardrail.py handles both):
                    #
                    # ANONYMIZE masks the matched text and keeps the rest. Right when the
                    # pattern covers the whole secret, so nothing sensitive is left behind.
                    #
                    # BLOCK refuses the whole write. Right when the pattern can only find
                    # the START of a secret: masking a private key's header line would
                    # store the key itself.
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="aws-access-key",
                        pattern=r"AKIA[0-9A-Z]{16}",
                        action="ANONYMIZE",
                        description="AWS access key ID",
                    ),
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="aws-secret-access-key",
                        pattern=r"aws_secret_access_key\s*[=:]\s*\S+",
                        action="ANONYMIZE",
                        description="AWS secret access key assignment, the value that usually follows a key ID",
                    ),
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="private-key-block",
                        pattern=r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
                        action="BLOCK",
                        description="PEM-format private key header: the key follows it, so the write is refused",
                    ),
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="postgres-uri",
                        pattern=r"postgres(?:ql)?://[^\s]+:[^\s]+@[^\s]+",
                        action="ANONYMIZE",
                        description="Postgres connection string with embedded credentials",
                    ),
                ],
                # No PII rules. This memory is an organisation's shared knowledge, and a fact
                # needs its names: who approved, which supplier, who to ask. Masking names,
                # emails and phone numbers would return "{NAME} is banned under 7A994".
                # Secrets are masked or refused everywhere; personal data rules, if a
                # namespace needs them, belong to that namespace.
            ),
        )
