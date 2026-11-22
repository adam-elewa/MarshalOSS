from marshal_oss.entities import _parse_json_blob


def test_parse_clean_json():
    d = _parse_json_blob(
        '{"entities":[{"type":"person","value":"Jane Smith"}],"tags":["dui","arrest"]}'
    )
    assert d["entities"][0]["canonical"] == "Jane Smith"
    assert "dui" in d["tags"]


def test_parse_fenced():
    d = _parse_json_blob(
        '```json\n{"entities":[],"tags":["bodycam"]}\n```'
    )
    assert d["tags"] == ["bodycam"]
