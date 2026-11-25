"""
reframer.py — Multi-layout 9:16 video reframing engine.

Supports 5 reframing modes (automatic via layout_detector + speaker_detector,
or explicitly forced from the Clip Editor):
  - dynamic_zoom    : Shot-aware Smart Face Track with lip-movement active-speaker
                      lock, camera-cut frame alignment, locked per-shot zoom (zero
                      zoom jitter), and deadzone tripod stabilization
  - podcast_split   : Two-speaker stacked 9:16 split-screen (Speaker A top,
                      Speaker B bottom) on true wide 2-shots, automatically
                      switching to centered full-screen 9:16 on single-speaker
                      cameras so a solo speaker is never split-screened
  - podcast_switch  : Full-screen active-speaker switching with exact hard cuts
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
import numpy as np  # type: ignore

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
_clahe = None
_GAMMA_LUT = np.array([min(255, int(round(((k / 255.0) ** 0.72) * 255.0))) for k in range(256)], dtype=np.uint8)

if _HAS_CV2:
    try:
        _cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_profileface.xml")
        if os.path.exists(_cascade_path):
            _profile_cascade = cv2.CascadeClassifier(_cascade_path)
            if _profile_cascade.empty():
                _profile_cascade = None
        _clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
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
# 0.52 confidence eliminates false positives on leather chairs, chests, hands, and fireplaces
options = vision.FaceDetectorOptions(base_options=base_options, min_detection_confidence=0.52)
face_detector = vision.FaceDetector.create_from_options(options)

DEFAULT_SAMPLE_FPS = 2.0


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

        width, height, fps, total_frames = await asyncio.to_thread(_get_video_info, raw_clip)
        sample_fps = DEFAULT_SAMPLE_FPS
        frames = await asyncio.to_thread(_extract_sample_frames, raw_clip, sample_fps)
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

        shot_analysis = None
        if chosen_mode in ("podcast_split", "podcast_switch", "dynamic_zoom"):
            shot_analysis = await asyncio.to_thread(_analyze_video_shots, raw_clip, fps, duration, total_frames)

        success = False
        if chosen_mode == "podcast_split":
            success = await _reframe_podcast_split(
                raw_clip,
                output_clip,
                width,
                height,
                face_data,
                duration=duration,
                fps=fps,
                sample_fps=sample_fps,
                shot_analysis=shot_analysis,
            )
        elif chosen_mode == "podcast_switch":
            success = await _reframe_podcast_switch(
                raw_clip,
                output_clip,
                width,
                height,
                frames,
                face_data,
                duration,
                fps=fps,
                sample_fps=sample_fps,
                shot_analysis=shot_analysis,
            )
        elif chosen_mode == "tutorial":
            success = await _reframe_tutorial(raw_clip, output_clip, width, height, face_data)
        elif chosen_mode == "original":
            success = await _reframe_original_blur(raw_clip, output_clip)
        else:
            chosen_mode = "dynamic_zoom"
            success, face_count = await _reframe_dynamic_zoom(
                raw_clip,
                output_clip,
                width,
                height,
                face_data,
                duration,
                frames=frames,
                fps=fps,
                sample_fps=sample_fps,
                shot_analysis=shot_analysis,
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


def _extract_sample_frames(video_path: str, sample_fps: float = DEFAULT_SAMPLE_FPS) -> list[Image.Image]:
    ffmpeg_path = ffbin.ffmpeg()
    fps_arg = f"fps={sample_fps:g}"
    result = _run(
        [
            ffmpeg_path,
            "-i", video_path,
            "-vf", fps_arg,
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
            img.load()
            frames.append(img)
        except Exception:
            pass
        start = end + 2
    return frames


# ---------------------------------------------------------------------------
# Face detection: multi-pass with high precision & dark-studio wide recall
# ---------------------------------------------------------------------------

def _box_iou(a: dict, b: dict) -> float:
    ax1, ay1 = float(a["cx"]) - float(a["w"]) / 2.0, float(a["cy"]) - float(a["h"]) / 2.0
    ax2, ay2 = float(a["cx"]) + float(a["w"]) / 2.0, float(a["cy"]) + float(a["h"]) / 2.0
    bx1, by1 = float(b["cx"]) - float(b["w"]) / 2.0, float(b["cy"]) - float(b["h"]) / 2.0
    bx2, by2 = float(b["cx"]) + float(b["w"]) / 2.0, float(b["cy"]) + float(b["h"]) / 2.0

    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    if inter <= 0.0:
        return 0.0
    area_a = max(1.0, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1.0, (bx2 - bx1) * (by2 - by1))
    return inter / max(1.0, area_a + area_b - inter)


def _is_torso_shadow(cand: dict, head: dict) -> bool:
    """Detect false-positive torso/chest boxes stacked directly underneath a real face."""
    if cand.get("has_landmarks") and not head.get("has_landmarks"):
        return False
    h_cx, h_cy = float(head["cx"]), float(head["cy"])
    h_w, h_h = float(head["w"]), float(head["h"])
    c_cx, c_cy = float(cand["cx"]), float(cand["cy"])
    c_w, c_h = float(cand["w"]), float(cand["h"])
    same_column = abs(c_cx - h_cx) < max(h_w, c_w) * 0.65
    below_head = c_cy > h_cy + h_h * 0.35
    larger_or_similar = c_h >= h_h * 1.05
    return same_column and below_head and larger_or_similar


def _nms_and_clean_faces(faces: list[dict], vid_height: int) -> list[dict]:
    """Size-aware deduplication + torso-shadow suppression."""
    if not faces:
        return []

    def _priority(f: dict) -> tuple[int, float, float]:
        has_lm = 2 if f.get("has_landmarks") else (1 if not f.get("profile") else 0)
        score = float(f.get("score", 0.5))
        # Prefer natural upper-half face placement over low-frame chest/hand hits
        y_pen = -abs(float(f["cy"]) / max(vid_height, 1) - 0.30)
        return (has_lm, score, y_pen)

    ordered = sorted(faces, key=_priority, reverse=True)
    kept: list[dict] = []
    for f in ordered:
        fw, fh = float(f["w"]), float(f["h"])
        drop = False
        for ex in kept:
            ew, eh = float(ex["w"]), float(ex["h"])
            dist = math.hypot(float(f["cx"]) - float(ex["cx"]), float(f["cy"]) - float(ex["cy"]))
            if _box_iou(f, ex) > 0.20 or dist < max(55.0, 0.62 * max(fw, fh, ew, eh)):
                # Transfer mouth score if missing on kept detection
                if "mouth" in f and "mouth" not in ex:
                    ex["mouth"] = f["mouth"]
                drop = True
                break
            if not f.get("has_landmarks") and _is_torso_shadow(f, ex):
                drop = True
                break
        if not drop:
            kept.append(f)
    return kept


def _detect_profile_faces_cv2(
    rgb: Any,
    vid_width: int,
    vid_height: int,
    offset_x: int = 0,
    offset_y: int = 0,
    min_neighbors: int = 4,
) -> list[dict]:
    """
    Detect 90-degree side-profile speakers using CLAHE-enhanced OpenCV profile
    Haar cascade on both original and horizontally flipped orientations.
    """
    if not _HAS_CV2 or _profile_cascade is None or rgb.size == 0:
        return []

    try:
        tile_h, tile_w = rgb.shape[:2]
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        if _clahe is not None:
            gray = _clahe.apply(gray)

        min_dim = max(36, int(vid_height * 0.065))
        max_dim = max(min_dim + 10, int(vid_height * 0.40))

        raw_candidates: list[dict] = []
        for img, flipped in ((gray, False), (cv2.flip(gray, 1), True)):
            dets = _profile_cascade.detectMultiScale(
                img,
                scaleFactor=1.08,
                minNeighbors=min_neighbors,
                minSize=(min_dim, min_dim),
                maxSize=(max_dim, max_dim),
            )
            for (x, y, w, h) in dets:
                local_cx = (float(tile_w) - (float(x) + float(w) / 2.0)) if flipped else (float(x) + float(w) / 2.0)
                cx = float(offset_x) + local_cx
                cy = float(offset_y) + float(y) + float(h) / 2.0
                size = float(h) / max(vid_height, 1)
                if cy < vid_height * 0.58 and 0.065 <= size <= 0.40:
                    raw_candidates.append({
                        "cx": cx,
                        "cy": cy,
                        "w": float(w),
                        "h": float(h),
                        "size": size,
                        "profile": True,
                        "has_landmarks": False,
                        "source": "profile",
                        "score": 0.55,
                    })

        # Keep strongest (largest) candidate per horizontal half to avoid background noise
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


def _apply_gamma_lift(rgb: np.ndarray) -> np.ndarray:
    if _HAS_CV2:
        return cv2.LUT(rgb, _GAMMA_LUT)
    return _GAMMA_LUT[rgb]


def _detect_faces_all_frames(
    frames: list[Image.Image], vid_width: int, vid_height: int
) -> list[list[dict]]:
    """
    Precision-first multi-pass face detection:
      1. Full-frame MediaPipe FaceLandmarker (0.35 conf + mouth openness) +
         BlazeFace (0.52 conf, eliminating chair/chest/hand/fireplace false positives).
      2. Gamma-lifted tiled FaceLandmarker + CLAHE profile fallback ONLY when:
         - 0 faces were found (e.g. dark lighting / turned head / wide 2-shot), OR
         - 1 small/wide face (size < 0.22) was found and the opposite half may hold
           a second side-profile speaker.
      3. Size-aware IoU NMS + torso-shadow suppression.
    """
    all_faces: list[list[dict]] = []
    for frame in frames:
        rgb = np.array(frame.convert("RGB"))
        faces: list[dict] = []

        # Pass 1A: Full-frame FaceLandmarker (highest spatial precision + lip landmarks)
        try:
            lm_faces = speaker_detector.detect_faces_with_mouth_scores_rgb(rgb, vid_width, vid_height)
            faces.extend(lm_faces)
        except Exception:
            pass

        # Pass 1B: Full-frame BlazeFace (high-confidence 0.52)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        results = face_detector.detect(mp_image)
        if results.detections:
            for det in results.detections:
                bb = det.bounding_box
                score = float(det.categories[0].score) if det.categories else 0.52
                fw = float(bb.width)
                fh = float(bb.height)
                size = fh / max(vid_height, 1)
                aspect = fw / max(fh, 1.0)
                if size < 0.03 or size > 0.52 or aspect < 0.45 or aspect > 1.75:
                    continue
                faces.append({
                    "cx": float(bb.origin_x) + fw / 2.0,
                    "cy": float(bb.origin_y) + fh / 2.0,
                    "w": fw,
                    "h": fh,
                    "size": size,
                    "has_landmarks": False,
                    "source": "bf",
                    "score": score,
                })

        faces = _nms_and_clean_faces(faces, vid_height)

        # Pass 2: Gamma-lifted tiled & CLAHE profile fallback when wide or occluded
        has_large_closeup = any(float(f.get("size", 0.0)) >= 0.22 for f in faces)
        if len(faces) == 0:
            bright = _apply_gamma_lift(rgb)
            y1 = int(vid_height * 0.72)
            # Check left and right halves with FaceLandmarker first (zooms in 2x on small faces)
            for x0, x1 in ((0, int(vid_width * 0.52)), (int(vid_width * 0.48), vid_width)):
                try:
                    tile = bright[0:y1, x0:x1]
                    faces.extend(
                        speaker_detector.detect_faces_with_mouth_scores_rgb(
                            tile, vid_width, vid_height, offset_x=x0, offset_y=0
                        )
                    )
                except Exception:
                    pass
            faces = _nms_and_clean_faces(faces, vid_height)

            # If still missing side-profile speakers (e.g. wide 2-shot with both speakers 90deg),
            # run CLAHE profile cascade on the upper frame
            if len(faces) < 2 and not any(float(f.get("size", 0.0)) >= 0.22 for f in faces):
                prof_faces = _detect_profile_faces_cv2(bright[0:y1, :], vid_width, vid_height, min_neighbors=4)
                faces.extend(prof_faces)
                faces = _nms_and_clean_faces(faces, vid_height)

        elif len(faces) == 1 and not has_large_closeup:
            # One small/medium face found — probe the opposite half for a second wide-shot speaker
            ref = faces[0]
            ref_size = float(ref["size"])
            bright = _apply_gamma_lift(rgb)
            y1 = int(vid_height * 0.68)
            if float(ref["cx"]) > vid_width * 0.50:
                x0, x1 = 0, int(vid_width * 0.46)
            else:
                x0, x1 = int(vid_width * 0.54), vid_width

            tile = bright[0:y1, x0:x1]
            added: list[dict] = []
            try:
                added = speaker_detector.detect_faces_with_mouth_scores_rgb(
                    tile, vid_width, vid_height, offset_x=x0, offset_y=0
                )
            except Exception:
                added = []

            if not added:
                prof_tile = _detect_profile_faces_cv2(
                    tile, vid_width, vid_height, offset_x=x0, offset_y=0, min_neighbors=4
                )
                # Require scale consistency with the already-detected speaker so tiny wall noise is dropped
                added = [
                    pf for pf in prof_tile
                    if ref_size * 0.55 <= float(pf["size"]) <= ref_size * 1.85
                ]

            if added:
                faces.extend(added)
                faces = _nms_and_clean_faces(faces, vid_height)

        all_faces.append(faces)
    return all_faces


def _ease_in_out(t: float) -> float:
    return 2 * t * t if t < 0.5 else 1 - math.pow(-2 * t + 2, 2) / 2


# ---------------------------------------------------------------------------
# Fast frame-accurate camera cut detection & camera-angle clustering
# ---------------------------------------------------------------------------

def _detect_scene_cuts(video_path: str, duration: float) -> list[float]:
    """Legacy timestamp-based cut detector fallback using FFmpeg scdet."""
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


def _analyze_video_shots(
    video_path: str,
    fps: float,
    duration: float,
    probed_frames: int = 0,
) -> dict[str, Any]:
    """
    Detect exact camera-cut frame indices using downscaled 160x90 HSV histogram
    Bhattacharyya distance + pixel MAD (~1s per 60s clip), then cluster recurring
    camera angles so multi-cam podcasts share reliable speaker geometry across shots.
    """
    safe_fps = fps if fps > 1.0 else 30.0
    fallback_total = probed_frames if probed_frames > 0 else max(1, int(round(duration * safe_fps)))

    if not _HAS_CV2:
        cut_times = _detect_scene_cuts(video_path, duration)
        cut_frames = sorted({max(1, min(fallback_total - 1, int(round(t * safe_fps)))) for t in cut_times})
        bounds = [0] + cut_frames + [fallback_total]
        shots = [
            {
                "start_frame": bounds[i],
                "end_frame": bounds[i + 1],
                "start": bounds[i] / safe_fps,
                "end": bounds[i + 1] / safe_fps,
                "cluster_id": i,
            }
            for i in range(len(bounds) - 1)
            if bounds[i + 1] > bounds[i]
        ]
        return {"fps": safe_fps, "total_frames": fallback_total, "shots": shots}

    w_s, h_s = 160, 90
    try:
        res = _run(
            [
                ffbin.ffmpeg(),
                "-v", "error",
                "-i", video_path,
                "-fps_mode", "passthrough",
                "-vf", f"scale={w_s}:{h_s}",
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "pipe:1",
            ],
            timeout=180,
            text=False,
        )
        buf = np.frombuffer(res.stdout, dtype=np.uint8)
        frame_bytes = w_s * h_s * 3
        n_frames = len(buf) // frame_bytes
    except Exception as exc:
        logger.debug("Fast pipe shot analysis failed, falling back: %s", exc)
        n_frames = 0

    if n_frames < 2:
        shots = [{
            "start_frame": 0,
            "end_frame": fallback_total,
            "start": 0.0,
            "end": fallback_total / safe_fps,
            "cluster_id": 0,
        }]
        return {"fps": safe_fps, "total_frames": fallback_total, "shots": shots}

    frames_arr = buf[: n_frames * frame_bytes].reshape((n_frames, h_s, w_s, 3))
    hists: list[Any] = []
    bhats = np.zeros(n_frames, dtype=np.float32)
    mads = np.zeros(n_frames, dtype=np.float32)

    prev_hist = None
    prev_f = None
    for i in range(n_frames):
        fr = frames_arr[i]
        hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        hists.append(hist)
        if prev_hist is not None and prev_f is not None:
            bhats[i] = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
            mads[i] = float(np.mean(np.abs(fr.astype(np.float32) - prev_f.astype(np.float32))))
        prev_hist = hist
        prev_f = fr

    med_b = float(np.median(bhats[1:])) if n_frames > 1 else 0.03
    med_m = float(np.median(mads[1:])) if n_frames > 1 else 2.0
    bhat_thresh = max(0.22, med_b * 4.0)
    mad_thresh = max(11.0, med_m * 4.5)
    mad_hard = max(26.0, med_m * 7.5)
    min_shot_frames = max(6, int(round(0.35 * safe_fps)))

    cut_frames: list[int] = []
    for i in range(min_shot_frames, n_frames - min_shot_frames):
        is_cut = (bhats[i] > bhat_thresh and mads[i] > mad_thresh) or (mads[i] > mad_hard)
        if is_cut:
            if not cut_frames or (i - cut_frames[-1]) >= min_shot_frames:
                cut_frames.append(i)

    bounds = [0] + cut_frames + [n_frames]
    shots: list[dict[str, Any]] = []
    clusters: list[dict[str, Any]] = []

    for idx in range(len(bounds) - 1):
        f0, f1 = bounds[idx], bounds[idx + 1]
        if f1 <= f0:
            continue
        # Sample up to 5 interior frames for a clean camera-angle fingerprint
        margin = min(2, max(0, (f1 - f0) // 4))
        span_start = f0 + margin
        span_end = max(span_start + 1, f1 - margin)
        sample_idxs = np.linspace(span_start, span_end - 1, num=min(5, span_end - span_start), dtype=int)
        avg_hist = np.mean([hists[k] for k in sample_idxs], axis=0).astype(np.float32)
        cv2.normalize(avg_hist, avg_hist)
        avg_thumb = np.mean([frames_arr[k].astype(np.float32) for k in sample_idxs], axis=0)

        # Match against existing camera clusters (both color histogram AND spatial thumbnail)
        matched_cid = -1
        best_score = 1e9
        for cid, cl in enumerate(clusters):
            hb = float(cv2.compareHist(avg_hist, cl["hist"], cv2.HISTCMP_BHATTACHARYYA))
            tm = float(np.mean(np.abs(avg_thumb - cl["thumb"])))
            if hb < 0.17 and tm < 22.0 and hb < best_score:
                best_score = hb
                matched_cid = cid

        if matched_cid == -1:
            matched_cid = len(clusters)
            clusters.append({"hist": avg_hist, "thumb": avg_thumb})

        shots.append({
            "start_frame": f0,
            "end_frame": f1,
            "start": f0 / safe_fps,
            "end": f1 / safe_fps,
            "cluster_id": matched_cid,
        })

    return {"fps": safe_fps, "total_frames": n_frames, "shots": shots}


def _shot_face_samples(
    shot: dict[str, Any],
    face_data: list[list[dict]],
    sample_fps: float,
) -> list[tuple[int, float, list[dict]]]:
    """Return (sample_idx, sample_time, faces) strictly inside a shot interval."""
    t0 = float(shot["start"])
    t1 = float(shot["end"])
    margin = min(0.12, max(0.0, (t1 - t0) * 0.15))
    out: list[tuple[int, float, list[dict]]] = []
    for idx, faces in enumerate(face_data):
        ts = (idx + 0.5) / sample_fps
        if (t0 + margin) <= ts <= (t1 - margin):
            out.append((idx, ts, faces))
    if not out and face_data:
        # Very short shot: take any sample inside [t0, t1], else nearest to midpoint
        for idx, faces in enumerate(face_data):
            ts = (idx + 0.5) / sample_fps
            if t0 <= ts <= t1:
                out.append((idx, ts, faces))
    if not out and face_data:
        mid_t = (t0 + t1) / 2.0
        nearest = max(0, min(len(face_data) - 1, int(round(mid_t * sample_fps - 0.5))))
        out.append((nearest, (nearest + 0.5) / sample_fps, face_data[nearest]))
    return out


def _wide_two_pair(faces: list[dict], vid_width: int) -> Optional[tuple[dict, dict]]:
    """Return (left_face, right_face) if frame truly contains two horizontally separated wide-shot speakers."""
    if len(faces) < 2:
        return None
    ordered = sorted(faces, key=lambda f: float(f["cx"]))
    left_f, right_f = ordered[0], ordered[-1]
    gap = float(right_f["cx"]) - float(left_f["cx"])
    if (
        float(left_f["cx"]) < vid_width * 0.46
        and float(right_f["cx"]) > vid_width * 0.54
        and gap >= vid_width * 0.28
        and max(float(left_f.get("size", 0.0)), float(right_f.get("size", 0.0))) < 0.25
    ):
        return left_f, right_f
    return None


def _summarize_cluster_faces(
    shots: list[dict[str, Any]],
    face_data: list[list[dict]],
    vid_width: int,
    vid_height: int,
    sample_fps: float,
) -> dict[int, dict[str, Any]]:
    """
    Pool face evidence per camera-angle cluster so recurring cameras (e.g. wide 2-shot
    vs Speaker A solo vs Speaker B solo) get rock-solid classification & geometry.
    """
    by_cluster: dict[int, list[list[dict]]] = {}
    for sh in shots:
        cid = int(sh["cluster_id"])
        samps = _shot_face_samples(sh, face_data, sample_fps)
        by_cluster.setdefault(cid, []).extend([f for _, _, f in samps])

    summaries: dict[int, dict[str, Any]] = {}
    for cid, frame_lists in by_cluster.items():
        wide_left: list[dict] = []
        wide_right: list[dict] = []
        wide_pair_frames = 0
        solo_closeup_frames = 0
        all_single_faces: list[dict] = []

        for faces in frame_lists:
            pair = _wide_two_pair(faces, vid_width)
            if pair is not None:
                wide_pair_frames += 1
                wide_left.append(pair[0])
                wide_right.append(pair[1])
                continue

            if len(faces) == 1:
                f = faces[0]
                all_single_faces.append(f)
                sz = float(f.get("size", 0.0))
                if sz >= 0.22:
                    solo_closeup_frames += 1
                elif sz < 0.21:
                    if float(f["cx"]) < vid_width * 0.44:
                        wide_left.append(f)
                    elif float(f["cx"]) > vid_width * 0.56:
                        wide_right.append(f)
            elif len(faces) >= 2:
                # Non-wide multi-face (e.g., two close faces or one face + partial): keep strongest for solo fallback
                best = max(faces, key=lambda d: (1 if d.get("has_landmarks") else 0, float(d.get("size", 0.0))))
                all_single_faces.append(best)
                if float(best.get("size", 0.0)) >= 0.22:
                    solo_closeup_frames += 1

        # Cluster is a true wide 2-shot if we see opposite-side wide faces (either in the same frame
        # OR pooled across frames of this same camera angle) and it is NOT a solo close-up camera.
        has_both_sides = bool(wide_left) and bool(wide_right)
        side_gap_ok = (
            has_both_sides
            and (statistics.median([float(f["cx"]) for f in wide_right]) - statistics.median([float(f["cx"]) for f in wide_left]))
            >= vid_width * 0.28
        )
        is_wide_two = (
            side_gap_ok
            and (wide_pair_frames >= 1 or (len(wide_left) >= 1 and len(wide_right) >= 1 and solo_closeup_frames == 0))
            and (solo_closeup_frames == 0 or wide_pair_frames >= solo_closeup_frames)
        )

        # Robust solo face summary
        solo_pool = all_single_faces or [f for fl in frame_lists for f in fl]
        if solo_pool:
            solo_cx = statistics.median([float(f["cx"]) for f in solo_pool])
            solo_cy = statistics.median([float(f["cy"]) for f in solo_pool])
            solo_h = statistics.median([float(f.get("h", vid_height * 0.22)) for f in solo_pool])
            solo_size = statistics.median([float(f.get("size", 0.22)) for f in solo_pool])
        else:
            solo_cx = vid_width / 2.0
            solo_cy = vid_height * 0.36
            solo_h = vid_height * 0.22
            solo_size = 0.22

        summaries[cid] = {
            "is_wide_two": is_wide_two,
            "wide_pair_frames": wide_pair_frames,
            "solo_closeup_frames": solo_closeup_frames,
            "wide_left": wide_left,
            "wide_right": wide_right,
            "has_faces": bool(solo_pool) or has_both_sides,
            "solo_cx": solo_cx,
            "solo_cy": solo_cy,
            "solo_h": solo_h,
            "solo_size": solo_size,
        }
    return summaries


def _compute_split_boxes(
    left_cx: float,
    left_cy: float,
    left_h: float,
    right_cx: float,
    right_cy: float,
    right_h: float,
    vid_width: int,
    vid_height: int,
) -> tuple[int, int, int, int, int, int]:
    """
    Compute geometrically centerable crop boxes (crop_w, crop_h, lx, ly, rx, ry)
    for stacked 1080x960 podcast split-screen panels.
    Always avoids edge-clamping whenever physically possible so both speakers sit
    dead-center horizontally with balanced rule-of-thirds headroom.
    """
    target_ar = 1080.0 / 960.0
    median_face_h = statistics.median([left_h, right_h])
    ideal_crop_w = median_face_h * 4.0 * target_ar

    # Max width that lets each speaker sit at exact horizontal center without edge clamp
    max_w_left = 2.0 * min(left_cx, max(1.0, float(vid_width) - left_cx))
    max_w_right = 2.0 * min(right_cx, max(1.0, float(vid_width) - right_cx))
    max_centerable_w = min(max_w_left, max_w_right)
    no_overlap_w = max(2.0, (right_cx - left_cx) * 0.94)

    min_crop_w = max(vid_width * 0.18, median_face_h * 2.2 * target_ar)
    hard_max_w = min(vid_width * 0.48, vid_height * target_ar, no_overlap_w)
    if hard_max_w < min_crop_w:
        min_crop_w = hard_max_w

    # Smooth clamp with NO cliff jump: if speaker sits near edge, stay tight at max_centerable_w
    # (floored gently at min_crop_w) instead of exploding back to ideal_crop_w!
    crop_w_f = max(min_crop_w, min(ideal_crop_w, max_centerable_w, hard_max_w))
    crop_w = max(2, min(vid_width, int(round(crop_w_f)) & ~1))
    crop_h = max(2, min(vid_height, int(round(crop_w / target_ar)) & ~1))
    if crop_h == vid_height:
        crop_w = max(2, min(vid_width, int(round(crop_h * target_ar)) & ~1))

    def _place(cx: float, cy: float) -> tuple[int, int]:
        x = int(round(max(0.0, min(cx - crop_w / 2.0, float(vid_width - crop_w)))))
        # Rule-of-thirds eye-line: face center sits at ~38% from panel top
        y = int(round(max(0.0, min(cy - crop_h * 0.38, float(vid_height - crop_h)))))
        return x, y

    lx, ly = _place(left_cx, left_cy)
    rx, ry = _place(right_cx, right_cy)
    return crop_w, crop_h, lx, ly, rx, ry


def _compute_shot_crop_dims(
    median_size: float,
    median_cx: float,
    vid_width: int,
    vid_height: int,
    allow_zoom: bool = True,
) -> tuple[int, int]:
    """
    Compute a steady, shot-locked 9:16 crop (crop_w, crop_h) using a smooth
    continuous zoom curve (zero cliff jumps!) so there is NEVER zoom jitter.
    """
    if not allow_zoom or median_size >= 0.21:
        zoom = 1.0
    elif median_size >= 0.10:
        zoom = 1.0 + ((0.21 - median_size) / 0.11) * 0.45
    else:
        zoom = min(1.65, 1.45 + ((0.10 - median_size) / 0.06) * 0.20)

    # For wide-shot speakers seated near the left/right edges, gently nudge zoom
    # (up to 1.45) so the 9:16 crop centers them without hitting the frame edge.
    if allow_zoom and median_size < 0.21:
        base_w = (float(vid_height) / zoom) * 9.0 / 16.0
        max_centerable_w = 2.0 * min(median_cx, max(1.0, float(vid_width) - median_cx))
        if max_centerable_w > vid_width * 0.16 and base_w > max_centerable_w:
            needed_zoom = (float(vid_height) * 9.0 / 16.0) / max(1.0, max_centerable_w)
            zoom = max(zoom, min(1.48, needed_zoom))

    crop_h = float(vid_height) / max(1.0, zoom)
    crop_w = crop_h * 9.0 / 16.0
    if crop_w > vid_width:
        crop_w = float(vid_width)
        crop_h = crop_w * 16.0 / 9.0
        if crop_h > vid_height:
            crop_h = float(vid_height)
            crop_w = crop_h * 9.0 / 16.0

    w_even = max(2, min(vid_width, int(round(crop_w)) & ~1))
    h_even = max(2, min(vid_height, int(round(crop_h)) & ~1))
    return w_even, h_even


def _place_single_crop(cx: float, cy: float, crop_w: int, crop_h: int, vid_width: int, vid_height: int) -> tuple[int, int]:
    x = int(round(max(0.0, min(cx - crop_w / 2.0, float(vid_width - crop_w)))))
    y = int(round(max(0.0, min(cy - crop_h * 0.36, float(vid_height - crop_h)))))
    return x, y


# ---------------------------------------------------------------------------
# Reframing modes
# ---------------------------------------------------------------------------

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


async def _reframe_podcast_split(
    input_path: str,
    output_path: str,
    vid_width: int,
    vid_height: int,
    face_data: list[list[dict]],
    duration: float = 0.0,
    fps: float = 0.0,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    shot_analysis: Optional[dict[str, Any]] = None,
) -> bool:
    """
    Podcast Split-Screen (1080x1920):
      - Detects exact camera cuts and clusters recurring camera angles.
      - True wide 2-person shots render as dead-center stacked 1080x960 panels
        (Speaker A top, Speaker B bottom) with balanced headroom and zero edge drift.
      - Single-speaker shots ALWAYS render as centered full-screen 9:16 portrait
        so the same person talking is NEVER split-screened across top & bottom!
      - Uses frame-exact trim (start_frame/end_frame) for zero A/V sync drift.
    """
    if shot_analysis is None:
        if fps <= 1.0:
            _, _, fps, total_f = await asyncio.to_thread(_get_video_info, input_path)
        else:
            total_f = max(1, int(round(duration * fps)))
        shot_analysis = await asyncio.to_thread(_analyze_video_shots, input_path, fps, duration, total_f)

    shots = shot_analysis["shots"]
    cluster_info = _summarize_cluster_faces(shots, face_data, vid_width, vid_height, sample_fps)

    # Global fallback wide-shot coordinates pooled across all true wide clusters
    global_left = [f for info in cluster_info.values() if info["is_wide_two"] for f in info["wide_left"]]
    global_right = [f for info in cluster_info.values() if info["is_wide_two"] for f in info["wide_right"]]
    if not global_left or not global_right:
        for faces in face_data:
            pair = _wide_two_pair(faces, vid_width)
            if pair is not None:
                global_left.append(pair[0])
                global_right.append(pair[1])

    raw_segments: list[dict[str, Any]] = []
    last_single: Optional[tuple[float, float, float]] = None

    for sh in shots:
        cid = int(sh["cluster_id"])
        cinfo = cluster_info[cid]
        samps = _shot_face_samples(sh, face_data, sample_fps)
        shot_faces = [f for _, _, fl in samps for f in fl]

        if cinfo["is_wide_two"]:
            l_pool = cinfo["wide_left"] or global_left
            r_pool = cinfo["wide_right"] or global_right
            left_cx = statistics.median([float(f["cx"]) for f in l_pool]) if l_pool else vid_width * 0.25
            left_cy = statistics.median([float(f["cy"]) for f in l_pool]) if l_pool else vid_height * 0.35
            left_h = statistics.median([float(f.get("h", vid_height * 0.15)) for f in l_pool]) if l_pool else vid_height * 0.15
            right_cx = statistics.median([float(f["cx"]) for f in r_pool]) if r_pool else vid_width * 0.75
            right_cy = statistics.median([float(f["cy"]) for f in r_pool]) if r_pool else vid_height * 0.35
            right_h = statistics.median([float(f.get("h", vid_height * 0.15)) for f in r_pool]) if r_pool else vid_height * 0.15

            crop_w, crop_h, lx, ly, rx, ry = _compute_split_boxes(
                left_cx, left_cy, left_h, right_cx, right_cy, right_h, vid_width, vid_height
            )
            raw_segments.append({
                "kind": "split",
                "start_frame": sh["start_frame"],
                "end_frame": sh["end_frame"],
                "crop_w": crop_w,
                "crop_h": crop_h,
                "lx": lx,
                "ly": ly,
                "rx": rx,
                "ry": ry,
            })
        else:
            # Single-speaker (or cutaway) shot: NEVER split-screen one person!
            if shot_faces:
                # Filter out stray low-priority outliers if landmark faces exist in this shot
                lm_only = [f for f in shot_faces if f.get("has_landmarks")]
                use_pool = lm_only if lm_only else shot_faces
                cx = statistics.median([float(f["cx"]) for f in use_pool])
                cy = statistics.median([float(f["cy"]) for f in use_pool])
                sz = statistics.median([float(f.get("size", 0.24)) for f in use_pool])
                last_single = (cx, cy, sz)
            elif cinfo["has_faces"]:
                cx, cy, sz = cinfo["solo_cx"], cinfo["solo_cy"], cinfo["solo_size"]
                last_single = (cx, cy, sz)
            elif last_single is not None:
                cx, cy, sz = last_single
            else:
                cx, cy, sz = vid_width / 2.0, vid_height * 0.36, 0.24

            sc_w, sc_h = _compute_shot_crop_dims(sz, cx, vid_width, vid_height, allow_zoom=True)
            sx, sy = _place_single_crop(cx, cy, sc_w, sc_h, vid_width, vid_height)
            raw_segments.append({
                "kind": "single",
                "start_frame": sh["start_frame"],
                "end_frame": sh["end_frame"],
                "w": sc_w,
                "h": sc_h,
                "x": sx,
                "y": sy,
            })

    # Merge contiguous segments with essentially identical framing
    segments: list[dict[str, Any]] = []
    for seg in raw_segments:
        if segments and segments[-1]["kind"] == seg["kind"]:
            prev = segments[-1]
            if seg["kind"] == "split":
                if (
                    prev["crop_w"] == seg["crop_w"]
                    and prev["crop_h"] == seg["crop_h"]
                    and abs(prev["lx"] - seg["lx"]) <= 12
                    and abs(prev["rx"] - seg["rx"]) <= 12
                ):
                    prev["end_frame"] = seg["end_frame"]
                    continue
            else:
                if (
                    prev["w"] == seg["w"]
                    and prev["h"] == seg["h"]
                    and abs(prev["x"] - seg["x"]) <= 16
                    and abs(prev["y"] - seg["y"]) <= 16
                ):
                    prev["end_frame"] = seg["end_frame"]
                    continue
        segments.append(dict(seg))

    if not segments:
        return False

    logger.info(
        "podcast_split plan (%dx%d): %d segment(s) (%d split, %d single)",
        vid_width,
        vid_height,
        len(segments),
        sum(1 for s in segments if s["kind"] == "split"),
        sum(1 for s in segments if s["kind"] == "single"),
    )

    if len(segments) == 1:
        seg = segments[0]
        if seg["kind"] == "split":
            filter_cx = (
                f"[0:v]split=2[sa][sb];"
                f"[sa]crop={seg['crop_w']}:{seg['crop_h']}:{seg['lx']}:{seg['ly']},"
                f"scale=1080:960:flags=lanczos,setsar=1[top];"
                f"[sb]crop={seg['crop_w']}:{seg['crop_h']}:{seg['rx']}:{seg['ry']},"
                f"scale=1080:960:flags=lanczos,setsar=1[bot];"
                f"[top][bot]vstack=inputs=2,setsar=1[outv]"
            )
        else:
            filter_cx = (
                f"[0:v]crop={seg['w']}:{seg['h']}:{seg['x']}:{seg['y']},"
                f"scale=1080:1920:flags=lanczos,setsar=1[outv]"
            )
    else:
        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for idx, seg in enumerate(segments):
            is_last = idx == len(segments) - 1
            trim_expr = (
                f"trim=start_frame={seg['start_frame']},setpts=PTS-STARTPTS"
                if is_last
                else f"trim=start_frame={seg['start_frame']}:end_frame={seg['end_frame']},setpts=PTS-STARTPTS"
            )
            if seg["kind"] == "split":
                filter_parts.append(
                    f"[0:v]{trim_expr},split=2[s{idx}a][s{idx}b];"
                    f"[s{idx}a]crop={seg['crop_w']}:{seg['crop_h']}:{seg['lx']}:{seg['ly']},"
                    f"scale=1080:960:flags=lanczos,setsar=1[t{idx}];"
                    f"[s{idx}b]crop={seg['crop_w']}:{seg['crop_h']}:{seg['rx']}:{seg['ry']},"
                    f"scale=1080:960:flags=lanczos,setsar=1[b{idx}];"
                    f"[t{idx}][b{idx}]vstack=inputs=2,setsar=1[v{idx}]"
                )
            else:
                filter_parts.append(
                    f"[0:v]{trim_expr},crop={seg['w']}:{seg['h']}:{seg['x']}:{seg['y']},"
                    f"scale=1080:1920:flags=lanczos,setsar=1[v{idx}]"
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
    fps: float = 0.0,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    shot_analysis: Optional[dict[str, Any]] = None,
) -> bool:
    """Full-screen active-speaker camera switching with lip tracking and frame-exact hard cuts."""
    ok, _ = await _reframe_dynamic_zoom(
        input_path,
        output_path,
        vid_width,
        vid_height,
        face_data,
        duration,
        frames=frames,
        fps=fps,
        sample_fps=sample_fps,
        shot_analysis=shot_analysis,
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
    cx = statistics.median([float(f["cx"]) for f in best_bucket])
    cy = statistics.median([float(f["cy"]) for f in best_bucket])
    h = statistics.median([float(f.get("h", vid_height * 0.2)) for f in best_bucket])
    return cx, cy, h


# ---------------------------------------------------------------------------
# Smart Face Track helpers: median spike rejection, deadzone pan, RDP keyframes
# ---------------------------------------------------------------------------

def _median_filter_1d(vals: list[float], radius: int = 1) -> list[float]:
    n = len(vals)
    if n < 3:
        return list(vals)
    out: list[float] = []
    for i in range(n):
        window = vals[max(0, i - radius) : min(n, i + radius + 1)]
        out.append(float(statistics.median(window)))
    return out


def _smooth_pan_path(
    times: list[float],
    xs: list[float],
    deadzone_px: float,
) -> list[tuple[float, float]]:
    """
    Smooth horizontal crop-x trajectory with deadzone hysteresis + bidirectional
    Gaussian smoothing, then simplify nearly-collinear points (RDP) so FFmpeg
    evaluates a tiny, silky-smooth piecewise-linear expression natively per frame.
    """
    if not times or not xs:
        return []
    if len(xs) == 1:
        return [(times[0], xs[0])]

    med = _median_filter_1d(xs, radius=1)
    # Apply deadzone: only pull camera when target moves > deadzone_px from current anchor
    dz: list[float] = [med[0]]
    anchor = med[0]
    for v in med[1:]:
        delta = v - anchor
        if abs(delta) > deadzone_px:
            anchor = v - math.copysign(deadzone_px * 0.5, delta)
        dz.append(anchor)

    # Zero-phase 5-tap Gaussian kernel [1, 4, 6, 4, 1] / 16
    weights = [1.0, 4.0, 6.0, 4.0, 1.0]
    smoothed: list[float] = []
    n = len(dz)
    for i in range(n):
        acc = 0.0
        wsum = 0.0
        for off, w in zip((-2, -1, 0, 1, 2), weights):
            j = max(0, min(n - 1, i + off))
            acc += dz[j] * w
            wsum += w
        smoothed.append(acc / wsum)

    pts = list(zip(times, smoothed))
    return _rdp_simplify(pts, epsilon=4.0)


def _rdp_simplify(pts: list[tuple[float, float]], epsilon: float = 4.0) -> list[tuple[float, float]]:
    if len(pts) <= 2:
        return pts
    t0, x0 = pts[0]
    t1, x1 = pts[-1]
    dt = max(1e-6, t1 - t0)
    max_err = -1.0
    max_idx = 0
    for i in range(1, len(pts) - 1):
        ti, xi = pts[i]
        interp = x0 + (x1 - x0) * ((ti - t0) / dt)
        err = abs(xi - interp)
        if err > max_err:
            max_err = err
            max_idx = i
    if max_err > epsilon:
        left = _rdp_simplify(pts[: max_idx + 1], epsilon)
        right = _rdp_simplify(pts[max_idx:], epsilon)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def _piecewise_linear_expr(keyframes: list[tuple[float, float]], max_val: int) -> str:
    """Build a compact FFmpeg piecewise-linear expression for crop x(t)."""
    if not keyframes:
        return "0"
    if len(keyframes) == 1:
        return str(int(round(max(0.0, min(float(max_val), keyframes[0][1])))))

    # Limit depth to at most 18 segments so FFmpeg expression stays lean
    kfs = keyframes
    if len(kfs) > 18:
        idxs = np.linspace(0, len(kfs) - 1, num=18, dtype=int)
        kfs = [kfs[int(k)] for k in idxs]

    def _clamped(v: float) -> float:
        return max(0.0, min(float(max_val), v))

    expr = f"{_clamped(kfs[-1][1]):.1f}"
    for i in range(len(kfs) - 2, -1, -1):
        t0, v0 = kfs[i][0], _clamped(kfs[i][1])
        t1, v1 = kfs[i + 1][0], _clamped(kfs[i + 1][1])
        dt = max(0.01, t1 - t0)
        if abs(v1 - v0) < 0.5:
            seg_expr = f"{v0:.1f}"
        else:
            slope = (v1 - v0) / dt
            seg_expr = f"({v0:.1f}+({slope:.2f})*(t-{t0:.3f}))"
        expr = f"if(lt(t,{t1:.3f}),{seg_expr},{expr})"
    return f"clip({expr},0,{max_val})"


def _build_single_track_segment(
    start_frame: int,
    end_frame: int,
    fps: float,
    samples: list[tuple[float, dict]],
    vid_width: int,
    vid_height: int,
) -> dict[str, Any]:
    """
    Build one shot-locked Smart Face Track segment:
      - Constant (w, h) for the entire segment -> ZERO zoom jitter!
      - Rock-solid static tripod lock if speaker stays inside deadzone.
      - Silky smooth horizontal pan expression only when speaker actually moves across frame.
    """
    seg_t0 = start_frame / max(fps, 1.0)
    cxs = [float(f["cx"]) for _, f in samples]
    cys = [float(f["cy"]) for _, f in samples]
    sizes = [float(f.get("size", 0.22)) for _, f in samples]

    med_cx = statistics.median(cxs)
    med_cy = statistics.median(cys)
    med_size = statistics.median(sizes)

    crop_w, crop_h = _compute_shot_crop_dims(med_size, med_cx, vid_width, vid_height, allow_zoom=True)
    static_x, static_y = _place_single_crop(med_cx, med_cy, crop_w, crop_h, vid_width, vid_height)

    # Check if the speaker genuinely moves beyond a seated deadzone
    med_cxs = _median_filter_1d(cxs, radius=1)
    x_span = (max(med_cxs) - min(med_cxs)) if len(med_cxs) >= 3 else 0.0
    max_x = max(0, vid_width - crop_w)

    if len(samples) >= 3 and x_span >= crop_w * 0.18 and max_x > 10:
        rel_times = [max(0.0, t - seg_t0) for t, _ in samples]
        raw_crop_xs = [max(0.0, min(float(max_x), cx - crop_w / 2.0)) for cx in cxs]
        keyframes = _smooth_pan_path(rel_times, raw_crop_xs, deadzone_px=crop_w * 0.06)
        if len(keyframes) >= 2 and abs(max(k[1] for k in keyframes) - min(k[1] for k in keyframes)) >= 18.0:
            x_expr = _piecewise_linear_expr(keyframes, max_x)
            return {
                "start_frame": start_frame,
                "end_frame": end_frame,
                "w": crop_w,
                "h": crop_h,
                "x": static_x,
                "y": static_y,
                "x_expr": x_expr,
            }

    return {
        "start_frame": start_frame,
        "end_frame": end_frame,
        "w": crop_w,
        "h": crop_h,
        "x": static_x,
        "y": static_y,
        "x_expr": None,
    }


async def _reframe_dynamic_zoom(
    input_path: str,
    output_path: str,
    vid_width: int,
    vid_height: int,
    face_data: list[list[dict]],
    duration: float,
    frames: Optional[list[Image.Image]] = None,
    fps: float = 0.0,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    shot_analysis: Optional[dict[str, Any]] = None,
) -> tuple[bool, int]:
    """
    Shot-aware Smart Face Track (1080x1920):
      - Aligns hard cuts to exact camera-cut frames (never lags behind cuts or slides
        across an empty studio background).
      - Locks a smooth, continuous per-shot zoom level (ZERO zoom jitter / breathing!).
      - Uses deadzone tripod-lock for seated speakers and smooth horizontal tracking
        with fixed crop dimensions when a speaker walks across the frame.
      - On multi-speaker shots, locks onto the active speaker via lip activity +
        hysteresis hold with crisp hard cuts on speaker switches.
    """
    if shot_analysis is None:
        if fps <= 1.0:
            _, _, fps, total_f = await asyncio.to_thread(_get_video_info, input_path)
        else:
            total_f = max(1, int(round(duration * fps)))
        shot_analysis = await asyncio.to_thread(_analyze_video_shots, input_path, fps, duration, total_f)

    safe_fps = float(shot_analysis["fps"])
    shots = shot_analysis["shots"]
    cluster_info = _summarize_cluster_faces(shots, face_data, vid_width, vid_height, sample_fps)

    non_empty_counts = [len(f) for f in face_data if len(f) > 0]
    overall_face_count = max(set(non_empty_counts), key=non_empty_counts.count) if non_empty_counts else 0

    raw_segments: list[dict[str, Any]] = []
    last_known_face: Optional[dict] = None

    for sh in shots:
        cid = int(sh["cluster_id"])
        cinfo = cluster_info[cid]
        f0, f1 = int(sh["start_frame"]), int(sh["end_frame"])
        if f1 <= f0:
            continue

        samps = _shot_face_samples(sh, face_data, sample_fps)
        shot_frame_imgs = [frames[idx] for idx, _, _ in samps if frames and idx < len(frames)]
        shot_face_lists = [fl for _, _, fl in samps]
        shot_times = [ts for _, ts, _ in samps]

        # Check whether this shot simultaneously shows 2+ distinct separated people
        multi_frames = sum(
            1 for fl in shot_face_lists
            if len(fl) >= 2 and (max(float(d["cx"]) for d in fl) - min(float(d["cx"]) for d in fl)) >= vid_width * 0.22
        )
        is_multi_speaker_shot = cinfo["is_wide_two"] or (multi_frames >= 2)

        if is_multi_speaker_shot and len(shot_face_lists) >= 1:
            # Active-speaker selection inside this wide/panel shot with hysteresis hold
            active = speaker_detector.pick_active_faces_per_second(
                shot_frame_imgs,
                shot_face_lists,
                vid_width,
                vid_height,
                hold_seconds=2,
                sample_fps=sample_fps,
            )
            # Fill any None gaps inside this shot with nearest valid active face
            valid_active = [a for a in active if a is not None]
            if not valid_active:
                # Fall back to wide cluster speaker if available
                fallback_pool = cinfo["wide_left"] or cinfo["wide_right"]
                if fallback_pool:
                    f_med = {
                        "cx": statistics.median([float(d["cx"]) for d in fallback_pool]),
                        "cy": statistics.median([float(d["cy"]) for d in fallback_pool]),
                        "size": statistics.median([float(d.get("size", 0.15)) for d in fallback_pool]),
                    }
                    last_known_face = f_med
                    raw_segments.append(_build_single_track_segment(f0, f1, safe_fps, [(sh["start"], f_med)], vid_width, vid_height))
                    continue
            else:
                # Partition active faces into speaker turns (jump > 20% width = speaker switch!)
                filled: list[tuple[float, dict]] = []
                cur = valid_active[0]
                for ts, a in zip(shot_times, active):
                    if a is not None:
                        cur = a
                    filled.append((ts, cur))

                turns: list[list[tuple[float, dict]]] = [[filled[0]]]
                for ts, face in filled[1:]:
                    prev_cx = statistics.median([float(x[1]["cx"]) for x in turns[-1]])
                    if abs(float(face["cx"]) - prev_cx) > vid_width * 0.20:
                        turns.append([(ts, face)])
                    else:
                        turns[-1].append((ts, face))

                # Convert turns to exact frame spans inside [f0, f1)
                cur_f = f0
                for t_idx, turn in enumerate(turns):
                    if t_idx == len(turns) - 1:
                        next_f = f1
                    else:
                        # Switch at midpoint between last sample of current turn and first of next
                        switch_t = (turn[-1][0] + turns[t_idx + 1][0][0]) / 2.0
                        next_f = max(cur_f + 2, min(f1 - 2, int(round(switch_t * safe_fps))))
                    if next_f <= cur_f:
                        continue
                    last_known_face = turn[-1][1]
                    raw_segments.append(_build_single_track_segment(cur_f, next_f, safe_fps, turn, vid_width, vid_height))
                    cur_f = next_f
                continue

        # Single-speaker shot: collect the best face per sample
        picked: list[tuple[float, dict]] = []
        for ts, fl in zip(shot_times, shot_face_lists):
            if not fl:
                continue
            if len(fl) == 1:
                picked.append((ts, fl[0]))
            else:
                # Prefer verified landmark face closest to this camera cluster's median X
                target_cx = cinfo["solo_cx"] if cinfo["has_faces"] else vid_width * 0.5
                best = min(
                    fl,
                    key=lambda d: (0 if d.get("has_landmarks") else 1, abs(float(d["cx"]) - target_cx)),
                )
                picked.append((ts, best))

        if picked:
            last_known_face = {
                "cx": statistics.median([float(f["cx"]) for _, f in picked]),
                "cy": statistics.median([float(f["cy"]) for _, f in picked]),
                "size": statistics.median([float(f.get("size", 0.22)) for _, f in picked]),
            }
            raw_segments.append(_build_single_track_segment(f0, f1, safe_fps, picked, vid_width, vid_height))
        elif cinfo["has_faces"]:
            cluster_face = {
                "cx": cinfo["solo_cx"],
                "cy": cinfo["solo_cy"],
                "size": cinfo["solo_size"],
            }
            last_known_face = cluster_face
            raw_segments.append(_build_single_track_segment(f0, f1, safe_fps, [(sh["start"], cluster_face)], vid_width, vid_height))
        elif last_known_face is not None and (f1 - f0) < safe_fps * 3.0:
            # Short occlusion / reaction shot: hold steady on last known speaker
            raw_segments.append(_build_single_track_segment(f0, f1, safe_fps, [(sh["start"], last_known_face)], vid_width, vid_height))
        else:
            # Genuine faceless B-roll: crisp centered 9:16 crop with zero zoom jitter
            cw, ch = _compute_shot_crop_dims(0.25, vid_width / 2.0, vid_width, vid_height, allow_zoom=False)
            cx_pos, cy_pos = _place_single_crop(vid_width / 2.0, vid_height / 2.0, cw, ch, vid_width, vid_height)
            raw_segments.append({
                "start_frame": f0,
                "end_frame": f1,
                "w": cw,
                "h": ch,
                "x": cx_pos,
                "y": cy_pos,
                "x_expr": None,
            })

    if not raw_segments:
        return False, overall_face_count

    # Merge contiguous static segments that share identical framing
    segments: list[dict[str, Any]] = []
    for seg in raw_segments:
        if (
            segments
            and segments[-1]["x_expr"] is None
            and seg["x_expr"] is None
            and segments[-1]["w"] == seg["w"]
            and segments[-1]["h"] == seg["h"]
            and abs(segments[-1]["x"] - seg["x"]) <= 16
            and abs(segments[-1]["y"] - seg["y"]) <= 16
        ):
            segments[-1]["end_frame"] = seg["end_frame"]
            continue
        segments.append(dict(seg))

    logger.info(
        "dynamic_zoom plan (%dx%d): %d segment(s) (%d static-locked, %d smooth-pan)",
        vid_width,
        vid_height,
        len(segments),
        sum(1 for s in segments if s["x_expr"] is None),
        sum(1 for s in segments if s["x_expr"] is not None),
    )

    def _crop_filter(seg: dict[str, Any]) -> str:
        if seg["x_expr"] is not None:
            return f"crop=w={seg['w']}:h={seg['h']}:x='{seg['x_expr']}':y={seg['y']}"
        return f"crop={seg['w']}:{seg['h']}:{seg['x']}:{seg['y']}"

    if len(segments) == 1:
        filter_cx = f"[0:v]{_crop_filter(segments[0])},scale=1080:1920:flags=lanczos,setsar=1[outv]"
    else:
        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for idx, seg in enumerate(segments):
            is_last = idx == len(segments) - 1
            trim_expr = (
                f"trim=start_frame={seg['start_frame']},setpts=PTS-STARTPTS"
                if is_last
                else f"trim=start_frame={seg['start_frame']}:end_frame={seg['end_frame']},setpts=PTS-STARTPTS"
            )
            filter_parts.append(
                f"[0:v]{trim_expr},{_crop_filter(seg)},scale=1080:1920:flags=lanczos,setsar=1[v{idx}]"
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

    try:
        result = await asyncio.to_thread(
            ffbin.run_ffmpeg_encode, prefix_cmd, suffix_cmd, quality="standard", timeout=900, text=False
        )
    except RuntimeError as exc:
        logger.error("FFmpeg dynamic_zoom reframe failed: %s", exc)
        return False, overall_face_count

    if result.returncode != 0:
        logger.error("FFmpeg dynamic_zoom reframe failed: %s", result.stderr.decode(errors="replace")[-1500:])
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


def _parse_fps(rate_str: str) -> float:
    try:
        if "/" in rate_str:
            num, den = rate_str.split("/", 1)
            d = float(den)
            return float(num) / d if d > 0 else 30.0
        v = float(rate_str)
        return v if v > 0 else 30.0
    except Exception:
        return 30.0


def _get_video_info(video_path: str) -> tuple[int, int, float, int]:
    cmd = [
        ffbin.ffprobe(),
        "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames",
        "-of", "json", video_path,
    ]
    result = _run(cmd, timeout=60, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    w = int(stream["width"])
    h = int(stream["height"])
    fps = _parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "30/1")
    if fps < 1.0 or fps > 240.0:
        fps = _parse_fps(stream.get("r_frame_rate") or "30/1")
    nb_raw = stream.get("nb_frames")
    nb = int(nb_raw) if nb_raw and str(nb_raw).isdigit() else 0
    return w, h, fps, nb


def _get_video_dimensions(video_path: str) -> tuple[int, int]:
    w, h, _, _ = _get_video_info(video_path)
    return w, h


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
