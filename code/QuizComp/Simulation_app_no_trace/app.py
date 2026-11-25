"""
QuizComp No-Trace Evaluation App
==================================
This app implements the no-trace condition for QuizComp study.
Participants explore the actual QuizComp interface, generate sample quizzes,
and evaluate their expected experience without seeing specific learner data.

Usage:
    streamlit run app.py
"""

import csv
import hmac
import json
import os
import random
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
MCQ_DATA_FILE = ROOT / "math.csv"

# Try to find math.csv in multiple locations
if not MCQ_DATA_FILE.exists():
    MCQ_DATA_FILE = ROOT.parent / "Simulation_app" / "data" / "math.csv"
if not MCQ_DATA_FILE.exists():
    MCQ_DATA_FILE = ROOT.parent / "data" / "math.csv"

PROLIFIC_PID_QUERY_PARAMS = ("PROLIFIC_PID", "prolific_pid", "participant_id", "participantId")
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

PHASES = ["landing", "consent", "exploration", "example", "survey", "done"]

DISPLAY_MATH_PATTERN = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


def load_mcq_questions():
    """Load all MCQ questions from CSV file."""
    if "mcq_questions_cache" in st.session_state:
        return st.session_state["mcq_questions_cache"]

    if not MCQ_DATA_FILE.exists():
        st.session_state["mcq_questions_cache"] = []
        return []

    questions = []
    with open(MCQ_DATA_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            questions.append({
                "id": row.get("id", ""),
                "question": row.get("question", ""),
                "difficulty_level": int(row.get("difficulty_level", 1)),
                "correct_answer": row.get("correct_answer", ""),
                "answer2": row.get("answer2", ""),
                "answer3": row.get("answer3", ""),
                "answer4": row.get("answer4", ""),
                "topic_id": row.get("topic_id", ""),
                "topic_name": row.get("topic_name", ""),
            })

    st.session_state["mcq_questions_cache"] = questions
    return questions


def calculate_actual_distributions(questions):
    """Calculate actual topic and difficulty distributions from generated questions."""
    if not questions:
        return [0] * len(TOPIC_NAMES), [0] * len(DIFFICULTY_LABELS)

    # Count topics
    topic_counts = {name: 0 for name in TOPIC_NAMES}
    for q in questions:
        topic = q.get("topic_name", "")
        if topic in topic_counts:
            topic_counts[topic] += 1

    # Convert to percentages
    total = len(questions)
    topic_percentages = [int(topic_counts[name] / total * 100) if total > 0 else 0
                        for name in TOPIC_NAMES]

    # Count difficulties
    difficulty_counts = [0] * len(DIFFICULTY_LABELS)
    for q in questions:
        level = q.get("difficulty_level", 1)
        if 1 <= level <= len(DIFFICULTY_LABELS):
            difficulty_counts[level - 1] += 1

    # Convert to percentages
    difficulty_percentages = [int(count / total * 100) if total > 0 else 0
                             for count in difficulty_counts]

    return topic_percentages, difficulty_percentages


def calculate_target_match(requested_topics, actual_topics, requested_diffs, actual_diffs):
    """Calculate how well the quiz matches the requested parameters (0-1 score)."""
    total_diff = 0
    count = 0

    # Compare topics
    for req, act in zip(requested_topics, actual_topics):
        total_diff += abs(req - act)
        count += 1

    # Compare difficulties
    for req, act in zip(requested_diffs, actual_diffs):
        total_diff += abs(req - act)
        count += 1

    # Average absolute difference (0-100 scale)
    avg_diff = total_diff / count if count > 0 else 0

    # Convert to match score (1.0 = perfect match, 0.0 = worst match)
    # Max possible diff is 100 per dimension
    match_score = max(0, 1.0 - (avg_diff / 100))

    return match_score


def generate_sample_quiz(num_questions, topic_weights, difficulty_weights):
    """Generate a sample quiz based on parameters."""
    all_questions = load_mcq_questions()
    if not all_questions:
        return []

    # Map topic names to IDs
    topic_name_to_idx = {name: idx for idx, name in enumerate(TOPIC_NAMES)}

    # Filter questions by selected topics
    selected_topics = [TOPIC_NAMES[i] for i, w in enumerate(topic_weights) if w > 0]
    if selected_topics:
        available = [q for q in all_questions if q["topic_name"] in selected_topics]
    else:
        available = all_questions

    # Sample questions based on difficulty distribution
    selected_questions = []
    remaining = num_questions

    for difficulty_idx, weight in enumerate(difficulty_weights):
        if weight <= 0:
            continue

        difficulty_level = difficulty_idx + 1
        num_for_this_level = max(1, int(num_questions * weight / 100))

        level_questions = [q for q in available if q["difficulty_level"] == difficulty_level]
        if level_questions:
            sample_size = min(num_for_this_level, len(level_questions), remaining)
            selected_questions.extend(random.sample(level_questions, sample_size))
            remaining -= sample_size

    # Fill remaining with random questions
    while len(selected_questions) < num_questions and available:
        candidates = [q for q in available if q not in selected_questions]
        if not candidates:
            break
        selected_questions.append(random.choice(candidates))

    return selected_questions[:num_questions]


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
                q6_satisfaction INTEGER,
                q7_would_use_again INTEGER,
                comments TEXT,
                duration_seconds REAL,
                time_landing REAL,
                time_consent REAL,
                time_exploration REAL,
                time_survey REAL,
                num_quiz_generations INTEGER,
                skipped_to_survey INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

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
                timestamp_utc TEXT,
                time_since_start REAL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS page_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_key TEXT,
                session_timestamp TEXT,
                phase TEXT,
                view_started_at TEXT,
                view_ended_at TEXT,
                duration_seconds REAL
            )
            """
        )

        connection.commit()


def log_interaction_event(event_type, event_action, event_details="", phase=""):
    """Log a user interaction event."""
    ensure_response_db()

    participant_context = st.session_state.get("participant_context") or get_participant_context()
    session_start = st.session_state.get("study_started_at", time.time())
    time_since_start = time.time() - session_start

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
                    event_details, page_phase, timestamp_utc, time_since_start
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    participant_context.get("participant_key"),
                    session_timestamp,
                    event_type,
                    event_action,
                    event_details,
                    phase,
                    datetime.now(timezone.utc).isoformat(),
                    time_since_start
                )
            )
            connection.commit()
    except Exception:
        pass


def log_page_view(phase):
    """Log a page view with timing."""
    ensure_response_db()

    participant_context = st.session_state.get("participant_context") or get_participant_context()
    session_timestamp = st.session_state.get("session_timestamp", datetime.now(timezone.utc).isoformat())

    page_key = f"page_view_{phase}"
    prev_page_key = st.session_state.get("current_page_key")

    if prev_page_key and prev_page_key != page_key:
        prev_start = st.session_state.get(f"{prev_page_key}_start")
        if prev_start:
            duration = time.time() - prev_start
            prev_phase = st.session_state.get(f"{prev_page_key}_phase")

            try:
                with sqlite3.connect(SURVEY_DB_FILE) as connection:
                    connection.execute(
                        """
                        INSERT INTO page_views (
                            participant_key, session_timestamp, phase,
                            view_started_at, view_ended_at, duration_seconds
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            participant_context.get("participant_key"),
                            session_timestamp,
                            prev_phase,
                            datetime.fromtimestamp(prev_start, timezone.utc).isoformat(),
                            datetime.now(timezone.utc).isoformat(),
                            duration
                        )
                    )
                    connection.commit()
            except Exception:
                pass

    st.session_state["current_page_key"] = page_key
    st.session_state[f"{page_key}_start"] = time.time()
    st.session_state[f"{page_key}_phase"] = phase


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

    if st.button("Submit", type="primary"):
        if hmac.compare_digest(entered_code, expected_code):
            st.session_state["access_granted"] = True
            rerun_app()
        else:
            st.error("Incorrect access code. Please try again.")

    return False


