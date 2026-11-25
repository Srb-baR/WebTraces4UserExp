"""
Simulation_app/app.py
=====================
TestReco Guided Simulation Study

This Streamlit app mirrors the QuizComp simulation flow for the TestReco study.
Participants first inspect a guided walkthrough of the adaptive recommendation
interface, then replay one recorded learning session, and finally answer the
same post-study survey from the replayed learner's perspective.

Usage:
    streamlit run app.py

Optional direct assignment:
    http://localhost:8501/?replay_session_id=883
    http://localhost:8501/?replay_id=12
"""

import csv
import hmac
import os
from html import escape
import inspect
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[3] / ".env")


ROOT = Path(__file__).parent
LOCAL_RESULTS_DB_FILE = ROOT.parents[1] / "report/data/actors/testreco/results.db"
LOCAL_BENCHMARK_QA_FILE = ROOT / "QA.json"
LOCAL_DIFF_CSV_FILE = ROOT / "mathe_QA_scaled_difficulty.csv"
RESULTS_DB_FILE = LOCAL_RESULTS_DB_FILE if LOCAL_RESULTS_DB_FILE.exists() else ROOT.parent / "results.db"
BENCHMARK_QA_FILE = LOCAL_BENCHMARK_QA_FILE if LOCAL_BENCHMARK_QA_FILE.exists() else ROOT.parent / "benchmarks" / "MATHE" / "processed" / "QA.json"
DIFF_CSV_FILE = LOCAL_DIFF_CSV_FILE if LOCAL_DIFF_CSV_FILE.exists() else ROOT.parent / "benchmarks" / "MATHE" / "processed" / "mathe_QA_scaled_difficulty.csv"
SURVEY_DB_FILE = Path(os.environ.get("REVIEWER_OUTPUT_DIR", str(ROOT))) / "simulation_survey_responses.db"
ACCESS_CODE_FILE = ROOT / ".tunnel" / "access_code.txt"

PROLIFIC_PID_QUERY_PARAMS = ("PROLIFIC_PID", "prolific_pid", "participant_id", "participantId")
STUDY_ID_QUERY_PARAMS = ("STUDY_ID", "study_id")
SESSION_ID_QUERY_PARAMS = ("SESSION_ID", "session_id")
PROLIFIC_COMPLETION_CODE_QUERY_PARAMS = ("PROLIFIC_COMPLETION_CODE", "completion_code", "cc")

TOPIC_NAME = "Fundamental Mathematics"
QUESTIONS_PER_STEP = 5
TOTAL_STEPS = 10
PRETEST_QUESTIONS = 12

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
        "description": "Learn how the platform works before observation begins.",
        "phases": {"walkthrough"},
    },
    {
        "label": "Phase 2",
        "title": "Observe a replay",
        "description": "Move through the recorded session page by page.",
        "phases": {"replay"},
    },
    {
        "label": "Phase 3",
        "title": "Final questionnaire",
        "description": "Evaluate the observed experience from the learner's perspective.",
        "phases": {"survey", "done"},
    },
]

PHASE_STAGE_NOTES = {
    "consent": {
        "label": "",
        "title": "Review consent before Phase 1 begins",
        "description": "Once consent is confirmed, you will be taken to the platform overview that will allow you to understand how the interaction works.",
    },
    "walkthrough": {
        "label": "",
        "title": "Learn the interface before observation begins",
        "description": "First review the full prequalification test, then move to a short explanation of how the adaptive learning steps were presented.",
    },
    "replay": {
        "label": "",
        "title": "Observe the recorded learner journey",
        "description": "Move through the prequalification page first, then review each adaptive learning step before opening the final questionnaire.",
    },
    "survey": {
        "label": "",
        "title": "Evaluate the observed experience",
        "description": "You have finished the observation phase. Treat the recorded interaction as if it were your own use of TestReco. Take into consideration the time that would have been spent solving the MCQs, the mental effort required to read and answer the questions, and your overall satisfaction with the experience when answering the questions below.",
        "alert": "Please answer every question before submitting the questionnaire.",
    },
    "done": {
        "label": "",
        "title": "Questionnaire complete",
        "description": "Your feedback has been saved. You can restart the simulation if you want to inspect the assigned replay again.",
    },
}

PROMPT_LABELS = {
    None: "",
    "": "",
    # Canonical prompt types used by the simulation.
    "calibrated": "Calibrated",
    "role_anchored": "Role-Anchored",
    "task_constrained": "Task-Constrained",
    "survey": "Survey-Only",
    # Legacy spellings kept so older result DBs still render correctly.
    "detailed": "Calibrated",
    "general": "Task-Constrained",
    "simple": "Role-Anchored",
    "survey_only": "Survey-Only",
}

MODEL_LABELS = {
    # Original TestReco roster
    "gpt-5-2025-08-07": "GPT-5",
    "llama-3-8b": "LLaMA-3-8B",
    "mistral-24b-instruct": "Mistral-24B-Instruct",
    "chatgpt-4o-mini": "ChatGPT-4o-mini",
    # Ollama (local)
    "llama3.3:70b": "LLaMA-3.3-70B",
    "llama3.1:8b": "LLaMA-3.1-8B",
    # Bedrock
    "bedrock-claude-sonnet-4-6": "Claude Sonnet 4.6",
    "bedrock-claude-sonnet-5": "Claude Sonnet 5",
    "bedrock-claude-opus-4.7": "Claude Opus 4.7",
    "bedrock-claude-opus-4.8": "Claude Opus 4.8",
    "bedrock-claude-3-sonnet": "Claude 3 Sonnet",
    "bedrock-claude-fable-5": "Claude Fable 5",
    "bedrock-claude-fable-5.1": "Claude Fable 5.1",
    "bedrock-gpt-oss-120b": "GPT-OSS-120B",
    "bedrock-gpt-5.5": "GPT-5.5",
    "bedrock-gpt-5.6-luna": "GPT-5.6 Luna",
    "bedrock-gpt-5.6-sol": "GPT-5.6 Sol",
    "bedrock-gpt-5.6-terra": "GPT-5.6 Terra",
    "bedrock-gpt-6-astra": "GPT-6 Astra",
    "bedrock-claude-opus-5": "Claude Opus 5",
    "bedrock-grok-4.6": "Grok 4.6",
    "bedrock-deepseek-v3.2": "DeepSeek-V3.2",
    "bedrock-qwen3-32b": "Qwen3-32B",
    "bedrock-qwen3-next-80b-a3b": "Qwen3-Next-80B-A3B",
    "bedrock-qwen3-235b-a22b-2507": "Qwen3-235B-A22B",
    "bedrock-qwen3-coder-480b-a35b": "Qwen3-Coder-480B-A35B",
    "bedrock-devstral-2-123b": "Devstral-2-123B",
    "bedrock-nemotron-super-3-120b": "Nemotron-Super-3-120B",
    "bedrock-glm-4.7-flash": "GLM-4.7-Flash",
    "bedrock-glm-4.7": "GLM-4.7",
    "bedrock-glm-5": "GLM-5",
    "bedrock-llama-3.3-70b": "LLaMA-3.3-70B (Bedrock)",
    "bedrock-magistral-small-2509": "Magistral-Small-2509",
    "bedrock-mistral-large-2402": "Mistral Large (24.02)",
    "bedrock-mistral-large-3-675b": "Mistral Large 3",
    "bedrock-kimi-k2.5": "Kimi-K2.5",
    "bedrock-minimax-m2.5": "MiniMax-M2.5",
    "bedrock-nova-lite": "Nova Lite",
    "bedrock-gemma-3-12b-it": "Gemma-3-12B (Bedrock)",
    "bedrock-gemma-3-27b-it": "Gemma-3-27B (Bedrock)",
    # Hugging Face local
    "hf-gemma-3-27b": "Gemma-3-27B",
    "hf-gemma-3-12b": "Gemma-3-12B",
    "hf-gemma-3-4b": "Gemma-3-4B",
    "hf-deepseek-r1": "DeepSeek-R1",
    "hf-deepseek-r1-distill-llama-70b": "DeepSeek-R1-Distill-LLaMA-70B",
    "hf-deepseek-r1-distill-qwen-32b": "DeepSeek-R1-Distill-Qwen-32B",
    "hf-deepseek-r1-distill-llama-8b": "DeepSeek-R1-Distill-LLaMA-8B",
    "hf-qwen3-32b": "Qwen3-32B (HF)",
    "hf-qwen3-30b-a3b": "Qwen3-30B-A3B",
    "hf-qwen3-235b": "Qwen3-235B-A22B (HF)",
    "hf-kimi-k2": "Kimi-K2",
    "hf-moonlight-16b-a3b": "Moonlight-16B-A3B",
    "hf-llama-4-maverick": "LLaMA-4-Maverick",
    "hf-llama-3.1-8b": "LLaMA-3.1-8B (HF)",
    "qwen-14b": "Qwen2.5-14B",
    # OpenRouter
    "deepseek-v4-flash-0731": "DeepSeek-V4-Flash-0731",
}

OBJECTIVE_LABELS = {
    0: "Performance",
    1: "Gap",
    2: "Aptitude",
}

