"""
Simulation_app/app.py
=====================
QuizComp Guided Simulation Study

This Streamlit app helps a participant live the QuizComp experience without
triggering a fresh quiz composition. The flow is:

1. Study briefing
2. Consent and data use
3. Platform walkthrough with editable controls and no live composition
4. Replay of one recorded real-user session from the cleaned QuizComp SQLite DB
5. Post-study survey answered by the current participant

Usage:
    streamlit run app.py

Optional direct preview:
    http://localhost:8501/?persona_id=3
    http://localhost:8501/?replay_session_id=5
"""

import csv
import inspect
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[3] / ".env")


ROOT = Path(__file__).parent.resolve()
LOCAL_DATA_DIR = ROOT / "data"
SURVEY_DB_FILE = Path(os.environ.get("REVIEWER_OUTPUT_DIR", str(ROOT))) / "simulation_survey_responses.db"


def resolve_first_existing_path(*candidates):
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


SOURCE_DB_FILE = resolve_first_existing_path(
    ROOT.parents[1] / "report/data/actors/quizcomp/quizcomp_study.sqlite",
    ROOT.parent / "quizcomp_llm_study" / "results" / "dbs" / "Crowd db" / "quizcomp_study.sqlite",
    ROOT.parent / "quizcomp_study.sqlite",
)


MCQ_DATA_DIR = resolve_first_existing_path(
    LOCAL_DATA_DIR,
    ROOT.parent / "data",
)
ROOT_MCQ_FILE = resolve_first_existing_path(
    LOCAL_DATA_DIR / "math.csv",
    ROOT / "math.csv",
    ROOT.parent / "data" / "math.csv",
)

PROLIFIC_PID_QUERY_PARAMS = ("PROLIFIC_PID", "prolific_pid")
STUDY_ID_QUERY_PARAMS = ("STUDY_ID", "study_id")
SESSION_ID_QUERY_PARAMS = ("SESSION_ID", "session_id")
PROLIFIC_COMPLETION_CODE_QUERY_PARAMS = ("PROLIFIC_COMPLETION_CODE", "completion_code", "cc")

TOPIC_NAMES = [
    "Differentiation",
    "Integration",
    "Fundamental Mathematics",
    "Complex Numbers",
    "Optimization",
    "Differential Equations",
    "Probability",
    "Linear Algebra",
    "Numerical Methods",
    "Discrete Mathematics",
]

DIFFICULTY_LABELS = [
    "Very Easy (L1)",
    "Easy (L2)",
    "Medium (L3)",
    "Medium (L4)",
    "Hard (L5)",
    "Very Hard (L6)",
]

PHASES = ["landing", "consent", "walkthrough", "replay", "survey", "done"]
PHASE_LABELS = {
    "landing": "Study briefing",
    "consent": "Consent",
    "walkthrough": "Platform walkthrough",
    "replay": "Recorded session replay",
    "survey": "Post-study survey",
    "done": "Finish",
}

STUDY_STAGES = [
    {
        "label": "Start",
        "title": "Briefing and consent",
        "description": "Understand the study goal and confirm participation.",
        "phases": {"landing", "consent"},
    },
    {
        "label": "Phase 1",
        "title": "Guided exploration",
        "description": "Learn how quiz requests are configured before observation begins.",
        "phases": {"walkthrough"},
    },
    {
        "label": "Phase 2",
        "title": "Observe attempts",
        "description": "Move through the recorded interaction one attempt at a time.",
        "phases": {"replay"},
    },
    {
        "label": "Phase 3",
        "title": "Final questionnaire",
        "description": "Evaluate the observed experience from that learner's perspective.",
        "phases": {"survey", "done"},
    },
]

INTRO_PHASE_CARDS = [
    {
        "label": "Phase 1",
        "title": "Guided exploration",
        "description": "Start with a short tutorial that explains how the platform works and how interactions are performed. No real experience evaluation is required yet.",
    },
    {
        "label": "Phase 2",
        "title": "Observe recorded attempts",
        "description": "Then watch the full interaction attempts captured in one recorded learner session. Navigate through the attempts one by one and observe the process carefully.",
    },
    {
        "label": "Phase 3",
        "title": "Evaluate the experience",
        "description": "At the end, answer the questionnaire based on what you observed, as if you had been the person performing the interaction.",
    },
]

CACHE_DECORATOR = st.cache_data
BUTTON_PARAMS = inspect.signature(st.button).parameters
MODERN_STREAMLIT = hasattr(st, "cache_data")
DISPLAY_MATH_PATTERN = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


@CACHE_DECORATOR
def load_simulation_data():
    if not SOURCE_DB_FILE.exists():
        return {
            "source_db": str(SOURCE_DB_FILE),
            "num_personas": 0,
            "description": "QuizComp real-user study sessions",
        }, []

    with sqlite3.connect(SOURCE_DB_FILE) as connection:
        connection.row_factory = sqlite3.Row
        session_rows = connection.execute(
            """
            SELECT
                s.id AS session_id,
                s.user_id,
                u.username,
                COALESCE(NULLIF(TRIM(s.prolific_pid), ''), u.username) AS prolific_pid,
                s.total_time_s,
                sr.q1_accomplishment,
                sr.q2_effort,
                sr.q3_mental_demand,
                sr.q4_controllability,
                sr.q5_temporal_demand,
                sr.q6_satisfaction_trust,
                sr.q7_would_use_again,
                sr.comments
            FROM study_sessions s
            JOIN users u ON u.id = s.user_id
            JOIN survey_responses sr ON sr.session_id = s.id
            WHERE s.status = 'completed'
              AND EXISTS (SELECT 1 FROM compose_attempts c WHERE c.session_id = s.id)
            ORDER BY s.id
            """
        ).fetchall()

        session_ids = [safe_int(row["session_id"], -1) for row in session_rows]
        if not session_ids:
            return {
                "source_db": str(SOURCE_DB_FILE),
                "num_personas": 0,
                "description": "QuizComp real-user study sessions",
            }, []

        placeholders = ", ".join("?" for _ in session_ids)
        attempt_rows = connection.execute(
            f"""
            SELECT
            session_id,
            mode,
            data_uuid,
            teacher_topic_json,
            teacher_level_json,
            num_mcqs,
            mcq_ids_json,
                topic_coverage_json,
                difficulty_coverage_json,
                target_match,
                api_time_s,
                ts
            FROM compose_attempts
            WHERE session_id IN ({placeholders})
            ORDER BY session_id, id
            """,
            session_ids,
        ).fetchall()

    attempts_by_session = {}
    for row in attempt_rows:
        attempt = dict(row)
        for field_name in (
            "teacher_topic_json",
            "teacher_level_json",
            "mcq_ids_json",
            "topic_coverage_json",
            "difficulty_coverage_json",
        ):
            attempt[field_name] = parse_json_list(attempt.get(field_name))
        session_id = safe_int(attempt.get("session_id"), -1)
        attempts_by_session.setdefault(session_id, []).append(attempt)

    personas = []
    for persona_id, row in enumerate(session_rows):
        session_id = safe_int(row["session_id"], -1)
        compose_attempts = attempts_by_session.get(session_id, [])
        personas.append(
            {
                "persona_id": persona_id,
                "replay_session_id": session_id,
                "session_id": session_id,
                "user_id": safe_int(row["user_id"], -1),
                "username": row["username"],
                "prolific_pid": row["prolific_pid"],
                "total_time_s": float(row["total_time_s"] or 0.0),
                "num_compose_attempts": len(compose_attempts),
                "compose_attempts": compose_attempts,
                "survey_q1_accomplishment": row["q1_accomplishment"],
                "survey_q2_effort": row["q2_effort"],
                "survey_q3_mental_demand": row["q3_mental_demand"],
                "survey_q4_controllability": row["q4_controllability"],
                "survey_q5_temporal_demand": row["q5_temporal_demand"],
                "survey_q6_satisfaction": row["q6_satisfaction_trust"],
                "survey_q7_would_use_again": row["q7_would_use_again"],
                "survey_comments": row["comments"],
            }
        )

    return {
        "source_db": str(SOURCE_DB_FILE),
        "num_personas": len(personas),
        "description": "QuizComp completed real-user study sessions",
    }, personas


def safe_int(value, default=0):
    try:
        return int(str(value).strip())
    except (AttributeError, TypeError, ValueError):
        return default


def parse_json_list(value):
    if isinstance(value, list):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


@lru_cache(maxsize=None)
def load_mcq_lookup_from_csv(csv_path_str):
    csv_path = Path(csv_path_str)
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = ";"
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=";,|\t,").delimiter
        except csv.Error:
            pass
        reader = csv.DictReader(handle, delimiter=delimiter)
        rows = list(reader)

    lookup = {}
    for index, row in enumerate(rows):
        record = dict(row)
        if not record.get("id"):
            record["id"] = str(index)

        difficulty = safe_int(record.get("difficulty") or record.get("difficulty_level"), 0)
        record["difficulty"] = difficulty
        record["difficulty_level"] = difficulty
        record["correct_option"] = record.get("correct_option") or record.get("correct_answer") or ""
        record["options"] = [
            option
            for option in [
                record.get("option_a") or record.get("correct_answer"),
                record.get("option_b") or record.get("answer2"),
                record.get("option_c") or record.get("answer3"),
                record.get("option_d") or record.get("answer4"),
            ]
            if option not in (None, "")
        ]

        mcq_id = safe_int(record.get("mcq_id") or record.get("id"), -1)
        if mcq_id < 0:
            continue

        lookup[mcq_id] = record
        local_id = safe_int(record.get("id"), mcq_id)
        if local_id != mcq_id:
            lookup[local_id] = record

    return lookup


@lru_cache(maxsize=None)
def load_root_mcq_lookup():
    if ROOT_MCQ_FILE.exists():
        return load_mcq_lookup_from_csv(str(ROOT_MCQ_FILE))
    return {}


def get_attempt_mcq_ids(attempt):
    raw_ids = attempt.get("mcq_ids_json")
    if isinstance(raw_ids, list):
        return [mcq_id for mcq_id in (safe_int(item, -1) for item in raw_ids) if mcq_id >= 0]
    return []


def normalize_quiz_item(item):
    options = item.get("options") or [
        item.get("option_a"),
        item.get("option_b"),
        item.get("option_c"),
        item.get("option_d"),
    ]
    difficulty = safe_int(item.get("difficulty") or item.get("difficulty_level"), 0)
    return {
        "id": safe_int(item.get("id", item.get("mcq_id")), 0),
        "question": item.get("question") or "Question unavailable",
        "options": [option for option in options if option not in (None, "")],
        "topic_name": item.get("topic_name") or "Unknown topic",
        "difficulty": difficulty,
        "difficulty_level": difficulty,
        "correct_option": item.get("correct_option") or item.get("correct_answer") or "",
    }


def resolve_attempt_mcq_lookup(attempt):
    data_uuid = str(attempt.get("data_uuid") or "").strip()
    if data_uuid:
        candidate = MCQ_DATA_DIR / data_uuid / f"mcqs_{data_uuid}.csv"
        if candidate.exists():
            return load_mcq_lookup_from_csv(str(candidate))
    return load_root_mcq_lookup() or {}


