"""
Unit & FFmpeg integration tests for livestream highlight signals and /live/ URL support:
  - /live/<id> URL extraction in transcriber._extract_video_id
  - YouTube Most Replayed heatmap peak detection
  - Live Chat replay NDJSON spike & humor keyword burst detection (with pre-roll offset)
  - Audio hype/laughter RMS burst detection (unit + real synthetic FFmpeg video)
  - Multi-signal hotspot fusion, chunk prompt injection, and candidate enrichment
"""

import json
import os
import subprocess
import tempfile
import unittest

import numpy as np

import ffbin
import highlight_signals as hs
import llm
import transcriber


def _chat_line(offset_ms: int, text: str) -> str:
    return json.dumps({
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset_ms),
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatTextMessageRenderer": {
                                "message": {"runs": [{"text": text}]}
                            }
                        }
                    }
                }
            ],
        }
    })


class TestHighlightSignals(unittest.TestCase):
    def test_extract_video_id_supports_live_urls(self):
        self.assertEqual(
            transcriber._extract_video_id("https://www.youtube.com/live/dQw4w9WgXcQ?si=abc"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            transcriber._extract_video_id("https://youtube.com/live/abc123XYZ09"),
            "abc123XYZ09",
        )
        self.assertEqual(
            transcriber._extract_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_heatmap_peak_detection(self):
        heatmap = [
            {"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": 0.95 if i in (12, 13) else 0.25}
            for i in range(30)
        ]
        peaks = hs.parse_heatmap_peaks(heatmap, video_duration=300.0)
        self.assertEqual(len(peaks), 1)
        self.assertAlmostEqual(peaks[0]["start"], 120.0)
        self.assertAlmostEqual(peaks[0]["end"], 140.0)
        self.assertIn("95%", peaks[0]["label"])

    def test_live_chat_spike_and_humor_keywords(self):
        lines = []
        # Steady baseline: 2 normal messages every 10s across 0..200s
        for sec in range(0, 200, 5):
            lines.append(_chat_line(sec * 1000, "hello chat nice stream"))
        # Massive funny burst at 120..128s
        for idx in range(18):
            msg = "LMAOOOO CLIP IT 💀" if idx % 2 == 0 else "hahahaha no way bro 😂"
            lines.append(_chat_line((120 + (idx % 8)) * 1000, msg))

        with tempfile.NamedTemporaryFile("w", suffix=".live_chat.json", delete=False, encoding="utf-8") as f:
            f.write("\n".join(lines))
            path = f.name

        try:
            spikes = hs.parse_live_chat_spikes(path, video_duration=200.0)
            self.assertEqual(len(spikes), 1)
            sp = spikes[0]
            # Window should be shifted 12s before the 120s chat burst to capture setup + joke
            self.assertLessEqual(sp["start"], 110.0)
            self.assertGreaterEqual(sp["end"], 130.0)
            self.assertGreaterEqual(sp["ratio"], 4.0)
            self.assertTrue(any(k in sp["top_keywords"] for k in ("lmao", "haha", "clip it", "emoji-laugh")))
        finally:
            os.remove(path)

    def test_audio_peaks_real_ffmpeg_video(self):
        """Synthetic 40s video with steady moderate volume and a 4s loud laughter/hype burst at 22..26s."""
        ffmpeg = ffbin.ffmpeg()
        with tempfile.TemporaryDirectory() as tmp:
            vid = os.path.join(tmp, "vod.mp4")
            cmd = [
                ffmpeg, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=160x90:d=40:r=10",
                "-f", "lavfi", "-i",
                "aevalsrc='if(between(t,22,26),0.85,0.12)*sin(2*PI*320*t)':d=40:s=16000",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                vid,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)

            peaks = hs._extract_audio_peaks(vid, video_duration=40.0)
            self.assertGreaterEqual(len(peaks), 1)
            self.assertTrue(
                any(p["start"] <= 22.0 and p["end"] >= 25.0 for p in peaks),
                f"Expected audio peak covering 22..26s, got {peaks}",
            )

    def test_fusion_prompt_injection_and_candidate_enrichment(self):
        fused = hs.fuse_hotspots(
            heatmap_peaks=[{"start": 115.0, "end": 135.0, "value": 0.92, "label": "MOST REPLAYED (92%)"}],
            chat_spikes=[{"start": 108.0, "end": 132.0, "ratio": 5.0, "top_keywords": ["lmao"], "label": "CHAT SPIKE (5.0x: lmao)"}],
            audio_peaks=[{"start": 114.0, "end": 128.0, "zscore": 3.5, "label": "AUDIO HYPE/LAUGHTER (+3.5σ)"}],
        )
        self.assertEqual(len(fused), 1)
        self.assertEqual(set(fused[0]["sources"]), {"most_replayed", "chat_spike", "audio_hype"})

        signals = {"was_live": True, "hotspots": fused}
        chunk = [
            {"start": 100.0, "end": 105.0, "text": "Normal setup line."},
            {"start": 116.0, "end": 122.0, "text": "Wait did he really just say that?"},
        ]
        prompt = llm._build_chunk_prompt(chunk, 0, 6, highlight_signals=signals)
        self.assertIn("LIVESTREAM VOD", prompt)
        self.assertIn("AUDIENCE & AUDIO HIGHLIGHT SIGNALS", prompt)
        self.assertIn("[HOTSPOT:", prompt)
        self.assertIn("return []", prompt)

        enriched = hs.enrich_candidates_with_signals(
            [{"start": 105.0, "end": 140.0, "title": "Hilarious Moment", "viral_score": 8}],
            signals,
        )
        self.assertGreater(enriched[0]["signal_boost"], 0.0)
        formatted_cand = llm._format_candidates(enriched)
        self.assertIn("[SIGNALS:", formatted_cand)


if __name__ == "__main__":
    unittest.main()
