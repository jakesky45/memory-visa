"""A throwaway Gateway for one experiment: can a temporal (Dogwood) policy enforce
"no read without a checked grant", and does a policy see another caller's events?

Separate from the main Gateway on purpose. Once a policy engine holds a temporal
policy, every request must carry a session ID header, and that must not happen to
the main Gateway by accident. It has its own CDK app, cdk/spike_app.py:

    npx cdk --app "python3 spike_app.py" deploy MemoryVisaDogwoodSpike

It uses the main stack's Cognito pool and user client (read from Parameter Store),
so MemoryVisaAgentCore must be deployed first. The temporal policies themselves are
created by spike/dogwood_spike.py, because their errors are part of the result.
"""
from __future__ import annotations

from aws_cdk import Duration, Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_ssm as ssm
from constructs import Construct

TARGET = "SpikeTools"
NAMESPACE_INPUT = {
    "type": "object",
    "properties": {"namespace": {"type": "string"}},
    "required": ["namespace"],
}

BASE_PERMIT = """
permit (
    principal is AgentCore::OAuthUser,
    action == AgentCore::Action::"SpikeTools___check_grant",
    resource == AgentCore::Gateway::"__GATEWAY_ARN__"
);
"""


class DogwoodSpikeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        user_pool_id = ssm.StringParameter.value_for_string_parameter(self, "/memory-visa/user-pool-id")
        user_client_id = ssm.StringParameter.value_for_string_parameter(self, "/memory-visa/user-client-id")

        engine = agentcore.CfnPolicyEngine(
            self,
            "Engine",
            name="MemoryVisaDogwoodSpike",
            description="Throwaway: temporal policy experiment",
        )

        tools_fn = lambda_.Function(
            self,
            "SpikeTools",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="spike_tools.handler",
            code=lambda_.Code.from_asset("../spike", exclude=["dogwood_spike.py", "__pycache__"]),
            timeout=Duration.seconds(10),
        )

        role = iam.Role(self, "GatewayRole", assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:GetPolicyEngine",
                    "bedrock-agentcore:AuthorizeAction",
                    "bedrock-agentcore:PartiallyAuthorizeActions",
                    "bedrock-agentcore:BatchAuthorizeActions",
                ],
                resources=[
                    engine.attr_policy_engine_arn,
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/*",
                ],
            )
        )
        role.add_to_policy(iam.PolicyStatement(actions=["lambda:InvokeFunction"], resources=[tools_fn.function_arn]))
        # AWS requires this of the Gateway role for temporal policies ("IAM permissions for
        # temporal policies"). The gateway ID is not known until the Gateway exists, so the
        # second resource is a wildcard under the default directory.
        directory = f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default"
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PolicySessionWorkloadIdentity",
                actions=["bedrock-agentcore:GetWorkloadAccessToken"],
                resources=[directory, f"{directory}/workload-identity/*"],
            )
        )

        gateway = agentcore.CfnGateway(
            self,
            "Gateway",
            name="memory-visa-dogwood-spike",
            role_arn=role.role_arn,
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=(
                        f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
                    ),
                    allowed_clients=[user_client_id],
                ),
            ),
            policy_engine_configuration=agentcore.CfnGateway.GatewayPolicyEngineConfigurationProperty(
                arn=engine.attr_policy_engine_arn,
                mode="ENFORCE",
            ),
        )
        gateway.node.add_dependency(role)

        def tool(name: str, description: str, output_schema=None):
            return agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name=name, description=description, input_schema=NAMESPACE_INPUT, output_schema=output_schema
            )

        target = agentcore.CfnGatewayTarget(
            self,
            "Target",
            gateway_identifier=gateway.attr_gateway_identifier,
            name=TARGET,
            description="Throwaway tools for the temporal policy experiment",
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                )
            ],
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=tools_fn.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=[
                                tool(
                                    "check_grant",
                                    "Say whether a grant is active for a namespace.",
                                    # Declared so a policy can read output.active.
                                    output_schema={
                                        "type": "object",
                                        "properties": {"active": {"type": "boolean"}, "namespace": {"type": "string"}},
                                        "required": ["active"],
                                    },
                                ),
                                tool("visa_read_a", "A read. Needs an earlier check_grant for the same namespace."),
                                tool("visa_read_b", "A read. Needs an earlier check_grant that returned active."),
                            ],
                        ),
                    ),
                ),
            ),
        )
        target.node.add_dependency(gateway)
        target.node.add_dependency(role)

        # A plain permit for the prerequisite. Only a permitted call that completes is
        # recorded as a `response`, and the temporal policies match on that.
        base_permit = agentcore.CfnPolicy(
            self,
            "PermitCheckGrant",
            name="PermitCheckGrant",
            policy_engine_id=engine.attr_policy_engine_id,
            definition=agentcore.CfnPolicy.PolicyDefinitionProperty(
                cedar=agentcore.CfnPolicy.CedarPolicyProperty(
                    statement=BASE_PERMIT.replace("__GATEWAY_ARN__", gateway.attr_gateway_arn)
                )
            ),
            enforcement_mode="ACTIVE",
            validation_mode="FAIL_ON_ANY_FINDINGS",
            description="check_grant is allowed for any signed-in caller",
        )
        base_permit.node.add_dependency(target)

        for name, value in [
            ("gateway-url", gateway.attr_gateway_url),
            ("gateway-arn", gateway.attr_gateway_arn),
            ("policy-engine-id", engine.attr_policy_engine_id),
        ]:
            ssm.StringParameter(
                self,
                f"Param{name.title().replace('-', '')}",
                parameter_name=f"/memory-visa/spike/{name}",
                string_value=value,
            )