def get_attempt_quiz_items(attempt):
    embedded_items = attempt.get("quiz_items")
    if isinstance(embedded_items, list) and embedded_items:
        return [normalize_quiz_item(item) for item in embedded_items if isinstance(item, dict)]

    mcq_ids = get_attempt_mcq_ids(attempt)
    mcq_lookup = resolve_attempt_mcq_lookup(attempt)
    quiz_items = []
    if mcq_lookup and mcq_ids:
        for mcq_id in mcq_ids:
            record = mcq_lookup.get(int(mcq_id))
            if record is None:
                quiz_items.append(
                    {
                        "id": int(mcq_id),
                        "question": f"MCQ {mcq_id}",
                        "options": [],
                        "topic_name": "Unknown topic",
                        "difficulty": 0,
                        "correct_option": "",
                    }
                )
            else:
                quiz_items.append(normalize_quiz_item(record))

        if any(item.get("options") or item.get("difficulty", 0) > 0 for item in quiz_items):
            return quiz_items

    return []


def ensure_response_db():
    SURVEY_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SURVEY_DB_FILE) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS simulations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id INTEGER NOT NULL,
                run_number INTEGER NOT NULL,
                session_id TEXT NOT NULL UNIQUE,
                source_session_id TEXT,

                num_iterations INTEGER,
                final_quiz_size INTEGER,
                final_match_quality REAL,

                duration_seconds REAL,
                timestamp TEXT,
                prompt_type TEXT,
                model_name TEXT,

                reviewed_attempts_json TEXT,
                total_tokens INTEGER DEFAULT 0,
                total_llm_calls INTEGER DEFAULT 0,

                UNIQUE(persona_id, run_number, prompt_type, model_name)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS composition_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                simulation_id INTEGER NOT NULL,
                iteration INTEGER NOT NULL,

                action TEXT,
                mode TEXT,
                topic_distribution_json TEXT,
                difficulty_distribution_json TEXT,
                reasoning TEXT,

                num_mcqs INTEGER,
                target_match REAL,
                generation_time_s REAL,

                timestamp TEXT,

                FOREIGN KEY(simulation_id) REFERENCES simulations(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_survey_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                simulation_id INTEGER NOT NULL,

                q1_accomplishment INTEGER,
                q2_effort INTEGER,
                q3_mental_demand INTEGER,
                q4_controllability INTEGER,
                q5_temporal_demand INTEGER,
                q6_satisfaction INTEGER,
                q7_would_use_again INTEGER,
                comments TEXT,

                timestamp TEXT,

                FOREIGN KEY(simulation_id) REFERENCES simulations(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS participant_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_key TEXT NOT NULL UNIQUE,
                prolific_pid TEXT,
                study_id TEXT,
                prolific_session_id TEXT,
                assignment_source TEXT NOT NULL,
                replay_session_id INTEGER NOT NULL,
                source_user_id INTEGER,
                source_username TEXT,
                replay_index INTEGER NOT NULL,
                assigned_at TEXT NOT NULL,
                completed_simulation_id INTEGER,
                completed_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_participant_assignments_replay_session_id
            ON participant_assignments (replay_session_id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_assignment_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                next_catalog_index INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        simulation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(simulations)").fetchall()
        }
        for column_name, column_type in (
            ("participant_key", "TEXT"),
            ("observer_prolific_pid", "TEXT"),
            ("observer_study_id", "TEXT"),
            ("observer_session_id", "TEXT"),
            ("source_user_id", "INTEGER"),
            ("source_username", "TEXT"),
        ):
            if column_name not in simulation_columns:
                connection.execute(f"ALTER TABLE simulations ADD COLUMN {column_name} {column_type}")

        assignment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(participant_assignments)").fetchall()
        }
        for column_name, column_type in (
            ("source_user_id", "INTEGER"),
            ("source_username", "TEXT"),
        ):
            if column_name not in assignment_columns:
                connection.execute(f"ALTER TABLE participant_assignments ADD COLUMN {column_name} {column_type}")

        connection.commit()


def next_run_number(connection, persona_id, prompt_type, model_name):
    row = connection.execute(
        """
        SELECT COALESCE(MAX(run_number), 0) + 1
        FROM simulations
        WHERE persona_id = ? AND prompt_type = ? AND model_name = ?
        """,
        (persona_id, prompt_type, model_name),
    ).fetchone()
    return int(row[0])


def save_simulation_response(persona, attempts, metadata, responses, reviewed_attempts, duration_seconds):
    ensure_response_db()

    persona_id = int(persona.get("persona_id", 0))
    participant_context = st.session_state.get("participant_context") or get_participant_context()
    prompt_type = ""
    model_name = ""
    if metadata:
        prompt_type = str((metadata.get("prompt_types") or [""])[0])
        model_name = str((metadata.get("model_names") or [""])[0])

    final_attempt = attempts[-1] if attempts else {}
    unique_session_id = f"simulation_{persona_id}_{uuid4().hex}"

    with sqlite3.connect(SURVEY_DB_FILE) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        existing_assignment = connection.execute(
            "SELECT completed_simulation_id FROM participant_assignments WHERE participant_key = ?",
            (participant_context.get("participant_key"),),
        ).fetchone()
        if existing_assignment is not None and existing_assignment["completed_simulation_id"]:
            return str(SURVEY_DB_FILE)

        run_number = next_run_number(connection, persona_id, prompt_type, model_name)
        cursor = connection.execute(
            """
            INSERT INTO simulations (
                persona_id,
                run_number,
                session_id,
                source_session_id,
                num_iterations,
                final_quiz_size,
                final_match_quality,
                duration_seconds,
                timestamp,
                prompt_type,
                model_name,
                reviewed_attempts_json,
                total_tokens,
                total_llm_calls,
                participant_key,
                observer_prolific_pid,
                observer_study_id,
                observer_session_id,
                source_user_id,
                source_username
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persona_id,
                run_number,
                unique_session_id,
                persona.get("session_id"),
                len(attempts),
                int(final_attempt.get("num_mcqs", 0) or 0),
                float(final_attempt.get("target_match", 0) or 0),
                float(duration_seconds),
                now_iso(),
                prompt_type,
                model_name,
                json.dumps(list(reviewed_attempts), ensure_ascii=True),
                0,
                0,
                participant_context.get("participant_key"),
                participant_context.get("prolific_pid"),
                participant_context.get("study_id"),
                participant_context.get("prolific_session_id"),
                persona.get("user_id"),
                persona.get("username"),
            ),
        )
        simulation_id = int(cursor.lastrowid)

        for iteration, attempt in enumerate(attempts, start=1):
            connection.execute(
                """
                INSERT INTO composition_attempts (
                    simulation_id,
                    iteration,
                    action,
                    mode,
                    topic_distribution_json,
                    difficulty_distribution_json,
                    reasoning,
                    num_mcqs,
                    target_match,
                    generation_time_s,
                    timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    simulation_id,
                    iteration,
                    "replay_review",
                    attempt.get("mode"),
                    json.dumps(attempt.get("teacher_topic_json", []), ensure_ascii=True),
                    json.dumps(attempt.get("teacher_level_json", []), ensure_ascii=True),
                    None,
                    int(attempt.get("num_mcqs", 0) or 0),
                    float(attempt.get("target_match", 0) or 0),
                    float(attempt.get("api_time_s", 0) or 0),
                    attempt.get("ts") or now_iso(),
                ),
            )

        connection.execute(
            """
            INSERT INTO llm_survey_responses (
                simulation_id,
                q1_accomplishment,
                q2_effort,
                q3_mental_demand,
                q4_controllability,
                q5_temporal_demand,
                q6_satisfaction,
                q7_would_use_again,
                comments,
                timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                simulation_id,
                responses.get("q1"),
                responses.get("q2"),
                responses.get("q3"),
                responses.get("q4"),
                responses.get("q5"),
                responses.get("q6"),
                responses.get("q7"),
                responses.get("comments"),
                now_iso(),
            ),
        )

        connection.execute(
            """
            UPDATE participant_assignments
            SET completed_simulation_id = ?, completed_at = ?
            WHERE participant_key = ?
            """,
            (
                simulation_id,
                now_iso(),
                participant_context.get("participant_key"),
            ),
        )
        connection.commit()

    return str(SURVEY_DB_FILE)


def get_query_param(name, default):
    qp = getattr(st, "query_params", None)
    if qp is not None:
        value = qp.get(name, default)
        return str(value[0]) if isinstance(value, list) else str(value)
    getter = getattr(st, "experimental_get_query_params", None)
    if getter:
        value = getter().get(name, [default])
        return str(value[0]) if value else str(default)
    return str(default)


def get_first_query_param(names, default=""):
    for name in names:
        value = str(get_query_param(name, "") or "").strip()
        if value:
            return value
    return default


def get_participant_context():
    prolific_pid = get_first_query_param(PROLIFIC_PID_QUERY_PARAMS, "")
    study_id = get_first_query_param(STUDY_ID_QUERY_PARAMS, "")
    prolific_session_id = get_first_query_param(SESSION_ID_QUERY_PARAMS, "")

    if prolific_pid:
        participant_key = f"prolific:{prolific_pid}"
        assignment_source = "prolific"
    else:
        browser_session_key = st.session_state.setdefault("_assignment_session_key", uuid4().hex)
        participant_key = f"session:{browser_session_key}"
        assignment_source = "browser_session"

    return {
        "participant_key": participant_key,
        "prolific_pid": prolific_pid or None,
        "study_id": study_id or None,
        "prolific_session_id": prolific_session_id or None,
        "assignment_source": assignment_source,
    }


def get_prolific_completion_code():
    query_code = get_first_query_param(PROLIFIC_COMPLETION_CODE_QUERY_PARAMS, "")
    if query_code:
        return query_code
    return str(os.getenv("PROLIFIC_COMPLETION_CODE", "") or "").strip()


def get_prolific_completion_url():
    participant_context = st.session_state.get("participant_context") or get_participant_context()
    if participant_context.get("assignment_source") != "prolific":
        return None

    completion_code = get_prolific_completion_code()
    if not completion_code:
        return None
    return f"https://app.prolific.com/submissions/complete?cc={completion_code}"


def get_persona_index_by_replay_session_id(personas, replay_session_id):
    replay_session_id = safe_int(replay_session_id, -1)
    for idx, persona in enumerate(personas):
        if safe_int(persona.get("replay_session_id"), -1) == replay_session_id:
            return idx
    return -1


def get_explicit_replay_session_id(personas):
    replay_ids = [safe_int(item.get("replay_session_id"), -1) for item in personas]

    requested_session_id = safe_int(get_query_param("replay_session_id", -1), -1)
    if requested_session_id in replay_ids:
        return requested_session_id

    requested_index = safe_int(get_query_param("persona_id", -1), -1)
    if 0 <= requested_index < len(personas):
        explicit_persona_id = str(get_query_param("persona_id", "") or "").strip()
        if explicit_persona_id:
            return safe_int(personas[requested_index].get("replay_session_id"), -1)

    return -1


def get_participant_assignment(participant_context):
    participant_key = (participant_context or {}).get("participant_key")
    if not participant_key:
        return None

    ensure_response_db()
    with sqlite3.connect(SURVEY_DB_FILE) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM participant_assignments WHERE participant_key = ?",
            (participant_key,),
        ).fetchone()
    return dict(row) if row is not None else None


def allocate_replay_session_id(personas, participant_context):
    ensure_response_db()
    if not personas:
        return -1

    participant_key = participant_context["participant_key"]
    with sqlite3.connect(SURVEY_DB_FILE, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")

        existing_row = connection.execute(
            """
            SELECT replay_session_id
            FROM participant_assignments
            WHERE participant_key = ?
            """,
            (participant_key,),
        ).fetchone()
        if existing_row is not None:
            return safe_int(existing_row["replay_session_id"], -1)

        assigned_replay_ids = {
            safe_int(row[0], -1)
            for row in connection.execute("SELECT replay_session_id FROM participant_assignments").fetchall()
        }

        state_row = connection.execute(
            "SELECT next_catalog_index FROM replay_assignment_state WHERE singleton = 1"
        ).fetchone()
        next_index = safe_int(state_row["next_catalog_index"], 0) if state_row is not None else 0

        replay_index = -1
        replay_session_id = -1
        assigned_persona = None
        for candidate_index in range(max(0, next_index), len(personas)):
            candidate_persona = personas[candidate_index]
            candidate_session_id = safe_int(candidate_persona.get("replay_session_id"), -1)
            if candidate_session_id not in assigned_replay_ids:
                replay_index = candidate_index
                replay_session_id = candidate_session_id
                assigned_persona = candidate_persona
                break

        if replay_index < 0:
            final_index = len(personas)
            if state_row is None:
                connection.execute(
                    """
                    INSERT INTO replay_assignment_state (singleton, next_catalog_index, updated_at)
                    VALUES (1, ?, ?)
                    """,
                    (final_index, now_iso()),
                )
            else:
                connection.execute(
                    """
                    UPDATE replay_assignment_state
                    SET next_catalog_index = ?, updated_at = ?
                    WHERE singleton = 1
                    """,
                    (final_index, now_iso()),
                )
            return -1

        connection.execute(
            """
            INSERT INTO participant_assignments (
                participant_key,
                prolific_pid,
                study_id,
                prolific_session_id,
                assignment_source,
                replay_session_id,
                source_user_id,
                source_username,
                replay_index,
                assigned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                participant_key,
                participant_context.get("prolific_pid"),
                participant_context.get("study_id"),
                participant_context.get("prolific_session_id"),
                participant_context.get("assignment_source") or "unknown",
                replay_session_id,
                assigned_persona.get("user_id") if assigned_persona else None,
                assigned_persona.get("username") if assigned_persona else None,
                replay_index,
                now_iso(),
            ),
        )

        updated_index = replay_index + 1
        if state_row is None:
            connection.execute(
                """
                INSERT INTO replay_assignment_state (singleton, next_catalog_index, updated_at)
                VALUES (1, ?, ?)
                """,
                (updated_index, now_iso()),
            )
        else:
            connection.execute(
                """
                UPDATE replay_assignment_state
                SET next_catalog_index = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (updated_index, now_iso()),
            )

    return replay_session_id


def get_initial_replay_session_id(personas, participant_assignment):
    explicit_replay_session_id = get_explicit_replay_session_id(personas)
    if explicit_replay_session_id >= 0:
        return explicit_replay_session_id

    if participant_assignment is not None:
        return safe_int(participant_assignment.get("replay_session_id"), -1)

    return -1


def assign_replay_for_participant(personas, participant_context):
    explicit_replay_session_id = get_explicit_replay_session_id(personas)
    if explicit_replay_session_id >= 0:
        return explicit_replay_session_id, False, ""

    participant_assignment = get_participant_assignment(participant_context)
    if participant_assignment is not None:
        return safe_int(participant_assignment.get("replay_session_id"), -1), False, ""

    if (participant_context or {}).get("assignment_source") != "prolific":
        return (
            -1,
            False,
            "Use the Prolific study link to start this public study. For a manual preview, add persona_id or replay_session_id to the URL.",
        )

    replay_session_id = allocate_replay_session_id(personas, participant_context)
    if replay_session_id < 0:
        return (
            -1,
            True,
            "All available teacher sessions have already been assigned. This study link is no longer accepting new participants.",
        )

    return replay_session_id, False, ""


def rerun_app():
    rerun = getattr(st, "rerun", None)
    if rerun is not None:
        rerun()
        return
    experimental_rerun = getattr(st, "experimental_rerun", None)
    if experimental_rerun is not None:
        experimental_rerun()


def button_compat(container, label, **kwargs):
    if "use_container_width" not in BUTTON_PARAMS:
        kwargs.pop("use_container_width", None)
    if "type" not in BUTTON_PARAMS:
        kwargs.pop("type", None)
    return container.button(label, **kwargs)


def update_single_choice_checkbox(state_key, selected_value, options):
    widget_key = f"{state_key}_{selected_value}_choice"
    if st.session_state.get(widget_key):
        st.session_state[state_key] = selected_value
    elif st.session_state.get(state_key) == selected_value:
        st.session_state[state_key] = None

    current = st.session_state.get(state_key)
    for option in options:
        st.session_state[f"{state_key}_{option}_choice"] = option == current


def pct(value):
    return f"{value * 100:.0f}%"


def score_color(score):
    if score >= 0.85:
        return "#22c55e"
    if score >= 0.70:
        return "#f59e0b"
    return "#ef4444"


def coverage_status(delta):
    if delta > 0.01:
        return "Above target"
    if delta < -0.01:
        return "Below target"
    return "On target"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def format_assigned_learner_label(selected_idx):
    if safe_int(selected_idx, -1) < 0:
        return "Pending assignment"
    return f"Learner {int(selected_idx) + 1}"


def mini_bar_html(value, color="#3b82f6", width="100%"):
    pct_width = max(0, min(1, float(value))) * 100
    return (
        f"<div style='width:{width};height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden;'>"
        f"<div style='width:{pct_width:.1f}%;height:100%;background:{color};border-radius:999px;'></div>"
        f"</div>"
    )


def format_quiz_id(mcq_ids):
    normalized_ids = [str(qid).strip() for qid in (mcq_ids or []) if str(qid).strip()]
    if not normalized_ids:
        return "Quiz ID unavailable"
    return "QZ-" + "-".join(normalized_ids)


def normalize_math_text(text):
    normalized = str(text or "").strip()
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\\[", "$$").replace("\\]", "$$")
    normalized = normalized.replace("\\(", "$").replace("\\)", "$")
    return normalized


def render_mixed_content(text):
    normalized = normalize_math_text(text)
    parts = DISPLAY_MATH_PATTERN.split(normalized)

    for idx, part in enumerate(parts):
        content = part.strip()
        if not content:
            continue

        if idx % 2 == 1:
            st.latex(content)
            continue

        for paragraph in re.split(r"\n\s*\n", content):
            paragraph_text = paragraph.strip()
            if paragraph_text:
                st.markdown(paragraph_text)


def format_attempt_mode(mode, att_idx, short=False):
    mode_value = str(mode or "").strip().lower()
    if mode_value == "improve":
        return "Improve" if short else "Improve with the same parameters"
    if att_idx == 0:
        return "Initial" if short else "Initial fresh composition"
    return "Fresh" if short else "Fresh start with new parameters"


def describe_attempt_origin(att_idx, attempts):
    if att_idx <= 0:
        return "This is the first generated quiz. The participant began by choosing initial parameters and composing a fresh quiz."

    current_mode = str(attempts[att_idx].get("mode") or "").strip().lower()
    if current_mode == "improve":
        return "This attempt exists because, on the previous quiz, the participant was not satisfied and chose Improve while keeping the same parameters."

    return "This attempt exists because, on the previous quiz, the participant was not satisfied and chose Fresh to enter a new set of parameters."


def describe_post_attempt_choice(att_idx, attempts):
    if att_idx >= len(attempts) - 1:
        return "Yes", "End the interaction and answer the survey", "After this quiz, the participant stopped the interaction and moved to the final survey."

    next_mode = str(attempts[att_idx + 1].get("mode") or "").strip().lower()
    if next_mode == "improve":
        return "No", "Improve with the same parameters", "The participant kept the same requested parameters and asked QuizComp to improve the generated quiz."

    return "No", "Start fresh with new parameters", "The participant restarted the composition flow with a new set of parameters for the next attempt."


def render_styles():
    st.markdown(
        """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

    html, body, [class*="st"] {
        font-family: 'IBM Plex Sans', sans-serif !important;
    }

    section[data-testid="stSidebar"] {
        background: #0f172a !important;
        min-width: 300px !important;
        max-width: 300px !important;
    }

    section[data-testid="stSidebar"] * {
        color: #dbe4ef !important;
    }

    .main .block-container {
        padding-top: 1.2rem !important;
        max-width: 1180px !important;
    }

    .hero-card {
        background: linear-gradient(135deg, #15324f 0%, #1d4d7a 100%);
        border-radius: 16px;
        padding: 1.4rem 1.5rem;
        color: white;
        margin-bottom: 1rem;
    }

    .hero-card h1,
    .hero-card h2,
    .hero-card p,
    .hero-card div {
        color: white !important;
    }

    .app-shell {
        background: linear-gradient(180deg, #f8fbff 0%, #eff6ff 100%);
        border: 1px solid #dbe7f5;
        border-radius: 18px;
        padding: 1.25rem 1.4rem;
        margin-bottom: 1rem;
    }

    .callout {
        border-radius: 12px;
        padding: 0.9rem 1rem;
        margin: 0.8rem 0;
        border: 1px solid #cbd5e1;
        background: #f8fafc;
        color: #0f172a;
    }

    .callout strong {
        display: block;
        margin-bottom: 0.2rem;
        font-size: 0.95rem;
    }

    .callout.subtle {
        background: #f8fafc;
        border-color: #dbe4ef;
    }

    .callout.accent {
        background: #eef6ff;
        border-color: #bfdbfe;
    }

    .callout.warn {
        background: #fff7ed;
        border-color: #fdba74;
    }

    .callout.good {
        background: #ecfdf5;
        border-color: #86efac;
    }

    .callout-section + .callout-section {
        margin-top: 0.8rem;
        padding-top: 0.8rem;
        border-top: 1px solid rgba(148, 163, 184, 0.28);
    }

    .callout-section-title {
        font-weight: 700;
        margin-bottom: 0.2rem;
    }

    .section-card {
        background: white;
        border: 1px solid #dbe4ef;
        border-radius: 14px;
        padding: 1rem 1rem 0.8rem;
        margin-bottom: 0.9rem;
    }

    .section-card h3,
    .section-card h4 {
        color: #15324f;
        margin-bottom: 0.6rem;
    }

    .intro-detail-card {
        background: white;
        border: 1px solid #dbe4ef;
        border-radius: 18px;
        padding: 1.2rem 1.3rem;
        margin-bottom: 1rem;
    }

    .intro-detail-card h2 {
        color: #15324f;
        font-size: 1.45rem;
        margin-bottom: 0.7rem;
    }

    .intro-detail-card h3 {
        color: #15324f;
        font-size: 1rem;
        margin: 1rem 0 0.4rem;
    }

    .intro-detail-card p,
    .intro-detail-card li {
        color: #334155;
        font-size: 0.95rem;
        line-height: 1.7;
    }

    .intro-detail-card ul {
        margin-top: 0.35rem;
        margin-bottom: 0.65rem;
        padding-left: 1.2rem;
    }

    .summary-strip {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: 1rem;
    }

    .summary-item {
        background: white;
        border: 1px solid #dbe4ef;
        border-radius: 12px;
        padding: 0.9rem 1rem;
    }

    .summary-item .label {
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #64748b;
        font-weight: 600;
        margin-bottom: 0.2rem;
    }

    .summary-item .value {
        font-size: 1.15rem;
        font-weight: 700;
        color: #0f172a;
    }

    .platform-header {
        background: linear-gradient(135deg, #15324f 0%, #1d4d7a 100%);
        border-radius: 16px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1rem;
        display: flex;
        justify-content: space-between;
        gap: 16px;
        align-items: flex-start;
    }

    .platform-header .title {
        font-size: 1.45rem;
        font-weight: 700;
        color: white;
    }

    .platform-header .meta {
        font-size: 0.92rem;
        color: rgba(255, 255, 255, 0.78);
        margin-top: 0.3rem;
    }

    .score-chip {
        font-family: 'IBM Plex Mono', monospace;
        color: white;
        border-radius: 10px;
        padding: 0.45rem 0.85rem;
        font-weight: 700;
        font-size: 1rem;
        display: inline-block;
    }

    .locked-note {
        font-size: 0.82rem;
        color: #475569;
        margin-top: 0.3rem;
    }

    .sidebar-panel {
        background: rgba(15, 23, 42, 0.42);
        border: 1px solid rgba(148, 163, 184, 0.22);
        border-radius: 12px;
        padding: 0.85rem 0.9rem;
        margin-bottom: 0.9rem;
    }

    .sidebar-panel .eyebrow {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #93a5bc !important;
        margin-bottom: 0.3rem;
        font-weight: 600;
    }

    .phase-step {
        border-bottom: 1px solid rgba(148, 163, 184, 0.16);
        padding: 0.45rem 0;
        font-size: 0.87rem;
    }

    .phase-step:last-child {
        border-bottom: none;
    }

    .phase-step.active {
        color: white !important;
        font-weight: 700;
    }

    .phase-banner {
        background: linear-gradient(135deg, #f8fbff 0%, #dbeafe 100%);
        border: 1px solid #bfdbfe;
        border-radius: 18px;
        padding: 1.2rem 1.3rem;
        margin-bottom: 1rem;
    }

    .phase-banner .eyebrow {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #1d4d7a;
        font-weight: 700;
        margin-bottom: 0.45rem;
    }

    .phase-banner-title {
        font-size: 1.55rem;
        font-weight: 700;
        color: #0f172a;
        line-height: 1.2;
        margin-bottom: 0.45rem;
    }

    .phase-banner-copy {
        font-size: 0.96rem;
        line-height: 1.65;
        color: #334155;
        max-width: 72ch;
    }

    .phase-banner-alert {
        font-size: 0.96rem;
        line-height: 1.65;
        color: #dc2626;
        font-weight: 700;
        margin-top: 0.55rem;
        max-width: 72ch;
    }

    .study-stepper {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin: 0.8rem 0 1rem;
    }

    .study-step {
        background: white;
        border: 1px solid #dbe4ef;
        border-radius: 14px;
        padding: 0.9rem 0.95rem;
        display: flex;
        gap: 12px;
        min-height: 126px;
    }

    .study-step.completed {
        border-color: #86efac;
        background: #f0fdf4;
    }

    .study-step.active {
        border-color: #93c5fd;
        background: #eff6ff;
        box-shadow: 0 8px 24px rgba(59, 130, 246, 0.12);
    }

    .study-step-number {
        width: 34px;
        height: 34px;
        border-radius: 999px;
        background: #e2e8f0;
        color: #15324f;
        font-weight: 700;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        font-family: 'IBM Plex Mono', monospace;
    }

    .study-step.completed .study-step-number {
        background: #bbf7d0;
        color: #166534;
    }

    .study-step.active .study-step-number {
        background: #15324f;
        color: white;
    }

    .study-step-label {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #64748b;
        font-weight: 700;
        margin-bottom: 0.25rem;
    }

    .study-step-title {
        font-size: 1rem;
        font-weight: 700;
        color: #0f172a;
        margin-bottom: 0.3rem;
    }

    .study-step-body {
        font-size: 0.86rem;
        line-height: 1.55;
        color: #475569;
    }

    .role-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
        margin: 0.8rem 0 1rem;
    }

    .role-card {
        background: white;
        border: 1px solid #dbe4ef;
        border-radius: 14px;
        padding: 1rem;
    }

    .role-card h3 {
        font-size: 1rem;
        color: #15324f;
        margin: 0 0 0.45rem;
    }

    .role-card p {
        margin: 0;
        color: #334155;
        line-height: 1.6;
        font-size: 0.92rem;
    }

    .phase-card-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin: 0.8rem 0 1rem;
    }

    .phase-card {
        background: white;
        border: 1px solid #dbe4ef;
        border-radius: 16px;
        padding: 1rem;
        min-height: 180px;
    }

    .phase-card-label {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #1d4d7a;
        font-weight: 700;
        margin-bottom: 0.35rem;
    }

    .phase-card-title {
        font-size: 1rem;
        font-weight: 700;
        color: #15324f;
        margin-bottom: 0.45rem;
    }

    .phase-card-copy {
        font-size: 0.91rem;
        line-height: 1.6;
        color: #334155;
    }

    .attempt-pager {
        background: white;
        border: 1px solid #cbd5e1;
        border-radius: 16px;
        padding: 1rem;
        margin: 0.85rem 0 1rem;
        box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
    }

    .attempt-pager-header {
        display: flex;
        justify-content: space-between;
        gap: 16px;
        align-items: flex-start;
        margin-bottom: 0.75rem;
    }

    .attempt-pager-label {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: #64748b;
        font-weight: 700;
    }

    .attempt-pager-title {
        font-size: 1.2rem;
        font-weight: 700;
        color: #0f172a;
        margin-top: 0.15rem;
    }

    .attempt-pager-copy {
        font-size: 0.91rem;
        line-height: 1.55;
        color: #475569;
        margin-top: 0.2rem;
        max-width: 64ch;
    }

    .attempt-status-chip {
        display: inline-block;
        padding: 0.35rem 0.7rem;
        border-radius: 999px;
        background: #dcfce7;
        color: #166534;
        font-size: 0.8rem;
        font-weight: 700;
    }

    .attempt-metrics {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin: 0.8rem 0;
    }

    .attempt-metric {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 0.8rem 0.9rem;
    }

    .attempt-metric-label {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: #64748b;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }

    .attempt-metric-value {
        font-size: 1rem;
        font-weight: 700;
        color: #0f172a;
    }

    .nav-helper {
        font-size: 0.8rem;
        color: #64748b;
        margin-top: 0.35rem;
        line-height: 1.4;
    }

    .question-card {
        background: white;
        border: 1px solid #dbe4ef;
        border-radius: 14px;
        padding: 1rem;
        margin-bottom: 0.85rem;
    }

    .question-meta {
        font-size: 0.83rem;
        color: #475569;
        margin-bottom: 0.55rem;
        font-weight: 600;
    }

    .question-stem {
        font-size: 1rem;
        color: #0f172a;
        margin-bottom: 0.7rem;
    }

    .option-row {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 0.55rem 0.7rem;
        margin-bottom: 0.45rem;
    }

    .option-label {
        font-family: 'IBM Plex Mono', monospace;
        font-weight: 700;
        color: #1d4d7a;
        margin-right: 0.4rem;
    }

    .rating-label {
        font-size: 0.85rem;
        color: #64748b;
        margin-top: 0.25rem;
        margin-bottom: 0.85rem;
    }

    button[kind="header"] {
        display: none !important;
    }

    h1 a, h2 a, h3 a, h4 a, h5 a, h6 a {
        display: none !important;
    }

    a[href^="#"] {
        display: none !important;
    }

    #MainMenu, footer {
        visibility: hidden;
    }

    header[data-testid="stHeader"] {
        display: none;
    }

    @media (max-width: 900px) {
        .study-stepper,
        .phase-card-grid,
        .attempt-metrics {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .role-grid {
            grid-template-columns: 1fr;
        }

        .summary-strip {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 700px) {
        .study-stepper,
        .phase-card-grid,
        .attempt-metrics,
        .summary-strip {
            grid-template-columns: 1fr;
        }

        .attempt-pager-header {
            flex-direction: column;
        }
    }
    </style>
    """,
        unsafe_allow_html=True,
    )


def render_callout(title, body, tone="subtle"):
    st.markdown(
        f"<div class='callout {tone}'><strong>{title}</strong><div>{body}</div></div>",
        unsafe_allow_html=True,
    )


def callout_sections_html(sections):
    blocks = []
    for section_title, section_body in sections:
        blocks.append(
            "<div class='callout-section'>"
            + f"<div class='callout-section-title'>{section_title}</div>"
            + f"<div>{section_body}</div>"
            + "</div>"
        )
    return "".join(blocks)


def render_survey_selection_styles():
    st.markdown(
        """
        <style>
        div[data-testid="stCheckbox"] {
            margin-bottom: 0.2rem;
        }

        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"] {
            margin: 0 !important;
            display: flex !important;
            justify-content: center !important;
        }

        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"] > div:first-child {
            display: none !important;
        }

        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"] > div:last-child {
            width: 100%;
            min-width: 3.25rem;
            min-height: 3.25rem;
            padding: 0.7rem 0.95rem !important;
            border: 1px solid rgba(148, 163, 184, 0.34) !important;
            border-radius: 0.6rem !important;
            background: rgba(15, 23, 42, 0.35) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            transition: all 0.15s ease;
        }

        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"] > div:last-child p {
            margin: 0 !important;
            font-size: 1.05rem !important;
            font-weight: 600 !important;
            color: #e5e7eb !important;
        }

        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"]:hover > div:last-child {
            border-color: rgba(239, 68, 68, 0.55) !important;
            background: rgba(127, 29, 29, 0.10) !important;
        }

        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"] input:checked ~ div:last-child,
        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"]:has(input:checked) > div:last-child {
            background: rgba(127, 29, 29, 0.18) !important;
            border: 2px solid #ef4444 !important;
            box-shadow: 0 0 0 1px rgba(239, 68, 68, 0.18), 0 0 18px rgba(239, 68, 68, 0.12) !important;
        }

        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"] input:checked ~ div:last-child p,
        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"]:has(input:checked) > div:last-child p {
            color: #fca5a5 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_study_stage_index(current_phase):
    for idx, stage in enumerate(STUDY_STAGES):
        if current_phase in stage["phases"]:
            return idx
    return 0


