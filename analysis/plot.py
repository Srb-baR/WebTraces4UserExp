"""Render the five standalone WWW paper plots from the analysis tables."""
import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import pandas as pd

CELLS = ['WebA 1', 'WebA 2', 'TestReco', 'QuizComp']
DIMS = ['Acc', 'Eff', 'Men', 'Ctl', 'Tmp', 'Sat']
CELL_KEYS = [('webarena', 'T1'), ('webarena', 'T2'), ('testreco', 'main'), ('quizcomp', 'main')]
DIM_KEYS = ['Acc', 'Eff', 'MD', 'Cont', 'TD', 'Sat']
GROUPS = ['human', 'deepseek/deepseek-v3.2', 'meta-llama/llama-4-scout',
          'mistralai/mistral-small-3.2-24b-instruct', 'openai/gpt-oss-120b',
          'qwen/qwen3-235b-a22b-2507', 'llm']
LABELS = ['Human evaluators (mean of rounds)', 'DeepSeek V3.2', 'Llama 4 Scout',
          'Mistral Small 3.2', 'GPT-OSS 120B', 'Qwen3 235B', 'LLM evaluators (mean of 5 models)']
GRAY = '#d9d9d9'
EDGE = '#5a5a5a'
X = np.arange(24)
FONT = 'TeX Gyre Termes'
STYLE = {
    'font.family': 'serif',
    'font.serif': [FONT, 'Liberation Serif', 'DejaVu Serif'],
    'font.weight': 'bold',
    'axes.labelweight': 'bold',
    'axes.linewidth': .6,
    'hatch.linewidth': .65,
    'pdf.fonttype': 42,
    'savefig.facecolor': 'white',
}


def add_axes(fig, box):
    """Position axes using top-left PDF coordinates in points."""
    width, height = fig.get_size_inches() * 72
    left, top, right, bottom = box
    return fig.add_axes((left / width, 1 - bottom / height,
                         (right - left) / width, (bottom - top) / height))


def save(fig, folder, name):
    fig.savefig(folder/f'{name}.pdf')
    fig.savefig(folder/f'{name}.png', dpi=300)
    plt.close(fig)


def stars(p, q):
    return '**' if q < .05 else '*' if p < .05 else ''


def study_ticks(ax, size, right=False):
    ax.set_xlim(-.6, 23.6)
    ax.set_xticks(X, DIMS * 4, rotation=90)
    ax.tick_params(length=3.5, width=.6, pad=1.6, labelsize=size)
    ax.spines[['top', 'right']].set_visible(False)
    if right:
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position('right')
    for i, label in enumerate(CELLS):
        ax.text(i*6+2.5, 1.015, label, transform=ax.get_xaxis_transform(),
                ha='center', va='bottom', fontsize=size)
        if i:
            ax.axvline(i*6-.5, color=GRAY, linewidth=.6, zorder=0)


def component_bars(ax, x, human_or_population, llm_or_individual):
    ax.bar(x-.17, human_or_population, width=.34, align='center',
           color=GRAY, edgecolor=EDGE, linewidth=.45)
    ax.bar(x+.17, llm_or_individual, width=.34, align='center',
           color='white', edgecolor='black', linewidth=.45, hatch='///')


