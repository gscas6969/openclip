"""
speaker_detector.py — Active speaker detection using MediaPipe FaceLandmarker
(per-face lip movement mapped accurately to detected face bounding boxes)
with spatial face tracking and hysteresis hold.
"""

import os
import math
from typing import Any, List, Optional
import numpy as np  # type: ignore
from PIL import Image  # type: ignore

import mediapipe as mp  # type: ignore
from mediapipe.tasks import python  # type: ignore
from mediapipe.tasks.python import vision  # type: ignore

model_path = os.path.join(os.path.dirname(__file__), "face_landmarker.task")


def _ensure_model_exists():
    if not os.path.exists(model_path):
        import urllib.request

        url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
        urllib.request.urlretrieve(url, model_path)


_ensure_model_exists()
base_options = python.BaseOptions(model_asset_path=model_path)
options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    num_faces=6,
    min_face_detection_confidence=0.35,
    min_face_presence_confidence=0.35,
)
face_landmarker = vision.FaceLandmarker.create_from_options(options)


def _box_iou(a: dict, b: dict) -> float:
    ax1, ay1 = float(a["cx"]) - float(a["w"]) / 2.0, float(a["cy"]) - float(a["h"]) / 2.0
    ax2, ay2 = float(a["cx"]) + float(a["w"]) / 2.0, float(a["cy"]) + float(a["h"]) / 2.0
    bx1, by1 = float(b["cx"]) - float(b["w"]) / 2.0, float(b["cy"]) - float(b["h"]) / 2.0
    bx2, by2 = float(b["cx"]) + float(b["w"]) / 2.0, float(b["cy"]) + float(b["h"]) / 2.0

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    area_a = max(1.0, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1.0, (bx2 - bx1) * (by2 - by1))
    return inter / max(1.0, area_a + area_b - inter)


def detect_faces_with_mouth_scores_rgb(
    rgb: np.ndarray,
    vid_width: int,
    vid_height: int,
    offset_x: int = 0,
    offset_y: int = 0,
) -> List[dict]:
    """
    Run FaceLandmarker on an RGB numpy array (full frame or cropped tile) and map
    face bounding boxes + normalized mouth-opening scores back to full-frame coords.
    """
    if rgb.size == 0:
        return []
    tile_h, tile_w = rgb.shape[:2]
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    mesh_result = face_landmarker.detect(mp_image)
    if not mesh_result.face_landmarks:
        return []

    raw_found: List[dict] = []
    for lms in mesh_result.face_landmarks:
        xs = [offset_x + p.x * tile_w for p in lms]
        ys = [offset_y + p.y * tile_h for p in lms]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        fw = max(1.0, max_x - min_x)
        fh = max(1.0, max_y - min_y)
        size = fh / max(vid_height, 1)
        aspect = fw / fh
        if size < 0.03 or size > 0.56:
            continue
        if aspect < 0.42 or aspect > 1.85:
            continue

        # Inner lips: upper lip bottom (13) and lower lip top (14)
        mouth_open = abs(lms[14].y - lms[13].y) * tile_h / fh
        raw_found.append({
            "cx": (min_x + max_x) / 2.0,
            "cy": (min_y + max_y) / 2.0,
            "w": fw,
            "h": fh,
            "size": size,
            "mouth": float(mouth_open),
            "has_landmarks": True,
            "source": "lm",
            "score": 0.90,
        })

    # Size-aware deduplication in case FaceLandmarker emits overlapping boxes on one face
    deduped: List[dict] = []
    for f in sorted(raw_found, key=lambda d: (-d["size"], d["cy"])):
        is_dup = False
        for ex in deduped:
            dist = math.hypot(f["cx"] - ex["cx"], f["cy"] - ex["cy"])
            max_dim = max(f["w"], f["h"], ex["w"], ex["h"])
            if _box_iou(f, ex) > 0.22 or dist < max(48.0, 0.58 * max_dim):
                is_dup = True
                break
        if not is_dup:
            deduped.append(f)

    return deduped


def detect_faces_with_mouth_scores(
    frame: Image.Image,
    vid_width: int,
    vid_height: int,
) -> List[dict]:
    """
    Use FaceLandmarker to detect faces (including angled/wide podcast faces)
    and compute each face's normalized mouth-opening score (lip distance / face height).
    """
    rgb = np.array(frame.convert("RGB"))
    return detect_faces_with_mouth_scores_rgb(rgb, vid_width, vid_height)


