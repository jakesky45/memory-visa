# memory-visa

> Time-boxed, human-approved, audited access across isolated AI-agent memory boundaries - built on Cedar, Cognito, DynamoDB Streams and AWS Bedrock AgentCore.

Static isolation keeps two organizations' - or two departments' - AI agents apart. It can't let them legitimately cross over on purpose, for a stated reason, for a limited time. This project is that second part: a grant store sitting on top of static isolation, not instead of it.

## What it does

1. Robotinc's departments (Engineering, Legal, Finance) each get their own AgentCore Memory namespace and toolset by default - Cedar policy and Cognito JWT claims, nothing custom.
2. When someone needs a real exception - Maya (Engineering) needs an export-control fact from Legal's files before a shipment - she calls `request_access` with a reason and a duration.
3. Legal's real domain owner (Liam) approves or denies it from his own AI client - no bespoke UI, just an MCP tool call he can already make.
4. Within the window, the Memory namespace opens for the requester, to read - never to write. The domain's tools come with it only if the request asked for them and the owner approved that. Past the window, access reverts on its own - no manual cleanup step.
5. Every transition is audited: who requested, who approved, when it expired. A DynamoDB Streams trigger is wired up to notify both the owner and the requester on each write, once a Slack webhook is configured - it isn't yet.
6. A sixth tool, `whoami`, reports a caller's own effective access live: static namespaces, any active grant, and the domain toolset that grant carries.

The same workflow covers a narrower starting point too: Jordan, a contractor, external, scoped to one project, no department claim at all (`tests/eval_rbac_isolation.py::test_eval5`).

## Architecture

![memory-visa architecture](docs/diagrams/architecture.svg)

Cedar policies and Cognito JWT claims handle static org/department isolation - that's standard AgentCore. The addition here is `GrantStore`, an external entitlements store backing that same Cedar ABAC, rather than AgentCore's [Dogwood](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal.html) temporal policies - Dogwood's session history is scoped to one caller, so it can't model a two-person approval (requester and approver).

## Status

Offline, `make eval` runs six properties end to end against fakes: cross-org isolation, within-org silo enforcement, legitimate cross-team flow, credential scrubbing, and the full grant lifecycle for both an employee and a contractor whose default access starts narrower.

`make eval` also runs the tests around those properties: the grant rules, the visa credentials, the Guardrail handling, the DynamoDB store and the tools Lambda. The last two need `boto3` installed and are skipped without it; none of them calls AWS.

Deployed in the sandbox account, `eu-west-2`:

| Resource | Status | Notes |
|---|---|---|
| VPC + flow logs | live | |
| Bedrock Guardrail | deployed, call path confirmed live | every `memory_write` passes it first: a secret is masked, a private key refuses the whole write, and content that cannot be checked is not stored (`src/memory/guardrail.py`). No PII rules - confirmed live, Liam's and Maya's names came back unmasked. The one live write so far was plain text; masking and blocking a real secret are tested offline only, not yet proved against AWS |
| Cognito pool + domain | live | custom `department`/`org` claims, both OAuth flows configured |
| AgentCore PolicyEngine | `ACTIVE` | one `permit` baseline, one `forbid` kept at `LOG_ONLY` (see below) |
| AgentCore Gateway | `READY` | `CUSTOM_JWT` auth; `IdentityInterceptor` resolves real Cognito identity before every tool call |
| EngineeringTools target | `READY`, gate inert | `check_shipping_classification`'s department gate can't fire - Cognito access tokens carry no custom attributes, so `hasTag("department")` is never true. Left at `LOG_ONLY` rather than claim a gate that doesn't hold |
| AccessRequestTools target | `READY` | eight MCP tools (`request_access`, `approve_access`, `whoami`, `memory_search`, `memory_write`, etc.), one Lambda, state in DynamoDB |
| GrantNotifier | `READY`, not wired up | Lambda + DynamoDB Streams trigger fire on every grant write, but `/memory-visa/slack-webhook-url` was never set - nothing is delivered yet |
| AgentCore Memory | `ACTIVE` | pool-tenancy, one semantic strategy, 31-day record expiry |
| AgentCore Runtime | `READY` | `network_mode="PUBLIC"` (no NAT gateway in this stack) |
| The visa (`memory/visa_credentials.py`) | proved against real AWS | `demo/visa_credentials_demo.py`: a namespace-scoped, time-boxed credential reads its own namespace, and AWS itself (not this code) refuses a different namespace and refuses the same one once the window closes - both `AccessDeniedException` |
| The grant lifecycle, end to end | proved against real AWS | request, approve, read under the grant, a write refused under that same grant, and a read refused again once the window closes - all through the real Gateway, not the offline fakes |

