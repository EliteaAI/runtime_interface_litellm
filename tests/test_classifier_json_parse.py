"""Classifier responses: the first JSON object is the contract (#6826).

Claude Haiku 4.5 answers with a fenced object followed by a prose rationale.
Every consumer must read the same single parse and ignore what follows.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routing.v7.candidate import FamilyClassifier
from routing.v7.catalog import compile_catalog
from routing.v7.routing import (CONFIGURED_CLASSIFIER, Classifier, decode_classifier_json,
                                parse_classifier_json, validate_descriptor)
from routing.v7.task_profile import apply_profile

OBJECT = {
    "operation": "design", "demand": "deep", "relation": "followup", "reference_ids": ["m1"],
    "needs_context": False,
    "reason": "Applying zero-trust security model to the existing Rust/Postgres/NATS architecture "
              "from m1 requires new threat analysis, identity/access controls, and failure-mode redesign.",
    "effort_need": "high", "task_family": "security_review",
    "work_profile": {"work": "design", "reasoning": "interacting_constraints", "evidence": "supplied",
                     "creativity": "constrained", "verification": "prove"},
}
PRETTY = json.dumps(OBJECT, indent=2)
HAIKU = "```json\n" + PRETTY + "\n```\n\n**Rationale:**\n\n- **operation=design/deep**: Zero-trust is not a transformation ..."
AVAILABILITY = {"input_status": "provided", "retrieval_source_ids": []}


def view():
    return {"latest": {"id": "m2", "text": "Now apply zero trust to that architecture"},
            "recent": [{"id": "m0", "text": "hi"}, {"id": "m1", "text": "architecture"}],
            "earlier_index": [], "allowed_reference_ids": ["m0", "m1"], "retrieval_options": []}


class Gateway:
    classifier_contract = {"model": "classifier-model", "effort": "low"}

    def __init__(self, content, finish_reason="stop"):
        self.content, self.finish_reason, self.calls = content, finish_reason, 0

    def complete(self, model, payload, **kwargs):
        self.calls += 1
        return {"message": {"content": self.content}, "finish_reason": self.finish_reason}


def classify(content):
    return Classifier(Gateway(content), CONFIGURED_CLASSIFIER).classify(view())


def test_haiku_fenced_object_with_trailing_rationale_is_accepted():
    assert parse_classifier_json(HAIKU) == OBJECT
    descriptor = validate_descriptor(parse_classifier_json(HAIKU), view())
    assert (descriptor["operation"], descriptor["demand"], descriptor["relation"]) == ("design", "deep", "followup")
    assert descriptor["reference_ids"] == ["m1"]
    descriptor, info = classify(HAIKU)
    assert info["schema_valid"] is True and "error" not in info
    assert info["parsed"] == OBJECT
    assert info["trailing_chars"] > 0
    assert descriptor["operation"] == "design" and descriptor["reference_ids"] == ["m1"]


def test_clean_object_has_no_trailing_chars():
    raw = json.dumps(OBJECT)
    assert parse_classifier_json(raw) == OBJECT
    descriptor, info = classify(raw)
    assert info["schema_valid"] is True and info["trailing_chars"] == 0
    assert descriptor["demand"] == "deep"


def test_closing_fence_only_still_parses_without_counting_the_fence():
    raw = "```json\n" + PRETTY + "\n```"
    assert decode_classifier_json(raw) == (OBJECT, 0)
    assert decode_classifier_json("```" + PRETTY + "```") == (OBJECT, 0)
    _, info = classify(raw)
    assert info["schema_valid"] is True and info["trailing_chars"] == 0


def test_bom_and_prose_before_the_object():
    assert parse_classifier_json("﻿" + PRETTY) == OBJECT
    assert parse_classifier_json("Here is the classification:\n" + PRETTY) == OBJECT
    _, info = classify("Here is the classification:\n" + PRETTY)
    assert info["schema_valid"] is True


def test_two_objects_first_one_wins_and_is_not_merged():
    second = {"operation": "other", "demand": "simple", "extra": 1}
    raw = PRETTY + "\n" + json.dumps(second)
    assert parse_classifier_json(raw) == OBJECT
    assert "extra" not in parse_classifier_json(raw)


@pytest.mark.parametrize("raw", ["", None, "no json here", "[1, 2]", '"text"', '{"operation": "design"',
                                 "```json\n```", "{not json}"])
def test_missing_non_object_or_truncated_response_is_a_value_error(raw):
    with pytest.raises(ValueError, match="Classifier response is not a JSON object"):
        parse_classifier_json(raw)


@pytest.mark.parametrize("raw", ['{"operation": "design"', "no json here", "[1, 2]"])
def test_classify_falls_back_to_unknown_on_genuine_failure(raw):
    descriptor, info = classify(raw)
    assert info["schema_valid"] is False and "not a JSON object" in info["error"]
    assert "parsed" not in info
    assert descriptor["operation"] == "other" and descriptor["demand"] == "deep"
    assert descriptor["relation"] == "ambiguous"


def test_schema_validation_still_rejects_bad_content_in_a_valid_object():
    descriptor, info = classify(json.dumps({**OBJECT, "demand": "enormous"}))
    assert info["schema_valid"] is False and info["error"] == "Invalid task classification"
    assert descriptor["operation"] == "other"


def test_family_classifier_end_to_end_with_haiku_response():
    # The production prompt also asks for the availability fields; the sample is completed with them.
    body = json.dumps({**OBJECT, **AVAILABILITY}, indent=2)
    content = "```json\n" + body + "\n```\n\n**Rationale:**\n\n- **operation=design/deep**: Zero-trust ..."
    gateway = Gateway(content)
    descriptor, info = FamilyClassifier(gateway, CONFIGURED_CLASSIFIER, compile_catalog()).classify(view())
    assert gateway.calls == 1
    assert info["schema_valid"] is True and "error" not in info and info["trailing_chars"] > 0
    assert descriptor["operation"] == "design" and descriptor["relation"] == "followup"
    assert descriptor["reference_ids"] == ["m1"]
    assert descriptor["task_family"] == "security_review"
    assert descriptor["effort_need"] == "high"
    assert descriptor["input_status"] == "provided" and descriptor["retrieval_source_ids"] == []
    expected = apply_profile({"demand": "deep"}, OBJECT["work_profile"])
    assert descriptor["work_profile"] == expected["work_profile"]
    assert descriptor["profile_demand_floor"] == expected["profile_demand_floor"] == "deep"
    json.dumps(info["parsed"])  # trace-safe


def test_family_classifier_failure_through_prose_only_is_unknown():
    descriptor, info = FamilyClassifier(Gateway("I cannot classify this."), CONFIGURED_CLASSIFIER,
                                        compile_catalog()).classify(view())
    assert info["schema_valid"] is False and descriptor["operation"] == "other"
