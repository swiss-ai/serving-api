# ADR-0002: Access can be changed after launch, via a launch-scoped override table

**Status**: Accepted (2026-09-05)

**Amends**: [ADR-0001](0001-label-based-model-authorization.md), specifically its consequence *"A permission change requires a relaunch (labels are set at peer start)."*

## Context

ADR-0001 made a model's audience a launch-time OpenTela label. That was the right call for getting the policy onto the mesh without a launch registry, and it stays the mechanism by which a model arrives with an audience. But it left one property that turned out to be the wrong half of the trade: **an OpenTela label is stamped at peer start and immutable for the life of the SLURM job.** Getting `--authorization` wrong, or simply changing your mind, means cancelling a job that may be hours into serving and launching it again.

ADR-0001 called that "acceptable for SLURM-scheduled models whose lifetime is hours". In practice the common asks are exactly the ones a label cannot absorb: *I launched it private and now want to share it with a collaborator*, and *I launched it public and shouldn't have.* Neither is a relaunch-shaped problem.

ADR-0001 also rejected an "ACL table in the Serving API database", on three grounds: it would need a launch-registration API, it would tie gateway state to SLURM lifecycles the gateway cannot observe, and it would create a second source of truth. Those objections are what this ADR has to answer, not ignore.

## Decision

### Labels set the initial policy; an override table can change it

A model's audience now has two layers:

1. The `authorization` label, exactly as in ADR-0001 — the policy the model **launched with**.
2. An optional row in `model_access_override`, which while present **replaces** that label as the policy the gateway enforces.

Deleting the row ("Reset") returns authority to the label. The label is never rewritten by an override, which is what makes Reset meaningful: the launch-time intent stays on the mesh as the thing to fall back to, and remains visible in the DNT for anyone auditing what a model was started as.

Replacement rather than intersection: an override may widen access as well as narrow it. Narrow-only would have been the safer rule but cannot express "I launched private and want to share", which is half the motivation.

### The override is keyed by launch, not by model name

SML now stamps two more labels on every launch:

- `launch_id` — a UUID identifying **this launch**, generated per `LaunchArgs`.
- `launched_by_email` — the launcher's platform identity, resolved via `/v1/whoami`.

Override rows are keyed by `launch_id`. This is what answers ADR-0001's objections:

- **No launch-registration API.** The identity the table keys on is minted by SML and published through the same label channel as everything else. The gateway learns it by reading the DNT, exactly as it learns the policy.
- **No lifecycle to observe.** A row whose launch is gone is *inert*, not stale: nothing can ever present that `launch_id` again, so a dead row cannot attach to anything. Pruning (`prune_dead_overrides`) is housekeeping, not correctness.
- **No second source of truth about a launch.** The table says nothing about which models exist, who launched them, or when they end — the mesh remains the only answer to all three. It stores one fact the mesh structurally cannot: a decision made *after* the labels were fixed.

Keying by name instead would have re-created the problem ADR-0001 worried about. Names are a shared namespace anyone can relaunch into, so a name-keyed override could outlive its job and silently apply to a stranger's later launch of the same name — turning an access-control feature into an access-control hole.

The cost is that an override does not survive a relaunch: a new job draws a new UUID and starts at its own label. That is the conservative direction to fail in, and for a model whose audience matters the launch flag is the right place to say so anyway.

### Who may change it

The launch's own `launched_by_email`, or any admin.

This needs the new label because the existing `launched_by` is `$USER` — a **cluster shell account** (`bdoan`, `dmelikidze`), which cannot be compared against an API key's `owner_email`. Before this, the gateway genuinely could not tell which platform user launched a model.

`launched_by_email` resolution is deliberately **best-effort** at launch, unlike `private` resolution. There, the email *is* the policy, so failing to resolve it must fail the launch. Here it only decides who may edit the policy later, and failing a public launch because the Serving API blipped would be the worse trade. A launch with no owner label — that blip, pre-feature SML, or our k8s-hosted models — is admin-managed rather than open to the first caller.

### Conflicts are computed on the effective policy

ADR-0001's deny-all rule for a name served by launches that disagree now compares **effective** policies, not labels. Two launches under one name can share a label and diverge because only one was overridden; the routing problem is identical, so the rule has to see it. Correspondingly, the `PUT` endpoint applies a change to **every** launch serving the name and refuses unless the caller may edit all of them — a partial write would otherwise be a way to make your own model unroutable.

### Endpoints

`GET|PUT|DELETE /v1/model-access/{model_id}`, authenticated by either a serving API key or an IdP token (like the admin endpoints). `GET` returns the effective policy, the launch-time label, whether it is overridden, and whether the caller may edit — enough for the UI to render the menu and offer Reset only when there is something to reset. `GET` is gated on the caller already being able to *see* the model, since the policy names the people on the allowlist.

## Consequences

- **Access is no longer frozen at launch.** The label is the starting state; the override is the current state.
- **A permission change now takes effect in seconds, without touching the job.** Overrides are read through Redis and invalidated on write, so a change lands on the next request across all replicas — and costs the inference path no database round-trip.
- **An override cannot outlive its job.** This is a deliberate limit, not an oversight; see the keying argument above.
- **Two places can now set a model's audience.** `GET` returns both layers precisely so this stays legible rather than mysterious, and the UI says "changed after launch — it was launched as X".
- **A database failure degrades to the labels**, which can only ever revert a model to what its launcher originally asked for — never to something more permissive than either layer said.
- **The cold-start fail-open covers overrides too.** ADR-0001 fails open when no replica has ever fetched the DNT; overrides cannot narrow that window, because they key on `launch_id` and without the DNT there is no way to learn which launches serve a requested id. An overridden model is therefore as open as a label-restricted one during that window — the same bounded, logged trade, not a new one.
- **Models launched by older SML cannot be managed here** (no `launch_id` to attach a row to). They keep working under their labels, and the UI says why rather than offering a button that would fail.

## Alternatives considered

1. **Make OpenTela labels mutable.** The clean fix, and out of our hands — it needs a label-update path in OpenTela plus a way to authenticate who may call it. Worth revisiting if that ever lands; this table would then become a cache of it.
2. **Narrow-only overrides** (intersect with the label, never widen). Safer against a mistaken or hijacked override, but cannot express the "launched private, want to share" case that motivates the feature. Rejected on utility.
3. **Key overrides by model name.** Simpler, survives relaunches — and lets a dead override apply to a stranger's later launch of the same name. Rejected on safety.
4. **Derive ownership from the `authorization` label** (the email list already names the launcher), avoiding a new label. Rejected: a `public` launch carries no email at all, so nobody could ever restrict a public model — the main case.
5. **Admin-only management.** Would have shipped sooner and needed no SML change, but leaves users unable to manage their own models, which is the point.
6. **Rewrite the label instead of storing an overlay.** Not possible (see 1), and it would also destroy the launch-time intent that makes Reset meaningful.

## Related

- Supersedes one consequence of [ADR-0001](0001-label-based-model-authorization.md); the label mechanism itself is unchanged.
- Companion SML change: `launch_id` + `launched_by_email` labels, and best-effort whoami resolution on every launch path.
