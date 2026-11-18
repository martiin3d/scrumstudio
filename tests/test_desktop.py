import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request, urlopen

from desktop import desktop_backend
from store import run


def call(url, action, payload):
    request = Request(
        f'{url}/api/{action}', data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'Origin': url},
    )
    with urlopen(request, timeout=3) as response:
        return json.load(response)


class DesktopBackendTest(unittest.TestCase):
    def test_serves_registry_and_persists_board_actions(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ):
            os.environ.pop('SCRUM_REGISTRY', None)
            os.environ['SCRUM_DB'] = str(Path(temp) / 'desktop.db')
            project = run('project_create', {'name': 'Desktop fixture'})['id']
            with desktop_backend(0) as url:
                self.assertTrue(url.startswith('http://127.0.0.1:'))
                with urlopen(f'{url}/?desktop=1', timeout=3) as response:
                    self.assertIn(b'Kanban board', response.read())
                self.assertEqual(call(url, 'projects', {})[0]['id'], project)
                task = call(url, 'task_create', {
                    'project': project, 'title': 'Desktop task',
                    'description': 'Exercise the desktop HTTP backend.',
                    'acceptance_criteria': ['The web app can read the task.'],
                })
                board = call(url, 'web_refresh', {'project': project, 'page': 'Board'})
                self.assertEqual(board['data']['tasks'][0]['id'], task['id'])
            self.assertEqual(run('task_get', {'project': project, 'id': task['id']})['title'], 'Desktop task')
            with self.assertRaises(URLError):
                urlopen(url, timeout=1)
