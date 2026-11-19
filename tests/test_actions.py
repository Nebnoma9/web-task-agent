from __future__ import annotations

import pytest

from wta.actions import (
    ActionParseError,
    Click,
    Done,
    Fill,
    Press,
    WaitFor,
    action_json_schema,
    parse_action,
)


def test_parse_envelope_with_thought() -> None:
    action, thought = parse_action(
        '{"thought": "search box is e3", "action": {"type": "fill", "ref": "e3", "text": "kettle"}}'
    )
    assert action == Fill(ref="e3", text="kettle")
    assert thought == "search box is e3"


def test_parse_bare_action_and_code_fence() -> None:
    action, _ = parse_action('Sure!\n```json\n{"type": "click", "ref": "e12"}\n```')
    assert action == Click(ref="e12")


def test_parse_dict_input() -> None:
    action, _ = parse_action({"type": "done", "success": False, "summary": "export broken"})
    assert isinstance(action, Done)
    assert action.success is False


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("not json at all", "no JSON object"),
        ('{"type": "click", "ref": "e1"', "invalid JSON"),
        ('{"action": {"ref": "e1"}}', "action.type is required"),
        ('{"type": "hover", "ref": "e1"}', "unknown action type 'hover'"),
        ('{"type": "click", "ref": "button-1"}', "ref"),
        ('{"type": "click"}', "ref"),
        ('{"type": "click", "ref": "e1", "force": true}', "force"),  # extra fields forbidden
        ('{"type": "press", "key": "Meta+Shift+Q"}', "not allowed"),
        ('{"type": "wait_for", "text": "x", "timeout_ms": 600000}', "timeout_ms"),
        ('{"type": "done", "success": "maybe", "summary": "?"}', "success"),
        ('{"type": "done", "success": true, "summary": ""}', "summary"),
        ("[1, 2, 3]", "no JSON object"),
    ],
)
def test_invalid_actions_are_rejected_with_reason(raw: str, fragment: str) -> None:
    with pytest.raises(ActionParseError) as exc:
        parse_action(raw)
    assert fragment in str(exc.value)


def test_defaults_and_bounds() -> None:
    a, _ = parse_action({"type": "wait_for", "text": "Order confirmed"})
    assert isinstance(a, WaitFor)
    assert a.timeout_ms == 3000
    p, _ = parse_action({"type": "press", "key": "Enter"})
    assert p == Press(key="Enter")


def test_actions_are_immutable() -> None:
    a = Click(ref="e1")
    with pytest.raises(Exception):  # noqa: B017, PT011 - pydantic frozen model error
        a.ref = "e2"


def test_json_schema_lists_all_action_types() -> None:
    schema = action_json_schema()
    text = str(schema)
    for t in (
        "goto",
        "click",
        "fill",
        "select",
        "press",
        "scroll",
        "wait_for",
        "assert_text",
        "done",
    ):
        assert t in text
