"""One-shot h3.c process manager (P2). Resident interactive session is P5."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from h3_media import require_ui_canvas, snap_frames
from h3_paths import default_h3_bin, default_model_dir, mk_scratch_file

log = logging.getLogger("h3-backend")

ProgressCallback = Callable[[dict[str, Any]], None]


class GenerationCancelledError(RuntimeError):
    """Raised when the user or SIGINT cancelled an in-flight ``h3`` process."""


QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    "four_step": {
        "id": "four_step",
        "label": "Four-step",
        "steps": 4,
        "layers": 50,
        "reuse": 1,
        "core_reuse": None,
        "token_reduction": False,
        "render": None,
    },
    "aggressive": {
        "id": "aggressive",
        "label": "Aggressive preview",
        "steps": 20,
        "layers": 40,
        "reuse": 3,
        "core_reuse": None,
        "token_reduction": False,
        "render": (320, 320),  # only applied when output is 512×512
    },
    "fast": {
        "id": "fast",
        "label": "Fast",
        "steps": 20,
        "layers": 45,
        "reuse": 2,
        "core_reuse": None,
        "token_reduction": True,
        "render": None,
    },
    "balanced": {
        "id": "balanced",
        "label": "Balanced (default)",
        "steps": 20,
        "layers": 45,
        "reuse": 2,
        "core_reuse": None,
        "token_reduction": False,
        "render": None,
    },
    "close": {
        "id": "close",
        "label": "Close / reference",
        "steps": 50,
        "layers": 50,
        "reuse": 1,
        "core_reuse": None,
        "token_reduction": False,
        "render": None,
    },
}

QUALITY_PRESET_LIST = [
    {"id": p["id"], "label": p["label"]} for p in QUALITY_PRESETS.values()
]

GENERATION_MODES = [
    {"id": "t2va", "label": "Text to video+audio"},
    {"id": "first_frame", "label": "First frame → video"},
    {"id": "last_frame", "label": "Last frame → video"},
    {"id": "fl2va", "label": "First and last frame"},
    {"id": "ref2va", "label": "Ordered references (Ref2VA)"},
]

_STEP_RE = re.compile(
    r"(?:step|pass|denois\w*)[^\d]{0,12}(\d+)\s*/\s*(\d+)",
    re.IGNORECASE,
)


def physical_memory_bytes() -> int | None:
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return int(out.strip())
        except (OSError, ValueError, subprocess.CalledProcessError):
            return None
    return None


def ram_gb() -> float | None:
    n = physical_memory_bytes()
    return None if n is None else n / (1024**3)


def recommend_ssd_streaming(ram: float | None = None) -> bool:
    gb = ram if ram is not None else ram_gb()
    if gb is None:
        return False
    return gb < 64.0


def fl2va_dir(model_dir: Path) -> Path:
    return model_dir / "FL2VA"


def ref2va_dir(model_dir: Path) -> Path:
    return model_dir / "Ref2VA"


def model_layout_ok(model_dir: Path, *, need_ref2va: bool = False) -> tuple[bool, str]:
    root = Path(model_dir)
    if not root.is_dir():
        return False, f"model dir not found: {root}"
    fl = fl2va_dir(root)
    if not fl.is_dir():
        return False, f"FL2VA checkpoint missing under {root}"
    if need_ref2va and not ref2va_dir(root).is_dir():
        return False, f"Ref2VA checkpoint missing under {root}"
    return True, str(root)


def expand_quality(
    quality: str,
    *,
    steps: int | None = None,
    layers: int | None = None,
    reuse: int | None = None,
    core_reuse: int | None = None,
    token_reduction: bool | None = None,
    width: int = 512,
    height: int = 512,
    render_width: int | None = None,
    render_height: int | None = None,
) -> dict[str, Any]:
    preset = QUALITY_PRESETS.get((quality or "balanced").strip().lower())
    if preset is None:
        preset = QUALITY_PRESETS["balanced"]
    out = dict(preset)
    if steps is not None:
        out["steps"] = max(1, int(steps))
    if layers is not None:
        out["layers"] = max(1, min(50, int(layers)))
    if reuse is not None:
        out["reuse"] = max(1, int(reuse))
    if core_reuse is not None:
        out["core_reuse"] = max(1, int(core_reuse))
        out["reuse"] = None
    if token_reduction is not None:
        out["token_reduction"] = bool(token_reduction)
    if width == 256 and height == 256:
        out["token_reduction"] = False
    rw, rh = render_width, render_height
    suggested = out.get("render")
    if rw is None and rh is None and suggested and width == 512 and height == 512:
        rw, rh = suggested
    if (rw is None) != (rh is None):
        raise ValueError("--render-width and --render-height must be set together")
    if rw is not None and rh is not None:
        if rw > width or rh > height:
            raise ValueError("internal render size cannot exceed output canvas")
        out["render"] = (int(rw), int(rh))
    else:
        out["render"] = None
    # h3.c: do not combine token-reduction with layers 40 + reuse 3
    if out.get("token_reduction") and out.get("layers") == 40 and out.get("reuse") == 3:
        raise ValueError(
            "token-reduction cannot be combined with --layers 40 and --reuse 3"
        )
    if out.get("reuse") and out.get("core_reuse"):
        raise ValueError("--reuse and --core-reuse are mutually exclusive")
    if int(out["steps"]) <= 7 and (out.get("reuse") or 1) > 1:
        out["reuse"] = 1
    return out


REF_KINDS = ("image", "silent_video", "video", "video_audio", "audio")
MAX_REF_IMAGES = 9
MAX_REF_VIDEOS = 3
MAX_REF_AUDIO = 3
MAX_REF_FILES = 12


@dataclass
class RefItem:
    """One ordered Ref2VA input. ``kind`` maps 1:1 to an h3.c flag."""

    kind: str
    path: Path
    audio_path: Path | None = None
    name: str = ""

    def file_count(self) -> int:
        if self.kind == "video_audio":
            return 2
        return 1


def refs_from_legacy(
    *,
    ref_images: list[Path] | None = None,
    ref_silent_videos: list[Path] | None = None,
    ref_videos: list[Path] | None = None,
    ref_audio: list[Path] | None = None,
) -> list[RefItem]:
    items: list[RefItem] = []
    for p in ref_images or []:
        items.append(RefItem(kind="image", path=Path(p)))
    for p in ref_silent_videos or []:
        items.append(RefItem(kind="silent_video", path=Path(p)))
    for p in ref_videos or []:
        items.append(RefItem(kind="video", path=Path(p)))
    for p in ref_audio or []:
        items.append(RefItem(kind="audio", path=Path(p)))
    return items


def validate_refs(refs: list[RefItem]) -> None:
    n_img = n_vid = n_aud = n_files = 0
    has_visual = False
    for item in refs:
        kind = (item.kind or "").strip().lower()
        if kind not in REF_KINDS:
            raise ValueError(f"unknown reference kind: {item.kind}")
        if not item.path:
            raise ValueError("reference path is required")
        n_files += item.file_count()
        if kind == "image":
            n_img += 1
            has_visual = True
        elif kind in ("silent_video", "video", "video_audio"):
            n_vid += 1
            has_visual = True
            if kind == "video_audio":
                n_aud += 1
                if item.audio_path is None:
                    raise ValueError("--ref-video-audio requires a replacement audio file")
        elif kind == "audio":
            n_aud += 1
    if n_img > MAX_REF_IMAGES:
        raise ValueError(f"at most {MAX_REF_IMAGES} reference images")
    if n_vid > MAX_REF_VIDEOS:
        raise ValueError(f"at most {MAX_REF_VIDEOS} reference videos")
    if n_aud > MAX_REF_AUDIO:
        raise ValueError(f"at most {MAX_REF_AUDIO} audio references")
    if n_files > MAX_REF_FILES:
        raise ValueError(f"at most {MAX_REF_FILES} mixed reference files")
    if n_aud and not has_visual:
        raise ValueError("standalone audio must accompany an image or video reference")


def parse_refs_payload(raw: Any) -> list[RefItem]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("refs must be a list")
    items: list[RefItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each ref must be an object")
        kind = str(entry.get("kind") or "").strip().lower()
        path = str(entry.get("path") or "").strip()
        if not kind or not path:
            raise ValueError("each ref needs kind and path")
        audio = str(entry.get("audio_path") or "").strip()
        items.append(
            RefItem(
                kind=kind,
                path=Path(path),
                audio_path=Path(audio) if audio else None,
                name=str(entry.get("name") or Path(path).name),
            )
        )
    validate_refs(items)
    return items


def append_ref_flags(cmd: list[str], refs: list[RefItem]) -> None:
    """Emit flags in list order so the model sees Picture 1 / Video 1 correctly."""
    for item in refs:
        if item.kind == "image":
            cmd.extend(["--ref-image", str(item.path)])
        elif item.kind == "silent_video":
            cmd.extend(["--ref-silent-video", str(item.path)])
        elif item.kind == "video":
            cmd.extend(["--ref-video", str(item.path)])
        elif item.kind == "video_audio":
            cmd.extend(["--ref-video-audio", str(item.path), str(item.audio_path)])
        elif item.kind == "audio":
            cmd.extend(["--ref-audio", str(item.path)])


@dataclass
class GenerateRequest:
    prompt: str
    output_path: Path
    width: int = 512
    height: int = 512
    num_frames: int = 22
    quality: str = "balanced"
    steps: int | None = None
    layers: int | None = None
    reuse: int | None = None
    core_reuse: int | None = None
    token_reduction: bool | None = None
    render_width: int | None = None
    render_height: int | None = None
    seed: int | None = None
    ssd_streaming: bool = False
    first_frame: Path | None = None
    last_frame: Path | None = None
    refs: list[RefItem] = field(default_factory=list)
    mode: str = "t2va"
    profile: bool = True


def uses_ref2va(req: GenerateRequest) -> bool:
    return bool(req.refs) or req.mode == "ref2va"


def uses_fl2va_anchors(req: GenerateRequest) -> bool:
    return req.first_frame is not None or req.last_frame is not None


def build_h3_argv(
    *,
    h3_bin: Path,
    model_dir: Path,
    req: GenerateRequest,
) -> list[str]:
    width, height = require_ui_canvas(req.width, req.height)
    frames = snap_frames(req.num_frames)
    q = expand_quality(
        req.quality,
        steps=req.steps,
        layers=req.layers,
        reuse=req.reuse,
        core_reuse=req.core_reuse,
        token_reduction=req.token_reduction,
        width=width,
        height=height,
        render_width=req.render_width,
        render_height=req.render_height,
    )
    if req.refs:
        validate_refs(req.refs)
    if uses_ref2va(req) and uses_fl2va_anchors(req):
        raise ValueError("Ref2VA references cannot be mixed with first/last-frame anchors")
    if req.mode == "ref2va" and not req.refs:
        raise ValueError("ref2va requires at least one image, video, or audio reference")

    cmd: list[str] = [
        str(h3_bin),
        "-d",
        str(model_dir),
        "-p",
        req.prompt,
        "--width",
        str(width),
        "--height",
        str(height),
        "--frames",
        str(frames),
        "--steps",
        str(q["steps"]),
        "--layers",
        str(q["layers"]),
        "-o",
        str(req.output_path),
    ]
    if q.get("core_reuse"):
        cmd.extend(["--core-reuse", str(q["core_reuse"])])
    elif q.get("reuse"):
        cmd.extend(["--reuse", str(q["reuse"])])
    if q.get("token_reduction"):
        cmd.append("--token-reduction")
    render = q.get("render")
    if render:
        cmd.extend(["--render-width", str(render[0]), "--render-height", str(render[1])])
    if req.seed is not None and int(req.seed) >= 0:
        cmd.extend(["--seed", str(int(req.seed))])
    if req.ssd_streaming:
        cmd.append("--ssd-streaming")
    if req.profile:
        cmd.append("--profile")
    if req.first_frame:
        cmd.extend(["--first-frame", str(req.first_frame)])
    if req.last_frame:
        cmd.extend(["--last-frame", str(req.last_frame)])
    append_ref_flags(cmd, req.refs)
    return cmd


class H3Engine:
    """Spawns ``./h3`` per job. One generation at a time is enforced by the caller."""

    def __init__(
        self,
        h3_bin: Path | None = None,
        model_dir: Path | None = None,
        *,
        default_ssd_streaming: bool | None = None,
    ) -> None:
        self.h3_bin = Path(h3_bin) if h3_bin else default_h3_bin()
        self.model_dir = Path(model_dir) if model_dir else default_model_dir()
        if default_ssd_streaming is None:
            default_ssd_streaming = recommend_ssd_streaming()
        self.default_ssd_streaming = bool(default_ssd_streaming)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._cancel = threading.Event()
        self._progress: dict[str, Any] = {}
        self._t0 = 0.0

    def model_progress_for_ws(self) -> dict[str, Any] | None:
        p = dict(self._progress)
        return p or None

    def request_cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def shutdown(self, wait: bool = True) -> None:
        self.request_cancel()
        proc = self._proc
        if proc is None:
            return
        if wait:
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None

    def info(self) -> dict[str, Any]:
        if not self.h3_bin.is_file():
            return {
                "ok": False,
                "error": f"h3 binary not found at {self.h3_bin} (run scripts/build_h3.sh)",
            }
        ok, note = model_layout_ok(self.model_dir)
        if not ok:
            return {"ok": False, "error": note, "h3_bin": str(self.h3_bin)}
        try:
            proc = subprocess.run(
                [str(self.h3_bin), "--info", "-d", str(self.model_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": str(exc), "h3_bin": str(self.h3_bin)}
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": text.strip()[-4000:],
            "h3_bin": str(self.h3_bin),
            "model_dir": str(self.model_dir),
            "ram_gb": ram_gb(),
            "recommend_ssd_streaming": self.default_ssd_streaming,
        }

    def _parse_line(self, line: str, steps: int) -> None:
        line = line.strip()
        if not line:
            return
        elapsed = round(time.time() - self._t0, 1)
        mp: dict[str, Any] = {
            "stage": "generating",
            "elapsed_s": elapsed,
            "label": line[:160],
        }
        m = _STEP_RE.search(line)
        if m:
            step, total = int(m.group(1)), int(m.group(2))
            mp["step"] = step
            mp["total"] = total
            if total > 0:
                mp["pct"] = round(100.0 * step / total, 1)
        elif steps > 0:
            mp["total"] = steps
        self._progress = mp

    def generate(
        self,
        req: GenerateRequest,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        if not self.h3_bin.is_file():
            raise FileNotFoundError(
                f"h3 binary not found at {self.h3_bin}. Run scripts/build_h3.sh"
            )
        need_ref = uses_ref2va(req)
        ok, note = model_layout_ok(self.model_dir, need_ref2va=need_ref)
        if not ok:
            raise FileNotFoundError(note)
        ffmpeg = shutil.which(os.environ.get("H3_FFMPEG", "ffmpeg")) or shutil.which(
            "ffmpeg"
        )
        if not ffmpeg:
            raise FileNotFoundError(
                "FFmpeg not found on PATH (h3.c needs ffmpeg + ffprobe)"
            )

        req.output_path = Path(req.output_path)
        req.output_path.parent.mkdir(parents=True, exist_ok=True)

        q = expand_quality(
            req.quality,
            steps=req.steps,
            layers=req.layers,
            reuse=req.reuse,
            core_reuse=req.core_reuse,
            token_reduction=req.token_reduction,
            width=req.width,
            height=req.height,
            render_width=req.render_width,
            render_height=req.render_height,
        )
        argv = build_h3_argv(h3_bin=self.h3_bin, model_dir=self.model_dir, req=req)
        log.info("h3 argv: %s", " ".join(argv[:8]) + " …")

        self._cancel.clear()
        self._t0 = time.time()
        self._progress = {"stage": "starting", "elapsed_s": 0, "total": q["steps"]}
        if on_progress:
            on_progress(self._progress)

        env = os.environ.copy()
        try:
            with self._lock:
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
                self._proc = proc
        except OSError as exc:
            raise RuntimeError(f"failed to spawn h3: {exc}") from exc

        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if self._cancel.is_set():
                    proc.terminate()
                    raise GenerationCancelledError("cancelled")
                self._parse_line(line, int(q["steps"]))
                if on_progress:
                    on_progress(self._progress)
                log.debug("h3: %s", line.rstrip())
            rc = proc.wait()
        finally:
            self._proc = None

        if self._cancel.is_set():
            raise GenerationCancelledError("cancelled")
        if rc != 0:
            raise RuntimeError(f"h3 exited {rc}")
        if not req.output_path.is_file() or req.output_path.stat().st_size < 32:
            raise RuntimeError(f"h3 did not write an MP4 at {req.output_path}")
        return str(req.output_path)


def scratch_output(prefix: str = "h3_") -> Path:
    fd, path = mk_scratch_file(prefix, ".mp4")
    os.close(fd)
    return Path(path)
