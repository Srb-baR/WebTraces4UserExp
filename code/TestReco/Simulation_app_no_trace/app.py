"""
Simulation_app_no_trace/app.py
================================
TestReco Description-Only Evaluation Study

This Streamlit app implements the "no-trace" condition for the TestReco study.
Participants see only a description of the TestReco system and task (no learner-specific
interaction data), imagine completing the experience themselves, then answer the same
post-study survey questions.

This condition serves as a baseline to measure: Do interaction traces provide information
about subjective experience beyond what someone can infer from the task and system
description alone?

Usage:
    streamlit run app.py
"""

import hmac
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent / ".env")


ROOT = Path(__file__).parent
SURVEY_DB_FILE = Path(os.environ.get("REVIEWER_OUTPUT_DIR", str(ROOT))) / "no_trace_survey_responses.db"
ACCESS_CODE_FILE = ROOT / ".tunnel" / "access_code.txt"

# Question files
QA_FILE = ROOT.parent / "benchmarks" / "MATHE" / "processed" / "QA.json"
DIFF_CSV_FILE = ROOT.parent / "benchmarks" / "MATHE" / "processed" / "mathe_QA_scaled_difficulty.csv"

# Check if files exist locally first
if not QA_FILE.exists():
    QA_FILE = ROOT / "QA.json"
if not DIFF_CSV_FILE.exists():
    DIFF_CSV_FILE = ROOT / "mathe_QA_scaled_difficulty.csv"

PROLIFIC_PID_QUERY_PARAMS = ("PROLIFIC_PID", "prolific_pid", "participant_id", "participantId")
STUDY_ID_QUERY_PARAMS = ("STUDY_ID", "study_id")
SESSION_ID_QUERY_PARAMS = ("SESSION_ID", "session_id")
PROLIFIC_COMPLETION_CODE_QUERY_PARAMS = ("PROLIFIC_COMPLETION_CODE", "completion_code", "cc")

TOPIC_NAME = "Fundamental Mathematics"
QUESTIONS_PER_STEP = 5
TOTAL_STEPS = 10
PRETEST_QUESTIONS = 12

# Fixed pretest question IDs from the actual study
PRETEST_QUESTION_IDS = ["102", "104", "1256", "1550", "1551", "2062", "2064", "456", "458", "497", "505", "510"]

PHASES = ["landing", "consent", "exploration", "survey", "done"]
PHASE_LABELS = {
    "landing": "Study briefing",
    "consent": "Consent",
    "exploration": "Explore TestReco",
    "survey": "Post-study survey",
    "done": "Finish",
}


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
    return str(os.getenv("PROLIFIC_COMPLETION_CODE", "") or "").strip()


def get_prolific_completion_url():
    participant_context = st.session_state.get("participant_context") or get_participant_context()
    if participant_context.get("assignment_source") != "prolific":
        return None
    completion_code = get_prolific_completion_code()
    if not completion_code:
        return None
    return f"https://app.prolific.com/submissions/complete?cc={completion_code}"


def get_participant_context():
    prolific_pid = get_first_query_param(PROLIFIC_PID_QUERY_PARAMS, "")
    study_id = get_first_query_param(STUDY_ID_QUERY_PARAMS, "")
    session_id = get_first_query_param(SESSION_ID_QUERY_PARAMS, "")

    if os.getenv("PROLIFIC_REQUIRED", "1") != "0":
        if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value or "") for value in (prolific_pid, study_id, session_id)):
            raise ValueError("Open this study from Prolific. Participant, study and session IDs must be supplied in the URL.")
        if not re.fullmatch(r"[A-Za-z0-9]+", get_prolific_completion_code()):
            raise ValueError("The researcher must configure PROLIFIC_COMPLETION_CODE before launching this study.")
    if prolific_pid:
        return {
            "assignment_source": "prolific",
            "participant_key": prolific_pid,
            "prolific_pid": prolific_pid,
            "study_id": study_id,
            "session_id": session_id,
        }

    fallback_key = st.session_state.get("fallback_participant_key")
    if not fallback_key:
        fallback_key = f"local_{uuid4().hex[:12]}"
        st.session_state["fallback_participant_key"] = fallback_key

    return {
        "assignment_source": "local",
        "participant_key": fallback_key,
        "prolific_pid": None,
        "study_id": None,
        "session_id": None,
    }


