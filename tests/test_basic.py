import json
import subprocess
from pathlib import Path

from PIL import Image


def test_basic(tmp_path: Path):
    img_path = tmp_path / "img.png"
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(img_path)

    video_path = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=2x2:d=1", str(video_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    output_path = tmp_path / "out.bin"
    subprocess.run([
        "python", "beheader.py", str(output_path), str(img_path), str(video_path)
    ], check=True)

    assert output_path.exists()
    assert output_path.stat().st_size > 0
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=format_name",
        "-of", "json", str(output_path)
    ], check=True, capture_output=True, text=True)
    data = json.loads(probe.stdout)
    assert "mp4" in data["format"]["format_name"]
