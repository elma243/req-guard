"""Requirement and test case IDs written in code, commits and pull requests (plain code, no AI: rule 4).

Matches REQ-004, REQ_4, req004 and the ID inside a Python name such as test_req_004_login, but not PREREQ-4 or a
bare "REQ-". The number is what counts: REQ-4 and REQ-0004 both name requirement 4.
"""

import re

REQ_ID = re.compile(r"(?<![A-Za-z0-9])REQ[-_]?0*(\d{1,6})(?!\d)", re.IGNORECASE)
TC_ID = re.compile(r"(?<![A-Za-z0-9])TC[-_]?0*(\d{1,4})(?!\d)", re.IGNORECASE)


def _numbers(pattern: re.Pattern, text: str | None) -> list[int]:
    found: list[int] = []
    for match in pattern.finditer(text or ""):
        number = int(match.group(1))
        if number > 0 and number not in found:
            found.append(number)
    return found


def find_req_numbers(text: str | None) -> list[int]:
    """Requirement numbers mentioned in the text, in order, each once."""
    return _numbers(REQ_ID, text)


def find_tc_numbers(text: str | None) -> list[int]:
    """Test case numbers (TC-3) mentioned in the text, in order, each once."""
    return _numbers(TC_ID, text)