def agreement(panels, destination):
    fig = plt.figure(figsize=(236.240875/72, 141.375748/72))
    left = add_axes(fig, (22.33, 14.33, 109.98, 127.98))
    right = add_axes(fig, (120.50, 14.33, 208.14, 127.98))
    study_ticks(left, 4.6)
    study_ticks(right, 4.6, right=True)
    left.set_ylim(.30, 1.02)
    left.set_yticks(np.arange(.3, 1.01, .1))
    right.set_ylim(-.09, .268)
    right.set_yticks(np.arange(-.05, .251, .05), labels=[
        '−0.05', '0.00', '0.05', '0.10', '0.15', '0.20', '0.25'])
    for ax, title, label in [
        (left, '(a) Near Agreement', 'Near Agreement (|Δ| ≤ 1)'),
        (right, '(b) Gain over shuffled actors', 'Information gain (MAE)'),
    ]:
        ax.tick_params(axis='y', labelsize=5.5)
        ax.set_ylabel(label, fontsize=6, labelpad=2)
        ax.text(.10, 1.10, title, transform=ax.transAxes, fontsize=6.3, va='top')
    human, llm = panels['human'], panels['llm']
    left.plot(X, human.oracle_near, linestyle='none', marker='_',
              color='black', markersize=3.6, markeredgewidth=.8)
    left.plot(X-.10, human.near, linestyle='none', marker='o', markerfacecolor='white',
              markeredgecolor='black', markersize=2.6, markeredgewidth=.65)
    left.plot(X+.10, llm.near, linestyle='none', marker='D', color='black', markersize=2.6)
    component_bars(right, X, human.individual, llm.individual)
    right.axhline(0, color='black', linewidth=.4)
    for offset, ratings in [(-.17, human), (.17, llm)]:
        for j, record in enumerate(ratings.itertuples()):
            mark = stars(record.individual_p, record.individual_q)
            if mark:
                right.annotate(mark, (j+offset, record.individual), xytext=(0, 1.4),
                               textcoords='offset points', ha='center', va='bottom', fontsize=4.5)
    save(fig, destination, 'fig1_panels_column_print')


def permutation_map(panels, destination, permutations):
    fig = plt.figure(figsize=(695.448853/72, 240.043701/72))
    ax = add_axes(fig, (152.25, 22.90, 644.80, 205.86))
    scale = add_axes(fig, (650.16, 22.90, 658.80, 205.86))
    palette = LinearSegmentedColormap.from_list('paper_diverging', [
        '#bd665d', '#cd7e77', '#dea8a0', '#eacbc5', '#f5f1ec',
        '#cfdbe4', '#a5c0d9', '#7ca3ca', '#4578a6'])
    grid = ax.imshow([panels[g].rho for g in GROUPS], vmin=-.55, vmax=.55,
                     cmap=palette, aspect='auto', interpolation='nearest')
    for i, group in enumerate(GROUPS):
        for j, row in enumerate(panels[group].itertuples()):
            mark = stars(row.rho_p, row.rho_q)
            text = f'{row.rho:.2f}'.replace('0.', '.').replace('-', '−')
            ax.text(j, i, text+mark, ha='center', va='center',
                    fontsize=7.5, fontweight='bold' if mark else 'normal')
    ax.set_xticks(X, DIMS * 4)
    ax.set_yticks(range(len(LABELS)), LABELS)
    ax.tick_params(length=0, pad=2.5, labelsize=9.5)
    ax.spines[:].set_visible(False)
    for i, label in enumerate(CELLS):
        ax.text(i*6+2.5, 1.06, label, transform=ax.get_xaxis_transform(),
                ha='center', va='bottom', fontsize=10.5)
        if i:
            ax.axvline(i*6-.5, color='white', linewidth=2.5)
    for divider in (.5, 5.5):
        ax.axhline(divider, color='white', linewidth=2.5)
    bar = fig.colorbar(grid, cax=scale, ticks=[-.4, -.2, 0, .2, .4])
    bar.ax.tick_params(labelsize=8.5, width=.6)
    bar.set_label('Spearman ρ (blue: positive, red: negative)', fontsize=8.5)
    fig.text(.57, .014, f"* p < .05 against {permutations:,} shuffles of the actors' reports"
             "     ** q < .05 after BH over the 24 cells of the row",
             ha='center', va='bottom', fontsize=9)
    save(fig, destination, 'fig3p_permutation_map')


def gain(ratings, destination, group, title):
    fig = plt.figure(figsize=(223.490631/72, (171.351501 if group == 'llm' else 171.320251)/72))
    ax = add_axes(fig, (32.33, 19.25, 222.05, 149.54))
    study_ticks(ax, 6.5)
    ax.set_ylim(-.65, .7565)
    ax.set_yticks(np.arange(-.6, .61, .2))
    ax.tick_params(axis='y', labelsize=7)
    ax.set_ylabel('Reduction in MAE (Likert points)', fontsize=7.5, labelpad=2)
    ax.text(0, 1.145, title, transform=ax.transAxes, fontsize=8, va='top')
    component_bars(ax, X, ratings.population, ratings.individual)
    ax.axhline(0, color='black', linewidth=.5)
    ax.vlines(X, ratings.total_low, ratings.total_high, color='black', linewidth=.55)
    ax.plot(X, ratings.total, linestyle='none', marker='D', markersize=3, color='black')
    for i, row in enumerate(ratings.itertuples()):
        if row.total_q < .05:
            positive = row.total > 0
            ax.annotate('**', (i, row.total_high if positive else row.total_low),
                        xytext=(0, 1 if positive else -1), textcoords='offset points',
                        ha='center', va='bottom' if positive else 'top', fontsize=7.5)
        if row.individual_q < .05:
            ax.annotate('†', (i+.17, row.individual), xytext=(0, 1),
                        textcoords='offset points', ha='center', va='bottom', fontsize=7.5)
    save(fig, destination, f'fig9_{group}_print')


