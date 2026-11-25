"""Full-detail WebArena observer study, restricted to 53 complete original actors."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
# Reload shared UI code on every rerun so browser refresh picks up edits.
import importlib
import simulation_full_trace.evidence as evidence
import simulation_full_trace.launch as launch
importlib.reload(evidence)
importlib.reload(launch)
from simulation_full_trace.launch import webarena_app
webarena_app(Path(__file__).resolve().parent)
