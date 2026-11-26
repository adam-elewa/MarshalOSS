from marshal_oss.bates import extract_bates
from marshal_oss.config import Settings

PAT = Settings().bates_pattern


def test_doc_bates_still_extracted():
    assert extract_bates("PROD/PROD_0001234.pdf", PAT) == "PROD_0001234"
    assert extract_bates("Smith_Depo_Bates 44521.pdf", PAT) == "Bates 44521"
    assert extract_bates("NX_0004821.pdf", PAT) == "NX_0004821"


def test_video_paths_skip_bates():
    assert extract_bates("BODYCAM_143022.mp4", PAT) is None
    assert extract_bates("clips/AXON_Body_2_Video_2024-01-15_143022.mp4", PAT) is None
    assert extract_bates("VID_20240115_143022.mp4", PAT) is None
    assert extract_bates("interview.wav", PAT) is None


def test_timestampish_token_rejected_even_on_docs():
    # Rare, but clock-like tails should not become Bates labels.
    assert extract_bates("notes_BODYCAM_143022.pdf", PAT) is None
