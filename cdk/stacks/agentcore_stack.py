"""AgentCore Runtime (hosts the orchestrator) plus a Policy Engine enforcing
the *static* part of domain gating - department/org claims are confirmed
available as principal attributes, so "Legal can't call
check_shipping_classification" maps cleanly onto a Cedar policy, evaluated at
the Gateway, outside agent code.

What's deliberately NOT here yet: the grant-aware dynamic check (is there a
currently-active, human-approved grant for this requester into this
namespace). AgentCore Policy's only confirmed "information provider" beyond
static request/principal attributes is Guardrails - there's no verified
mechanism yet for feeding our own GrantStore's live status into a Cedar
evaluation as trustworthy context. Until that's confirmed, the grant check
stays at the orchestrator layer (src/memory/guarded_memory.py), where it's
already implemented and tested. Don't paper over this with invented syntax -
flag it, and revisit once AgentCore Policy's custom-context story is clear.
"""
from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as lambda_event_sources
from aws_cdk import aws_ssm as ssm
from constructs import Construct

# Fixed so it matches exactly one pre-registered Cognito callback URL - the
# OAuth authorization_code flow (unlike client_credentials) needs a real
# redirect, and Cognito requires it registered verbatim, so the port has to
# be fixed rather than left to Claude Code's random-port default.
MCP_OAUTH_CALLBACK_PORT = 51789

# Cedar's schema is auto-generated from the gateway's REAL target/tool
# definitions - confirmed by two live validation errors in sequence when this
# target's tool was still named terraform_plan: first "unrecognized action
# ...terraform_plan, did you mean UnknownTool?", then "UnknownTool" itself
# rejected as not a real action ID (just a fuzzy-match suggestion string).
# Renamed since (terraform_plan -> check_shipping_classification, a tool name
# that actually fits this story) - same mechanism, now a real Gateway Target
# exists ("EngineeringTools", backed by the check_shipping_classification
# Lambda), so the schema recognizes the real action name the target defines.
#
# Two earlier real deploy failures shaped the resource clause: a bare
# `resource` is rejected as an implicit wildcard, and a resource constrained
# only to the `AgentCore::Gateway` *type* is rejected too once the action
# itself is tool-specific - AWS requires a specific Gateway ARN. The
# placeholder below is substituted with the real Gateway's ARN once it
# exists (see AgentCoreStack.__init__), since the ARN isn't known until the
# Gateway resource is actually created.
# JWT claims are stored as TAGS on the OAuthUser principal, not plain
# attributes - `principal.department` fails schema validation ("did you mean
# `id`?"). Confirmed against AWS's own policy-conditions doc: the real
# accessor is principal.hasTag(...)/getTag(...).
#
# `principal is AgentCore::OAuthUser`, not a bare `principal`: Cedar's own
# automated analysis (a real feature, not noise - "Overly Restrictive" is
# one of its documented checks) caught that IamEntity principals don't
# support tags at all, so hasTag("department") is always false for them -
# the unless clause could never except an IAM caller, so an unscoped
# `principal` would unconditionally forbid every IAM-authenticated request.
# Scoping to the one principal type the condition is actually meaningful for.
STATIC_DOMAIN_POLICY = """
forbid (
    principal is AgentCore::OAuthUser,
    action == AgentCore::Action::"EngineeringTools___check_shipping_classification",
    resource == AgentCore::Gateway::"__GATEWAY_ARN__"
)
unless {
    principal.hasTag("department") && principal.getTag("department") == "engineering"
};
"""

# Confirmed by a live 403 the moment we actually tried invoking anything
# through the gateway ("denied by default. No policy applies to the
# request."): Cedar is default-deny, and the engine's only other policy is a
# `forbid` with no corresponding `permit` anywhere. A forbid narrows what an
# allowed request can't do; it doesn't grant the baseline access for
# anything to be reachable in the first place. Fine-grained per-namespace/
# per-grant enforcement happens inside GuardedMemory/access_requests in the
# orchestrator's own code, not duplicated here.
#
# A bare `action` (any action, present and future) was tried first and
# rejected by Cedar's own analysis as "Overly Permissive" - same automated-
# reasoning feature that caught the overly-restrictive forbid earlier, this
# time catching the opposite mistake. Its error message also revealed the
# real auto-generated action name for an HTTP/agentcoreRuntime target:
# "Orchestrator___POST:/invocations" - not the MCP tool-schema naming
# convention, since this target is HTTP passthrough, not an MCP tool.
# Enumerating the two actions that actually exist today, not a wildcard.
BASE_PERMIT_POLICY = """
permit (
    principal is AgentCore::OAuthUser,
    action in [
        AgentCore::Action::"EngineeringTools___check_shipping_classification",
        AgentCore::Action::"Orchestrator___POST:/invocations",
        AgentCore::Action::"AccessRequestTools___request_access",
        AgentCore::Action::"AccessRequestTools___list_pending",
        AgentCore::Action::"AccessRequestTools___approve_access",
        AgentCore::Action::"AccessRequestTools___deny_access",
        AgentCore::Action::"AccessRequestTools___revoke_access",
        AgentCore::Action::"AccessRequestTools___whoami"
    ],
    resource == AgentCore::Gateway::"__GATEWAY_ARN__"
);
"""


class AgentCoreStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # A real, minimal identity provider - the Cedar policy's
        # principal.department condition is meaningless for an AWS_IAM
        # principal (no custom claim attributes), so CUSTOM_JWT needs an
        # actual OIDC source, not a stand-in.
        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="memory-visa",
            custom_attributes={
                "department": cognito.StringAttribute(mutable=True),
                "org": cognito.StringAttribute(mutable=True),
            },
            self_sign_up_enabled=False,
            # CDK's UserPool defaults to RETAIN on delete - missed this
            # earlier, and it's why roughly nine orphaned "memory-visa" pools
            # accumulated over this session's many full-stack delete/recreate
            # cycles. DESTROY matches every other resource in this demo stack.
            removal_policy=RemovalPolicy.DESTROY,
        )
        # client_credentials can't use identity scopes (profile/email/openid,
        # the client-less default) - it needs a custom resource-server scope.
        self.resource_server = self.user_pool.add_resource_server(
            "GatewayResourceServer",
            identifier="memory-visa-gateway",
            scopes=[cognito.ResourceServerScope(scope_name="invoke", scope_description="Invoke gateway tools")],
        )
        self.user_pool_client = self.user_pool.add_client(
            "GatewayClient",
            generate_secret=True,  # required by Cognito for the client_credentials flow
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[cognito.OAuthScope.resource_server(self.resource_server, cognito.ResourceServerScope(
                    scope_name="invoke", scope_description="Invoke gateway tools",
                ))],
            ),
        )
        # Without a domain there's no /oauth2/token endpoint at all - nobody,
        # not just a test script, could actually exchange client credentials
        # for a bearer token. Missed when the pool/client/resource-server
        # were first set up; a real gap, not a testing convenience.
        self.user_pool_domain = self.user_pool.add_domain(
            "GatewayDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=f"memory-visa-{self.account}"),
        )

        # A second app client, for real per-user identities. client_credentials
        # tokens represent the app itself, never a specific human - there's no
        # way to tell Maya's call from Liam's call with one shared token, so
        # the identity fix (see IdentityInterceptor below) needs a flow that
        # actually authenticates a person. Public client (no secret) since
        # USER_PASSWORD_AUTH doesn't need a confidential one.
        self.user_client = self.user_pool.add_client(
            "UserClient",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_password=True),
            # authorization_code is what gives Claude Code (or any OAuth-aware
            # MCP client) a real refresh token - a static bearer header has no
            # way to renew itself once the hour-long access token expires.
            # user_password stays too, for the quick CLI test path.
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[f"http://localhost:{MCP_OAUTH_CALLBACK_PORT}/callback"],
            ),
            read_attributes=cognito.ClientAttributes()
            .with_standard_attributes(email=True)
            .with_custom_attributes("department", "org"),
        )

        # Real people, not just the one shared app-client token - each of
        # Maya/Liam/Priya gets their own login, so a verified JWT can actually
        # say which of them made a call. CfnUserPoolUser can't set a password
        # declaratively; set one with `aws cognito-idp admin-set-user-password
        # --permanent` after deploy.
        for username, email, department in [
            ("maya", "maya@robotinc.example", "engineering"),
            ("liam", "liam@robotinc.example", "legal"),
            ("priya", "priya@robotinc.example", "finance"),
            # Jordan: external contractor, scoped narrower than an employee -
            # no department claim at all, only an org. Same grant workflow,
            # different trust level (mirrors tests/eval_rbac_isolation.py::test_eval5).
            ("jordan", "jordan@contractor.example", None),
        ]:
            attributes = [
                cognito.CfnUserPoolUser.AttributeTypeProperty(name="email", value=email),
                cognito.CfnUserPoolUser.AttributeTypeProperty(name="email_verified", value="true"),
                cognito.CfnUserPoolUser.AttributeTypeProperty(name="custom:org", value="robotinc"),
            ]
            if department is not None:
                attributes.append(cognito.CfnUserPoolUser.AttributeTypeProperty(name="custom:department", value=department))

            cognito.CfnUserPoolUser(
                self,
                f"User{username.capitalize()}",
                user_pool_id=self.user_pool.user_pool_id,
                username=username,
                user_attributes=attributes,
            )

        self.policy_engine = agentcore.CfnPolicyEngine(
            self,
            "PolicyEngine",
            name="MemoryVisaPolicyEngine",
            description="Static org/department gating for memory-visa's domain tools",
        )

        self.gateway_role = iam.Role(
            self,
            "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                # AWS's own service-authorization reference doesn't list
                # AuthorizeAction/PartiallyAuthorizeActions for this service
                # yet - this is a very new feature and the docs haven't
                # caught up. Trusting the real AccessDenied error text over
                # the stale reference page. BatchAuthorizeActions included
                # on the same naming pattern; a wrong/nonexistent action
                # name in an IAM policy is inert, not a deploy error.
                actions=[
                    "bedrock-agentcore:GetPolicyEngine",
                    "bedrock-agentcore:AuthorizeAction",
                    "bedrock-agentcore:PartiallyAuthorizeActions",
                    "bedrock-agentcore:BatchAuthorizeActions",
                ],
                resources=[self.policy_engine.attr_policy_engine_arn],
            )
        )
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                # Each of the policy-engine-scoped actions above is checked
                # a second time against the gateway's own ARN too (confirmed
                # for AuthorizeAction, then again for PartiallyAuthorizeActions -
                # same pattern both times), whose physical-id suffix isn't
                # known until after creation - wildcard within this
                # account/region, not a blanket "*".
                actions=[
                    "bedrock-agentcore:AuthorizeAction",
                    "bedrock-agentcore:PartiallyAuthorizeActions",
                    "bedrock-agentcore:BatchAuthorizeActions",
                ],
                resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/*"],
            )
        )

        # Decodes the caller's already-verified JWT and injects their real
        # email into the tool call's arguments, overwriting whatever the
        # caller tried to assert - this is what actually closes "anyone can
        # call approve_access as anyone", not just a documented limitation.
        # Runs gateway-wide (every target gets it), self-contained (stdlib
        # only), so packaged the same minimal way as CheckShippingClassificationTool.
        self.identity_interceptor_fn = lambda_.Function(
            self,
            "IdentityInterceptor",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="identity_interceptor.handler",
            code=lambda_.Code.from_asset("../src/tools"),
            timeout=Duration.seconds(5),
        )
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[self.identity_interceptor_fn.function_arn],
            )
        )
        # The Gateway's authorizer only admits access tokens (they carry the
        # required `scope` claim; ID tokens don't), and access tokens carry no
        # email/custom attributes - GetUser resolves the same already-verified
        # token to its owner's real attributes directly from Cognito.
        self.identity_interceptor_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["cognito-idp:GetUser"],
                resources=[self.user_pool.user_pool_arn],
            )
        )

        # Referencing role_arn alone only orders Gateway after the Role
        # resource, not after the separate AWS::IAM::Policy sub-resource
        # add_to_policy() creates - confirmed by an actual GetPolicyEngine
        # AccessDenied on deploy. Explicit construct-level dependency so
        # CloudFormation waits for the attached policy too, not just the role.
        self.gateway = agentcore.CfnGateway(
            self,
            "Gateway",
            name="memory-visa-gateway",
            role_arn=self.gateway_role.role_arn,
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=(
                        f"https://cognito-idp.{self.region}.amazonaws.com/"
                        f"{self.user_pool.user_pool_id}/.well-known/openid-configuration"
                    ),
                    allowed_clients=[
                        self.user_pool_client.user_pool_client_id,
                        self.user_client.user_pool_client_id,
                    ],
                ),
            ),
            policy_engine_configuration=agentcore.CfnGateway.GatewayPolicyEngineConfigurationProperty(
                arn=self.policy_engine.attr_policy_engine_arn,
                mode="ENFORCE",
            ),
            interceptor_configurations=[
                agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                    interception_points=["REQUEST"],
                    interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                        lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                            arn=self.identity_interceptor_fn.function_arn,
                        ),
                    ),
                    input_configuration=agentcore.CfnGateway.InterceptorInputConfigurationProperty(
                        # Needs the Authorization header to decode the caller's
                        # claims - the one legitimate use of this normally-risky
                        # flag (AWS's own docs warn headers can carry secrets).
                        pass_request_headers=True,
                    ),
                ),
            ],
        )
        self.gateway.node.add_dependency(self.gateway_role)

        # The real Gateway Target: a stub Lambda backing one tool,
        # "check_shipping_classification" - named for what Engineering would
        # actually use it for in this story, not a disconnected placeholder.
        # This gives Cedar's auto-generated schema a real action to validate
        # against - "EngineeringTools___check_shipping_classification" -
        # resolving the earlier CREATE_FAILED ("unrecognized action ...did
        # you mean UnknownTool?", and UnknownTool itself turned out not to be
        # real either). Target before Policy, the order we got wrong the
        # first time.
        self.shipping_classification_fn = lambda_.Function(
            self,
            "CheckShippingClassificationTool",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="check_shipping_classification_tool.handler",
            code=lambda_.Code.from_asset("../src/tools"),
            timeout=Duration.seconds(10),
        )

        # L1 CfnGatewayTarget, not the L2 add_lambda_target() convenience
        # method: that method only works on a Gateway constructed natively
        # as the L2 `Gateway` class. Ours is a `CfnGateway` (L1), and
        # wrapping it via `Gateway.from_gateway_attributes` produces a proxy
        # that implements IGateway's read accessors but not its builder
        # methods (confirmed by an AttributeError at synth) - replacing the
        # already-deployed Gateway with the L2 construct would mean another
        # expensive delete/recreate cycle, so this stays on the Gateway
        # we've got.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[self.shipping_classification_fn.function_arn],
            )
        )
        self.engineering_target = agentcore.CfnGatewayTarget(
            self,
            "EngineeringToolsTarget",
            gateway_identifier=self.gateway.attr_gateway_identifier,
            name="EngineeringTools",
            description="Engineering's tools - currently just check_shipping_classification",
            # Confirmed required by a live error ("CredentialProviderConfigurations
            # is required for Lambda targets") - this is AgentCore Identity's
            # outbound-authentication story: how the Gateway authenticates to
            # invoke the Lambda. GATEWAY_IAM_ROLE (same terminology as the
            # Memory connector's outbound credential mode) since this is an
            # internal AWS-to-AWS call, not an external API needing OAuth/API
            # keys.
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                )
            ],
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=self.shipping_classification_fn.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=[
                                agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                                    name="check_shipping_classification",
                                    description="Look up a shipment's export-control classification - read-only stub.",
                                    input_schema={
                                        "type": "object",
                                        "properties": {"shipment": {"type": "string"}},
                                        "required": ["shipment"],
                                    },
                                )
                            ],
                        ),
                    ),
                ),
            ),
        )
        self.engineering_target.node.add_dependency(self.gateway)
        self.engineering_target.node.add_dependency(self.gateway_role)

        # Tier two: request_access/list_pending/approve_access/deny_access as
        # real, individually-discoverable MCP tools, so an approver can act
        # through whatever MCP client they're already in (Claude, Cursor)
        # instead of a bespoke UI or a Slack app neither of us has admin
        # rights to stand up. Needs its own table, not the orchestrator's
        # in-process GrantStore - each tools/call can land on a separate,
        # stateless Lambda invocation, so there's no shared Python process
        # for in-memory grant state to survive in between calls.
        self.grants_table = dynamodb.Table(
            self,
            "GrantsTable",
            partition_key=dynamodb.Attribute(name="grant_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            # Powers the pull->push fix below - a new pending grant triggers
            # GrantNotifier instead of sitting there until someone thinks to ask.
            stream=dynamodb.StreamViewType.NEW_IMAGE,
        )

        # Append-only, unlike GrantsTable above (current state only, one item
        # per grant, overwritten on every transition) - this is what actually
        # closes the "no full audit trail in the live deployment" gap, giving
        # the live store the same property the in-memory GrantStore already
        # proves in the eval suite.
        self.grants_audit_table = dynamodb.Table(
            self,
            "GrantsAuditTable",
            partition_key=dynamodb.Attribute(name="grant_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="at", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Closes the pull-only gap: fires on every new GrantsTable item and
        # posts to Slack via an Incoming Webhook, instead of leaving it to
        # Liam to remember to ask. DOMAIN_OWNERS addresses are fictional demo
        # data - the webhook posts to one real demo channel while the
        # Lambda's own logic still resolves and states the real intended
        # owner in the message.
        self.grant_notifier_fn = lambda_.Function(
            self,
            "GrantNotifier",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="tools.grant_notifier.handler",
            code=lambda_.Code.from_asset("../src"),
            timeout=Duration.seconds(10),
            # Just the SSM parameter's name, not the webhook URL - nothing
            # that could post into the wrong channel goes into the template
            # or git. The actual URL is set once via `aws ssm put-parameter
            # --type SecureString`, outside CDK entirely.
            environment={"SLACK_WEBHOOK_PARAM": "/memory-visa/slack-webhook-url"},
        )
        self.grant_notifier_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/memory-visa/slack-webhook-url"],
            )
        )
        self.grant_notifier_fn.add_event_source(
            lambda_event_sources.DynamoEventSource(
                self.grants_table,
                starting_position=lambda_.StartingPosition.LATEST,
            )
        )

        # Packaged from the whole src/ tree, not just src/tools/ like
        # CheckShippingClassificationTool above - this handler imports across
        # agents/memory/tools (agents.access_requests, memory.grants_dynamodb),
        # so the zip root has to be src/ itself for those imports to resolve,
        # matching how the orchestrator's own Dockerfile copies all of src/.
        self.access_request_tools_fn = lambda_.Function(
            self,
            "AccessRequestTools",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="tools.access_request_tools.handler",
            code=lambda_.Code.from_asset("../src"),
            timeout=Duration.seconds(10),
            environment={
                "GRANTS_TABLE_NAME": self.grants_table.table_name,
                "GRANTS_AUDIT_TABLE_NAME": self.grants_audit_table.table_name,
            },
        )
        self.grants_table.grant_read_write_data(self.access_request_tools_fn)
        self.grants_audit_table.grant_read_write_data(self.access_request_tools_fn)

        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[self.access_request_tools_fn.function_arn],
            )
        )

        # No email/identity field on any of these - the caller's identity comes
        # from their verified JWT (see IdentityInterceptor above), injected as
        # `_verified_email` before this Lambda ever sees the call. A schema
        # field here would just be an argument nobody should be able to lie in.
        _ACCESS_REQUEST_TOOL_SCHEMAS = [
            agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="request_access",
                description="Request time-boxed access into another org/department's memory namespace and toolset.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "target_org": {"type": "string"},
                        "target_department": {"type": "string"},
                        "reason": {"type": "string"},
                        "duration_hours": {"type": "number"},
                    },
                    "required": ["target_org", "target_department", "reason", "duration_hours"],
                },
            ),
            agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="list_pending",
                description="List pending cross-domain access requests awaiting the caller's decision, as a domain owner.",
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="approve_access",
                description="Approve a pending cross-domain access request. Only the target domain's owner may approve it.",
                input_schema={
                    "type": "object",
                    "properties": {"grant_id": {"type": "string"}},
                    "required": ["grant_id"],
                },
            ),
            agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="deny_access",
                description="Deny a pending cross-domain access request. Only the target domain's owner may deny it.",
                input_schema={
                    "type": "object",
                    "properties": {"grant_id": {"type": "string"}},
                    "required": ["grant_id"],
                },
            ),
            agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="revoke_access",
                description=(
                    "Pull back an active or pending grant before it would otherwise expire - the "
                    "break-glass path. Only the target domain's owner may revoke it. Does not touch "
                    "the requester's own baseline access - revoking someone's account entirely is a "
                    "Cognito admin action, not a grant."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"grant_id": {"type": "string"}},
                    "required": ["grant_id"],
                },
            ),
            agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="whoami",
                description=(
                    "Report the caller's own verified identity and effective access right now: "
                    "Memory namespaces (static and currently-granted), the domain toolset any active "
                    "grant carries, and whether the one real Gateway tool (check_shipping_classification) would "
                    "currently respond."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
        ]
        self.access_request_tools_target = agentcore.CfnGatewayTarget(
            self,
            "AccessRequestToolsTarget",
            gateway_identifier=self.gateway.attr_gateway_identifier,
            name="AccessRequestTools",
            description="The grant workflow as real MCP tools: request/list/approve/deny cross-domain access",
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                )
            ],
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=self.access_request_tools_fn.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=_ACCESS_REQUEST_TOOL_SCHEMAS,
                        ),
                    ),
                ),
            ),
        )
        self.access_request_tools_target.node.add_dependency(self.gateway)
        self.access_request_tools_target.node.add_dependency(self.gateway_role)

        self.static_domain_policy = agentcore.CfnPolicy(
            self,
            "StaticDomainPolicy",
            name="StaticDomainGating",
            policy_engine_id=self.policy_engine.attr_policy_engine_id,
            definition=agentcore.CfnPolicy.PolicyDefinitionProperty(
                cedar=agentcore.CfnPolicy.CedarPolicyProperty(
                    statement=STATIC_DOMAIN_POLICY.replace("__GATEWAY_ARN__", self.gateway.attr_gateway_arn)
                )
            ),
            # Reverted to LOG_ONLY after a real ACTIVE test: promoting this denied
            # EVERYONE, including Maya (engineering) - not just non-engineering callers.
            # Root cause, confirmed: the Gateway's CUSTOM_JWT authorizer only ever sees
            # Cognito ACCESS tokens, and access tokens carry no custom attributes at all
            # (the same reason IdentityInterceptor has to call GetUser separately -
            # see identity_interceptor.py). principal.hasTag("department") is therefore
            # always false, so this forbid's "unless" branch can never fire - LOG_ONLY
            # was masking a policy that was never actually enforceable as written, not a
            # validated one waiting to be promoted. See docs/index.html.
            enforcement_mode="LOG_ONLY",
            validation_mode="FAIL_ON_ANY_FINDINGS",
            description="Denies Engineering-only tools to anyone without the engineering department claim",
        )
        self.static_domain_policy.add_resource_dependency(self.gateway)
        self.static_domain_policy.node.add_dependency(self.engineering_target)

        self.base_permit_policy = agentcore.CfnPolicy(
            self,
            "BasePermitPolicy",
            name="BaselineGatewayAccess",
            policy_engine_id=self.policy_engine.attr_policy_engine_id,
            definition=agentcore.CfnPolicy.PolicyDefinitionProperty(
                cedar=agentcore.CfnPolicy.CedarPolicyProperty(
                    statement=BASE_PERMIT_POLICY.replace("__GATEWAY_ARN__", self.gateway.attr_gateway_arn)
                )
            ),
            enforcement_mode="ACTIVE",  # must be ACTIVE, not LOG_ONLY, or nothing is actually unlocked
            validation_mode="FAIL_ON_ANY_FINDINGS",
            description="Baseline: any authenticated gateway caller can reach any target, by default denied otherwise",
        )
        self.base_permit_policy.add_resource_dependency(self.gateway)
        # Cedar's schema is generated from the gateway's real targets - the
        # AccessRequestTools actions below need that target to exist first,
        # same reasoning as the original terraform_plan CREATE_FAILED/
        # UnknownTool saga.
        self.base_permit_policy.node.add_dependency(self.access_request_tools_target)

        # The actual memory backend GuardedMemory calls into. Pool-tenancy,
        # not one Memory resource per org - that's the documented AWS
        # pattern (the aws-samples healthcare repo uses one shared
        # Knowledge Base per tier with a tenant filter per request, not a
        # KB per clinic), and it's the cost-effective choice too: isolation
        # is namespace strings at the GuardedMemory layer, not infrastructure
        # count. One semantic strategy only - not summary/episodic/
        # user-preference stacked together, which would cost more in
        # extraction compute than this demo needs - and a short expiry
        # rather than indefinite retention.
        self.memory_execution_role = iam.Role(
            self,
            "MemoryExecutionRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        # Confirmed live: this role had a trust policy and nothing else - no
        # permission to invoke the model AgentCore Memory needs for semantic
        # extraction. Every retrieve against long-term memory came back empty
        # because extraction could never actually run, not because of lag.
        self.memory_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[f"arn:aws:bedrock:{self.region}::foundation-model/*"],
            )
        )
        self.memory = agentcore.CfnMemory(
            self,
            "Memory",
            name="memory_visa",
            description="Pool-tenancy memory store - isolation is namespace strings, not resource count",
            event_expiry_duration=7,  # days - short, cost-conscious retention for a demo
            memory_execution_role_arn=self.memory_execution_role.role_arn,
            memory_strategies=[
                agentcore.CfnMemory.MemoryStrategyProperty(
                    semantic_memory_strategy=agentcore.CfnMemory.SemanticMemoryStrategyProperty(
                        name="domain_facts",
                        description="Facts, preferences, and summaries extracted per namespace",
                        record_expiry_duration_days=31,  # 31 is the minimum AgentCore allows
                        # Confirmed live, the hard way: left at AWS's default
                        # ("/strategies/{memoryStrategyId}/actors/{actorId}/"),
                        # extraction worked but every retrieve_memory_records
                        # call against our own plain namespace strings
                        # (actorId alone, e.g. "org_robotinc_dept_legal") came
                        # back empty - records were filed under a path this
                        # code never queried. {actorId} alone matches our
                        # existing namespace convention exactly, no code
                        # change needed on the retrieve/put side.
                        namespaces=["{actorId}"],
                    )
                )
            ],
        )

        self.runtime_role = iam.Role(
            self,
            "RuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],  # GetAuthorizationToken doesn't support resource-level scoping
            )
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                resources=[f"arn:aws:ecr:{self.region}:{self.account}:repository/memory-visa-orchestrator"],
            )
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:PutResourcePolicy",
                ],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*"],
            )
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                # Writes only. The orchestrator's own role can no longer READ memory:
                # every read goes through the visa role below, with credentials AWS
                # limits to one namespace until the grant ends.
                actions=["bedrock-agentcore:CreateEvent"],
                resources=[self.memory.attr_memory_arn],
            )
        )

        # The visa role (src/memory/visa_credentials.py). It may read any namespace,
        # but nobody uses it as it is: the orchestrator assumes it once per read with
        # a session policy that narrows it to one namespace and one end time. A
        # session policy can only narrow a role, so this policy is the ceiling.
        #
        # sts:TagSession, so the session carries the grant ID and the requester and
        # CloudTrail shows which grant a read was made under.
        self.visa_role = iam.Role(
            self,
            "VisaReaderRole",
            role_name="memory-visa-reader",
            description="Read AgentCore Memory. Always assumed with a session policy for one namespace and one end time.",
            assumed_by=iam.ArnPrincipal(self.runtime_role.role_arn),
            max_session_duration=Duration.hours(1),
        )
        self.visa_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                actions=["sts:TagSession"],
                principals=[iam.ArnPrincipal(self.runtime_role.role_arn)],
            )
        )
        # For demo/visa_credentials_demo.py only: one more principal that may assume the
        # visa role, so the proof can be run from a laptop. Off unless the context
        # value is set:  cdk deploy -c visaDemoPrincipalArn=<your role ARN>
        demo_principal_arn = self.node.try_get_context("visaDemoPrincipalArn")
        if demo_principal_arn:
            self.visa_role.assume_role_policy.add_statements(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole", "sts:TagSession"],
                    principals=[iam.ArnPrincipal(demo_principal_arn)],
                )
            )
        self.visa_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:GetMemoryRecord",
                ],
                resources=[self.memory.attr_memory_arn],
            )
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole", "sts:TagSession"],
                resources=[self.visa_role.role_arn],
            )
        )
        self.grants_table.grant_read_write_data(self.runtime_role)
        self.grants_audit_table.grant_read_write_data(self.runtime_role)

        # No VPC/security group for the Runtime - see the network_mode="PUBLIC"
        # comment below for why. A security-group ingress rule was tried first
        # and didn't fix the 502, which is itself useful evidence: it pointed
        # at outbound routing (no NAT gateway in this stack, by design) rather
        # than inbound reachability as the real problem.

        self.runtime = agentcore.CfnRuntime(
            self,
            "OrchestratorRuntime",
            agent_runtime_name="memory_visa_orchestrator",
            role_arn=self.runtime_role.role_arn,
            environment_variables={
                "MEMORY_ID": self.memory.attr_memory_id,
                "MEMORY_ARN": self.memory.attr_memory_arn,
                "VISA_ROLE_ARN": self.visa_role.role_arn,
                "GRANTS_TABLE_NAME": self.grants_table.table_name,
                "GRANTS_AUDIT_TABLE_NAME": self.grants_audit_table.table_name,
            },
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    # Referenced by digest, not the mutable :latest tag -
                    # AgentCore Runtime doesn't re-pull per invocation, so a
                    # same-string container_uri means CloudFormation sees no
                    # change and the new image never actually ships. Update
                    # this digest on every push.
                    container_uri=(
                        f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/memory-visa-orchestrator"
                        "@sha256:ed9255da6d312f5b88804e827e99795278e4333f1dff458de3c09f27a6d81d4e"
                    ),
                ),
            ),
            # Switched from "VPC" to "PUBLIC" (AgentCore's default,
            # service-managed networking, no VPC config needed at all).
            # VPC mode put the Runtime's ENI in our network stack's public
            # subnets, which have no NAT gateway by design (a deliberate
            # cost decision elsewhere in this stack) - if that ENI didn't
            # get a public IP, it would have had no outbound route at all,
            # not even to pull its own image. That's a better explanation
            # for a persistent 502 with zero logs (the container may never
            # have properly come up) than the security group ingress rule
            # alone, which didn't fix it. We don't actually need network-level
            # isolation here anyway - the real security boundary is IAM,
            # Cedar, and Cognito, not the compute's network placement.
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC",
            ),
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                idle_runtime_session_timeout=180,  # 3 minutes, matching the FinOps target
            ),
        )
        self.runtime.node.add_dependency(self.runtime_role)

        # MCP exposure, tier one: a dedicated AgentCore Runtime HTTP target
        # (not an MCP target - confirmed via docs these are mutually
        # exclusive with MCP-protocol gateways, and ours has no protocol
        # type set, so this is allowed). This is transparent passthrough -
        # the gateway forwards to InvokeAgentRuntime verbatim, applying its
        # own Cognito/CUSTOM_JWT auth and Cedar policy in front, without the
        # orchestrator needing to speak real MCP JSON-RPC. Honest limitation,
        # not hidden: a real MCP client doing tools/list won't see memory_put/
        # retrieve/request_access as individually discoverable tools this
        # way - that would need each split into its own persisted-state
        # Lambda-backed MCP target, since the in-process GrantStore can't be
        # shared across separate stateless Lambda invocations. This tier
        # gives one governed URL through the gateway; deeper tool-level MCP
        # discovery is further work, not done here.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                # Confirmed by a live AccessDenied: the actual call targets
                # a runtime-endpoint sub-resource ARN
                # (.../runtime/ID/runtime-endpoint/DEFAULT), which the bare
                # runtime ARN alone doesn't cover.
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[
                    self.runtime.attr_agent_runtime_arn,
                    f"{self.runtime.attr_agent_runtime_arn}/runtime-endpoint/*",
                ],
            )
        )
        self.orchestrator_target = agentcore.CfnGatewayTarget(
            self,
            "OrchestratorTarget",
            gateway_identifier=self.gateway.attr_gateway_identifier,
            name="Orchestrator",
            description="The real orchestrator - memory put/retrieve, access requests - fronted by the gateway",
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                )
            ],
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                http=agentcore.CfnGatewayTarget.HttpTargetConfigurationProperty(
                    agentcore_runtime=agentcore.CfnGatewayTarget.RuntimeTargetConfigurationProperty(
                        arn=self.runtime.attr_agent_runtime_arn,
                    ),
                ),
            ),
        )
        self.orchestrator_target.node.add_dependency(self.gateway)
        self.orchestrator_target.node.add_dependency(self.gateway_role)
        self.orchestrator_target.node.add_dependency(self.runtime)

        # Demo config, looked up instead of pasted into commands or docs -
        # none of this is secret (the user-client OAuth app has no client
        # secret, and the gateway URL/pool/client IDs are routing info, not
        # credentials), so plain SSM String parameters, not Secrets Manager.
        # The one genuinely sensitive value - the shared demo password - is
        # set directly on the Cognito users (admin-set-user-password) and
        # deliberately never written into CloudFormation or this template at all.
        for name, value in [
            ("gateway-url", self.gateway.attr_gateway_url),
            ("gateway-mcp-url", f"{self.gateway.attr_gateway_url}/mcp"),
            ("user-pool-id", self.user_pool.user_pool_id),
            ("user-client-id", self.user_client.user_pool_client_id),
            ("oauth-callback-port", str(MCP_OAUTH_CALLBACK_PORT)),
            ("memory-id", self.memory.attr_memory_id),
            ("memory-arn", self.memory.attr_memory_arn),
            ("visa-role-arn", self.visa_role.role_arn),
        ]:
            ssm.StringParameter(
                self,
                f"Param{name.title().replace('-', '')}",
                parameter_name=f"/memory-visa/{name}",
                string_value=value,
            )