CACHE_DECORATOR = st.cache_data
BUTTON_PARAMS = inspect.signature(st.button).parameters
MODERN_STREAMLIT = hasattr(st, "cache_data")
DISPLAY_MATH_PATTERN = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


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


def get_prolific_completion_code():
    query_code = get_first_query_param(PROLIFIC_COMPLETION_CODE_QUERY_PARAMS, "")
    if query_code:
        return query_code

    env_code = str(os.getenv("PROLIFIC_COMPLETION_CODE", "") or "").strip()
    return env_code


def get_prolific_completion_url():
    participant_context = st.session_state.get("participant_context") or get_participant_context()
    if participant_context.get("assignment_source") != "prolific":
        return None

    completion_code = get_prolific_completion_code()
    if not completion_code:
        return None

    return f"https://app.prolific.com/submissions/complete?cc={completion_code}"


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


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pct(value):
    return f"{float(value) * 100:.0f}%"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_run_number(notes):
    match = re.search(r"run:\s*(\d+)", str(notes or ""))
    return int(match.group(1)) if match else None


def format_prompt_type(prompt_type):
    return PROMPT_LABELS.get(prompt_type, str(prompt_type or "").title())


def format_model_name(model_name):
    return MODEL_LABELS.get(model_name, str(model_name or "Unknown model"))


def format_actor_label(replay_meta):
    learner_id = safe_int(replay_meta.get("source_user_id"), -1)
    if learner_id < 0:
        learner_id = safe_int(replay_meta.get("source_session_id"), safe_int(replay_meta.get("replay_session_id"), -1))
    if learner_id >= 0:
        return f"Learner {learner_id}"
    return "Assigned learner"


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


@CACHE_DECORATOR
def load_question_bank():
    with open(BENCHMARK_QA_FILE, encoding="utf-8") as handle:
        raw = json.load(handle)

    question_items = raw.get("questions", raw if isinstance(raw, list) else [])

    diff_map = {}
    with open(DIFF_CSV_FILE, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            qid = str(row.get("question_id", "")).strip()
            if not qid:
                continue
            diff_map[qid] = {
                "original_difficulty": safe_float(row.get("original_difficulty"), 0.0),
                "scaled_difficulty": safe_float(row.get("scaled_difficulty"), 0.0),
            }

    questions = []
    for question in question_items:
        if str(question.get("topic") or "").strip() != TOPIC_NAME:
            continue

        dataset_id = str(question.get("id") or "").strip()
        diff_info = diff_map.get(
            dataset_id,
            {
                "original_difficulty": safe_float(question.get("level"), 0.0),
                "scaled_difficulty": 0.0,
            },
        )
        options = [str(option) for option in question.get("options", [])]
        questions.append(
            {
                "env_index": len(questions),
                "dataset_id": dataset_id,
                "question": str(question.get("question") or "Question text unavailable."),
                "options": options,
                "correct_answer_index": safe_int(question.get("correct_answer"), -1),
                "topic": str(question.get("topic") or TOPIC_NAME),
                "subtopic": str(question.get("subtopic") or "Unknown subtopic"),
                "difficulty_level": safe_int(question.get("level"), 0),
                "original_difficulty": diff_info["original_difficulty"],
                "scaled_difficulty": diff_info["scaled_difficulty"],
            }
        )

    return questions


def question_for_env_index(env_index):
    questions = load_question_bank()
    if 0 <= env_index < len(questions):
        return questions[env_index]
    return {
        "env_index": env_index,
        "dataset_id": f"missing_{env_index}",
        "question": f"Question content unavailable for index {env_index}.",
        "options": [],
        "correct_answer_index": -1,
        "topic": TOPIC_NAME,
        "subtopic": "Unavailable",
        "difficulty_level": 0,
        "original_difficulty": 0.0,
        "scaled_difficulty": 0.0,
    }


def simulation_questions_table_exists(connection):
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'simulation_questions'"
    ).fetchone()
    return row is not None


def load_stored_simulation_questions(connection, replay_session_id):
    """Load the questions persisted by the simulation for one session.

    LLM sessions store the exact MCQ text/options they were shown in
    `simulation_questions` (written by llm_user_study/db_saver.py). Preferring
    that table over re-deriving questions from QA.json keeps the replay faithful
    even if the benchmark file is later reordered or edited.

    Returns {(phase, step_index, question_index): question_payload}.
    """
    if not simulation_questions_table_exists(connection):
        return {}

    rows = connection.execute(
        """
        SELECT phase, step_index, question_index, dataset_question_id,
               topic, subtopic, question_text, options_json,
               correct_option_index, chosen_option_index, is_correct,
               original_difficulty, scaled_difficulty
        FROM simulation_questions
        WHERE session_id = ?
        ORDER BY phase, step_index, position_in_step, id
        """,
        (replay_session_id,),
    ).fetchall()

    stored = {}
    for row in rows:
        row = dict(row)
        try:
            options = json.loads(row.get("options_json") or "[]")
        except json.JSONDecodeError:
            options = []
        env_index = safe_int(row.get("question_index"), -1)
        step_index = row.get("step_index")
        stored[(str(row.get("phase")), step_index, env_index)] = {
            "env_index": env_index,
            "dataset_id": str(row.get("dataset_question_id") or f"missing_{env_index}"),
            "question": str(row.get("question_text") or "Question text unavailable."),
            "options": [str(option) for option in options],
            "correct_answer_index": safe_int(row.get("correct_option_index"), -1),
            "chosen_option_index": row.get("chosen_option_index"),
            "topic": row.get("topic") or TOPIC_NAME,
            "subtopic": row.get("subtopic") or "Unknown subtopic",
            "difficulty_level": safe_int(row.get("original_difficulty"), 0),
            "original_difficulty": safe_float(row.get("original_difficulty"), 0.0),
            "scaled_difficulty": safe_float(row.get("scaled_difficulty"), 0.0),
        }
    return stored


