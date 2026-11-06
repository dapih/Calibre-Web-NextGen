"""Disposable authenticated web edit used by the M0 coordinator proof."""
import io
import os
import pathlib
import time
import re
import sys
import requests

port, book_id, title, log_path = sys.argv[1:]
sys.stdout = open(log_path, 'a', encoding='utf-8', buffering=1)
sys.stderr = sys.stdout
session = requests.Session()
session.trust_env = False
base = 'http://127.0.0.1:' + port
page = session.get(base + '/login', timeout=10, headers={'Accept':'*/*'})
page.raise_for_status()
match = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
if not match:
    raise RuntimeError('login form did not expose its CSRF token')
token = match.group(1)
result = session.post(base + '/login', timeout=10, allow_redirects=False,
                      data={'username':'admin','password':os.environ['M0_PROOF_PASSWORD'],'csrf_token':token})
if result.status_code not in (302, 303):
    raise RuntimeError('fixture login failed: HTTP ' + str(result.status_code))
result = session.post(base + '/ajax/xchange', timeout=30,
                      headers={'X-CSRFToken':token}, json={'xchange':[int(book_id)]})
result.raise_for_status()
if result.json().get('success') is not True:
    raise RuntimeError('web title/author swap did not report success')
result = session.post(base + '/ajax/editselectedbooks', timeout=30,
                      headers={'X-CSRFToken':token}, json={'selections':[int(book_id)],'title':title})
result.raise_for_status()
if result.json().get('success') is not True:
    raise RuntimeError('web metadata edit did not report success')
ready, go = pathlib.Path(os.environ['M0_COVER_READY']), pathlib.Path(os.environ['M0_COVER_GO'])
ready.touch()
deadline = time.monotonic() + 30
while not go.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError('cover synchronization did not arrive')
    time.sleep(.05)
from PIL import Image
image = io.BytesIO()
Image.new('RGB', (32, 48), (32, 100, 170)).save(image, 'PNG')
result = session.post(base + '/api/v1/books/' + book_id + '/cover', timeout=30,
                      headers={'X-CSRFToken':token},
                      files={'file':('fixture-cover.png', image.getvalue(), 'image/png')})
result.raise_for_status()
if result.json().get('ok') is not True:
    raise RuntimeError('web cover edit did not report success')
maintenance_ready = pathlib.Path(os.environ['M0_MAINT_READY'])
maintenance_go = pathlib.Path(os.environ['M0_MAINT_GO'])
maintenance_queued = pathlib.Path(os.environ['M0_MAINT_QUEUED'])
maintenance_done = pathlib.Path(os.environ['M0_MAINT_DONE'])
maintenance_ready.touch()
deadline = time.monotonic() + 30
while not maintenance_go.exists():
    if time.monotonic() >= deadline: raise TimeoutError('maintenance synchronization did not arrive')
    time.sleep(.05)
before = session.get(base+'/api/v1/tasks',timeout=10).json()['items']
ids = {item['task_id'] for item in before}
result = session.post(base+'/metadata_backup',timeout=10,headers={'X-CSRFToken':token})
result.raise_for_status()
maintenance_queued.touch()
deadline = time.monotonic() + 30
while True:
    items = session.get(base+'/api/v1/tasks',timeout=10).json()['items']
    added = [item for item in items if item['task_id'] not in ids]
    if any(item['stat'] in (1,4,5) for item in added): raise RuntimeError('metadata maintenance failed')
    if any(item['stat'] == 3 for item in added): break
    if time.monotonic() >= deadline: raise TimeoutError('metadata maintenance did not finish')
    time.sleep(.1)
maintenance_done.touch()
convert_ready = pathlib.Path(os.environ['M0_CONVERT_READY'])
convert_go = pathlib.Path(os.environ['M0_CONVERT_GO'])
convert_queued = pathlib.Path(os.environ['M0_CONVERT_QUEUED'])
convert_done = pathlib.Path(os.environ['M0_CONVERT_DONE'])
convert_ready.touch()
deadline = time.monotonic() + 30
while not convert_go.exists():
    if time.monotonic() >= deadline: raise TimeoutError('conversion synchronization did not arrive')
    time.sleep(.05)
result = session.post(base+'/api/v1/books/'+book_id+'/convert',timeout=30,headers={'X-CSRFToken':token},
                      json={'from':'EPUB','to':'KEPUB'})
result.raise_for_status()
task_id = str(result.json()['task_id'])
convert_queued.touch()
deadline = time.monotonic() + 90
while True:
    items = session.get(base+'/api/v1/tasks',timeout=10).json()['items']
    task = next((item for item in items if str(item['task_id']) == task_id), None)
    if task and task['stat'] in (1,4,5): raise RuntimeError('book conversion failed: ' + str(task.get('error')))
    if task and task['stat'] == 3: break
    if time.monotonic() >= deadline: raise TimeoutError('book conversion did not finish')
    time.sleep(.1)
convert_done.touch()
print('Authenticated real web metadata, cover, maintenance and conversion operations completed.')
