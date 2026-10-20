import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scrum_setup', ROOT / 'scripts' / 'setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

class SetupTest(unittest.TestCase):
    def test_legacy_skill_repair_preserves_resources_and_is_repeatable(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as temp:
            profile=Path(temp);config=profile/'custom-codex';backup=config/'backups/repair'
            legacy=config/'skills/memory';(legacy/'references').mkdir(parents=True)
            (legacy/'SKILL.md').write_text('See references/detail.md')
            (legacy/'references/detail.md').write_bytes(b'complete\x00resources')
            before=setup.skill_manifest(legacy)
            self.assertEqual(setup.repair_skill('memory',profile,config,backup),'repaired')
            self.assertFalse(legacy.exists())
            self.assertEqual(setup.skill_manifest(profile/'.agents/skills/memory'),before)
            self.assertEqual(setup.skill_manifest(backup/'legacy-skills/memory'),before)
            self.assertEqual(setup.repair_skill('memory',profile,config,backup),'already_canonical')

    def test_repair_preserves_conflicting_canonical_skill_and_legacy(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as temp:
            profile=Path(temp);config=profile/'.codex'
            for root,text in [(config/'skills/memory','legacy'),(profile/'.agents/skills/memory','newer')]:
                root.mkdir(parents=True);(root/'SKILL.md').write_text(text)
            with self.assertRaisesRegex(ValueError,'differs'):setup.repair_skill('memory',profile,config,config/'backups/repair')
            self.assertEqual((config/'skills/memory/SKILL.md').read_text(),'legacy')
            self.assertEqual((profile/'.agents/skills/memory/SKILL.md').read_text(),'newer')

    def test_repair_rejects_traversal_and_links(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as temp:
            profile=Path(temp);config=profile/'.codex'
            with self.assertRaisesRegex(ValueError,'simple skill'):setup.repair_skill('../escape',profile,config,config/'backups/repair')
            legacy=config/'skills/memory';legacy.mkdir(parents=True);(legacy/'SKILL.md').write_text('skill')
            with patch.object(setup.Path,'is_symlink',return_value=True):
                with self.assertRaisesRegex(ValueError,'symlinks'):setup.repair_skill('memory',profile,config,config/'backups/repair')
            self.assertTrue(legacy.exists());self.assertFalse((profile/'.agents/skills/memory').exists())

    def test_portable_registration_and_backup(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'data') as temp:
            profile = Path(temp)
            config = profile / 'custom-codex'
            config.mkdir()
            (config / 'config.toml').write_text('preserved')
            old = profile / '.agents' / 'skills' / 'scrum-chat'
            old.mkdir(parents=True)
            (old / 'SKILL.md').write_text('old skill')
            with patch.object(setup.Path, 'home', return_value=profile), patch.dict(os.environ, {'CODEX_HOME': str(config)}), patch.object(setup.sys, 'argv', ['setup.py']), patch.object(setup.shutil, 'which', return_value='codex'), patch.object(setup.subprocess, 'run') as run:
                setup.main()
            self.assertEqual(run.call_args_list[0].args[0][:3], ['codex','mcp','remove'])
            command = run.call_args_list[1].args[0]
            self.assertEqual(command[:5], ['codex','mcp','add','scrum-studio','--'])
            self.assertEqual(command[-3:], [str(ROOT / 'mcp_server.py'),'--profile','worker'])
            self.assertEqual((old / 'SKILL.md').read_bytes(), (ROOT / 'skills' / 'scrum-chat' / 'SKILL.md').read_bytes())
            self.assertEqual((config / 'config.toml').read_text(), 'preserved')
            backups = list((config / 'backups' / 'scrum-studio').iterdir())
            self.assertEqual((backups[0] / 'skills' / 'scrum-chat' / 'SKILL.md').read_text(), 'old skill')

    def test_missing_cli_stops_before_install(self):
        with patch.object(setup.sys, 'argv', ['setup.py']), patch.object(setup.shutil, 'which', return_value=None), patch.object(setup.Path, 'home') as home:
            with self.assertRaises(SystemExit):
                setup.main()
            home.assert_not_called()

    def test_claude_installs_skills_and_uses_user_stdio_scope(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as temp:
            profile=Path(temp)
            with patch.object(setup.Path,'home',return_value=profile),patch.dict(os.environ,{'CODEX_HOME':str(profile/'.codex')}),patch.object(setup.sys,'argv',['setup.py','--client','claude']),patch.object(setup.shutil,'which',return_value='claude'),patch.object(setup.subprocess,'run') as run:
                setup.main()
            self.assertTrue((profile/'.claude/skills/scrum-work/SKILL.md').exists())
            self.assertIn('--scope',run.call_args_list[1].args[0])
            self.assertIn('user',run.call_args_list[1].args[0])