@CACHE_DECORATOR
def load_replay_catalog():
    catalog = []
    with sqlite3.connect(RESULTS_DB_FILE) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            WITH completed_step_counts AS (
                SELECT session_id, COUNT(*) AS step_count
                FROM learning_steps
                GROUP BY session_id
            )
            SELECT
                s.id AS replay_session_id,
                s.simulated_session_id,
                COALESCE(s.simulated_session_id, s.id) AS source_session_id,
                src.user_id AS source_user_id,
                s.prompt_type,
                s.model_name,
                s.status,
                s.pretest_num_questions,
                s.pretest_correct,
                s.pretest_mastery_init,
                s.total_steps_planned,
                s.questions_per_step,
                s.final_mastery,
                s.final_accuracy,
                s.notes,
                u.username
            FROM study_sessions s
                        JOIN completed_step_counts csc ON csc.session_id = s.id
                        JOIN survey_responses sr ON sr.session_id = s.id
                        LEFT JOIN study_sessions src ON src.id = COALESCE(s.simulated_session_id, s.id)
            JOIN users u ON u.id = s.user_id
            WHERE s.status = 'completed'
                            AND s.total_steps_planned = ?
                            AND csc.step_count = ?
              AND (
                    s.simulated_session_id IS NULL
                    OR (s.simulated_session_id IS NOT NULL AND s.notes LIKE '%run: 0%')
                  )
            ORDER BY
                CASE WHEN s.simulated_session_id IS NULL THEN 0 ELSE 1 END,
                COALESCE(s.simulated_session_id, s.id),
                COALESCE(s.prompt_type, ''),
                s.model_name,
                s.id
                        """,
                        (TOTAL_STEPS, TOTAL_STEPS),
        ).fetchall()

    for replay_id, row in enumerate(rows):
        replay_meta = dict(row)
        replay_meta["replay_id"] = replay_id
        replay_meta["actor_type"] = "llm" if replay_meta.get("simulated_session_id") is not None else "human"
        replay_meta["run_number"] = parse_run_number(replay_meta.get("notes"))
        replay_meta["actor_label"] = format_actor_label(replay_meta)
        replay_meta["source_session_label"] = f"Session {replay_meta['source_session_id']}"
        catalog.append(replay_meta)

    return catalog


@CACHE_DECORATOR
def load_replay_detail(replay_session_id):
    replay_session_id = safe_int(replay_session_id, -1)
    questions = load_question_bank()

    with sqlite3.connect(RESULTS_DB_FILE) as connection:
        connection.row_factory = sqlite3.Row

        session_row = connection.execute(
            "SELECT * FROM study_sessions WHERE id = ?",
            (replay_session_id,),
        ).fetchone()
        if session_row is None:
            return None

        session = dict(session_row)
        survey_row = connection.execute(
            "SELECT * FROM survey_responses WHERE session_id = ?",
            (replay_session_id,),
        ).fetchone()
        survey = dict(survey_row) if survey_row is not None else None

        pretest_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM attempts
                WHERE session_id = ? AND phase = 'pretest'
                ORDER BY id
                """,
                (replay_session_id,),
            ).fetchall()
        ]

        learning_step_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM learning_steps
                WHERE session_id = ?
                ORDER BY step_index
                """,
                (replay_session_id,),
            ).fetchall()
        ]

        learning_attempt_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM attempts
                WHERE session_id = ? AND phase = 'learning'
                ORDER BY step_index, id
                """,
                (replay_session_id,),
            ).fetchall()
        ]

        orchestrator_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM orchestrator_calls
                WHERE session_id = ?
                ORDER BY step_index
                """,
                (replay_session_id,),
            ).fetchall()
        ]

        # Questions persisted by the simulation (LLM sessions). Crowd sessions
        # have no rows here and fall back to the QA.json question bank.
        stored_questions = load_stored_simulation_questions(connection, replay_session_id)

    attempts_by_step = {}
    for attempt in learning_attempt_rows:
        step_index = safe_int(attempt.get("step_index"), -1)
        attempts_by_step.setdefault(step_index, []).append(attempt)

    orchestrator_by_step = {
        safe_int(row.get("step_index"), -1): row for row in orchestrator_rows
    }

    def build_question_review(question_payload, attempt_payload=None, phase_label="learning"):
        attempt_payload = attempt_payload or {}
        options = list(question_payload.get("options") or [])
        chosen_option_index = attempt_payload.get("chosen_option_index")
        correct_option_index = attempt_payload.get("correct_option_index")
        if chosen_option_index is None:
            chosen_option_index = question_payload.get("chosen_option_index")
        if correct_option_index is None:
            correct_option_index = question_payload.get("correct_answer_index")
        chosen_option_index = safe_int(chosen_option_index, -1)
        correct_option_index = safe_int(correct_option_index, -1)

        chosen_option = options[chosen_option_index] if 0 <= chosen_option_index < len(options) else None
        correct_option = options[correct_option_index] if 0 <= correct_option_index < len(options) else None

        return {
            "phase": phase_label,
            "env_index": safe_int(attempt_payload.get("question_index"), question_payload.get("env_index", -1)),
            "dataset_question_id": str(
                attempt_payload.get("dataset_question_id")
                or question_payload.get("dataset_id")
                or f"missing_{question_payload.get('env_index', -1)}"
            ),
            "topic": question_payload.get("topic") or TOPIC_NAME,
            "subtopic": question_payload.get("subtopic") or "Unknown subtopic",
            "difficulty_level": safe_int(question_payload.get("difficulty_level"), 0),
            "scaled_difficulty": safe_float(question_payload.get("scaled_difficulty"), 0.0),
            "question": question_payload.get("question") or "Question text unavailable.",
            "options": options,
            "chosen_option_index": chosen_option_index,
            "correct_option_index": correct_option_index,
            "chosen_option": chosen_option,
            "correct_option": correct_option,
            "is_correct": bool(attempt_payload.get("is_correct")) if attempt_payload else None,
            "response_time_ms": safe_int(attempt_payload.get("response_time_ms"), 0) if attempt_payload else 0,
        }

    def resolve_question_payload(phase_label, step_index, env_index):
        """Prefer the question stored with the run, fall back to QA.json."""
        stored = stored_questions.get((phase_label, step_index, env_index))
        if stored is not None:
            return stored
        return question_for_env_index(env_index)

    pretest_attempts = []
    for attempt in pretest_rows:
        env_index = safe_int(attempt.get("question_index"), -1)
        question_payload = resolve_question_payload("pretest", None, env_index)
        pretest_attempts.append(build_question_review(question_payload, attempt, phase_label="pretest"))

    steps = []
    mastery_series = [safe_float(session.get("pretest_mastery_init"), 0.0)]
    for step_row in learning_step_rows:
        step_index = safe_int(step_row.get("step_index"), 0)
        attempt_list = attempts_by_step.get(step_index, [])
        attempt_map = {safe_int(attempt.get("question_index"), -1): attempt for attempt in attempt_list}

        try:
            selected_indices = json.loads(step_row.get("selected_question_indices_json") or "[]")
        except json.JSONDecodeError:
            selected_indices = []
        selected_indices = [safe_int(index, -1) for index in selected_indices]
        if not selected_indices:
            selected_indices = [safe_int(attempt.get("question_index"), -1) for attempt in attempt_list]

        question_reviews = []
        for env_index in selected_indices:
            question_payload = resolve_question_payload("learning", step_index, env_index)
            question_reviews.append(
                build_question_review(question_payload, attempt_map.get(env_index), phase_label="learning")
            )

        orchestrator_row = orchestrator_by_step.get(step_index, {})
        action = safe_int(step_row.get("action"), -1)
        strategy = orchestrator_row.get("selected_strategy") or OBJECTIVE_LABELS.get(action, f"Action {action}")
        step_payload = {
            "step_index": step_index,
            "action": action,
            "strategy": strategy,
            "mastery_before": safe_float(step_row.get("mastery_before"), 0.0),
            "mastery_after": safe_float(step_row.get("mastery_after"), 0.0),
            "rolling_accuracy": safe_float(step_row.get("rolling_accuracy"), 0.0),
            "reward_perf": safe_float(step_row.get("reward_perf"), 0.0),
            "reward_gap": safe_float(step_row.get("reward_gap"), 0.0),
            "reward_apt": safe_float(step_row.get("reward_apt"), 0.0),
            "latency_s": safe_float(orchestrator_row.get("latency_s"), 0.0) if orchestrator_row else None,
            "state_obs_json": orchestrator_row.get("state_obs_json") if orchestrator_row else None,
            "questions": question_reviews,
        }
        steps.append(step_payload)
        mastery_series.append(step_payload["mastery_after"])

    pages = [
        {
            "kind": "pretest",
            "title": "Prequalification test",
        }
    ]
    for step in steps:
        pages.append(
            {
                "kind": "step",
                "title": f"Learning step {step['step_index'] + 1}",
                "step_index": step["step_index"],
            }
        )

    detail = {
        "session": session,
        "survey": survey,
        "pretest_attempts": pretest_attempts,
        "steps": steps,
        "pages": pages,
        "mastery_series": mastery_series,
    }
    return detail


def ensure_response_db():
    SURVEY_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SURVEY_DB_FILE) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT NOT NULL UNIQUE,
                replay_session_id INTEGER NOT NULL,
                source_session_id INTEGER NOT NULL,
                source_user_id INTEGER,
                actor_type TEXT NOT NULL,
                model_name TEXT,
                prompt_type TEXT,
                reviewed_pages_json TEXT,
                q1_accomplishment INTEGER,
                q2_effort_required INTEGER,
                q3_mental_demand INTEGER,
                q4_perceived_controllability INTEGER,
                q5_temporal_demand INTEGER,
                q6_frustration INTEGER,
                q7_trust INTEGER,
                would_use_again INTEGER,
                comments TEXT,
                duration_seconds REAL,
                created_at TEXT NOT NULL
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
                replay_index INTEGER NOT NULL,
                assigned_at TEXT NOT NULL,
                completed_review_id TEXT,
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
        feedback_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(simulation_feedback)").fetchall()
        }
        if "source_user_id" not in feedback_columns:
            connection.execute("ALTER TABLE simulation_feedback ADD COLUMN source_user_id INTEGER")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_simulation_feedback_source_user_id
            ON simulation_feedback (source_user_id)
            """
        )
        connection.commit()


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


def allocate_replay_session_id(catalog, participant_context):
    ensure_response_db()
    if not catalog:
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
        for candidate_index in range(max(0, next_index), len(catalog)):
            candidate_session_id = safe_int(catalog[candidate_index].get("replay_session_id"), -1)
            if candidate_session_id not in assigned_replay_ids:
                replay_index = candidate_index
                replay_session_id = candidate_session_id
                break

        if replay_index < 0:
            final_index = len(catalog)
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
                replay_index,
                assigned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                participant_key,
                participant_context.get("prolific_pid"),
                participant_context.get("study_id"),
                participant_context.get("prolific_session_id"),
                participant_context.get("assignment_source") or "unknown",
                replay_session_id,
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


def reset_survey_state():
    st.session_state["simulation_started_at"] = time.time()
    for survey_key in ["q1", "q2", "q3", "q4", "q5", "q6", "q7"]:
        st.session_state[survey_key] = None
        for option in [1, 2, 3, 4, 5]:
            st.session_state.pop(f"{survey_key}_{option}_choice", None)
    st.session_state["would_use_again"] = False
    st.session_state["survey_comments"] = ""
    st.session_state["survey_error"] = ""
    st.session_state["save_error"] = ""
    st.session_state["saved_review_id"] = ""


def reset_replay_state(clear_survey=False):
    st.session_state["replay_page_idx"] = 0
    st.session_state["visited_replay_pages"] = []
    if clear_survey:
        reset_survey_state()


def update_single_choice_checkbox(state_key, selected_value, options):
    widget_key = f"{state_key}_{selected_value}_choice"
    if st.session_state.get(widget_key):
        st.session_state[state_key] = selected_value
    elif st.session_state.get(state_key) == selected_value:
        st.session_state[state_key] = None

    current = st.session_state.get(state_key)
    for option in options:
        st.session_state[f"{state_key}_{option}_choice"] = option == current


def get_explicit_replay_session_id(catalog):
    replay_ids = [safe_int(item["replay_session_id"], -1) for item in catalog]

    requested_session_id = safe_int(get_query_param("replay_session_id", -1), -1)
    if requested_session_id in replay_ids:
        return requested_session_id

    requested_index = safe_int(get_query_param("replay_id", 0), 0)
    requested_index = max(0, min(requested_index, len(catalog) - 1)) if catalog else 0
    if catalog:
        explicit_replay_id = str(get_query_param("replay_id", "") or "").strip()
        if explicit_replay_id:
            return safe_int(catalog[requested_index]["replay_session_id"], -1)

    return -1


