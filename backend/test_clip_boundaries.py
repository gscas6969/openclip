"""
Unit & integration tests for abrupt-ending fixes:
  - JSON3 rolling-caption de-overlapping & short segment grouping
  - Long cached transcript splitting for LLM prompts
  - Decimal-second prompt formatting & chunk overlap
  - Transcript sentence/pause boundary snapping (mid-sentence, over-max, under-min, unpunctuated pauses)
  - Real FFmpeg audio waveform end refinement + micro audio fade-out in reframer._cut_raw_clip
"""

import asyncio
import json
import os
import subprocess
import tempfile
import unittest

import clip_boundaries
import ffbin
import llm
import reframer
import transcriber


def _make_words(sentence_specs):
    """
    Build transcript segments from a list of (start, words_list, word_dur, gap_after_last).
    """
    segments = []
    for seg_start, tokens, word_dur in sentence_specs:
        words = []
        t = seg_start
        for tok in tokens:
            words.append({"word": tok, "start": round(t, 3), "end": round(t + word_dur, 3)})
            t += word_dur + 0.04
        segments.append({
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "text": " ".join(tokens),
            "words": words,
        })
    return segments


class TestClipEndings(unittest.TestCase):
    def test_json3_does_not_merge_rolling_captions_into_giant_blob(self):
        """YouTube JSON3 auto-captions have 5s rolling dDurationMs holds; parser must keep segments short and preserve pauses."""
        events = []
        # 15 rolling events spanning 0..45s with a 1.5s pause at t=20s
        for i in range(15):
            base_ms = i * 2600 + (1500 if i >= 8 else 0)
            events.append({
                "tStartMs": base_ms,
                "dDurationMs": 5500,  # Typical YouTube rolling hold (overlaps next event!)
                "segs": [
                    {"utf8": f"word{i}a", "tOffsetMs": 0},
                    {"utf8": f" word{i}b", "tOffsetMs": 400},
                    {"utf8": f" word{i}c.", "tOffsetMs": 800},
                    {"utf8": "\n"},
                ],
            })

        with tempfile.NamedTemporaryFile("w", suffix=".json3", delete=False, encoding="utf-8") as f:
            json.dump({"events": events}, f)
            path = f.name

        try:
            segs = transcriber._parse_json3(path)
            self.assertGreaterEqual(len(segs), 8, "Rolling captions should not collapse into 1 giant segment")
            for seg in segs:
                self.assertLessEqual(seg["end"] - seg["start"], 7.0)
            # Ensure the 1.5s pause before event 8 is preserved (not smeared by dDurationMs=5500)
            all_words = [w for s in segs for w in s["words"]]
            idx_ev8 = next(idx for idx, w in enumerate(all_words) if w["word"] == "word8a")
            gap = all_words[idx_ev8]["start"] - all_words[idx_ev8 - 1]["end"]
            self.assertGreater(gap, 1.0, f"Pause before event 8 was smeared away (gap={gap:.3f}s)")
        finally:
            os.remove(path)

    def test_prepare_segments_splits_legacy_giant_segment(self):
        """Cached giant segments with words should be broken into short timestamped lines for the LLM."""
        words = []
        t = 0.0
        for i in range(100):
            tok = f"token{i}." if (i + 1) % 10 == 0 else f"token{i}"
            words.append({"word": tok, "start": round(t, 3), "end": round(t + 0.3, 3)})
            t += 0.35 + (0.6 if (i + 1) % 10 == 0 else 0.0)

        giant = [{"start": 0.0, "end": words[-1]["end"], "text": " ".join(w["word"] for w in words), "words": words}]
        prepared = clip_boundaries.prepare_segments_for_llm(giant)
        self.assertGreaterEqual(len(prepared), 8)
        for seg in prepared:
            self.assertLessEqual(seg["end"] - seg["start"], 7.5)

    def test_mid_sentence_end_snaps_forward_to_sentence_completion(self):
        """If LLM ends mid-sentence at 42.0s and sentence finishes at 44.1s, snap to complete sentence + tail."""
        transcript = _make_words([
            (10.0, ["Here", "is", "the", "crazy", "story."], 0.35),
            (13.0, ["We", "built", "something", "wild", "that", "nobody", "expected."], 0.35),
            (38.0, ["And", "that", "is", "the", "exact", "reason", "why", "everything", "changed", "forever", "right", "there."], 0.45),
            (46.0, ["Now", "let", "us", "move", "to", "topic", "two."], 0.35),
        ])
        # Last word "there." finishes around 38.0 + 11*0.49 + 0.45 = 43.84s; next sentence starts at 46.0s
        st, en = clip_boundaries.snap_clip_boundaries(
            start=10.0, end=41.5, transcript=transcript,
            video_duration=120.0, min_dur=22.0, target_min=30.0, target_max=60.0,
        )
        sent_end = transcript[2]["words"][-1]["end"]
        self.assertGreaterEqual(en, sent_end, "Clip cut off before the closing sentence finished")
        self.assertLess(en, 45.95, "Clip bled into the next topic sentence")

    def test_over_max_duration_trims_to_sentence_boundary_not_blind_chop(self):
        """A 75s suggestion when max_duration=60s must trim to a clean sentence end rather than chopping mid-sentence at 70.0s."""
        transcript = _make_words([
            (10.0, ["Opening", "hook", "right", "here."], 0.4),
            (63.0, ["This", "sentence", "ends", "cleanly", "before", "sixty."], 0.4),  # ends ~65.8s (dur 55.8s)
            (67.2, ["This", "new", "long", "sentence", "runs", "right", "across", "the", "seventy", "second", "mark", "and", "beyond."], 0.45),
            (76.0, ["Later", "topic", "starts", "here."], 0.4),
        ])
        st, en = clip_boundaries.snap_clip_boundaries(
            start=10.0, end=85.0, transcript=transcript,
            video_duration=120.0, min_dur=22.0, target_min=30.0, target_max=60.0,
        )
        clean_end = transcript[1]["words"][-1]["end"]
        # Should snap right after "sixty." (~65.8s + tail ~66.1s), NOT blindly chop at 70.0s mid-sentence!
        self.assertGreaterEqual(en, clean_end)
        self.assertLess(en, 67.15, f"Expected trim to clean sentence end ~66.1s, got {en}s")

    def test_under_min_duration_extends_to_sentence_end_not_mid_sentence(self):
        """A 25s clip when target_min=30s should extend cleanly to a sentence end instead of blindly cutting at +30s."""
        transcript = _make_words([
            (10.0, ["Strong", "opening", "hook."], 0.4),
            (32.0, ["First", "complete", "point."], 0.4),  # ends ~33.3s (dur 23.3s)
            (35.0, ["And", "here", "is", "the", "second", "complete", "point", "that", "wraps", "it", "up", "nicely."], 0.5),  # ends ~41.5s
            (43.5, ["Completely", "different", "subject", "now."], 0.4),
        ])
        st, en = clip_boundaries.snap_clip_boundaries(
            start=10.0, end=33.5, transcript=transcript,
            video_duration=120.0, min_dur=22.0, target_min=30.0, target_max=60.0,
        )
        second_end = transcript[2]["words"][-1]["end"]
        # Blind extension would stop at 40.0s right in the middle of "wraps it up nicely."!
        self.assertGreaterEqual(en, second_end, f"Should finish second sentence ({second_end}s), got {en}s")
        self.assertLess(en, 43.45, f"Should not bleed into next subject at 43.5s, got {en}s")

    def test_unpunctuated_auto_captions_snap_to_pause_gap(self):
        """Auto-captions often lack punctuation; snapping must use natural pause gaps."""
        transcript = _make_words([
            (0.0, ["so", "check", "this", "out"], 0.35),
            (31.0, ["and", "that", "is", "how", "you", "win"], 0.4),  # ends ~33.6s
            (34.8, ["okay", "moving", "on", "to", "the", "next", "part"], 0.4),
        ])
        st, en = clip_boundaries.snap_clip_boundaries(
            start=0.0, end=32.5, transcript=transcript,
            video_duration=120.0, min_dur=22.0, target_min=30.0, target_max=60.0,
        )
        win_end = transcript[1]["words"][-1]["end"]
        self.assertGreaterEqual(en, win_end)
        self.assertLess(en, 34.75)

    def test_chunk_transcript_overlap_and_decimal_format(self):
        segs = [{"start": float(i * 5), "end": float(i * 5 + 4.7), "text": f"line {i}"} for i in range(120)]
        chunks = llm._chunk_transcript(segs, chunk_minutes=8, overlap=60)
        self.assertEqual(len(chunks), 2)
        # Chunk 0 should reach 480 + 60 = 540s so a 60s clip starting at 475s isn't cut off!
        self.assertGreaterEqual(chunks[0][-1]["end"], 535.0)
        formatted = llm._format_transcript(segs[:2])
        self.assertIn("[0.0s-4.7s]", formatted)
        self.assertIn("[5.0s-9.7s]", formatted)

    def test_audio_end_refinement_and_micro_fade_with_ffmpeg(self):
        """Generate a synthetic video with tone burst (0..5.2s) -> silence (5.2..6.2s) -> tone burst (6.2..9.0s)."""
        ffmpeg = ffbin.ffmpeg()
        with tempfile.TemporaryDirectory() as tmp:
            vid = os.path.join(tmp, "sample.mp4")
            cut_out = os.path.join(tmp, "cut.mp4")
            # Build 9s test clip: audible speech-like tone 0..5.2s, silence 5.2..6.2s, tone 6.2..9.0s
            cmd = [
                ffmpeg, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:d=9:r=30",
                "-f", "lavfi", "-i",
                "aevalsrc='if(between(t,5.2,6.2),0,0.4*sin(2*PI*300*t))':d=9:s=16000",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                vid,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Synthetic video creation failed: {res.stderr}")

            # Case 1: end=4.6s is mid-tone -> should extend to pause after 5.2s and stay before 6.2s
            refined = clip_boundaries.refine_end_with_audio(vid, start=0.0, end=4.6, video_duration=9.0, max_end=8.5)
            self.assertGreaterEqual(refined, 5.25, f"Expected extension into pause after 5.2s, got {refined}")
            self.assertLess(refined, 6.15, f"Expected cut before speech resumes at 6.2s, got {refined}")

            # Case 2: end=5.6s is already inside silence -> should stay put
            refined_quiet = clip_boundaries.refine_end_with_audio(vid, start=0.0, end=5.6, video_duration=9.0, max_end=8.5)
            self.assertAlmostEqual(refined_quiet, 5.6, places=2)

            # Case 3: reframer._cut_raw_clip runs cleanly with micro fade-in/out
            asyncio.run(reframer._cut_raw_clip(vid, 0.5, refined, cut_out))
            self.assertTrue(os.path.isfile(cut_out) and os.path.getsize(cut_out) > 1000)


if __name__ == "__main__":
    unittest.main()
