import importlib.util
from pathlib import Path
import unittest
import tempfile
import subprocess
from unittest.mock import Mock,patch

spec=importlib.util.spec_from_file_location('unity_verify',Path(__file__).resolve().parents[1]/'scripts/unity_verify.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class UnityVerificationTests(unittest.TestCase):
    def test_license_warning_does_not_hide_later_compile_failure(self):
        result=runner.classify('Licensing initialization failed\nAssets/Test.cs(1): error CS0246: Missing type',None,1)
        self.assertEqual(result['kind'],'compile_error')

    def test_success_requires_executed_tests_and_successful_process(self):
        xml='<test-run total="3" passed="3" failed="0" result="Passed"/>'
        self.assertTrue(runner.classify('Channel doesn\'t exist',xml,0)['passed'])
        self.assertFalse(runner.classify('',xml,1)['passed'])
        self.assertFalse(runner.classify('',xml,0,True)['passed'])
        self.assertFalse(runner.classify('','<test-run total="3" passed="0" failed="0" result="Passed"/>',0)['passed'])

    def test_missing_invalid_and_failed_reports_never_pass(self):
        for xml in [None,'<bad','<test-run total="1" passed="0" failed="1" result="Failed"/>']:
            self.assertFalse(runner.classify('',xml,0)['passed'])

    def test_access_failure_requires_actual_review_not_license_changes(self):
        result=runner.classify('Failed to connect to channel: LicenseClient-user',None,0,True)
        self.assertEqual(result['kind'],'environment_access')
        self.assertIn('once',result['next_step'])

    def test_timeout_terminates_only_owned_editor_and_uses_unique_reports(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            root=Path(folder);(root/'ProjectSettings').mkdir()
            (root/'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: fixture')
            editor=root/'Unity.exe';editor.touch()
            child=Mock(returncode=-9)
            child.wait.side_effect=[subprocess.TimeoutExpired('Unity',30),None,None]
            with patch.object(runner.subprocess,'Popen',return_value=child) as start:
                first=runner.run(editor,root,timeout=30)
                second=runner.run(editor,root,timeout=30)
            child.kill.assert_called_once()
            self.assertTrue(first['timed_out'])
            self.assertFalse(first['passed'])
            self.assertNotEqual(first['report'],second['report'])
            self.assertNotIn('-quit',start.call_args.args[0])

    def test_pass_reuse_requires_same_inputs_editor_graphics_and_report(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            root=Path(folder);(root/'ProjectSettings').mkdir();(root/'Assets').mkdir()
            (root/'ProjectSettings/ProjectVersion.txt').write_text('fixture')
            source=root/'Assets/Feature.cs';source.write_text('original')
            editor=root/'Unity.exe';editor.touch()
            def launch(command,**kwargs):
                Path(command[command.index('-testResults')+1]).write_text('<test-run total="2" passed="2" failed="0" result="Passed"/>')
                return Mock(returncode=0)
            with patch.object(runner.subprocess,'Popen',side_effect=launch) as spawn:
                first=runner.run(editor,root,test_filter='Feature',reuse=True)
                second=runner.run(editor,root,test_filter='Feature',reuse=True)
                self.assertTrue(second['reused']);self.assertEqual(spawn.call_count,1)
                self.assertIn('-nographics',spawn.call_args.args[0])
                Path(second['report']).write_text('damaged')
                runner.run(editor,root,test_filter='Feature',reuse=True)
                self.assertEqual(spawn.call_count,2)
                source.write_text('changed');runner.run(editor,root,test_filter='Feature',reuse=True)
                self.assertEqual(spawn.call_count,3)
                runner.run(editor,root,test_filter='Feature',graphics='on',reuse=True)
                self.assertEqual(spawn.call_count,4);self.assertNotIn('-nographics',spawn.call_args.args[0])
                editor.write_text('new version');runner.run(editor,root,test_filter='Feature',graphics='on',reuse=True)
                self.assertEqual(spawn.call_count,5)
                Path(first['report']).write_text('damaged')
                source.write_text('original');editor.write_text('')
                runner.run(editor,root,test_filter='Feature',reuse=True)
                self.assertEqual(spawn.call_count,6)
            events=(root/'.scrum/local/verification-events.jsonl').read_text().splitlines()
            self.assertEqual(len(events),7)

    def test_visual_filters_and_full_suite_enable_graphics_and_diagnose_failure(self):
        result=runner.classify('RenderTexture.Create failed',None,1)
        self.assertEqual(result['kind'],'graphics_unavailable')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            root=Path(folder);(root/'ProjectSettings').mkdir();(root/'ProjectSettings/ProjectVersion.txt').write_text('fixture')
            editor=root/'Unity.exe';editor.touch()
            with patch.object(runner.subprocess,'Popen',return_value=Mock(returncode=1)) as spawn:
                runner.run(editor,root,test_filter='CaptureTests')
                self.assertNotIn('-nographics',spawn.call_args.args[0])
                runner.run(editor,root)
                self.assertNotIn('-nographics',spawn.call_args.args[0])

    def test_external_mutable_package_disables_reuse(self):
        import sys
        sys.path.insert(0,str(Path(runner.__file__).parent))
        import verification_receipts as receipts
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            root=Path(folder);(root/'Packages').mkdir();(root/'Packages/manifest.json').write_text('{"dependencies":{"local":"file:../external"}}')
            editor=root/'Unity.exe';editor.touch()
            self.assertIsNone(receipts.fingerprint(root,editor,'EditMode','Feature',False))