def get_initial_replay_session_id(catalog, participant_assignment):
    explicit_replay_session_id = get_explicit_replay_session_id(catalog)
    if explicit_replay_session_id >= 0:
        return explicit_replay_session_id

    if participant_assignment is not None:
        return safe_int(participant_assignment.get("replay_session_id"), -1)

    return -1


def assign_replay_for_participant(catalog, participant_context):
    explicit_replay_session_id = get_explicit_replay_session_id(catalog)
    if explicit_replay_session_id >= 0:
        return explicit_replay_session_id, False, ""

    participant_assignment = get_participant_assignment(participant_context)
    if participant_assignment is not None:
        return safe_int(participant_assignment.get("replay_session_id"), -1), False, ""

    if (participant_context or {}).get("assignment_source") != "prolific":
        return (
            -1,
            False,
            "Use the Prolific study link to start this public study. For a manual preview, add replay_id or replay_session_id to the URL.",
        )

    replay_session_id = allocate_replay_session_id(catalog, participant_context)
    if replay_session_id < 0:
        return (
            -1,
            True,
            "All available replay sessions have already been assigned. This testing link is no longer accepting new participants.",
        )

    return replay_session_id, False, ""



def initialize_state(catalog):
    participant_context = get_participant_context()
    st.session_state["participant_context"] = participant_context
    participant_assignment = get_participant_assignment(participant_context)
    requested_replay_session_id = get_initial_replay_session_id(catalog, participant_assignment)
    st.session_state["participant_assignment"] = participant_assignment
    st.session_state.setdefault("assignments_exhausted", False)

    if st.session_state.pop("_pending_survey_reset", False):
        reset_survey_state()

    st.session_state.setdefault("phase", "landing")
    current_phase = st.session_state.get("phase", "landing")
    previous_phase = st.session_state.get("_last_phase")

    st.session_state.setdefault("assigned_replay_session_id", requested_replay_session_id)
    st.session_state.setdefault("replay_page_idx", 0)
    st.session_state.setdefault("visited_replay_pages", [])
    st.session_state.setdefault("simulation_started_at", time.time())
    st.session_state.setdefault("survey_error", "")
    st.session_state.setdefault("save_error", "")
    st.session_state.setdefault("saved_review_id", "")
    st.session_state.setdefault("consent_error", "")
    st.session_state.setdefault("survey_comments", "")
    st.session_state.setdefault("would_use_again", False)
    st.session_state.setdefault("walkthrough_step", "pretest")

    for survey_key in ["q1", "q2", "q3", "q4", "q5", "q6", "q7"]:
        st.session_state.setdefault(survey_key, None)

    if current_phase == "landing" and previous_phase != "landing":
        reset_replay_state(clear_survey=True)
        st.session_state["walkthrough_step"] = "pretest"

    if st.session_state.get("_last_requested_replay_session_id") != requested_replay_session_id:
        st.session_state["assigned_replay_session_id"] = requested_replay_session_id
        reset_replay_state(clear_survey=True)
        st.session_state["walkthrough_step"] = "pretest"

    if participant_assignment and participant_assignment.get("completed_review_id"):
        st.session_state["saved_review_id"] = participant_assignment["completed_review_id"]
        st.session_state["phase"] = "done"
        st.session_state["assignments_exhausted"] = False

    st.session_state["_last_requested_replay_session_id"] = requested_replay_session_id
    st.session_state["_last_phase"] = current_phase


def get_replay_meta(catalog, replay_session_id):
    for replay in catalog:
        if safe_int(replay.get("replay_session_id"), -1) == safe_int(replay_session_id, -1):
            return replay
    return None


def mark_replay_page_visited(page_idx):
    visited = list(st.session_state.get("visited_replay_pages", []))
    if page_idx not in visited:
        visited.append(page_idx)
        visited.sort()
        st.session_state["visited_replay_pages"] = visited


