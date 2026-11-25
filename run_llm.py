"""Run the paper's time-hidden or description-only LLM evaluation."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
RUNNERS = {
    'testreco': ROOT/'code/TestReco/llm_user_study',
    'quizcomp': ROOT/'code/QuizComp/quizcomp_llm_study',
    'webarena': ROOT/'code/Webarena',
}
MODELS = ('deepseek-v3.2', 'llama-4-scout', 'mistral-small-3.2', 'gpt-oss-120b', 'qwen3-235b')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', choices=RUNNERS)
    parser.add_argument('condition', choices=('trace', 'no-trace'))
    parser.add_argument('model', choices=MODELS)
    parser.add_argument('--preview', action='store_true', help='Export prompts without network calls')
    parser.add_argument('--rebuild-prompts', action='store_true',
                        help='Build requests from the supplied code and actor data instead of the recorded messages')
    parser.add_argument('--runs', type=int)
    parser.add_argument('--session-id', action='append', default=[])
    args = parser.parse_args()
    python_file = 'survey_only_no_trace_openrouter.py' if args.condition == 'no-trace' else 'survey_only_openrouter.py'
    destination = ROOT/'runs/llm'/args.application/args.condition
    if args.preview:
        destination = ROOT/'runs/prompts'/args.application/args.condition
    command = [
        sys.executable, str(RUNNERS[args.application]/python_file),
        '--model', args.model, '--temperature', '0.5',
        '--runs', str(args.runs if args.runs is not None else (65 if args.condition == 'no-trace' and not args.preview else 1)), '--output-dir', str(destination),
        '--max-output-tokens', '8192',
    ]
    if not args.rebuild_prompts:
        command += ['--recorded-prompts', str(ROOT/'prompts'/f'{args.application}-{args.condition}.json.gz')]
    if args.preview:
        command += ['--dry-run', '--offline-context-limit', '4000000', '--context-check', 'provider']
    else:
        command += ['--context-check', 'provider', '--exclude-provider', 'google-vertex']
    if args.application == 'webarena' and args.condition == 'trace':
        command.append('--completed-pairs-only')
    for session_id in args.session_id:
        command += ['--session-id', session_id]
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
