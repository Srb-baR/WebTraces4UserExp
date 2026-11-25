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

## Verification against the manuscript

The checks below refer to `reproduce.py --observed`. The default reads the balanced evaluator databases in place. The resulting control estimates can differ from the figures in the manuscript. See [balanced database notes](../BALANCED_DATABASES.md).

- 226 actor task surveys and 23,280 dimension-level evaluations after 7 audited response exclusions.
- Near Agreement ranges round to 0.51–0.82 for human evaluators and 0.36–0.96 for LLM evaluators. Population-mean correlations for human evaluations are .922 with traces and .901 without traces. The trace slope is .480.
- The pooled LLM row retains the same four corrected rank-correlation cells. Human evaluation retains none. The centered bootstrap gives 11 significantly negative total-gain cells for LLMs, compared with 9 in the manuscript, and recovers the two positive cells.
- The reconstructed rank test gives Qwen3 four adjusted-significant cells, compared with five in the manuscript. DeepSeek retains one adjusted-significant cell in this rerun. The WebArena 2 LLM satisfaction adjusted p-value is about .010 here and .014 in the manuscript.
- Twelve primary cue associations pass BH. Figure 4’s principal point estimates reproduce to two decimals. The exploratory WebArena definitions count recorded keydown and scroll events, distinct page URLs, and returns to an earlier page URL after visiting a different one. Distinct page URLs associate with WebArena 2 satisfaction in this implementation (q = .026), whereas the manuscript reports no discovery in that exploratory family. The exact original counting definition was unavailable.

The per-cell and per-model rating spread and modal response fractions are recorded in `llm_rating_dispersion.csv`.

The original PDF panels and regenerated panels are both included so the comparison remains inspectable. The archived inputs are read-only during the analysis. A run can be repeated at a different seed by passing `--seed`.

The checked run used Python 3.11.16, NumPy 1.26.4, pandas 3.0.6, SciPy 1.17.1, and Matplotlib 3.11.2. Local evaluator startup tests used Streamlit 1.65.0.
