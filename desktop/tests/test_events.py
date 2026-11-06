from careflow_desktop.api.events import parse_sse


def test_parse_sse_events_comments_and_multiline_data():
    lines = [
        "retry: 5000", "",
        "event: ready", 'data: {"scope": "ok"}', "",
        ": keepalive", "",
        "id: 1", "event: queue_entry", 'data: {"a": 1,', 'data: "b": 2}', "",
        "data: plain", "",
    ]
    assert list(parse_sse(lines)) == [
        ("ready", '{"scope": "ok"}'),
        ("queue_entry", '{"a": 1,\n"b": 2}'),
        ("message", "plain"),
    ]