def create_example_quiz_from_db():
    """Create a pre-loaded example from actual database results."""
    # Real example from database: session with 92.7% match score
    # Requested: 30% Differentiation, 70% Probability, 100% Very Easy (L1), 5 questions
    # Got: 20% Differentiation, 20% Complex Numbers, 60% Probability, mixed difficulties

    all_questions = load_mcq_questions()
    if not all_questions:
        return None

    # Get questions matching this example
    example_questions = []

    # 1 Differentiation L1
    diff_l1 = [q for q in all_questions if q["topic_name"] == "Differentiation" and q["difficulty_level"] == 1]
    if diff_l1:
        example_questions.append(random.choice(diff_l1))

    # 1 Complex Numbers L3
    complex_l3 = [q for q in all_questions if q["topic_name"] == "Complex Numbers" and q["difficulty_level"] == 3]
    if complex_l3:
        example_questions.append(random.choice(complex_l3))

    # 3 Probability (mixed levels: 2xL1, 1xL5)
    prob_l1 = [q for q in all_questions if q["topic_name"] == "Probability" and q["difficulty_level"] == 1]
    prob_l5 = [q for q in all_questions if q["topic_name"] == "Probability" and q["difficulty_level"] == 5]

    if len(prob_l1) >= 2:
        example_questions.extend(random.sample(prob_l1, 2))
    elif prob_l1:
        example_questions.append(random.choice(prob_l1))

    if prob_l5:
        example_questions.append(random.choice(prob_l5))

    # Fill up to 5 questions if needed
    while len(example_questions) < 5 and all_questions:
        remaining = [q for q in all_questions if q not in example_questions]
        if not remaining:
            break
        example_questions.append(random.choice(remaining))

    requested_topics = [30, 0, 0, 0, 0, 0, 70, 0, 0, 0]  # 30% Diff, 70% Prob
    requested_diffs = [100, 0, 0, 0, 0, 0]  # 100% L1
    actual_topics = [20, 0, 0, 20, 0, 0, 60, 0, 0, 0]  # 20% Diff, 20% Complex, 60% Prob
    actual_diffs = [60, 0, 20, 0, 20, 0]  # 60% L1, 20% L3, 20% L5

    return {
        "num_questions": 5,
        "requested_topic_weights": requested_topics,
        "requested_diff_weights": requested_diffs,
        "actual_topic_weights": actual_topics,
        "actual_diff_weights": actual_diffs,
        "target_match": 0.927,
        "questions": example_questions[:5],  # Ensure exactly 5
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "example",
        "is_example": True,
    }


