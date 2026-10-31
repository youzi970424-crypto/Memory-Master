#!/usr/bin/env python3
"""Exercise real save/chat HTTP routes in isolated DBs; retain evidence for review."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
TEMP = Path(tempfile.mkdtemp(prefix="pm-week-audit-"))
os.environ['MEMORY_MASTER_DB'] = str(TEMP / 'initial.db')
spec = importlib.util.spec_from_file_location('audit_server', ROOT / 'services/api/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
tree = ast.parse((ROOT / 'evals/run_pm_week_eval.py').read_text())
cases = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CASES' for t in n.targets))
memories = dict(re.findall(r'^#### (M\d+)｜[^\n]+\n\n(.+?)(?=\n\n)', SOURCE.read_text(), re.M | re.S))
assert len(memories) == 18
source = sqlite3.connect(f'file:{ROOT / "storage/memory_master.db"}?mode=ro', uri=True)
settings = dict(source.execute('SELECT key,value FROM settings'))
source.close()
allowed = {'provider', 'base_url', 'api_key', 'model', 'embedding_model', 'enabled'}
settings = {k:v for k,v in settings.items() if k in allowed}
manifest = {'started_at': datetime.now().astimezone().isoformat(), 'source': str(SOURCE), 'temp_dir': str(TEMP),
            'config': {k:v for k,v in settings.items() if k not in {'api_key', 'base_url'}},
            'hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'services/api/server.py', ROOT/'src/App.tsx', ROOT/'prompts/system_prompt.md']},
            'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'scope': 'HTTP API; quick-add parse/save and form save payloads reproduced from App.tsx; no GUI coverage',
            'clock': 'server.now and question date set to business date; wall-clock age weighting unchanged',
            'index_readiness': 'save commit and GET exact-content verification; retrieval builds any configured embeddings synchronously',
            'results': [], 'saves': []}

def persist():
    (OUT/'raw.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

context = {}
original_call = server.call_model
def captured_call(config, payload):
    context['model_payload'] = json.loads(json.dumps(payload))
    return original_call(config, payload)
server.call_model = captured_call
class QuietHandler(server.Handler):
    def log_message(self, *args):
        pass
httpd = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{httpd.server_port}'
def request(path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(base+path, data=data, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=240) as response:
        return json.load(response)

try:
    for entry in ['form', 'quick']:
        server.DB_PATH = TEMP / f'{entry}.db'
        c = sqlite3.connect(server.DB_PATH)
        c.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.executemany('INSERT INTO settings VALUES (?,?)', [*settings.items(), ('seed_initialized','1')])
        c.commit()
        c.close()
        folders = request('/api/folders')['items']
        saved = []
        for case in cases:
            cid, date, until, manual, query, *_ = case
            day = int(re.search(r'9月(\d+)', date).group(1))
            server.now = lambda day=day: f'2026-09-{day:02d}T18:00:00+08:00'
            for mid, content in memories.items():
                if mid > until or mid in saved:
                    continue
                payload = {'id': mid, 'title': content.split('。')[0][:80], 'content': content,
                           'type': '经验型', 'source': '手动添加', 'keywords':['手动添加'], 'folder_id': folders[0]['id']}
                record = {'entry':entry, 'id':mid, 'original':content}
                if entry == 'quick':
                    parsed = request('/api/memories/parse', {'content':content, 'title':'', 'folders':[f['name'] for f in folders]})
                    draft = parsed['draft']
                    payload.update(title=draft['title'] or payload['title'], content=draft['content'].strip() or content,
                                   keywords=['手动添加', *[k for k in draft['keywords'] if k.strip() and k != '手动添加'][:4]],
                                   folder_id=next((f['id'] for f in folders if f['name'].lower() == draft['folder'].strip().lower() or f['id'].lower() == draft['folder'].strip().lower()),None))
                    record['parse'] = parsed
                record['saved'] = request('/api/memories', payload)
                record['content_identical'] = record['saved']['content'] == content
                manifest['saves'].append(record)
                saved.append(mid)
                persist()
                print(f'SAVED {entry} {mid} exact={record["content_identical"]}', flush=True)
            actual = request('/api/memories')['items']
            assert set(m['id'] for m in actual) == set(saved)
            assert all(m['content'] == next(s['saved']['content'] for s in manifest['saves'] if s['entry']==entry and s['id']==m['id']) for m in actual)
            # Restore the identical pre-query DB before each stability repeat.
            snapshot = TEMP / f'{entry}-{cid}.db'
            c = sqlite3.connect(server.DB_PATH)
            backup = sqlite3.connect(snapshot)
            c.backup(backup)
            backup.close()
            c.close()
            for repeat in range(1, 4 if cid in {'T07','T10','T13','T16'} else 2):
                if repeat > 1:
                    src = sqlite3.connect(snapshot)
                    dst = sqlite3.connect(server.DB_PATH)
                    src.backup(dst)
                    dst.close()
                    src.close()
                context.clear()
                body = {'query':f'本次测试视当前日期为{date}。{query}', 'manual_memory_ids':manual, 'conversation_history':[]}
                started = time.monotonic()
                response = request('/api/chat', body)
                row = {'entry':entry, 'case':cid, 'repeat':repeat, 'date':date, 'saved_ids':list(saved),
                       'request':body, 'response':response, 'model_input':context.get('model_payload'),
                       'elapsed_seconds':round(time.monotonic()-started,2)}
                manifest['results'].append(row)
                persist()
                print(f'RESULT {entry} {cid}/{repeat} {response["meta"]["generation_mode"]} {row["elapsed_seconds"]}s selected={response["memory_usage"]["selected_memory_ids"]}', flush=True)
    manifest['finished_at'] = datetime.now().astimezone().isoformat()
    persist()
finally:
    httpd.shutdown()
