"""Reconstruct the short paper's statistics from the packaged SQLite inputs."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code/report/reconstructibility_analysis/src'))
from data import DIMS, normalize, sources
from features import extract, additional_webarena_features
from statcore import rowrho

CELLS = [('webarena', 'T1'), ('webarena', 'T2'), ('testreco', 'main'), ('quizcomp', 'main')]


def average(rows):
    data = np.asarray(rows)
    valid = np.isfinite(data)
    counts = valid.sum(axis=0)
    return np.divide(np.nansum(data, axis=0), counts,
                     out=np.full(counts.shape, np.nan), where=counts > 0)


def permutation_p(null, observed):
    null = np.asarray(null)
    null = null[np.isfinite(null)]
    if not len(null) or not np.isfinite(observed):
        return np.nan
    return (1 + (np.abs(null) >= abs(observed) - 1e-12).sum()) / (len(null) + 1)


def with_bh(frame, columns, group='group'):
    for column in columns:
        q = column.removesuffix('_p') + '_q'
        frame[q] = np.nan
        for _, family in frame.groupby(group):
            p = family[column].dropna()
            if len(p):
                frame.loc[p.index, q] = stats.false_discovery_control(p)
    return frame


def evaluate(x, trace, no_trace, permutations, bootstraps, rng, human_control_indices):
    near, rho, rho_null, gain_null, errors, boot = [], [], [], [], [], []
    for label, rows in trace.groupby('evaluation_round'):
        values = rows.groupby('actor_session_id').evaluator_score.mean().reindex(x.index).to_numpy()
        valid = np.isfinite(values)
        actual, predicted = x.to_numpy()[valid], values[valid]
        if not len(actual):
            continue
        error = np.abs(actual - predicted)
        near.append((error <= 1).mean())
        rho.append(rowrho(actual, predicted))
        permuted = x.to_numpy()[permutations[:, valid]]
        rho_null.append(rowrho(permuted, predicted))
        shuffled = np.abs(actual[:, None] - predicted).mean()
        gain_null.append(shuffled - np.abs(permuted - predicted).mean(axis=1))
        control = no_trace if rows.evaluator_type.iloc[0] == 'human' else no_trace[no_trace.evaluation_round == label]
        control = control.dropna(subset=['evaluator_score'])
        padded = control.response_id.duplicated().any()
        if padded:
            # Keep the contribution of each row. In resampling, draw an
            # observed response and all its repeated rows together.
            blocks = control.groupby('response_id', sort=False).evaluator_score.agg(['first', 'size'])
            weights = blocks['size'].to_numpy()
            control = blocks['first'].to_numpy()
        else:
            control = control.evaluator_score.to_numpy()
        if not len(control):
            continue
        all_errors = np.abs(actual[:, None] - control)
        none = np.average(all_errors.mean(axis=0), weights=weights) if padded else all_errors.mean()
        errors.append((error.mean(), shuffled, none))
        # Share the actor bootstrap across rounds/models and the human control
        # bootstrap across human rounds. Missing model predictions stay missing.
        sampled = bootstraps
        controls = (rng.integers(len(control), size=(len(sampled), len(control)))
                    if rows.evaluator_type.iloc[0] != 'human' else None)
        if controls is None:
            controls = human_control_indices
        differences = []
        for ids, ci in zip(sampled, controls):
            ids = ids[valid[ids]]
            if not len(ids):
                differences.append((np.nan, np.nan))
                continue
            a, b = x.to_numpy()[ids], values[ids]
            control_errors = np.abs(a[:, None] - control[ci])
            baseline = (np.average(control_errors.mean(axis=0), weights=weights[ci])
                        if padded else control_errors.mean())
            differences.append((baseline - np.abs(a - b).mean(),
                                baseline - np.abs(a[:, None] - b).mean()))
        boot.append(differences)
    near_value, corr_value = average(near), average(rho)
    result = dict(near=float(near_value), rho=float(corr_value),
                  rho_p=permutation_p(average(rho_null), corr_value))
    if not errors:
        return result
    traced, shuffled, none = average(errors)
    individual = shuffled - traced
    result.update(trace_mae=traced, shuffled_mae=shuffled, no_trace_mae=none,
                  total=none-traced, population=none-shuffled, individual=individual,
                  individual_p=permutation_p(average(gain_null), individual))
    # The manuscript specifies percentile intervals. For adjusted significance,
    # use a two-sided centered bootstrap test.
    for name, draws in zip(('total', 'population'), average(boot).T):
        result[name+'_low'], result[name+'_high'] = np.nanquantile(draws, [.025, .975])
        result[name+'_p'] = permutation_p(draws-result[name], result[name])
    return result


def run(data, output, resamples, seed, use_observed=False):
    originals, evaluations, audit, exclusions = normalize(sources(ROOT, data, use_observed))
    output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    records, cue_rows, means = [], [], []
    features, _ = extract(originals)
    features = pd.concat([features, additional_webarena_features(originals)], ignore_index=True)
    for app, task in CELLS:
        original = originals.query('application == @app and task == @task').set_index('actor_session_id')
        evals = evaluations.query('application == @app and task == @task')
        cues = features.query('application == @app and task == @task')
        perm = np.argsort(rng.random((resamples, len(original))), axis=1)
        boot = rng.integers(len(original), size=(resamples, len(original)))
        for dimension in DIMS:
            x = original[dimension]
            use = evals.query('dimension == @dimension')
            human = use.query("evaluator_type == 'human'")
            llm = use.query("evaluator_type == 'llm'")
            human_trace = human.query("experimental_condition == 'trace'")
            human_control = human.query("experimental_condition == 'no_trace'")
            llm_trace = llm.query("experimental_condition == 'trace'")
            llm_control = llm.query("experimental_condition == 'no_trace'")
            n_control = human_control.dropna(subset=['evaluator_score']).response_id.nunique()
            human_control_indices = rng.integers(n_control, size=(resamples, n_control))
            common = dict(application=app, task=task, dimension=dimension)
            baseline = np.max((np.abs(x.to_numpy()[:, None] - np.arange(1, 6)) <= 1).mean(axis=0))
            results = [('human', human_trace, human_control), ('llm', llm_trace, llm_control)]
            for group, trace, control in results:
                records.append(dict(**common, group=group, oracle_near=baseline,
                                    **evaluate(x, trace, control, perm, boot, rng, human_control_indices)))
            # Model-level rank tests use the same actor permutations as the pooled row.
            for model, panel in llm_trace.groupby('evaluation_round'):
                records.append(dict(**common, group=model, oracle_near=baseline,
                                    **evaluate(x, panel, llm_control.iloc[:0], perm, boot, rng, human_control_indices)))
            means.append(dict(**common, actor=x.mean(),
                              human_trace=human_trace.groupby('evaluation_round').evaluator_score.mean().mean(),
                              human_no_trace=human_control.evaluator_score.mean()))
            for feature, feature_rows in cues.groupby('feature'):
                values = feature_rows.set_index('actor_session_id').value.reindex(x.index)
                if values.nunique() < 2:
                    continue
                paired = values.notna() & x.notna()
                validity = stats.spearmanr(values[paired], x[paired])
                row = dict(**common, feature=feature, n=int(paired.sum()),
                           family='additional' if feature_rows.additional.eq(True).any() else 'primary',
                           validity=validity.statistic, validity_p=validity.pvalue,
                           visible=feature_rows.visible_trace.iloc[0])
                for group, trace, _ in results:
                    correlations = []
                    for _, panel in trace.groupby('evaluation_round'):
                        y = panel.groupby('actor_session_id').evaluator_score.mean().reindex(x.index)
                        valid = y.notna() & values.notna()
                        if y[valid].nunique() > 1 and values[valid].nunique() > 1:
                            correlations.append(stats.spearmanr(values[valid], y[valid]).statistic)
                    # Figure 4 compares predictions with withheld duration as a
                    # descriptive association, even when evaluators never saw it.
                    row[group] = average(correlations)
                cue_rows.append(row)
    distribution_rows = []
    model_ratings = evaluations[evaluations.evaluator_type == 'llm']
    for keys, ratings in model_ratings.groupby(
        ['application', 'task', 'dimension', 'evaluation_round', 'experimental_condition']
    ):
        scores = ratings.evaluator_score.dropna()
        if scores.empty:
            continue
        freq = scores.value_counts()
        distribution_rows.append(dict(
            zip(['application', 'task', 'dimension', 'model', 'condition'], keys),
            n=len(scores), sd=scores.std(ddof=0), modal_fraction=freq.max()/len(scores),
        ))
    pd.DataFrame(distribution_rows).to_csv(output/'llm_rating_dispersion.csv', index=False)
    metrics = with_bh(pd.DataFrame(records), ['rho_p', 'total_p', 'population_p', 'individual_p'])
    metrics.to_csv(output/'agreement_ranks_and_gains.csv', index=False)
    cues = with_bh(pd.DataFrame(cue_rows), ['validity_p'], 'family')
    cues.to_csv(output/'cue_validity_and_utilization.csv', index=False)
    means = pd.DataFrame(means)
    means.to_csv(output/'population_means.csv', index=False)
    for group in ['human_trace', 'human_no_trace']:
        fit = stats.linregress(means.actor, means[group])
        print(f'{group}: population r={fit.rvalue:.3f}, slope={fit.slope:.3f}')
    audit['database'] = audit['path'].map(lambda value: Path(value).relative_to(data.resolve()).as_posix())
    audit.drop(columns=['path']).to_csv(output/'matching_audit.csv', index=False)
    if not exclusions.empty:
        exclusions['source_database'] = exclusions['source_database'].map(
            lambda value: Path(value).relative_to(data.resolve()).as_posix())
    exclusions.to_csv(output/'exclusions.csv', index=False)
    (output/'analysis_settings.json').write_text(
        json.dumps({'resamples': resamples, 'seed': seed, 'use_observed': use_observed}, indent=2) + '\n')
    print(f'{len(originals)} actor surveys, {len(evaluations)} dimension-level evaluations, {len(exclusions)} exclusions')
    print(cues.groupby('family').validity_p.agg(['size', 'count']).to_string())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT/'code/report/data')
    parser.add_argument('--output', type=Path, default=ROOT/'runs/analysis')
    parser.add_argument('--resamples', type=int, default=5000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--observed', action='store_true', help='Reproduce the paper from archived observed records')
    args = parser.parse_args()
    run(args.input, args.output, args.resamples, args.seed, args.observed)
