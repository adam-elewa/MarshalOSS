import shutil
import subprocess
from pathlib import Path

import pytest

from marshal_oss.visual import is_image_path, is_video_path, sample_video_frames


def test_ext_helpers():
    assert is_image_path(Path("a.JPG"))
    assert is_video_path(Path("b.mp4"))
    assert not is_video_path(Path("c.wav"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_sample_video_frames(tmp_path: Path):
    vid = tmp_path / "clip.mp4"
    # 3-second color test pattern
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=3",
            "-pix_fmt",
            "yuv420p",
            str(vid),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    frames = sample_video_frames(vid, interval_sec=1.0, max_frames=5)
    assert 2 <= len(frames) <= 5
    assert frames[0][0] == 0.0
    assert frames[0][1][:8] == b"\x89PNG\r\n\x1a\n"