def ensure_response_db():
    if SURVEY_DB_FILE.exists():
        return

    with sqlite3.connect(SURVEY_DB_FILE) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS survey_responses (
                review_id TEXT PRIMARY KEY,
                participant_key TEXT,
                prolific_pid TEXT,
                study_id TEXT,
                session_id TEXT,
                assignment_source TEXT,
                q1_accomplishment INTEGER,
                q2_effort_required INTEGER,
                q3_mental_demand INTEGER,
                q4_controllability INTEGER,
                q5_temporal_demand INTEGER,
                q6_frustration INTEGER,
                q7_trust INTEGER,
                would_use_again INTEGER,
                comments TEXT,
                duration_seconds REAL,
                time_landing REAL,
                time_consent REAL,
                time_exploration_intro REAL,
                time_exploration_pretest REAL,
                time_exploration_step1 REAL,
                time_exploration_step2 REAL,
                time_exploration_step3 REAL,
                time_exploration_step4 REAL,
                time_exploration_step5 REAL,
                time_exploration_step6 REAL,
                time_exploration_step7 REAL,
                time_exploration_step8 REAL,
                time_exploration_step9 REAL,
                time_exploration_step10 REAL,
                time_survey REAL,
                total_steps_viewed INTEGER,
                skipped_to_survey INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        # Create interaction events table for detailed tracking
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS interaction_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_key TEXT,
                session_timestamp TEXT,
                event_type TEXT,
                event_action TEXT,
                event_details TEXT,
                page_phase TEXT,
                exploration_stage TEXT,
                current_step INTEGER,
                timestamp_utc TEXT,
                time_since_start REAL
            )
            """
        )

        # Create page views table for time spent on each page
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS page_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_key TEXT,
                session_timestamp TEXT,
                phase TEXT,
                exploration_stage TEXT,
                step_number INTEGER,
                view_started_at TEXT,
                view_ended_at TEXT,
                duration_seconds REAL,
                questions_visible INTEGER
            )
            """
        )

        connection.commit()


def log_interaction_event(event_type, event_action, event_details="", phase="", exploration_stage="", current_step=None):
    """Log a user interaction event to the database."""
    ensure_response_db()

    participant_context = st.session_state.get("participant_context") or get_participant_context()
    session_start = st.session_state.get("study_started_at", time.time())
    time_since_start = time.time() - session_start

    # Create session identifier
    session_timestamp = st.session_state.get("session_timestamp")
    if not session_timestamp:
        session_timestamp = datetime.now(timezone.utc).isoformat()
        st.session_state["session_timestamp"] = session_timestamp

    try:
        with sqlite3.connect(SURVEY_DB_FILE) as connection:
            connection.execute(
                """
                INSERT INTO interaction_events (
                    participant_key, session_timestamp, event_type, event_action,
                    event_details, page_phase, exploration_stage, current_step,
                    timestamp_utc, time_since_start
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    participant_context.get("participant_key"),
                    session_timestamp,
                    event_type,
                    event_action,
                    event_details,
                    phase,
                    exploration_stage,
                    current_step,
                    datetime.now(timezone.utc).isoformat(),
                    time_since_start
                )
            )
            connection.commit()
    except Exception as e:
        # Don't break the app if logging fails
        pass


def log_page_view(phase, exploration_stage="", step_number=None, questions_visible=0):
    """Log a page view with timing information."""
    ensure_response_db()

    participant_context = st.session_state.get("participant_context") or get_participant_context()
    session_timestamp = st.session_state.get("session_timestamp", datetime.now(timezone.utc).isoformat())

    # Track current page view start time
    page_key = f"page_view_{phase}_{exploration_stage}_{step_number}"

    # End previous page view if exists
    prev_page_key = st.session_state.get("current_page_key")
    if prev_page_key and prev_page_key != page_key:
        prev_start = st.session_state.get(f"{prev_page_key}_start")
        if prev_start:
            duration = time.time() - prev_start
            prev_phase = st.session_state.get(f"{prev_page_key}_phase")
            prev_stage = st.session_state.get(f"{prev_page_key}_stage", "")
            prev_step = st.session_state.get(f"{prev_page_key}_step")
            prev_questions = st.session_state.get(f"{prev_page_key}_questions", 0)

            try:
                with sqlite3.connect(SURVEY_DB_FILE) as connection:
                    connection.execute(
                        """
                        INSERT INTO page_views (
                            participant_key, session_timestamp, phase, exploration_stage,
                            step_number, view_started_at, view_ended_at, duration_seconds,
                            questions_visible
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            participant_context.get("participant_key"),
                            session_timestamp,
                            prev_phase,
                            prev_stage,
                            prev_step,
                            datetime.fromtimestamp(prev_start, timezone.utc).isoformat(),
                            datetime.now(timezone.utc).isoformat(),
                            duration,
                            prev_questions
                        )
                    )
                    connection.commit()
            except Exception as e:
                pass

    # Start tracking current page view
    st.session_state["current_page_key"] = page_key
    st.session_state[f"{page_key}_start"] = time.time()
    st.session_state[f"{page_key}_phase"] = phase
    st.session_state[f"{page_key}_stage"] = exploration_stage
    st.session_state[f"{page_key}_step"] = step_number
    st.session_state[f"{page_key}_questions"] = questions_visible


