"""
layout_detector.py — Smart multi-speaker & screen-share layout classifier.
"""

import math
import numpy as np  # type: ignore
from PIL import Image  # type: ignore
from typing import Any


def detect_layout(
    frames: list[Image.Image],
    face_data: list[list[dict[str, Any]]],
    vid_width: int,
    vid_height: int,
) -> dict:
    """Analyze sampled frames and return layout decision."""
    face_data = _deduplicate_faces(face_data)
    face_data = _filter_false_positives(face_data)
    majority_faces = _majority_face_count(face_data)

    total = max(len(face_data), 1)
    two_plus_ratio = sum(1 for f in face_data if len(f) >= 2) / total
    any_face_ratio = sum(1 for f in face_data if len(f) >= 1) / total

    has_screen = _detect_screen_region(frames, face_data, vid_width, vid_height)

    # Check if two+ distinct speakers appear side-by-side frequently (>= 30% of frames)
    consistent_sides = _check_consistent_face_sides(face_data, vid_width)

    if any_face_ratio < 0.15:
        mode = "none"
        confidence = 1.0
    elif (majority_faces >= 2 or two_plus_ratio >= 0.30) and consistent_sides:
        mode = "podcast"
        confidence = 0.92
    elif majority_faces >= 3:
        mode = "panel"
        confidence = 0.88
    elif majority_faces == 1 or any_face_ratio >= 0.15:
        if has_screen and two_plus_ratio < 0.20:
            mode = "tutorial"
            confidence = 0.85
        else:
            mode = "single"
            confidence = 0.95
    else:
        mode = "none"
        confidence = 0.9

    return {
        "mode": mode,
        "confidence": confidence,
        "face_count": max(majority_faces, 2 if mode == "podcast" else majority_faces),
        "has_screen_region": has_screen,
        "needs_user_confirm": confidence < 0.8,
    }


def _detect_screen_region(
    frames: list[Image.Image],
    face_data: list[list[dict[str, Any]]],
    vid_width: int,
    vid_height: int,
) -> bool:
    screen_count: float = 0.0
    step = max(1, len(frames) // 5)
    sample_frames = [frames[x] for x in range(0, len(frames), step)]
    sample_faces = [face_data[x] for x in range(0, len(face_data), step)]

    for frame, faces in zip(sample_frames, sample_faces):
        gray = np.array(frame.convert("L"), dtype=float)
        h, w = gray.shape
        cell_h = max(1, h // 4)
        cell_w = max(1, w // 4)

        face_mask = np.zeros((4, 4), dtype=bool)
        for face in faces:
            col = max(0, min(int(face["cx"] / max(w, 1) * 4), 3))
            row = max(0, min(int(face["cy"] / max(h, 1) * 4), 3))
            face_mask[row][col] = True

        high_variance_cells = 0
        for row in range(4):
            for col in range(4):
                if face_mask[row][col]:
                    continue
                cell = gray[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w]
                if float(np.var(cell)) > 1100:
                    high_variance_cells += 1

        # Require a smaller corner-style face (typical webcam overlay) + high-detail screen
        avg_face_size = (sum(f["size"] for f in faces) / len(faces)) if faces else 1.0
        if high_variance_cells >= 4 and avg_face_size < 0.25:
            screen_count += 1.0

    return float(screen_count) / max(len(sample_frames), 1) > 0.5


def _majority_face_count(face_data: list[list[dict[str, Any]]]) -> int:
    from collections import Counter

    if not face_data:
        return 0
    # Prefer counting frames that actually contain faces so B-roll doesn't hide speakers
    non_empty = [len(f) for f in face_data if len(f) > 0]
    if not non_empty:
        return 0
    counts = Counter(non_empty)
    return int(counts.most_common(1)[0][0])


def _filter_false_positives(face_data: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    return [[f for f in frame_faces if 0.03 <= float(f["size"]) <= 0.56] for frame_faces in face_data]


def _box_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    aw = float(a.get("w", a.get("h", 80.0)))
    ah = float(a.get("h", aw))
    bw = float(b.get("w", b.get("h", 80.0)))
    bh = float(b.get("h", bw))
    ax1, ay1 = float(a["cx"]) - aw / 2.0, float(a["cy"]) - ah / 2.0
    ax2, ay2 = float(a["cx"]) + aw / 2.0, float(a["cy"]) + ah / 2.0
    bx1, by1 = float(b["cx"]) - bw / 2.0, float(b["cy"]) - bh / 2.0
    bx2, by2 = float(b["cx"]) + bw / 2.0, float(b["cy"]) + bh / 2.0
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    if inter <= 0.0:
        return 0.0
    return inter / max(1.0, aw * ah + bw * bh - inter)


def _deduplicate_faces(face_data: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    cleaned = []
    for frame_faces in face_data:
        unique: list[dict[str, Any]] = []
        for face in frame_faces:
            fw = float(face.get("w", face.get("h", 80.0)))
            fh = float(face.get("h", fw))
            is_dup = False
            for ex in unique:
                ew = float(ex.get("w", ex.get("h", 80.0)))
                eh = float(ex.get("h", ew))
                dist = math.hypot(float(face["cx"]) - float(ex["cx"]), float(face["cy"]) - float(ex["cy"]))
                if _box_iou(face, ex) > 0.20 or dist < max(55.0, 0.60 * max(fw, fh, ew, eh)):
                    is_dup = True
                    break
            if not is_dup:
                unique.append(face)
        cleaned.append(unique)
    return cleaned


def _check_consistent_face_sides(
    face_data: list[list[dict[str, Any]]],
    vid_width: int,
) -> bool:
    """Check whether multi-face frames actually have distinct people separated horizontally."""
    two_face_frames = [f for f in face_data if len(f) >= 2]
    if len(two_face_frames) < 2:
        return False

    min_pixel_gap = max(100.0, vid_width * 0.24)
    separated = 0
    for frame in two_face_frames:
        ordered = sorted(frame, key=lambda f: float(f["cx"]))
        left_f, right_f = ordered[0], ordered[-1]
        gap = float(right_f["cx"]) - float(left_f["cx"])
        on_opposite_halves = float(left_f["cx"]) < vid_width * 0.48 and float(right_f["cx"]) > vid_width * 0.52
        not_giant_closeup = max(float(left_f.get("size", 0.0)), float(right_f.get("size", 0.0))) < 0.30
        if gap >= min_pixel_gap and on_opposite_halves and not_giant_closeup:
            separated += 1

    return (separated / len(two_face_frames)) >= 0.55
