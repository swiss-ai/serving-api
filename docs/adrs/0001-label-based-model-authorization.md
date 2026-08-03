# ADR-0001: Model authorization is a launch-time OpenTela label, resolved by the client

**Status**: Accepted (2026-08-03)

## Context

Every model served on the OpenTela mesh was visible to and usable by every user of the Serving API. We need per-user access control: a launcher must be able to restrict a model to themselves or to an explicit set of collaborators.

Two structural facts shape the solution:

- The gateway is **stateless with respect to launches**. Models appear by joining the mesh (SML wraps the framework process in OpenTela) and disappear when their SLURM job ends. The gateway has no launch registry, no model table, and no notion of who launched what — everything it knows about a model comes from the DNT table (peer entries with labels such as `launched_by`, `worker_group_id`, `expires_at`).
- User identity already exists on both sides: the Serving API maps API keys to `owner_email` (the `APIKey` table), and SML already holds the user's CSCS API key for health checks.

## Decision

### Authorization travels as a peer label, like every other launch-time fact

A new OpenTela peer label `authorization` carries the access policy. Its grammar:

- `public` — anyone, including anonymous `/v1/models` callers.
- `email1,email2,...` — only the listed users (normalized: stripped, lowercased; the gateway also compares case-insensitively as defense in depth).
- **Missing/empty label — public.** Every model launched before this feature keeps working and stays visible; no migration, no flag day.

The gateway derives a `model_id → [authorization values]` map from the same DNT table the models router already reads (including the `served_model_name` label fallback for pending/follower peers).

### Multi-entry semantics: one policy or refuse

A model id may be advertised by many peer entries. Each entry's label is normalized to a *policy* — public, or a set of lowercased emails — so reordered/re-cased lists, whitespace, and `public` vs a missing label all compare equal. Then:

- **All entries share one policy** → apply it. This covers every legitimate multi-entry case: replicas and followers of one launch, consecutive-chain handovers, and a same-name relaunch with an unchanged list.
- **Entries disagree** → the name is in **conflict** and the gateway refuses to route it for *everyone* (403 naming the conflict), until one side is relaunched under a unique name or with a matching label.

Deny-all on conflict is deliberate. OpenTela load-balances a model name across every peer advertising it, and the gateway cannot pin a request to a particular launch's replicas — so once two launches with different policies share a name, *any* routed request may land on a replica its caller never chose to trust. The alternatives are all worse: **union** (any entry grants) lets a same-named `public` launch silently widen access to a restricted model — an attacker-triggerable bypass; **intersection** still routes an all-lists-approved caller's prompts to the colliding launcher's replica, which could log them. A collision with differing policies is either a misconfiguration or an attack; both deserve a loud, attributable failure (the DNT records each entry's `launched_by`) rather than best-effort routing. The cost is a denial-of-service handle — a colliding launch can make a name unroutable — but a same-named peer already receives a share of the traffic and can garbage it, so that handle exists with or without authorization.

### "private" is resolved by SML, never seen by the gateway

`--authorization private` (the SML default) means "only the launcher" — but the gateway does not know who the launcher is. Rather than teach it, SML translates `private` into the launcher's own email **before submission** by calling the new `GET /v1/whoami` (bearer = the user's API key → `{"email": ...}`). The literal value `private` therefore never reaches the mesh, and the gateway's grammar stays two-valued (public / email list). A validator in SML rejects an unresolved `private` at `LaunchArgs` construction as a guardrail.

### Enforcement and filtering at the gateway

- Every inference proxy route checks the model's authorization after body parsing and before proxying; an unauthorized caller gets **403** in the OpenAI `permission_error` envelope. A model id unknown to the DNT falls through to the upstream (which 404s), unchanged.
- `/v1/models` and `/v1/models_detailed` take an **optional** bearer: absent → public entries only; valid API key → public + entries listing the caller; present-but-unknown key → 401 (fail loud, not a silently narrower list).
- Passthrough providers (CSCS L1, RCP) are always public — they are upstream-hosted and carry no labels.

### Failure policy: availability over strictness

The authorization map is cached module-level with a ~10s TTL (one fetch retry per TTL, so a down DNT cannot add its fetch timeout to every request). On fetch failure the stale map keeps serving. Only at **true cold start** — no successful fetch ever — does enforcement fail open, with a logged warning. A DNT blip must never 500 (or wrongly 403) inference traffic; if the DNT is down long enough for this to matter, OpenTela routing is typically down too.

## Consequences

- Authorization lives and dies with the peer: when the job ends, the policy disappears with the model. There is no ACL store to garbage-collect and no second source of truth.
- **Labels are self-asserted; the mesh is the trust boundary.** Anyone who can join the mesh can set any labels — including copying a restricted model's exact `authorization` list onto their own same-named peer, which the gateway cannot distinguish from a legitimate relaunch. Conflict detection therefore protects against *accidental* collisions and *policy-changing* squatting, not against a peer that impersonates the policy verbatim. Closing that hole needs mesh-level name ownership / authenticated labels in OpenTela, which is out of scope here. `sml preconfigured` mitigates by salting generated names (`<model>-<4-char salt>`); `sml advanced` users choosing explicit names should treat them as claims on a shared namespace.
- A conflicted name recovers by attrition: peers expire with their SLURM jobs, and the moment the surviving entries agree the model routes again — no gateway state to reset.
- A permission change requires a relaunch (labels are set at peer start). Acceptable for SLURM-scheduled models whose lifetime is hours.
- Identity is the API key's `owner_email`. Identity resolution (`get_email_for_token`) is cached ~5 min per process; key rotation evicts the rotated key from the cache so `/v1/whoami` and `/v1/models` stop honoring it immediately (other workers' entries age out within the TTL).
- The label — including the collaborator email list — is visible to whoever can see the model, which is exactly the set of people on the list (or everyone, for public models).
- An explicit email list does **not** auto-include the launcher. Spec-literal: `--authorization a@x.ch` means exactly that user, so a launcher can hand a model to someone else — or lock themselves out. SML could auto-append the launcher later without a gateway change.
- 403 (not 404) for unauthorized access deliberately trades a small information leak — the model id exists — for a debuggable error. Restricted ids contain a random launch salt, so the leak is minimal.

## Alternatives considered

1. **ACL table in the Serving API database.** Rejected: requires a launch-registration API, ties gateway state to SLURM job lifecycles it cannot observe, and creates a second source of truth next to the mesh. Labels already model "facts about a launch".

2. **Resolve `private` server-side.** The gateway would need to know the launcher, which means SML registering launches — the same registry we just rejected. Client-side resolution reuses two things that already exist: the whoami-able API key and the label channel.

3. **Fail closed when the DNT is unreachable.** Rejected: it converts an infrastructure blip into a full inference outage for public and restricted models alike. Fail-open is bounded to the never-fetched cold-start state and logged.

4. **Filter `/v1/models` client-side in the frontend.** Rejected: hiding is not access control, and anonymous visitors would receive restricted entries in the payload. The backend filters; the frontend only chooses which credential to send.

5. **Union / intersection / first-launch-wins on a name collision.** All rejected in favor of deny-all — see "Multi-entry semantics" above. Union widens access under attacker control; intersection and priority rules still route prompts to replicas the caller never trusted, because routing (OpenTela's) and policy (the gateway's) are decided in different places.

## Related

- Companion SML change: swiss-ai/model-launch#193 (`--authorization` flag, whoami client, label emission).
- Introduced in swiss-ai/serving-api#91.
- Label semantics and endpoint docs: README, "Model Authorization".