def initialize_state():
    st.session_state.setdefault("phase", "landing")
    st.session_state.setdefault("study_started_at", time.time())
    st.session_state.setdefault("survey_error", "")
    st.session_state.setdefault("save_error", "")
    st.session_state.setdefault("survey_comments", "")
    st.session_state["participant_context"] = get_participant_context()
    st.session_state.setdefault("phase_timings", {})
    st.session_state.setdefault("current_phase_start", time.time())
    st.session_state.setdefault("num_mcqs", 10)

    # Initialize quiz history with example if first time in exploration
    if "quiz_history" not in st.session_state:
        try:
            example_quiz = create_example_quiz_from_db()
            st.session_state["quiz_history"] = [example_quiz] if example_quiz else []
        except Exception as e:
            # If example creation fails, start with empty history
            st.session_state["quiz_history"] = []

    # Initialize topic selections
    for idx in range(len(TOPIC_NAMES)):
        st.session_state.setdefault(f"topic_selected_{idx}", False)
        st.session_state.setdefault(f"topic_weight_{idx}", 0)

    # Initialize difficulty selections
    for idx in range(len(DIFFICULTY_LABELS)):
        st.session_state.setdefault(f"diff_selected_{idx}", False)
        st.session_state.setdefault(f"diff_weight_{idx}", 0)

    for i in range(1, 8):
        st.session_state.setdefault(f"q{i}", None)


def track_phase_time(phase_name):
    """Track time spent on current phase."""
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
    """Button wrapper for compatibility."""
    supported_params = {"key", "help", "on_click", "args", "kwargs"}

    if hasattr(st, "cache_data"):
        supported_params.update({"type", "use_container_width", "disabled"})
    elif hasattr(st, "experimental_memo"):
        supported_params.add("disabled")

    filtered_kwargs = {k: v for k, v in kwargs.items() if k in supported_params}
    return st.button(label, **filtered_kwargs)


