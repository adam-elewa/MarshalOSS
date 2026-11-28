from marshal_oss.entities import (
    _parse_json_blob,
    collapse_person_counts,
    filter_tags,
    is_noise_entity,
    looks_like_diarization_speaker,
    looks_like_video_timestamp,
    merge_person_entities,
    person_names_compatible,
    prefer_person_canonical,
    scrub_noise_entities,
)


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


def test_drops_video_timestamp_bates():
    assert looks_like_video_timestamp("15:22:33")
    assert looks_like_video_timestamp("4:01 PM")
    assert looks_like_video_timestamp("19:29:58 UTC")
    # Bates / times are no longer substantive entity types
    assert is_noise_entity("bates", "PROD_0001234")
    assert is_noise_entity("time", "16:05")
    assert is_noise_entity("date", "08/09/2024")
    d = _parse_json_blob(
        '{"entities":['
        '{"type":"bates","value":"PROD_0001234"},'
        '{"type":"time","value":"16:05"},'
        '{"type":"person","value":"Jane Smith"}'
        '],"tags":["bodycam","daytime","timestamp"]}'
    )
    canons = [e["canonical"] for e in d["entities"]]
    assert canons == ["Jane Smith"]
    assert d["tags"] == ["bodycam"]
    scrubbed = scrub_noise_entities(
        [
            {"type": "bates", "canonical": "NX_1"},
            {"type": "person", "canonical": "Jane Smith"},
            {"type": "officer", "canonical": "7867"},
        ]
    )
    assert [e["canonical"] for e in scrubbed] == ["Jane Smith"]


def test_filters_noise_tags_and_weak_people():
    assert is_noise_entity("officer", "Axon Body 3")
    assert is_noise_entity("person", "man in green shirt")
    assert is_noise_entity("location", "Georgia")
    assert not is_noise_entity("location", "Springfield, IL")
    assert filter_tags(["BodyCam", "daytime", "New Jersey", "firearm", "timestamp", "dui"]) == [
        "bodycam",
        "firearm",
        "dui",
    ]


def test_drops_diarization_speakers():
    assert looks_like_diarization_speaker("Speaker A")
    assert looks_like_diarization_speaker("Speaker B")
    assert looks_like_diarization_speaker("SPK_2")
    assert looks_like_diarization_speaker("Unknown Speaker")
    assert not looks_like_diarization_speaker("Jane Smith")
    d = _parse_json_blob(
        '{"entities":['
        '{"type":"person","value":"Speaker A"},'
        '{"type":"person","value":"Speaker B"},'
        '{"type":"person","value":"Jane Smith"}'
        '],"tags":[]}'
    )
    canons = [e["canonical"] for e in d["entities"]]
    assert canons == ["Jane Smith"]
    scrubbed = scrub_noise_entities(
        [
            {"type": "person", "canonical": "Speaker C"},
            {"type": "person", "canonical": "Officer Lee"},
        ]
    )
    assert len(scrubbed) == 1 and scrubbed[0]["canonical"] == "Officer Lee"


def test_person_name_variants_merge():
    assert person_names_compatible("Jane Doe", "Jane Q. Doe")
    assert person_names_compatible("Jane Doe", "Jane Quinn Doe")
    assert person_names_compatible("Jane Q Doe", "Jane Quinn Doe")
    assert not person_names_compatible("Jane Doe", "Jane Davis")
    assert prefer_person_canonical("Jane Doe", "Jane Quinn Doe") == "Jane Quinn Doe"
    assert prefer_person_canonical("Jane Q. Doe", "Jane Quinn Doe") == "Jane Quinn Doe"

    merged = merge_person_entities(
        [
            {"type": "person", "canonical": "Jane Doe", "aliases": []},
            {"type": "person", "canonical": "Jane Q. Doe", "aliases": []},
            {"type": "person", "canonical": "Jane Quinn Doe", "aliases": []},
            {"type": "person", "canonical": "Jane Davis", "aliases": []},
        ]
    )
    people = [e for e in merged if e["type"] == "person"]
    canons = sorted(e["canonical"] for e in people)
    assert canons == ["Jane Davis", "Jane Quinn Doe"]

    collapsed = collapse_person_counts(
        {
            ("person", "Jane Doe"): 16,
            ("person", "Jane Q. Doe"): 4,
            ("person", "Jane Q Doe"): 4,
            ("person", "Jane Quinn Doe"): 10,
            ("person", "Jamaal A Hart"): 3,
            ("person", "Jamaal Hart"): 2,
        }
    )
    assert collapsed[("person", "Jane Quinn Doe")] == 34
    assert ("person", "Jane Doe") not in collapsed
    assert collapsed[("person", "Jamaal A Hart")] == 5
