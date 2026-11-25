# Short-paper analysis

The five supplied vector originals are in [figures/reference](../figures/reference/). The manuscript PDF is the source for the assembled figures in [figures](../figures/). `reproduce.py` implements the paper’s protocol using the repository’s database adapters and SQL-derived trace features. `plot.py` draws five standalone plots from its CSVs and writes PDF and 300-DPI PNG versions.

| Figure | Generated asset | Numerical output |
| --- | --- | --- |
| 1a | `fig1_panels_column_print` | `near` and `oracle_near` in `agreement_ranks_and_gains.csv` |
| 1b | `fig1_panels_column_print` | `individual`, `individual_p`, `individual_q` in the same file |
| 2 | `fig3p_permutation_map` | `rho`, `rho_p`, `rho_q` in the same file |
| 3 | `fig9_human_print`, `fig9_llm_print` | `total`, `population`, `individual`, their inference columns, and bootstrap intervals in the same file |
| 4 | `fig6_plot_` | `validity`, `human`, and `llm` in `cue_validity_and_utilization.csv` |

The pipeline averages per-round human metrics and per-model LLM metrics with equal weights. Model-specific missing predictions are omitted for the affected actor/model cell. MAE under random pairing is the mean over all admissible cross-pairings. Shared actor permutations drive rank and individual-gain tests. The actor bootstrap shares indices across estimators, and the no-trace bootstrap resamples independent ratings within each model. The reconstruction uses percentile intervals and a two-sided centered-bootstrap test for total and population gain, followed by BH correction. The paper does not specify how the latter bootstrap p-values were computed. Cue validity uses Spearman correlation and separate BH families for 120 primary and 48 exploratory cue–dimension comparisons. `MD`, `Cont`, and `TD` in the CSV correspond to Men, Ctl, and Tmp in the PDF.

Run `python analysis/plot.py results runs/figures` from the package root to draw the shipped results. To render a fresh analysis, use that run’s output directory in place of `results`. The plotter uses TeX Gyre Termes when installed and falls back to Liberation Serif or DejaVu Serif. The originals preserve the exact manuscript marks and typesetting. The data renderer uses the rerun CSV values and significance tests recorded below.

The cue panel includes descriptive associations between evaluator ratings and held-out duration. Its `visible` field remains false because evaluators had no access to the recorded duration.

The checked run used Python 3.11.16, NumPy 1.26.4, pandas 3.0.6, SciPy 1.17.1, and Matplotlib 3.11.2. Local evaluator startup tests used Streamlit 1.65.0.
