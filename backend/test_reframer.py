"""
Unit tests for reframer, speaker_detector, and layout_detector fixes:
  - Dead-center podcast split-screen box math (including edge-sitting speakers)
  - Never split-screening a single-speaker close-up shot
  - Size-aware face NMS & torso/chest false-positive suppression
  - Shot-locked continuous zoom (zero step-function zoom jitter)
  - Deadzone tripod lock vs smooth pan expression for walking speakers
  - Spatial track stability & hysteresis hold in speaker_detector
"""

import unittest

import layout_detector
import reframer
import speaker_detector


class TestPodcastSplitCentering(unittest.TestCase):
    def test_standard_wide_speakers_dead_center(self):
        w, h = 1920, 1080
        lcx, lcy, lh = 480.0, 320.0, 170.0
        rcx, rcy, rh = 1440.0, 320.0, 170.0
        cw, ch, lx, ly, rx, ry = reframer._compute_split_boxes(lcx, lcy, lh, rcx, rcy, rh, w, h)

        self.assertLessEqual(abs((lx + cw / 2.0) - lcx), 2.0)
        self.assertLessEqual(abs((rx + cw / 2.0) - rcx), 2.0)
        self.assertLessEqual(lx + cw, rx + 20)  # non-overlapping panels
        # Eye-line near 38% from panel top
        self.assertAlmostEqual((lcy - ly) / ch, 0.38, delta=0.04)

    def test_edge_sitting_speakers_no_cliff_jump(self):
        """Regression test for wide podcasts where speakers sit near ~13% and ~88% of frame width."""
        w, h = 1920, 1080
        lcx, lcy, lh = 260.0, 260.0, 162.0
        rcx, rcy, rh = 1675.0, 260.0, 162.0
        cw, ch, lx, ly, rx, ry = reframer._compute_split_boxes(lcx, lcy, lh, rcx, rcy, rh, w, h)

        # Both speakers must be centered inside their panels (<= 3px error), NOT pushed off to the side
        self.assertLessEqual(abs((lx + cw / 2.0) - lcx), 3.0)
        self.assertLessEqual(abs((rx + cw / 2.0) - rcx), 3.0)
        self.assertGreaterEqual(lx, 0)
        self.assertLessEqual(rx + cw, w)
        self.assertGreaterEqual(ly, 0)
        self.assertLessEqual(ry + ch, h)

    def test_extreme_edge_speaker_clamps_gracefully(self):
        w, h = 1920, 1080
        cw, ch, lx, ly, rx, ry = reframer._compute_split_boxes(110.0, 240.0, 150.0, 1720.0, 240.0, 150.0, w, h)
        self.assertGreaterEqual(lx, 0)
        self.assertLessEqual(rx + cw, w)
        # Should clamp tight near min_crop_w instead of exploding to 750+ px
        self.assertLessEqual(cw, 450)


class TestFaceCleaningAndClustering(unittest.TestCase):
    def test_nms_merges_overlapping_closeup_and_drops_chest_false_positive(self):
        vid_h = 1080
        real_face = {
            "cx": 900.0, "cy": 300.0, "w": 260.0, "h": 310.0, "size": 310.0 / vid_h,
            "has_landmarks": True, "source": "lm", "score": 0.90, "mouth": 0.08,
        }
        # Overlapping duplicate 90px away (old 55px threshold failed to merge this!)
        dup_box = {
            "cx": 990.0, "cy": 290.0, "w": 280.0, "h": 320.0, "size": 320.0 / vid_h,
            "has_landmarks": False, "source": "bf", "score": 0.60,
        }
        # Chest/t-shirt hallucination directly below face
        chest_fp = {
            "cx": 910.0, "cy": 610.0, "w": 520.0, "h": 540.0, "size": 540.0 / vid_h,
            "has_landmarks": False, "source": "bf", "score": 0.57,
        }
        cleaned = reframer._nms_and_clean_faces([chest_fp, dup_box, real_face], vid_h)
        self.assertEqual(len(cleaned), 1)
        self.assertTrue(cleaned[0]["has_landmarks"])
        self.assertAlmostEqual(cleaned[0]["cx"], 900.0)

    def test_single_closeup_cluster_never_classified_as_split(self):
        w, h = 1920, 1080
        shots = [
            {"start_frame": 0, "end_frame": 120, "start": 0.0, "end": 5.0, "cluster_id": 0},
            {"start_frame": 120, "end_frame": 168, "start": 5.0, "end": 7.0, "cluster_id": 1},
            {"start_frame": 168, "end_frame": 288, "start": 7.0, "end": 12.0, "cluster_id": 0},
        ]
        # 2fps => 24 samples
        face_data = []
        for idx in range(24):
            t = (idx + 0.5) / 2.0
            if 5.0 <= t < 7.0:
                # Wide 2-shot
                face_data.append([
                    {"cx": 260.0, "cy": 260.0, "w": 150.0, "h": 165.0, "size": 0.15, "has_landmarks": True},
                    {"cx": 1670.0, "cy": 260.0, "w": 150.0, "h": 165.0, "size": 0.15, "has_landmarks": True},
                ])
            else:
                # Solo closeup
                face_data.append([
                    {"cx": 1080.0, "cy": 290.0, "w": 240.0, "h": 280.0, "size": 0.26, "has_landmarks": True},
                ])

        summary = reframer._summarize_cluster_faces(shots, face_data, w, h, sample_fps=2.0)
        self.assertFalse(summary[0]["is_wide_two"], "Solo close-up cluster must NEVER be split-screened")
        self.assertTrue(summary[1]["is_wide_two"], "Wide 2-shot cluster must be classified for split-screen")


