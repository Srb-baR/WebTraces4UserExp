"""Run a local human evaluator app and store new responses in runs/."""
import argparse
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
APPS = {'testreco': 'TestReco', 'quizcomp': 'QuizComp', 'webarena': 'Webarena'}
VARIANTS = {'trace': 'Simulation_app', 'no-trace': 'Simulation_app_no_trace',
            'timing-visible': 'Simulation_app_full_trace'}
WEB_VARIANTS = {'trace': 'reconstructed_Webarena', 'no-trace': 'no_trace_webarena',
                'timing-visible': 'reconstructed_Webarena_full_trace'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', choices=APPS)
    parser.add_argument('variant', choices=VARIANTS)
    parser.add_argument('--port', type=int, default=8501)
    args = parser.parse_args()
    load_dotenv(ROOT/'.env')
    os.chdir(ROOT)
    output = ROOT/'runs/human'/args.application/args.variant
    output.mkdir(parents=True, exist_ok=True)
    os.environ['REVIEWER_OUTPUT_DIR'] = str(output)
    os.environ['RECONSTRUCTED_DB'] = str(output/'responses.sqlite')
    os.environ['NO_TRACE_DB'] = str(output/'responses.sqlite')
    os.environ.setdefault('PROLIFIC_COMPLETION_CODE', 'LOCALPREVIEW')
    os.environ.setdefault('PROLIFIC_REQUIRED', '0')
    if args.application == 'webarena':
        folder = ROOT/'code'/WEB_VARIANTS[args.variant]
    else:
        folder = ROOT/'code'/APPS[args.application]/VARIANTS[args.variant]
    query = '?PROLIFIC_PID=LOCALPREVIEW&STUDY_ID=LOCAL&SESSION_ID=LOCAL1'
    print(f'Open http://127.0.0.1:{args.port}/{query}', flush=True)
    os.execv(sys.executable, [sys.executable, '-m', 'streamlit', 'run', str(folder/'app.py'),
                             '--server.address', '127.0.0.1', '--server.port', str(args.port),
                             '--server.headless', 'true'])


if __name__ == '__main__':
    main()
