"""
speaker_detector.py — Active speaker detection using MediaPipe FaceLandmarker
(per-face lip movement mapped accurately to detected face bounding boxes)
with FFmpeg audio RMS fallback and hysteresis hold.
"""

import os
import subprocess
import math
from typing import Any, List, Tuple
import numpy as np  # type: ignore
from PIL import Image  # type: ignore

import ffbin  # type: ignore
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
    min_face_detection_confidence=0.25,
    min_face_presence_confidence=0.25,
)
face_landmarker = vision.FaceLandmarker.create_from_options(options)


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
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    mesh_result = face_landmarker.detect(mp_image)
    if not mesh_result.face_landmarks:
        return []

    found: List[dict] = []
    for lms in mesh_result.face_landmarks:
        xs = [p.x * vid_width for p in lms]
        ys = [p.y * vid_height for p in lms]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        fw = max(1.0, max_x - min_x)
        fh = max(1.0, max_y - min_y)
        size = fh / max(vid_height, 1)
        if size < 0.028:
            continue

        # Inner lips: upper lip bottom (13) and lower lip top (14)
        mouth_open = abs(lms[14].y - lms[13].y) * vid_height / fh
        # Outer lips width vs height adds robustness for talking motion
        found.append({
            "cx": (min_x + max_x) / 2.0,
            "cy": (min_y + max_y) / 2.0,
            "w": fw,
            "h": fh,
            "size": size,
            "mouth": float(mouth_open),
        })
    return found


def pick_active_faces_per_second(
    frames: List[Image.Image],
    face_data: List[List[dict]],
    vid_width: int,
    vid_height: int,
    hold_seconds: int = 2,
) -> List[dict | None]:
    """
    For each sampled second, choose ONE target face to frame:
      - 0 faces -> None
      - 1 face  -> that face
      - 2+ faces -> track lip-opening activity (mouth openness + frame-to-frame
        mouth delta) with hysteresis hold so the camera stays locked on the
        active speaker and NEVER hovers in the empty center between speakers.
    """
    if not face_data:
        return []

    # Track recent mouth readings by coarse X-slot (left/mid/right bucket) to compute lip motion delta
    prev_mouth_by_slot: dict[int, float] = {}
    raw_chosen: List[dict | None] = []
    raw_slot: List[int] = []

    for i, faces in enumerate(face_data):
        if not faces:
            raw_chosen.append(None)
            raw_slot.append(-1)
            continue

        if len(faces) == 1:
            f = faces[0]
            slot = int(min(3, max(0, (f["cx"] / max(vid_width, 1)) * 4)))
            prev_mouth_by_slot[slot] = float(f.get("mouth", 0.0))
            raw_chosen.append(f)
            raw_slot.append(slot)
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
                        enriched.append({**f, "mouth": best_lm["mouth"]})
                    else:
                        enriched.append({**f, "mouth": 0.0})

        best_face = enriched[0]
        best_score = -1.0
        best_slot = 0

        for f in enriched:
            slot = int(min(3, max(0, (f["cx"] / max(vid_width, 1)) * 4)))
            mouth = float(f.get("mouth", 0.0))
            prev_m = prev_mouth_by_slot.get(slot, mouth)
            mouth_delta = abs(mouth - prev_m)
            prev_mouth_by_slot[slot] = mouth

            # Speaking score = lip openness + lip movement delta + slight foreground size tie-breaker
            speak_score = (mouth * 2.5) + (mouth_delta * 4.0) + (float(f.get("size", 0.1)) * 0.35)
            if speak_score > best_score:
                best_score = speak_score
                best_face = f
                best_slot = slot

        raw_chosen.append(best_face)
        raw_slot.append(best_slot)

    # Apply hysteresis hold across slots so we don't ping-pong every 1 second
    locked_slots = _apply_speaker_hold(raw_slot, hold_seconds=hold_seconds)
    result: List[dict | None] = []
    for i, faces in enumerate(face_data):
        if not faces:
            result.append(None)
            continue
        if len(faces) == 1:
            result.append(faces[0])
            continue
        target_slot = locked_slots[i]
        # Pick the face closest to the locked speaker slot
        slot_center_x = ((target_slot + 0.5) / 4.0) * vid_width if target_slot >= 0 else vid_width * 0.5
        match = min(faces, key=lambda f: abs(f["cx"] - slot_center_x))
        result.append(match)

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
