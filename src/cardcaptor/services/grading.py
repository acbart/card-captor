"""Grading engine."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

RULE_TYPES = ("exact", "case_insensitive", "multi_accept", "numeric", "mcq")

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class GradingRules:
    """Rules for grading an activity."""

    rule_type: str = "exact"
    accepted_answers: list[str] = field(default_factory=list)
    numeric_tolerance: Optional[float] = None
    normalization: list[str] = field(default_factory=list)
    point_value: float = 1.0
    #: Valid MCQ choices. These constrain OCR interpretation only - they are
    #: deliberately *not* the accepted answers, so OCR cannot be biased towards
    #: the correct option.
    choices: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type,
            "accepted_answers": list(self.accepted_answers),
            "numeric_tolerance": self.numeric_tolerance,
            "normalization": list(self.normalization),
            "point_value": self.point_value,
            "choices": list(self.choices),
        }


@dataclass
class GradingResult:
    score: float
    possible: float
    rule_used: str
    auto_graded: bool
    normalized_answer: str
    matched_answer: Optional[str] = None


def normalize_answer(answer: str, rules: list[str]) -> str:
    """Apply normalization rules to an answer string."""
    text = ("" if answer is None else str(answer)).strip()
    applied = list(rules or [])
    for rule in applied:
        key = rule.strip().lower()
        if key == "strip":
            text = text.strip()
        elif key in ("lower", "lowercase", "case_insensitive"):
            text = text.lower()
        elif key in ("upper", "uppercase"):
            text = text.upper()
        elif key in ("remove_spaces", "no_spaces"):
            text = re.sub(r"\s+", "", text)
        elif key in ("collapse_spaces", "squash_spaces"):
            text = _WHITESPACE_RE.sub(" ", text).strip()
        elif key in ("remove_punctuation", "strip_punctuation"):
            text = _PUNCTUATION_RE.sub("", text)
        elif key == "alphanumeric":
            text = re.sub(r"[^0-9A-Za-z]", "", text)
    return text


def _parse_number(text: str) -> Optional[float]:
    match = re.search(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?", (text or "").replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def grade_answer(raw_answer: Optional[str], grading_rules: GradingRules) -> GradingResult:
    """Grade a recognized answer against the activity's rules."""
    rules = grading_rules
    possible = float(rules.point_value)
    rule_type = (rules.rule_type or "exact").strip().lower()

    normalization = list(rules.normalization)
    if rule_type in ("case_insensitive", "mcq") and not any(
        r.lower() in ("lower", "lowercase", "case_insensitive") for r in normalization
    ):
        normalization = normalization + ["lower"]

    normalized = normalize_answer(raw_answer or "", normalization)

    if not normalized:
        return GradingResult(
            score=0.0,
            possible=possible,
            rule_used=rule_type,
            auto_graded=True,
            normalized_answer="",
            matched_answer=None,
        )

    accepted = [str(a) for a in rules.accepted_answers]

    if rule_type == "numeric":
        value = _parse_number(normalized)
        tolerance = rules.numeric_tolerance if rules.numeric_tolerance is not None else 0.0
        if value is None:
            return GradingResult(0.0, possible, rule_type, True, normalized, None)
        for candidate in accepted:
            target = _parse_number(candidate)
            if target is None:
                continue
            if abs(value - target) <= abs(tolerance) + 1e-9 or math.isclose(
                value, target, rel_tol=1e-9
            ):
                return GradingResult(possible, possible, rule_type, True, normalized, candidate)
        return GradingResult(0.0, possible, rule_type, True, normalized, None)

    if rule_type == "mcq":
        given = normalized[:1]
        for candidate in accepted:
            normalized_candidate = normalize_answer(candidate, normalization)[:1]
            if normalized_candidate and given == normalized_candidate:
                return GradingResult(possible, possible, rule_type, True, given, candidate)
        return GradingResult(0.0, possible, rule_type, True, given, None)

    # exact / case_insensitive / multi_accept share string comparison semantics.
    for candidate in accepted:
        normalized_candidate = normalize_answer(candidate, normalization)
        if normalized and normalized == normalized_candidate:
            return GradingResult(possible, possible, rule_type, True, normalized, candidate)

    return GradingResult(0.0, possible, rule_type, True, normalized, None)


def parse_grading_rules_from_dict(d: Optional[dict]) -> GradingRules:
    """Parse grading rules from the JSON dict stored on an Activity."""
    data = dict(d or {})
    rule_type = str(data.get("rule_type") or data.get("type") or "exact").strip().lower()
    if rule_type not in RULE_TYPES:
        rule_type = "exact"

    accepted = data.get("accepted_answers")
    if accepted is None:
        accepted = data.get("answers") or data.get("expected_answers") or []
    if isinstance(accepted, str):
        accepted = [accepted]
    accepted = [str(a) for a in accepted]

    tolerance = data.get("numeric_tolerance", data.get("tolerance"))
    try:
        tolerance = float(tolerance) if tolerance is not None else None
    except (TypeError, ValueError):
        tolerance = None

    normalization = data.get("normalization") or []
    if isinstance(normalization, str):
        normalization = [normalization]
    normalization = [str(n) for n in normalization]

    try:
        point_value = float(data.get("point_value", 1.0))
    except (TypeError, ValueError):
        point_value = 1.0

    choices = data.get("choices") or data.get("mcq_choices") or []
    if isinstance(choices, str):
        choices = [c.strip() for c in choices.split(",") if c.strip()]
    choices = [str(c) for c in choices]

    return GradingRules(
        rule_type=rule_type,
        accepted_answers=accepted,
        numeric_tolerance=tolerance,
        normalization=normalization,
        point_value=point_value,
        choices=choices,
    )


DEFAULT_MCQ_CHOICES = ["A", "B", "C", "D"]


def mcq_choices(rules: GradingRules) -> list[str]:
    """Valid MCQ letters for OCR constraints.

    Falls back to A-D plus any accepted answer letters, so the OCR character
    whitelist never collapses to just the correct answer.
    """
    if rules.choices:
        return [c.strip().upper()[:1] for c in rules.choices if c.strip()]
    letters = {c.strip().upper()[:1] for c in rules.accepted_answers if c.strip()}
    return sorted(set(DEFAULT_MCQ_CHOICES) | {c for c in letters if c.isalpha()})


def rules_for_activity(activity) -> GradingRules:
    """Build :class:`GradingRules` from an :class:`Activity` row."""
    import json

    try:
        raw_rules = json.loads(activity.grading_rules or "{}")
    except (TypeError, ValueError):
        raw_rules = {}
    if not isinstance(raw_rules, dict):
        raw_rules = {}

    try:
        expected = json.loads(activity.expected_answers or "[]")
    except (TypeError, ValueError):
        expected = []
    if isinstance(expected, str):
        expected = [expected]

    rules = parse_grading_rules_from_dict(raw_rules)
    if not rules.accepted_answers and expected:
        rules.accepted_answers = [str(a) for a in expected]
    if activity.point_value is not None:
        rules.point_value = float(activity.point_value)
    return rules
