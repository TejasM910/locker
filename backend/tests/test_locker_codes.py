import pytest

from app.locker_codes import locker_code, location_code_prefix


def test_location_prefix_uses_name_and_resolves_collisions():
    used = set()

    assert location_code_prefix("DEMO ONLY - Church Street", used) == "CH"
    used.add("CH")
    assert location_code_prefix("Church Street Annex", used) == "CHU"
    assert location_code_prefix("Cubbon Park", used) == "CU"


def test_locker_codes_are_stable_and_validate_inputs():
    assert locker_code("CH", 101) == "CH101"
    with pytest.raises(ValueError):
        locker_code("Church Street", 101)
