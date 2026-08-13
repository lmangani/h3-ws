import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_av(argv: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    script = _repo_root() / "h3_av.py"
    return subprocess.run(
        [sys.executable, str(script), *argv],
        input=stdin,
        capture_output=True,
        check=False,
    )


class H3AvShimTests(unittest.TestCase):
    def test_ffprobe_png_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.png"
            Image.new("RGB", (96, 64), (10, 20, 30)).save(path)
            proc = _run_av(
                [
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0:s=x",
                    str(path),
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(proc.stdout.decode().strip(), "96x64")

    def test_decode_image_stretch_rgb24(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.png"
            Image.new("RGB", (40, 20), (255, 0, 0)).save(path)
            proc = _run_av(
                [
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=32:32:flags=lanczos",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(len(proc.stdout), 32 * 32 * 3)

    def test_encode_raw_rgb_mp4(self) -> None:
        width, height, frames = 32, 32, 5
        raw = bytes([i % 256 for i in range(width * height * 3)]) * frames
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.mp4"
            proc = _run_av(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "rawvideo",
                    "-pixel_format",
                    "rgb24",
                    "-video_size",
                    f"{width}x{height}",
                    "-framerate",
                    "24",
                    "-i",
                    "pipe:0",
                    "-an",
                    "-c:v",
                    "libx264",
                    str(out),
                ],
                stdin=raw,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 32)

    def test_media_shim_ok(self) -> None:
        from h3_media import media_shim_ok

        ok, note = media_shim_ok()
        self.assertTrue(ok, note)


if __name__ == "__main__":
    unittest.main()
