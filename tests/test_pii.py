"""AIENG-62: the PII and secret detectors find what has a shape, and only that."""

import pytest

from incidentops_ai.security.pii import redact

pytestmark = pytest.mark.redteam


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Contact jane.doe+ops@example.co.uk for access.", "EMAIL"),
        ("Call +44 20 7946 0958 now", "PHONE"),
        ("call (415) 555-0132 after 5", "PHONE"),
        ("Card 4111 1111 1111 1111 was charged twice", "CARD"),
        ("IBAN GB82 WEST 1234 5698 7654 32 bounced", "IBAN"),
        ("from 203.0.113.45 every second", "IP"),
        ("key AKIAIOSFODNN7EXAMPLE leaked", "AWS_KEY"),
        ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLXZhbHVl in the log", "TOKEN"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "PRIVATE_KEY"),
        ("db_password=hunter2-prod in the config", "SECRET"),
    ],
)
def test_each_kind_is_found_and_replaced(text, kind):
    result = redact(text)

    assert f"[{kind}_1]" in result.text
    assert [f.kind for f in result.findings] == [kind]


@pytest.mark.parametrize(
    "text",
    [
        "Card 4111 1111 1111 1112 fails the Luhn check",  # not a card number
        "version v4.12.0 deployed at 10:00",
        "Orders API timing out after 30 seconds; p95 2000 ms",
        "IBAN GB00 WEST 1234 5698 7654 32 has a bad checksum",
        "Amara Okafor is on call",  # names are not redacted: they are the point
    ],
)
def test_lookalikes_are_left_alone(text):
    assert redact(text).text == text


def test_the_same_value_gets_the_same_placeholder():
    result = redact("Mail ops@example.com, then mail ops@example.com again, or dev@example.com.")

    assert result.text == "Mail [EMAIL_1], then mail [EMAIL_1] again, or [EMAIL_2]."
