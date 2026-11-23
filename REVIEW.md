# memory-visa: review and recommendations

Reviewed 23 November 2026, at commit `49f59ba`. Scope: the code under `src/`, `cdk/` and `tests/`,
the README, and the blog at `docs/index.html`. The focus is the memory layer and the aim of
presenting "sharing context at scale" at an AWS Summit.

How I checked: I read every source file, ran `make eval` (6 pass), and ran six probes against
the in-memory store to confirm the grant findings below. I did not deploy the stack, so
everything about the live system comes from the code, the README and the blog.

## Progress and action points

Updated 23 November 2026, after stage 1 of the plan. This section is the working list.
The findings below it are the review as first written, at commit `49f59ba`.

### The plan

Four stages. Nothing in a later stage is built until the one before it is proven.

| Stage | Goal | Who | State |
|---|---|---|---|
| 1. Safe and true | Nothing wrong goes live, and the blog claims nothing that has not run | Claude | **Done.** See "Stage 1" below |
| 2. Deploy and prove | The visa, proven against AWS | Claude, with Jake's AWS session | **Done.** See "Stage 2" below |
| 2b. Finish the live checks | Three small checks stage 2 left out | Claude, with Jake's AWS session | **Done.** See "Third review" below |
| 3. A memory demo | An agent in the loop, provenance, search across namespaces, a second way to share | Claude builds, Jake reviews and deploys | **Run, and blocked.** The agent, the Gateway path and a real code bug are proven. The model call itself is blocked by an org policy - see "Fourth review: the stage 3 run" |
| 4. Scale and stage | Queries not scans, measured numbers, claims in the token, the blog and the talk | Both | After stage 3 |

### Third review: commits `0091dbf` and `e34b8f4`

Stage 2 ran, and the result that matters is in: **AWS itself refuses a read outside the
namespace and after the window.** The idea holds. Stage 3 can start.

