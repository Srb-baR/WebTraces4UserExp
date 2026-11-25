"""One parameterized entry point for all trace-based survey experiments.

Example:
  python code/survey_trace/run_survey.py --app testreco --configuration journey \
      --timing visible --model deepseek-v4.1-flash --temperature 0.5
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from survey_trace import runner


def main(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    selector=argparse.ArgumentParser(add_help=False,description=__doc__)
    selector.add_argument('--app',choices=['testreco','quizcomp','webarena'])
    selector.add_argument('--configuration',choices=['full-trace','journey'],default='full-trace',help='Structured full trace or detailed first-person journey.')
    selector.add_argument('--timing',choices=['hidden','visible'],default='hidden',help='Withhold or include recorded interaction timing.')
    selected,remaining=selector.parse_known_args(arguments)
    help_requested=any(a in ('-h','--help') for a in arguments)
    if help_requested:
        print('APPLICATION / CONFIGURATION OPTIONS:')
        selector.print_help()
        print('\nMODEL, TEMPERATURE, DATA, BUDGET AND EXECUTION OPTIONS:')
    elif not selected.app:selector.error('--app is required')
    journey=selected.configuration=='journey'
    forwarded=remaining+(['--timing',selected.timing] if journey else [])
    previous=sys.argv
    try:
        sys.argv=[previous[0]]+forwarded
        runner.main(selected.app or 'testreco',journey=journey,time_visible=not journey and selected.timing=='visible')
    finally:sys.argv=previous

if __name__=='__main__':main()
