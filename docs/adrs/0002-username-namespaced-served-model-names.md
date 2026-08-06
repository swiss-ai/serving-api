# ADR-0002: Served model names are namespaced by the launching cluster username

**Status**: Accepted (2026-08-06)

## Context

Every model on the OpenTela mesh is addressed by its served name, and that name is a flat, first-come string. Nothing binds a name to the person who launched it, which has two consequences:

- **Collisions are ordinary, not exceptional.** Two users launching the same catalogue model naturally pick the same name. OpenTela then treats both jobs as replicas of one model and load-balances across them, so a request for "the" model may land on either user's job — different weights, different sampling config, different lifetime. The failure is silent: nothing in `/v1/models` shows that two unrelated launches are sharing an id.
- **The mitigation was a random salt.** `sml preconfigured` appended a 4-character salt (`swiss-ai/Apertus-70B-a1b2`) to make clashes unlikely. It works, but the name is then unguessable: you cannot script against your own model, share it in a doc, or recognise it in `/v1/models` without copying it out of the launch output each time. `sml advanced` users, who name their models by hand, got no help at all — the usual workaround was to hand-append `$(whoami)` to the name.

Meanwhile the mesh already carries the fact we want: every SML job emits a `launched_by` label set from `$USER`, the cluster account the SLURM job runs as.

## Decision

### The served name is `<username>/<vendor>/<model>`

SML namespaces every served name under the cluster username that submits the job — `alice/swiss-ai/Apertus-70B`. The random salt is dropped: identity, not entropy, is what separates two users' launches.

The rule is applied in one place (`swiss_ai_model_launch.launchers.served_name`) and covers every launch path — `preconfigured`, `advanced`, loadtest, and the MCP server:

- An unnamespaced name (`Apertus-70B`, `swiss-ai/Apertus-70B`) gets the username prepended, so **existing launch scripts keep working unedited**.
- A name already namespaced under the launcher's own username passes through untouched (re-running a rendered script is idempotent).
- A name under someone *else's* username is rejected at submission, before the job is created.

In `sml advanced` the rewrite is applied to the `--served-model-name` inside `--framework-args` as well as to the `LaunchArgs` field. The framework process is what actually advertises the id to OpenTela; rewriting only the label would put an un-namespaced id on the mesh while the labels claimed a namespaced one.

Two launches by the *same* user of the same model do still share a name. That is deliberate: they are that user's own jobs, so OpenTela load-balancing across them is exactly what someone relaunching to add capacity wants.

### The gateway cross-checks the namespace against `launched_by`

A peer serving `alice/swiss-ai/X` from a job that ran as `bob` is publishing under a namespace that is not its own. The gateway refuses it (`backend/services/namespace_service.py`):

- **Listing** — `/v1/models` and `/v1/models_detailed` drop that peer's entry. It is never advertised.
- **Routing** — `ensure_namespace_ok` returns 403 for the id if *any* peer serving it fails the check. Refusing only the squatting peer is not possible: OpenTela balances a name across every peer advertising it, so the gateway cannot keep a request off the squatter.

The map of model id → `launched_by` is derived from the same DNT table the model list is built from, cached with a ~10s TTL (one fetch retry per TTL while the DNT is down). On a fetch failure the last good map is kept; only at true cold start, having never fetched successfully, does the check fail open — logged, because taking the gateway down with the DNT is worse than not enforcing a naming convention.

The check is deliberately lenient where there is nothing to compare:

- Ids with fewer than three segments (`swiss-ai/Apertus-70B`) predate namespacing and carry no username — unchecked, so every pre-feature launch and every passthrough provider id keeps working.
- Peers advertising no `launched_by` at all (OpenTela <v0.0.6 emits no labels) — unchecked, matching how the rest of the service treats missing labels as back-compatible rather than suspicious.

Comparison is case-insensitive and whitespace-tolerant on both sides.

## Consequences

- **A model's name now says who is responsible for it.** `alice/swiss-ai/Apertus-70B` is readable, guessable from user + model, stable across relaunches, and scriptable — the salt's job done by a meaningful token.
- **Ordinary cross-user collisions disappear**, and with them the silent cross-user load-balancing that motivated this ADR.
- **This is a consistency check, not an authentication boundary.** Both the served name and `launched_by` are self-asserted by whoever joins the mesh; a peer that sets `launched_by=alice` *and* serves `alice/...` passes. It catches accidental squats, stale hand-written scripts, and namespace-changing mistakes — not a peer that impersonates an identity wholesale. Closing that needs authenticated labels / mesh-level name ownership in OpenTela, which is out of scope here.
- **A squatted id can still be listed while being unroutable.** The squatter's entry is filtered out of listing, but the legitimate peer's entry remains, and the id 403s. The refusal is logged with the offending `launched_by` values, and the 403 detail names the namespace — an operator can find the job. Filtering the legitimate peer too would hide a model from its rightful owner because someone else misbehaved.
- **The gateway's leniency toward 2-segment names is permanent, not a migration window.** Passthrough provider ids (`swiss-ai/Apertus-8B-Instruct-2509`) are 2-segment by nature and are never namespaced.
- Frontends that split a model id to find its vendor must take the *second* segment for a namespaced name (`getModelVendor` in `frontend/src/lib/modelLogos.ts`).
- Names are longer, and the username — a piece of identity — is now visible to everyone who can see the model. `launched_by` already exposed it on the same entries.
- Every inference proxy route pays one dictionary lookup, plus one DNT fetch per TTL per worker.

## Alternatives considered

1. **Keep the salt and add the username** (`alice/swiss-ai/Apertus-70B-a1b2`). Rejected: the salt's only remaining job — separating one user's two launches — is better served by letting them share a name and load-balance. Keeping it would preserve exactly the unguessability the namespace is meant to remove.

2. **Namespace by email instead of cluster username.** Rejected: the mesh has no email; the SLURM job knows `$USER` and nothing else. Emails would have to be resolved at submission and could not be cross-checked against any label the job emits.

3. **Build the name in bash from `$USER` at job start**, guaranteeing it matches the `launched_by` label byte for byte. Rejected: SML must know the served name at submission time — it is what the CLI prints, what health checks poll, what the MCP tool returns, and what the telemetry labels carry. Resolving the username in Python (`Launcher.username`, the FirecREST/SLURM account) and letting the gateway verify it against the label gets the same guarantee without a name that only exists after the job starts.

4. **No gateway check — rename only.** Rejected: a namespace nobody verifies is decoration. The check is cheap — one DNT-derived map per TTL — and turns a silent cross-user collision into a visible, attributable error.

5. **Refuse to list the legitimate peer as well when an id is squatted.** Rejected: it lets any user erase another user's model from the catalogue by launching one mislabelled peer. Refusing to *route* is already the loud failure; erasing the row as well only removes the evidence.

## Related

- Companion SML change: `swiss_ai_model_launch.launchers.served_name` + the `username` argument threaded through `build_launch_args_from_advanced` and both launchers.
- ADR-0001 (label-based model authorization) is developed on a separate branch and lands independently; when both are in, `namespace_service` and `authorization_service` share the same DNT-map-per-TTL shape and should be collapsed into one fetch.