def load_access_code():
    if not ACCESS_CODE_FILE.exists():
        return None
    return ACCESS_CODE_FILE.read_text().strip()


def render_access_gate(expected_code):
    if st.session_state.get("access_granted"):
        return True

    st.title("Access required")
    st.markdown("This study app is password-protected. Enter the access code to continue.")

    entered_code = st.text_input("Access code", type="password", key="access_code_input")

    if safe_button("Submit", type="primary"):
        if hmac.compare_digest(entered_code, expected_code):
            st.session_state["access_granted"] = True
            rerun_app()
        else:
            st.error("Incorrect access code. Please try again.")

    return False


def initialize_state():
    st.session_state.setdefault("phase", "landing")
    st.session_state.setdefault("study_started_at", time.time())
    st.session_state.setdefault("survey_error", "")
    st.session_state.setdefault("save_error", "")
    st.session_state.setdefault("survey_comments", "")
    st.session_state["participant_context"] = get_participant_context()

    # Initialize timing tracking
    st.session_state.setdefault("phase_timings", {})
    st.session_state.setdefault("current_phase_start", time.time())

    for i in range(1, 8):
        st.session_state.setdefault(f"q{i}", None)


def track_phase_time(phase_name):
    """Track time spent on current phase before moving to next."""
    if "current_phase_start" in st.session_state:
        elapsed = time.time() - st.session_state["current_phase_start"]
        st.session_state["phase_timings"][phase_name] = elapsed
        st.session_state["current_phase_start"] = time.time()


def rerun_app():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def safe_button(label, **kwargs):
    """Button wrapper for compatibility with older Streamlit versions."""
    # Base parameters supported in Streamlit 1.8+
    supported_params = {"key", "help", "on_click", "args", "kwargs"}

    # Check Streamlit version capabilities
    # In Streamlit >= 1.12, type and use_container_width are supported
    if hasattr(st, "cache_data"):  # Proxy for Streamlit >= 1.13
        supported_params.update({"type", "use_container_width", "disabled"})
    elif hasattr(st, "experimental_memo"):  # Streamlit >= 1.8, < 1.13
        supported_params.add("disabled")

    filtered_kwargs = {k: v for k, v in kwargs.items() if k in supported_params}
    return st.button(label, **filtered_kwargs)