def render_styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

        html, body, .stApp {
            font-family: 'IBM Plex Sans', sans-serif !important;
        }

        h1, h2, h3, h4, h5, h6 {
            font-family: 'Space Grotesk', sans-serif !important;
            letter-spacing: -0.02em;
        }

        .main .block-container {
            max-width: 1180px !important;
            padding-top: 1.2rem !important;
        }

        section[data-testid="stSidebar"] {
            background: #0f172a !important;
            min-width: 310px !important;
            max-width: 310px !important;
        }

        section[data-testid="stSidebar"] * {
            color: #dbe4ef !important;
        }

        .stage-ribbon {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.85rem;
            margin-bottom: 0.8rem;
        }

        .stage-card {
            background: linear-gradient(180deg, rgba(248, 250, 252, 0.98) 0%, rgba(241, 245, 249, 0.98) 100%);
            border: 1px solid rgba(148, 163, 184, 0.28);
            border-radius: 20px;
            padding: 0.95rem 1rem 1.05rem;
            min-height: 10.4rem;
            box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
        }

        .stage-card.active {
            background: linear-gradient(180deg, #dce8fa 0%, #edf4ff 100%);
            border-color: rgba(148, 184, 224, 0.82);
        }

        .stage-card.completed {
            background: linear-gradient(180deg, #e8f1ff 0%, #f7fbff 100%);
            border-color: rgba(191, 219, 254, 0.92);
        }

        .stage-index {
            width: 2.65rem;
            height: 2.65rem;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            background: #dce3ed;
            color: #243b53;
            font-size: 1.3rem;
            font-weight: 700;
            margin-bottom: 0.75rem;
        }

        .stage-card.active .stage-index {
            background: #18385a;
            color: #f8fbff;
        }

        .stage-card.completed .stage-index {
            background: #b7cde6;
            color: #18385a;
        }

        .stage-label {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.18em;
            color: #64748b;
            font-weight: 700;
            margin-bottom: 0.35rem;
        }

        .stage-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.96rem;
            line-height: 1.4;
            color: #1e293b;
            font-weight: 700;
            margin-bottom: 0.4rem;
        }

        .stage-description {
            font-size: 0.84rem;
            line-height: 1.55;
            color: #556579;
        }

        .stage-banner {
            background: linear-gradient(135deg, #dce9fb 0%, #ebf4ff 100%);
            border: 1px solid rgba(167, 198, 242, 0.9);
            border-radius: 22px;
            padding: 1.15rem 1.25rem;
            margin-bottom: 1.45rem;
            box-shadow: 0 18px 42px rgba(15, 23, 42, 0.08);
        }

        .stage-banner-label {
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.18em;
            color: #2f5f95;
            font-weight: 700;
            margin-bottom: 0.4rem;
        }

        .stage-banner-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.65rem;
            line-height: 1.15;
            color: #17233a;
            font-weight: 700;
            margin-bottom: 0.45rem;
        }

        .stage-banner-description {
            font-size: 0.98rem;
            line-height: 1.65;
            color: #41556d;
            margin: 0;
            max-width: 52rem;
        }

        .stage-banner-alert {
            font-size: 0.96rem;
            line-height: 1.65;
            color: #dc2626;
            font-weight: 700;
            margin-top: 0.6rem;
            max-width: 52rem;
        }

        .hero-card {
            background: linear-gradient(135deg, #0f3d5e 0%, #164e63 55%, #1d4ed8 100%);
            border-radius: 20px;
            padding: 1.5rem 1.6rem;
            color: white;
            margin-bottom: 1rem;
        }

        .hero-card * {
            color: white !important;
        }

        .briefing-card {
            background: white;
            border: 1px solid #dbe4ef;
            border-radius: 18px;
            padding: 1.2rem 1.3rem;
            margin-bottom: 1rem;
        }

        .briefing-card .eyebrow {
            color: #1d4d7a;
            margin-bottom: 0.45rem;
        }

        .briefing-card h1,
        .briefing-card h2,
        .briefing-card p,
        .briefing-card li,
        .briefing-card strong {
            color: #334155 !important;
        }

        .briefing-card h1 {
            font-size: 1.75rem;
            line-height: 1.2;
            margin: 0 0 0.7rem;
            color: #15324f !important;
        }

        .briefing-card h2 {
            font-size: 1rem;
            margin: 1rem 0 0.4rem;
            color: #15324f !important;
        }

        .briefing-card p,
        .briefing-card li {
            font-size: 0.95rem;
            line-height: 1.7;
        }

        .briefing-card p {
            margin: 0 0 0.8rem;
        }

        .briefing-card ul {
            margin-top: 0.35rem;
            margin-bottom: 0.65rem;
            margin-left: 0;
            padding: 0;
            padding-left: 1.2rem;
        }

        .briefing-lead {
            font-size: 0.95rem !important;
            line-height: 1.7 !important;
            color: #334155 !important;
            margin-bottom: 0.8rem !important;
        }

        .page-intro {
            margin-bottom: 1.45rem;
        }

        .page-intro-eyebrow {
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            color: #8aa9cf;
            font-weight: 700;
            margin-bottom: 0.7rem;
        }

        .page-intro h1 {
            font-size: clamp(2.3rem, 4vw, 3.25rem);
            line-height: 1.05;
            margin: 0 0 0.85rem;
            color: var(--text-color);
        }

        .page-intro p {
            font-size: 1.02rem;
            line-height: 1.8;
            margin: 0;
            color: var(--text-color);
            opacity: 0.86;
            max-width: 64rem;
        }

        .intro-card,
        .section-card,
        .attempt-card {
            background: white;
            border: 1px solid #dbe4ef;
            border-radius: 18px;
            padding: 1rem 1.1rem;
            margin-bottom: 1rem;
            box-shadow: 0 12px 32px rgba(15, 23, 42, 0.05);
        }

        .summary-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.8rem;
            margin: 0.8rem 0 1rem;
        }

        .summary-item {
            background: rgba(248, 250, 252, 0.95);
            border: 1px solid #dbe4ef;
            border-radius: 14px;
            padding: 0.9rem 1rem;
        }

        .summary-item .label {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: #64748b;
            margin-bottom: 0.35rem;
            font-weight: 700;
        }

        .summary-item .value {
            font-size: 1.15rem;
            color: #0f172a;
            font-weight: 700;
        }

        .callout {
            border-radius: 14px;
            padding: 0.9rem 1rem;
            margin: 0.75rem 0;
            border: 1px solid #cbd5e1;
            background: #f8fafc;
            color: #0f172a;
        }

        .callout strong {
            display: block;
            margin-bottom: 0.25rem;
        }

        .callout.subtle {
            background: #f8fafc;
            border-color: #dbe4ef;
        }

        .callout.accent {
            background: #eef6ff;
            border-color: #bfdbfe;
        }

        .callout.good {
            background: #ecfdf5;
            border-color: #86efac;
        }

        .callout.warn {
            background: #fff7ed;
            border-color: #fdba74;
        }

        .callout.bad {
            background: #fef2f2;
            border-color: #fca5a5;
        }

        .metric-chip {
            display: inline-block;
            border-radius: 999px;
            padding: 0.22rem 0.7rem;
            font-size: 0.82rem;
            font-weight: 700;
            background: #eff6ff;
            color: #1d4ed8;
            margin-right: 0.4rem;
            margin-bottom: 0.4rem;
        }

        .replay-nav-title {
            font-size: 1.35rem;
            font-weight: 700;
            color: #0f172a;
        }

        .rating-label {
            margin-top: 0.25rem;
            margin-bottom: 1rem;
            color: #64748b;
            font-size: 0.95rem;
        }

        .sidebar-panel {
            background: rgba(15, 23, 42, 0.36);
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 14px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.85rem;
        }

        .eyebrow {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            color: #94a3b8;
            font-weight: 700;
            margin-bottom: 0.45rem;
        }

        .phase-step {
            padding: 0.48rem 0.65rem;
            border-radius: 10px;
            margin-bottom: 0.35rem;
            background: rgba(148, 163, 184, 0.10);
            color: #dbe4ef;
            font-size: 0.92rem;
        }

        .phase-step.active {
            background: rgba(59, 130, 246, 0.20);
            border: 1px solid rgba(96, 165, 250, 0.45);
            color: #eff6ff;
            font-weight: 700;
        }

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
            min-width: 3.1rem;
            min-height: 3.1rem;
            padding: 0.7rem 0.95rem !important;
            border: 1px solid rgba(148, 163, 184, 0.34) !important;
            border-radius: 0.7rem !important;
            background: rgba(15, 23, 42, 0.35) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            transition: all 0.15s ease;
        }

        div[data-testid="stCheckbox"] label[data-baseweb="checkbox"] > div:last-child p {
            margin: 0 !important;
            font-size: 1.02rem !important;
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

        @media (max-width: 900px) {
            .stage-ribbon {
                grid-template-columns: 1fr 1fr;
            }

            .summary-grid {
                grid-template-columns: 1fr 1fr;
            }
        }

        @media (max-width: 700px) {
            .stage-ribbon {
                grid-template-columns: 1fr;
            }

            .stage-card {
                min-height: auto;
            }

            .stage-banner {
                padding: 1.2rem 1.15rem;
            }

            .stage-banner-title {
                font-size: 1.55rem;
            }

            .summary-grid {
                grid-template-columns: 1fr;
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


def load_access_code():
    if not ACCESS_CODE_FILE.exists():
        return None
    access_code = ACCESS_CODE_FILE.read_text(encoding="utf-8").strip()
    return access_code or None


def render_access_gate(access_code):
    if st.session_state.get("access_granted") and st.session_state.get("_access_granted_code") == access_code:
        return True

    st.markdown(
        "<div class='hero-card'>"
        "<h1>Supervisor preview</h1>"
        "<p>This public link is protected. Enter the shared access code to open the TestReco study interface.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    entered_code = st.text_input("Access code", type="password", key="access_code_input")
    if button_compat(st, "Open gated preview", type="primary", use_container_width=True):
        if hmac.compare_digest(entered_code.strip(), access_code):
            st.session_state["access_granted"] = True
            st.session_state["_access_granted_code"] = access_code
            st.session_state["access_code_error"] = ""
            rerun_app()
        else:
            st.session_state["access_code_error"] = "Incorrect access code."

    if st.session_state.get("access_code_error"):
        render_callout("Access required", st.session_state["access_code_error"], tone="warn")

    return False


def render_study_progress(current_phase):
    cards = []
    for phase_name in PHASES:
        active_class = "active" if phase_name == current_phase else ""
        cards.append(f"<div class='phase-step {active_class}'>{PHASE_LABELS[phase_name]}</div>")
    st.sidebar.markdown(
        "<div class='sidebar-panel'><div class='eyebrow'>Study flow</div>" + "".join(cards) + "</div>",
        unsafe_allow_html=True,
    )


def render_sidebar(catalog, replay_meta, replay_detail):
    phase = st.session_state.get("phase", "landing")
    assigned_learner_label = replay_meta["actor_label"] if replay_meta is not None else "Assigned after consent"
    assigned_learner_detail = (
        f"Replayed session {replay_meta['replay_session_id']}"
        if replay_meta is not None
        else "This public deployment assigns one recorded learner journey after you confirm consent through the Prolific link."
    )

    st.sidebar.markdown(
        """
        <div style='padding-top:0.2rem;padding-bottom:0.5rem'>
            <div style='font-size:11px;text-transform:uppercase;letter-spacing:0.14em;color:#93a5bc;font-weight:600;'>
                TestReco Simulation
            </div>
            <div style='font-size:20px;font-weight:700;color:#f8fafc;'>
                Guided experience viewer
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown(
        (
            "<div class='sidebar-panel'>"
            "<div class='eyebrow'>Goal of the simulation</div>"
            "<div style='font-size:13px;line-height:1.6;color:#dbe4ef !important;'>"
            "You will not solve the mathematics session yourself. Instead, you will inspect one complete recorded "
            "learning journey, follow the learner's answers step by step, and finally answer the qualitative survey "
            "as if that observed experience had been your own."
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )

    render_study_progress(phase)

    st.sidebar.markdown(
        (
            "<div class='sidebar-panel'>"
            "<div class='eyebrow'>Assigned learner</div>"
            f"<div style='font-size:15px;font-weight:700;color:#f8fafc !important;'>{assigned_learner_label}</div>"
            f"<div style='font-size:12px;line-height:1.5;color:#b7c5d8 !important;margin-top:0.3rem;'>"
            f"{assigned_learner_detail}"
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )

    if phase == "replay" and replay_detail is not None:
        st.sidebar.markdown("**Replay pages**")
        current_page_idx = safe_int(st.session_state.get("replay_page_idx"), 0)
        visited = set(st.session_state.get("visited_replay_pages", []))
        for idx, page in enumerate(replay_detail["pages"]):
            suffix = " (reviewed)" if idx in visited else ""
            label = f"{page['title']}{suffix}"
            if button_compat(
                st.sidebar,
                label,
                key=f"replay_page_{idx}",
                type="primary" if idx == current_page_idx else "secondary",
                use_container_width=True,
            ):
                st.session_state["replay_page_idx"] = idx
                rerun_app()

def get_stage_index_for_phase(current_phase):
    for index, stage in enumerate(STUDY_STAGES):
        if current_phase in stage["phases"]:
            return index
    return len(STUDY_STAGES) - 1


def render_phase_banner(current_phase, note_override=None):
    stage_index = get_stage_index_for_phase(current_phase)
    note = note_override or PHASE_STAGE_NOTES.get(current_phase, PHASE_STAGE_NOTES["done"])

    cards = []
    for index, stage in enumerate(STUDY_STAGES):
        if index < stage_index:
            state_class = "completed"
        elif index == stage_index:
            state_class = "active"
        else:
            state_class = "upcoming"

        cards.append(
            (
                f"<div class='stage-card {state_class}'>"
                f"<div class='stage-index'>{index + 1}</div>"
                + (f"<div class='stage-label'>{escape(stage['label'])}</div>" if stage.get("label") else "")
                + f"<div class='stage-title'>{escape(stage['title'])}</div>"
                + f"<div class='stage-description'>{escape(stage['description'])}</div>"
                + "</div>"
            )
        )

    banner_label = f"<div class='stage-banner-label'>{escape(note['label'])}</div>" if note.get("label") else ""
    banner_alert = f"<div class='stage-banner-alert'>{escape(note['alert'])}</div>" if note.get("alert") else ""
    st.markdown(
        (
            "<div class='stage-ribbon'>"
            + "".join(cards)
            + "</div>"
            + "<div class='stage-banner'>"
            + banner_label
            + f"<div class='stage-banner-title'>{escape(note['title'])}</div>"
            + f"<p class='stage-banner-description'>{escape(note['description'])}</p>"
            + banner_alert
            + "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_hero(title, body, eyebrow, banner_override=None):
    current_phase = st.session_state.get("phase", "landing")
    if current_phase != "landing":
        render_phase_banner(current_phase, note_override=banner_override)

    eyebrow_html = f"<div class='page-intro-eyebrow'>{escape(eyebrow)}</div>" if eyebrow else ""
    st.markdown(
        (
            "<div class='page-intro'>"
            + eyebrow_html
            + f"<h1>{escape(title)}</h1>"
            + f"<p>{escape(body)}</p>"
            + "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_summary_grid(items):
    html = []
    for label, value in items:
        html.append(
            "<div class='summary-item'>"
            f"<div class='label'>{label}</div>"
            f"<div class='value'>{value}</div>"
            "</div>"
        )
    st.markdown("<div class='summary-grid'>" + "".join(html) + "</div>", unsafe_allow_html=True)


def render_study_briefing(replay_meta):
    briefing_html = inspect.cleandoc(
        """
        <div class='briefing-card'>
            <h1>Platform and study overview</h1>
           <p class='briefing-lead'>
            This study focuses on understanding and evaluating the experience of interacting with TestReco, an adaptive recommendation platform for Fundamental Mathematics multiple-choice questions. <br> 
            The goal of this study is to evaluate how the interaction is perceived from the learner’s perspective, based on a recorded session rather than direct participation. <br> 
            You will not complete the mathematics session yourself. Instead, you will observe one end-to-end recorded learner session, inspect the questions presented and the answers given, and then evaluate the experience from the learner’s perspective as if you had personally taken the test.<br>
            Please consider the effort that took the learner to solve the MCQs.
            </p>
            <h2 style='margin-top:0;'>How TestReco works</h2>
            <p>TestReco is designed to support learning through adaptive question recommendation. In the original study, the platform used the learner's performance to decide which mathematics questions should be proposed next.</p>
            <p>The recorded experience you will observe contains three main parts:</p>
            <ul>
                <li>A prequalification test with 12 mathematics questions.</li>
                <li>A 10-step adaptive learning phase where up to 5 recommended questions were shown at each step.</li>
                <li>A final questionnaire about effort, difficulty, controllability, frustration, trust, and perceived learning value.</li>
            </ul>
            <p>At each learning step, the system selects a recommendation strategy, proposes a batch of questions, records the learner's answers, and updates an estimate of the learner's mastery before moving to the next step.</p>
            <h2>What you will observe</h2>
            <p>During this simulation, you will carefully follow one complete recorded trajectory from beginning to end. You will be able to:</p>
            <ul>
                <li>inspect the prequalification stage that took place before adaptive learning began,</li>
                <li>review the recommended mathematics questions shown at each adaptive step,</li>
                <li>see which answers were chosen by the learner and whether they were correct,</li>
                <li>observe how the learning strategy and mastery estimate evolved over time.</li>
            </ul>
            <p>Your role is observational: Position yourself at the center of the experience, as if you were the learner yourself. Imagine that you went through the same process, made the same mistakes, faced the same difficulties, and experienced the same progression.</p>
            <h2>Exploration phase</h2>
            <p>Before the recorded replay starts, the app includes a short guided walkthrough. This phase is meant to help you understand how the original TestReco interface was structured, how the prequalification stage worked, and what to pay attention to during the adaptive learning phase.</p>
            <h2>Goal of the study</h2>
            <p>The goal of this study is to collect reliable qualitative feedback about the user experience of the adaptive learning process.</p>
            <p>At the end, you will answer the same qualitative survey from the learner's perspective, as if the observed session had been your own experience. Your final answers will help evaluate the perceived effort, mental demand, controllability, frustration, trust, and overall usefulness of the TestReco interaction flow.</p>
        </div>
        """
    )
    st.markdown(
        briefing_html,
        unsafe_allow_html=True,
    )

    if replay_meta is not None:
        render_summary_grid(
            [
                ("Assigned learner", replay_meta["actor_label"]),
                ("Replayed session", replay_meta["replay_session_id"]),
            ]
        )
    else:
        render_summary_grid(
            [
                ("Replay assignment", "After consent"),
                ("Public entry", "Use the Prolific link"),
            ]
        )

    render_callout(
        "Study flow",
        "You will first understand the structure of the TestReco study, then inspect the platform through a short guided walkthrough, then observe one recorded pretest and ten adaptive learning steps page by page, and finally complete the same post-study survey from the observed learner's perspective.",
        tone="accent",
    )

    if button_compat(st, "Continue to consent and setup", type="primary", use_container_width=True):
        st.session_state["phase"] = "consent"
        rerun_app()


def render_consent_page(catalog):
    render_hero(
        "Consent and data use",
        "The simulation app records only your navigation through the replay and the survey answers you submit at the end. It does not create a new adaptive learning run and it does not alter the original study database.",
        "",
    )

    st.markdown(
        """
        **During this simulation, the app may record:**
        - which replay session you were assigned,
        - which replay pages you reviewed,
        - your final survey answers,
        - optional free-text comments.
        """
    )

    st.markdown(
        """
        **Voluntary participation**
        - You may stop the simulation at any time.
        - The replay is read-only and based on stored study data.
        - You should answer the final survey as if the replayed learner journey had been your own.
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
        if button_compat(st, "Continue to Phase 1: guided exploration", type="primary", use_container_width=True):
            if agreed:
                participant_context = st.session_state.get("participant_context") or get_participant_context()
                replay_session_id, assignments_exhausted, assignment_error = assign_replay_for_participant(catalog, participant_context)
                if replay_session_id >= 0:
                    st.session_state["assigned_replay_session_id"] = replay_session_id
                    st.session_state["participant_assignment"] = get_participant_assignment(participant_context)
                    st.session_state["assignments_exhausted"] = False
                    st.session_state["consent_error"] = ""
                    st.session_state["_last_requested_replay_session_id"] = replay_session_id
                    reset_replay_state(clear_survey=True)
                    st.session_state["walkthrough_step"] = "pretest"
                    st.session_state["phase"] = "walkthrough"
                    rerun_app()
                    return
                st.session_state["assignments_exhausted"] = assignments_exhausted
                st.session_state["consent_error"] = assignment_error or "No replay session could be assigned for this participant."
                rerun_app()
                return
            st.session_state["consent_error"] = "Please confirm consent before continuing."
            rerun_app()
            return

    if st.session_state.get("consent_error"):
        render_callout("Consent required", st.session_state["consent_error"], tone="warn")
        st.session_state["consent_error"] = ""


def render_walkthrough_question(question, key_prefix, question_number):
    level_label = f"Level {question['difficulty_level']}" if safe_int(question.get("difficulty_level"), 0) > 0 else "Level ?"
    st.markdown(f"**Q{question_number}**  ({level_label})")
    render_mixed_content(question.get("question"))

    options = question.get("options") or []
    if not options:
        st.info("No options were recorded for this question.")
        return

    choice_key = f"{key_prefix}_choice_idx"
    if choice_key not in st.session_state:
        st.session_state[choice_key] = -1

    chosen_idx = safe_int(st.session_state.get(choice_key), -1)
    letters = [chr(ord("A") + idx) for idx in range(len(options))]
    supports_type = "type" in BUTTON_PARAMS

    for idx, option in enumerate(options):
        left_col, right_col = st.columns([1, 12])
        button_label = letters[idx] if supports_type or chosen_idx != idx else f"● {letters[idx]}"
        button_type = "primary" if chosen_idx == idx else "secondary"

        with left_col:
            if button_compat(
                left_col,
                button_label,
                key=f"{key_prefix}_pick_{idx}",
                type=button_type,
                use_container_width=True,
            ):
                st.session_state[choice_key] = idx
                rerun_app()

        with right_col:
            render_mixed_content(option)

    chosen_idx = safe_int(st.session_state.get(choice_key), -1)
    st.session_state[f"{key_prefix}_choice"] = options[chosen_idx] if 0 <= chosen_idx < len(options) else None

    if chosen_idx >= 0:
        st.caption(f"Selected answer: {letters[chosen_idx]}")

    correct_index = safe_int(question.get("correct_option_index"), question.get("correct_answer_index", -1))
    if st.checkbox("Show correct answer", key=f"{key_prefix}_show_answer"):
        if 0 <= correct_index < len(options):
            if chosen_idx >= 0:
                if chosen_idx == correct_index:
                    st.success("The selected answer matches the correct answer.")
                else:
                    st.info(f"The selected answer was {letters[chosen_idx]}. The correct answer is {letters[correct_index]}.")

            st.markdown(f"**Correct answer: {letters[correct_index]}.**")
            render_mixed_content(options[correct_index])
        else:
            st.info("The recorded correct option is unavailable.")


def render_walkthrough_page(replay_meta, replay_detail):
    walkthrough_step = st.session_state.get("walkthrough_step", "pretest")
    pretest_attempts = replay_detail.get("pretest_attempts") or []
    sample_step_question = None
    if replay_detail.get("steps") and replay_detail["steps"][0].get("questions"):
        sample_step_question = replay_detail["steps"][0]["questions"][0]

    if walkthrough_step == "pretest":
        render_hero(
            "Phase 1: guided exploration",
            "Review the full prequalification test interface first. After that, continue to a short explanation of how the adaptive learning phase was presented in the original study.",
            "",
            banner_override={
                "label": "",
                "title": "Learn the interface before observation begins",
                "description": "First review the full prequalification test. This is the same multiple-choice format participants saw before learning began and it contains 12 fixed questions across different difficulty levels. Then move to a short explanation of how the adaptive learning steps were presented.",
            },
        )
        render_summary_grid(
            [
                ("Pretest questions", replay_meta.get("pretest_num_questions", len(pretest_attempts) or PRETEST_QUESTIONS)),
                ("Topic", TOPIC_NAME),
                ("Assigned learner", replay_meta["actor_label"]),
                ("Next screen", "Learning phase overview"),
            ]
        )

        if pretest_attempts:
            for idx, attempt in enumerate(pretest_attempts, start=1):
                render_walkthrough_question(
                    attempt,
                    f"walkthrough_{replay_meta['replay_session_id']}_{idx}",
                    idx,
                )
                if idx < len(pretest_attempts):
                    st.markdown("---")
        else:
            st.info("No prequalification questions were available in the assigned replay.")

        nav_left, nav_right = st.columns([1, 1])
        with nav_left:
            if button_compat(st, "Back to consent and instructions", use_container_width=True):
                st.session_state["walkthrough_step"] = "pretest"
                st.session_state["phase"] = "consent"
                rerun_app()
        with nav_right:
            if button_compat(st, "Next: learning phase overview", type="primary", use_container_width=True):
                st.session_state["walkthrough_step"] = "learning"
                rerun_app()
        return

    learning_card_html = inspect.cleandoc(
        f"""
        <div class='briefing-card'>
            <h1>Learning phase</h1>
            <p class='briefing-lead'>After the prequalification test, the original study moved to the adaptive learning phase.</p>
            <p>Questions kept the same multiple-choice layout as the prequalification test, but the system adapted difficulty over time based on the learner's previous answers.</p>
            <p>In the original workflow:</p>
            <ul>
                <li>the learner could go through up to <strong>{replay_meta.get('total_steps_planned', TOTAL_STEPS)}</strong> steps,</li>
                <li>each step could contain up to <strong>{replay_meta.get('questions_per_step', QUESTIONS_PER_STEP)}</strong> questions,</li>
                <li>after a step, the learner could continue to the next one or finish and go to the final questionnaire.</li>
            </ul>
            <p>The replay will show the exact questions selected for each step, the learner's answers, the strategy chosen by the system, and the mastery update after each batch.</p>
        </div>
        """
    )
    st.markdown(learning_card_html, unsafe_allow_html=True)

    if sample_step_question is not None:
        st.markdown("### Example learning question")
        render_walkthrough_question(
            sample_step_question,
            f"walkthrough_learning_example_{replay_meta['replay_session_id']}",
            1,
        )

    nav_left, nav_right = st.columns([1, 1])
    with nav_left:
        if button_compat(st, "Back to the prequalification test", use_container_width=True):
            st.session_state["walkthrough_step"] = "pretest"
            rerun_app()
    with nav_right:
        if button_compat(st, "Recorded session replay", type="primary", use_container_width=True):
            st.session_state["walkthrough_step"] = "learning"
            reset_replay_state(clear_survey=True)
            st.session_state["phase"] = "replay"
            rerun_app()


def render_question_review(question_review, key_prefix):
    question_id = question_review.get("dataset_question_id") or f"Q{question_review['env_index']}"
    st.markdown(
        f"**Question {question_id}** | {question_review['subtopic']} | Level {question_review['difficulty_level']}"
    )
    render_mixed_content(question_review.get("question"))

    options = question_review.get("options") or []
    chosen_option_index = safe_int(question_review.get("chosen_option_index"), -1)
    correct_option_index = safe_int(question_review.get("correct_option_index"), -1)
    for option_idx, option in enumerate(options):
        letter = chr(ord("A") + option_idx)
        letter_col, option_col = st.columns([1, 18])
        with letter_col:
            st.markdown(f"**{letter}.**")
        with option_col:
            render_mixed_content(option)

    is_correct = question_review.get("is_correct")
    chosen_option_letter = chr(ord("A") + chosen_option_index) if 0 <= chosen_option_index < len(options) else None
    correct_option_letter = chr(ord("A") + correct_option_index) if 0 <= correct_option_index < len(options) else None
    if is_correct is True:
        if correct_option_letter is not None:
            outcome_text = f"The correct answer is {correct_option_letter}, and it was selected by the learner."
        else:
            outcome_text = "The learner answered this question correctly."
        render_callout("Recorded outcome", outcome_text, tone="good")
    elif is_correct is False:
        details = []
        if correct_option_letter is not None:
            details.append(f"The correct answer is {correct_option_letter}.")
        if chosen_option_letter is not None:
            details.append(f"The learner selected {chosen_option_letter}.")
        else:
            details.append("The learner's selected answer was not recorded.")
        render_callout("Recorded outcome", " ".join(details), tone="bad")
    else:
        details = []
        if correct_option_letter is not None:
            details.append(f"The correct answer is {correct_option_letter}.")
        if chosen_option_letter is not None:
            details.append(f"The learner selected {chosen_option_letter}.")
        details.append("The correctness of this answer was not recorded.")
        render_callout("Recorded outcome", " ".join(details), tone="subtle")


def render_pretest_page(replay_meta, replay_detail):
    render_hero(
        "Replay page 1: prequalification test",
        "This page summarizes the learner's prequalification stage. In the real study, completing and passing this test was required before the adaptive recommendation loop started.",
        "",
    )

    for idx, attempt in enumerate(replay_detail["pretest_attempts"], start=1):
        with st.expander(f"Prequalification question {idx}", expanded=idx == 1):
            render_question_review(attempt, f"pretest_{idx}")


def render_mastery_progress_chart(replay_detail, current_step_index=None):
    values = replay_detail.get("mastery_series") or []
    if not values:
        return
    chart_data = {"Estimated mastery": values}
    st.line_chart(chart_data)
    if current_step_index is None:
        st.caption("The line starts at prequalification mastery and then shows mastery after each adaptive step.")
    else:
        st.caption(f"Current replay page corresponds to learning step {current_step_index + 1}.")


def render_step_page(replay_meta, replay_detail, step_payload):
    step_index = step_payload["step_index"]
    render_hero(
        f"Replay page {step_index + 2}: learning step {step_index + 1}",
        "This page shows the recommendation strategy chosen for this step, the questions shown to the learner, the learner's answers, and the mastery update after those responses.",
        "",
    )

    for question_idx, question_review in enumerate(step_payload["questions"], start=1):
        st.markdown(f"### Recommended question {question_idx}")
        render_question_review(question_review, f"step_{step_index}_{question_idx}")


def replay_page_title(replay_detail, page_idx):
    page = replay_detail["pages"][page_idx]
    if page["kind"] == "pretest":
        return page["title"]
    return page["title"]


def resolve_replay_page_idx(replay_detail):
    total_pages = len(replay_detail["pages"])
    page_idx = max(0, min(safe_int(st.session_state.get("replay_page_idx"), 0), total_pages - 1))
    st.session_state["replay_page_idx"] = page_idx
    mark_replay_page_visited(page_idx)
    return page_idx, total_pages


def render_replay_navigation(replay_detail):
    page_idx, total_pages = resolve_replay_page_idx(replay_detail)

    visited = list(st.session_state.get("visited_replay_pages", []))
    st.markdown(
        (
            "<div class='section-card'>"
            "<div class='eyebrow'>Replay navigation</div>"
            f"<div class='replay-nav-title'>{replay_page_title(replay_detail, page_idx)}</div>"
            f"<div style='color:#475569;margin-top:0.35rem;'>Page {page_idx + 1} of {total_pages}. Review every page before opening the survey.</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    render_summary_grid(
        [
            ("Current page", f"{page_idx + 1} / {total_pages}"),
            ("Reviewed so far", f"{len(visited)} / {total_pages}"),
            ("Pretest", f"{safe_int(replay_detail['session'].get('pretest_correct'), 0)} / {safe_int(replay_detail['session'].get('pretest_num_questions'), PRETEST_QUESTIONS)}"),
            ("Adaptive steps", safe_int(replay_detail['session'].get('total_steps_planned'), TOTAL_STEPS)),
        ]
    )

    prev_disabled = page_idx <= 0
    next_disabled = page_idx >= total_pages - 1
    prev_col, next_col = st.columns(2)
    with prev_col:
        if button_compat(st, "Previous page", key="replay_prev_page", disabled=prev_disabled, use_container_width=True):
            st.session_state["replay_page_idx"] = page_idx - 1
            rerun_app()
        st.markdown(
            "<div class='rating-label'>Already at the first replay page.</div>" if prev_disabled else "<div class='rating-label'>Go back one page to review the previous stage.</div>",
            unsafe_allow_html=True,
        )
    with next_col:
        if button_compat(st, "Next page", key="replay_next_page", type="primary", disabled=next_disabled, use_container_width=True):
            st.session_state["replay_page_idx"] = page_idx + 1
            rerun_app()
        st.markdown(
            "<div class='rating-label'>This is the last replay page. The survey opens below once all pages are reviewed.</div>" if next_disabled else "<div class='rating-label'>Move forward one page in the recorded learner journey.</div>",
            unsafe_allow_html=True,
        )


def render_replay_page(replay_meta, replay_detail):
    page_idx, _ = resolve_replay_page_idx(replay_detail)
    current_page = replay_detail["pages"][page_idx]

    if current_page["kind"] == "pretest":
        render_pretest_page(replay_meta, replay_detail)
    else:
        step_payload = replay_detail["steps"][current_page["step_index"]]
        render_step_page(replay_meta, replay_detail, step_payload)

    render_replay_navigation(replay_detail)

    all_reviewed = len(st.session_state.get("visited_replay_pages", [])) == len(replay_detail["pages"])
    if all_reviewed:
        render_callout(
            "Replay complete",
            "You have reviewed the prequalification stage and all ten adaptive learning steps. You can now continue to the post-study survey.",
            tone="good",
        )
    else:
        render_callout(
            "Keep navigating",
            "Review the prequalification page and all ten learning-step pages before the survey opens. The sidebar page list and the previous/next buttons both navigate through the replay.",
            tone="warn",
        )

    action_left, action_right = st.columns([1, 1])
    with action_left:
        if button_compat(st, "Back to Phase 1 tutorial", use_container_width=True):
            st.session_state["walkthrough_step"] = "learning"
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
            "All replay pages are reviewed. You can continue to the questionnaire now."
            if all_reviewed
            else "The questionnaire unlocks after every replay page has been reviewed."
        )
        st.markdown(f"<div class='rating-label'>{survey_help}</div>", unsafe_allow_html=True)


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


def save_feedback(replay_meta):
    ensure_response_db()
    review_id = f"testreco_{replay_meta['replay_session_id']}_{uuid4().hex}"
    participant_context = st.session_state.get("participant_context") or get_participant_context()
    responses = {
        "q1": st.session_state.get("q1"),
        "q2": st.session_state.get("q2"),
        "q3": st.session_state.get("q3"),
        "q4": st.session_state.get("q4"),
        "q5": st.session_state.get("q5"),
        "q6": st.session_state.get("q6"),
        "q7": st.session_state.get("q7"),
        "would_use_again": bool(st.session_state.get("would_use_again")),
        "comments": st.session_state.get("survey_comments", "").strip() or None,
    }
    duration_seconds = max(0.0, time.time() - float(st.session_state.get("simulation_started_at", time.time())))

    with sqlite3.connect(SURVEY_DB_FILE) as connection:
        connection.row_factory = sqlite3.Row
        existing_assignment = connection.execute(
            "SELECT completed_review_id FROM participant_assignments WHERE participant_key = ?",
            (participant_context.get("participant_key"),),
        ).fetchone()
        if existing_assignment is not None and existing_assignment["completed_review_id"]:
            return str(existing_assignment["completed_review_id"])

        connection.execute(
            """
            INSERT INTO simulation_feedback (
                review_id,
                replay_session_id,
                source_session_id,
                source_user_id,
                actor_type,
                model_name,
                prompt_type,
                reviewed_pages_json,
                q1_accomplishment,
                q2_effort_required,
                q3_mental_demand,
                q4_perceived_controllability,
                q5_temporal_demand,
                q6_frustration,
                q7_trust,
                would_use_again,
                comments,
                duration_seconds,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                replay_meta["replay_session_id"],
                replay_meta["source_session_id"],
                replay_meta.get("source_user_id"),
                replay_meta["actor_type"],
                replay_meta.get("model_name"),
                replay_meta.get("prompt_type"),
                json.dumps(st.session_state.get("visited_replay_pages", [])),
                responses["q1"],
                responses["q2"],
                responses["q3"],
                responses["q4"],
                responses["q5"],
                responses["q6"],
                responses["q7"],
                1 if responses["would_use_again"] else 0,
                responses["comments"],
                duration_seconds,
                now_iso(),
            ),
        )
        connection.execute(
            """
            UPDATE participant_assignments
            SET completed_review_id = ?, completed_at = ?
            WHERE participant_key = ?
            """,
            (
                review_id,
                now_iso(),
                participant_context.get("participant_key"),
            ),
        )
        connection.commit()

    return review_id


def render_survey_page(replay_meta):
    render_hero(
        "Phase 3: post-study survey",
        "Answer the following questions as if the replayed learning journey had been your own use of TestReco. Consider the prequalification stage, the recommended questions across the ten learning steps, the overall effort required, and how much you would trust the system in a real study session.",
        "",
    )

    q1 = render_rating_buttons(
        "(Q1) Feeling of Accomplishment: How much do you feel that the recommended questions helped you improve your understanding of the topic?",
        "q1",
    )
    q2 = render_rating_buttons(
        "(Q2) Effort Required: How much effort was required to follow the recommendations and work through the questions?",
        "q2",
    )
    q3 = render_rating_buttons(
        "(Q3) Mental Demand: How mentally demanding was it to understand and solve the recommended questions?",
        "q3",
    )
    q4 = render_rating_buttons(
        "(Q4) Perceived Controllability: How well did the difficulty of the recommended questions match your current level throughout the session?",
        "q4",
    )
    q5 = render_rating_buttons(
        "(Q5) Temporal Demand: How time-pressured did you feel while completing the recommended questions within the allotted time?",
        "q5",
    )
    q6 = render_rating_buttons(
        "(Q6) Frustration: How frustrated did you feel while working with the recommended questions?",
        "q6",
    )
    q7 = render_rating_buttons(
        "(Q7) Trust: How much did you trust the system to recommend appropriate questions for your learning progress?",
        "q7",
    )

    st.checkbox("I would use such a system again.", key="would_use_again")
    st.text_area(
        "Any comments?",
        key="survey_comments",
        height=140,
        placeholder="Write anything you noticed about the replay, recommendation logic, or overall learning experience.",
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
                review_id = save_feedback(replay_meta)
                st.session_state["saved_review_id"] = review_id
                st.session_state["save_error"] = ""
                st.session_state["survey_error"] = ""
                st.session_state["phase"] = "done"
                rerun_app()
            except Exception as exc:
                st.session_state["save_error"] = str(exc)
                rerun_app()


def render_done_page(replay_meta):
    st.title("Study complete")
    st.markdown(
        "Thank you for completing the TestReco guided simulation. Your response has been recorded."
    )
    completion_url = get_prolific_completion_url()
    if completion_url:
        st.markdown(
            "You will now be returned to Prolific to complete your submission. If the redirect does not happen automatically, use the button below."
        )
        st.link_button("Return to Prolific", completion_url, use_container_width=True)
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
        st.markdown("This study session is finished. Please close this page.")


def main():
    st.set_page_config(
        page_title="TestReco guided simulation",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    render_styles()
    ensure_response_db()
    access_code = load_access_code()
    if access_code and not render_access_gate(access_code):
        return

    catalog = load_replay_catalog()
    if not catalog:
        render_callout(
            "No replay data found",
            f"The app could not load any replay sessions from {RESULTS_DB_FILE.name}.",
            tone="warn",
        )
        return

    initialize_state(catalog)
    phase = st.session_state.get("phase", "landing")
    assigned_replay_session_id = safe_int(st.session_state.get("assigned_replay_session_id"), -1)
    replay_meta = get_replay_meta(catalog, assigned_replay_session_id) if assigned_replay_session_id >= 0 else None

    if st.session_state.get("assignments_exhausted") and replay_meta is None:
        render_callout(
            "Study closed",
            "All available replay sessions have already been assigned. This testing link is no longer accepting new participants.",
            tone="warn",
        )
        return

    replay_detail = None
    if replay_meta is not None:
        replay_detail = load_replay_detail(replay_meta["replay_session_id"])
        if replay_detail is None:
            render_callout(
                "Replay details unavailable",
                f"Replay session {replay_meta['replay_session_id']} could not be loaded from {RESULTS_DB_FILE.name}.",
                tone="warn",
            )
            return
    elif phase not in {"landing", "consent"}:
        render_callout(
            "Replay session unavailable",
            st.session_state.get("consent_error") or "Open this study from its Prolific link, or use replay_id / replay_session_id for a manual preview.",
            tone="warn",
        )
        return

    render_sidebar(catalog, replay_meta, replay_detail)

    if phase == "landing":
        render_study_briefing(replay_meta)
        return
    if phase == "consent":
        render_consent_page(catalog)
        return
    if phase == "walkthrough":
        render_walkthrough_page(replay_meta, replay_detail)
        return
    if phase == "replay":
        render_replay_page(replay_meta, replay_detail)
        return
    if phase == "survey":
        render_survey_page(replay_meta)
        return
    render_done_page(replay_meta)


if __name__ == "__main__":
    main()