def cue_plot(source, destination):
    selection = [
        ('testreco', 'main', 'Acc', 'learning_accuracy', 'TestReco Acc: accuracy'),
        ('webarena', 'T2', 'Acc', 'submission_count', 'WebA 2 Acc: submissions'),
        ('webarena', 'T1', 'TD', 'session_duration', 'WebA 1 Tmp: duration (hidden)'),
        ('quizcomp', 'main', 'Cont', 'improvement_actions', 'QuizComp Ctl: improve req.'),
        ('quizcomp', 'main', 'Acc', 'improvement_actions', 'QuizComp Acc: improve req.'),
        ('webarena', 'T2', 'Sat', 'submission_count', 'WebA 2 Sat: submissions'),
        ('quizcomp', 'main', 'Sat', 'improvement_actions', 'QuizComp Sat: improve req.'),
    ]
    data = pd.read_csv(source/'cue_validity_and_utilization.csv')
    selected = data.set_index(['application', 'task', 'dimension', 'feature']).loc[
        [item[:4] for item in selection]]
    aligned = selected[['validity', 'human', 'llm']].mul(np.sign(selected.validity), axis=0)
    if aligned.isna().any().any():
        raise ValueError('Selected cue correlations are incomplete. Re-run reproduce.py to include the held-out duration correlations.')
    fig = plt.figure(figsize=(297.381500/72, 138.306870/72))
    ax = add_axes(fig, (106.07, 1.44, 291.88, 112.32))
    y = np.arange(len(selection))
    ax.hlines(y, aligned.min(axis=1), aligned.max(axis=1), color=GRAY, linewidth=2)
    for name, marker, fill in [('validity', 's', '#9a9a9a'), ('human', 'o', 'white'), ('llm', 'D', 'black')]:
        ax.plot(aligned[name], y, linestyle='none', marker=marker, markerfacecolor=fill,
                markeredgecolor='black', markeredgewidth=.75, markersize=3.6)
    ax.set_xlim(0, .9)
    ax.set_ylim(-.3, 6.3)
    ax.set_xticks(np.arange(0, 1, .1))
    ax.set_yticks(y, [item[4] for item in selection])
    ax.set_xlabel('|ρ| with the behavioral cue', fontsize=7.5, labelpad=3)
    ax.tick_params(length=3.5, width=.6, pad=3, labelsize=6.5)
    ax.tick_params(axis='y', labelsize=7)
    ax.spines[['top', 'right']].set_visible(False)
    save(fig, destination, 'fig6_plot_')


def main(source, destination, permutations=5000):
    destination.mkdir(parents=True, exist_ok=True)
    data = pd.read_csv(source/'agreement_ranks_and_gains.csv')
    index = pd.MultiIndex.from_tuples([(*cell, dim) for cell in CELL_KEYS for dim in DIM_KEYS])
    indexed = data.set_index(['group', 'application', 'task', 'dimension'])
    panels = {group: indexed.loc[group].reindex(index) for group in GROUPS}
    with plt.rc_context(STYLE):
        agreement(panels, destination)
        permutation_map(panels, destination, permutations)
        gain(panels['human'], destination, 'human', 'Human evaluators')
        gain(panels['llm'], destination, 'llm', 'LLM evaluators (5 models)')
        cue_plot(source, destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--permutations', type=int, help='Override the permutation count printed in the heatmap')
    args = parser.parse_args()
    metadata = args.input/'analysis_settings.json'
    if args.permutations is not None:
        count = args.permutations
    elif metadata.is_file():
        count = json.loads(metadata.read_text())['resamples']
    else:
        count = 5000
    main(args.input, args.output, count)