def render_styles():
    st.markdown(
        """
        <style>
        /* Compact text */
        .stMarkdown {
            font-size: 1.05rem;
            line-height: 1.4;
            margin-bottom: 0.5rem;
        }

        /* Compact headers */
        h1 {
            font-size: 2rem !important;
            margin-bottom: 0.5rem !important;
            margin-top: 0.5rem !important;
        }

        h2 {
            font-size: 1.6rem !important;
            margin-top: 1rem !important;
            margin-bottom: 0.5rem !important;
        }

        h3 {
            font-size: 1.3rem !important;
            margin-top: 0.8rem !important;
            margin-bottom: 0.4rem !important;
        }

        /* Compact emphasis */
        .callout {
            padding: 0.8rem;
            margin: 0.8rem 0;
            border-left: 4px solid #2196f3;
            background: transparent;
            font-size: 1.05rem;
            font-weight: 500;
        }

        .callout-accent {
            border-left-color: #2196f3;
            color: #1976d2;
        }

        .callout-warn {
            border-left-color: #ffc107;
            color: #f57c00;
        }

        /* Rating labels */
        .rating-label {
            font-size: 0.95rem;
            color: #666;
            margin-top: 0.3rem;
            font-style: italic;
        }

        /* Questions styling */
        .stMarkdown strong {
            font-size: 1.05rem;
        }

        /* Reduce paragraph spacing */
        p {
            margin-bottom: 0.5rem !important;
        }

        /* Reduce list spacing */
        ul, ol {
            margin-top: 0.3rem !important;
            margin-bottom: 0.5rem !important;
        }

        li {
            margin-bottom: 0.2rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_callout(title, message, tone="accent"):
    """Render an emphasis callout with simple border - no box background."""
    st.markdown(
        f"""
        <div class='callout callout-{tone}'>
            <strong>{title}:</strong> {message}
        </div>
        """,
        unsafe_allow_html=True,
    )


def load_questions():
    """Load sample questions from QA.json file."""
    # Use session state as cache instead of @st.cache to avoid pickling issues
    if "all_questions_cache" in st.session_state:
        return st.session_state["all_questions_cache"]

    if not QA_FILE.exists():
        return []

    with open(QA_FILE, "r") as f:
        data = json.load(f)

    # QA.json has structure: {"questions": [...]}
    if isinstance(data, dict) and "questions" in data:
        questions = data["questions"]
    elif isinstance(data, list):
        questions = data
    else:
        questions = []

    # Cache in session state
    st.session_state["all_questions_cache"] = questions
    return questions


def wrap_math_for_markdown(text):
    """Convert LaTeX math delimiters for Streamlit display."""
    if not text:
        return ""
    # Convert \( \) to $ $
    text = re.sub(r'\\\((.+?)\\\)', r'$\1$', text)
    # Convert \[ \] to $$ $$
    text = re.sub(r'\\\[(.+?)\\\]', r'$$\1$$', text)
    return text


def render_demo_question(question, question_num):
    """Render a question in demo mode (no answering, just observation)."""
    st.markdown(f"### Question {question_num}")

    # Display question text
    question_text = question.get("question", "")
    if question_text:
        st.markdown(wrap_math_for_markdown(question_text))

    # Display options
    options = question.get("options", [])
    if options:
        st.markdown("**Answer choices:**")
        for i, option in enumerate(options):
            letter = chr(ord('A') + i)
            st.markdown(f"**{letter}.** {wrap_math_for_markdown(option)}")

    st.markdown("---")


def get_demo_questions(num_questions, difficulty_range=None, use_pretest_ids=False):
    """Get sample questions for demo mode."""
    all_questions = load_questions()

    if not all_questions:
        return []

    # For pretest, use the fixed question IDs from the actual study
    if use_pretest_ids:
        pretest_questions = []
        for q in all_questions:
            if q.get("id") in PRETEST_QUESTION_IDS:
                pretest_questions.append(q)
        # Sort by the order in PRETEST_QUESTION_IDS
        sorted_questions = []
        for qid in PRETEST_QUESTION_IDS:
            for q in pretest_questions:
                if q.get("id") == qid:
                    sorted_questions.append(q)
                    break
        return sorted_questions

    # Sample random questions for learning steps
    import random
    num_to_sample = min(num_questions, len(all_questions))

    if num_to_sample > 0:
        sampled = random.sample(all_questions, num_to_sample)
    else:
        sampled = []

    return sampled


def update_single_choice_checkbox(state_key, selected_option, all_options):
    for option in all_options:
        if option != selected_option:
            st.session_state[f"{state_key}_{option}_choice"] = False
    st.session_state[state_key] = selected_option


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


def render_landing_page():
    # Track page view
    log_page_view("landing")

    st.title("TestReco Experience Evaluation")

    st.markdown("""
    You will evaluate the experience of using TestReco, an adaptive mathematics learning system.

    **What you'll do:**
    - Explore the TestReco interface and see sample questions (without answering them)
    - Imagine completing the full learning experience yourself
    - Answer a questionnaire about your expected experience
    """)

    st.markdown("## About TestReco")

    st.markdown("""
    TestReco is an adaptive mathematics learning system with two stages:

    **1. Qualification Test:** 12 mathematics questions to estimate initial level

    **2. Adaptive Learning:** 10 steps with up to 5 questions each, adapting difficulty based on performance

    Total: 62 mathematics problems requiring sustained cognitive effort.
    """)

    st.markdown("## Your Task")

    st.markdown("""
    1. View sample questions (without answering)
    2. Imagine YOU personally completed the entire experience
    3. Rate: accomplishment, effort, mental demand, controllability, temporal demand, satisfaction

    **Note:** Don't assume you performed well or poorly. Base evaluation on the system shown.
    """)


    if safe_button("Continue to consent", type="primary", use_container_width=True):
        log_interaction_event("button_click", "continue_to_consent", phase="landing")
        track_phase_time("landing")
        st.session_state["phase"] = "consent"
        rerun_app()


def render_consent_page():
    # Track page view
    log_page_view("consent")

    st.title("Consent and data use")

    st.markdown(
        """
        This study collects your evaluation of the TestReco learning experience.
        Your participation is voluntary and your responses are recorded for research purposes.
        """
    )

    st.markdown(
        """
        **During this study, we will record:**
        - Your survey answers about the expected experience
        - Optional free-text comments
        - Time spent completing the study
        """
    )

    st.markdown(
        """
        **Voluntary participation**
        - You may stop at any time
        - You will explore the TestReco interface and see sample questions
        - You will NOT answer questions yourself - only observe them
        - Your final survey answers should reflect how you think you would experience using the system
        """
    )

    agreed = st.checkbox(
        "I consent to participate in this study and to the recording of my responses for research purposes.",
        value=False,
        key="consent_checkbox",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        if safe_button("Back to briefing", use_container_width=True):
            log_interaction_event("button_click", "back_to_landing", phase="consent")
            track_phase_time("consent")
            st.session_state["phase"] = "landing"
            rerun_app()
    with col2:
        if safe_button("Continue to study", type="primary", use_container_width=True, disabled=not agreed):
            if not agreed:
                st.error("Please confirm your consent to continue.")
            else:
                log_interaction_event("button_click", "consent_agreed", event_details="user_consented", phase="consent")
                track_phase_time("consent")
                st.session_state["phase"] = "exploration"
                rerun_app()


def render_exploration_page():
    """Interactive exploration of TestReco interface with sample questions."""

    # Initialize exploration state
    if "exploration_stage" not in st.session_state:
        st.session_state.exploration_stage = "intro"
    if "pretest_questions" not in st.session_state:
        st.session_state.pretest_questions = get_demo_questions(PRETEST_QUESTIONS, use_pretest_ids=True)
    if "learning_step_questions" not in st.session_state:
        # Generate questions for all 10 steps
        all_learning_q = get_demo_questions(QUESTIONS_PER_STEP * TOTAL_STEPS)
        st.session_state.learning_step_questions = {}
        for step in range(1, TOTAL_STEPS + 1):
            start_idx = (step - 1) * QUESTIONS_PER_STEP
            end_idx = step * QUESTIONS_PER_STEP
            st.session_state.learning_step_questions[step] = all_learning_q[start_idx:end_idx]
    if "current_learning_step" not in st.session_state:
        st.session_state.current_learning_step = 1

    stage = st.session_state.exploration_stage

    # Intro stage
    if stage == "intro":
        log_page_view("exploration", exploration_stage="intro")

        st.title("Explore TestReco")

        st.markdown("""
        **Qualification Test:** 12 EXACT questions all learners see (fixed)

        **Adaptive Learning:** Sample questions (vary by learner based on performance)

        You'll observe questions without answering them, then imagine completing the experience yourself.
        """)

        st.info("**Observation Mode**: View questions without answering. Imagine completing them yourself.")

        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            if safe_button("Back to consent", use_container_width=True):
                log_interaction_event("button_click", "back_to_consent", phase="exploration", exploration_stage="intro")
                track_phase_time("exploration_intro")
                st.session_state["phase"] = "consent"
                rerun_app()
        with col2:
            if safe_button("View Qualification Test", type="primary", use_container_width=True):
                log_interaction_event("button_click", "view_pretest", phase="exploration", exploration_stage="intro")
                track_phase_time("exploration_intro")
                st.session_state.exploration_stage = "pretest"
                rerun_app()
        with col3:
            if safe_button("Skip to Survey", use_container_width=True):
                log_interaction_event("button_click", "skip_to_survey", event_details="from_intro", phase="exploration", exploration_stage="intro")
                track_phase_time("exploration_intro")
                st.session_state["skipped_exploration"] = True
                st.session_state["phase"] = "survey"
                rerun_app()

    # Pretest exploration
    elif stage == "pretest":
        num_pretest_qs = len(st.session_state.pretest_questions)
        log_page_view("exploration", exploration_stage="pretest", questions_visible=num_pretest_qs)

        st.title("Qualification Test - All 12 Questions")

        st.markdown("""
        **Fixed Questions:** All learners answer these same 12 questions to establish initial level. Scroll to view all.
        """)

        st.markdown("---")

        # Show ALL pretest questions
        if st.session_state.pretest_questions:
            st.markdown(f"### All {len(st.session_state.pretest_questions)} Qualification Questions")

            # Display all questions
            for idx, q in enumerate(st.session_state.pretest_questions, 1):
                render_demo_question(q, idx)
        else:
            st.info("Questions are being loaded. If this persists, the question database may not be available.")

        st.markdown("---")


        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            if safe_button("Back", use_container_width=True):
                log_interaction_event("button_click", "back_to_intro", phase="exploration", exploration_stage="pretest")
                track_phase_time("exploration_pretest")
                st.session_state.exploration_stage = "intro"
                rerun_app()
        with col2:
            if safe_button("View Adaptive Learning", type="primary", use_container_width=True):
                log_interaction_event("button_click", "view_adaptive_learning", phase="exploration", exploration_stage="pretest")
                track_phase_time("exploration_pretest")
                st.session_state.current_learning_step = 1
                st.session_state.exploration_stage = "learning"
                rerun_app()
        with col3:
            if safe_button("Skip to Survey", use_container_width=True):
                log_interaction_event("button_click", "skip_to_survey", event_details="from_pretest", phase="exploration", exploration_stage="pretest")
                track_phase_time("exploration_pretest")
                st.session_state["skipped_exploration"] = True
                st.session_state["phase"] = "survey"
                rerun_app()

    # Learning exploration with step navigation
    elif stage == "learning":
        current_step = st.session_state.current_learning_step

        # Track viewed steps
        if "viewed_steps" not in st.session_state:
            st.session_state["viewed_steps"] = set()
        st.session_state["viewed_steps"].add(current_step)

        step_questions = st.session_state.learning_step_questions.get(current_step, [])
        log_page_view("exploration", exploration_stage="learning", step_number=current_step, questions_visible=len(step_questions))

        st.title(f"Adaptive Learning Phase - Step {current_step} of {TOTAL_STEPS}")

        st.markdown("""
        **Sample Questions:** Each learner sees different adaptive questions based on performance. Navigate through all 10 steps.
        """)

        # Progress indicator
        st.progress(current_step / TOTAL_STEPS)
        st.markdown(f"**Progress:** Step {current_step} / {TOTAL_STEPS}")

        st.markdown("---")

        st.markdown(f"### Step {current_step} - Recommended Questions")

        # Show questions for current step
        step_questions = st.session_state.learning_step_questions.get(current_step, [])
        if step_questions:
            for idx, q in enumerate(step_questions, 1):
                render_demo_question(q, idx)
        else:
            st.info("Questions for this step are being loaded.")

        st.markdown("---")

        if current_step == 1:
            st.info("**Adaptive System**: After answering these questions, the system analyzes your performance and selects the next set of questions. Difficulty adjusts dynamically based on your answers.")
        elif current_step == TOTAL_STEPS:
            st.success("**Final Step Complete**: This is the last adaptive learning step. After this, learners would complete the post-study questionnaire about their experience.")

        # Navigation buttons
        col1, col2, col3, col4 = st.columns([1, 1, 1, 1])

        with col1:
            if current_step > 1:
                if safe_button("◀ Previous Step", use_container_width=True):
                    log_interaction_event("button_click", "previous_step", event_details=f"from_step_{current_step}_to_{current_step-1}",
                                         phase="exploration", exploration_stage="learning", current_step=current_step)
                    track_phase_time(f"exploration_step{current_step}")
                    st.session_state.current_learning_step -= 1
                    rerun_app()
            else:
                if safe_button("◀ Back to Pretest", use_container_width=True):
                    log_interaction_event("button_click", "back_to_pretest", event_details="from_step_1",
                                         phase="exploration", exploration_stage="learning", current_step=current_step)
                    track_phase_time(f"exploration_step{current_step}")
                    st.session_state.exploration_stage = "pretest"
                    rerun_app()

        with col2:
            if current_step < TOTAL_STEPS:
                if safe_button("Next Step ▶", type="primary", use_container_width=True):
                    log_interaction_event("button_click", "next_step", event_details=f"from_step_{current_step}_to_{current_step+1}",
                                         phase="exploration", exploration_stage="learning", current_step=current_step)
                    track_phase_time(f"exploration_step{current_step}")
                    st.session_state.current_learning_step += 1
                    rerun_app()

        with col3:
            # Jump to specific step
            jump_to_step = st.selectbox(
                "Jump to step:",
                range(1, TOTAL_STEPS + 1),
                index=current_step - 1,
                key="step_selector"
            )
            if jump_to_step != current_step:
                if safe_button("Go", use_container_width=True):
                    log_interaction_event("button_click", "jump_to_step", event_details=f"from_step_{current_step}_to_{jump_to_step}",
                                         phase="exploration", exploration_stage="learning", current_step=current_step)
                    track_phase_time(f"exploration_step{current_step}")
                    st.session_state.current_learning_step = jump_to_step
                    rerun_app()

        with col4:
            if safe_button("Skip to Survey", use_container_width=True):
                log_interaction_event("button_click", "skip_to_survey", event_details=f"from_step_{current_step}",
                                     phase="exploration", exploration_stage="learning", current_step=current_step)
                track_phase_time(f"exploration_step{current_step}")
                st.session_state["skipped_from_step"] = current_step
                st.session_state["phase"] = "survey"
                rerun_app()

        # Final summary after all steps
        if current_step == TOTAL_STEPS:
            st.markdown("---")
            st.markdown("### The Complete Learning Experience")
            st.markdown("""
            You have now seen samples from the complete TestReco learning session:
            - **12 qualification test questions** covering various mathematics topics
            - **10 adaptive learning steps** with 5 questions each (50 questions total)
            - **Dynamic difficulty adjustment** based on performance
            - **Sustained cognitive effort** throughout the session

            **Now imagine** that YOU personally completed this entire process. Consider:
            - The mental effort required to solve all these mathematics problems
            - The time commitment needed (answering 62 questions total)
            - How the adaptive system would feel to interact with
            - Your overall satisfaction with the experience
            """)

            st.info("**Ready for Survey**: Click 'Skip to Survey' above to evaluate how you think you would have experienced this complete learning process.")


def render_survey_page():
    log_page_view("survey")

    st.title("Experience Evaluation")

    st.markdown("---")
    st.markdown("""
    ## IMAGINE YOU ACTUALLY EXPERIENCED IT

    **IMAGINE that you PERSONALLY COMPLETED** the entire learning experience: answering all 12 qualification questions and working through all 10 adaptive learning steps, solving 62 mathematics problems total.

    **Answer as if you HAD personally done this yourself.**
    """)
    st.markdown("---")

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

    st.markdown("---")

    st.checkbox("I would use such a system again.", key="would_use_again")
    st.text_area(
        "Any comments?",
        key="survey_comments",
        height=140,
        placeholder="Write anything you noticed about the sample questions, the recommendation system, or overall learning experience.",
    )

    if st.session_state.get("save_error"):
        st.error(f"**Survey save failed:** {st.session_state['save_error']}")

    if st.session_state.get("survey_error"):
        st.warning(f"**Survey incomplete:** {st.session_state['survey_error']}")

    col1, col2 = st.columns([1, 1])
    with col1:
        if safe_button("Back to exploration", use_container_width=True):
            st.session_state["phase"] = "exploration"
            st.session_state.exploration_stage = "learning"
            rerun_app()
    with col2:
        if safe_button("Submit survey", type="primary", use_container_width=True):
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
                review_id = save_feedback()
                st.session_state["saved_review_id"] = review_id
                st.session_state["save_error"] = ""
                st.session_state["survey_error"] = ""
                st.session_state["phase"] = "done"
                rerun_app()
            except Exception as exc:
                st.session_state["save_error"] = str(exc)
                rerun_app()


def save_feedback():
    ensure_response_db()
    review_id = f"testreco_notrace_{uuid4().hex}"
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

    # Log survey submission event
    log_interaction_event("survey", "submit_survey", event_details="completed", phase="survey")

    # Track final survey time
    track_phase_time("survey")

    duration_seconds = max(0.0, time.time() - float(st.session_state.get("study_started_at", time.time())))
    now_iso = datetime.now(timezone.utc).isoformat()

    # Get phase timings
    phase_timings = st.session_state.get("phase_timings", {})

    # Calculate interaction metrics
    viewed_steps = st.session_state.get("viewed_steps", set())
    total_steps_viewed = len(viewed_steps)
    skipped_to_survey = 1 if st.session_state.get("skipped_exploration", False) else 0

    with sqlite3.connect(SURVEY_DB_FILE, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if participant_context.get("assignment_source") == "prolific":
            prior = connection.execute("SELECT review_id FROM survey_responses WHERE prolific_pid=? AND study_id=? AND session_id=? LIMIT 1", (participant_context["prolific_pid"], participant_context["study_id"], participant_context["session_id"])).fetchone()
            if prior:
                return prior[0]
        connection.execute(
            """
            INSERT INTO survey_responses (
                review_id,
                participant_key,
                prolific_pid,
                study_id,
                session_id,
                assignment_source,
                q1_accomplishment,
                q2_effort_required,
                q3_mental_demand,
                q4_controllability,
                q5_temporal_demand,
                q6_frustration,
                q7_trust,
                would_use_again,
                comments,
                duration_seconds,
                time_landing,
                time_consent,
                time_exploration_intro,
                time_exploration_pretest,
                time_exploration_step1,
                time_exploration_step2,
                time_exploration_step3,
                time_exploration_step4,
                time_exploration_step5,
                time_exploration_step6,
                time_exploration_step7,
                time_exploration_step8,
                time_exploration_step9,
                time_exploration_step10,
                time_survey,
                total_steps_viewed,
                skipped_to_survey,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                participant_context.get("participant_key"),
                participant_context.get("prolific_pid"),
                participant_context.get("study_id"),
                participant_context.get("session_id"),
                participant_context.get("assignment_source"),
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
                phase_timings.get("landing", 0.0),
                phase_timings.get("consent", 0.0),
                phase_timings.get("exploration_intro", 0.0),
                phase_timings.get("exploration_pretest", 0.0),
                phase_timings.get("exploration_step1", 0.0),
                phase_timings.get("exploration_step2", 0.0),
                phase_timings.get("exploration_step3", 0.0),
                phase_timings.get("exploration_step4", 0.0),
                phase_timings.get("exploration_step5", 0.0),
                phase_timings.get("exploration_step6", 0.0),
                phase_timings.get("exploration_step7", 0.0),
                phase_timings.get("exploration_step8", 0.0),
                phase_timings.get("exploration_step9", 0.0),
                phase_timings.get("exploration_step10", 0.0),
                phase_timings.get("survey", 0.0),
                total_steps_viewed,
                skipped_to_survey,
                now_iso,
                now_iso,
            ),
        )
        connection.commit()

    return review_id


