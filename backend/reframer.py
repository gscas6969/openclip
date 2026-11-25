"""
reframer.py — Multi-layout 9:16 video reframing engine.

Supports 5 reframing modes (automatic via layout_detector + speaker_detector,
or explicitly forced from the Clip Editor):
  - dynamic_zoom    : Active-speaker face-tracked crop with lip-movement lock
                      and instant hard-cuts on speaker/scene switches (never
                      drifts into the empty center between 2+ people!)
  - podcast_split   : Two-speaker stacked 9:16 split-screen (Speaker A top, Speaker B bottom)
  - podcast_switch  : Full-screen active-speaker switching with hard camera cuts
  - tutorial        : Top screen-share canvas + bottom speaker facecam
  - original        : Blurred 9:16 background with full uncropped 16:9 video centered
"""

import asyncio
import subprocess
import json
import os
import ffbin  # type: ignore
import math
import re
import statistics
import sys
import types
import io
import logging
from typing import Optional, Any
from PIL import Image  # type: ignore

try:
    import cv2  # type: ignore
    _HAS_CV2 = True
except Exception:
    _HAS_CV2 = False
    if "cv2" not in sys.modules:
        _cv2_stub = types.ModuleType("cv2")
        sys.modules["cv2"] = _cv2_stub

import mediapipe as mp  # type: ignore
from mediapipe.tasks import python  # type: ignore
from mediapipe.tasks.python import vision  # type: ignore

import layout_detector  # type: ignore
import speaker_detector  # type: ignore

logger = logging.getLogger(__name__)

_profile_cascade = None
if _HAS_CV2:
    try:
        _cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_profileface.xml")
        if os.path.exists(_cascade_path):
            _profile_cascade = cv2.CascadeClassifier(_cascade_path)
            if _profile_cascade.empty():
                _profile_cascade = None
    except Exception as _exc:
        logger.debug("OpenCV profile cascade unavailable: %s", _exc)
        _profile_cascade = None

LAYOUT_MODES = {
    "auto": "Auto Detect",
    "dynamic_zoom": "Smart Face Track",
    "podcast_split": "Podcast Split-Screen",
    "podcast_switch": "Podcast Active Speaker",
    "tutorial": "Tutorial (Screen + Face)",
    "original": "Original + Blur Fill",
}

model_path = os.path.join(os.path.dirname(__file__), "blaze_face_short_range.tflite")


def _ensure_model_exists():
    if not os.path.exists(model_path):
        import urllib.request

        url = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
        urllib.request.urlretrieve(url, model_path)


_ensure_model_exists()
base_options = python.BaseOptions(model_asset_path=model_path)
# Lower detection threshold (0.28) catches wider/angled studio & podcast speakers reliably
options = vision.FaceDetectorOptions(base_options=base_options, min_detection_confidence=0.28)
face_detector = vision.FaceDetector.create_from_options(options)


