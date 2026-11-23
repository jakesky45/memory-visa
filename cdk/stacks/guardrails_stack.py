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
                    # ANONYMIZE, not BLOCK: BLOCK on a sensitive-information filter
                    # rejects the whole message and substitutes the blocked_input_messaging
                    # string in its place, which isn't redaction - it's refusal. ANONYMIZE
                    # replaces just the matched span, keeping the rest of the text, which is
                    # what "redact an AWS access key... before anything reaches memory"
                    # above actually means.
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="aws-access-key",
                        pattern=r"AKIA[0-9A-Z]{16}",
                        action="ANONYMIZE",
                        description="AWS access key ID",
                    ),
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="private-key-block",
                        pattern=r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
                        action="ANONYMIZE",
                        description="PEM-format private key header",
                    ),
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name="postgres-uri",
                        pattern=r"postgres(?:ql)?://[^\s]+:[^\s]+@[^\s]+",
                        action="ANONYMIZE",
                        description="Postgres connection string with embedded credentials",
                    ),
                ],
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="EMAIL", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="PHONE", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="NAME", action="ANONYMIZE"),
                ],
            ),
        )
