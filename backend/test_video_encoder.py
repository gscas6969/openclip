import subprocess
import unittest
from unittest.mock import patch

import ffbin


class TestVideoEncoder(unittest.TestCase):
    def setUp(self):
        ffbin._cached_available_encoders = None
        ffbin.set_preferred_encoder("auto")

    def tearDown(self):
        ffbin._cached_available_encoders = None
        ffbin.set_preferred_encoder("auto")

    def test_video_encode_args_per_encoder(self):
        nvenc = ffbin.video_encode_args(quality="standard", encoder="h264_nvenc")
        self.assertIn("h264_nvenc", nvenc)
        self.assertIn("p4", nvenc)
        self.assertIn("-cq", nvenc)
        self.assertIn("yuv420p", nvenc)

        nvenc_fast = ffbin.video_encode_args(quality="fast", encoder="h264_nvenc")
        self.assertIn("p1", nvenc_fast)
        self.assertIn("19", nvenc_fast)

        amf = ffbin.video_encode_args(quality="compress", crf=28, encoder="h264_amf")
        self.assertIn("h264_amf", amf)
        self.assertIn("28", amf)

        qsv = ffbin.video_encode_args(quality="standard", encoder="h264_qsv")
        self.assertIn("h264_qsv", qsv)
        self.assertIn("-global_quality", qsv)

        vt = ffbin.video_encode_args(quality="standard", encoder="h264_videotoolbox")
        self.assertIn("h264_videotoolbox", vt)
        self.assertIn("-q:v", vt)

        cpu = ffbin.video_encode_args(quality="standard", force_cpu=True)
        self.assertEqual(cpu[:6], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])

    def test_detect_and_resolve_prefers_first_working_gpu(self):
        with (
            patch.object(ffbin, "_list_ffmpeg_encoders", return_value={"h264_nvenc", "h264_qsv", "libx264"}),
            patch.object(ffbin, "_probe_single_encoder", side_effect=lambda _exe, enc: enc == "h264_nvenc"),
        ):
            available = ffbin.detect_video_encoders(force_refresh=True)
            self.assertEqual([a["id"] for a in available], ["h264_nvenc", "libx264"])
            self.assertEqual(ffbin.resolve_video_encoder(), "h264_nvenc")

            ffbin.set_preferred_encoder("cpu")
            self.assertEqual(ffbin.resolve_video_encoder(), "libx264")

            ffbin.set_preferred_encoder("h264_amf")  # unavailable -> falls back to best available
            self.assertEqual(ffbin.resolve_video_encoder(), "h264_nvenc")

    def test_run_ffmpeg_encode_falls_back_to_cpu_on_gpu_failure(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "h264_nvenc" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="NVENC session limit exceeded")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            patch.object(ffbin, "resolve_video_encoder", return_value="h264_nvenc"),
            patch.object(ffbin.subprocess, "run", side_effect=fake_run),
        ):
            res = ffbin.run_ffmpeg_encode(
                ["ffmpeg", "-y", "-i", "in.mp4"],
                ["-c:a", "copy", "out.mp4"],
                quality="standard",
            )
            self.assertEqual(res.returncode, 0)
            self.assertEqual(len(calls), 2)
            self.assertIn("h264_nvenc", calls[0])
            self.assertIn("libx264", calls[1])

    def test_set_preferred_encoder_normalizes_invalid_values(self):
        self.assertEqual(ffbin.set_preferred_encoder("libx264"), "cpu")
        self.assertEqual(ffbin.set_preferred_encoder("unknown_codec"), "auto")
        self.assertEqual(ffbin.set_preferred_encoder("h264_nvenc"), "h264_nvenc")


if __name__ == "__main__":
    unittest.main()