**Known gaps:**
- The DynamoDB grant store is an append-only table queried by `grant_id`, not a single `audit_log` attribute like the in-memory version.
- Reads (`memory_search`) aren't written to the audit table yet - only the request/approval/expiry lifecycle is. A read's own log line goes to stdout, with no verified-identity check on that log path.
- When you capture a `tools/call` response in a shell variable, print it with `printf '%s'`, not zsh's `echo`. The tool's result is JSON inside a JSON string, and zsh's `echo` removes one level of backslashes, which makes a valid response look malformed. See `REVIEW.md`'s S12.

## Quickstart

Needs: Python 3, Node.js (for CDK), the AWS CLI (configured with credentials), `jq`, `curl`.

<details>
<summary><strong>1. Offline proof</strong> - no AWS account needed</summary>

```bash
git clone https://github.com/jakesky45/memory-visa.git && cd memory-visa
cd cdk && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt pytest
cd .. && make eval
```

</details>

<details>
<summary><strong>2. Deploy the live stack</strong> - real, billed AWS infrastructure</summary>

```bash
cd cdk && source .venv/bin/activate
cdk deploy MemoryVisaAgentCore --require-approval never
```

</details>

<details>
<summary><strong>3. Give yourself a real Cognito login and call it as a person</strong></summary>

```bash
USER_POOL_ID=$(aws ssm get-parameter --name /memory-visa/user-pool-id --query Parameter.Value --output text)
USER_CLIENT_ID=$(aws ssm get-parameter --name /memory-visa/user-client-id --query Parameter.Value --output text)
GATEWAY_URL=$(aws ssm get-parameter --name /memory-visa/gateway-url --query Parameter.Value --output text)

aws cognito-idp admin-set-user-password --user-pool-id "$USER_POOL_ID" \
  --username maya --password '<temporary-password>' --permanent

TOKEN=$(aws cognito-idp initiate-auth --client-id "$USER_CLIENT_ID" --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=maya,PASSWORD='<temporary-password>' \
  --query AuthenticationResult.AccessToken --output text)

curl -s -X POST "$GATEWAY_URL/mcp" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | sed -n 's/^data: //p' | jq -r '.result.tools[].name'
```

Same for `liam` and `priya` - `CfnUserPoolUser` can't set a password declaratively, so that one step is manual for each.

</details>

<details>
<summary><strong>4. Tear down</strong> - stop the billing</summary>

```bash
cd cdk && source .venv/bin/activate
cdk destroy --all
```

`cdk deploy MemoryVisaAgentCore` in step 2 also deploys its two dependency stacks (`MemoryVisaNetwork`, `MemoryVisaGuardrails`); `--all` tears down all three.

</details>

## Repo layout

| Path | What's there |
|---|---|
| `src/memory/grants.py` | the entitlements store, in-process |
| `src/memory/grants_dynamodb.py` | same interface, DynamoDB-backed - what the MCP tools actually use |
| `src/memory/guarded_memory.py` | the RBAC gate |
| `src/memory/agentcore_backend.py` | the real AgentCore Memory client |
| `src/memory/visa_credentials.py` | the visa as a credential: reads use AWS credentials limited to one namespace until the grant ends |
| `src/memory/guardrail.py` | what a write may store after the Guardrail has seen it |
| `demo/visa_credentials_demo.py` | the live proof that AWS refuses a read outside the namespace or after the window |
| `src/agents/domain_config.py` | per-role toolset, grant-aware |
| `src/agents/domain_owners.py` | per-org approval authority |
| `src/agents/access_requests.py` | request/approve/deny logic |
| `src/tools/identity_interceptor.py` | resolves a caller's real Cognito identity before any tool runs |
| `src/tools/access_request_tools.py` | the grant workflow, `memory_search`/`memory_write`, and `grant_activity` (what was read under a grant), and `publish_fact` (a domain owner publishes a fact to the org; not deployed yet) - ten MCP tools, one Lambda |
| `src/tools/grant_notifier.py` | DynamoDB Streams → Slack, both directions |
| `src/tools/check_shipping_classification_tool.py` | the one real Engineering Gateway tool, a stub |
| `src/agents/chat_agent.py` | the agent - Claude on Amazon Bedrock, with memory only through the Gateway tools and the user's own token |
| `src/agents/gateway_client.py` | a small MCP client for the Gateway (`tools/list`, `tools/call`) |
| `src/server.py` | the orchestrator - `/ping`, and `/invocations` for one chat turn with the agent |
| `demo/agent_chat.py` | talk to the agent from a terminal; each memory call is shown under the reply |
| `tests/` | the eval suite - runs the six properties offline, no AWS account needed |
| `cdk/` | network, guardrails, and agentcore CDK stacks |
| `Dockerfile` | the orchestrator's container image (arm64, `PYTHONUNBUFFERED=1`) |
| `Makefile` | `make eval` |

## What's next

Memory is only as fresh as the last explicit `put()`. Nothing here tails a real system of record, so a fact doesn't get corrected if the underlying classification changes later - the 31-day record expiry just lets old facts drop out eventually.
