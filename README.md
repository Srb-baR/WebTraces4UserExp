# WebTraces4UserExp

Reproduction materials for **What Web Traces Reveal and Miss About User Experience** (WWW ’27 short paper). The study compares actor self-reports with human and LLM evaluator ratings in TestReco, QuizComp, and two consecutive WebArena tasks. The analysis uses 55 TestReco actors, 65 QuizComp actors, and 53 WebArena actors (106 task surveys).

## Start here

From this directory, use Python 3.11 or later.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env

python verify_inputs.py
python analysis/reproduce.py --resamples 5000 --seed 42
python analysis/plot.py runs/analysis runs/analysis/figures
```

The analysis reads the SQLite databases locally. It makes no API calls and needs no credentials. Generated files go to `runs/`. The default rerun uses the balanced evaluator files in the main data directories. Pass `--observed` to reproduce the shipped [results](results/) from the observed records. The [database notes](BALANCED_DATABASES.md) give the row counts and duplicate IDs. The renderer produces five standalone assets with the same filenames and page dimensions as the [supplied figure PDFs](figures/reference/). The [four assembled figures from the manuscript](figures/) are also available. The [replication notes](analysis/README.md) describe the tests and differences between the submitted figures and the regenerated outputs.

## Human evaluator studies

| Application | Time-hidden replay used in the paper | Description-only control used in the paper | Timing-visible replay |
| --- | --- | --- | --- |
| TestReco | [Source](code/TestReco/Simulation_app/app.py) | [Source](code/TestReco/Simulation_app_no_trace/app.py) | [Source](code/TestReco/Simulation_app_full_trace/app.py) |
| QuizComp | [Source](code/QuizComp/Simulation_app/app.py) | [Source](code/QuizComp/Simulation_app_no_trace/app.py) | [Source](code/QuizComp/Simulation_app_full_trace/app.py) |
| WebArena | [Source](code/reconstructed_Webarena/app.py) | [Source](code/no_trace_webarena/app.py) | [Source](code/reconstructed_Webarena_full_trace/app.py) |

```sh
python launch_study.py testreco trace --port 8521
python launch_study.py quizcomp no-trace --port 8522
python launch_study.py webarena timing-visible --port 8523
```

Run each server in its own terminal. Each application accepts `trace` or `no-trace`. Open the printed URL for the consent screen and use a different dummy `PROLIFIC_PID` to start a fresh preview. Responses go to `runs/human/<application>/<variant>/`. 

To recruit a new cohort, deploy the selected app over HTTPS with its own output database and configure its study ID and completion code. The Prolific entry link uses `?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}`. Set `PROLIFIC_REQUIRED=1` for TestReco and QuizComp description-only deployments. For WebArena, set `PROLIFIC_STUDY_ID` in the server environment. The bundled launcher uses loopback and a local completion code for review. Live recruitment, historical assignment URLs, and the original hosted services were outside local verification.

## LLM evaluators and prompts

The [prompt archive](prompts/) contains the recorded system and user messages linked to every saved prediction. One WebArena message has a participant identifier redacted. The rerun CLI uses those messages by default. Pass `--rebuild-prompts` to exercise the included source templates. The [runner](code/survey_trace/runner.py) contains the full-trace system and role prompts, questionnaire mappings, response parser, and OpenRouter integration. [Trace adapters](code/survey_trace/traces.py) build the evidence from actor data and withhold timestamps and self-reports. [Description-only prompts](code/survey_trace/descriptions.py) provide application and task context. WebArena trace prompts use recorded actions as text. Human WebArena replays also show visual evidence.

```sh
# Export prompts without contacting OpenRouter.
python run_llm.py testreco trace deepseek-v3.2 --preview
python run_llm.py webarena no-trace deepseek-v3.2 --preview

# Start a new paid run after setting OPENROUTER_API_KEY in .env.
python run_llm.py quizcomp trace qwen3-235b
python run_llm.py webarena no-trace qwen3-235b --runs 10
```

The aliases are `deepseek-v3.2`, `llama-4-scout`, `mistral-small-3.2`, `gpt-oss-120b`, and `qwen3-235b`. To cover all applications, run each alias for each of `testreco`, `quizcomp`, and `webarena` in both conditions. The trace default is one prediction per task survey per model. New paid no-trace batches default to 65 runs per model and application. Use `--runs` to override this count. See the saved panel counts in the [no-trace inventory](code/report/data/no_trace_inventory.csv). The wrapper uses temperature 0.5. New generations depend on the serving endpoint and sampling. Use `analysis/reproduce.py --observed` to reproduce the original paper analysis from the saved predictions. Offline previews simulate the request and do not verify current provider capacity or availability. Preview request JSON and newly generated answers are stored separately in `runs/prompts/` and `runs/llm/`.

## Data and application code

- [Actor databases](code/report/data/actors/) contain the original survey scores, recorded trace data, cohort links, and the question or activity outcome records needed for analysis and replay.
- [Human evaluator databases](code/report/data/human%20evaluators/) contain 10 TestReco, 4 QuizComp, and 4 WebArena replay rounds and one description-only control database per application. The main files contain balanced rating rows. The observed database archive preserves the original rating and assignment tables used for paper reproduction.
- [LLM evaluator databases](code/report/data/LLM%20evaluators/) contain five balanced model panels per application and condition. The `__no_trace` suffix identifies description-only predictions. The observed archive retains saved predictions, usage data, and prompt digests.
- [Original TestReco application](code/TestReco/real_user_study/) includes its recommendation policies, orchestrator and prompts, and question bank.
- [Original QuizComp application](code/QuizComp/app/) includes the composition backend, math dataset and model weights. Its [actor UI](code/QuizComp/quizcomp_ui/) is separate from the replay.
- [Original WebArena application](code/Webarena/) includes the study UI, task and survey logic, and recording code. Its live mode requires independently provisioned shopping and forum services. The reviewer replay is local and uses the archived evidence.

The actor SQLite copies remove account credentials, direct Prolific identifiers and survey comments while retaining interaction content and timestamps for analysis and replay. Original webpages, typed task text and screenshots can still contain identifying content. Treat these as controlled reviewer materials until the study team has reviewed the retained traces and visuals against participant consent and anonymization requirements. See the [data documentation](code/report/data/README.md) for the retained fields and matching rules, and [deployment notes](ACTOR_APPS.md) for the original applications.

The WebArena database contains recorded page evidence and is the largest file in the package. Include it and the replay assets when distributing the folder.
