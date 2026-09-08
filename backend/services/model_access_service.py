"""Post-launch access changes for a launched model.

An OpenTela peer's labels are stamped at process start and immutable for
the life of the SLURM job, which makes `--authorization` a decision the
launcher cannot revisit without taking the model down and putting it back
up. That is fine as a *starting* state and wrong as the only one, so this
module adds a mutable overlay on top of it:

- while an override row exists for a launch, it REPLACES that launch's
  `authorization` label as the policy the gateway enforces;
- deleting the row ("Reset") hands authority straight back to the label.

The label is never rewritten — it stays on the peer as the record of what
the model was launched as, which is what makes Reset meaningful rather than
just another edit.

Rows are keyed by `launch_id`, the UUID label SML stamps on each launch,
so an override belongs to one launch instance and not to a name. See
:class:`~backend.models.entities.ModelAccessOverride` for why that matters.

Who may edit: the launch's own owner (its `launched_by_email` label) or any
admin. A launch with no owner label — anything launched before SML emitted
one, and our k8s-hosted models — is admin-only by construction, since there
is no one to attribute it to.
"""

import logging
import re
from datetime import datetime

from sqlmodel import Session, select

from backend.models.entities import ModelAccessOverride
from backend.redis_cache import get_token_cache

logger = logging.getLogger("backend")

PUBLIC = "public"

# Deliberately loose, and identical to SML's launch-side check: the gateway
# compares these against an API key's owner_email verbatim, so the job here
# is to catch typos and paste accidents, not to adjudicate what a valid
# address is.
_EMAIL_RE = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")

# Safety net only. Every write invalidates the key, so this TTL is what
# bounds the damage of an invalidation that never landed (a Redis blip
# mid-write), not the normal propagation delay.
_CACHE_TTL_SECONDS = 30


class InvalidPolicyError(ValueError):
    """The requested policy isn't expressible as an authorization label."""


def normalize_policy_value(raw: str) -> str:
    """Canonicalize a requested policy into label grammar, or raise.

    "public" in any case stays "public"; anything else must be a
    comma-separated email list, which comes back stripped, lowercased and
    de-duplicated with order preserved. Canonicalizing on the way IN means
    an override and a label that mean the same thing compare equal, so
    setting an override to exactly what the label already said doesn't read
    as a conflict between replicas.

    Note there is no "private" here, unlike the SML flag: "private" is a
    launch-time shorthand SML resolves against whoami before submission,
    and by the time a model exists the concrete email is what we store.
    """
    text = (raw or "").strip()
    if not text:
        raise InvalidPolicyError(
            "Access policy must not be empty; use 'public' or a list of emails."
        )
    if text.lower() == PUBLIC:
        return PUBLIC

    emails: list[str] = []
    for part in text.split(","):
        email = part.strip().lower()
        if not email:
            continue
        if email == PUBLIC:
            raise InvalidPolicyError(
                "Mixing 'public' with an email list is ambiguous; "
                "use either 'public' or a comma-separated list of emails."
            )
        if not _EMAIL_RE.match(email):
            raise InvalidPolicyError(f"'{email}' is not an email address.")
        if email not in emails:
            emails.append(email)

    if not emails:
        raise InvalidPolicyError("Access policy lists no email addresses.")
    return ",".join(emails)


def load_overrides(engine) -> dict[str, str]:
    """Every active override, as launch_id → policy.

    All rows in one read rather than a per-model lookup: the table holds one
    row per model whose access someone changed, which is a small fraction of
    a small set, and the inference path needs the answer for whichever model
    a request names. Cached in Redis and invalidated on write, so the hot
    path normally touches neither Postgres nor a stale answer.

    Returns {} when no engine is wired (unit tests, fixture-mode dev) — no
    database means no overrides, which is the pre-feature behaviour.
    """
    if engine is None:
        return {}

    cache = get_token_cache()
    cached = cache.get_access_overrides()
    if cached is not None:
        return cached

    try:
        with Session(engine) as session:
            rows = session.exec(select(ModelAccessOverride)).all()
        overrides = {row.launch_id: row.policy for row in rows}
    except Exception:
        # A database blip must not take enforcement with it. Falling back to
        # "no overrides" means we enforce the launch labels, which is the
        # conservative reading: it can only ever revert to what the launcher
        # originally asked for, never to something more permissive than
        # either source said.
        logger.warning(
            "Could not read model access overrides; enforcing launch labels only",
            exc_info=True,
        )
        return {}

    cache.set_access_overrides(overrides, ttl=_CACHE_TTL_SECONDS)
    return overrides


