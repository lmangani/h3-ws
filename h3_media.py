"""H3 canvas / duration snap helpers and PyAV media I/O.

All Python-side media (concat, last-frame, duration, and the h3.c ffmpeg CLI
shim in ``h3_av.py``) goes through PyAV. h3.c posix_spawns ``scripts/h3-av``
via ``H3_AV`` instead of a system ffmpeg install.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

FPS = 24
SPATIAL_ALIGN = 32
MIN_SPATIAL = 32
MAX_PIXELS = 768 * 1344  # released 768p-class cap from h3.c
MIN_FRAMES = 22  # 5 + 17*1


def snap_frames(raw: int) -> int:
    """Snap *up* to the next legal H3 temporal shape ``5 + 17n`` (n ≥ 1)."""
    n = max(1, int(raw))
    if n <= MIN_FRAMES:
        return MIN_FRAMES
    k = math.ceil((n - 5) / 17)
    k = max(1, k)
    return 5 + 17 * k


def frames_to_seconds(num_frames: int, fps: int = FPS) -> float:
    return float(num_frames) / float(fps)


def seconds_to_frames(seconds: float, fps: int = FPS) -> int:
    raw = int(math.ceil(float(seconds) * fps))
    return snap_frames(raw)


def snap_spatial(n: int, align: int = SPATIAL_ALIGN) -> int:
    n = int(n)
    if n < MIN_SPATIAL:
        return MIN_SPATIAL
    return max(MIN_SPATIAL, int(round(n / align) * align))


def validate_canvas(width: int, height: int) -> tuple[int, int]:
    w = snap_spatial(width)
    h = snap_spatial(height)
    if w * h > MAX_PIXELS:
        raise ValueError(
            f"canvas {w}×{h} ({w * h} px) exceeds H3 limit {MAX_PIXELS} "
            f"(768×1344)"
        )
    return w, h


def duration_preset(
    preset_id: str, *, num_frames: int, note: str = ""
) -> dict[str, Any]:
    nf = snap_frames(int(num_frames))
    seconds = frames_to_seconds(nf)
    label = f"~{seconds:.3g} s ({nf} frames @ {FPS} fps)"
    if note:
        label = f"{label} — {note}"
    return {
        "id": preset_id,
        "seconds": round(seconds, 3),
        "num_frames": nf,
        "label": label,
    }


DURATION_PRESETS = [
    duration_preset("0.9s", num_frames=22, note="dev"),
    duration_preset("1.6s", num_frames=39),
    duration_preset("2.3s", num_frames=56),
    duration_preset("4.5s", num_frames=107, note="UI default"),
    duration_preset("10s", num_frames=243),
    duration_preset("15s", num_frames=362),
]

# Only the canvases documented by h3.c. Internal render sizes are same-aspect
# DiT/VAE canvases that vImage-upscale to the output (512 square only).
RESOLUTION_PRESETS = [
    {
        "id": "256x256",
        "width": 256,
        "height": 256,
        "label": "256 × 256 — fast preview",
        "guidance": "Native 8×8 token grid with automatic low-resolution RoPE. Keep token reduction off.",
    },
    {
        "id": "512x512",
        "width": 512,
        "height": 512,
        "label": "512 × 512 — safest (default)",
        "guidance": "Repeatedly validated development size.",
    },
    {
        "id": "512x512-fast",
        "width": 512,
        "height": 512,
        "render_width": 384,
        "render_height": 384,
        "label": "512 × 512 · 384 internal — fast-quality",
        "guidance": "Validated fast-quality scaling point: DiT/VAE at 384, output 512.",
    },
    {
        "id": "512x512-aggressive",
        "width": 512,
        "height": 512,
        "render_width": 320,
        "render_height": 320,
        "label": "512 × 512 · 320 internal — aggressive",
        "guidance": "Validated aggressive scaling point. Do not add token-reduction with layers 40 + reuse 3.",
    },
    {
        "id": "768x768",
        "width": 768,
        "height": 768,
        "label": "768 × 768 — close-quality square",
        "guidance": "Validated close-quality square; substantially more expensive.",
    },
    {
        "id": "1024x768",
        "width": 1024,
        "height": 768,
        "label": "1024 × 768 — 4:3",
        "guidance": "Valid 4:3 768p-class canvas.",
    },
    {
        "id": "768x1024",
        "width": 768,
        "height": 1024,
        "label": "768 × 1024 — 3:4",
        "guidance": "Valid 3:4 768p-class canvas.",
    },
    {
        "id": "1344x768",
        "width": 1344,
        "height": 768,
        "label": "1344 × 768 — 768p landscape",
        "guidance": "Released 768p-class landscape limit.",
    },
    {
        "id": "768x1344",
        "width": 768,
        "height": 1344,
        "label": "768 × 1344 — 768p portrait",
        "guidance": "Released 768p-class portrait limit.",
    },
]

ALLOWED_OUTPUT_SIZES = frozenset(
    (int(p["width"]), int(p["height"])) for p in RESOLUTION_PRESETS
)


def require_ui_canvas(width: int, height: int) -> tuple[int, int]:
    """Accept only documented H3 output canvases (mechanical ×32 cap still applies)."""
    w, h = validate_canvas(width, height)
    if (w, h) not in ALLOWED_OUTPUT_SIZES:
        allowed = ", ".join(f"{a}×{b}" for a, b in sorted(ALLOWED_OUTPUT_SIZES))
        raise ValueError(f"canvas {w}×{h} is not a documented H3 size ({allowed})")
    return w, h


def sanitize_filename(prompt: str, maxlen: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", (prompt or "").strip())[:maxlen].strip("_")
    return slug.lower() or "clip"


def media_available() -> bool:
    try:
        import av  # noqa: F401

        return True
    except ImportError:
        return False


MIN_REF_AUDIO_S = 2.0
MAX_REF_AUDIO_S = 15.0
MAX_REF_AUDIO_TOTAL_S = 15.0
MAX_REF_AUDIO_CLIPS = 3
_DURATION_SLACK_S = 0.05


def media_shim_ok() -> tuple[bool, str]:
    """PyAV + h3-av shim (the tools h3.c will posix_spawn)."""
    from h3_paths import default_h3_av

    if not media_available():
        return False, "PyAV is required (pip install av) — h3.c media goes through h3-av"
    shim = default_h3_av()
    if not shim.is_file():
        return False, f"h3-av shim not found at {shim}"
    return True, str(shim)


def ffmpeg_ok() -> tuple[bool, str]:
    """Backward-compatible alias: the media stack is the PyAV shim."""
    return media_shim_ok()


def probe_duration_seconds(path: Path | str) -> float | None:
    """Duration in seconds via PyAV. No ffprobe CLI."""
    src = Path(path)
    if not src.is_file() or not media_available():
        return None
    try:
        import av

        container = av.open(str(src))
        try:
            if container.duration and container.duration > 0:
                return float(container.duration) / float(av.time_base)
            best = 0.0
            for stream in container.streams:
                if stream.duration and stream.time_base and stream.duration > 0:
                    seconds = float(stream.duration * stream.time_base)
                    if seconds > best:
                        best = seconds
            return best or None
        finally:
            container.close()
    except Exception:
        return None


def assert_audio_durations(seconds: list[float]) -> None:
    """Enforce h3.c audio-reference limits (2–15 s each, ≤3 clips, total ≤15 s)."""
    if len(seconds) > MAX_REF_AUDIO_CLIPS:
        raise ValueError(f"at most {MAX_REF_AUDIO_CLIPS} audio references")
    total = 0.0
    for duration in seconds:
        if duration + _DURATION_SLACK_S < MIN_REF_AUDIO_S:
            raise ValueError(
                f"audio reference is {duration:.2f}s; h3.c requires at least "
                f"{MIN_REF_AUDIO_S:.0f}s"
            )
        if duration - _DURATION_SLACK_S > MAX_REF_AUDIO_S:
            raise ValueError(
                f"audio reference is {duration:.2f}s; h3.c allows at most "
                f"{MAX_REF_AUDIO_S:.0f}s"
            )
        total += duration
    if total - _DURATION_SLACK_S > MAX_REF_AUDIO_TOTAL_S:
        raise ValueError(
            f"audio references total {total:.2f}s; h3.c caps the sum at "
            f"{MAX_REF_AUDIO_TOTAL_S:.0f}s"
        )


def extract_last_frame(video_path: str | Path, dest: str | Path) -> Path:
    """Write the last decoded frame of ``video_path`` as PNG to ``dest``."""
    import av
    from PIL import Image

    src = Path(video_path)
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(src))
    stream = container.streams.video[0]
    last = None
    for frame in container.decode(stream):
        last = frame
    container.close()
    if last is None:
        raise RuntimeError(f"no video frames in {src}")
    img = last.to_ndarray(format="rgb24")
    Image.fromarray(img).save(out)
    return out


def concat_mp4s(paths: list[Path], dest: Path) -> Path:
    """Stream-copy concatenate MP4s with PyAV. Falls back to re-encode if needed."""
    import av

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        import shutil

        shutil.copy2(paths[0], dest)
        return dest

    first = av.open(str(paths[0]))
    out = av.open(str(dest), mode="w")
    stream_map: dict[int, Any] = {}
    for i, stream in enumerate(first.streams):
        if stream.type not in ("video", "audio"):
            continue
        out_stream = out.add_stream(template=stream)
        stream_map[i] = out_stream
    first.close()

    try:
        for path in paths:
            src = av.open(str(path))
            for packet in src.demux():
                if packet.stream_index not in stream_map:
                    continue
                if packet.dts is None:
                    continue
                packet.stream = stream_map[packet.stream_index]
                out.mux(packet)
            src.close()
    finally:
        out.close()
    return dest
