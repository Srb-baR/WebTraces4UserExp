# Original actor applications

These entry points are for collecting new interactions. Use the reviewer launcher in the main README to replay completed sessions.

## TestReco

In a dedicated Python environment, install `code/TestReco/Final_Requirements.txt`. From `code/TestReco`, run `PYTHONPATH=. python -m streamlit run real_user_study/app.py`. Set `OPENROUTER_API_KEY` in the server environment for the tool-call orchestrator and `REALUSER_DB_PATH` to a new database. The question bank, tool schema, prompt templates, and saved recommendation policies are under this application directory. Set `PROLIFIC_COMPLETION_CODE` for a new recruitment.

## QuizComp

Install `code/QuizComp/requirements.txt` plus `code/QuizComp/quizcomp_ui/requirements.txt` in a dedicated environment. From `code/QuizComp`, start the backend with `python -m uvicorn app.app:app --port 8000` and the UI with `python -m streamlit run quizcomp_ui/str_app.py`. This working directory provides the math dataset and weights. Set `QUIZCOMP_API_BASE`, `QUIZCOMP_DB_PATH`, and `QUIZCOMP_CODES_JSON` in the server environment for a new deployment. The code allocator reads a private JSON object with a `codes` array. Provide deployment-specific entries to the allocator or use the local preview code.

## WebArena

Install `code/Webarena/requirements.txt`. From `code/Webarena`, start the event and workspace service with `python -m uvicorn server:app --host 127.0.0.1 --port 8766`. In a second terminal, `python -m streamlit run app.py --server.port 8505` opens the included local replica configuration. The original two-task protocol used live, independently provisioned shopping and Reddit-style services. To recreate it, start from `code/Webarena/config.live.example.json` and set `WEBARENA_CONFIG` to a copy with `mode: live`, your site URLs, and two `full_tasks` entries containing product, rating and forum path. Run the bundled event service and configure recording for your own sites before recruiting. Use a new `WEBARENA_DB`. Configure authentication and site accounts in your own environment.
