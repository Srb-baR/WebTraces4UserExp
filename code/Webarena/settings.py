import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("WEBARENA_DB", str(ROOT / "data/study.sqlite")))
CONFIG_PATH = Path(os.environ.get("WEBARENA_CONFIG", str(ROOT / "config.json")))

def config():
    result = json.loads(CONFIG_PATH.read_text())
    result['prolific_required']=os.environ.get('WEBARENA_PROLIFIC','').lower() in ('1','true','yes') or result.get('prolific_required',False)
    result['completion_code']=os.environ.get('WEBARENA_COMPLETION_CODE',result.get('completion_code',''))
    if result['prolific_required']:
        from prolific import completion_code
        completion_code(result['completion_code'])
    if result.get("mode") not in ("local_replica", "live"):
        raise ValueError("mode must be local_replica or live")
    if result.get("full_tasks"):
        plan=result['full_tasks']
        if not isinstance(plan,list) or len(plan)!=2:
            raise ValueError('full_tasks must contain exactly two complete assignments')
        for task in plan:
            if not all(isinstance(task.get(k),str) and task[k].strip() for k in ('product','rating','forum_path')):
                raise ValueError('Each full task requires product, rating and forum_path')
            if not __import__('re').fullmatch(r'/f/[A-Za-z0-9_]+',task['forum_path']):
                raise ValueError('Invalid full-task forum path')
    if result["mode"] == "live":
        from urllib.parse import urlsplit
        for key in ("shopping_url", "reddit_url"):
            assert urlsplit(result[key]).scheme in ("http", "https")
            assert urlsplit(result[key]).netloc
        return result
    assert result["template_id"] == 101
    assert type(result["rating"]) is int and 1 <= result["rating"] <= 5
    product = next(p for p in result["products"] if p["name"] == result["product"])
    assert any(r["rating"] == result["rating"] for r in product["reviews"])
    assert result["subreddit"] == "games"
    return result

def intent(c):
    if c.get("intent"):
        return c["intent"]
    return (f'Gather the titles of {c["product"]} reviews with {c["rating"]}-star rating '
            f'from OneStopShop, and post them in the games subreddit under the title '
            f'"real user feedback on {c["product"]}"')
