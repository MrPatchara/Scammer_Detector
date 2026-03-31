"""Scalable auto-learning engine for correction generation, selection, and promotion."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .thai_phonetic_corrector import ThaiPhoneticCorrector


@dataclass
class PromotionResult:
    generated_candidates: int
    promoted_items: int


class AutoLearningEngine:
    """Store correction evidence in SQLite and auto-promote high-confidence mappings."""

    def __init__(self, db_path: str | Path, corrections_json_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.corrections_json_path = Path(corrections_json_path)
        self.corrector = ThaiPhoneticCorrector(confidence_threshold=0.78)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    wrong TEXT NOT NULL,
                    corrected TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 0,
                    average_score REAL NOT NULL DEFAULT 0,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (wrong, corrected)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS generated_candidates (
                    wrong TEXT NOT NULL,
                    corrected TEXT NOT NULL,
                    source TEXT NOT NULL,
                    support_count INTEGER NOT NULL DEFAULT 0,
                    average_score REAL NOT NULL DEFAULT 0,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (wrong, corrected)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promotions (
                    wrong TEXT NOT NULL,
                    corrected TEXT NOT NULL,
                    source TEXT NOT NULL,
                    promoted_at TEXT NOT NULL,
                    PRIMARY KEY (wrong, corrected)
                )
                """
            )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _upsert_running_average(
        self,
        conn: sqlite3.Connection,
        table: str,
        wrong: str,
        corrected: str,
        score: float,
        source: str | None = None,
    ) -> None:
        if table == "generated_candidates":
            current = conn.execute(
                """
                SELECT support_count, average_score
                FROM generated_candidates
                WHERE wrong = ? AND corrected = ?
                """,
                (wrong, corrected),
            ).fetchone()
        else:
            current = conn.execute(
                """
                SELECT seen_count, average_score
                FROM observations
                WHERE wrong = ? AND corrected = ?
                """,
                (wrong, corrected),
            ).fetchone()
        now = self._now_iso()

        if current is None:
            if table == "generated_candidates":
                conn.execute(
                    """
                    INSERT INTO generated_candidates
                    (wrong, corrected, source, support_count, average_score, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (wrong, corrected, source or "generated", 1, score, now),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO observations
                    (wrong, corrected, seen_count, average_score, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (wrong, corrected, 1, score, now),
                )
            return

        if table == "generated_candidates":
            count = int(current["support_count"])
            avg = float(current["average_score"])
            new_avg = ((avg * count) + score) / (count + 1)
            conn.execute(
                """
                UPDATE generated_candidates
                SET support_count = ?, average_score = ?, last_seen = ?
                WHERE wrong = ? AND corrected = ?
                """,
                (count + 1, new_avg, now, wrong, corrected),
            )
            return

        count = int(current["seen_count"])
        avg = float(current["average_score"])
        new_avg = ((avg * count) + score) / (count + 1)
        conn.execute(
            """
            UPDATE observations
            SET seen_count = ?, average_score = ?, last_seen = ?
            WHERE wrong = ? AND corrected = ?
            """,
            (count + 1, new_avg, now, wrong, corrected),
        )

    def _generate_candidates_for_target(
        self,
        conn: sqlite3.Connection,
        target_word: str,
        max_variants: int = 180,
    ) -> int:
        variants = self.corrector.generate_correction_candidates(target_word)
        created = 0
        for variant in variants[:max_variants]:
            wrong = variant.strip()
            corrected = target_word.strip()
            if not wrong or not corrected or wrong == corrected:
                continue
            score = SequenceMatcher(None, wrong, corrected).ratio()
            self._upsert_running_average(
                conn,
                table="generated_candidates",
                wrong=wrong,
                corrected=corrected,
                score=score,
                source="generated",
            )
            created += 1
        return created

    def record_corrections(self, corrections_info: list[dict[str, Any]]) -> int:
        """Create + collect evidence from observed corrections and generated variants."""
        if not corrections_info:
            return 0

        generated_total = 0
        with self._connect() as conn:
            for item in corrections_info:
                wrong = str(item.get("original", "")).strip().lower()
                corrected = str(item.get("corrected", "")).strip().lower()
                score = float(item.get("score", 0.0) or 0.0)

                if not wrong or not corrected or wrong == corrected:
                    continue

                # Collect observed evidence
                self._upsert_running_average(
                    conn,
                    table="observations",
                    wrong=wrong,
                    corrected=corrected,
                    score=score,
                )

                # Create candidate space around corrected target
                generated_total += self._generate_candidates_for_target(conn, corrected)

                # Also support exact seen pair as generated candidate evidence
                self._upsert_running_average(
                    conn,
                    table="generated_candidates",
                    wrong=wrong,
                    corrected=corrected,
                    score=score,
                    source="observed",
                )
        return generated_total

    def _select_promotable(
        self,
        min_count: int,
        min_score: float,
        max_batch: int,
    ) -> list[tuple[str, str, str]]:
        # Promote observed evidence aggressively, generated evidence conservatively.
        strict_generated_count = max(4, min_count + 2)
        strict_generated_score = min(0.995, min_score + 0.12)
        generated_limit = max(1, max_batch // 5)

        with self._connect() as conn:
            observed_rows = conn.execute(
                """
                SELECT wrong, corrected, seen_count AS cnt, average_score AS score
                FROM observations
                WHERE seen_count >= ? AND average_score >= ?
                ORDER BY seen_count DESC, average_score DESC
                LIMIT ?
                """,
                (min_count, min_score, max_batch),
            ).fetchall()

            generated_rows = conn.execute(
                """
                SELECT wrong, corrected, support_count AS cnt, average_score AS score
                FROM generated_candidates
                WHERE support_count >= ? AND average_score >= ?
                ORDER BY support_count DESC, average_score DESC
                LIMIT ?
                """,
                (strict_generated_count, strict_generated_score, generated_limit),
            ).fetchall()

        selected: list[tuple[str, str, str]] = []
        seen_wrong: set[str] = set()

        for row in observed_rows:
            wrong = str(row["wrong"]).strip()
            corrected = str(row["corrected"]).strip()
            if len(wrong) < 3 or len(corrected) < 3:
                continue
            if wrong in seen_wrong:
                continue
            selected.append((wrong, corrected, "observed"))
            seen_wrong.add(wrong)
            if len(selected) >= max_batch:
                return selected

        for row in generated_rows:
            wrong = str(row["wrong"]).strip()
            corrected = str(row["corrected"]).strip()
            if len(wrong) < 3 or len(corrected) < 3:
                continue
            if wrong in seen_wrong:
                continue
            selected.append((wrong, corrected, "generated"))
            seen_wrong.add(wrong)
            if len(selected) >= max_batch:
                break

        return selected

    def _load_corrections_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"common_errors": {}}
        if self.corrections_json_path.exists():
            try:
                with open(self.corrections_json_path, "r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    payload = loaded
            except Exception:
                return payload
        return payload

    def _flatten_existing_mappings(self, payload: dict[str, Any]) -> set[str]:
        known: set[str] = set()
        common_errors = payload.get("common_errors", {})
        if not isinstance(common_errors, dict):
            return known

        for category in common_errors.values():
            if not isinstance(category, dict):
                continue
            for wrong in category.keys():
                known.add(str(wrong).strip().lower())
        return known

    def promote(
        self,
        min_count: int = 2,
        min_score: float = 0.82,
        max_batch: int = 200,
    ) -> PromotionResult:
        payload = self._load_corrections_payload()
        common_errors = payload.setdefault("common_errors", {})
        if not isinstance(common_errors, dict):
            common_errors = {}
            payload["common_errors"] = common_errors

        learned_auto = common_errors.setdefault("learned_auto", {})
        if not isinstance(learned_auto, dict):
            learned_auto = {}
            common_errors["learned_auto"] = learned_auto

        known_wrong = self._flatten_existing_mappings(payload)
        candidates = self._select_promotable(
            min_count=max(1, min_count),
            min_score=max(0.0, min(min_score, 1.0)),
            max_batch=max(1, max_batch),
        )

        promoted = 0
        with self._connect() as conn:
            for wrong, corrected, source in candidates:
                if wrong in known_wrong:
                    continue

                already = conn.execute(
                    "SELECT 1 FROM promotions WHERE wrong = ? AND corrected = ?",
                    (wrong, corrected),
                ).fetchone()
                if already is not None:
                    continue

                learned_auto[wrong] = corrected
                known_wrong.add(wrong)
                conn.execute(
                    "INSERT INTO promotions (wrong, corrected, source, promoted_at) VALUES (?, ?, ?, ?)",
                    (wrong, corrected, source, self._now_iso()),
                )
                promoted += 1

        if promoted > 0:
            self.corrections_json_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.corrections_json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

        return PromotionResult(generated_candidates=len(candidates), promoted_items=promoted)

    def get_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            obs = int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
            gen = int(conn.execute("SELECT COUNT(*) FROM generated_candidates").fetchone()[0])
            promoted = int(conn.execute("SELECT COUNT(*) FROM promotions").fetchone()[0])
        return {
            "observed_pairs": obs,
            "generated_pairs": gen,
            "promoted_pairs": promoted,
        }
