"""Every M1 requirement must be covered by at least one test (or be marked manual)."""
import re
from pathlib import Path

from tests.conftest import SPECS

# Requirements verified by integration/manual checks until a DB test environment exists.
INTEGRATION_ONLY = {
    "REQ-BOOK-001", "REQ-BOOK-002", "REQ-BOOK-003", "REQ-BOOK-005", "REQ-LOCK-001",
    "REQ-LOCK-006", "REQ-SEC-001", "REQ-OPS-001", "REQ-NFR-001",
}


def test_every_m1_requirement_has_a_test():
    text = (SPECS / "requirements.md").read_text()
    m1 = set(re.findall(r"\*\*(REQ-[A-Z]+-\d+)\*\* \(M1\)", text))
    tests = "\n".join(p.read_text() for p in Path(__file__).parent.glob("test_*.py"))
    covered = set(re.findall(r'req\("(REQ-[A-Z]+-\d+)"\)', tests))
    uncovered = m1 - covered - INTEGRATION_ONLY
    assert not uncovered, f"M1 requirements without tests: {sorted(uncovered)}"
