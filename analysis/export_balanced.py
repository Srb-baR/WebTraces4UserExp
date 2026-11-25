"""Export size-matched evaluator SQLite copies for demonstration and sensitivity checks."""
import argparse
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'code/report/reconstructibility_analysis/src'))
from data import DIMS, normalize, sources

TARGETS = {'testreco': 55, 'quizcomp': 65, 'webarena': 53}
METADATA = ['task', 'response_id', 'actor_id', 'actor_session_id', 'evaluator_id',
            'evaluation_round', 'run_id', 'protocol']


def write_report(report, manifest):
    lines = [
        '# Balanced evaluator databases',
        '',
        'The evaluator files in `code/report/data/human evaluators/` and `code/report/data/LLM evaluators/` contain the balanced rows. The default rerun reads these files including the padded rows. Trace metrics aggregate rows by actor session within each panel. The description-only bootstrap draws each original response together with its copies. To reproduce the analysis from the observed records, pass `--observed`.',
        '',
        'The target is 55 rows per TestReco panel, 65 per QuizComp panel, and 53 per WebArena task and panel (106 rows across its two tasks). A panel is a human replay round, a human description-only control, or one model in one condition. The export reuses the paper’s eligibility rules and six rating dimensions.',
        '',
        'Panels over target were sampled without replacement with the export seed. TestReco description-only controls went from 58 to 55 for humans and from 65 to 55 per model. QuizComp GPT-OSS trace and Llama 4 Scout description-only went from 63 to 65 rows. The WebArena human description-only copy keeps the 53 observers who answered both tasks. Model description-only copies keep the same 53 generations for both task views. Short trace panels were padded per task by sampling from their observed ratings with replacement. Repetitions add rows without adding independent ratings or filling missing actor sessions.',
        '',
        'The row IDs below are local to the indicated SQLite file. The new rows retain their source rating and evaluator and actor metadata.',
        '',
        '| Database | Task | New row ID | Duplicates row ID |',
        '| --- | --- | ---: | ---: |',
    ]
    for panel in manifest:
        for pair in panel['duplicate_rows']:
            lines.append(f"| `{panel['database']}` | {panel['task']} | {pair['new_id']} | {pair['original_id']} |")
    lines += ['', '## Panel counts', '',
              '| Database | Task | Observed | Retained | Removed | Duplicated | Output rows |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for p in manifest:
        lines.append(f"| `{p['database']}` | {p['task']} | {p['observed']} | {p['retained_observed']} | {p['removed']} | {p['duplicated']} | {p['output_rows']} |")
    report.write_text('\n'.join(lines)+'\n', encoding='utf-8')


def export(data, output, seed):
    _, evaluations, _, _ = normalize(sources(ROOT, data, use_observed=True))
    output.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(seed)
    manifest = []
    for source, panel in evaluations.groupby('source_database', sort=True):
        relative = Path(source).relative_to(data).as_posix().removeprefix('observed/')
        relative = Path(relative)
        application = panel.application.iloc[0]
        condition = panel.experimental_condition.iloc[0]
        kind = panel.evaluator_type.iloc[0]
        target = TARGETS[application]
        base = panel[METADATA].drop_duplicates().set_index(['task', 'response_id'])
        scores = panel.pivot(index=['task', 'response_id'], columns='dimension', values='evaluator_score')
        rows = base.join(scores[DIMS]).reset_index()
        if application == 'webarena' and condition == 'no_trace':
            # Human controls keep the same complete observers in both tasks.
            # A model generation supplies both task evaluations in the analysis.
            shared_key = 'evaluator_id' if kind == 'human' else 'run_id'
            panels = [task[shared_key].to_numpy() for _, task in rows.groupby('task')]
            shared = np.intersect1d(*panels)
            chosen = rng.choice(shared, size=target, replace=False)
        else:
            shared_key = None
        destination = output/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as db:
            db.executescript('''
                CREATE TABLE metadata (
                    application TEXT NOT NULL, condition TEXT NOT NULL,
                    evaluator_type TEXT NOT NULL, source_database TEXT NOT NULL,
                    target_per_task INTEGER NOT NULL, seed INTEGER NOT NULL,
                    usage TEXT NOT NULL
                );
                CREATE TABLE ratings (
                    id INTEGER PRIMARY KEY,
                    task TEXT NOT NULL, source_response_id TEXT NOT NULL,
                    actor_id TEXT, actor_session_id TEXT, evaluator_id TEXT NOT NULL,
                    evaluation_round TEXT NOT NULL, run_id TEXT NOT NULL, protocol TEXT NOT NULL,
                    Acc INTEGER, Eff INTEGER, MD INTEGER, Cont INTEGER, TD INTEGER, Sat INTEGER,
                    is_duplicate INTEGER NOT NULL CHECK (is_duplicate IN (0,1))
                );
            ''')
            db.execute('INSERT INTO metadata VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (application, condition, kind, relative.as_posix(), target, seed,
                        'Derived size-matched export. Duplicates are not independent observations.'))
            for task, task_rows in rows.groupby('task', sort=True):
                observed = len(task_rows)
                if shared_key:
                    selected = task_rows[task_rows[shared_key].isin(chosen)]
                elif observed > target:
                    selected = task_rows.iloc[np.sort(rng.choice(observed, target, replace=False))]
                else:
                    selected = task_rows
                values = selected[METADATA+DIMS].astype(object)
                values = list(values.where(values.notna(), None).itertuples(index=False, name=None))
                query = 'INSERT INTO ratings VALUES (NULL, ' + ', '.join(['?'] * (len(METADATA+DIMS)+1)) + ')'
                ids = [db.execute(query, (*value, 0)).lastrowid for value in values]
                missing = target - len(values)
                repeats = []
                for i in rng.integers(len(ids), size=missing):
                    row_id = db.execute(query, (*values[i], 1)).lastrowid
                    repeats.append({'new_id': row_id, 'original_id': ids[i]})
                manifest.append(dict(database=relative.as_posix(), task=task, observed=observed,
                                     retained_observed=len(values), removed=observed-len(values),
                                     duplicated=missing, output_rows=target, duplicate_rows=repeats))
    write_report(output.parent/'BALANCED_DATABASES.md', manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'runs/balanced_export')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    export(ROOT/'code/report/data', args.output.resolve(), args.seed)
