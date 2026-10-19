import contextlib
import errno
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch,Mock
import diagnostics
import server

ROOT=Path(__file__).resolve().parents[1]


class DiagnosticsTest(unittest.TestCase):
    def test_classified_failures_log_safe_correlated_metadata(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder,patch.object(diagnostics,'LOG_PATH',Path(folder)/'diagnostics.log'),contextlib.redirect_stderr(io.StringIO()):
            for error,code in [(PermissionError(errno.EACCES,'claim-token-secret /private/path'),'STORAGE_PERMISSION_DENIED'),(sqlite3.OperationalError('database is locked'),'STORAGE_BUSY'),(sqlite3.OperationalError('attempt to write a readonly database'),'STORAGE_PERMISSION_DENIED'),(sqlite3.IntegrityError('FOREIGN KEY constraint failed secret'),'STORAGE_CONSTRAINT'),(sqlite3.OperationalError('unable to open database file'),'STORAGE_UNAVAILABLE'),(RuntimeError('secret task text'),'INTERNAL_ERROR')]:
                detail=diagnostics.failure(error,'project_open')
                self.assertEqual(detail['code'],code)
                self.assertTrue(detail['next_step'])
                event=json.loads(diagnostics.LOG_PATH.read_text().splitlines()[-1])
                self.assertEqual(event['id'],detail['diagnostic_id'])
                self.assertEqual(event['exception_type'],type(error).__name__)
            log=diagnostics.LOG_PATH.read_text()
            self.assertNotIn('secret',log)
            self.assertNotIn('/private/path',log)

    def test_unwritable_log_does_not_hide_original_error(self):
        with patch.object(diagnostics,'RotatingFileHandler',side_effect=PermissionError()),contextlib.redirect_stderr(io.StringIO()) as stderr:
            result=diagnostics.failure(PermissionError(),'project_open')
        self.assertEqual(result['code'],'STORAGE_PERMISSION_DENIED')
        self.assertIn('unavailable',result['diagnostic_log'])
        self.assertEqual(json.loads(stderr.getvalue())['id'],result['diagnostic_id'])

    def test_web_project_listing_returns_diagnostics_instead_of_dropping_connection(self):
        handler=server.Handler.__new__(server.Handler)
        handler.path='/api/projects'
        handler.reply=Mock()
        detail={'code':'STORAGE_PERMISSION_DENIED','message':'No access'}
        with patch.object(server,'run',side_effect=PermissionError()),patch.object(server,'failure',return_value=detail):
            handler.do_GET()
        handler.reply.assert_called_once_with(500,{'error':'No access','diagnostic':detail})