| # | Severity | Finding | Fix |
|---|---|---|---|
| T1 | High | **The "Gateway bug" (S12) is the shell.** A valid response printed with zsh's `echo` gives the exact broken string that was published as proof. The claim was in the blog, the README, this file and a commit message | **Confirmed.** The one-minute check in S12 passed: `curl -o` straight into Python parses clean, top level and nested. S12 closed below |
| T2 | Medium | **The sandbox account ID was in this public file.** `cdk/app.py` says the account ID is never in git | Removed from this file, and from the git history on 23 November 2026 (U9) |
| T3 | Medium | **The Guardrail has not seen a secret live.** The one live write was plain text. Masking and blocking are proven offline only | **Checked.** A fake access key, written live, was gone by the time it came back through search - extraction reworded it to "(key value omitted for security)", no raw or masked key text anywhere in the result. A fake private key, written live, came back `422 "this content may not be stored: the Guardrail blocked it"`, and nothing was stored. Both as designed |
| T4 | Low | **Which namespace form IAM matched is not recorded.** A8 line 1 was allowed, so one of the two forms in the policy matched. Knowing which lets the other be removed | **Checked, and the answer is odd: both matched independently.** A session policy naming only `/org_robotinc_dept_legal/` allowed the read; a separate one naming only the bare `org_robotinc_dept_legal` also allowed it. AgentCore's `bedrock-agentcore:namespace` condition key appears to match this namespace in either representation - I don't have a documented explanation for why, only the two results. Listing both forms in `namespace_condition_values` is still the safe choice; there's no evidence either is the one doing the work |
| T5 | Low | **S5 is closed** - for the calls checked at the time. *(Reopened in the N8 run, P5: `BatchCreateMemoryRecords` with `metadata` needed a newer SDK than the runtime's own. Fixed - see N8)* | None |

The three live checks above are done (T3, T4, S12) - see each row for the result.

### What now

The agent is built, deployed, and proven as far as it can be without a model it's allowed
to call. So the plan has two tracks: Jake unblocks the model, and Claude builds what does
not need one.

| Order | Who | What | Where |
|---|---|---|---|
| 1 | Claude | ~~On the other machine: `git fetch origin && git reset --hard origin/main`~~ **Done** | "Fourth review" |
| 2 | Claude | ~~Deploy, then run the HTTP/1.1 check (U8)~~ **Done, 23 November 2026: closed, and U6 corrected to the real cause** | "Fourth review" |
| 3 | Jake | Unblock a model. Ask the Organization's administrator for a policy exception and for access to Claude Opus 5.5 or Sonnet 5.5 in `eu-west-2`. If that will take more than a few days, use a personal AWS account for the demo | U2/U3 |
| 4 | Jake | Meanwhile, prove the agent with an API key: `demo/agent_chat.py --provider anthropic`, runbook steps 3 and 4 | "Stage 3: the agent" |
| 5 | Jake | Say when the Summit is and how long the slot is (D8) | "Your decisions" |
| 6 | Claude | ~~Provenance on results, and reads in the audit table (N2)~~ **Done, deployed and proven live** | "N2: provenance and the audit trail" |
| 7 | Claude | ~~Published facts, the second way to share (N8)~~ **Done, deployed and proven live** | "N8: published facts" |
| 8 | Claude | ~~Search across every namespace the caller may read (N7)~~ **Done, deployed and proven live.** Found a real pre-existing bug along the way (Q4): a caller's own user namespace can't be read, ever - M8's redesign, not a small fix | "N7: one search across what you may read" |
| 8 | Claude | ~~Deploy the fix for Q4 (a person's own namespace) and run the check~~ **Done, deployed and proven live** | "N7: one search across what you may read" |
| 8a | Jake | Stop GitHub serving the old commits that hold the account and policy IDs: ask GitHub Support to remove them, or delete the repository and push it again. Then delete `/tmp/memory-visa-before-scrub.bundle` on the first machine | U9 |
| 8b | Jake | Run the proof that IAM refuses a direct record write outside a published namespace (P6): `cdk deploy MemoryVisaAgentCore -c visaDemoPrincipalArn=<your role ARN>`, then `python3 demo/publish_boundary_demo.py`. Expected: line 1 ALLOWED, lines 2 and 3 `REFUSED by AWS: AccessDeniedException`. The deploy adds one demo-only role (`memory-visa-publisher-demo`) with the tools Lambda's publish statement and nothing else. Not proven: that `delete_memory_record` clean-up works | `demo/publish_boundary_demo.py` |
| 9 | Both | When a model works on Bedrock: run all four beats live, time them, then the blog and the talk (stage 4) | "The demo, in four beats" |

### Fourth review: the stage 3 run

Run 23 November 2026, against the deployed stack, using Jake's AWS session. Two real
findings, one fixed in code, one not ours to fix.

| # | Severity | Finding | Fix |
|---|---|---|---|
| U6 | High | **A caller on HTTP/1.1 got no verified identity.** The same token, headers and body worked over HTTP/2 and failed over HTTP/1.1 with `403 "no verified identity on this call"`. The cause was in this project, not the Gateway: `identity_interceptor.py` looked up the header as `authorization`, lower case only. HTTP/2 clients send header names in lower case; HTTP/1.1 clients send `Authorization`, so the lookup found nothing. Any HTTP/1.1 MCP client would have failed, not only this one. **The first record of this blamed the Gateway's HTTP/2 requirement - wrong. See U8**, which is how that got caught | **Fixed in the interceptor** (case-insensitive lookup, two tests). Deployed and confirmed 23 November 2026: the exact U8 check, `curl --http1.1`, now returns Maya's real identity. `gateway_client.py` went back to the standard library and the image was rebuilt without `httpx` - the HTTP/2 workaround was never the real fix, and keeping it once the interceptor was fixed would have been needless complexity for no remaining reason |
| U2/U3 | High | **No Claude model can be called from this account. There are two different refusals, and they may have two causes.** (1) The inference-profile IDs (`eu.` and `global.`, Opus, Sonnet and Haiku 5.5, and the 4.5 generation) are refused with `"not authorized to perform: bedrock:InvokeModel ... with an explicit deny in a service control policy"`. The policy belongs to the managing AWS Organization, so `AdministratorAccess` in the sandbox account cannot see or change it. (2) The plain ID `anthropic.claude-opus-5-5` is refused with `"not available for this account... contact AWS Sales"`. That is model access, not the policy. The agent uses the second path (`bedrock-mantle:CreateInference`, not `bedrock:InvokeModel`), so lifting the policy alone may not be enough | **Not ours to fix in code.** Ask the Organization's administrator for two things: an exception to the policy for this account, and access to Claude Opus 5.5 (or Sonnet 5.5) in `eu-west-2`. Until then, `demo/agent_chat.py --provider anthropic` runs the same agent against the Claude API with an API key, so U1 can be settled. U3 is still not reached |
| U4 | - | **Answered.** The Gateway path to the Runtime is `{gateway_url}/{TargetName}/invocations` - `/Orchestrator/invocations` here, not `/invocations` alone (that form reaches the Gateway but finds no target named "invocations"). Confirmed: with the fix above deployed, a call to this URL reaches the container, resolves Maya's identity correctly, and fails only at the model call - the same SCP-driven `NotFoundError` AnthropicBedrockMantle gives (its error reporting differs from the classic client's, but the cause is the same SCP) | Use this URL in the runbook, not `/invocations` |
| U5 | - | **Answered.** `cdk diff` and `cdk deploy` both ran clean, twice (once for N6, once for the U6 fix). The diff matched what N6 predicted: two new Runtime role statements, `MODEL_ID`, the new image digest, the new target description | None |
| U7 | Low | **A permanently-failing model call does not fail fast.** Through the deployed Runtime, a request that can only ever 404 took over 150 seconds and the container logged dozens of retried model calls before the platform itself gave up with `"Runtime initialization time exceeded"`. Not fully diagnosed - may be the Runtime's own cold-start/retry behaviour rather than anything in this code, and there was no way to test a call that eventually succeeds | Revisit once U2 is unblocked and a real reply can be timed |

Everything else N6 set out to prove - the agent's own logic, the tool set, the identity
rule, the Gateway wiring - is now proven as far as it can be without a model that is
allowed to answer. The blocker is organisational, not technical.

Two more points from reading this run:

| # | Severity | Finding | Fix |
|---|---|---|---|
| U8 | Medium | **The HTTP/2 conclusion was recorded before the cause was found** (the same pattern as S12). The header lookup explains the result without any Gateway behaviour | **Confirmed, 23 November 2026.** Deployed the interceptor fix, ran the exact check below: Maya's real identity came back over plain `curl --http1.1`. Closed - see U6 |
| U9 | Medium | **The managing Organization's account ID and policy ID were in this public file** (the same rule as T2) | Removed from this file and from the git history, with the sandbox account ID (T2). The history from stage 2 onward was rewritten and force-pushed on 23 November 2026, so the commit IDs changed. GitHub can still serve the old commits by their full ID until it removes them: ask GitHub Support to do that, or delete the repository and push it again |

The U8 check, run after `cdk deploy MemoryVisaAgentCore`:

```bash
curl -s --http1.1 -X POST "$GATEWAY_URL/mcp" -H "Authorization: Bearer $MAYA" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"AccessRequestTools___whoami","arguments":{}}}'
```

**Run 23 November 2026: `statusCode: 200`, Maya's real identity, over plain HTTP/1.1.** U8
and U6 are both closed.

On U7: the Anthropic SDK retries a failed call twice at most, and it does not retry a 404.
Dozens of model calls for one request means something outside this code sent the request
again. `server.py` answers a failed model call with HTTP 502, and a 5xx invites a retry.
Look at this again when a model call can succeed.

### N7: one search across what you may read

Written 23 November 2026. **Deployed and proven live the same day - and it surfaced a real,
pre-existing bug this project never happened to exercise before (Q4).**

| Change | What it does |
|---|---|
| `memory_search` with no `namespace` | Searches every namespace the caller may read now: their own, their department's, the org's published facts, and any namespace an open grant covers. Nothing else |
| Each namespace is one ordinary read | The same check, the same visa, the same audit entry. A search across five namespaces is five scoped reads, not one wide one. That is the point to make on stage: wider search did not need wider credentials |
| The answer | `records` (best match first, at most 10, each with its namespace and how it was allowed), `searched`, and `failed`. One namespace failing does not hide the others |
| The tools Lambda's timeout | 10 seconds to 30, because each namespace is one visa and one read |
| The agent | Leaves out the namespace by default |
| Metadata on a result | AgentCore's own `x-amz-...` fields are left out, so `metadata` is only the source of the fact |

**Q4, found running this live (not anticipated here): a caller's own user namespace cannot
be read at all.** `RetrieveMemoryRecords` rejects it: `ValidationException: Value at
'namespace' failed to satisfy constraint: Member must satisfy regular expression pattern:
[a-zA-Z0-9/*][a-zA-Z0-9-_/*]*(?::[a-zA-Z0-9-_/*]+)*[a-zA-Z0-9-_/*]*` - no `@` anywhere in
that pattern, and `org_robotinc_user_maya@robotinc.example` has one, because the namespace
convention embeds the caller's email address (M8). This was never caught before because
nothing in this project had called `memory_search` against a bare user namespace until N7's
fan-out tried every namespace a caller may read, including their own. Two different things
happen depending on how it's reached:
- Through `retrieve_all` (no `namespace` given): caught per namespace, reported in `failed`,
  the rest of the search still returns. This is why N7's own behaviour above is correct.
- Asked for directly (`memory_search` with that exact namespace): unhandled - `ValidationException`
  is not one of the exception types `access_request_tools.py`'s handler catches, so it reaches
  the caller as a bare `"An internal error occurred. Please retry later."`, no explanation.

**Run 23 November 2026, against the deployed stack, before the Q4 fix:**

```
Maya searches with no namespace: Legal's published fact (access "published"), searched =
  [org_robotinc_dept_engineering, org_robotinc_published], failed = [her own namespace,
  ValidationException] - 4.46s
Maya searches org_robotinc_dept_engineering directly: 200, works as before (0.97s)
Maya requests Legal, Liam approves (6 minutes)
Maya searches with no namespace again: five records across org_robotinc_published and
  org_robotinc_dept_legal, ranked by score, Legal's records correctly say access "grant"
  with the grant_id, approver and expiry - 2.41s
```

Q1, Q2 and Q3 are all answered by that run: the Gateway accepted the schema change with no
error; three and four namespaces took 2-4 seconds, not alarming but not instant either - no
need yet to issue visas side by side; and scores across the published namespace and a
grant-scoped department namespace ranked sensibly against each other (the published fact
came first both times).

P6 (IAM refuses a direct write outside a published namespace) is still unverified. The
proof script is written: `demo/publish_boundary_demo.py`. See "What now", row 8b.

**Fixed in code after the run, without the M8 redesign. Deployed and proven live the same
day.** The first note here said the fix needed M8. It does not: only the person's own
namespace has an email in it, and only that part needs to change.

| Change | What it does |
|---|---|
| The person's own namespace | `org_robotinc_user_maya_at_robotinc_dot_example`. `@` becomes `_at_`, `.` becomes `_dot_`, `_` is doubled, and any other character is written as `_x<hex>_`. Two different emails never give the same namespace: a test checks the pairs that a simple replace would merge |
| The grant's key | Unchanged. A grant is still filed under `org_<org>_user_<email>`. That key is never sent to AgentCore, so old grants still work |
| A refusal by AWS | The tools Lambda now reports it: `{"error": "refused by AWS: AccessDeniedException"}` with 403, `ValidationException` with 400, anything else with 502. Not "An internal error occurred". This also makes the visa's refusal readable on stage |

**Run 23 November 2026, against the deployed stack, after the fix:**

```
Maya's whoami: static_namespaces has org_robotinc_user_maya_at_robotinc_dot_example
Maya writes to it, waits for extraction, searches it directly by name: 200, the fact
  comes back word for word, access {"via": "membership"} - CreateEvent and
  RetrieveMemoryRecords both accept the escaped name
Maya searches with no namespace: the same fact is in the results, "searched" includes
  her own namespace, and "failed" is empty - Q4 no longer appears there at all
Liam tries Maya's namespace directly: 403 "no static claim, and no active grant"
```

Confirmed, not assumed: `CreateEvent` accepts the escaped name as `actorId`, and a search
of it returns exactly what was written. Anything written to the old
`...user_maya@robotinc.example` name before this fix is not moved - nothing could ever
read it, so nothing of value is lost.

Not yet deliberately exercised: the new AWS-refusal reporting (403/400/502 instead of "An
internal error occurred") - no call in this run happened to pass the Python check and then
be refused by AWS itself, so that path is confirmed offline only so far.

### N8: published facts

Written 23 November 2026. **Deployed and proven live the same day, with one real bug found
and fixed along the way** - see below.

The visa is the exception: one person, one namespace, a short time, a human approval. It
does not scale to a fact that many people need. A published fact is the rule: the owner of
a domain says it once, and everyone in the org can read it with no grant.

| Change | What it does |
|---|---|
| `publish_fact`, a new tool | The owner of a department publishes one fact for it. Nobody else can, not even a member of the department. The agent does not have this tool |
| The namespace `org_<org>_published` | Every member of the org may read it. Nobody writes to it with `memory_write`. Another org cannot read it |
| Stored as written | The fact is one long-term record, written with `BatchCreateMemoryRecords`. No model rewords it. This is the answer to M5 for facts someone must trust |
| The source is on the record | Metadata: `published_by`, `department`, `published_at`. A search result returns it, with `access: {"via": "published"}` |
| The Guardrail and the audit trail | A published fact passes the Guardrail first. Publishing and each read are in the audit trail under `namespace#org_<org>_published` |
| IAM | The tools Lambda may call `BatchCreateMemoryRecords` only for a namespace like `org_*_published` |
| `whoami` | Names the published namespace, so a person or the agent knows where to search |
| The agent | Searches the published namespace after the person's own, prefers a published fact, and says who published it |

**P5 (found running this live, not anticipated): the Lambda's own built-in boto3 did not
know the API.** `BatchCreateMemoryRecords` with `metadata` set failed with
`ParamValidationError: Unknown parameter in records[0]: "metadata", must be one of:
requestIdentifier, namespaces, content, timestamp, memoryStrategyId` - the Python 3.12
runtime's bundled SDK is older than this feature. This is **S5, back open**: T5 closed it
too early, on calls (`CreateEvent`, `RetrieveMemoryRecords`) that happened not to need a
newer SDK. **Fixed:** `src/requirements.txt` pins `boto3==1.44.9` (checked directly against
that version's service model: it has `metadata`), and `AccessRequestTools`' CDK asset now
bundles it with Docker (`BundlingOptions`, `pip install -r requirements.txt -t
/asset-output`) instead of relying on the runtime's own copy. Confirmed in the built
package: `boto3-1.44.9` and `botocore-1.44.9`, not the runtime's older version. Needs Docker
to build; `cdk synth`/`cdk deploy` do that automatically.

**Run 23 November 2026, against the deployed stack, after the fix above:**

```
Maya publishes for legal: 403 "does not own domain 'legal' in org 'robotinc'"
Liam publishes for legal: 200, recordId mem-1f992202-..., published_by liam
Maya's whoami: memory.published_namespace = "org_robotinc_published"
Maya searches org_robotinc_published, no grant: the fact, word for word, access
  {"via": "published"}, metadata has published_by, department, published_at, and three
  AgentCore-internal fields (x-amz-agentcore-memory-recordType etc.) - found with NO
  extraction wait, since this is a direct write, not an extracted event
Maya tries memory_write into org_robotinc_published: 403 "no static claim, and no active grant"
```

P1, P2, P3 and P4 are all answered by that one run: no `publishedStrategyId` was needed (the
record was found without one), the IAM condition matched cleanly, metadata was on the
result exactly as written, and it was findable immediately, not after a wait.

For the talk: Maya asks the agent about the X-7 and gets Legal's published answer with
Liam's name on it, with no request and no wait. Then she asks for something Legal has not
published, and that is where the visa comes in.

Not built: taking a published fact back or replacing it (M9), project namespaces (S6), and
the brokered answer.

### N2: provenance and the audit trail

Written 23 November 2026. **Deployed and proven live the same day** - see the real run below.

| Change | What it does |
|---|---|
| `memory_search` results | Each result now has `record_id`, `created_at`, `score`, `namespace`, and `access`. For a member, `access` is `{"via": "membership"}`. For a visitor it names the grant, who approved it, and when it ends |
| The audit table | Every read and write goes there, allowed or refused: who, the namespace, the question, the record IDs, the outcome. A read under a grant is filed under that grant. Anything else is filed under `namespace#<namespace>` |
| No audit, no answer | The read is recorded before the caller has the results. If the record cannot be written, nothing is returned |
| A refusal by AWS | When the visa's credentials are refused, that is recorded under the grant too |
| `grant_activity`, a new tool | The owner of the target domain, and the requester, can see one grant's whole trail: requested, approved, each read, each refused write. Nobody else can. The agent does not have this tool |

What it does not do: the author is not on the record. A long-term record is extracted by a
model from events, and AgentCore does not carry the writer onto it. The audit trail has
the writer and the event ID. Putting the author on the record needs N9.

**Run 23 November 2026, against the deployed stack. Every field was real - nothing from the
"not proven" list above turned out absent.** `cdk diff` matched exactly (the tools Lambda
code, one new tool, one new Cedar action); `cdk deploy` was clean.

```
Liam writes: memory_write -> 200, eventId 0000001795437247409#7102f70e
Liam searches, no grant: memory_search -> 200, four records, each with record_id,
  created_at, score, strategy_id, namespace, and access {"via": "membership"}
Maya requests, Liam approves (10 minutes: grant_id 3a440edd-...)
Maya searches, under the grant: the same four records, now access {"via": "grant",
  "grant_id": "3a440edd-...", "approved_by": "liam@robotinc.example", "expires_at": "..."}
Maya writes under the grant: 403 "a grant never allows a write"
grant_activity as Liam (owner): requested, approved, read (Maya's query and all four
  record IDs), write_refused - in order, in one call
grant_activity as Maya (requester): the same four events - both sides of a grant see it
grant_activity as Priya (neither): 403 "is not the owner of 'legal' and did not request"
```

`memoryRecordId`, `createdAt`, `score` and `memoryStrategyId` are all real fields on a live
`RetrieveMemoryRecords` answer - confirmed, not assumed.

For the talk this is a fifth beat: Liam asks his own client "what did Maya read under that
grant?" and gets the answer.

### Stage 3: the agent

Decided 23 November 2026: D9 is option A, and D10 is Claude Opus 5.5 on Amazon Bedrock.

**What was built (N6)**

| File | What it does |
|---|---|
| `src/agents/chat_agent.py` | The agent. It calls the model with the Anthropic SDK's Bedrock client (`AnthropicBedrockMantle`, model `anthropic.claude-opus-5-5`, set by `MODEL_ID`). It has four tools from the Gateway: `whoami`, `memory_search`, `memory_write`, `request_access`. It cannot approve, deny or revoke |
| `src/agents/gateway_client.py` | A small MCP client, standard library only. It sends the user's own token to the Gateway and returns the tool's status, so a 403 reaches the model as a refusal |
| `src/server.py` | `POST /invocations` with `{"prompt", "access_token", "session_id"}`. It does not read the token. It passes it to the Gateway. One conversation per session and verified email |
| `demo/agent_chat.py` | Talk to the agent from a terminal. Each memory call is printed under the reply. `/new` starts a new conversation |
| `tests/test_chat_agent.py` | 17 tests with a fake model and a fake Gateway |
| `cdk/stacks/agentcore_stack.py` | The Runtime role may call the model and read one parameter (the Gateway URL). `MODEL_ID` on the Runtime. No memory permission |
| `Dockerfile` | Installs `anthropic[bedrock]` |

How D9 is kept:

- The agent has no memory, grant or Guardrail permission. Memory is reached only through the Gateway tools, with the user's token.
- Who the user is comes from `whoami` through the Gateway. A name or a `claims` object in the request body is ignored, and a test proves it.
- A token the Gateway rejects gets a 403. Nothing in the container decides identity. M1 stays closed.
- S11 is closed in a different way: `Orchestrator___POST:/invocations` has a job again, so it stays in the Cedar permit.

Short-term memory is the message list of one conversation, held in the container for that session. Long-term memory is AgentCore Memory, through the tools.

**What is not proven yet**

| # | Not proven | How it is settled |
|---|---|---|
| U1 | **Still open.** The model's real behaviour - the tests use a fake model, and no live call has ever succeeded (U2) | Once U2 is unblocked |
| U2 | Your account can call *some* Claude model in `eu-west-2` | **Blocked by an org policy, not answered.** See "Fourth review" |
| U3 | The IAM statement for the Runtime role (`bedrock-mantle:CreateInference`, resource `*`) | **Not reached yet** - the org-level SCP denies the call before this policy is evaluated. See "Fourth review" |
| U4 | The path through the Gateway to the container | **Answered:** `{gateway_url}/{TargetName}/invocations`. See "Fourth review" |
| U5 | `cdk synth`/`cdk deploy` | **Answered: clean, twice.** See "Fourth review" |
| U6 | *(found during the run, not anticipated here)* identity over HTTP/1.1 | **Found, and the real cause confirmed.** See "Fourth review" |

**Runbook: stage 3**

Use the shell variables and the `token` function from "Runbook: stage 2 on another machine", step 6.

**1. Get the code and run the tests**

```bash
git pull
pip install pytest boto3 "anthropic[bedrock]"
make eval
```

Expected result: 71 passed (the interceptor fix added 5 more).

**2. Check model access**

In the Amazon Bedrock console, region `eu-west-2`, open "Model access" and make sure Claude Opus 5.5 is enabled. Then:

```bash
python3 - <<'PY'
from anthropic import AnthropicBedrockMantle
client = AnthropicBedrockMantle(aws_region="eu-west-2")
message = client.messages.create(model="anthropic.claude-opus-5-5", max_tokens=200,
                                 messages=[{"role": "user", "content": "Say hello in five words."}])
print(next(b.text for b in message.content if b.type == "text"))
PY
```

**Expected result: one short sentence. Run 23 November 2026: refused.** Not a model-access
toggle - an AWS Organizations SCP denies Opus, Sonnet and Haiku 5.5, and the 4.5-generation
models too, in this account. Switching `--model` in step 3 won't help until that's lifted.
See "Fourth review: the stage 3 run" for the exact errors and the policy ARN.

**3. Talk to the agent on your machine (beats 1 and 2)**

No deploy is needed for this step. The Gateway and the tools are the deployed ones.

```bash
MV_PASSWORD='<password>' python3 demo/agent_chat.py --user maya
```

Say these, in order:

| You type | Expected result |
|---|---|
| `The X-7 sensor ships under classification 7A994. Remember that.` | A `memory_write` line with status 200, to an Engineering namespace |
| `Which classification did I give, and for which product?` | The right answer, with no memory call. That is short-term memory |
| Wait one minute, then `/new` | "new conversation" |
| `What is the X-7 classified as?` | A `memory_search` line with status 200, and the answer with the namespace it came from. That is long-term memory |

**4. The refusal and the visa in a conversation (beats 3 and 4)**

In the same chat as Maya:

| You type | Expected result |
|---|---|
| `What has Legal recorded about the X-7?` | A `memory_search` line with status 403, marked REFUSED. The agent says access was refused and does not guess |
| `Request access to Legal for one hour. Reason: export review for the X-7.` | A `request_access` line with status 200 and a grant ID |
| In a second terminal, approve as Liam: `call "$LIAM" approve_access '{"grant_id":"<id>"}'` | `"status": "active"` |
| `Try Legal again.` | A `memory_search` line with status 200, and Legal's fact |

**5. Deploy the agent to the Runtime**

```bash
# Build and push the image as in the stage 2 runbook, step 4, and put the new digest in
# cdk/stacks/agentcore_stack.py.
cd cdk && cdk diff MemoryVisaAgentCore
```

Expected diff: two new statements on the Runtime role (`bedrock-mantle:CreateInference`, and `ssm:GetParameter` on `/memory-visa/gateway-mcp-url`), `MODEL_ID` on the Runtime, the new image digest, and a new description on the Orchestrator target. Nothing else. Then `cdk deploy MemoryVisaAgentCore`.

**6. Talk to the deployed agent**

```bash
MV_PASSWORD='<password>' python3 demo/agent_chat.py --user maya --url "$GATEWAY_URL/Orchestrator/invocations"
```

U4 is answered: it's `/Orchestrator/invocations`, not `/invocations` alone - see "Fourth review".
This step still can't finish end to end until U2 (the model block) is lifted.

**7. Send me the output of steps 2, 3, 4 and 6**

### Stage 2: what was done

Run 23 November 2026, against the sandbox account (`eu-west-2`), using Jake's
own AWS SSO session. Actions A1 to A9 all ran; A10 (send me the outputs) is this section.

| Step | Result |
|---|---|
| A2, diff | Matched the expected diff exactly: the visa role, the Lambda's five new environment variables and three new permissions, the runtime role losing memory and table access, the two new tools, the Cedar permit, three new parameters, four regex rules with no PII rules |
| A4, deploy | `cdk deploy --all`, all three stacks. Clean |
| A5, image | Built and pushed `linux/arm64`, digest `sha256:4c604185eeea6808c48e2c4794fc53a15d9e0f8cdd4d381b01f02189d6002f7f`, committed in `0091dbf` |
| A6, write | `memory_write` as Liam: `{"result": {"namespace": "org_robotinc_dept_legal", "eventId": "0000001795429942485#5581be4e"}}` |
| A7, read both ways | `memory_search` as Liam: the fact comes back. As Maya, no grant: `403 "maya@robotinc.example has no access to namespace 'org_robotinc_dept_legal' (no static claim, and no active grant)"` |
| A8, the visa proof | `1. org_robotinc_dept_legal ALLOWED: 2 record(s)` / `2. org_robotinc_dept_finance REFUSED by AWS: AccessDeniedException` / `3. org_robotinc_dept_legal REFUSED by AWS: AccessDeniedException` (after the window). Lines 2 and 3 refused: the idea holds |
| A9, one grant end to end | Request, approve, `memory_search` as Maya succeeds, `memory_write` as Maya refused (`"a grant never allows a write"`), `whoami` shows the granted namespace and its `expires_at`, and after the window closes the same search is refused again |

All nine results matched the runbook's "expected result" column exactly. The blog's
transcripts in "Maya Gets Her Fact" and the Quickstart's `tools/list` now carry this real
output, not the placeholder from before.

**One finding from running A6, S12, was first recorded as a Gateway bug. It is not one.**
See below.

### S12. The "invalid JSON" from the Gateway is the shell, not the Gateway

Recorded in `e34b8f4` as: the Gateway under-escapes a tool result, so every `tools/call`
response is invalid JSON, and it is AWS's bug. **Third review, same day: that conclusion is
almost certainly wrong, and the public claim is withdrawn** (blog, README and this section
corrected).

What the evidence shows:

- The broken string quoted as proof was
  `"text":"{\"hello\":\"world\",\"nested\":{\"quote\":\"a \\"quoted\\" value\"}}"`.
- I built the response a correct Gateway would send for that result, wrote it to a file, and
  parsed it with Python's `json`: valid.
- I put the same bytes in a shell variable and printed them with zsh's `echo`. The output is
  the broken string above, character for character, and the parser fails at the same place.
- With `printf '%s'`, or with bash's `echo`, the same variable parses.

zsh's `echo` treats `\\` as an escape and prints one backslash. A tool result is JSON inside
a JSON string, so its inner quotes arrive as `\\\"`; after `echo` they are `\\"`, which is
an escaped backslash and then a bare quote. That is exactly "escapes the backslash but not
the quote that follows it".

Why the two checks in `e34b8f4` did not catch it:

- "A direct Lambda invoke returns correct JSON": `aws lambda invoke` writes to a file, so the
  bytes never passed through `echo`.
- "A handler that returns a plain dict breaks the same way": the example used a value with
  quotes inside it, which is what needs the double escape. The claim that `{"hello": "world"}`
  alone breaks is not supported by the example shown.

And one thing that should have raised doubt at the time: MCP clients parse every response
with a strict JSON parser. If the Gateway sent invalid JSON for every `tools/call`, no client
could have used any of these tools, and the earlier sessions in the blog would not have worked.

**To confirm on the machine that saw it** (one minute, no shell in the way):

```bash
curl -s -X POST "$GATEWAY_URL/mcp" -H "Authorization: Bearer $LIAM" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"AccessRequestTools___whoami","arguments":{}}}' \
  -o /tmp/resp.txt
sed -n 's/^data: //p' /tmp/resp.txt | python3 -c 'import json,sys; d=json.load(sys.stdin); print("valid JSON"); print(json.loads(d["result"]["content"][0]["text"]))'
```

**Run 23 November 2026, on the same machine: it printed "valid JSON" at both levels, nested
included. S12 is closed.** No AWS support case. The response was always valid; the `echo` in
the original debugging session was the only thing that broke it.

The lesson for the runbook: capture responses with `curl -o` or pipe `curl` straight into
the parser. Never through `echo` in zsh.

### Runbook: stage 2 on another machine

Everything needed to deploy and prove the visa from a clean machine. The steps are the
action points A1 to A10, written out as commands. Nothing here has been run against AWS
yet, so read each output before the next step.

**0. What the machine needs**

| Tool | Used for | Check |
|---|---|---|
| Git and the GitHub CLI | Clone and push | `gh auth status` shows `jakesky45` |
| Python 3.10 or later | Tests, CDK app, demo script | `python3 --version` |
| Node.js and the CDK CLI | Deploy | `npx cdk --version` |
| AWS CLI, signed in to the sandbox account | Deploy, tokens, the proof | `aws sts get-caller-identity` |
| Docker, with `buildx` | The orchestrator image (arm64) | `docker buildx version` |
| `jq`, `curl` | Calling the tools | `jq --version` |

**1. Clone, and set the identity for this repository**

A fresh clone uses the machine's global git identity. Set the personal one before any commit.

```bash
git clone https://github.com/jakesky45/memory-visa.git && cd memory-visa
git config user.name "jakesky45"
git config user.email "jakesky45@users.noreply.github.com"
git log -1 --format='%h %s'      # 6725930 or later
```

**2. Run the tests (no AWS needed)**

```bash
python3 -m venv cdk/.venv && source cdk/.venv/bin/activate
pip install -r cdk/requirements.txt pytest boto3
make eval                        # expect: 49 passed
```

**3. Check what the deploy will change**

```bash
export AWS_REGION=eu-west-2
cd cdk && npx cdk diff --all && cd ..
```

Expected, and nothing else:

- A new IAM role, `memory-visa-reader`, trusted by the tools Lambda.
- The tools Lambda: five new environment variables, and permission to write events, to assume
  the new role, and to apply the Guardrail.
- The runtime role loses memory and table access. The runtime has no environment variables.
- The tools target: `memory_search` and `memory_write` added; `requester_org` removed from
  `request_access` and `include_tools` added.
- The Cedar permit lists the two new tools.
- Three new parameters under `/memory-visa/`.
- The Guardrails stack: four regex rules where there were three, and no PII rules.

If the diff fails on the Gateway or the Policy resources, the CDK library is older than the
one the stack was written with: `pip install -U aws-cdk-lib`.

**4. Build and push the orchestrator image, and record its digest**

`server.py` changed, and the stack names the image by digest.

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=$ACCOUNT.dkr.ecr.eu-west-2.amazonaws.com/memory-visa-orchestrator
aws ecr describe-repositories --repository-names memory-visa-orchestrator >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name memory-visa-orchestrator
aws ecr get-login-password | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.eu-west-2.amazonaws.com
docker buildx build --platform linux/arm64 -t $REPO:latest --push .
aws ecr describe-images --repository-name memory-visa-orchestrator --image-ids imageTag=latest \
  --query 'imageDetails[0].imageDigest' --output text
```

Put the digest that the last command prints into `cdk/stacks/agentcore_stack.py`, in the
`container_uri` line (`@sha256:...`). Commit that change.

**5. Deploy, with your own role allowed to run the proof**

```bash
ROLE_NAME=$(aws sts get-caller-identity --query Arn --output text | cut -d/ -f2)
MY_ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)
echo "$MY_ROLE_ARN"              # arn:aws:iam::<account>:role/...  (not an sts assumed-role ARN)

cd cdk && npx cdk deploy --all --require-approval never -c visaDemoPrincipalArn="$MY_ROLE_ARN" && cd ..
```

If the deploy fails on the Guardrail with a message about a regex, remove the
`aws-secret-access-key` rule in `cdk/stacks/guardrails_stack.py` and deploy again. That
pattern has not been tried against Bedrock.

**6. Sign in as each person, and define one helper**

Tokens last one hour. `<password>` is whatever you set; it is never written to the repository.

```bash
USER_POOL_ID=$(aws ssm get-parameter --name /memory-visa/user-pool-id --query Parameter.Value --output text)
USER_CLIENT_ID=$(aws ssm get-parameter --name /memory-visa/user-client-id --query Parameter.Value --output text)
GATEWAY_URL=$(aws ssm get-parameter --name /memory-visa/gateway-url --query Parameter.Value --output text)

for u in maya liam; do
  aws cognito-idp admin-set-user-password --user-pool-id "$USER_POOL_ID" --username $u --password '<password>' --permanent
done

token() { aws cognito-idp initiate-auth --client-id "$USER_CLIENT_ID" --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=$1,PASSWORD='<password>' --query AuthenticationResult.AccessToken --output text; }
MAYA=$(token maya); LIAM=$(token liam)

# call <token> <tool> '<json arguments>'
call() { curl -s -X POST "$GATEWAY_URL/mcp" -H "Authorization: Bearer $1" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"AccessRequestTools___$2\",\"arguments\":$3}}" \
  | sed -n 's/^data: //p' | jq '.result // .error'; }
```

**7. Write, then read (A6, A7)**

```bash
call "$LIAM" memory_write '{"namespace":"org_robotinc_dept_legal","text":"The X-7 battery pack is classified under export control code 7A994 and requires an export licence before international shipment."}'
sleep 90                         # long-term extraction runs in the background
call "$LIAM" memory_search '{"namespace":"org_robotinc_dept_legal","query":"X-7 export control"}'
call "$MAYA" memory_search '{"namespace":"org_robotinc_dept_legal","query":"X-7 export control"}'
```

Expected: the write returns an event ID; Liam's search returns the record; Maya's returns a
403 with "no access to namespace".

If the write or the search fails, read the Lambda's log first:

```bash
FN=$(aws lambda list-functions --query "Functions[?contains(FunctionName,'AccessRequestTools')].FunctionName" --output text)
aws logs tail "/aws/lambda/$FN" --since 10m
```

**8. The proof that AWS enforces the visa (A8)**

```bash
python3 demo/visa_credentials_demo.py \
  --namespace org_robotinc_dept_legal --other org_robotinc_dept_finance --seconds 60
```

Expected:

```
1. org_robotinc_dept_legal          ALLOWED: 1 record(s), first: "The X-7 battery pack ..."
2. org_robotinc_dept_finance        REFUSED by AWS: AccessDeniedException
3. org_robotinc_dept_legal          REFUSED by AWS: AccessDeniedException
```

This is the decision point. If lines 2 and 3 are refused, the idea holds. The table under
"If the proof does not match" says what each other result means.

**9. One grant, end to end (A9)**

```bash
call "$MAYA" request_access '{"target_org":"robotinc","target_department":"legal","reason":"Export-control classification for the X-7 before the Q4 shipment.","duration_hours":0.05}'
call "$LIAM" list_pending '{}'
call "$LIAM" approve_access '{"grant_id":"<grant_id from the request>"}'
call "$MAYA" memory_search '{"namespace":"org_robotinc_dept_legal","query":"X-7 export control"}'    # allowed
call "$MAYA" memory_write '{"namespace":"org_robotinc_dept_legal","text":"Supplier X is cleared."}'   # 403: a grant never writes
call "$MAYA" whoami '{}'
sleep 200                        # the window was 3 minutes
call "$MAYA" memory_search '{"namespace":"org_robotinc_dept_legal","query":"X-7 export control"}'    # 403 again
```

**10. Send the outputs, then tidy up**

Paste the outputs of steps 7, 8 and 9 into the conversation. They replace the "expected
output" transcripts in the blog.

To stop the billing when you are finished for the day: `cd cdk && npx cdk destroy --all`.

### Stage 1: what was done

| Finding | What changed | How it was checked |
|---|---|---|
| S3 | The Guardrail call is in `src/memory/guardrail.py`. A masked secret is stored masked. Blocked content raises and returns a 422; the refusal message is never stored. No Guardrail, or an intervention with nothing to store, refuses the write | 7 offline tests |
| S2 | The private-key rule is `BLOCK` again, so the write is refused. A new rule masks `aws_secret_access_key = ...`. The key ID and the Postgres URI are still masked | Read the template. **Deployed in stage 2. Not yet exercised live with an actual secret** |
| S4, D7 | The PII rules are removed. Shared facts keep their names; secrets are masked or refused | Read the template. **Deployed in stage 2 and confirmed: Liam's and Maya's names came back unmasked in every live call** |
| S7 | The `memory_write` description says "a namespace the caller is a member of" | Read |
| D1 | A grant opens memory only. It carries the domain's tools only when the request sets `include_tools` and the owner approves it. The owner sees the flag in `list_pending` | Offline tests; the two evals are updated |
| S10, N4 | Tests for the tools Lambda (11) and the DynamoDB store (6), including the race between two decisions | They need `boto3`. They are skipped without it |
| S1 | The blog's new transcripts are labelled as expected output, not a captured run | Read. Replace with real output after stage 2 |

`make eval` now runs 49 tests with `boto3` installed. Without it, 32 run and the two
modules that need it are skipped, with a message.

Still open from the second review: S5 (the Lambda's AWS SDK version), S6 (project
namespaces), S8 (a write visa), S9 (the usage log has no outcome), S11 (the dead route in
the Cedar permit).

### Second review: commits `ca72969` and `3db97ab`

These two commits wire up the Guardrail (M6) and move memory to verified MCP tools (M1).

What is good:

- **M1 is fixed the right way.** The claims-body path is deleted, not patched. `server.py` is
  a health check, and its role has no memory, table or guardrail permissions left.
- **The visa role now trusts the tools Lambda**, which is the only thing that reads memory.
- **Without the visa role the Lambda cannot read at all.** It has no read permission of its
  own, so a missing setting fails closed.
- **The README says what is not deployed.** Keep doing that.

What needs fixing. None of it is deployed yet, so fix before the deploy:

| # | Severity | Finding | Fix |
|---|---|---|---|
| S1 | High | **The blog shows the new tools as "run for real", and they were not.** The `memory_write` and `memory_search` transcripts carry the event ID (`0000001795364012656#508fa195`) and the time (`2026-11-22T16:17:46Z`) of the old run, put into the new JSON shape. The `tools/list` output now lists eight tools. The README says this path is not deployed. This is the one thing that would cost you the audience's trust if someone checked | Run them live and paste the real output, or label them "expected output" until you have |
| S2 | High | **Private keys are now stored.** The Guardrail rules changed from `BLOCK` to `ANONYMIZE`. The private-key pattern matches only the header line, so the header is masked and the key itself is written to memory. Before, the whole write was refused. The same is true of the secret that normally follows an `AKIA...` key ID | Keep `BLOCK` for the private key and refuse the write. Or match the whole block, to the `END` line |
| S3 | High | **A blocked write stores the refusal message as a fact.** `_apply_guardrail` treats every intervention as a redaction and stores `outputs`. When a rule blocks, `outputs` is "This input contains content that can't be stored." That sentence then goes into memory. Also, empty `outputs` falls back to the raw text, and no Guardrail ID means no check at all | Read the assessment. If anything was blocked, raise and return an error to the caller. Fail closed when `outputs` is empty |
| S4 | Medium | **Names, emails and phone numbers are masked on every write.** The PII rules anonymise `NAME`, `EMAIL` and `PHONE`. Shared organisational facts need names: who approved, which supplier, who to ask. A fact may come back as "{NAME} is banned under 7A994" | Decide what memory may hold. Remove the `NAME` rule, or apply the PII rules to user namespaces only |
| S5 | Medium | **The Lambda may not know the AgentCore API.** The memory tools now run in the Python 3.12 Lambda runtime with its built-in AWS SDK, which is often months old. `bedrock-agentcore` and `retrieve_memory_records` need a recent one. I have not checked which version the runtime ships | On the first call, look for `UnknownServiceError` or a parameter error in the logs. If it appears, bundle a current `boto3` with the function |
| S6 | Medium | **Project namespaces do not exist live.** `_claims_from` builds claims from one department and no projects, and Cognito has no projects attribute. So the shared project namespace (`test_eval2`) and Jordan's project scope work only in the offline tests | Add a `custom:projects` attribute and read it in the interceptor, or stop describing project sharing as working |
| S7 | Low | **The `memory_write` description is wrong.** It says it "always writes to the caller's own namespace". It takes a `namespace` and accepts any namespace the caller is a member of, such as their department's | Say "a namespace the caller is a member of" |
| S8 | Low | **Writes are checked in Python only.** The Lambda's role may write to the whole memory. `CreateEvent` accepts an `actorId` condition key, so a write could use a scoped credential in the same way a read does | Later: a write visa, scoped to the one actor |
| S9 | Low | **The usage log has no outcome.** It is written before the check, so a refused read and an allowed read look the same, and neither names the grant | Log after the check, with allowed or refused and the grant ID. This becomes N2 |
| S10 | Low | **No tests for the new code.** The two tools, the claims builder and the Guardrail function have none | Add them with N4 |
| S11 | Low | **The dead orchestrator route is still permitted.** `Orchestrator___POST:/invocations` is in the Cedar permit and returns 410 | Remove it from the permit until N6 gives it a job |

### Done since the review

| Finding | What changed | Commit | How it was checked |
|---|---|---|---|
| M1 | Memory is read and written through `memory_search` and `memory_write`, with identity from the verified token. The claims-body path and the body-driven approvals are deleted | `3db97ab` | 11 offline tests (`test_tools_lambda.py`). **Deployed and proved live in stage 2** |
| M2 | Memory is read with a visa: AWS credentials that allow one namespace until the grant ends. The reader's own role cannot read memory | `a280bd5`, `3db97ab` | 11 offline tests. **Deployed and proved against real AWS in stage 2 (A8): denied a different namespace, denied the same namespace after the window closed** |
| M3 (write) | A grant reads and never writes. Only a member writes into a namespace | `451beca` | Offline test. **Confirmed live in stage 2 (A9): a write under a grant came back 403** |
| M6 | The Guardrail is called on every write | `ca72969` | Read the diff. **Deployed in stage 2.** The one live write went through it without masking (plain text, nothing sensitive); S2/S3/S4's fixes are deployed but not yet exercised live with content that should trip them |
| G2 | A grant is decided once. An expired, denied or revoked grant cannot be approved; approving twice does not move the window; a closed window cannot be revoked | `451beca` | Offline tests on the in-memory store |
| G2 (race) | The DynamoDB store writes a decision only if the status is still what it read | `451beca` | **Syntax only.** No test covers the DynamoDB store |
| G2 (limits) | A request needs a reason, and a window of at most 24 hours | `451beca` | Offline tests |
| G4 | The requester's org comes from the verified token. `requester_org` is removed from the tool's schema | `451beca` | **Syntax only.** No test covers the tools Lambda |

`make eval` runs 25 tests: the 6 properties, 11 for the visa, 8 for the grant rules.

### Your action points: fix, deploy and prove

**Done 23 November 2026 in stage 2, using Jake's own AWS session.** See "Stage 2: what was
done" above for the real results. Left below as a record of what was expected at each step -
every one of them matched.

| # | Action | Command or detail | Expected result |
|---|---|---|---|
| A1 | Pull | `git pull` | S2 and S3 are fixed in the code you deploy |
| A2 | Check the infrastructure change | `cd cdk && source .venv/bin/activate && cdk diff --all` | A new role `memory-visa-reader`, trusted by the tools Lambda. The tools Lambda gains five environment variables and three permissions: write events, assume the visa role, apply the Guardrail. The runtime role loses memory and table access, and the runtime has no environment variables. Two new tools on the target and in the Cedar permit. `requester_org` leaves the `request_access` schema. Three new parameters. An `include_tools` field on `request_access`. In the Guardrails stack: four regex rules where there were three, and the PII rules removed. Stop if you see anything else |
| A3 | Find your role ARN | `aws sts get-caller-identity --query Arn --output text` | If it is an `assumed-role` ARN, use the IAM role behind it: `arn:aws:iam::<account>:role/<name>` |
| A4 | Deploy, with your role allowed to run the proof | `cdk deploy --all -c visaDemoPrincipalArn=<ARN from A3>` | The stacks update |
| A5 | Rebuild and push the orchestrator image | `server.py` changed, and the stack names the image by digest, so update the digest | The runtime answers `/ping` |
| A6 | Write Legal's fact through the new tool | `memory_write` as Liam, into `org_robotinc_dept_legal`. Watch the Lambda's logs on this first call (S5) | A result with an event ID. Wait a minute for extraction |
| A7 | Read it both ways | `memory_search` as Liam, then as Maya with no grant | Liam gets the record. Maya gets a 403 |
| A8 | Run the proof | `python3 demo/visa_credentials_demo.py --namespace org_robotinc_dept_legal --other org_robotinc_dept_finance --seconds 60` | Line 1 `ALLOWED`. Lines 2 and 3 `REFUSED by AWS: AccessDeniedException` |
| A9 | Run the grant end to end | Maya requests, Liam approves, Maya searches, the window closes, Maya searches again | Allowed inside the window; refused after it |
| A10 | Send me the outputs of A6 to A9 | Paste them | Then replace the transcripts in the blog with the real ones (S1) |

If the proof does not match:

| What you see | Likely cause | What to do |
|---|---|---|
| A8 line 1 refused | AgentCore gives IAM the namespace in a form the policy does not list. The names are flat strings today; AgentCore stores them with a leading and trailing slash | Send me the output. It is a one-line fix in `namespace_condition_values` |
| A8 line 2 allowed | The namespace condition is not applied to this call | Stop and send me the output. The design needs a different condition key, and nothing else should be built on it until this is known |
| An error when the visa is minted in A8 | Your role is not in the visa role's trust policy | Check A3 and A4 |
| A6 or A7 fails with `UnknownServiceError` or an unknown parameter | The Lambda runtime's AWS SDK is too old (S5) | Bundle a current `boto3` with the function |
| A7 as Liam returns nothing | Extraction has not finished, or the write was masked or blocked by the Guardrail (S3, S4) | Wait a minute. Then read the Lambda's log for the write |
| A6 times out | The function has 10 seconds for the Guardrail call and the write, on a cold start | Raise the timeout |

### Your decisions

| # | Decision | Why it is yours | My recommendation |
|---|---|---|---|
| D1 | Does a grant still carry the domain's tools? | **Decided: not by default.** A request may ask for them with `include_tools` | |
| D2 | Is 24 hours the right longest window? | It is a constant, `MAX_GRANT_DURATION`. I chose it | Keep it, or set it for each domain |
| D3 | Does `REVIEW.md` go into the public repository? | **Decided: yes.** Published in `4605991` | |
| D4 | The thesis of the talk and the next post (B1) | **Decided:** "isolated by default, shareable on purpose". Five patterns, with the visa as the exception path | |
| D5 | The tone toward AgentCore (B5) | It is your voice on an AWS stage | Present it as the layer you add on top, not as where the platform stops |
| D6 | Verify three facts before they are said on stage (B4) | I could not confirm them | The name "Dogwood"; the AWS security blog and its date; the AgentCore and Bedrock Agents dates |
| D7 | What may memory hold? (S4) | **Decided:** names stay; secrets are masked or refused | |
| D8 | When is the Summit, and how long is the slot? | It decides how much of stages 3 and 4 is realistic | |
| D9 | How does the agent learn who is talking to it? | It decides whether there is one path to memory or two | **Decided: option A.** Built. See "Stage 3: the agent" |
| D10 | Which model does the agent use on Bedrock? | Cost, and what your account has enabled | **Decided: Claude Opus 5.5** (`anthropic.claude-opus-5-5`). `MODEL_ID` or `-c agentModelId=` changes it; Sonnet 5.5 and Haiku 5.5 cost less |

### Next to build

In this order. Each is one change with its own tests.

| # | Work | Finding | State |
|---|---|---|---|
| N1 | `memory_search` and `memory_write` as MCP tools behind the interceptor. The claims body and the body-driven approvals removed | M1 | **Done and deployed** (`3db97ab`), proved live in stage 2 |
| N3 | Apply the guardrail on write | M6 | **Done and deployed.** S2 and S3 fixed. The live write path works; not yet exercised with content that should trip a rule |
| N4 | Tests for the DynamoDB store, the tools Lambda, the claims builder and the Guardrail function | Tests, S10 | **Done.** 24 tests |
| N2 | Results carry provenance: record ID, namespace, time, score, and how the caller was allowed. Reads and writes go to the audit table with the outcome and the grant ID | M4, M10, S9 | **Done, deployed and proven live** (12 tests). The author is in the audit trail, not on the record: that needs N9. See "N2: provenance and the audit trail" |
| N5 | Queries in place of table scans; `expires_at` stored; expiry emitted on time | G1, G3 | To do, after N4. Every `memory_search` now scans the grants table |
| N6 | An agent that calls a model and uses memory in a conversation | Scale | **Written and tested offline** (17 tests). Not deployed, not run against a real model. See "Stage 3: the agent" |
| N7 | Search across every namespace the caller may read | M7 | **Done, deployed and proven live** (7 tests). Found Q4 (a user's own namespace can't be read - M8) along the way. See "N7: one search across what you may read" |
| N8 | One more sharing pattern: a published fact | Scale, S6 | **Done, deployed and proven live** (14 tests). Found and fixed a real SDK-version bug along the way (P5). Project namespaces and the brokered answer are not built. See "N8: published facts" |
| N9 | Shared facts written as long-term records with metadata; path namespaces | M5, M8 | To do, after A8 |
| N10 | Claims in the access token; the Cedar `forbid` made active | C1 | To do. Would also carry projects (S6) |

### The blog

Reworked 23 November 2026 (`docs/index.html`). What changed, and what is still open:

| Blog | State |
|---|---|
| Title and opening | **Changed.** "Isolated by Default, Shareable on Purpose", with the three answers up front: publish most facts, a visa for the exception, AWS enforces both (B1) |
| "Five Ways to Share" | **New.** The five patterns, with which are live and which are not |
| "A Shell Trap Worth Knowing" | **Removed as a section.** It was a detour from the argument. Three sentences remain in the Quickstart |
| "AWS Enforces It, Not Our Code" | **New**, in its place. The session policy as the code builds it, and the three real lines from the visa proof |
| "Publish Once, Read Everywhere" | **New.** From the live N8 run |
| "One Search, Still Scoped" | **New.** From the live N7 run, with the two to four seconds stated |
| "What the Owner Sees Afterwards" | **New.** The audit trail and `grant_activity`, from the live N2 run |
| "Maya Gets Her Fact" | The two setup bugs are now one sentence (B2). The claim that a read is only a log line is gone: reads are in the audit table. Says that results now carry provenance. The JSON transcript is still the one from the first run |
| "built on Cedar"; "GrantStore backing ordinary Cedar ABAC" | **Fixed.** Says what Cedar does today and where the grant is checked (B3, C1) |
| Tool counts | **Fixed.** Ten tools; the Quickstart list has all ten |
| The orchestrator | **Fixed.** Says it holds an agent that has not yet answered with a real model, so the post shows the tools and not the agent |
| Identity binding | One short paragraph on the header-case mistake, as a lesson |
| Staleness | Says a published fact cannot yet be withdrawn |
| The middle sections | **Tightened** by about a quarter (1,725 words to 1,259): "What a Grant Unlocks", "No Bespoke UI", "Closing the Loop", "whoami". The Cedar gap is now one paragraph, not a numbered bug story |
| Facts (B4, D6) | **Checked against the AWS pages on 23 November 2026.** "Dogwood" is AWS's own name; the wording now follows AWS ("extends the authorization model of Cedar"). Per-caller session isolation is confirmed for authenticated Gateways and is now quoted with its link. AgentCore general availability: 13 October 2025, confirmed. Bedrock Agents Classic closed to new customers from 30 July 2026, confirmed. The Claude Code issue is closed without a fix, not "open": corrected |
| Still open | **The AWS Security Blog post.** I could not read its body or confirm its date (the page returns only navigation to my tools). The post says it came out "three days before this post" and lands on "the exact same primitive". Jake: open it in a browser and check both before the talk. The new transcripts are shortened from the run records, not raw JSON: replace them with raw output when you next run the tools. Beats 1 and 2 are not in the post, because no model has answered yet. Two links in the Dogwood paragraph are community posts, not AWS |

### The demo, in four beats

| Beat | What the audience sees | Ready? |
|---|---|---|
| 1. Short-term memory | Maya asks two questions in one conversation; the agent follows the thread | After stage 3, step 3 |
| 2. Long-term memory | A new conversation; the agent still knows what it learned | After stage 3, step 3 |
| 3. Shared, and refused | Legal records a fact; Engineering's read is refused | After A7. Refused by AWS itself after A8 |
| 4. The visa | Liam approves; the same call works; the window closes; AWS refuses again. The policy is on screen | After A9 |

Short-term memory is the transcript: events, for one person in one conversation, available
at once, kept 7 days here. Long-term memory is what was learned: records that a model
extracts from the transcript, stored under a namespace, found by meaning, kept 31 days here.
Shared context is long-term memory.

## Verdict

The grant workflow is a good idea with a clear story, and the write-up is honest. But the
project is an access-control demo with a thin memory layer attached. It does not yet show
context being shared, and it does not show scale.

Three things would stop me presenting it as it is:

1. **The memory path is the least protected path in the system.** The headline feature can be
   bypassed by anyone who holds a Gateway token.
2. **"Memory" is a namespace check in front of two API calls.** There is no agent that uses
   the memory, no provenance on what comes back, and no way to share one fact without opening
   a whole department.
3. **The blog claims more than the code does** ("built on Cedar", "credential scrubbing",
   "six properties proven"), and a Summit audience will include people who can tell.

All three are fixable. The plan at the end puts them in order.

## What is strong

Keep these. They are the parts a talk can stand on.

- **The two-person approval as MCP tools.** The owner approves from the AI client they already
  use. No approval UI. This is the most original part.
- **Identity from the verified token, never from arguments** (`identity_interceptor.py`). The
  overwrite-never-merge rule is right, and the adversarial transcripts prove it.
- **The Dogwood analysis.** Explaining why a single-caller session history cannot hold a
  two-person approval is a real insight.
- **The contractor case.** A narrower default through the same mechanism, with no special code.
- **The honesty.** The blog names its own gaps. Keep that, but move it (see the blog section).
- **`whoami`.** Showing effective access before and after expiry is the best demo moment.

## The memory layer

This is where the project is weakest. Findings are in order of importance.

### M1. The memory path trusts the request body for identity

`src/server.py:61-68` builds `UserClaims` from `payload["claims"]`. The orchestrator never
reads the verified token. So a caller can send any `email`, `org` and `departments` and read
or write any namespace. The shared client-credentials token is accepted on this target.

The same file is worse for approvals: `approve_access`, `deny_access` and `list_pending`
(`server.py:100-110`) take `approver_email` and `owner_email` from the body. So the hole that
`IdentityInterceptor` closes for the MCP tools is still open on the orchestrator: anyone can
approve a grant as Liam through Tier One.

The blog and the README both admit the first half. Neither mentions the approval half. For a
project whose subject is "who may read this memory", this is the first fix.

**Recommendation:** make `memory_search` and `memory_write` real MCP tools on the Gateway,
behind the interceptor, using `_verified_email`, `_verified_org` and `_verified_department`.
Then delete the `claims` body and the three approval actions from `server.py`. Tier One has
no reason to exist after that.

### M2. Enforcement is one Python `if`, and the platform does not back it

`GuardedMemory._check` (`guarded_memory.py:72-77`) is the only control. The runtime role has
`CreateEvent` and `RetrieveMemoryRecords` on the whole Memory resource
(`agentcore_stack.py:730`). Any other code that runs with that role, or any second agent
added later, reads everything.

AgentCore can enforce this itself. The IAM reference lists `bedrock-agentcore:namespace` as a
condition key on `RetrieveMemoryRecords` and `BatchCreateMemoryRecords`, and the developer
guide shows exact and prefix matches on namespaces.

**Recommendation, and the strongest idea for the talk:** when a grant is approved, mint
short-lived credentials (STS `AssumeRole` with a session policy) that allow reads on exactly
that namespace for exactly the grant's duration. The visa then is a real credential. AWS
enforces the boundary and the expiry, and the Python check becomes a second layer, not the
only one. Check one caveat first: the docs say caller-scoped IAM conditions do not apply in
the Gateway's `GATEWAY_IAM_ROLE` mode, so this fits a Lambda or Runtime that calls Memory
directly.

### M3. A grant is a whole department, read and write, for the full window

Maya needs one fact. The grant gives her everything in `org_robotinc_dept_legal`, the right
to write into it, and Legal's tools including `log_compliance_note`.

Probe result: with an approved grant, a visitor wrote "Supplier X is cleared for export" into
Legal's namespace, and held Legal's write tool. `GuardedMemory.put`
(`guarded_memory.py:79-82`) uses the same check as `retrieve`. The docstring in `grants.py`
says "temporary read access"; the code does not.

This is memory poisoning by design: the next Legal agent reads the visitor's text as Legal's
own knowledge. AWS has a security blog on exactly this risk (see Sources).

**Recommendation:**
- Add a `scope` to `Grant`: `read` by default; `write` only on request, and shown to the
  approver.
- Take tools out of the grant by default. "A full visa" is simple, but it is not least
  privilege, and that is the first question a security audience asks.
- Longer term, share less than a namespace (see "Sharing context at scale").

### M4. What comes back has no provenance

`AgentCoreMemoryBackend.retrieve` (`agentcore_backend.py:29-35`) returns `{"text": ...}` and
nothing else. No record ID, no namespace, no author, no time, no score.

For shared memory this matters more than anything. When Engineering's agent reads a Legal
fact, it must be able to say who asserted it, when, and whether it is still current. In the
blog's own example the retrieve returns two facts about 7A994 with no way to tell which is
newer or who wrote either.

**Recommendation:** return `memoryRecordId`, `namespace`, `createdAt` and the score. On
write, attach metadata: the verified author, the source, a classification label, and a
`valid_until` or `supersedes` field. Show provenance in the demo output.

### M5. LLM-extracted semantic memory is the wrong store for authoritative facts

Writes go in as a `USER` conversational event (`agentcore_backend.py:21-26`). A background
model then extracts "facts". That is lossy and delayed: about a minute in the blog, and the
extracted text is the model's rewording. For an export-control classification, the store
must return what Legal wrote.

Related problems:
- **No correction and no delete.** The blog's "staleness" section admits this. A wrong fact
  stays for 31 days. There is no supersede and no erase.
- **The organisation forgets in a month.** `record_expiry_duration_days=31` and
  `event_expiry_duration=7` (`agentcore_stack.py:675-683`). The comment says 31 is the
  minimum. For shared organisational knowledge, the minimum is the wrong choice.
- **`actorId` is used as the namespace** (`agentcore_backend.py:23`). Every member of a
  department writes as one actor. AgentCore then cannot tell Liam from another lawyer.

**Recommendation:** split the two kinds of memory and say so in the talk.
- *Conversational memory:* per user and per session, extracted by AgentCore. Keep as is.
- *Shared facts:* written directly as long-term records (`BatchCreateMemoryRecords`), with
  the real `actorId`, metadata, and an explicit update and delete path. No extraction step,
  so no delay and no rewording.

### M6. The secret scrubbing is not applied in the live system

`server.py:43`: `_apply_guardrail = lambda text: text`. The comment says the Guardrail
attaches at model invocation, but the orchestrator never invokes a model. So in the deployed
stack a pasted access key is stored as written.

`test_eval3` passes because it tests `tests/fakes.py`, which has its own regex. The README
lists "credential scrubbing" as proven and the Guardrail as live. Both are true only offline.

**Recommendation:** call `ApplyGuardrail` in the write path, or remove the claim. Add one
live integration test for it.

### M7. A caller must name the namespace

`retrieve` takes one namespace. An agent that wants "everything I may see about the X-7
battery" must know every namespace name and query each one.

**Recommendation:** add a search across all allowed namespaces: compute the allowed set from
claims and grants, query each, merge and rank, and return provenance with each result. This
is the function that makes shared memory useful, and it is the natural demo for "sharing
context".

### M8. Namespaces are flat strings joined with underscores

`org_{org}_dept_{dept}` is built by string formatting and parsed with `split("_", 3)`
(`access_requests.py:53`, `domain_config.py:37`). There is no validation, so an org or
department that contains an underscore breaks the parse. Email addresses are part of
namespace names.

**Recommendation:** use hierarchical paths, `/org/{org}/dept/{dept}/...`, which AgentCore
supports and which allow prefix conditions in IAM. Build and parse them in one small typed
module. AWS published a namespace design guide for AgentCore Memory in this style (see
Sources); align with it and cite it.

### M9. Access is time-boxed; knowledge is not

After the window closes, the fact is still in Maya's conversation, and her own agent may have
extracted it into her user namespace. The visa bounds new reads only.

That is acceptable, but the blog says "without leaving the door open behind them", which
reads as a stronger claim. Say what the control does: it limits and audits new reads.
Optionally tag anything derived from a granted read with its source and grant ID.

### M10. Reads are not in the audit trail

The grant audit table records request, approval and expiry. It does not record what was read
under the grant. Reads go to stdout (`server.py:47-58`), with identity taken from the
unverified body. So the audit of the thing that matters most can be forged.

**Recommendation:** write a `read` event to the audit table with the verified identity, the
namespace, the query and the `grant_id` that authorised it.

## Sharing context at scale

The current model has one way to share: open a whole namespace for a while. A talk on
sharing context at scale needs more than that. A design that would carry the title:

| Pattern | When | In the repo today |
|---|---|---|
| **Scoped by default** | Each user, department and project has its own namespace | Yes |
| **Shared by design** | A project namespace that several departments read and write | Yes (`test_eval2`), but the blog barely mentions it. This is the common case and it deserves a section |
| **Published facts** | A department marks a fact as readable by the org, with an owner and an expiry | No. This is how most context should be shared: Legal publishes the classification once, and nobody needs a grant |
| **Brokered answer** | The requester asks a question; the owner's side answers it; the answer is copied to the requester with provenance | No. "Share the answer, not the access" is the least-privilege form of the Maya story |
| **Visa** | A real exception: time-boxed access to a namespace, approved by the owner | Yes. Keep it as the exception path, not the main path |

Position the visa as the last of five patterns, not the only one. That changes the story from
"we built an approval flow" to "here is how an organisation shares agent memory, and here is
how the exceptions work".

To show scale, the talk needs evidence the repo cannot produce today:

- **An agent in the loop.** Nothing here calls a model. Show one agent answering a question
  better because it read another team's published fact.
- **Numbers.** Namespaces, records, grants per day, retrieve latency at p50 and p95, cost per
  thousand operations. Even small measured numbers beat none.
- **The hot path at size.** `active_grants_for` scans the whole grants table on every memory
  read (see G1). Fix it, then measure it with 10,000 grants.
- **Many clients.** The bring-your-own-client point is good. Show two different MCP clients in
  the demo.

## Grant store and tools

### G1. Every memory read scans the whole grants table

`DynamoGrantStore.active_grants_for` and `pending` call `scan()` with no pagination
(`grants_dynamodb.py:114-119`). Cost and latency grow with every grant ever made. Past 1 MB
of items, a scan returns one page, so active grants are missed and access is wrongly denied.

**Recommendation:** add a GSI on `requester_namespace` and one on the target domain with
status. Use `Query`. Store `expires_at` as an attribute.

### G2. The state machine has no guards

Confirmed with probes on the in-memory store; `grants_dynamodb.py:78-101` has the same logic.

- An **expired** grant can be approved again. The window reopens with no new request and no
  new reason.
- A **denied** grant can be approved afterwards.
- A **ten-year** duration with an **empty** reason is accepted.
- Approve, deny and revoke are read-then-write with no condition, so two calls can race.
- The grant write and the audit write are two separate calls. One can succeed without the other.

**Recommendation:** allow only `pending → approved | denied` and `approved → revoked |
expired`. Enforce it with a `ConditionExpression` on status. Write the grant and its audit
event in one `TransactWriteItems`. Cap the duration, per domain. Require a reason.

### G3. Expiry is noticed, not enforced

A grant becomes `expired` only when someone next checks it (`grants.py:104-119`). So:

- The audit time is when it was noticed, not when it ended.
- A grant nobody checks again stays `approved` in the table for ever. An auditor who reads
  the table sees an active grant.
- A read path writes to the table, so every reader needs write permission on it.

**Recommendation:** store `expires_at` at approval and compare against it. Emit the expiry
event at the right time with EventBridge Scheduler. Add a DynamoDB TTL for clean-up. With
the credential idea in M2, the expiry is also enforced by AWS.

### G4. The requester's org comes from the caller

`access_request_tools.py:98` uses `args["requester_org"]`, although the interceptor provides
`_verified_org`. Probe result: a caller's namespace becomes
`org_helios-biotech_user_maya@robotinc.example`. Use the verified org.

### G5. Owners and tools are constants in code

`DOMAIN_OWNERS` has one person per domain. There is no deputy and no delegation, and nothing
happens when an owner leaves. At scale, owners must come from the identity provider (a group
per domain) or a table. `DOMAIN_TOOLS` lists eight tools; one exists, as a stub.

### G6. `GetUser` on every tool call

The interceptor calls Cognito for every request. That adds latency and a Cognito rate limit
to the hot path. See C1 for the fix.

## Cedar and identity

### C1. Cedar does no real work today

The only active policy permits every listed action to every OAuth user
(`agentcore_stack.py:93-108`). The one `forbid` is `LOG_ONLY` and cannot work, because Cognito
access tokens carry no custom attributes. All real authorisation is Python.

The blog's hero line says "built on Cedar", and the body says `GrantStore` is "backing
ordinary Cedar ABAC". Neither is accurate: the grant store is not connected to Cedar at all.

**Recommendation, in two steps:**

1. **Put the claims in the access token.** A Cognito pre token generation trigger (event
   version 2) can add claims to access tokens. It needs the Essentials or Plus plan. Then
   `hasTag("department")` works, the `forbid` can go `ACTIVE`, and the per-call `GetUser`
   (G6) is no longer needed for org and department.
2. **Consider Amazon Verified Permissions for the grants.** A grant maps naturally to a
   Cedar policy: permit this principal, this action, this namespace, until this time. Create
   it on approval; delete it on revoke. Then "built on Cedar" is true, the decision is one
   `IsAuthorized` call, and the audit is in CloudTrail. I have not verified the time
   condition against the current Cedar and Verified Permissions documentation; check that
   before you commit to it.

## Tests

- **Only the in-memory path is tested.** There are no tests for `DynamoGrantStore`, the
  interceptor, the tools Lambda or `server.py`. The code that runs live is the code without
  tests.
- **The fake retrieve ignores the query** (`tests/fakes.py:26`). It returns everything in the
  namespace, so no test covers retrieval.
- **"Six properties end to end" overstates it.** They are unit tests against fakes. Call them
  that.
- **Add negative tests** for G2 (re-approve, approve after deny, duration cap), for M3 (a
  read grant must not allow a write), and for G4.
- **Add a small live suite** behind a flag: one round trip through the real Gateway, one
  guardrail check, one expiry.

## The blog

It is well written and unusually honest. It is not yet the right piece for this aim.

### B1. It argues a different thesis from the talk

The title is "Cross-Domain Access Grants on Bedrock AgentCore". The talk is "sharing context
at scale". "Why Share Memory At All" is three paragraphs, and the post never returns to it.
There is no moment where an agent does better work because memory was shared.

**Recommendation:** decide the thesis first. For the Summit: *shared agent memory is only
useful if it is isolated by default and shareable on purpose; here are the patterns, and here
is the exception path.* Then restructure around the five patterns above.

### B2. Too much of it is a bug diary

About a third of the 4,600 words is debugging: the `LOG_ONLY` policy, the execution role, the
namespace template, stdout buffering, the Claude Code OAuth bug. Each is interesting. Together
they bury the idea, and the reader finishes with a list of things that do not work: the Cedar
gate is inert, Slack is not wired, the memory path trusts the body, seven of eight tools do
not exist.

**Recommendation:** move the war stories to a separate "what we learned building on
AgentCore" post, or a short box near the end. In the main post, fix the gaps or scope them
out in one "limits" section.

### B3. Claims that do not match the code

| Blog or README says | The code |
|---|---|
| "built on Cedar"; "GrantStore backing ordinary Cedar ABAC" | Cedar permits everything. The grant store is not connected to it |
| "org and department claims ride along as JWT claims, which Cedar reads as tags" | Later in the same post: access tokens carry no custom attributes, so Cedar never sees them |
| "what a grant opens today is Memory ... nothing more" | Two paragraphs on: a grant carries the whole toolset. And it allows writes |
| "five tools" (five times) and "six tools" | Six tools. Pick one number |
| "Credential scrubbing" proven; Guardrail "live" | The live guardrail function returns its input (M6) |
| "Six properties are proven end-to-end" | Unit tests against fakes |
| "without leaving the door open behind them" | True for new reads only (M9) |

### B4. Facts to verify before a Summit audience reads them

I could not verify these, and AWS staff will be in the room:

- The name "Dogwood" for the temporal policy feature, and that its history is per principal.
  The post cites two third-party write-ups; cite the AWS documentation only.
- That an AWS security blog, published three days before the post, uses the same interceptor
  pattern. Link it, with its date.
- "AgentCore only hit general availability in October 2025" and "the original Bedrock Agents
  service closed to new customers under ten months later".

### B5. Tone for an AWS stage

"Bet on the Pattern, Not the Platform" and "Where It Stops" are fair, but they read as
criticism of the host. The same point lands better as: *AgentCore gives you the isolation
primitives; this is the layer you add on top, and it is built from open standards.*

### B6. Smaller points

- The "Anyone can lock a door" paragraph appears twice in the first screen.
- The phrases "confirmed live", "proof, not the claim" and "reported honestly" repeat. Once
  each is enough; after that they sound defensive.
- There is no threat model. A half-page table (who the attacker is, what they hold, what
  stops them) would replace many of the scattered caveats.
- There is no comparison with the alternatives: a shared project namespace, copying the fact,
  or Verified Permissions. A reader will ask "why not just...".
- The Quickstart is in the post and in the README. Keep it in the README and link to it.

## Related sessions already in an AWS Summit catalog

Checked 23 November 2026 against four session abstracts Jake found in a Summit catalog
(exact event not confirmed - verify before citing on stage). None of these make this talk
redundant; one of them, SEC429, is close enough that it should shape how this talk is framed.

| Session | What it solves | How this project differs |
|---|---|---|
| **COM341**, "The AgentCore Governance Loop: Policy, Guardrails, Observability" | A fleet-wide governance loop: cataloging agents, Cedar at the gateway, Guardrails, observability | No overlap on cataloging/discovery - this project is one agent type per domain, not a fleet registry. Overlaps on Cedar (this project's C1 finding is a concrete, documented case of Cedar *not* working yet - an honest counterpoint a broad governance talk may not have room for) and on Guardrails (proven live today: a masked key, a blocked private key). Observability is this project's own weak spot (M10), not a strength to lean on against this session |
| **TNC317**, "Building Context-Aware Agents with AgentCore Memory" (hands-on lab) | One agent remembering one customer across sessions - personalization, Semantic/Summary/User Preference strategies | Not a competitor. No second party, no isolation, no access control. Different problem: this project is about memory *between* parties, not an agent's memory of one user |
| **SEC429 / SEC429-R1**, "Tenant-isolated agent memory: one IAM policy for AgentCore Memory" (400, run twice - popular) | **The closest session in the catalog.** Static isolation at scale: one ABAC policy plus STS session tags instead of one IAM policy per tenant, a Token Vending Machine minting per-tenant credentials, `actorId`/namespace conventions | Same underlying mechanism family (STS session tags scoping a role per request) aimed at the opposite half of the problem. SEC429: keep tenants apart, cheaply, at scale - isolate by default, full stop. This project: what happens the one time a day two already-isolated parties need a legitimate, time-boxed exception, with a human in the loop. Nothing in SEC429's abstract has a second person approving anything - that two-person approval as real MCP tools is this project's most original part (see "What is strong") and SEC429 doesn't touch it. Worth watching or reading before the talk: their `actorId`/namespace conventions likely bear on this project's own open M5/M8 findings, and "Token Vending Machine" is good AWS-native language to reuse for `VisaIssuer` |
| **SVS336-SC2 / SC3**, "Building production AI agents on AWS: compute, security, state, and cost" (simulcast - popular) | A broad production-agent survey: compute choices, IAM/Identity/gateways/secrets, RAG + Memory for state, cost control | Mile wide, inch deep by design. No access-control narrative, no approval workflow, no claim this project needs to compete with - a different altitude entirely |

**The pitch, if asked "isn't this just SEC429":** *SEC429 shows you how to keep a hundred
tenants apart with one policy. This talk shows what you do the one time a day two of them
legitimately need to cross - and how to make that crossing auditable, time-boxed, and
revocable instead of ad hoc.*

## The Summit talk

A 30-minute shape that uses what is already strong:

1. **The problem (3 min).** Five agents, five islands of memory. Centralise it and everyone
   sees everything. One slide.
2. **The model (5 min).** The five sharing patterns. Isolated by default, shared on purpose.
3. **Demo 1: shared context working (5 min).** Legal publishes a fact. Engineering's agent
   uses it in an answer, and shows who said it and when.
4. **Demo 2: the visa (7 min).** Maya is denied, requests, Liam approves from a different MCP
   client, `whoami` before and after expiry. Then the adversarial calls: approve your own
   request; approve as another owner.
5. **How it is built (5 min).** Verified identity at the Gateway, Cedar for the decision,
   scoped credentials for the enforcement, DynamoDB for the audit.
6. **At scale (3 min).** Measured numbers, and what changes at 100 domains.
7. **What we learned (2 min).** Three lessons, not ten.

For the demo: record a fallback video, pre-create the users, and keep the grant window at
two minutes so the expiry happens on stage.

## Plan

### Before any talk or new post

1. M1: memory read and write as MCP tools with verified identity; remove body claims and the
   body-driven approvals.
2. G2 and G4: guard the state machine; use the verified org.
3. M3: read-only grants by default; tools out of the grant.
4. M6: apply the guardrail on write, or drop the claim.
5. B3: correct every claim in the table.

### To earn the title "sharing context at scale"

6. M4 and M10: provenance on results; reads in the audit trail.
7. M7: search across allowed namespaces.
8. Published facts and a brokered answer: at least one of the two.
9. An agent in the loop that uses shared memory in an answer.
10. G1 and G3: queries in place of scans; real expiry. Then measure.

### To make it a strong AWS-native design

11. C1: claims in the access token; the Cedar `forbid` active.
12. M2: scoped, short-lived credentials per grant.
13. M5 and M8: direct long-term records with metadata; hierarchical namespaces.
14. Verified Permissions for the grants, if the time condition checks out.
15. Tests for the DynamoDB store, the interceptor and the tools; a small live suite.

## Sources

AWS documentation and blogs found while checking the recommendations:

- [Organizing Agents' memory at scale: Namespace design patterns in AgentCore Memory](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)
- [Memory scoping with namespaces (AgentCore developer guide)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/session-actor-namespace.html)
- [Specify long-term memory organization with namespaces (AgentCore developer guide)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/specify-long-term-memory-organization.html)
- [Actions, resources, and condition keys for Amazon Bedrock AgentCore](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonbedrockagentcore.html)
- [Access AgentCore Memory through a gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-gateway-connector.html)
- [Help defend against memory poisoning in Amazon Bedrock AgentCore with multi-layer write validation](https://aws.amazon.com/blogs/security/help-defend-against-memory-poisoning-in-amazon-bedrock-agentcore-with-multi-layer-write-validation/)
- [Propagate user authorization context in AI agents with Amazon Bedrock AgentCore](https://aws.amazon.com/blogs/security/propagate-user-authorization-context-in-ai-agents-with-amazon-bedrock-agentcore/)
- [Pre token generation Lambda trigger (Amazon Cognito)](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-pre-token-generation.html)
- [How to customize access tokens in Amazon Cognito user pools](https://aws.amazon.com/blogs/security/how-to-customize-access-tokens-in-amazon-cognito-user-pools/)

I read search summaries of these pages, not every page in full. Two details need a check
against the current documentation before you build on them: the `namespacePath` condition key
for prefix matching, and which condition keys apply to the delete actions.
