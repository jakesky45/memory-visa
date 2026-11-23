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
| 3. A memory demo | An agent in the loop, provenance, search across namespaces, a second way to share | Claude builds, Jake reviews and deploys | To do |
| 4. Scale and stage | Queries not scans, measured numbers, claims in the token, the blog and the talk | Both | After stage 3 |

### Stage 2: what was done

Run 23 November 2026, against the sandbox account (`<account-id>`, `eu-west-2`), using Jake's
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

**One new finding, found running A6: S12.** See below.

### S12. The Gateway's own response is not valid JSON

Found running A6 by hand: `jq` choked on the response, and so does Python's own `json.loads` -
this is not a shell-quoting problem, the bytes on the wire are not valid JSON.

AgentCore Gateway wraps a Lambda tool's result as a string, the `content[0].text` field of a
`tools/call` response. That needs the result's own quotes and backslashes escaped a second
time. The Gateway escapes the backslash but not the quote that follows it, so a result as
plain as `{"hello": "world"}` comes back malformed. Confirmed two ways, to rule out our own
code: invoking the Lambda directly, bypassing the Gateway, returns correctly-escaped JSON
every time; and swapping the Lambda's handler for one that returns a plain dict with no
`statusCode`/`body` wrapper reproduces the identical broken escaping. It is unconditional -
any object-shaped result trips it, since even a result with no user text at all still has
field names in quotes. `tools/list` is unaffected; nothing in that response is a nested,
stringified tool result.

This was not introduced by anything in `3db97ab` or `6725930`. It has been there since the
first of the eight tools went live - every `tools/list`/`tools/call` this project has called
"confirmed live" went through this same broken wrapping. Nobody had previously fed the raw
bytes to a strict parser.

**Recommendation:** this is AWS's bug, not ours - open a support case, with the two
reproductions above. Until it is fixed, a client either needs a tolerant parser (undo the
missing escape level) or must accept that this Gateway's `tools/call` responses cannot be
parsed as standard JSON-RPC. Say so plainly in the blog rather than show a clean transcript
that implies the real response can be parsed as sent (now done - see "The Gateway's Own Bug").

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

### Next to build

In this order. Each is one change with its own tests.

| # | Work | Finding | State |
|---|---|---|---|
| N1 | `memory_search` and `memory_write` as MCP tools behind the interceptor. The claims body and the body-driven approvals removed | M1 | **Done and deployed** (`3db97ab`), proved live in stage 2 |
| N3 | Apply the guardrail on write | M6 | **Done and deployed.** S2 and S3 fixed. The live write path works; not yet exercised with content that should trip a rule |
| N4 | Tests for the DynamoDB store, the tools Lambda, the claims builder and the Guardrail function | Tests, S10 | **Done.** 24 tests |
| N2 | Results carry provenance: record ID, namespace, author, time, score. Reads go to the audit table with the outcome and the grant ID | M4, M10, S9 | To do |
| N5 | Queries in place of table scans; `expires_at` stored; expiry emitted on time | G1, G3 | To do, after N4. Every `memory_search` now scans the grants table |
| N6 | An agent that calls a model and uses memory in a conversation | Scale | To do. `server.py` is kept as the place for it |
| N7 | Search across every namespace the caller may read | M7 | To do, after N2 |
| N8 | One more sharing pattern: a published fact, or a brokered answer. Project namespaces working live | Scale, S6 | To do |
| N9 | Shared facts written as long-term records with metadata; path namespaces | M5, M8 | To do, after A8 |
| N10 | Claims in the access token; the Cedar `forbid` made active | C1 | To do. Would also carry projects (S6) |

### The blog

The blog was updated in `3db97ab`. What changed, and what is still open:

| Blog | State |
|---|---|
| The Cognito row now says the access tokens carry no custom claims | Fixed. This was a contradiction in B3 |
| "For Memory, a grant is read-only" | Fixed, and correct |
| The memory section describes the tools and the deleted claims-body path | Correct, and a good story: say what was wrong and that it is gone |
| The `memory_write`, `memory_search` and `tools/list` transcripts | Labelled as expected output. Replace with real output after stage 2 |
| Jordan's `whoami` transcript shows Engineering's tools under his grant | True only for a request with `include_tools`. His `request_access` line in the post does not show it. Update both when the transcripts are re-run |
| "a log line records who asked" | Correct, and the caveat about the audit table is right |
| Nothing about AWS enforcing the boundary | Still missing. Add it once A8 passes: "the boundary and the expiry are enforced by IAM, not by our code" |
| "built on Cedar"; "GrantStore backing ordinary Cedar ABAC" | Still not accurate (B3, C1) |
| "five tools" in the older sections | Still there beside "eight tools". Use one number |

### The demo, in four beats

| Beat | What the audience sees | Ready? |
|---|---|---|
| 1. Short-term memory | Maya asks two questions in one conversation; the agent follows the thread | No. Needs N6 |
| 2. Long-term memory | A new conversation; the agent still knows what it learned | No. Needs N6 |
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
