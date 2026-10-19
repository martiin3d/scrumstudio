import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import cli_runtime

ROOT=Path(__file__).resolve().parents[1]


class CliRuntimeTest(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows desktop discovery')
    def test_real_home_runtime_survives_sandbox_localappdata(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder,patch.object(cli_runtime.Path,'home',return_value=Path(folder)),patch.dict(os.environ,{'LOCALAPPDATA':str(Path(folder)/'sandbox'),'SCRUM_CODEX_EXECUTABLE':''}):
            executable=Path(folder)/'AppData/Local/OpenAI/Codex/bin/build/codex.exe'
            executable.parent.mkdir(parents=True);executable.touch()
            self.assertEqual(cli_runtime.codex_executable(),str(executable.resolve()))

    @unittest.skipUnless(os.name=='nt','Windows desktop discovery')
    def test_desktop_runtime_is_preferred_over_stale_path(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder,patch.object(cli_runtime.Path,'home',return_value=Path(folder)),patch.dict(os.environ,{'LOCALAPPDATA':folder,'SCRUM_CODEX_EXECUTABLE':''}),patch.object(cli_runtime.shutil,'which',return_value='old-codex'):
            executable=Path(folder)/'OpenAI/Codex/bin/build/codex.exe'
            executable.parent.mkdir(parents=True);executable.touch()
            self.assertEqual(cli_runtime.codex_executable(),str(executable.resolve()))

    def test_explicit_runtime_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder:
            executable=Path(folder)/'explicit.exe';executable.touch()
            with patch.dict(os.environ,{'SCRUM_CODEX_EXECUTABLE':str(executable)}):
                self.assertEqual(cli_runtime.codex_executable(),str(executable.resolve()))
            with patch.dict(os.environ,{'SCRUM_CODEX_EXECUTABLE':str(executable)+'-missing'}):
                with self.assertRaisesRegex(RuntimeError,'existing Codex'):cli_runtime.codex_executable()

    def test_path_remains_fallback_without_desktop(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder,patch.object(cli_runtime.Path,'home',return_value=Path(folder)),patch.dict(os.environ,{'LOCALAPPDATA':folder,'SCRUM_CODEX_EXECUTABLE':''}),patch.object(cli_runtime.shutil,'which',return_value='path-codex'):
            self.assertEqual(cli_runtime.codex_executable(),'path-codex')
