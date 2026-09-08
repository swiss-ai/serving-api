"""Deriving a display name from an address, for launchers the IdP never
named to us. The point of the exercise is that the result is never a
mailable address — see backend/services/identity_service.py."""

import pytest

from backend.services.identity_service import derive_display_name


@pytest.mark.parametrize(
    "email,expected",
    [
        # The institutional shape: first.last, hyphenated names kept intact.
        ("alice.smith@epfl.ch", "Alice Smith"),
        ("jean-pierre.dupont@epfl.ch", "Jean-Pierre Dupont"),
        ("alice_smith@ethz.ch", "Alice Smith"),
        # A trailing account number is part of the account, not the name.
        ("john.doe2@ethz.ch", "John Doe"),
        # ...but not when stripping it would leave a single letter: an opaque
        # account is better shown whole than as "S".
        ("s1234567@student.ethz.ch", "S1234567"),
        ("alice@epfl.ch", "Alice"),
        # Nothing to name, and nothing to leak either.
        ("", ""),
    ],
)
def test_derives_a_name_without_the_domain(email, expected):
    assert derive_display_name(email) == expected


@pytest.mark.parametrize(
    "email",
    [
        "alice.smith@epfl.ch",
        "s1234567@student.ethz.ch",
        "weird+tag@sub.domain.example",
    ],
)
def test_never_returns_something_mailable(email):
    """The domain is what makes an address reachable, and it is dropped
    unconditionally — that is the whole protection."""
    assert "@" not in derive_display_name(email)