def get_override(engine, launch_id: str) -> ModelAccessOverride | None:
    """The override row for one launch, straight from the DB.

    Uncached on purpose: this backs the management UI, where showing a
    stale value right after someone saved would be worse than a query.
    """
    if engine is None or not launch_id:
        return None
    with Session(engine) as session:
        return session.get(ModelAccessOverride, launch_id)


def set_overrides(engine, launches: list[dict], policy: str, updated_by: str) -> int:
    """Set one policy across every launch of a model, in ONE transaction.

    All-or-nothing on purpose. A model's launches must agree — two that
    disagree make the name unroutable for everyone (ADR-0001) — so a loop of
    independent commits that failed halfway would leave the model broken in
    exactly the way this feature is supposed to prevent. One transaction
    means a failure changes nothing.

    ``launches`` are dicts with launch_id / model_id / owner_email, as the
    router assembles them from the DNT. ``policy`` must already be canonical:
    callers go through normalize_policy_value so an invalid value is a 400
    long before it reaches here.
    """
    now = datetime.now()
    with Session(engine) as session:
        for launch in launches:
            launch_id = launch["launch_id"]
            row = session.get(ModelAccessOverride, launch_id)
            if row is None:
                row = ModelAccessOverride(
                    launch_id=launch_id,
                    model_id=launch["model_id"],
                    owner_email=launch["owner_email"],
                    policy=policy,
                    updated_by=updated_by,
                    created_at=now,
                    updated_at=now,
                )
            else:
                row.policy = policy
                row.updated_by = updated_by
                row.updated_at = now
                # Refresh the denormalised copies: harmless when unchanged,
                # and it keeps the audit trail honest if a label moved.
                row.model_id = launch["model_id"]
                row.owner_email = launch["owner_email"]
            session.add(row)
        session.commit()

    get_token_cache().clear_access_overrides()
    return len(launches)


def set_override(
    engine,
    launch_id: str,
    model_id: str,
    owner_email: str,
    policy: str,
    updated_by: str,
) -> None:
    """Single-launch form of :func:`set_overrides`."""
    set_overrides(
        engine,
        [
            {
                "launch_id": launch_id,
                "model_id": model_id,
                "owner_email": owner_email,
            }
        ],
        policy,
        updated_by,
    )


def clear_overrides(engine, launch_ids) -> int:
    """Reset every launch of a model at once, returning how many rows went.

    One transaction, for the same reason as set_overrides: a half-finished
    reset leaves the launches disagreeing, which is worse than not having
    started. Missing rows are skipped, so the Reset button is safe to press
    twice.
    """
    removed = 0
    with Session(engine) as session:
        for launch_id in launch_ids:
            if not launch_id:
                continue
            row = session.get(ModelAccessOverride, launch_id)
            if row is None:
                continue
            session.delete(row)
            removed += 1
        if removed:
            session.commit()

    if removed:
        get_token_cache().clear_access_overrides()
    return removed


def clear_override(engine, launch_id: str) -> bool:
    """Single-launch form of :func:`clear_overrides`.

    This is "Reset": with the row gone, the launch's own `authorization`
    label decides again.
    """
    return clear_overrides(engine, [launch_id]) > 0


def prune_dead_overrides(engine, live_launch_ids: set[str]) -> int:
    """Drop override rows whose launch is no longer on the mesh, returning
    how many went.

    Not required for correctness — a dead row is inert, because nothing can
    ever present its launch_id again — but the table would otherwise grow
    one row per model whose access was ever changed, forever.

    An EMPTY live set is treated as "we couldn't see the mesh", never as
    "nothing is running": `get_all_models` returns [] when the DNT read
    fails, and pruning on that would delete every override in the table
    during an outage, silently reverting every model to its launch label.
    The guard belongs here rather than in each caller, since getting it
    wrong is quiet and destructive.
    """
    if engine is None or not live_launch_ids:
        return 0
    with Session(engine) as session:
        rows = session.exec(select(ModelAccessOverride)).all()
        dead = [row for row in rows if row.launch_id not in live_launch_ids]
        for row in dead:
            session.delete(row)
        if dead:
            session.commit()

    if dead:
        get_token_cache().clear_access_overrides()
    return len(dead)


def may_edit(caller_email: str | None, owner_email: str, is_admin_caller: bool) -> bool:
    """Whether ``caller_email`` may change this launch's access.

    The owner or an admin. An ownerless launch (no `launched_by_email`
    label: pre-feature SML, or our k8s models) has nobody to attribute it
    to, so it is admin-only rather than open to the first caller.
    """
    if is_admin_caller:
        return True
    if not caller_email or not owner_email:
        return False
    return caller_email.strip().lower() == owner_email.strip().lower()
