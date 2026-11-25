"""Read task assignments only; historical recordings are not needed at runtime."""
import json
from pathlib import Path

TASKS = Path(__file__).resolve().parent / 'data/tasks.json'


def _catalog():
    return json.loads(TASKS.read_text())


def available_pairs():
    """Preserve the original eligible, ordered two-task assignments."""
    return _catalog()['pairs']


def load(sid):
    for task in _catalog()['tasks']:
        if task['id'] == sid:
            return task
    raise ValueError('Task description not found.')