def _run(cmd: list[str], timeout: float, text: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{os.path.basename(cmd[0])} took longer than {timeout:.0f}s and was stopped") from exc


async def process_clip(
    video_path: str,
    suggestion: dict[str, Any],
    output_dir: str,
    project_id: str,
    forced_layout_mode: Optional[str] = None,
    output_filename: Optional[str] = None,
) -> dict[str, Any]:
    """Cut and reframe one clip into a clean 1080x1920 vertical MP4 (no captions burned)."""
    os.makedirs(output_dir, exist_ok=True)
    safe_start = f"{float(suggestion['start']):.2f}".replace(".", "_")
    raw_clip = os.path.join(output_dir, f"raw_{project_id}_{safe_start}.mp4")
    clean_name = output_filename or f"clip_{project_id}_{safe_start}_clean.mp4"
    output_clip = os.path.join(output_dir, clean_name)
    duration = float(suggestion["end"]) - float(suggestion["start"])

    needs_user_confirm = False
    chosen_mode = forced_layout_mode or "auto"
    face_count = 0

    try:
        await _cut_raw_clip(video_path, float(suggestion["start"]), float(suggestion["end"]), raw_clip)

        width, height = await asyncio.to_thread(_get_video_dimensions, raw_clip)
        frames = await asyncio.to_thread(_extract_sample_frames, raw_clip)
        face_data = await asyncio.to_thread(_detect_faces_all_frames, frames, width, height)

        layout_info = await asyncio.to_thread(layout_detector.detect_layout, frames, face_data, width, height)
        face_count = int(layout_info.get("face_count", 0))
        needs_user_confirm = bool(layout_info.get("needs_user_confirm", False))

        if chosen_mode in ("auto", "", None):
            detected = layout_info.get("mode", "single")
            if detected == "podcast":
                chosen_mode = "podcast_split"
            elif detected == "panel":
                chosen_mode = "podcast_switch"
            elif detected == "tutorial":
                chosen_mode = "tutorial"
            elif detected == "none":
                chosen_mode = "original"
            else:
                chosen_mode = "dynamic_zoom"

        success = False
        if chosen_mode == "podcast_split":
            success = await _reframe_podcast_split(
                raw_clip, output_clip, width, height, face_data, duration=duration
            )
        elif chosen_mode == "podcast_switch":
            success = await _reframe_podcast_switch(raw_clip, output_clip, width, height, frames, face_data, duration)
        elif chosen_mode == "tutorial":
            success = await _reframe_tutorial(raw_clip, output_clip, width, height, face_data)
        elif chosen_mode == "original":
            success = await _reframe_original_blur(raw_clip, output_clip)
        else:
            chosen_mode = "dynamic_zoom"
            success, face_count = await _reframe_dynamic_zoom(
                raw_clip, output_clip, width, height, face_data, duration, frames=frames
            )

        if not success:
            logger.warning("Layout '%s' failed; falling back to dominant-speaker static crop", chosen_mode)
            await _reframe_static_fallback(raw_clip, output_clip, width, height, face_data)
            chosen_mode = "dynamic_zoom"
    finally:
        if os.path.exists(raw_clip):
            os.remove(raw_clip)

    return {
        "file_path": output_clip,
        "clean_file_path": output_clip,
        "start_time": float(suggestion["start"]),
        "end_time": float(suggestion["end"]),
        "duration": duration,
        "title": suggestion.get("title", ""),
        "reason": suggestion.get("reason", ""),
        "viral_score": suggestion.get("viral_score", 0),
        "face_count": face_count,
        "layout_mode": chosen_mode,
        "needs_user_confirm": needs_user_confirm,
        "reframed": True,
    }


def _extract_sample_frames(video_path: str) -> list[Image.Image]:
    ffmpeg_path = ffbin.ffmpeg()
    result = _run(
        [
            ffmpeg_path,
            "-i", video_path,
            "-vf", "fps=1",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "pipe:1",
        ],
        timeout=300,
    )

    raw = result.stdout
    frames = []
    start = 0
    while True:
        start = raw.find(b"\xff\xd8", start)
        if start == -1:
            break
        end = raw.find(b"\xff\xd9", start)
        if end == -1:
            break
        jpeg_bytes = bytes(raw[start : end + 2])
        try:
            img = Image.open(io.BytesIO(jpeg_bytes))
            frames.append(img)
        except Exception:
            pass
        start = end + 2
    return frames


def _detect_profile_faces_cv2(
    rgb: Any, vid_width: int, vid_height: int
) -> list[dict]:
    """
    Detect 90-degree side-profile speakers using OpenCV's profile Haar cascade
    on both the original image (left-looking/right-side) and horizontally flipped
    image (right-looking/left-side). Filters out low-frame false positives (hands/table).
    """
    if not _HAS_CV2 or _profile_cascade is None:
        return []

    try:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        min_dim = max(36, int(vid_height * 0.06))
        max_dim = max(min_dim + 10, int(vid_height * 0.45))

        raw_candidates: list[dict] = []

        # Original orientation
        dets_orig = _profile_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(min_dim, min_dim),
            maxSize=(max_dim, max_dim),
        )
        for (x, y, w, h) in dets_orig:
            cx = float(x) + float(w) / 2.0
            cy = float(y) + float(h) / 2.0
            size = float(h) / max(vid_height, 1)
            if cy < vid_height * 0.62 and 0.055 <= size <= 0.45:
                raw_candidates.append({"cx": cx, "cy": cy, "w": float(w), "h": float(h), "size": size, "profile": True})

        # Horizontally flipped orientation
        flipped = cv2.flip(gray, 1)
        dets_flip = _profile_cascade.detectMultiScale(
            flipped,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(min_dim, min_dim),
            maxSize=(max_dim, max_dim),
        )
        for (x, y, w, h) in dets_flip:
            cx = float(vid_width) - (float(x) + float(w) / 2.0)
            cy = float(y) + float(h) / 2.0
            size = float(h) / max(vid_height, 1)
            if cy < vid_height * 0.62 and 0.055 <= size <= 0.45:
                raw_candidates.append({"cx": cx, "cy": cy, "w": float(w), "h": float(h), "size": size, "profile": True})

        # Keep strongest (largest) candidate per horizontal half to avoid duplicates/noise
        left_cands = [c for c in raw_candidates if c["cx"] < vid_width * 0.48]
        right_cands = [c for c in raw_candidates if c["cx"] > vid_width * 0.52]
        mid_cands = [c for c in raw_candidates if vid_width * 0.48 <= c["cx"] <= vid_width * 0.52]

        out: list[dict] = []
        if left_cands:
            out.append(max(left_cands, key=lambda c: c["size"]))
        if right_cands:
            out.append(max(right_cands, key=lambda c: c["size"]))
        if not out and mid_cands:
            out.append(max(mid_cands, key=lambda c: c["size"]))
        return out
    except Exception as exc:
        logger.debug("Profile face detection error: %s", exc)
        return []