def render_study_progress(current_phase):
    current_idx = get_study_stage_index(current_phase)
    step_cards = []
    for idx, stage in enumerate(STUDY_STAGES):
        state_class = "completed" if idx < current_idx else "active" if idx == current_idx else ""
        step_cards.append(
            "<div class='study-step {}'>".format(state_class)
            + f"<div class='study-step-number'>{idx + 1}</div>"
            + "<div>"
            + f"<div class='study-step-label'>{stage['label']}</div>"
            + f"<div class='study-step-title'>{stage['title']}</div>"
            + f"<div class='study-step-body'>{stage['description']}</div>"
            + "</div></div>"
        )
    st.markdown("<div class='study-stepper'>" + "".join(step_cards) + "</div>", unsafe_allow_html=True)


def render_phase_banner(eyebrow, title, body, alert=None):
    alert_html = f"<div class='phase-banner-alert'>{alert}</div>" if alert else ""
    st.markdown(
        "<div class='phase-banner'>"
        f"<div class='eyebrow'>{eyebrow}</div>"
        f"<div class='phase-banner-title'>{title}</div>"
        f"<div class='phase-banner-copy'>{body}</div>"
        f"{alert_html}"
        "</div>",
        unsafe_allow_html=True,
    )


def render_intro_phase_cards():
    phase_cards = []
    for stage in INTRO_PHASE_CARDS:
        phase_cards.append(
            "<div class='phase-card'>"
            + f"<div class='phase-card-label'>{stage['label']}</div>"
            + f"<div class='phase-card-title'>{stage['title']}</div>"
            + f"<div class='phase-card-copy'>{stage['description']}</div>"
            + "</div>"
        )
    st.markdown("<div class='phase-card-grid'>" + "".join(phase_cards) + "</div>", unsafe_allow_html=True)


