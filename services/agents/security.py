
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityResult:
    suspicious: bool
    reasons: tuple[str, ...]


PATTERNS = {
    "instruction_override": re.compile(
        r"\b(ignore|disregard|override|forget)\b.{0,80}"
        r"\b(previous|prior|above|system|developer)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "role_impersonation": re.compile(
        r"\b(system prompt|developer message|system note|"
        r"administrator instruction)\b",
        re.IGNORECASE,
    ),
    "unauthorized_action": re.compile(
        r"\b(approve|execute|authorize|transfer all|"
        r"bypass approval|do not tell anyone)\b",
        re.IGNORECASE,
    ),
}


def inspect_supplier_email(email_text: str) -> SecurityResult:
    if not isinstance(email_text, str) or not email_text.strip():
        return SecurityResult(
            suspicious=True,
            reasons=("Supplier email is empty or invalid.",),
        )

    reasons = []

    for label, pattern in PATTERNS.items():
        if pattern.search(email_text):
            reasons.append(label)

    return SecurityResult(
        suspicious=bool(reasons),
        reasons=tuple(reasons),
    )
