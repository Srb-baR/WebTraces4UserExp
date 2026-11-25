"""Verify the packaged SQLite files and their manifest."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
DATA = ROOT/'code/report/data'
MANIFEST = DATA/'manifest.json'


def inventory():
    for path in sorted(DATA.rglob('*')):
        if path.suffix not in {'.sqlite', '.db'}:
            continue
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError(f'SQLite integrity check failed for {path.relative_to(ROOT)}')
        yield {'path': path.relative_to(DATA).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest}


def verify_prompts():
    count = 0
    for path in sorted((ROOT/'prompts').glob('*.json.gz')):
        application, condition = path.name.removesuffix('.json.gz').split('-', 1)
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            archive = json.load(stream)
        for digest, messages in archive['messages'].items():
            payload = json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()
            expected_digest = archive.get('reviewer_digests', {}).get(digest, digest)
            if hashlib.sha256(payload).hexdigest() != expected_digest:
                raise ValueError(f'Prompt digest mismatch in {path.name}')
        suffix = '__no_trace' if condition == 'no-trace' else ''
        for model, sessions in archive['predictions'].items():
            db_path = DATA/'observed'/'LLM evaluators'/application/(model+suffix+'.sqlite')
            with sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True) as db:
                expected = sorted(db.execute('SELECT source_session_id, run, trace_sha256 FROM predictions'))
            actual = sorted((session, int(run), digest) for session, runs in sessions.items()
                            for run, digest in runs.items() if digest in archive['messages'])
            if expected != actual:
                raise ValueError(f'Incomplete prompt archive for {application}/{condition}/{model}')
            count += len(actual)
    if len(list((ROOT/'prompts').glob('*.json.gz'))) != 6 or not count:
        raise ValueError('Expected prompt archives for all three applications and both conditions')
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-manifest', action='store_true', help='Record checksums for a new export')
    args = parser.parse_args()
    files = list(inventory())
    if args.write_manifest:
        MANIFEST.write_text(json.dumps(files, indent=2)+'\n')
    elif files != json.loads(MANIFEST.read_text()):
        raise SystemExit('The database files differ from the packaged manifest.')
    else:
        print(f'Verified {len(files)} SQLite databases and {verify_prompts()} archived prediction prompts.')
