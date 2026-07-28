"""Produce privacy-preserving usage statistics for the ClipVault database."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "clipboard.db"


def percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * ratio)]


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def main() -> None:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = [dict(row) for row in db.execute("SELECT * FROM items ORDER BY id")]
    db.close()

    textual = [
        row
        for row in rows
        if row["kind"] in {"text", "url"} and (row.get("content") or "").strip()
    ]
    dates = [datetime.fromisoformat(row["created_at"]) for row in rows]
    lengths = [len(row["content"]) for row in textual]
    normalized = [normalize(row["content"]) for row in textual]
    frequencies = Counter(normalized)
    days = Counter(value.date().isoformat() for value in dates)
    hours = Counter(value.hour for value in dates)
    sources = Counter(row.get("source_app") or "unidentified" for row in rows)
    kinds = Counter(row["kind"] for row in rows)
    source_kinds = Counter(
        ((row.get("source_app") or "unidentified"), row["kind"]) for row in rows
    )
    gaps = [
        (dates[index] - dates[index - 1]).total_seconds()
        for index in range(1, len(dates))
        if dates[index] >= dates[index - 1]
    ]

    sensitive_pattern = re.compile(
        r"(password|passwd|api[_ -]?key|token|secret|verification code|"
        r"\u9a8c\u8bc1\u7801|\u5bc6\u7801)\s*[:=\uff1a]",
        re.IGNORECASE,
    )
    code_pattern = re.compile(
        r"```|^\s*(def |class |function |const |let |var |SELECT |INSERT |"
        r"UPDATE |DELETE |npm |pip |git |docker )",
        re.IGNORECASE | re.MULTILINE,
    )
    image_hashes = []
    for row in rows:
        image_path = row.get("image_path")
        if row["kind"] == "image" and image_path and os.path.exists(image_path):
            with open(image_path, "rb") as image_file:
                image_hashes.append(hashlib.sha256(image_file.read()).hexdigest())
    image_frequencies = Counter(image_hashes)

    sessions = []
    current_session = []
    for index, row in enumerate(rows):
        if index and (dates[index] - dates[index - 1]).total_seconds() > 30 * 60:
            if current_session:
                sessions.append(current_session)
            current_session = []
        current_session.append(row)
    if current_session:
        sessions.append(current_session)

    result = {
        "total": len(rows),
        "kinds": dict(kinds),
        "favorites": sum(int(row.get("favorite") or 0) for row in rows),
        "date_min": min(dates).isoformat(sep=" ") if dates else None,
        "date_max": max(dates).isoformat(sep=" ") if dates else None,
        "active_days": len(days),
        "daily_median": statistics.median(days.values()) if days else 0,
        "daily_max": max(days.values()) if days else 0,
        "top_days": days.most_common(5),
        "top_hours": hours.most_common(6),
        "sources": sources.most_common(),
        "source_kinds": [
            {"source": source, "kind": kind, "count": count}
            for (source, kind), count in source_kinds.most_common()
        ],
        "source_missing": sources["unidentified"],
        "textual": len(textual),
        "length_median": statistics.median(lengths) if lengths else 0,
        "length_p90": percentile(lengths, 0.90),
        "length_max": max(lengths) if lengths else 0,
        "short_le_30": sum(len(row["content"].strip()) <= 30 for row in textual),
        "long_ge_500": sum(len(row["content"]) >= 500 for row in textual),
        "multiline": sum("\n" in row["content"] for row in textual),
        "url_shape": sum(
            bool(re.match(r"^https?://\S+$", row["content"].strip(), re.IGNORECASE))
            for row in textual
        ),
        "code_like": sum(bool(code_pattern.search(row["content"])) for row in textual),
        "duplicate_extra": sum(count - 1 for count in frequencies.values() if count > 1),
        "duplicate_groups": sum(count > 1 for count in frequencies.values()),
        "max_repeat": max(frequencies.values()) if frequencies else 0,
        "consecutive_duplicates": sum(
            normalized[index] == normalized[index - 1]
            for index in range(1, len(normalized))
        ),
        "possible_sensitive": sum(
            bool(sensitive_pattern.search(row["content"])) for row in textual
        ),
        "numeric_only": sum(
            bool(re.fullmatch(r"[\d\s+().-]{4,20}", row["content"].strip()))
            for row in textual
        ),
        "email_like": sum(
            bool(re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", row["content"]))
            for row in textual
        ),
        "path_like": sum(
            bool(re.match(r"^[A-Za-z]:\\|^/[^\s]+", row["content"].strip()))
            for row in textual
        ),
        "image_duplicate_extra": sum(
            count - 1 for count in image_frequencies.values() if count > 1
        ),
        "image_duplicate_groups": sum(
            count > 1 for count in image_frequencies.values()
        ),
        "sessions_30m": len(sessions),
        "session_size_median": (
            statistics.median(len(session) for session in sessions) if sessions else 0
        ),
        "session_size_max": max((len(session) for session in sessions), default=0),
        "median_gap_seconds": statistics.median(gaps) if gaps else 0,
        "image_bytes": sum(
            os.path.getsize(row["image_path"])
            for row in rows
            if row.get("image_path") and os.path.exists(row["image_path"])
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
