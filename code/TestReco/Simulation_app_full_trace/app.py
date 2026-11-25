"""Full recorded interaction and timing version of the TestReco evaluator study."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
# Reload shared UI code on every rerun so browser refresh picks up edits.
import importlib
import simulation_full_trace.evidence as evidence
import simulation_full_trace.launch as launch
importlib.reload(evidence)
importlib.reload(launch)
from simulation_full_trace.launch import streamlit_app
streamlit_app('testreco',Path(__file__).resolve().parent)
