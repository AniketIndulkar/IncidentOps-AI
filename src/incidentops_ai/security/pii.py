"""Finding and redacting PII and secrets in text, deterministically (AIENG-62).

Detectors for what has a shape: emails, phone numbers, card numbers (Luhn-checked), IBANs
(mod-97-checked), IPv4 addresses, AWS access keys, bearer tokens and JWTs, private keys, and
`password=`-style assignments. Each value becomes a numbered placeholder (`[EMAIL_1]`), the same
value always the same one, so the model can still tell two addresses apart. Names are not redacted:
in an incident tool, who is on call is the point. Presidio's NER would find names and addresses,
at the cost of a large model and probabilistic answers (docs/roadmap-w10.md).
"""

import re
from dataclasses import dataclass


def _luhn(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if double:
            n = n * 2 - 9 if n > 4 else n * 2
        total, double = total + n, not double
    return total % 10 == 0


def _card(match: re.Match) -> bool:
    digits = re.sub(r"\D", "", match.group())
    return 13 <= len(digits) <= 19 and _luhn(digits)


def _iban(match: re.Match) -> bool:
    raw = re.sub(r"\s", "", match.group()).upper()
    rearranged = raw[4:] + raw[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)
    return int(number) % 97 == 1


def _ipv4(match: re.Match) -> bool:
    return all(0 <= int(p) <= 255 for p in match.group().split("."))


# Order matters: longer and more specific shapes first, so a JWT is not read as an email.
DETECTORS: list[tuple[str, re.Pattern, object]] = [
    (
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        None,
    ),
    ("TOKEN", re.compile(r"\beyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}\b"), None),
    ("TOKEN", re.compile(r"(?i)\bbearer\s+[\w.~+/-]{20,}=*"), None),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    (
        "SECRET",
        re.compile(
            r"(?i)\b[\w.-]*(?:password|passwd|secret|api[_-]?key|token)\s*[=:]\s*[^\s,;\"']{4,}"
        ),
        None,
    ),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), None),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b"), _iban),
    ("CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), _card),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), _ipv4),
    # Only unambiguous shapes: a +country code, an area code in parentheses, or NNN-NNN-NNNN.
    # Bare digit groups are too often versions, counts or card-like numbers.
    ("PHONE", re.compile(r"(?<![\w+])\+\d{1,3}(?:[ .-]?\d{1,4}){2,5}(?![\w])"), None),
    ("PHONE", re.compile(r"(?<!\w)\(\d{3}\)\s?\d{3}[ .-]\d{4}(?!\w)"), None),
    ("PHONE", re.compile(r"\b\d{3}-\d{3}-\d{4}\b"), None),
]


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class Redaction:
    text: str
    findings: list[Finding]


def redact(text: str) -> Redaction:
    spans: list[tuple[int, int, str]] = []
    for kind, pattern, check in DETECTORS:
        for match in pattern.finditer(text):
            if check is not None and not check(match):
                continue
            start, end = match.span()
            if any(start < e and end > s for s, e, _ in spans):
                continue  # already covered by a more specific detector
            spans.append((start, end, kind))
    spans.sort()
    numbers: dict[tuple[str, str], int] = {}
    counts: dict[str, int] = {}
    out, last, findings = [], 0, []
    for start, end, kind in spans:
        value = text[start:end]
        if (kind, value) not in numbers:
            counts[kind] = counts.get(kind, 0) + 1
            numbers[(kind, value)] = counts[kind]
        out += [text[last:start], f"[{kind}_{numbers[(kind, value)]}]"]
        findings.append(Finding(kind, start, end))
        last = end
    out.append(text[last:])
    return Redaction("".join(out), findings)