def render_attempt_navigation(attempts, att_idx, key_prefix):
    total_attempts = len(attempts)
    visited_attempts = list(st.session_state.get("visited_attempts", []))
    reviewed_attempts = len(visited_attempts)
    is_current_reviewed = att_idx in visited_attempts

    st.markdown(
        "<div class='attempt-pager'>"
        "<div class='attempt-pager-header'>"
        "<div>"
        "<div class='attempt-pager-label'>Attempt navigation</div>"
        f"<div class='attempt-pager-title'>Attempt {att_idx + 1} / {total_attempts}</div>"
        "<div class='attempt-pager-copy'>Use the buttons below to move page by page through the recorded interaction. The goal is to review every attempt before opening the questionnaire.</div>"
        "</div>"
        f"<div class='attempt-status-chip'>{'Reviewed' if is_current_reviewed else 'Pending'}</div>"
        "</div>"
        f"<div class='attempt-metrics'><div class='attempt-metric'><div class='attempt-metric-label'>Current page</div><div class='attempt-metric-value'>{att_idx + 1} / {total_attempts}</div></div>"
        f"<div class='attempt-metric'><div class='attempt-metric-label'>Reviewed so far</div><div class='attempt-metric-value'>{reviewed_attempts} / {total_attempts}</div></div>"
        f"<div class='attempt-metric'><div class='attempt-metric-label'>Session progress</div><div class='attempt-metric-value'>{pct(reviewed_attempts / float(total_attempts or 1))}</div></div></div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.progress(float(att_idx + 1) / float(total_attempts or 1))

    prev_disabled = att_idx <= 0
    next_disabled = att_idx >= total_attempts - 1
    prev_col, next_col = st.columns(2)
    with prev_col:
        if button_compat(
            st,
            "Previous attempt",
            key=f"{key_prefix}_previous_attempt",
            disabled=prev_disabled,
            use_container_width=True,
        ):
            st.session_state["attempt_idx"] = att_idx - 1
            rerun_app()
        prev_help = "Already at the first attempt in this session." if prev_disabled else "Go back one page to revisit the previous attempt."
        st.markdown(f"<div class='nav-helper'>{prev_help}</div>", unsafe_allow_html=True)
    with next_col:
        if button_compat(
            st,
            "Next attempt",
            key=f"{key_prefix}_next_attempt",
            type="primary",
            disabled=next_disabled,
            use_container_width=True,
        ):
            st.session_state["attempt_idx"] = att_idx + 1
            rerun_app()
        next_help = (
            "This is the last attempt. Use the questionnaire button below once every attempt has been reviewed."
            if next_disabled
            else "Move forward one page to the next attempt."
        )
        st.markdown(f"<div class='nav-helper'>{next_help}</div>", unsafe_allow_html=True)


def initialize_practice_state():
    st.session_state.setdefault("practice_num_mcqs", 10)

    for idx, _topic in enumerate(TOPIC_NAMES):
        st.session_state.setdefault(f"practice_topic_selected_{idx}", False)
        st.session_state.setdefault(f"practice_topic_weight_{idx}", 0)

    for idx, _label in enumerate(DIFFICULTY_LABELS):
        st.session_state.setdefault(f"practice_diff_selected_{idx}", False)
        st.session_state.setdefault(f"practice_diff_weight_{idx}", 0)


def reset_practice_state():
    st.session_state["practice_num_mcqs"] = 10

    for idx, _topic in enumerate(TOPIC_NAMES):
        st.session_state[f"practice_topic_selected_{idx}"] = False
        st.session_state[f"practice_topic_weight_{idx}"] = 0
        st.session_state.pop(f"practice_topic_weight_{idx}_slider", None)

    for idx, _label in enumerate(DIFFICULTY_LABELS):
        st.session_state[f"practice_diff_selected_{idx}"] = False
        st.session_state[f"practice_diff_weight_{idx}"] = 0
        st.session_state.pop(f"practice_diff_weight_{idx}_slider", None)


def reset_survey_state():
    st.session_state["simulation_started_at"] = time.time()
    for survey_key in ["q1", "q2", "q3", "q4", "q5", "q6", "q7"]:
        st.session_state[survey_key] = None
        for option in [0, 1, 2, 3, 4, 5]:
            st.session_state.pop(f"{survey_key}_{option}_choice", None)
    st.session_state["survey_comments"] = ""
    st.session_state["survey_error"] = ""
    st.session_state["save_error"] = ""
    st.session_state["survey_saved_path"] = ""


def reset_replay_state(clear_survey=False):
    st.session_state["attempt_idx"] = 0
    st.session_state["visited_attempts"] = []
    if clear_survey:
        reset_practice_state()
        reset_survey_state()


def initialize_state(personas):
    participant_context = get_participant_context()
    participant_assignment = get_participant_assignment(participant_context)
    requested_replay_session_id = get_initial_replay_session_id(personas, participant_assignment)
    requested_persona_idx = get_persona_index_by_replay_session_id(personas, requested_replay_session_id)
    st.session_state["participant_context"] = participant_context
    st.session_state["participant_assignment"] = participant_assignment
    st.session_state.setdefault("assignments_exhausted", False)

    if st.session_state.pop("_pending_survey_reset", False):
        reset_survey_state()
    st.session_state.setdefault("phase", "landing")
    current_phase = st.session_state.get("phase", "landing")
    previous_phase = st.session_state.get("_last_phase")
    st.session_state.setdefault("assigned_replay_session_id", requested_replay_session_id)
    st.session_state.setdefault("assigned_persona_idx", requested_persona_idx)
    st.session_state.setdefault("attempt_idx", 0)
    st.session_state.setdefault("visited_attempts", [])
    st.session_state.setdefault("survey_error", "")
    st.session_state.setdefault("save_error", "")
    st.session_state.setdefault("survey_saved_path", "")
    st.session_state.setdefault("survey_comments", "")
    st.session_state.setdefault("consent_error", "")
    st.session_state.setdefault("simulation_started_at", time.time())

    for survey_key in ["q1", "q2", "q3", "q4", "q5", "q6", "q7"]:
        st.session_state.setdefault(survey_key, None)

    initialize_practice_state()

    if current_phase == "landing" and previous_phase != "landing":
        reset_replay_state(clear_survey=True)

    if current_phase == "walkthrough" and previous_phase != "walkthrough":
        reset_practice_state()

    if st.session_state.get("phase") in {"landing", "consent"}:
        if st.session_state.get("assigned_replay_session_id") != requested_replay_session_id:
            st.session_state["assigned_replay_session_id"] = requested_replay_session_id
            reset_replay_state(clear_survey=True)

    current_replay_session_id = safe_int(st.session_state.get("assigned_replay_session_id"), -1)
    st.session_state["assigned_persona_idx"] = get_persona_index_by_replay_session_id(personas, current_replay_session_id)
    st.session_state["_last_phase"] = current_phase


def mark_attempt_visited(att_idx):
    visited = list(st.session_state.get("visited_attempts", []))
    if att_idx not in visited:
        visited.append(att_idx)
        visited.sort()
        st.session_state["visited_attempts"] = visited


def render_sidebar(personas):
    phase = st.session_state["phase"]
    selected_idx = int(st.session_state.get("assigned_persona_idx", -1))
    selected_idx = selected_idx if 0 <= selected_idx < len(personas) else -1
    st.session_state["assigned_persona_idx"] = selected_idx
    persona = personas[selected_idx] if 0 <= selected_idx < len(personas) else {}
    current_stage = STUDY_STAGES[get_study_stage_index(phase)]

    st.sidebar.markdown(
        """
        <div style='padding-top:0.2rem;padding-bottom:0.5rem'>
            <div style='font-size:11px;text-transform:uppercase;letter-spacing:0.14em;color:#93a5bc;font-weight:600;'>
                QuizComp Simulation
            </div>
            <div style='font-size:20px;font-weight:700;color:#f8fafc;'>
                Guided experience viewer
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown(
        """
        <div class='sidebar-panel'>
            <div class='eyebrow'>Goal of the simulation</div>
            <div style='font-size:13px;line-height:1.5;'>
                You first practice the platform controls, then replay one full recorded session,
                review the returned quizzes, and finally answer the study survey from that perspective.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    phase_rows = []
    for phase_name in PHASES:
        active_class = "active" if phase_name == phase else ""
        phase_rows.append(f"<div class='phase-step {active_class}'>{PHASE_LABELS[phase_name]}</div>")
    st.sidebar.markdown(
        "<div class='sidebar-panel'><div class='eyebrow'>Study flow</div>"
        + "".join(phase_rows)
        + "</div>",
        unsafe_allow_html=True,
    )

    st.sidebar.markdown(
        (
            "<div class='sidebar-panel'>"
            "<div class='eyebrow'>Current stage</div>"
            f"<div style='font-size:15px;font-weight:700;'>{current_stage['label']}: {current_stage['title']}</div>"
            f"<div style='font-size:12px;line-height:1.5;color:#b7c5d8 !important;margin-top:0.3rem;'>{current_stage['description']}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    if personas:
        assignment_label = format_assigned_learner_label(selected_idx)
        assignment_meta = (
            f"{persona.get('num_compose_attempts', 0)} attempts"
            if persona
            else "A replay session will be assigned after consent."
        )
        st.sidebar.markdown(
            (
                "<div class='sidebar-panel'>"
                "<div class='eyebrow'>Assigned learner</div>"
                f"<div style='font-size:16px;font-weight:700;'>{assignment_label}</div>"
                f"<div style='font-size:12px;color:#b7c5d8 !important;margin-top:0.3rem;'>"
                f"{assignment_meta}"
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )

        st.sidebar.markdown(
            (
                "<div class='sidebar-panel'>"
                "<div class='eyebrow'>Assignment mode</div>"
                "<div style='font-size:12px;line-height:1.5;color:#b7c5d8 !important;'>"
                "This simulation assigns one completed real-user session per Prolific participant after consent. "
                "Manual preview is still possible with <code>persona_id</code> or <code>replay_session_id</code> in the URL."
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )

        if phase == "replay" and persona:
            render_attempt_list_sidebar(persona, st.session_state["attempt_idx"])

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        "<div style='font-size:11px;color:#9fb2c8;'>"
        "The walkthrough is interactive, but the replay is read-only and never generates a new quiz live."
        "</div>",
        unsafe_allow_html=True,
    )

    return selected_idx

def render_attempt_list_sidebar(persona, selected_attempt_idx):
    st.sidebar.markdown("**Recorded attempts**")
    attempts = persona.get("compose_attempts", [])
    visited = set(st.session_state.get("visited_attempts", []))
    total_attempts = len(attempts)

    for idx, attempt in enumerate(attempts):
        score = attempt.get("target_match", 0)
        tags = []
        if idx in visited:
            tags.append("viewed")
        suffix = f" ({', '.join(tags)})" if tags else ""
        if button_compat(
            st.sidebar,
            f"Attempt {idx + 1}/{total_attempts} - {format_attempt_mode(attempt.get('mode'), idx, short=True)} - {pct(score)}{suffix}",
            key=f"attempt_sidebar_{idx}",
            type="primary" if idx == selected_attempt_idx else "secondary",
            use_container_width=True,
        ):
            st.session_state["attempt_idx"] = idx
            rerun_app()


def render_study_briefing(persona_count, persona):
    st.markdown(
        inspect.cleandoc(
            """
        <div class='intro-detail-card'>
        <h2>Platform and study overview</h2>
        <p>
        This study focuses on understanding and evaluating the experience of interacting with QuizComp, a platform designed to compose quizzes automatically from a pool of Mathematics multiple-choice questions (MCQs).
        </p>

        <p>
        The goal of this study is to evaluate how the effort required to use the platform is perceived by users who only observe the interaction instead of performing the task themselves. Participants are expected to imagine themselves in the user’s position and judge how easy, difficult, clear, or mentally demanding the interaction appears to be.
        </p>

        <p>
        During the study, you will observe one recorded user session interacting with the platform. Your role is to carefully follow the interaction process and evaluate the experience from the user’s perspective, as if you were the one using the system.
        </p>
        <h3>How QuizComp works</h3>
        <p>The platform takes as input the user's quiz requirements, including:</p>
        <ul>
        <li>The desired number of questions in the quiz,</li>
        <li>The topics or subjects to include,</li>
        <li>The difficulty levels of the questions,</li>
        </ul>
        <p>Based on these requirements, the system searches through a large collection of available questions and attempts to generate the quiz that best matches the user's needs.</p>
        <p>The generated quiz is then presented to the user for inspection and evaluation.</p>
        <h3>User decision process</h3>
        <p>After reviewing the proposed quiz, the user is asked whether they are satisfied with the generated result.</p>
        <p><strong>If the user is satisfied:</strong></p>
        <ul>
        <li>The interaction ends and the user is invited to complete an evaluation survey about their experience with the platform.</li>
        </ul>
        <p>The survey evaluates several aspects of the interaction, including:</p>
        <ul>
        <li>Mental demand, for example how demanding it was to inspect and compare quizzes,</li>
        <li>Temporal demand,</li>
        <li>Ease of interaction,</li>
        <li>Satisfaction,</li>
        <li>Perceived usefulness,</li>
        <li>Overall experience.</li>
        </ul>
        <p><strong>If the user is not satisfied:</strong></p>
        <p>The user can continue the interaction using one of two modes.</p>
        <p><strong>Improve Mode</strong></p>
        <p>The user keeps the same requirements and asks the system to generate a better quiz based on the existing preferences.</p>
        <p><strong>Fresh Mode</strong></p>
        <p>The user modifies or refines their requirements before requesting another quiz. For example:</p>
        <ul>
        <li>Adding or removing topics,</li>
        <li>Changing the number of questions,</li>
        <li>Adjusting difficulty levels,</li>
        </ul>
        <p>The interaction continues iteratively until the user obtains a quiz that sufficiently matches their expectations.</p>
        <h3>What you will observe</h3>
        <p>Throughout the study, you will be able to:</p>
        <ul>
        <li>Visualize the quizzes returned by the platform at each attempt,</li>
        <li>Inspect how the proposed quizzes evolve over time,</li>
        <li>Observe the decisions and actions taken during the recorded learner session,</li>
        <li>Follow the complete interaction flow from beginning to end.</li>
        </ul>
        <p>You should observe the experience as if you personally interacted with the system.</p>
        <h3>Exploration phase</h3>
        <p>Before observing the actual interaction sequence, the study also provides a short exploration or tutorial phase.</p>
        <p>During this phase, you will have the possibility to briefly interact with the platform yourself by:</p>
        <ul>
        <li>Entering quiz requirements,</li>
        <li>Selecting topics,</li>
        <li>Choosing the number of MCQs,</li>
        <li>Adjusting difficulty levels,</li>
        <li>And exploring how the platform behaves.</li>
        </ul>
        <p>This phase is intended only to help you understand how the system works before starting the observation task.</p>
        <h3>Goal of the study</h3>
        <p>The main objective of this study is to collect reliable feedback about the user experience of the platform for research purposes.</p>
        <p>Your observations and final survey responses will help evaluate:</p>
        <ul>
        <li>The usability of the system,</li>
        <li>The cognitive effort required,</li>
        <li>And the overall effectiveness of the quiz composition experience.</li>
        </ul>
        </div>
        """
        ),
        unsafe_allow_html=True,
    )
    render_study_progress("landing")

    if button_compat(st, "Continue to consent and setup", type="primary", use_container_width=True):
        st.session_state["phase"] = "consent"
        rerun_app()


def render_consent_page(personas):
    render_study_progress("consent")
    render_phase_banner(
        "Start step",
        "Review consent before Phase 1 begins",
        "Once consent is confirmed, you will be taken to a short overview of the QuizComp platform and its interface. This will help you understand how the platform interaction works.",
    )
    st.title("Consent and data use")
    st.markdown(
        "This study is conducted for research purposes to examine how people understand and evaluate a quiz composition system through guided simulation."
    )

    st.markdown(
        """
        **During this session, the app may record:**
        - the simulation session assigned to you,
        - the walkthrough parameters you inspect,
        - the recorded attempts you review,
        - your answers to the final questionnaire.
        """
    )

    st.markdown(
        """
        **Privacy**
        - No new quiz is generated from your own input in this simulation.
        - The replayed data comes from a pre-recorded session stored in the project files.
        """
    )

    st.markdown(
        """
        **Voluntary participation**
        - You may stop the simulation at any time.
        - You should only continue if you agree to review one recorded session and answer the questionnaire from that perspective.
        """
    )

    agreed = st.checkbox(
        "I consent to participate in this simulation study and to the recording of my navigation and survey responses for research purposes.",
        value=False,
        key="consent_checkbox",
    )

    nav_left, nav_right = st.columns([1, 1])
    with nav_left:
        if button_compat(st, "Back to study overview", use_container_width=True):
            st.session_state["phase"] = "landing"
            rerun_app()
    with nav_right:
        if button_compat(st, "Continue to Phase 1: Guided exploration", type="primary", use_container_width=True):
            if agreed:
                participant_context = st.session_state.get("participant_context") or get_participant_context()
                replay_session_id, assignments_exhausted, assignment_error = assign_replay_for_participant(personas, participant_context)
                if replay_session_id >= 0:
                    st.session_state["consent_error"] = ""
                    st.session_state["assignments_exhausted"] = assignments_exhausted
                    st.session_state["assigned_replay_session_id"] = replay_session_id
                    st.session_state["assigned_persona_idx"] = get_persona_index_by_replay_session_id(personas, replay_session_id)
                    st.session_state["participant_assignment"] = get_participant_assignment(participant_context)
                    st.session_state["phase"] = "walkthrough"
                    rerun_app()

                st.session_state["assignments_exhausted"] = assignments_exhausted
                st.session_state["consent_error"] = assignment_error or "No teacher session is available for assignment right now."
                rerun_app()
            st.session_state["consent_error"] = "Please confirm consent before continuing."
            rerun_app()

    if st.session_state.get("consent_error"):
        render_callout("Consent required", st.session_state["consent_error"], tone="warn")
        st.session_state["consent_error"] = ""


def collect_practice_distribution(labels, prefix):
    weights = []
    selected_indices = []
    for idx, label in enumerate(labels):
        selected_key = f"{prefix}_selected_{idx}"
        weight_key = f"{prefix}_weight_{idx}"
        slider_key = f"{weight_key}_slider"

        selected = st.checkbox(label, key=selected_key)
        if selected:
            selected_indices.append(idx)
            if slider_key not in st.session_state:
                st.session_state[slider_key] = int(st.session_state.get(weight_key, 0) or 0)

            weight = st.slider(
                f"{label} target (%)",
                min_value=0,
                max_value=100,
                step=5,
                key=slider_key,
            )
            st.session_state[weight_key] = int(weight)
        else:
            st.session_state[weight_key] = 0
            st.session_state.pop(slider_key, None)
            weight = 0

        weights.append(weight)

    total = sum(weights)
    return selected_indices, weights, total


def render_distribution_summary(labels, weights):
    found_any = False
    for label, weight in zip(labels, weights):
        if weight > 0:
            found_any = True
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:0.3rem;'><span>{label}</span><span>{weight}%</span></div>",
                unsafe_allow_html=True,
            )
            st.progress(float(weight) / 100.0)
    if not found_any:
        st.markdown("No values selected yet.")


def render_walkthrough_page(persona):
    render_study_progress("walkthrough")
    render_phase_banner(
        "Phase 1 of 3",
        "Guided platform exploration",
        "Use this to understand how QuizComp requests are configured and how the interaction is structured. You are not judging the final experience yet: this phase is only here to help you understand what you will observe next. Notice how quiz size, topic targets, difficulty targets, and the final satisfaction decision shape the interaction. After this overview, you will move to the recorded attempts and observe the interaction flow in full.",
    )

    if MODERN_STREAMLIT:
        controls_col, explain_col = st.columns([1.15, 0.85], gap="large")
    else:
        controls_col, explain_col = st.container(), st.container()

    with controls_col:
        st.subheader("Platform walkthrough")
        st.markdown(
            "Choose parameters as if you were preparing a quiz for your own course. The settings below are interactive, but the compose action is intentionally disabled."
        )

        practice_num_mcqs = st.slider(
            "How many questions should the quiz contain?",
            min_value=3,
            max_value=30,
            step=1,
            key="practice_num_mcqs",
        )

        st.markdown("### Topic targets")
        topic_indices, topic_weights, topic_total = collect_practice_distribution(TOPIC_NAMES, "practice_topic")

        st.markdown("### Difficulty targets")
        diff_indices, diff_weights, diff_total = collect_practice_distribution(DIFFICULTY_LABELS, "practice_diff")

        if not topic_indices:
            render_callout("Topics not selected yet", "Choose at least one topic to mirror the real parameter-configuration step.", tone="warn")
        elif topic_total != 100:
            render_callout(
                "Topic total check",
                f"The topic sliders currently sum to {topic_total}%. In the real platform, the total should reach 100% before composition.",
                tone="warn",
            )

        if not diff_indices:
            render_callout("Difficulty levels not selected yet", "Choose at least one difficulty level before moving on to composition in the real platform.", tone="warn")
        elif diff_total != 100:
            render_callout(
                "Difficulty total check",
                f"The difficulty sliders currently sum to {diff_total}%. In the real platform, the total should reach 100% before composition.",
                tone="warn",
            )

        button_compat(
            st,
            "Compose my quiz",
            key="compose_disabled",
            disabled=True,
            type="primary",
            use_container_width=True,
        )
        st.markdown(
            "<div class='locked-note'>Composition is disabled here. The next phase replays the outcome of a recorded session instead.</div>",
            unsafe_allow_html=True,
        )

    with explain_col:
        st.subheader("How to read this platform")
        st.markdown(
            """
            1. Set the number of questions.
            2. Choose topic targets and difficulty targets.
            3. In the real platform, the user would compose a quiz.
            4. After each generated quiz, the platform asks: Are you satisfied with the generated quiz?
            5. If the answer is No, the user either chooses Improve with the same parameters or starts Fresh with new parameters.
            """
        )
        render_callout(
            "Why this matters",
            "The replay that follows only makes sense once you know how the parameters drive the generated quiz. Treat the walkthrough as your practice round.",
            tone="accent",
        )
        render_callout(
            "Decision flow after each attempt",
            "The replay will show, for every generated quiz, whether the participant was satisfied, whether they kept the same parameters and chose Improve, or whether they restarted Fresh with new parameters.",
            tone="subtle",
        )

        st.markdown("### Current walkthrough summary")
        st.markdown(f"**Requested quiz size:** {practice_num_mcqs} questions")
        st.markdown("**Topic distribution**")
        render_distribution_summary(TOPIC_NAMES, topic_weights)
        st.markdown("**Difficulty distribution**")
        render_distribution_summary(DIFFICULTY_LABELS, diff_weights)

        st.markdown("### Assigned learner")
        st.markdown(
            f"You will next review {format_assigned_learner_label(st.session_state['assigned_persona_idx'])}, which contains {persona.get('num_compose_attempts', 0)} recorded attempts."
        )

    nav_left, nav_right = st.columns([1, 1])
    with nav_left:
        if button_compat(st, "Back to consent and instructions", use_container_width=True):
            st.session_state["phase"] = "consent"
            rerun_app()
    with nav_right:
        if button_compat(st, "Start Phase 2: observe attempts", type="primary", use_container_width=True):
            st.session_state["phase"] = "replay"
            st.session_state["attempt_idx"] = 0
            st.session_state["visited_attempts"] = []
            rerun_app()


def render_overview_strip(persona):
    reviewed = len(st.session_state.get("visited_attempts", []))
    st.markdown(
        (
            "<div class='summary-strip'>"
            "<div class='summary-item'><div class='label'>Assigned learner</div>"
            f"<div class='value'>{format_assigned_learner_label(st.session_state['assigned_persona_idx'])}</div></div>"
            "<div class='summary-item'><div class='label'>Attempts</div>"
            f"<div class='value'>{persona.get('num_compose_attempts', 0)}</div></div>"
            "<div class='summary-item'><div class='label'>Observation mode</div>"
            "<div class='value'>Replay</div></div>"
            "<div class='summary-item'><div class='label'>Reviewed</div>"
            f"<div class='value'>{reviewed}/{persona.get('num_compose_attempts', 0)}</div></div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_platform_header(persona, attempt, att_idx):
    mode = attempt.get("mode", "fresh")
    score = attempt.get("target_match", 0)
    quiz_id = format_quiz_id(attempt.get("mcq_ids_json", []))
    st.markdown(
        (
            "<div class='platform-header'>"
            "<div><div class='title'>QuizComp platform replay</div>"
            f"<div class='meta'>{format_assigned_learner_label(st.session_state['assigned_persona_idx'])} | "
            f"Attempt {att_idx + 1} of {persona.get('num_compose_attempts', 0)} | "
            f"Mode: {format_attempt_mode(mode, att_idx)} | Quiz ID: {quiz_id}</div></div>"
            "<div style='text-align:right;'>"
            "<div style='font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:rgba(255,255,255,0.7);margin-bottom:0.25rem;'>Target match</div>"
            f"<div class='score-chip' style='background:{score_color(score)}'>{pct(score)}</div>"
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )


def render_params_panel(attempt):
    topic_weights = attempt.get("teacher_topic_json", [0] * len(TOPIC_NAMES))
    level_weights = attempt.get("teacher_level_json", [0] * len(DIFFICULTY_LABELS))
    num_mcqs = attempt.get("num_mcqs", 0)

    st.subheader("Requested parameters")
    st.markdown("These are the parameters that the user submitted when this recorded attempt was locked in.")

    st.markdown("### Topic targets")
    topic_found = False
    for name, weight in zip(TOPIC_NAMES, topic_weights):
        if weight > 0:
            topic_found = True
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:0.25rem;'><span>{name}</span><span>{pct(weight)}</span></div>",
                unsafe_allow_html=True,
            )
            st.progress(float(weight))
    if not topic_found:
        st.markdown("No explicit topic weighting was recorded for this attempt.")

    st.markdown("### Difficulty targets")
    for label, weight in zip(DIFFICULTY_LABELS, level_weights):
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:0.25rem;'><span>{label}</span><span>{pct(weight)}</span></div>",
            unsafe_allow_html=True,
        )
        st.progress(float(weight))

    st.markdown("### Requested quiz size")
    st.markdown(f"**{int(num_mcqs or 0)} questions**")
    st.markdown("<div class='locked-note'>In the real platform, these settings would feed the compose action.</div>", unsafe_allow_html=True)


def render_quiz_items(quiz_items, mcq_ids):
    if not quiz_items:
        render_callout(
            "Quiz content unavailable",
            "Only the selected question ids were recorded for this attempt.",
            tone="warn",
        )
        if mcq_ids:
            badges = "".join(
                f"<span style='display:inline-block;background:#eff6ff;border:1px solid #bfdbfe;border-radius:999px;padding:0.2rem 0.6rem;margin:0.15rem;font-size:12px;color:#1d4d7a;'>Q#{qid}</span>"
                for qid in mcq_ids
            )
            st.markdown(badges, unsafe_allow_html=True)
        return

    for idx, item in enumerate(quiz_items, start=1):
        options = item.get("options") or [
            item.get("option_a"),
            item.get("option_b"),
            item.get("option_c"),
            item.get("option_d"),
        ]
        topic_name = str(item.get("topic_name") or "Unknown topic").strip()
        difficulty = item.get("difficulty", item.get("difficulty_level", "?"))
        question_text = normalize_math_text(item.get("question") or "Question text unavailable.")
        correct_option = normalize_math_text(item.get("correct_option") or item.get("correct_answer") or "")

        st.markdown(f"**Question {idx} | {topic_name} | Level {difficulty}**")
        render_mixed_content(question_text)
        for option_idx, option in enumerate(options):
            if option is None:
                continue
            label = chr(ord("A") + option_idx)
            option_text = normalize_math_text(option)
            if option_text.startswith("$$") or "\n" in option_text:
                st.markdown(f"**{label}.**")
                render_mixed_content(option_text)
            else:
                st.markdown(f"**{label}.** {option_text}")
        if correct_option:
            st.markdown("Recorded correct answer:")
            render_mixed_content(correct_option)
        if idx < len(quiz_items):
            st.markdown("---")


def render_attempt_decision_flow(att_idx, attempts):
    satisfied_answer, next_action, next_action_text = describe_post_attempt_choice(att_idx, attempts)

    render_callout(
        "Decision flow around this attempt",
        callout_sections_html(
            [
                ("How this attempt started", describe_attempt_origin(att_idx, attempts)),
                ("Question shown after the generated quiz", "Are you satisfied with the generated quiz?"),
                (
                    "Recorded choice",
                    f"Satisfaction answer: {satisfied_answer}. Next action: {next_action}. {next_action_text}",
                ),
            ]
        ),
        tone="subtle",
    )


def render_result_panel(attempt):
    topic_coverage = attempt.get("topic_coverage_json", [0] * len(TOPIC_NAMES))
    diff_coverage = attempt.get("difficulty_coverage_json", [0] * len(DIFFICULTY_LABELS))
    quiz_items = get_attempt_quiz_items(attempt)
    mcq_ids = get_attempt_mcq_ids(attempt)
    quiz_id = format_quiz_id(mcq_ids)
    score = attempt.get("target_match", 0)
    color = score_color(score)

    st.subheader("Returned quiz")
    st.markdown("This is the system output for the recorded attempt you are replaying.")
    render_callout(
        "Quiz identifier",
        f"Quiz ID: {quiz_id}. If the same ID is found in another attempt, this means the system returned the same quiz.",
        tone="subtle",
    )
    st.markdown(
        f"<div style='border:2px solid {color};border-radius:12px;padding:1rem;margin:0.6rem 0 0.9rem;'>"
        f"<div style='font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#64748b;'>Target match score</div>"
        f"<div style='font-size:2.1rem;font-weight:800;color:{color};font-family:IBM Plex Mono, monospace;'>{pct(score)}</div>"
        f"<div style='margin-top:0.45rem;'>{mini_bar_html(score, color)}</div>"
        f"<div class='locked-note'>How closely the returned quiz matched the requested parameters.</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    coverage_tab, quiz_tab = st.tabs(["Coverage analysis", "Quiz questions"])

    with coverage_tab:
        left_col, right_col = st.columns(2)
        with left_col:
            st.markdown("**Topic coverage achieved**")
            for name, target, actual in zip(TOPIC_NAMES, attempt.get("teacher_topic_json", [0] * len(TOPIC_NAMES)), topic_coverage):
                if target > 0 or actual > 0:
                    delta = float(actual) - float(target)
                    status = coverage_status(delta)
                    delta_color = "#22c55e" if delta >= -0.05 else "#ef4444"
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;font-size:12px;margin-bottom:0.25rem;'><span>{name}</span><span style='color:{delta_color};'>{status}: {pct(actual)}</span></div>",
                        unsafe_allow_html=True,
                    )
                    st.progress(float(actual))

        with right_col:
            st.markdown("**Difficulty coverage achieved**")
            for label, target, actual in zip(DIFFICULTY_LABELS, attempt.get("teacher_level_json", [0] * len(DIFFICULTY_LABELS)), diff_coverage):
                delta = float(actual) - float(target)
                status = coverage_status(delta)
                delta_color = "#22c55e" if delta >= -0.05 else "#ef4444"
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;font-size:12px;margin-bottom:0.25rem;'><span>{label}</span><span style='color:{delta_color};'>{status}: {pct(actual)}</span></div>",
                    unsafe_allow_html=True,
                )
                st.progress(float(actual))

    with quiz_tab:
        render_quiz_items(quiz_items, mcq_ids)


def render_replay_page(persona):
    attempts = persona.get("compose_attempts", [])
    if not attempts:
        render_callout("No replay data found", "The assigned session does not contain any recorded attempts.", tone="warn")
        if button_compat(st, "Back to walkthrough", use_container_width=True):
            st.session_state["phase"] = "walkthrough"
            rerun_app()
        return

    att_idx = min(int(st.session_state.get("attempt_idx", 0)), len(attempts) - 1)
    st.session_state["attempt_idx"] = att_idx
    attempt = attempts[att_idx]
    mark_attempt_visited(att_idx)

    render_study_progress("replay")
    render_phase_banner(
        "Phase 2 of 3",
        "Observe the recorded interaction attempt by attempt",
        "You are now in the observation phase. Move through the attempts page by page, inspect each generated quiz, and watch how the interaction evolves before opening the questionnaire.",
    )
    render_callout(
        "Survey note",
        "The original session owner's survey answers are intentionally hidden. You will answer the questionnaire yourself after the replay.",
        tone="subtle",
    )

    render_overview_strip(persona)
    render_platform_header(persona, attempt, att_idx)

    if MODERN_STREAMLIT:
        params_col, result_col = st.columns([1, 1], gap="large")
        with params_col:
            render_params_panel(attempt)
        with result_col:
            render_result_panel(attempt)
    else:
        render_params_panel(attempt)
        st.markdown("<br>", unsafe_allow_html=True)
        render_result_panel(attempt)

    render_attempt_decision_flow(att_idx, attempts)

    all_reviewed = len(st.session_state.get("visited_attempts", [])) == len(attempts)
    if all_reviewed:
        replay_status_sections = [
            (
                "Replay complete",
                "You have reviewed every recorded attempt in this session. You can now move to the post-study survey.",
            ),
            (
                "Attempt reviewed",
                "This attempt is now marked as reviewed. Use the pager below to continue to the next attempt or revisit earlier ones.",
            ),
        ]
        replay_status_tone = "good"
    else:
        replay_status_sections = [
            (
                "Keep navigating",
                "Review every recorded attempt before the survey opens. Use the sidebar list or the previous and next buttons to move through the session.",
            ),
            (
                "Attempt reviewed",
                "This attempt is now marked as reviewed. Use the pager below to continue to the next attempt or revisit earlier ones.",
            ),
        ]
        replay_status_tone = "warn"

    render_callout(
        "Replay status",
        callout_sections_html(replay_status_sections),
        tone=replay_status_tone,
    )
    render_attempt_navigation(attempts, att_idx, key_prefix="attempt_nav_bottom")

    action_left, action_right = st.columns([1, 1])
    with action_left:
        if button_compat(st, "Back to Phase 1 tutorial", use_container_width=True):
            st.session_state["phase"] = "walkthrough"
            rerun_app()
    with action_right:
        if button_compat(
            st,
            "Start Phase 3 questionnaire",
            type="primary",
            disabled=not all_reviewed,
            use_container_width=True,
        ):
            st.session_state["phase"] = "survey"
            rerun_app()
        survey_help = (
            "All attempts have been reviewed. You can now continue to the questionnaire."
            if all_reviewed
            else "The questionnaire unlocks after every attempt in this session has been reviewed."
        )
        st.markdown(f"<div class='nav-helper'>{survey_help}</div>", unsafe_allow_html=True)


def render_rating_buttons(question, state_key):
    st.markdown(f"**{question}**")
    options = [1, 2, 3, 4, 5]
    current = st.session_state.get(state_key)
    for option in options:
        st.session_state[f"{state_key}_{option}_choice"] = option == current

    cols = st.columns(len(options))
    for option, col in zip(options, cols):
        with col:
            st.checkbox(
                str(option),
                key=f"{state_key}_{option}_choice",
                on_change=update_single_choice_checkbox,
                args=(state_key, option, options),
            )

    current = st.session_state.get(state_key)
    label = f"Selected rating: {current} / 5" if current is not None else "Choose a rating from 1 to 5."
    st.markdown(f"<div class='rating-label'>{label}</div>", unsafe_allow_html=True)
    return current


def render_yes_no_buttons(question, state_key):
    st.markdown(f"**{question}**")
    options = [1, 0]
    labels = {1: "Yes", 0: "No"}
    current = st.session_state.get(state_key)
    for option in options:
        st.session_state[f"{state_key}_{option}_choice"] = option == current

    cols = st.columns(len(options))
    for option, col in zip(options, cols):
        with col:
            st.checkbox(
                labels[option],
                key=f"{state_key}_{option}_choice",
                on_change=update_single_choice_checkbox,
                args=(state_key, option, options),
            )

    current = st.session_state.get(state_key)
    if current == 1:
        label = "Selected answer: Yes"
    elif current == 0:
        label = "Selected answer: No"
    else:
        label = "Choose Yes or No."
    st.markdown(f"<div class='rating-label'>{label}</div>", unsafe_allow_html=True)
    return current


def persist_survey_response(persona, attempts, metadata):
    responses = {
        "q1": st.session_state.get("q1"),
        "q2": st.session_state.get("q2"),
        "q3": st.session_state.get("q3"),
        "q4": st.session_state.get("q4"),
        "q5": st.session_state.get("q5"),
        "q6": st.session_state.get("q6"),
        "q7": st.session_state.get("q7"),
        "comments": st.session_state.get("survey_comments", "").strip() or None,
    }
    duration_seconds = max(0.0, time.time() - float(st.session_state.get("simulation_started_at", time.time())))
    return save_simulation_response(
        persona,
        attempts,
        metadata,
        responses,
        st.session_state.get("visited_attempts", []),
        duration_seconds,
    )


def render_survey_page(persona, metadata):
    attempts = persona.get("compose_attempts", [])
    render_survey_selection_styles()
    render_study_progress("survey")
    render_phase_banner(
        "Phase 3 of 3",
        "Evaluate the observed experience",
        "You have finished the observation phase. Treat the recorded interaction as if it had been your own use of QuizComp. Take in consideration the time that would have passed while reviewing the attempts, the mental effort required to read and evaluate the quizzes, and your overall satisfaction with the experience when answering the questions below.",
        alert="Please answer every question before submitting the questionnaire.",
    )
    st.title("Post-study survey")
    
    q1 = render_rating_buttons(
        "(Q1) Feeling of Accomplishment: How successful do you feel you were in building a quiz that matches your needs (topics and difficulty) during the session?",
        "q1",
    )
    q2 = render_rating_buttons(
        "(Q2) Effort Required: How much effort was required to inspect the proposed quizzes and decide whether to keep or change them?",
        "q2",
    )
    q3 = render_rating_buttons(
        "(Q3) Mental Demand: How mentally demanding was it to read and evaluate the candidate quizzes?",
        "q3",
    )
    q4 = render_rating_buttons(
        "(Q4) Perceived Controllability: How much control did you feel you had over the final quiz?",
        "q4",
    )
    q5 = render_rating_buttons(
        "(Q5) Temporal Demand: How time-pressured did you feel while composing the quiz within the allotted time?",
        "q5",
    )
    q6 = render_rating_buttons(
        "(Q6) Satisfaction: Overall, how satisfied are you with the final quiz you produced?",
        "q6",
    )
    q7 = render_yes_no_buttons(
        "(Q7) Would you use this system again?",
        "q7",
    )

    st.text_area(
        "Optional comments (what worked, what did not, suggestions)",
        key="survey_comments",
        height=140,
        placeholder="Write anything you noticed about the walkthrough, replay, or platform logic.",
    )

    if st.session_state.get("save_error"):
        render_callout("Survey save failed", st.session_state["save_error"], tone="warn")

    if st.session_state.get("survey_error"):
        render_callout("Survey incomplete", st.session_state["survey_error"], tone="warn")

    action_left, action_right = st.columns([1, 1])
    with action_left:
        if button_compat(st, "Back to replay", use_container_width=True):
            st.session_state["_pending_survey_reset"] = True
            st.session_state["phase"] = "replay"
            rerun_app()
    with action_right:
        if button_compat(st, "Submit survey", type="primary", use_container_width=True):
            missing = [
                label
                for label, value in [
                    ("Q1", q1),
                    ("Q2", q2),
                    ("Q3", q3),
                    ("Q4", q4),
                    ("Q5", q5),
                    ("Q6", q6),
                    ("Q7", q7),
                ]
                if value is None
            ]
            if missing:
                st.session_state["survey_error"] = f"Please answer all required items before submitting: {', '.join(missing)}."
                rerun_app()

            try:
                saved_path = persist_survey_response(persona, attempts, metadata)
                st.session_state["survey_saved_path"] = saved_path
                st.session_state["save_error"] = ""
                st.session_state["survey_error"] = ""
                st.session_state["phase"] = "done"
                rerun_app()
            except Exception as exc:
                st.session_state["save_error"] = str(exc)
                rerun_app()


def render_done_page():
    render_study_progress("done")
    render_phase_banner(
        "Study complete",
        "Thank you for completing the guided simulation",
        "You have finished the three study phases: guided exploration, attempt observation, and questionnaire-based evaluation.",
    )
    st.markdown("## Thank you")
    st.markdown("You have completed the guided simulation and submitted the post-study survey.")

    st.markdown("### What this simulation covered")
    st.markdown(
        """
        - You reviewed the study briefing and consent information.
        - You practiced the parameter controls without triggering a live composition.
        - You replayed one full recorded quiz-composition session.
        - You answered the post-study survey from the perspective of that session.
        """
    )

    completion_url = get_prolific_completion_url()
    if completion_url:
        st.markdown("### Return to Prolific")
        st.markdown(f"[Return to Prolific]({completion_url})")
        components.html(
            f"""
            <script>
            setTimeout(function() {{
                window.top.location.href = {json.dumps(completion_url)};
            }}, 1500);
            </script>
            """,
            height=0,
        )
    else:
        participant_context = st.session_state.get("participant_context") or get_participant_context()
        if participant_context.get("assignment_source") == "prolific":
            render_callout(
                "Prolific completion code required",
                "This submission was recorded, but no Prolific completion code is configured on the server yet. Set PROLIFIC_COMPLETION_CODE before launching Streamlit, then reopen the completion page to enable automatic return to Prolific.",
                tone="warn",
            )

    if button_compat(st, "Restart the simulation", use_container_width=True):
        reset_replay_state(clear_survey=True)
        st.session_state["phase"] = "landing"
        rerun_app()


def main():
    st.set_page_config(
        page_title="QuizComp guided simulation",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    render_styles()
    ensure_response_db()

    metadata, personas = load_simulation_data()
    if not personas:
        render_callout(
            "No replay data found",
            f"The app could not load any sessions from {SOURCE_DB_FILE.name}.",
            tone="warn",
        )
        return

    initialize_state(personas)
    selected_idx = render_sidebar(personas)
    persona = personas[selected_idx] if 0 <= selected_idx < len(personas) else {}

    phase = st.session_state.get("phase", "landing")
    if phase == "landing":
        render_study_briefing(len(personas), persona)
        return
    if phase == "consent":
        render_consent_page(personas)
        return
    if not persona:
        render_callout(
            "Assignment required",
            "This public study assigns a recorded teacher session only after consent through a Prolific link. For manual preview, add persona_id or replay_session_id to the URL.",
            tone="warn",
        )
        return
    if phase == "walkthrough":
        render_walkthrough_page(persona)
        return
    if phase == "replay":
        render_replay_page(persona)
        return
    if phase == "survey":
        render_survey_page(persona, metadata)
        return
    render_done_page()


if __name__ == "__main__":
    main()
