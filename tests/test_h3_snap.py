import unittest
from pathlib import Path

from h3_backend import GenerateRequest, RefItem, build_h3_argv, expand_quality
from h3_media import (
    require_ui_canvas,
    seconds_to_frames,
    snap_frames,
    snap_spatial,
    validate_canvas,
)


class SnapTests(unittest.TestCase):
    def test_snap_frames_minimum(self) -> None:
        self.assertEqual(snap_frames(1), 22)
        self.assertEqual(snap_frames(22), 22)

    def test_snap_frames_up(self) -> None:
        self.assertEqual(snap_frames(23), 39)
        self.assertEqual(snap_frames(107), 107)
        self.assertEqual(snap_frames(108), 124)

    def test_seconds_to_frames(self) -> None:
        self.assertEqual(seconds_to_frames(0.9), 22)
        self.assertEqual(seconds_to_frames(10), 243)

    def test_snap_spatial(self) -> None:
        self.assertEqual(snap_spatial(16), 32)
        self.assertEqual(snap_spatial(512), 512)
        self.assertEqual(snap_spatial(500), 512)

    def test_validate_canvas_cap(self) -> None:
        validate_canvas(768, 1344)
        with self.assertRaises(ValueError):
            validate_canvas(1344, 1344)

    def test_require_ui_canvas_documented_only(self) -> None:
        self.assertEqual(require_ui_canvas(512, 512), (512, 512))
        self.assertEqual(require_ui_canvas(256, 256), (256, 256))
        self.assertEqual(require_ui_canvas(1344, 768), (1344, 768))
        with self.assertRaises(ValueError):
            require_ui_canvas(640, 640)
        with self.assertRaises(ValueError):
            require_ui_canvas(1024, 1024)


class QualityTests(unittest.TestCase):
    def test_expand_quality_rejects_token_reduction_aggressive_combo(self) -> None:
        with self.assertRaisesRegex(ValueError, "token-reduction"):
            expand_quality("aggressive", token_reduction=True, width=512, height=512)

    def test_expand_quality_small_steps_forces_reuse_1(self) -> None:
        q = expand_quality("four_step")
        self.assertEqual(q["steps"], 4)
        self.assertEqual(q["reuse"], 1)

    def test_build_argv_t2va(self) -> None:
        req = GenerateRequest(
            prompt="a red fox",
            output_path=Path("/tmp/out.mp4"),
            width=512,
            height=512,
            num_frames=22,
            quality="balanced",
            seed=42,
        )
        argv = build_h3_argv(h3_bin=Path("/opt/h3"), model_dir=Path("/models/H3"), req=req)
        self.assertEqual(argv[0], "/opt/h3")
        self.assertIn("-p", argv)
        self.assertIn("a red fox", argv)
        self.assertIn("--frames", argv)
        self.assertIn("22", argv)
        self.assertIn("--reuse", argv)
        self.assertIn("2", argv)
        self.assertIn("--seed", argv)
        self.assertIn("42", argv)
        self.assertIn("--profile", argv)

    def test_build_argv_rejects_mixed_checkpoints(self) -> None:
        req = GenerateRequest(
            prompt="x",
            output_path=Path("/tmp/out.mp4"),
            first_frame=Path("a.png"),
            refs=[RefItem(kind="image", path=Path("b.png"))],
        )
        with self.assertRaisesRegex(ValueError, "cannot be mixed"):
            build_h3_argv(h3_bin=Path("/opt/h3"), model_dir=Path("/m"), req=req)

    def test_build_argv_audio_needs_visual_ref(self) -> None:
        req = GenerateRequest(
            prompt="x",
            output_path=Path("/tmp/out.mp4"),
            refs=[RefItem(kind="audio", path=Path("a.wav"))],
        )
        with self.assertRaisesRegex(ValueError, "standalone audio"):
            build_h3_argv(h3_bin=Path("/opt/h3"), model_dir=Path("/m"), req=req)

    def test_ref_flag_order_preserved(self) -> None:
        req = GenerateRequest(
            prompt="Picture 1 then Video 1",
            output_path=Path("/tmp/out.mp4"),
            mode="ref2va",
            refs=[
                RefItem(kind="image", path=Path("a.png")),
                RefItem(kind="silent_video", path=Path("b.mp4")),
                RefItem(kind="audio", path=Path("c.wav")),
            ],
        )
        argv = build_h3_argv(h3_bin=Path("/opt/h3"), model_dir=Path("/m"), req=req)
        self.assertLess(argv.index("--ref-image"), argv.index("--ref-silent-video"))
        self.assertLess(argv.index("--ref-silent-video"), argv.index("--ref-audio"))
        self.assertEqual(argv[argv.index("--ref-image") + 1], "a.png")
        self.assertEqual(argv[argv.index("--ref-silent-video") + 1], "b.mp4")
        self.assertEqual(argv[argv.index("--ref-audio") + 1], "c.wav")

    def test_ref_video_audio_flag(self) -> None:
        req = GenerateRequest(
            prompt="Video 1",
            output_path=Path("/tmp/out.mp4"),
            mode="ref2va",
            refs=[
                RefItem(kind="image", path=Path("still.png")),
                RefItem(
                    kind="video_audio",
                    path=Path("clip.mp4"),
                    audio_path=Path("replace.wav"),
                ),
            ],
        )
        argv = build_h3_argv(h3_bin=Path("/opt/h3"), model_dir=Path("/m"), req=req)
        i = argv.index("--ref-video-audio")
        self.assertEqual(argv[i + 1], "clip.mp4")
        self.assertEqual(argv[i + 2], "replace.wav")

    def test_internal_render_flags(self) -> None:
        req = GenerateRequest(
            prompt="x",
            output_path=Path("/tmp/out.mp4"),
            width=512,
            height=512,
            render_width=384,
            render_height=384,
        )
        argv = build_h3_argv(h3_bin=Path("/opt/h3"), model_dir=Path("/m"), req=req)
        self.assertEqual(argv[argv.index("--render-width") + 1], "384")
        self.assertEqual(argv[argv.index("--render-height") + 1], "384")
        self.assertEqual(argv[argv.index("--width") + 1], "512")

    def test_256_preview_disables_token_reduction(self) -> None:
        req = GenerateRequest(
            prompt="x",
            output_path=Path("/tmp/out.mp4"),
            width=256,
            height=256,
            quality="fast",
            token_reduction=True,
        )
        argv = build_h3_argv(h3_bin=Path("/opt/h3"), model_dir=Path("/m"), req=req)
        self.assertNotIn("--token-reduction", argv)


if __name__ == "__main__":
    unittest.main()