def _detect_faces_all_frames(
    frames: list[Image.Image], vid_width: int, vid_height: int
) -> list[list[dict]]:
    """
    Triple-pass face detection:
      1. Fast MediaPipe BlazeFace detector (min_detection_confidence=0.28, size>=0.028)
      2. MediaPipe FaceLandmarker pass (478 landmarks + mouth opening score)
      3. OpenCV side-profile Haar cascade fallback whenever <2 frontal faces are
         found (catches 90-degree side-profile podcast guests that frontal models miss!)
    """
    import numpy as np  # type: ignore

    all_faces = []
    for frame in frames:
        rgb = np.array(frame.convert("RGB"))
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        results = face_detector.detect(mp_image)
        faces: list[dict] = []
        if results.detections:
            for det in results.detections:
                bb = det.bounding_box
                cx = bb.origin_x + bb.width / 2.0
                cy = bb.origin_y + bb.height / 2.0
                size = bb.height / max(vid_height, 1)
                if size < 0.028:
                    continue
                faces.append({"cx": cx, "cy": cy, "w": bb.width, "h": bb.height, "size": size})

        # Supplement with FaceLandmarker to catch wider/angled speakers & get mouth openness
        try:
            lm_faces = speaker_detector.detect_faces_with_mouth_scores(frame, vid_width, vid_height)
            for lmf in lm_faces:
                matched = False
                for f in faces:
                    if math.hypot(f["cx"] - lmf["cx"], f["cy"] - lmf["cy"]) < max(55.0, vid_width * 0.06):
                        f["mouth"] = lmf["mouth"]
                        matched = True
                        break
                if not matched:
                    faces.append(lmf)
        except Exception:
            pass

        # Supplement with OpenCV 90-degree side-profile detector on wide shots where
        # frontal models miss side-facing podcast guests (skip if a single large
        # close-up face is already detected to avoid background false positives).
        has_large_closeup = len(faces) == 1 and float(faces[0].get("size", 0.0)) >= 0.24
        if len(faces) < 2 and not has_large_closeup:
            prof_faces = _detect_profile_faces_cv2(rgb, vid_width, vid_height)
            for pf in prof_faces:
                if not any(
                    math.hypot(f["cx"] - pf["cx"], f["cy"] - pf["cy"]) < max(75.0, vid_width * 0.08)
                    for f in faces
                ):
                    faces.append(pf)

        # Deduplicate faces within 55px
        valid_faces: list[dict] = []
        for f in faces:
            if not any(math.hypot(f["cx"] - vf["cx"], f["cy"] - vf["cy"]) < 55 for vf in valid_faces):
                valid_faces.append(f)

        all_faces.append(valid_faces)
    return all_faces


def _ease_in_out(t: float) -> float:
    return 2 * t * t if t < 0.5 else 1 - math.pow(-2 * t + 2, 2) / 2