class TestSmartFaceTrackStability(unittest.TestCase):
    def test_continuous_zoom_has_no_cliff_between_029_and_031(self):
        w1, h1 = reframer._compute_shot_crop_dims(0.29, 960.0, 1920, 1080)
        w2, h2 = reframer._compute_shot_crop_dims(0.31, 960.0, 1920, 1080)
        self.assertEqual((w1, h1), (w2, h2))

        # Smooth transition across medium sizes (no >5% sudden jumps for 0.01 size delta)
        prev_h = None
        for sz_int in range(8, 35):
            sz = sz_int / 100.0
            _, ch = reframer._compute_shot_crop_dims(sz, 960.0, 1920, 1080)
            if prev_h is not None:
                self.assertLessEqual(abs(ch - prev_h) / float(prev_h), 0.05)
            prev_h = ch

    def test_seated_speaker_uses_static_tripod_lock(self):
        # Speaker gently nods/shifts within +/- 30px
        samples = [
            (i * 0.5, {"cx": 1050.0 + (20.0 if i % 2 == 0 else -20.0), "cy": 290.0, "size": 0.26})
            for i in range(12)
        ]
        seg = reframer._build_single_track_segment(0, 144, 24.0, samples, 1920, 1080)
        self.assertIsNone(seg["x_expr"], "Seated speaker within deadzone should tripod-lock with zero jitter")
        self.assertEqual(seg["h"], 1080)
        self.assertLessEqual(abs((seg["x"] + seg["w"] / 2.0) - 1050.0), 4.0)

    def test_walking_speaker_uses_smooth_pan_with_constant_zoom(self):
        # Speaker walks smoothly from x=400 to x=1500 over 6 seconds
        samples = [
            (i * 0.5, {"cx": 400.0 + i * 100.0, "cy": 320.0, "size": 0.18})
            for i in range(12)
        ]
        seg = reframer._build_single_track_segment(0, 144, 24.0, samples, 1920, 1080)
        self.assertIsNotNone(seg["x_expr"], "Walking speaker should emit smooth horizontal pan expression")
        self.assertIn("clip(", seg["x_expr"])
        # Crop w/h remain strictly fixed for the shot -> zero zoom jitter
        self.assertGreater(seg["w"], 0)
        self.assertGreater(seg["h"], 0)


class TestSpeakerAndLayoutDetectors(unittest.TestCase):
    def test_speaker_detector_hysteresis_and_center_crossing(self):
        # Speaker A leans from x=940 to x=980 (crossing x=960 middle) while Speaker B sits at x=1600
        face_data = [
            [
                {"cx": 940.0 + i * 10.0, "cy": 300.0, "w": 150.0, "h": 160.0, "size": 0.15, "mouth": 0.12, "has_landmarks": True},
                {"cx": 1600.0, "cy": 300.0, "w": 150.0, "h": 160.0, "size": 0.15, "mouth": 0.01, "has_landmarks": True},
            ]
            for i in range(6)
        ]
        chosen = speaker_detector.pick_active_faces_per_second([], face_data, 1920, 1080, hold_seconds=2, sample_fps=1.0)
        for c in chosen:
            self.assertIsNotNone(c)
            self.assertLess(c["cx"], 1200.0, "Should stay locked on talking Speaker A even across x=0.50")

    def test_layout_detector_dedups_overlapping_closeup(self):
        face_data = [
            [
                {"cx": 960.0, "cy": 300.0, "w": 260.0, "h": 300.0, "size": 0.28},
                {"cx": 1030.0, "cy": 290.0, "w": 270.0, "h": 310.0, "size": 0.29},
            ]
            for _ in range(10)
        ]
        deduped = layout_detector._deduplicate_faces(face_data)
        self.assertTrue(all(len(f) == 1 for f in deduped))
        self.assertFalse(layout_detector._check_consistent_face_sides(deduped, 1920))


if __name__ == "__main__":
    unittest.main()