def pick_active_faces_per_second(
    frames: List[Image.Image],
    face_data: List[List[dict]],
    vid_width: int,
    vid_height: int,
    hold_seconds: int = 2,
    sample_fps: float = 1.0,
) -> List[Optional[dict]]:
    """
    For each sampled frame, choose ONE target face to frame:
      - 0 faces -> None
      - 1 face  -> that face
      - 2+ faces -> track lip-opening activity (mouth openness + frame-to-frame
        mouth delta) using stable spatial speaker tracks with hysteresis hold
        so the camera stays locked on the active speaker and NEVER hovers in
        the empty center between speakers.
    """
    if not face_data:
        return []

    # Maintain persistent spatial speaker tracks (id -> {cx, mouth}) so leaning across
    # an arbitrary x=0.25/0.50 grid line never triggers a false speaker switch.
    tracks: list[dict[str, float]] = []
    track_match_radius = max(80.0, vid_width * 0.16)

    def _match_or_create_track(face: dict) -> int:
        cx = float(face["cx"])
        best_tid = -1
        best_dist = track_match_radius
        for tid, tr in enumerate(tracks):
            d = abs(cx - tr["cx"])
            if d < best_dist:
                best_dist = d
                best_tid = tid
        if best_tid == -1:
            tracks.append({"cx": cx, "mouth": float(face.get("mouth", 0.0))})
            return len(tracks) - 1
        # Gently update track anchor
        tracks[best_tid]["cx"] = tracks[best_tid]["cx"] * 0.75 + cx * 0.25
        return best_tid

    raw_track_ids: List[int] = []
    enriched_all: List[List[dict]] = []

    for i, faces in enumerate(face_data):
        if not faces:
            enriched_all.append([])
            raw_track_ids.append(-1)
            continue

        if len(faces) == 1:
            f = faces[0]
            tid = _match_or_create_track(f)
            tracks[tid]["mouth"] = float(f.get("mouth", 0.0))
            enriched_all.append([f])
            raw_track_ids.append(tid)
            continue

        # 2+ faces in frame: enrich with mouth scores if missing
        enriched = faces
        if all("mouth" not in f for f in faces) and i < len(frames):
            lm_faces = detect_faces_with_mouth_scores(frames[i], vid_width, vid_height)
            if lm_faces:
                enriched = []
                for f in faces:
                    best_lm = min(lm_faces, key=lambda lm: math.hypot(lm["cx"] - f["cx"], lm["cy"] - f["cy"]))
                    if math.hypot(best_lm["cx"] - f["cx"], best_lm["cy"] - f["cy"]) < vid_width * 0.12:
                        enriched.append({**f, "mouth": best_lm["mouth"], "has_landmarks": True})
                    else:
                        enriched.append({**f, "mouth": 0.0})
        enriched_all.append(enriched)

        best_score = -1.0
        best_tid = 0

        for f in enriched:
            tid = _match_or_create_track(f)
            mouth = float(f.get("mouth", 0.0))
            prev_m = tracks[tid]["mouth"]
            mouth_delta = abs(mouth - prev_m)
            tracks[tid]["mouth"] = mouth

            lm_bonus = 0.08 if f.get("has_landmarks") else 0.0
            # Speaking score = lip openness + lip movement delta + landmark preference + subtle size tie-breaker
            speak_score = (mouth * 2.5) + (mouth_delta * 4.5) + lm_bonus + (float(f.get("size", 0.1)) * 0.25)
            if speak_score > best_score:
                best_score = speak_score
                best_tid = tid

        raw_track_ids.append(best_tid)

    hold_samples = max(1, int(round(hold_seconds * max(0.5, sample_fps))))
    locked_tids = _apply_speaker_hold(raw_track_ids, hold_seconds=hold_samples)

    result: List[Optional[dict]] = []
    for i, faces in enumerate(enriched_all):
        if not faces:
            result.append(None)
            continue
        if len(faces) == 1:
            result.append(faces[0])
            continue
        target_tid = locked_tids[i]
        if 0 <= target_tid < len(tracks):
            target_cx = tracks[target_tid]["cx"]
            match = min(faces, key=lambda f: abs(float(f["cx"]) - target_cx))
            result.append(match)
        else:
            result.append(max(faces, key=lambda f: float(f.get("mouth", 0.0)) + float(f.get("size", 0.0)) * 0.25))

    return result


def detect_speakers_per_second(
    video_path: str,
    frames: List[Image.Image],
    face_data: List[List[dict]],
    vid_width: int,
    vid_height: int,
) -> List[int]:
    """Return active speaker index (0 = leftmost, 1 = rightmost, etc.) per second."""
    chosen = pick_active_faces_per_second(frames, face_data, vid_width, vid_height, hold_seconds=2)
    indices: List[int] = []
    last_idx = 0
    for i, faces in enumerate(face_data):
        if len(faces) >= 2 and chosen[i] is not None:
            ordered = sorted(faces, key=lambda f: f["cx"])
            best = min(
                range(len(ordered)),
                key=lambda k: math.hypot(ordered[k]["cx"] - chosen[i]["cx"], ordered[k]["cy"] - chosen[i]["cy"]),  # type: ignore
            )
            last_idx = best
            indices.append(best)
        else:
            indices.append(last_idx)
    return _apply_speaker_hold(indices, hold_seconds=2)


def _apply_speaker_hold(raw_speakers: List[int], hold_seconds: int = 2) -> List[int]:
    if not raw_speakers:
        return []

    smoothed = [raw_speakers[0]]
    current = raw_speakers[0]
    candidate = current
    candidate_count = 0

    for i in range(1, len(raw_speakers)):
        s = raw_speakers[i]
        if s == -1:
            smoothed.append(current)
            continue
        if current == -1:
            current = s
            smoothed.append(current)
            continue
        if s == current:
            candidate_count = 0
            smoothed.append(current)
        else:
            if s == candidate:
                candidate_count += 1
            else:
                candidate = s
                candidate_count = 1

            if candidate_count >= hold_seconds:
                current = candidate
                candidate_count = 0

            smoothed.append(current)

    return smoothed
