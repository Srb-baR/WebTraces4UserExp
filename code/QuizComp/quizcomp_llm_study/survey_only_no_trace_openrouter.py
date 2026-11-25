"""Hypothetical surveys using system/study descriptions only, without individual traces."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from survey_trace.runner import main

if __name__ == '__main__':
    main('quizcomp', description_only=True)
