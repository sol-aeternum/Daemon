"""Display-name compatibility must not weaken exact outbound endpoint pins."""

import pytest

from scripts.model_roster_live import provider_echo_matches


@pytest.mark.parametrize("reported", ["Google", "google-vertex", "google-vertex/europe"])
def test_google_catalog_display_name(reported: str) -> None:
    assert provider_echo_matches(reported, "google-vertex/europe")


@pytest.mark.parametrize("reported", ["google-vertex/us", "Google AI Studio", "Azure", ""])
def test_google_unrelated_or_different_subroute_rejected(reported: str) -> None:
    assert not provider_echo_matches(reported, "google-vertex/europe")


def test_google_alias_does_not_apply_to_other_provider() -> None:
    assert not provider_echo_matches("Google", "azure/eu")
