from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chapter:
    start: float
    end: float
    title: str


TIMESTAMP_LINE = re.compile(
    r"(?m)^\s*(?P<time>(?:\d{1,2}:)?\d{1,2}:\d{2})\s*(?:[-–—:|]\s*)?(?P<title>[^\r\n]+?)\s*$"
)


def _seconds(value: str) -> float:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    return float(parts[0] * 3600 + parts[1] * 60 + parts[2])


def chapters_from_info(info: dict) -> list[Chapter]:
    duration = float(info.get("duration") or 0)
    native = info.get("chapters") or []
    chapters: list[Chapter] = []
    for index, item in enumerate(native):
        start = float(item.get("start_time") or 0)
        end = float(item.get("end_time") or 0)
        if end <= start and index + 1 < len(native):
            end = float(native[index + 1].get("start_time") or 0)
        if end <= start:
            end = duration
        title = str(item.get("title") or f"Faixa {index + 1}").strip()
        if end > start and title:
            chapters.append(Chapter(start, end, title))
    if chapters:
        return chapters

    matches = list(TIMESTAMP_LINE.finditer(str(info.get("description") or "")))
    points: list[tuple[float, str]] = []
    for match in matches:
        start = _seconds(match.group("time"))
        title = match.group("title").strip(" -–—:|")
        if title and start < duration and (not points or start > points[-1][0]):
            points.append((start, title))
    if len(points) < 2:
        return []
    for index, (start, title) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else duration
        if end - start >= 2:
            chapters.append(Chapter(start, end, title))
    return chapters