async def _reframe_original_blur(input_path: str, output_path: str) -> bool:
    """Center the full horizontal frame over a softly blurred 9:16 background."""
    filter_cx = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=25:5,eq=brightness=-0.12[bg];"
        "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[outv]"
    )
    prefix_cmd = [
        ffbin.ffmpeg(), "-y", "-i", input_path,
        "-filter_complex", filter_cx,
        "-map", "[outv]", "-map", "0:a?",
    ]
    suffix_cmd = [
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    res = await asyncio.to_thread(
        ffbin.run_ffmpeg_encode, prefix_cmd, suffix_cmd, quality="standard", timeout=900, text=False
    )
    return res.returncode == 0


def _detect_scene_cuts(video_path: str, duration: float) -> list[float]:
    """Detect exact camera-cut timestamps (in seconds) using FFmpeg scdet."""
    try:
        cmd = [ffbin.ffmpeg(), "-i", video_path, "-vf", "scdet=t=12", "-f", "null", "-"]
        res = _run(cmd, timeout=180, text=True)
        raw_times = [float(m) for m in re.findall(r"lavfi\.scd\.time:\s*([0-9.]+)", res.stderr or "")]
    except Exception as exc:
        logger.debug("Scene cut detection failed: %s", exc)
        return []

    cuts: list[float] = []
    for t in sorted(raw_times):
        if t < 0.45 or (duration > 0 and duration - t < 0.45):
            continue
        if cuts and (t - cuts[-1]) < 0.45:
            continue
        cuts.append(t)
    return cuts


async def _reframe_podcast_split(
    input_path: str,
    output_path: str,
    vid_width: int,
    vid_height: int,
    face_data: list[list[dict]],
    duration: float = 0.0,
) -> bool:
    """
    Podcast Split-Screen (1080x1920):
      - Detects both speakers (including 90-degree side profiles) and computes a
        tight, geometrically centerable crop box around each speaker so BOTH
        people sit dead-center in their respective 1080x960 top/bottom panels
        with natural headroom (head + shoulders + mic).
      - Automatically detects multi-camera cuts between wide 2-person shots and
        single-speaker close-ups: wide shots render as centered 2-up split-screen,
        and single-speaker close-ups render as centered full-screen 9:16 so a
        close-up speaker is never sliced in half across top & bottom panels!
    """
    # 1. Gather wide-shot left/right speaker coordinates using medians (outlier-proof)
    left_xs, left_ys, left_hs = [], [], []
    right_xs, right_ys, right_hs = [], [], []

    # First pass: frames with 2+ opposite-side faces
    for faces in face_data:
        if len(faces) >= 2:
            ordered = sorted(faces, key=lambda f: f["cx"])
            left_f, right_f = ordered[0], ordered[-1]
            if left_f["cx"] < vid_width * 0.48 and right_f["cx"] > vid_width * 0.52:
                left_xs.append(float(left_f["cx"]))
                left_ys.append(float(left_f["cy"]))
                left_hs.append(float(left_f.get("h", vid_height * 0.16)))
                right_xs.append(float(right_f["cx"]))
                right_ys.append(float(right_f["cy"]))
                right_hs.append(float(right_f.get("h", vid_height * 0.16)))

    # Supplement with single-side wide-shot detections (size < 0.21) if needed
    if len(left_xs) < 2 or len(right_xs) < 2:
        for faces in face_data:
            for f in faces:
                if float(f.get("size", 0.0)) >= 0.21:
                    continue
                cx = float(f["cx"])
                cy = float(f["cy"])
                fh = float(f.get("h", vid_height * 0.16))
                if cx < vid_width * 0.45 and len(left_xs) < 20:
                    left_xs.append(cx)
                    left_ys.append(cy)
                    left_hs.append(fh)
                elif cx > vid_width * 0.55 and len(right_xs) < 20:
                    right_xs.append(cx)
                    right_ys.append(cy)
                    right_hs.append(fh)

    left_cx = statistics.median(left_xs) if left_xs else vid_width * 0.25
    left_cy = statistics.median(left_ys) if left_ys else vid_height * 0.40
    left_h = statistics.median(left_hs) if left_hs else vid_height * 0.16
    right_cx = statistics.median(right_xs) if right_xs else vid_width * 0.75
    right_cy = statistics.median(right_ys) if right_ys else vid_height * 0.40
    right_h = statistics.median(right_hs) if right_hs else vid_height * 0.16

    # 2. Compute tight, geometrically centerable crop box for 1080x960 panels.
    #    Crucial: crop_w MUST be <= 2 * min(left_cx, vid_width - right_cx) whenever
    #    possible so neither box clamps to the left/right frame edge and pushes the
    #    speaker off-center!
    target_ar = 1080.0 / 960.0
    median_face_h = statistics.median([left_h, right_h])
    ideal_crop_h = median_face_h * 4.3
    ideal_crop_w = ideal_crop_h * target_ar

    max_centerable_w = min(left_cx * 2.0, (vid_width - right_cx) * 2.0)
    min_crop_w = max(vid_width * 0.26, median_face_h * 2.8 * target_ar)
    hard_max_w = min(vid_width * 0.46, vid_height * target_ar)

    if max_centerable_w >= min_crop_w:
        crop_w_f = min(ideal_crop_w, max_centerable_w)
    else:
        crop_w_f = min(ideal_crop_w, hard_max_w)
    crop_w_f = max(min_crop_w, min(hard_max_w, crop_w_f))

    crop_w = max(2, int(round(crop_w_f)) & ~1)
    crop_h = max(2, min(vid_height, int(round(crop_w / target_ar)) & ~1))
    # Ensure crop_w matches aspect cleanly if crop_h hit vid_height
    if crop_h == vid_height:
        crop_w = max(2, min(vid_width // 2, int(round(crop_h * target_ar)) & ~1))

    def _split_box(cx: float, cy: float, fh: float) -> tuple[int, int]:
        # Rule-of-thirds vertical framing: place face center at ~38% from panel top
        # so there is balanced headroom above the head and room for shoulders + mic.
        anchor_cy = cy + fh * 0.50
        x = int(round(max(0.0, min(cx - crop_w / 2.0, float(vid_width - crop_w)))))
        y = int(round(max(0.0, min(anchor_cy - crop_h / 2.0, float(vid_height - crop_h)))))
        return x, y

    lx, ly = _split_box(left_cx, left_cy, left_h)
    rx, ry = _split_box(right_cx, right_cy, right_h)

    logger.info(
        "podcast_split boxes (%dx%d): left=(cx=%.0f,cy=%.0f)->(%d,%d) right=(cx=%.0f,cy=%.0f)->(%d,%d) box=%dx%d",
        vid_width, vid_height, left_cx, left_cy, lx, ly, right_cx, right_cy, rx, ry, crop_w, crop_h,
    )

    # 3. Multi-camera shot awareness: detect exact camera cuts and switch to
    #    centered full-screen 9:16 during single-speaker close-up shots!
    cuts = await asyncio.to_thread(_detect_scene_cuts, input_path, duration) if duration > 1.5 else []
    boundaries = [0.0] + cuts + [max(duration, float(len(face_data)))]

    raw_segments: list[dict[str, Any]] = []
    for idx in range(len(boundaries) - 1):
        t0, t1 = boundaries[idx], boundaries[idx + 1]
        if t1 - t0 < 0.05:
            continue

        # Collect 1fps face samples inside this shot interval
        shot_frames: list[list[dict]] = []
        for s_idx, faces in enumerate(face_data):
            sample_t = s_idx + 0.5
            if t0 <= sample_t <= t1:
                shot_frames.append(faces)
        if not shot_frames and face_data:
            mid_idx = max(0, min(len(face_data) - 1, int((t0 + t1) / 2.0)))
            shot_frames.append(face_data[mid_idx])

        closeup_faces: list[dict] = []
        wide_two_count = 0
        for faces in shot_frames:
            if len(faces) >= 2:
                ordered = sorted(faces, key=lambda f: f["cx"])
                if ordered[0]["cx"] < vid_width * 0.48 and ordered[-1]["cx"] > vid_width * 0.52:
                    wide_two_count += 1
            elif len(faces) == 1 and float(faces[0].get("size", 0.0)) >= 0.22:
                closeup_faces.append(faces[0])

        is_single_closeup = (
            len(closeup_faces) > 0
            and wide_two_count == 0
            and len(closeup_faces) >= max(1, len(shot_frames) // 2)
        )
        if is_single_closeup:
            raw_segments.append({
                "kind": "single",
                "start": t0,
                "end": t1,
                "cx": statistics.median([float(f["cx"]) for f in closeup_faces]),
                "cy": statistics.median([float(f["cy"]) for f in closeup_faces]),
            })
        else:
            raw_segments.append({"kind": "split", "start": t0, "end": t1})

    # Merge contiguous segments of the same kind (and similar speaker position for single)
    segments: list[dict[str, Any]] = []
    for seg in raw_segments:
        if segments and segments[-1]["kind"] == seg["kind"]:
            if seg["kind"] == "split":
                segments[-1]["end"] = seg["end"]
                continue
            if abs(segments[-1]["cx"] - seg["cx"]) < vid_width * 0.14:
                segments[-1]["end"] = seg["end"]
                segments[-1]["cx"] = (segments[-1]["cx"] + seg["cx"]) / 2.0
                segments[-1]["cy"] = (segments[-1]["cy"] + seg["cy"]) / 2.0
                continue
        segments.append(dict(seg))

    has_split = any(s["kind"] == "split" for s in segments)
    has_single = any(s["kind"] == "single" for s in segments)

    # Fast single-pass graph if entire clip is a single wide split-screen shot
    if not has_single or len(segments) <= 1 and has_split:
        filter_cx = (
            f"[0:v]crop={crop_w}:{crop_h}:{lx}:{ly},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[top];"
            f"[0:v]crop={crop_w}:{crop_h}:{rx}:{ry},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bot];"
            f"[top][bot]vstack=inputs=2,setsar=1[outv]"
        )
    else:
        # Hybrid multi-camera filtergraph: centered split-screen on wide shots +
        # centered 9:16 portrait close-up on single-speaker close-up shots!
        sc_h = vid_height
        sc_w = max(2, int(round(sc_h * 9.0 / 16.0)) & ~1)
        if sc_w > vid_width:
            sc_w = vid_width & ~1
            sc_h = max(2, min(vid_height, int(round(sc_w * 16.0 / 9.0)) & ~1))

        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for idx, seg in enumerate(segments):
            is_last = idx == len(segments) - 1
            trim_expr = (
                f"trim=start={seg['start']:.3f},setpts=PTS-STARTPTS"
                if is_last
                else f"trim=start={seg['start']:.3f}:end={seg['end']:.3f},setpts=PTS-STARTPTS"
            )
            if seg["kind"] == "split":
                filter_parts.append(
                    f"[0:v]{trim_expr},split=2[s{idx}a][s{idx}b];"
                    f"[s{idx}a]crop={crop_w}:{crop_h}:{lx}:{ly},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[t{idx}];"
                    f"[s{idx}b]crop={crop_w}:{crop_h}:{rx}:{ry},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[b{idx}];"
                    f"[t{idx}][b{idx}]vstack=inputs=2,setsar=1[v{idx}]"
                )
            else:
                sx = int(round(max(0.0, min(seg["cx"] - sc_w / 2.0, float(vid_width - sc_w)))))
                sy = int(round(max(0.0, min(seg["cy"] - sc_h * 0.40, float(vid_height - sc_h)))))
                filter_parts.append(
                    f"[0:v]{trim_expr},crop={sc_w}:{sc_h}:{sx}:{sy},"
                    f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v{idx}]"
                )
            concat_inputs.append(f"[v{idx}]")

        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(segments)}:v=1:a=0[outv]")
        filter_cx = ";".join(filter_parts)

    prefix_cmd = [
        ffbin.ffmpeg(), "-y", "-i", input_path,
        "-filter_complex", filter_cx,
        "-map", "[outv]", "-map", "0:a?",
    ]
    suffix_cmd = [
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    res = await asyncio.to_thread(
        ffbin.run_ffmpeg_encode, prefix_cmd, suffix_cmd, quality="standard", timeout=900, text=False
    )
    if res.returncode != 0:
        logger.error("podcast_split FFmpeg failed: %s", res.stderr.decode(errors="replace")[-1200:])
        return False
    return True


async def _reframe_podcast_switch(
    input_path: str,
    output_path: str,
    vid_width: int,
    vid_height: int,
    frames: list[Image.Image],
    face_data: list[list[dict]],
    duration: float,
) -> bool:
    """Full-screen active-speaker camera switching with lip tracking and hard cuts."""
    ok, _ = await _reframe_dynamic_zoom(
        input_path, output_path, vid_width, vid_height, face_data, duration, frames=frames
    )
    return ok


async def _reframe_tutorial(
    input_path: str,
    output_path: str,
    vid_width: int,
    vid_height: int,
    face_data: list[list[dict]],
) -> bool:
    """Top 1080x1180 screen capture + bottom 1080x740 speaker facecam."""
    all_faces = [f for sec in face_data for f in sec]
    if all_faces:
        # Pick dominant face cluster so two faces don't average into the middle
        dom_cx, dom_cy, dom_h = _dominant_face_cluster(all_faces, vid_width, vid_height)
    else:
        dom_cx, dom_cy, dom_h = vid_width * 0.85, vid_height * 0.78, vid_height * 0.22

    face_ar = 1080.0 / 740.0
    fc_h = int(max(vid_height * 0.3, min(vid_height * 0.6, dom_h * 2.6)))
    fc_w = int(fc_h * face_ar)
    if fc_w > vid_width:
        fc_w = vid_width
        fc_h = int(fc_w / face_ar)
    fx = int(max(0, min(dom_cx - fc_w / 2, vid_width - fc_w)))
    fy = int(max(0, min(dom_cy - fc_h / 2, vid_height - fc_h)))

    filter_cx = (
        f"[0:v]scale=1080:1180:force_original_aspect_ratio=decrease,"
        f"pad=1080:1180:(ow-iw)/2:(oh-ih)/2:color=#0a0a0a[top];"
        f"[0:v]crop={fc_w}:{fc_h}:{fx}:{fy},scale=1080:740:force_original_aspect_ratio=increase,crop=1080:740[bot];"
        f"[top][bot]vstack=inputs=2,setsar=1[outv]"
    )
    prefix_cmd = [
        ffbin.ffmpeg(), "-y", "-i", input_path,
        "-filter_complex", filter_cx,
        "-map", "[outv]", "-map", "0:a?",
    ]
    suffix_cmd = [
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    res = await asyncio.to_thread(
        ffbin.run_ffmpeg_encode, prefix_cmd, suffix_cmd, quality="standard", timeout=900, text=False
    )
    return res.returncode == 0


def _dominant_face_cluster(
    all_faces: list[dict], vid_width: int, vid_height: int
) -> tuple[float, float, float]:
    """Pick the most populated horizontal region instead of averaging opposite speakers."""
    if not all_faces:
        return vid_width / 2.0, vid_height * 0.45, vid_height * 0.2
    buckets: dict[int, list[dict]] = {}
    for f in all_faces:
        b = int(min(3, max(0, (f["cx"] / max(vid_width, 1)) * 4)))
        buckets.setdefault(b, []).append(f)
    best_bucket = max(buckets.values(), key=len)
    cx = sum(f["cx"] for f in best_bucket) / len(best_bucket)
    cy = sum(f["cy"] for f in best_bucket) / len(best_bucket)
    h = sum(f.get("h", vid_height * 0.2) for f in best_bucket) / len(best_bucket)
    return cx, cy, h


async def _reframe_dynamic_zoom(
    input_path: str,
    output_path: str,
    vid_width: int,
    vid_height: int,
    face_data: list[list[dict]],
    duration: float,
    frames: Optional[list[Image.Image]] = None,
) -> tuple[bool, int]:
    """
    Active-speaker dynamic 9:16 reframer:
      - When 2+ people are in frame, uses lip-movement active-speaker lock
        (pick_active_faces_per_second) so it locks onto who is talking.
      - Uses HARD CUTS when switching speakers or camera angles (jump > 20%
        of frame width) so the crop NEVER pans through the empty center wall!
      - Uses smooth EMA tracking only for subtle head movement of the same person.
    """
    fps = 30
    total_frames = max(1, int(duration * fps))

    non_empty_counts = [len(f) for f in face_data if len(f) > 0]
    overall_face_count = max(set(non_empty_counts), key=non_empty_counts.count) if non_empty_counts else 0

    # Pick ONE active speaker face per second using lip activity + hysteresis
    active_faces = speaker_detector.pick_active_faces_per_second(
        frames or [], face_data, vid_width, vid_height, hold_seconds=2
    )

    # Backfill initial empty seconds with the first detected speaker so the clip
    # doesn't start on an empty center frame before the first face is spotted
    first_valid = next((f for f in active_faces if f is not None), None)

    sec_targets = []
    total_secs = max(1, math.ceil(duration))
    for i in range(len(active_faces)):
        face = active_faces[i]
        if face is not None:
            size = float(face.get("size", 0.15))
            if size < 0.08:
                zoom_target = 1.9
            elif size < 0.16:
                zoom_target = 1.55
            elif size < 0.30:
                zoom_target = 1.25
            else:
                zoom_target = 1.05
            sec_targets.append({"has_face": True, "zoom": zoom_target, "cx": face["cx"], "cy": face["cy"]})
        elif first_valid is not None and not sec_targets:
            sec_targets.append({"has_face": True, "zoom": 1.25, "cx": first_valid["cx"], "cy": first_valid["cy"]})
        else:
            sec_targets.append({"has_face": False, "zoom": 1.0, "cx": vid_width / 2.0, "cy": vid_height / 2.0})

    while len(sec_targets) < total_secs:
        sec_targets.append(
            sec_targets[-1] if sec_targets else {"has_face": False, "zoom": 1.0, "cx": vid_width / 2.0, "cy": vid_height / 2.0}
        )

    frame_crops = []
    last_face_zoom = sec_targets[0]["zoom"]
    last_face_cx = sec_targets[0]["cx"]
    last_face_cy = sec_targets[0]["cy"]

    frames_since_face_lost = 0
    # Hold the last speaker's framing for 4 seconds on brief occlusions / turns
    # so we don't immediately slide into the empty center between two people.
    ZOOM_OUT_HOLD = int(4 * fps)
    ZOOM_OUT_DUR = int(1 * fps)
    NO_FACE_ZOOM_RETAIN = 0.85

    smooth_zoom = last_face_zoom
    smooth_cx = last_face_cx
    smooth_cy = last_face_cy
    EMA_ALPHA = 0.10
    HARD_CUT_THRESHOLD_PX = vid_width * 0.20

    for f_idx in range(total_frames):
        sec = min(int(f_idx / fps), total_secs - 1)
        target = sec_targets[sec]

        if target["has_face"]:
            frames_since_face_lost = 0
            desired_zoom = target["zoom"]
            desired_cx = target["cx"]
            desired_cy = target["cy"]

            # Hard cut when switching speakers or camera angle (no empty-center pan!)
            if abs(desired_cx - smooth_cx) > HARD_CUT_THRESHOLD_PX:
                smooth_cx = desired_cx
                smooth_cy = desired_cy
                smooth_zoom = desired_zoom

            last_face_zoom = desired_zoom
            last_face_cx = desired_cx
            last_face_cy = desired_cy
        else:
            frames_since_face_lost += 1
            if frames_since_face_lost > ZOOM_OUT_HOLD and overall_face_count <= 1:
                progress = min(1.0, (frames_since_face_lost - ZOOM_OUT_HOLD) / float(ZOOM_OUT_DUR))
                eased = _ease_in_out(progress)
                target_zoom = 1.0 + (last_face_zoom - 1.0) * NO_FACE_ZOOM_RETAIN
                desired_zoom = last_face_zoom + (target_zoom - last_face_zoom) * eased
                desired_cx = last_face_cx + (vid_width / 2.0 - last_face_cx) * eased
                desired_cy = last_face_cy + (vid_height / 2.0 - last_face_cy) * eased
            else:
                # In multi-speaker clips or brief pauses, STAY on the last speaker!
                desired_zoom = last_face_zoom
                desired_cx = last_face_cx
                desired_cy = last_face_cy

        smooth_zoom += (desired_zoom - smooth_zoom) * EMA_ALPHA
        smooth_cx += (desired_cx - smooth_cx) * EMA_ALPHA
        smooth_cy += (desired_cy - smooth_cy) * EMA_ALPHA

        crop_h = float(vid_height) / max(smooth_zoom, 1.0)
        crop_w = crop_h * 9.0 / 16.0

        if crop_w > vid_width:
            crop_w = float(vid_width)
            crop_h = crop_w * 16.0 / 9.0
            if crop_h > vid_height:
                crop_h = float(vid_height)
            crop_w = crop_h * 9.0 / 16.0

        crop_x = max(0.0, min(smooth_cx - crop_w / 2.0, float(vid_width) - crop_w))
        crop_y = max(0.0, min(smooth_cy - crop_h / 2.0, float(vid_height) - crop_h))

        frame_crops.append({
            "ts": f_idx / float(fps),
            "w": max(2, int(crop_w) & ~1),
            "h": max(2, int(crop_h) & ~1),
            "x": int(crop_x),
            "y": int(crop_y),
        })

    cmd_file = input_path + ".sendcmd"
    lines = [
        f"{c['ts']:.3f} crop w {c['w']}, crop h {c['h']}, crop x {c['x']}, crop y {c['y']};"
        for c in frame_crops
    ]
    with open(cmd_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    filter_cmd_file = cmd_file.replace("\\", "/").replace(":", "\\:")
    init_c = frame_crops[0]
    filter_cx = (
        f"sendcmd=f='{filter_cmd_file}',"
        f"crop={init_c['w']}:{init_c['h']}:{init_c['x']}:{init_c['y']},"
        f"scale=1080:1920:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )

    prefix_cmd = [
        ffbin.ffmpeg(), "-y", "-i", input_path,
        "-vf", filter_cx,
    ]
    suffix_cmd = [
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]

    try:
        result = await asyncio.to_thread(
            ffbin.run_ffmpeg_encode, prefix_cmd, suffix_cmd, quality="standard", timeout=900, text=False
        )
    except RuntimeError as exc:
        logger.error("FFmpeg dynamic sendcmd reframe failed: %s", exc)
        return False, overall_face_count
    finally:
        if os.path.exists(cmd_file):
            os.remove(cmd_file)

    if result.returncode != 0:
        logger.error("FFmpeg dynamic sendcmd reframe failed: %s", result.stderr.decode(errors="replace")[-1500:])
        return False, overall_face_count

    return True, overall_face_count


async def _reframe_static_fallback(
    input_path: str,
    output_path: str,
    vid_width: int,
    vid_height: int,
    face_data: list[list[dict]],
) -> None:
    """Fallback static crop anchored on the dominant speaker cluster (never empty center)."""
    all_faces = [f for sec_data in face_data for f in sec_data]
    dom_cx, _, _ = _dominant_face_cluster(all_faces, vid_width, vid_height)

    crop_h = float(vid_height)
    crop_w = crop_h * 9.0 / 16.0
    if crop_w > vid_width:
        crop_w = float(vid_width)
        crop_h = crop_w * 16.0 / 9.0

    crop_x = max(0.0, min(dom_cx - crop_w / 2.0, float(vid_width) - crop_w))
    crop_y = (vid_height - crop_h) / 2.0

    filter_cx = (
        f"crop={int(crop_w)}:{int(crop_h)}:{int(crop_x)}:{int(crop_y)},"
        f"scale=1080:1920:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    prefix_cmd = [
        ffbin.ffmpeg(), "-y", "-i", input_path,
        "-vf", filter_cx,
    ]
    suffix_cmd = [
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    result = await asyncio.to_thread(
        ffbin.run_ffmpeg_encode, prefix_cmd, suffix_cmd, quality="standard", timeout=900, text=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg static fallback reframe failed: {result.stderr.decode(errors='replace')[-500:]}")


def _get_video_dimensions(video_path: str) -> tuple[int, int]:
    cmd = [
        ffbin.ffprobe(),
        "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", video_path,
    ]
    result = _run(cmd, timeout=60, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"])


async def _cut_raw_clip(video_path: str, start: float, end: float, output_path: str) -> None:
    """
    Frame-accurate cut starting at exact ``start`` seconds.
    Avoids `-c copy` keyframe snapping which shifts audio/video ahead of
    word-level caption timestamps by 0.2s-1.5s, and applies a subtle micro
    audio fade-in/out to eliminate harsh cut clicks at clip boundaries.
    """
    duration = max(0.5, end - start)
    base_cmd = [
        ffbin.ffmpeg(), "-y",
        "-ss", f"{start:.3f}",
        "-i", video_path,
        "-t", f"{duration:.3f}",
    ]
    tail_cmd = [
        "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]

    if duration > 1.0:
        fade_out_d = 0.08
        fade_out_st = max(0.0, duration - fade_out_d)
        af = f"asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.025,afade=t=out:st={fade_out_st:.3f}:d={fade_out_d:.3f}"
        result = await asyncio.to_thread(
            ffbin.run_ffmpeg_encode,
            base_cmd,
            ["-af", af, *tail_cmd],
            quality="fast",
            timeout=300,
            text=True,
        )
        if result.returncode == 0:
            return
        # Fallback without audio filter (e.g. silent video with no audio stream)
        logger.debug("Raw clip cut with audio fade failed, retrying without -af: %s", result.stderr[-250:])

    result = await asyncio.to_thread(
        ffbin.run_ffmpeg_encode,
        base_cmd,
        tail_cmd,
        quality="fast",
        timeout=300,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed cutting raw clip: {result.stderr[-500:]}")
