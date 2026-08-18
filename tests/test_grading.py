"""Tests for the grading engine."""

from __future__ import annotations

import pytest

from cardcaptor.services.grading import (
    GradingRules,
    grade_answer,
    mcq_choices,
    normalize_answer,
    parse_grading_rules_from_dict,
)


def test_exact_match_passes():
    rules = GradingRules(rule_type="exact", accepted_answers=["Paris"], point_value=2.0)
    result = grade_answer("Paris", rules)
    assert result.score == 2.0
    assert result.possible == 2.0
    assert result.matched_answer == "Paris"
    assert result.auto_graded is True


def test_exact_match_is_case_sensitive():
    rules = GradingRules(rule_type="exact", accepted_answers=["Paris"])
    assert grade_answer("paris", rules).score == 0.0
    assert grade_answer("London", rules).score == 0.0


def test_case_insensitive_match():
    rules = GradingRules(rule_type="case_insensitive", accepted_answers=["Paris"])
    assert grade_answer("PARIS", rules).score == 1.0
    assert grade_answer("  paris  ", rules).score == 1.0
    assert grade_answer("Lyon", rules).score == 0.0


def test_multi_accept_matches_any_value():
    rules = GradingRules(
        rule_type="multi_accept",
        accepted_answers=["H2O", "water"],
        normalization=["strip", "lower"],
    )
    assert grade_answer("h2o", rules).matched_answer == "H2O"
    assert grade_answer("Water", rules).matched_answer == "water"
    assert grade_answer("oxygen", rules).score == 0.0


def test_numeric_within_tolerance():
    rules = GradingRules(
        rule_type="numeric", accepted_answers=["3.14"], numeric_tolerance=0.01, point_value=1.0
    )
    assert grade_answer("3.145", rules).score == 1.0
    assert grade_answer("3.13", rules).score == 1.0


def test_numeric_outside_tolerance():
    rules = GradingRules(rule_type="numeric", accepted_answers=["3.14"], numeric_tolerance=0.01)
    assert grade_answer("3.2", rules).score == 0.0
    assert grade_answer("not a number", rules).score == 0.0


def test_numeric_extracts_value_from_noisy_ocr():
    rules = GradingRules(rule_type="numeric", accepted_answers=["42"], numeric_tolerance=0.0)
    assert grade_answer(" 42 ", rules).score == 1.0


@pytest.mark.parametrize("given,expected", [("A", 0.0), ("B", 1.0), ("b", 1.0), ("C", 0.0), ("D", 0.0)])
def test_mcq_matching(given, expected):
    rules = GradingRules(rule_type="mcq", accepted_answers=["B"])
    assert grade_answer(given, rules).score == expected


def test_mcq_ignores_trailing_noise():
    rules = GradingRules(rule_type="mcq", accepted_answers=["C"])
    assert grade_answer("C.", rules).score == 1.0


def test_empty_answer_scores_zero():
    rules = GradingRules(rule_type="mcq", accepted_answers=["B"], point_value=3.0)
    for value in ("", "   ", None):
        result = grade_answer(value, rules)
        assert result.score == 0.0
        assert result.possible == 3.0
        assert result.matched_answer is None


def test_normalization_rules():
    assert normalize_answer("  Hello World  ", ["strip"]) == "Hello World"
    assert normalize_answer("Hello", ["lower"]) == "hello"
    assert normalize_answer(" H 2 O ", ["strip", "remove_spaces"]) == "H2O"
    assert normalize_answer("a,b.c!", ["remove_punctuation"]) == "abc"
    assert normalize_answer("A  B", ["collapse_spaces"]) == "A B"


def test_normalization_applied_during_grading():
    rules = GradingRules(
        rule_type="exact", accepted_answers=["h2o"], normalization=["strip", "lower", "remove_spaces"]
    )
    assert grade_answer("  H 2 O ", rules).score == 1.0


def test_parse_grading_rules_from_dict():
    rules = parse_grading_rules_from_dict(
        {
            "rule_type": "numeric",
            "accepted_answers": ["10"],
            "numeric_tolerance": "0.5",
            "normalization": "strip",
            "point_value": "2",
        }
    )
    assert rules.rule_type == "numeric"
    assert rules.accepted_answers == ["10"]
    assert rules.numeric_tolerance == 0.5
    assert rules.normalization == ["strip"]
    assert rules.point_value == 2.0


def test_parse_grading_rules_defaults_to_exact():
    rules = parse_grading_rules_from_dict({"rule_type": "nonsense"})
    assert rules.rule_type == "exact"
    assert rules.accepted_answers == []
    assert rules.point_value == 1.0


def test_mcq_choices_defaults_do_not_leak_the_answer() -> None:
    rules = parse_grading_rules_from_dict(
        {"rule_type": "mcq", "accepted_answers": ["B"]}
    )
    assert mcq_choices(rules) == ["A", "B", "C", "D"]


def test_mcq_choices_uses_explicit_choices() -> None:
    rules = parse_grading_rules_from_dict(
        {"rule_type": "mcq", "accepted_answers": ["e"], "choices": "A,B,C,D,E"}
    )
    assert mcq_choices(rules) == ["A", "B", "C", "D", "E"]
