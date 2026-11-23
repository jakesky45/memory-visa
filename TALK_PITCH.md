# Talk pitch: AWS Summit submission

Draft for submission. Matches the abstract format AWS's own catalog uses (title, level,
one paragraph, features) so it can be pasted into a CFP form with minimal editing. The
session code in a real catalog entry is assigned by AWS, not the speaker - left blank here.

Built from the thesis already decided in `REVIEW.md` (D4): *isolated by default, shareable
on purpose.* Not final - see "Open items" at the end before submitting.

---

## Title (primary)

**Isolated by Default, Shareable on Purpose: Cross-Domain Memory Grants on Bedrock AgentCore**

### Alternates

- The Exception Path: Time-Boxed, Human-Approved Memory Sharing on Bedrock AgentCore
- Sharing Agent Memory at Scale: Five Patterns, and the One That Needs a Human

## Level

**300 - Advanced.** The mechanism (STS credentials narrowed by a session policy, Cedar
policy, Cognito claims) is expert-adjacent, but the talk is a narrative with a live demo, not a hands-on
build. 400 is the alternative if the content committee wants more IAM-policy detail on
screen and less story.

## Session type

**Breakout session**, lecture-style with two live demos (see `REVIEW.md`'s "The Summit
talk" for the full 30-minute shape). Not a chalk talk - the two demos, including watching
AWS itself refuse a request live, need the room's attention on one screen, not a
discussion format. Not a hands-on lab - there's one worked example, not an exercise
attendees build themselves.

## Abstract

> Static isolation keeps every team's AI agent memory apart - exactly as it should, until
> someone needs a legitimate, time-boxed exception. In this session, see a real
> cross-domain grant workflow on Amazon Bedrock AgentCore: a requester asks, the domain
> owner approves from their own AI client with no bespoke UI, and AWS itself - not
> application code - enforces the boundary and the expiry, through short-lived STS
> credentials with a session policy for one namespace and one deadline. Watch the grant work, then watch
> AWS refuse the same read the instant the window closes. Along the way: where Cedar's
> policy engine helps today and where it doesn't yet, why LLM-extracted memory is the
> wrong store for a fact someone needs to trust, and two production bugs - a policy that
> can never fire, a Guardrail that was never actually called - that honest testing caught
> before an audience would have. Leave with a framework of five patterns for sharing
> agent memory - scoped by default, shared by design, published facts, brokered answers,
> and the visa as the exception path - and the reference architecture for the one pattern
> most teams haven't built yet: the human-approved crossing.

(~175 words; trim toward 150 if the CFP has a hard limit.)

## Three takeaways (if the form asks for them separately)

1. A framework of five patterns for sharing agent memory across teams (three shown live:
   scoped by default, shared by design, the visa; two described), with the visa positioned as
   the exception path, not the default - and a concrete reason why: AgentCore's Dogwood
   temporal policies are scoped to one caller's session history, which can't model a
   two-person approval.
2. A working pattern for the approval step itself: real MCP tools the domain owner calls
   from whatever AI client they already use, not a bespoke UI or a Slack app needing admin
   rights nobody wants to request.
3. What "AWS enforces it, not your code" actually looks like end to end: an STS session
   policy scoped to one namespace and one `aws:CurrentTime` deadline, and the exact
   `AccessDeniedException` that proves it on stage.

## Why this isn't redundant with the rest of the catalog

See `REVIEW.md`'s "Related sessions already in an AWS Summit catalog" for the full
comparison. Short version, in case a reviewer asks: the closest session (SEC429, tenant
isolation at scale via one ABAC policy and STS session tags) solves keeping parties apart
cheaply at scale. This talk solves the one time a day two already-isolated parties need a
legitimate, time-boxed exception - with a human in the loop, which nothing else in the
catalog checked so far has.

## Open items before this is submittable

- **D8** (`REVIEW.md`, "Your decisions"): when the Summit is and how long the slot is -
  still unanswered, and it decides whether this is realistic as a 30-minute breakout or
  needs cutting to a shorter slot.
- **Stage 3**: the agent for demo beats 1 and 2 (short- and long-term memory in a real
  conversation) is written and tested offline, and not yet deployed or run against a real
  model (`REVIEW.md`, "Stage 3: the agent"). The abstract above only promises what demo
  beats 3 and 4 already prove live - it doesn't oversell beats 1 and 2.
- **The five patterns**: the session tags on the visa are for the audit trail only; the
  scoping is the session policy. Published facts and brokered answers are not built. The
  abstract calls the five a framework for that reason. Published facts (N8) is written
  and tested offline; once it is proven live, the talk can show four of the five.
- **D6**: three facts named in the talk outline (the name "Dogwood", an AWS security blog's
  publish date, the AgentCore/Bedrock Agents GA dates) still need a last check against
  live AWS documentation immediately before the talk, not just at review time.
- The real event name/catalog this was checked against (23 November 2026) is unconfirmed -
  verify it before citing SEC429/TNC317/SVS336/COM341 by code on stage or in a submission.
