"""Who launched a model, said in a way that cannot be spammed.

The model catalogue (/v1/models, /v1/models_detailed) is readable by
anonymous callers by design — the public model list has to render for a
logged-out visitor. Once SML started stamping ``launched_by_email`` and
``authorization`` on every peer, that turned every launcher's address, and
every collaborator on a restricted model, into a field of a public JSON
document — and, via the model card's "extra labels" block, into text on a
public web page. Harvesting the lot is one GET.

So the catalogue carries display NAMES, and the translation happens here,
on the server. Doing it in the frontend would be no protection at all:
rendering a name while the payload still contains the address leaves it in
the network tab, in ``curl``, and in every scraper — the DOM is not where
the disclosure happens.

Names come from ``apikey.owner_name``, recorded from the IdP's ``name``
claim whenever a user loads their profile. For an address with no such row
— someone who has only ever used the API, or a label naming a user who
never signed in to the web UI — we derive one from the local part
("jean-pierre.dupont@epfl.ch" -> "Jean-Pierre Dupont"). That is a guess at
a name, but never a routable address: the domain is what makes an email
mailable, and it is dropped.

Raw addresses stay available exactly where they are needed and already
access-controlled: /v1/model-access/<model> hands a launch's owner (or an
admin) the real allowlist to edit, and /v1/profile hands you your own.
"""

import logging
import re

from sqlmodel import Session, func, select

from backend.models.entities import APIKey

logger = logging.getLogger("backend")

# Local-part word boundaries. Hyphens are deliberately NOT here: they
# belong inside a name ("jean-pierre"), so they are title-cased in place.
_LOCAL_SEPARATORS = re.compile(r"[._+]+")
# A trailing disambiguator on an institutional address ("john.doe2") is
# part of the account, not of the person's name.
_TRAILING_DIGITS = re.compile(r"\d+$")
# ...but only where a name is left behind. Stripping the digits off an
# opaque account like "s1234567" leaves "s", which is worse than saying
# the account: keep the token whole unless the remainder still reads as a
# word.
_MIN_NAME_LETTERS = 2


def _titlecase(token: str) -> str:
    """Capitalize a name token, hyphenated parts included, without touching
    the rest of the casing — ``str.title()`` would turn "McKay" into
    "Mckay" and "O'Neil" into "O'Neil" only by luck."""
    return "-".join(
        part[:1].upper() + part[1:] if part else part for part in token.split("-")
    )


def _undisambiguated(token: str) -> str:
    """``token`` without a trailing account number, when what remains is
    still a word."""
    stripped = _TRAILING_DIGITS.sub("", token)
    if sum(char.isalpha() for char in stripped) >= _MIN_NAME_LETTERS:
        return stripped
    return token


def derive_display_name(email: str) -> str:
    """A best-effort human name for an address we have no record of.

    Drops the domain — an address without it cannot be mailed, which is the
    whole point — and reads the local part as dot/underscore-separated name
    parts. When there is nothing name-shaped left (an opaque account like
    "s1234567") the cleaned local part is returned as-is: still not an
    address, and better than showing nothing where a launcher belongs.
    """
    local = (email or "").split("@")[0].strip()
    if not local:
        return ""
    tokens = [
        _undisambiguated(token) for token in _LOCAL_SEPARATORS.split(local) if token
    ]
    named = [_titlecase(token) for token in tokens if token]
    if named:
        return " ".join(named)
    return _titlecase(local)


def display_names(engine, emails) -> dict[str, str]:
    """Map each address to the name to show for it, keyed by the address as
    given (callers hold labels, which are not normalized).

    One query for the whole batch: a listing response names one launcher per
    entry plus every collaborator on a restricted one, and resolving those
    one at a time would put a round-trip per model card on a page load.
    Uncached on purpose — it is a single indexed-scan on a small table, and
    a user who has just corrected their name in the IdP should see it on the
    next page load rather than after a TTL.

    Never raises on a database failure: this runs on the anonymous listing
    path, so it degrades to derived names rather than taking the public
    catalogue down with it.
    """
    wanted = {email: email.strip().lower() for email in emails if email}
    if not wanted:
        return {}

    recorded: dict[str, str] = {}
    try:
        with Session(engine) as session:
            rows = session.exec(
                select(APIKey.owner_email, APIKey.owner_name).where(
                    func.lower(APIKey.owner_email).in_(set(wanted.values()))
                )
            ).all()
    except Exception:
        # Resolving names is what first put the database on the path of an
        # ANONYMOUS request: /v1/models* rendered the public catalogue
        # without touching it at all before this. A database blip must
        # therefore cost the recorded names, not the page — every address
        # still gets a derived name below, which is a worse name but never
        # an address, so the disclosure this module exists to prevent holds
        # either way. Also covers engine=None (no database wired at all),
        # matching model_access_service.load_overrides.
        logger.warning(
            "Could not read recorded display names; deriving them from addresses",
            exc_info=True,
        )
        rows = []
    for owner_email, owner_name in rows:
        if owner_name and owner_name.strip():
            recorded[(owner_email or "").strip().lower()] = owner_name.strip()

    return {
        email: recorded.get(lowered) or derive_display_name(email)
        for email, lowered in wanted.items()
    }


def display_name(engine, email: str) -> str:
    """``display_names`` for a single address; "" for an empty one (a k8s
    model or a pre-feature launch has no owner to name)."""
    if not email:
        return ""
    return display_names(engine, [email]).get(email, "")
