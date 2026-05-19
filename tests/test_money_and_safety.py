import pytest

from ledger.money import to_cents
from ledger.sync import validate_account_is_safe


def test_to_cents_is_decimal_safe():
    assert to_cents("12.345") == 1235
    assert to_cents("-0.10") == -10
    assert to_cents("1,234.56") == 123456


def test_anz_classic_connection_is_rejected():
    account = {
        "_id": "acc_1",
        "name": "Everyday",
        "connection": {"name": "ANZ", "connection_type": "classic"},
    }
    with pytest.raises(ValueError, match="not official"):
        validate_account_is_safe(account, ["ANZ"])


def test_anz_official_connection_is_allowed():
    account = {
        "_id": "acc_1",
        "name": "Everyday",
        "connection": {"name": "ANZ", "connection_type": "official"},
    }
    validate_account_is_safe(account, ["ANZ"])

