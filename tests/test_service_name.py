"""The service-name rule lives in one place and every entry point agrees with it."""

import pytest
from pydantic import ValidationError

from incidentops_ai.extraction.models import ServiceRef
from incidentops_ai.incidents.models import IncidentCreate, check_service_name
from incidentops_ai.tools.recent_incidents import GetRecentIncidentsArgs

VALID = ["checkout-api", "a", "0-service", "x" * 63]
INVALID = ["", "Checkout-api", "-leading", "has space", "under_score", "x" * 64, "checkout\napi"]


def accepted(build) -> bool:
    try:
        build()
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize("name", VALID + INVALID)
def test_api_extraction_and_tool_accept_and_reject_the_same_names(name):
    verdicts = {
        "api": accepted(
            lambda: IncidentCreate(
                title="t", description="d", service=name, severity="SEV2", environment="production"
            )
        ),
        "extraction": accepted(lambda: ServiceRef(name=name, stated_explicitly=True)),
        "tool": accepted(
            lambda: GetRecentIncidentsArgs.model_validate(
                {"service": name, "limit": 1}, strict=True
            )
        ),
    }

    assert set(verdicts.values()) == {name in VALID}, verdicts


def test_extraction_may_leave_the_service_name_out():
    assert ServiceRef(name=None, stated_explicitly=False).name is None


@pytest.mark.parametrize("name", VALID)
def test_shared_check_returns_a_valid_name_unchanged(name):
    assert check_service_name(name) == name


@pytest.mark.parametrize("name", INVALID)
def test_shared_check_rejects_an_invalid_name(name):
    with pytest.raises(ValueError, match="kebab-case"):
        check_service_name(name)