def render_styles():
    st.markdown(
        """
        <style>
        .stMarkdown {
            font-size: 1.05rem;
            line-height: 1.4;
            margin-bottom: 0.5rem;
        }
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
        p {
            margin-bottom: 0.5rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
    label = f"Selected: {current} / 5" if current is not None else "Choose 1-5"
    st.markdown(f"<div style='font-size:0.9rem;color:#666;font-style:italic;'>{label}</div>", unsafe_allow_html=True)
    return current


def collect_distribution(labels, prefix):
    """Collect weights from checkboxes and sliders."""
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
    """Render a summary of the distribution."""
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


def normalize_math_text(text):
    """Normalize math text formatting."""
    normalized = str(text or "").strip()
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\\[", "$$").replace("\\]", "$$")
    normalized = normalized.replace("\\(", "$").replace("\\)", "$")
    return normalized


def render_mixed_content(text):
    """Render text with mixed LaTeX and regular content."""
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


def render_quiz_question(question, idx):
    """Render a single quiz question - simple version matching original."""
    if not question:
        return

    topic_name = question.get('topic_name', 'Unknown')
    difficulty = question.get('difficulty_level', '?')
    question_text = normalize_math_text(question.get('question', 'Question unavailable'))

    st.markdown(f"**Question {idx} | {topic_name} | Level {difficulty}**")
    render_mixed_content(question_text)

    # Render options
    options = [
        question.get('correct_answer', ''),
        question.get('answer2', ''),
        question.get('answer3', ''),
        question.get('answer4', '')
    ]
    options = [opt for opt in options if opt]

    for opt_idx, option in enumerate(options):
        if option:
            label = chr(ord("A") + opt_idx)
            option_text = normalize_math_text(option)
            if option_text.startswith("$$") or "\n" in option_text:
                st.markdown(f"**{label}.**")
                render_mixed_content(option_text)
            else:
                st.markdown(f"**{label}.** {option_text}")

    st.markdown("---")


def render_landing_page():
    log_page_view("landing")

    st.title("QuizComp Experience Evaluation")

    st.markdown("""
    You will evaluate your expected experience using QuizComp, an interactive AI-assisted quiz composition system.

    **What you'll do:**
    - Explore the actual QuizComp platform interface
    - Set parameters (topics, difficulty, number of questions)
    - Generate sample quizzes and see the outputs
    - Imagine YOU personally composed a quiz this way
    - Answer a questionnaire about your expected experience
    """)

    st.markdown("## About QuizComp")

    st.markdown("""
    QuizComp helps instructors compose quizzes by:

    **1. Set Parameters:** Choose quiz size, topic distribution, and difficulty distribution

    **2. Generate Quiz:** System creates a quiz matching your parameters

    **3. Iterate:** Accept the quiz OR generate a new one with adjusted parameters

    Users typically generate 2-5 quizzes before finding one they're satisfied with.
    """)

    st.markdown("## Your Task")

    st.markdown("""
    1. Interact with the QuizComp platform (set parameters, generate quizzes)
    2. Explore multiple quiz generations to understand the system
    3. Imagine YOU personally used this to compose a quiz for your course
    4. Rate: accomplishment, effort, mental demand, controllability, temporal demand, satisfaction

    **Note:** Don't assume your quiz was perfect or terrible. Base evaluation on the system you experience.
    """)

    if safe_button("Continue to consent", type="primary", use_container_width=True):
        log_interaction_event("button_click", "continue_to_consent", phase="landing")
        track_phase_time("landing")
        st.session_state["phase"] = "consent"
        rerun_app()


def render_consent_page():
    log_page_view("consent")

    st.title("Consent and data use")

    st.markdown("""
    This study collects your evaluation of the QuizComp quiz composition experience.
    """)

    st.markdown("""
    **We will record:**
    - Your survey answers about the expected experience
    - Your interactions with the platform (parameter choices, quiz generations)
    - Time spent on each part
    - Optional comments
    """)

    st.markdown("""
    **Voluntary participation:**
    - You may stop at any time
    - You'll interact with the actual QuizComp platform interface
    - Sample quizzes generated are for demonstration only
    - Your answers reflect how you think you would experience the system
    """)

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
        if safe_button("Continue to platform", type="primary", use_container_width=True, disabled=not agreed):
            if not agreed:
                st.error("Please confirm your consent to continue.")
            else:
                log_interaction_event("button_click", "consent_agreed", event_details="user_consented", phase="consent")
                track_phase_time("consent")
                st.session_state["phase"] = "exploration"
                rerun_app()


def render_exploration_page():
    log_page_view("exploration")

    st.title("QuizComp Platform - Explore the Interface")

    st.markdown("""
    Below is the actual QuizComp interface. Explore the controls to understand how users compose quizzes, then see a real example of a composition.
    """)

    st.markdown("---")

    # Left column: Interactive controls
    # Right column: Explanation
    col1, col2 = st.columns([1.15, 0.85])

    with col1:
        st.subheader("Configure your quiz")

        num_mcqs = st.slider(
            "How many questions should the quiz contain?",
            min_value=3,
            max_value=30,
            step=1,
            key="num_mcqs",
        )

        st.markdown("### Topic targets")
        topic_indices, topic_weights, topic_total = collect_distribution(TOPIC_NAMES, "topic")

        st.markdown("### Difficulty targets")
        diff_indices, diff_weights, diff_total = collect_distribution(DIFFICULTY_LABELS, "diff")

        # Validation info
        if not topic_indices:
            st.info("Choose at least one topic to proceed.")
        elif topic_total != 100:
            st.info(f"Topic sliders sum to {topic_total}%. Should reach 100% for composition.")

        if not diff_indices:
            st.info("Choose at least one difficulty level to proceed.")
        elif diff_total != 100:
            st.info(f"Difficulty sliders sum to {diff_total}%. Should reach 100% for composition.")

        can_generate = (topic_indices and topic_total == 100 and
                       diff_indices and diff_total == 100)

        safe_button("Generate my quiz (DISABLED)", type="primary", use_container_width=True, disabled=True)

        st.markdown("Generation is disabled. You are exploring the interface. See the example below for what users receive.")

    with col2:
        st.subheader("How to use this platform")
        st.markdown("""
        1. Set the number of questions
        2. Choose topic targets (must sum to 100%)
        3. Choose difficulty targets (must sum to 100%)
        4. Click Generate to create a quiz
        5. View the generated quiz
        6. Accept, Improve, or Adjust parameters
        """)

        st.markdown("### Your current configuration")
        st.markdown(f"**Quiz size:** {num_mcqs} questions")
        st.markdown("**Topic distribution:**")
        render_distribution_summary(TOPIC_NAMES, topic_weights)
        st.markdown("**Difficulty distribution:**")
        render_distribution_summary(DIFFICULTY_LABELS, diff_weights)

    st.markdown("---")

    # Navigation buttons
    col1, col2 = st.columns(2)
    with col1:
        if safe_button("Back to consent", use_container_width=True):
            st.session_state["phase"] = "consent"
            rerun_app()
    with col2:
        if safe_button("View Real Example", type="primary", use_container_width=True):
            log_interaction_event("button_click", "view_example", "from_exploration", "exploration")
            track_phase_time("exploration")
            st.session_state["phase"] = "example"
            rerun_app()


def render_example_page():
    """Show a real example of quiz composition."""
    log_page_view("example")

    st.title("Real User Composition Example")

    st.markdown("Below is an actual quiz composition from a real user session. This shows what the user requested vs what they received from the system.")

    st.markdown("---")

    # Load and display example quiz
    quiz_history = st.session_state.get("quiz_history", [])

    # Load example quiz on first visit
    if not quiz_history:
        try:
            example_quiz = create_example_quiz_from_db()
            if example_quiz and example_quiz.get("questions"):
                st.session_state["quiz_history"] = [example_quiz]
                quiz_history = [example_quiz]
        except Exception:
            pass

    if quiz_history and len(quiz_history) > 0:
        quiz = quiz_history[0]
        target_match = quiz.get('target_match', 0)
        match_color = "#22c55e" if target_match >= 0.85 else "#f59e0b" if target_match >= 0.70 else "#ef4444"

        # Show target match score
        st.markdown(
            f"""
            <div style='border:2px solid {match_color};border-radius:8px;padding:0.8rem;margin:0.8rem 0;'>
            <div style='font-size:0.9rem;font-weight:600;color:#64748b;'>Target Match Score</div>
            <div style='font-size:1.8rem;font-weight:800;color:{match_color};'>{int(target_match * 100)}%</div>
            <div style='font-size:0.85rem;color:#64748b;margin-top:0.3rem;'>How closely the quiz matched the requested parameters.</div>
            </div>
            """,
            unsafe_allow_html=True
        )

        # Show comparison in two columns
        col_req, col_got = st.columns(2)

        with col_req:
            st.markdown("**What User Requested:**")
            req_topics = quiz.get('requested_topic_weights', [])
            req_diffs = quiz.get('requested_diff_weights', [])

            if len(req_topics) < len(TOPIC_NAMES):
                req_topics = req_topics + [0] * (len(TOPIC_NAMES) - len(req_topics))
            if len(req_diffs) < len(DIFFICULTY_LABELS):
                req_diffs = req_diffs + [0] * (len(DIFFICULTY_LABELS) - len(req_diffs))

            st.markdown(f"Quiz size: {quiz.get('num_questions', 0)} questions")
            topics_str = ", ".join([f"{TOPIC_NAMES[i]} {w}%" for i, w in enumerate(req_topics) if w > 0])
            diffs_str = ", ".join([f"{DIFFICULTY_LABELS[i]} {w}%" for i, w in enumerate(req_diffs) if w > 0])
            st.markdown(f"Topics: {topics_str}")
            st.markdown(f"Difficulty: {diffs_str}")

        with col_got:
            st.markdown("**What User Actually Got:**")
            actual_topics = quiz.get('actual_topic_weights', [])
            actual_diffs = quiz.get('actual_diff_weights', [])

            if len(actual_topics) < len(TOPIC_NAMES):
                actual_topics = actual_topics + [0] * (len(TOPIC_NAMES) - len(actual_topics))
            if len(actual_diffs) < len(DIFFICULTY_LABELS):
                actual_diffs = actual_diffs + [0] * (len(DIFFICULTY_LABELS) - len(actual_diffs))

            st.markdown(f"Quiz size: {len(quiz.get('questions', []))} questions")
            actual_topics_str = ", ".join([f"{TOPIC_NAMES[i]} {w}%" for i, w in enumerate(actual_topics) if w > 0])
            actual_diffs_str = ", ".join([f"{DIFFICULTY_LABELS[i]} {w}%" for i, w in enumerate(actual_diffs) if w > 0])
            st.markdown(f"Topics: {actual_topics_str}")
            st.markdown(f"Difficulty: {actual_diffs_str}")

        st.markdown("")
        st.info("The system tried to match the request but added extra topics and difficulty levels. This is normal.")
        st.markdown("")

        # Show the actual quiz questions
        st.markdown("**Generated Quiz Questions:**")
        questions = quiz.get('questions', [])
        if questions:
            for q_idx, question in enumerate(questions, 1):
                if question:
                    render_quiz_question(question, q_idx)
        else:
            st.warning("No questions available for this quiz.")

        st.markdown("---")

        st.markdown("**User's Options After Seeing the Quiz:**")
        st.markdown("1. **Accept** - Satisfied with this quiz")
        st.markdown("2. **Improve** - Keep same parameters, ask system for better match")
        st.markdown("3. **Adjust parameters** - Change topics/difficulty and generate fresh")

    st.markdown("---")

    # Navigation buttons
    col1, col2 = st.columns(2)
    with col1:
        if safe_button("Back to Interface", use_container_width=True):
            st.session_state["phase"] = "exploration"
            rerun_app()
    with col2:
        if safe_button("Continue to Survey", type="primary", use_container_width=True):
            log_interaction_event("button_click", "go_to_survey", "from_example", "example")
            track_phase_time("example")
            st.session_state["phase"] = "survey"
            rerun_app()


def render_survey_page_OLD():  # Old disabled section starts here
    if False:
        pass  # Old code disabled

        # Show explanation before first quiz
        first_quiz = quiz_history[0] if quiz_history else None
        if first_quiz and first_quiz.get("is_example"):
            st.markdown("""
            <div class='callout' style='background:#dcfce7;border-left-color:#22c55e;'>
            <strong>📚 Example from Real Usage:</strong><br>
            Below is an <strong>actual example</strong> from a real QuizComp user's session.
            This shows what a user <strong>requested</strong> vs what they <strong>received</strong> from the system.
            <br><br>
            Users typically generate 2-5 quizzes before finding one they're satisfied with, using:
            <ul>
            <li><strong>Improve:</strong> Keep parameters, system finds better match</li>
            <li><strong>Adjust parameters:</strong> Change topics/difficulty and try again</li>
            <li><strong>Accept:</strong> Satisfied with current quiz</li>
            </ul>
            </div>
            """, unsafe_allow_html=True)

        if len(quiz_history) == 1 and not first_quiz.get("is_example"):
            st.markdown("""
            <div class='callout'>
            <strong>How QuizComp Works:</strong><br>
            The system tries to match your requested parameters, but may not always achieve a perfect match.
            You'll see below what you <strong>requested</strong> vs what you <strong>actually got</strong>, with a match score.
            <br><br>
            After viewing the quiz, you can:
            <ul>
            <li><strong>Improve:</strong> Keep same parameters, ask system to find a better match</li>
            <li><strong>Fresh:</strong> Change parameters above and generate a new quiz</li>
            <li><strong>Accept:</strong> If satisfied, continue to the survey</li>
            </ul>
            This is an example of what a real user experiences when composing quizzes with QuizComp.
            </div>
            """, unsafe_allow_html=True)

        for quiz_idx, quiz in enumerate(reversed(quiz_history), 1):
            actual_idx = len(quiz_history) - quiz_idx
            target_match = quiz.get('target_match', 0)
            match_color = "#22c55e" if target_match >= 0.85 else "#f59e0b" if target_match >= 0.70 else "#ef4444"

            is_example = quiz.get('is_example', False)
            quiz_label = f"📚 Example Quiz (Real User Session)" if is_example else f"Quiz {actual_idx + 1}"
            expander_title = f"{quiz_label} - Target Match: {int(target_match * 100)}% ({len(quiz.get('questions', []))} questions)"

            with st.expander(expander_title, expanded=(quiz_idx == 1)):

                # Show target match score prominently
                st.markdown(
                    f"""
                    <div style='border:2px solid {match_color};border-radius:12px;padding:1rem;margin:0.6rem 0 0.9rem;background:#f8fafc;'>
                    <div style='font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#64748b;'>Target match score</div>
                    <div style='font-size:2.1rem;font-weight:800;color:{match_color};font-family:monospace;'>{int(target_match * 100)}%</div>
                    <div style='margin-top:0.45rem;height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden;'>
                    <div style='width:{target_match * 100}%;height:100%;background:{match_color};border-radius:999px;'></div>
                    </div>
                    <div style='font-size:0.85rem;color:#64748b;margin-top:0.5rem;'>How closely the generated quiz matched your requested parameters.</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                # Show comparison in tabs
                comparison_tab, questions_tab = st.tabs(["Parameter Comparison", "Quiz Questions"])

                with comparison_tab:
                    if is_example:
                        st.markdown("""
                        <div style='background:#fef3c7;padding:0.8rem;border-radius:8px;margin-bottom:1rem;border-left:4px solid #f59e0b;'>
                        <strong>Real Example:</strong> This shows what an actual user <strong>REQUESTED</strong> (left) vs what they <strong>RECEIVED</strong> (right) from QuizComp.
                        Notice the system added extra topics and difficulty levels to fill the quiz.
                        </div>
                        """, unsafe_allow_html=True)

                    col_left, col_right = st.columns(2)

                    with col_left:
                        st.markdown("### What User Requested")
                        st.markdown(f"**Quiz size:** {quiz.get('num_questions', 0)} questions")

                        st.markdown("**Topic distribution:**")
                        req_topics = quiz.get('requested_topic_weights', [])
                        if len(req_topics) < len(TOPIC_NAMES):
                            req_topics = req_topics + [0] * (len(TOPIC_NAMES) - len(req_topics))

                        for topic_name, weight in zip(TOPIC_NAMES, req_topics):
                            if weight > 0:
                                st.markdown(
                                    f"<div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:0.3rem;'><span>{topic_name}</span><span>{weight}%</span></div>",
                                    unsafe_allow_html=True,
                                )
                                st.progress(float(weight) / 100.0)

                        st.markdown("**Difficulty distribution:**")
                        req_diffs = quiz.get('requested_diff_weights', [])
                        if len(req_diffs) < len(DIFFICULTY_LABELS):
                            req_diffs = req_diffs + [0] * (len(DIFFICULTY_LABELS) - len(req_diffs))

                        for diff_label, weight in zip(DIFFICULTY_LABELS, req_diffs):
                            if weight > 0:
                                st.markdown(
                                    f"<div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:0.3rem;'><span>{diff_label}</span><span>{weight}%</span></div>",
                                    unsafe_allow_html=True,
                                )
                                st.progress(float(weight) / 100.0)

                    with col_right:
                        st.markdown("### What User Actually Got")
                        st.markdown(f"**Quiz size:** {len(quiz.get('questions', []))} questions")

                        st.markdown("**Topic coverage:**")
                        actual_topics = quiz.get('actual_topic_weights', [])
                        requested_topics = quiz.get('requested_topic_weights', [])

                        # Ensure both lists have same length
                        if len(actual_topics) < len(TOPIC_NAMES):
                            actual_topics = actual_topics + [0] * (len(TOPIC_NAMES) - len(actual_topics))
                        if len(requested_topics) < len(TOPIC_NAMES):
                            requested_topics = requested_topics + [0] * (len(TOPIC_NAMES) - len(requested_topics))

                        for topic_name, actual, requested in zip(TOPIC_NAMES, actual_topics, requested_topics):
                            if actual > 0 or requested > 0:
                                delta = actual - requested
                                delta_color = "#22c55e" if abs(delta) <= 5 else "#ef4444"
                                status = "✓" if abs(delta) <= 5 else "✗"
                                st.markdown(
                                    f"<div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:0.3rem;'><span>{topic_name}</span><span style='color:{delta_color};'>{status} {actual}% ({delta:+d})</span></div>",
                                    unsafe_allow_html=True,
                                )
                                st.progress(float(actual) / 100.0)

                        st.markdown("**Difficulty coverage:**")
                        actual_diffs = quiz.get('actual_diff_weights', [])
                        requested_diffs = quiz.get('requested_diff_weights', [])

                        # Ensure both lists have same length
                        if len(actual_diffs) < len(DIFFICULTY_LABELS):
                            actual_diffs = actual_diffs + [0] * (len(DIFFICULTY_LABELS) - len(actual_diffs))
                        if len(requested_diffs) < len(DIFFICULTY_LABELS):
                            requested_diffs = requested_diffs + [0] * (len(DIFFICULTY_LABELS) - len(requested_diffs))

                        for diff_label, actual, requested in zip(DIFFICULTY_LABELS, actual_diffs, requested_diffs):
                            if actual > 0 or requested > 0:
                                delta = actual - requested
                                delta_color = "#22c55e" if abs(delta) <= 5 else "#ef4444"
                                status = "✓" if abs(delta) <= 5 else "✗"
                                st.markdown(
                                    f"<div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:0.3rem;'><span>{diff_label}</span><span style='color:{delta_color};'>{status} {actual}% ({delta:+d})</span></div>",
                                    unsafe_allow_html=True,
                                )
                                st.progress(float(actual) / 100.0)

                    st.markdown("---")
                    st.markdown("""
                    **Note:** The system tries to match your requested distribution, but may include extra topics or
                    difficulty levels if there aren't enough questions matching your exact parameters.
                    The ✓ indicates a good match (within 5%), ✗ indicates a larger deviation.
                    """)

                with questions_tab:
                    st.markdown("**Generated Questions:**")
                    questions = quiz.get('questions', [])
                    if questions:
                        for q_idx, question in enumerate(questions, 1):
                            if question:
                                render_quiz_question(question, q_idx)
                    else:
                        st.warning("No questions available for this quiz.")

                # Action buttons (only for the most recent quiz)
                if quiz_idx == 1:
                    st.markdown("---")
                    st.markdown("### What would you like to do next?")

                    st.markdown("""
                    <div style='background:#f1f5f9;padding:0.8rem;border-radius:8px;margin-bottom:1rem;'>
                    <strong>Three options:</strong>
                    <ul style='margin:0.5rem 0 0 0;padding-left:1.5rem;'>
                    <li><strong>Accept:</strong> You're satisfied with this quiz</li>
                    <li><strong>Improve:</strong> Keep same parameters, system tries to find better match</li>
                    <li><strong>Adjust parameters:</strong> Change topics/difficulty above and generate fresh</li>
                    </ul>
                    </div>
                    """, unsafe_allow_html=True)

                    col1, col2, col3 = st.columns(3)

                    with col1:
                        if safe_button("✓ Accept this quiz", use_container_width=True):
                            log_interaction_event("button_click", "accept_quiz",
                                                f"quiz_{actual_idx}_match_{int(target_match*100)}", "exploration")
                            st.success("✓ Quiz accepted! You can continue to the survey or keep exploring.")

                    with col2:
                        if safe_button("↻ Improve (same parameters)", use_container_width=True):
                            # Generate with same parameters (Improve mode)
                            quiz_questions = generate_sample_quiz(
                                quiz['num_questions'],
                                quiz['requested_topic_weights'],
                                quiz['requested_diff_weights']
                            )
                            actual_topics, actual_diffs = calculate_actual_distributions(quiz_questions)
                            target_match_new = calculate_target_match(
                                quiz['requested_topic_weights'], actual_topics,
                                quiz['requested_diff_weights'], actual_diffs
                            )

                            st.session_state["quiz_history"].append({
                                "num_questions": quiz['num_questions'],
                                "requested_topic_weights": quiz['requested_topic_weights'],
                                "requested_diff_weights": quiz['requested_diff_weights'],
                                "actual_topic_weights": actual_topics,
                                "actual_diff_weights": actual_diffs,
                                "target_match": target_match_new,
                                "questions": quiz_questions,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "mode": "improve",
                            })
                            log_interaction_event("button_click", "improve_quiz",
                                                f"from_{actual_idx}_match_{int(target_match*100)}_to_{int(target_match_new*100)}",
                                                "exploration")
                            rerun_app()

                    with col3:
                        pass  # Old button removed


def render_survey_page():
    log_page_view("survey")

    st.title("Experience Evaluation")

    st.markdown("---")
    st.markdown("""
    ## IMAGINE YOU ACTUALLY EXPERIENCED IT

    **IMAGINE that you PERSONALLY COMPOSED** a quiz using QuizComp: setting parameters, generating quizzes, reviewing proposals, adjusting parameters, and iterating until satisfied.

    **Answer as if you HAD personally used this system yourself.**
    """)
    st.markdown("---")

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
    q7 = render_rating_buttons(
        "(Q7) Would you use this system again?",
        "q7",
    )

    st.markdown("---")

    st.text_area(
        "Any comments?",
        key="survey_comments",
        height=140,
        placeholder="Write anything about the quiz composition process, the platform interface, or your experience.",
    )

    if st.session_state.get("save_error"):
        st.error(f"**Survey save failed:** {st.session_state['save_error']}")

    if st.session_state.get("survey_error"):
        st.warning(f"**Survey incomplete:** {st.session_state['survey_error']}")

    col1, col2 = st.columns([1, 1])
    with col1:
        if safe_button("Back to platform", use_container_width=True):
            st.session_state["phase"] = "exploration"
            rerun_app()
    with col2:
        if safe_button("Submit survey", type="primary", use_container_width=True):
            missing = [
                label
                for label, value in [
                    ("Q1", q1), ("Q2", q2), ("Q3", q3), ("Q4", q4),
                    ("Q5", q5), ("Q6", q6), ("Q7", q7),
                ]
                if value is None
            ]
            if missing:
                st.session_state["survey_error"] = f"Please answer all: {', '.join(missing)}."
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
    review_id = f"quizcomp_notrace_{uuid4().hex}"
    participant_context = st.session_state.get("participant_context") or get_participant_context()

    log_interaction_event("survey", "submit_survey", event_details="completed", phase="survey")
    track_phase_time("survey")

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

    duration_seconds = max(0.0, time.time() - float(st.session_state.get("study_started_at", time.time())))
    now_iso = datetime.now(timezone.utc).isoformat()

    phase_timings = st.session_state.get("phase_timings", {})
    num_quiz_generations = len(st.session_state.get("quiz_history", []))
    skipped_to_survey = 0 if num_quiz_generations > 0 else 1

    with sqlite3.connect(SURVEY_DB_FILE, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if participant_context.get("assignment_source") == "prolific":
            prior = connection.execute("SELECT review_id FROM survey_responses WHERE prolific_pid=? AND study_id=? AND session_id=? LIMIT 1", (participant_context["prolific_pid"], participant_context["study_id"], participant_context["session_id"])).fetchone()
            if prior:
                return prior[0]
        connection.execute(
            """
            INSERT INTO survey_responses (
                review_id, participant_key, prolific_pid, study_id, session_id,
                assignment_source, q1_accomplishment, q2_effort_required,
                q3_mental_demand, q4_controllability, q5_temporal_demand,
                q6_satisfaction, q7_would_use_again, comments, duration_seconds,
                time_landing, time_consent, time_exploration, time_survey,
                num_quiz_generations, skipped_to_survey,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                responses["comments"],
                duration_seconds,
                phase_timings.get("landing", 0.0),
                phase_timings.get("consent", 0.0),
                phase_timings.get("exploration", 0.0),
                phase_timings.get("survey", 0.0),
                num_quiz_generations,
                skipped_to_survey,
                now_iso,
                now_iso,
            ),
        )
        connection.commit()

    return review_id


def render_done_page():
    st.title("Study complete")
    st.markdown("Thank you for completing the QuizComp evaluation. Your response has been recorded.")

    completion_url = get_prolific_completion_url()
    if completion_url:
        st.code(get_prolific_completion_code(), language=None)
        st.markdown("You will now be returned to Prolific. If redirect doesn't happen, use the link below.")
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
            st.warning("**Prolific completion code required**: Set PROLIFIC_COMPLETION_CODE in environment.")
        st.markdown("This study session is finished. Please close this page.")


def main():
    st.set_page_config(
        page_title="QuizComp Experience Evaluation",
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
    elif phase == "example":
        render_example_page()
    elif phase == "survey":
        render_survey_page()
    else:
        render_done_page()


if __name__ == "__main__":
    main()
