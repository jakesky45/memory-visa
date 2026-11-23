#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.guardrails_stack import GuardrailsStack
from stacks.agentcore_stack import AgentCoreStack

app = cdk.App()

# Resolved from whatever AWS credentials are active when `cdk` runs (the
# CDK CLI sets this itself, CDK's standard "environment-agnostic" pattern) -
# never a hardcoded account ID in git.
env = cdk.Environment(account=os.environ["CDK_DEFAULT_ACCOUNT"], region="eu-west-2")

network = NetworkStack(app, "MemoryVisaNetwork", env=env)
guardrails = GuardrailsStack(app, "MemoryVisaGuardrails", env=env)
agentcore = AgentCoreStack(
    app,
    "MemoryVisaAgentCore",
    vpc=network.vpc,
    guardrail_id=guardrails.guardrail.attr_guardrail_id,
    env=env,
)
agentcore.add_stack_dependency(network)
agentcore.add_stack_dependency(guardrails)

app.synth()