def render_done_page():
    st.title("Study complete")
    st.markdown(
        "Thank you for completing the TestReco description-only evaluation. Your response has been recorded."
    )

    completion_url = get_prolific_completion_url()
    if completion_url:
        st.code(get_prolific_completion_code(), language=None)
        st.markdown(
            "You will now be returned to Prolific to complete your submission. If the redirect does not happen automatically, use the link below."
        )
        st.markdown(f"### [Click here to return to Prolific]({completion_url})")
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
            st.warning("**Prolific completion code required**: This submission was recorded, but no Prolific completion code is configured on the server yet. Set PROLIFIC_COMPLETION_CODE before launching Streamlit, then reopen the completion page to enable automatic return to Prolific.")
        st.markdown("This study session is finished. Please close this page.")


def main():
    st.set_page_config(
        page_title="TestReco Description-Only Evaluation",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    render_styles()
    ensure_response_db()

    access_code = load_access_code()
    if access_code and not render_access_gate(access_code):
        return

    try:
        context = get_participant_context()
    except ValueError as error:
        st.error(str(error))
        return
    old = st.session_state.get("participant_context")
    if old and old != context:
        st.session_state.clear()
    initialize_state()
    if context["assignment_source"] == "prolific":
        with sqlite3.connect(SURVEY_DB_FILE, timeout=30) as connection:
            prior = connection.execute("SELECT review_id FROM survey_responses WHERE prolific_pid=? AND study_id=? AND session_id=? LIMIT 1", (context["prolific_pid"], context["study_id"], context["session_id"])).fetchone()
        if prior:
            st.session_state["phase"] = "done"

    phase = st.session_state.get("phase", "landing")

    if phase == "landing":
        render_landing_page()
    elif phase == "consent":
        render_consent_page()
    elif phase == "exploration":
        render_exploration_page()
    elif phase == "survey":
        render_survey_page()
    else:
        render_done_page()


if __name__ == "__main__":
    main()
