import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import mcp_selection as selection
import test_storage
from store import run

class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data')
        self.root=Path(self.temp.name);self.home=self.root/'home';self.home.mkdir()
        self.folder=self.root/'project';self.folder.mkdir()
        (self.home/'.codex').mkdir()
        (self.home/'.codex/config.toml').write_text('[mcp_servers.graph]\ncommand="secret-command"\n[mcp_servers.unity]\nurl="https://secret.invalid"\n',encoding='utf-8')
        (self.home/'.claude.json').write_text(json.dumps({'mcpServers':{'graph':{'command':'secret-command','env':{'KEY':'secret-value'}}}}))
        self.env=patch.dict(os.environ,{'CODEX_HOME':str(self.home/'.codex')});self.env.start()
        self.home_patch=patch.object(Path,'home',return_value=self.home);self.home_patch.start()
    def tearDown(self):
        self.home_patch.stop();self.env.stop();self.temp.cleanup()
    def test_plugin_inventory_refresh_is_read_only_and_names_only(self):
        root=self.home/'.codex/plugins/cache/openai-curated-remote/figma/16.0.0'
        (root/'.codex-plugin').mkdir(parents=True)
        (root/'.codex-plugin/plugin.json').write_text(json.dumps({'mcpServers':'./.mcp.json'}))
        (root/'.mcp.json').write_text(json.dumps({'mcpServers':{'figma':{'url':'secret-url','headers':{'key':'secret-token'}}}}))
        with patch('subprocess.run',side_effect=AssertionError('No process discovery')):
            data=selection.listing(self.folder)
        self.assertEqual(data['codex']['plugin_integrations'][0]['name'],'figma')
        self.assertIn('unverified',data['codex']['plugin_integrations'][0]['status'])
        self.assertNotIn('figma',[x['name'] for x in data['codex']['servers_available']])
        self.assertNotIn('secret',json.dumps(data))
        config=self.home/'.codex/config.toml'
        config.write_text(config.read_text()+'\n[plugins."figma@openai-curated-remote"]\nenabled=false\n')
        self.assertEqual(selection.listing(self.folder)['codex']['plugin_integrations'][0]['status'],'disabled')
        (root/'.mcp.json').unlink()
        self.assertEqual(selection.listing(self.folder)['codex']['plugin_integrations'],[])

    def test_plugin_manifest_cannot_read_outside_package(self):
        root=self.home/'.codex/plugins/cache/market/plugin/1'
        (root/'.codex-plugin').mkdir(parents=True)
        (root/'.codex-plugin/plugin.json').write_text(json.dumps({'mcpServers':'../../../../../outside.json'}))
        self.assertEqual(selection.plugin_integrations(),[])

    def test_listing_is_names_only_and_does_not_launch_servers(self):
        with patch('subprocess.run',side_effect=AssertionError('No process discovery')):
            data=selection.listing(self.folder)
        self.assertEqual(data['codex']['mode'],'inherit')
        self.assertIn('graph',[s['name'] for s in data['claude']['servers_available']])
        self.assertNotIn('secret',json.dumps(data))
    def test_codex_selection_disables_others_and_always_keeps_scrum(self):
        selection.save(self.folder,'codex','selected',['graph'])
        options=selection.codex_options(self.folder)
        self.assertIn('mcp_servers.graph.enabled=true',options)
        self.assertIn('mcp_servers.unity.enabled=false',options)
        self.assertIn('mcp_servers.scrum-studio.enabled=true',options)
        self.assertEqual(selection.codex_options(self.root),[])
    def test_claude_selected_and_inherit_and_missing_names(self):
        selection.save(self.folder,'claude','selected',['graph'])
        servers,strict=selection.claude_config(self.folder,{'command':'scrum'})
        self.assertTrue(strict);self.assertEqual(set(servers),{'graph','scrum-studio'})
        self.assertEqual(servers['graph']['env']['KEY'],'secret-value')
        selection.save(self.folder,'claude','inherit',[])
        self.assertFalse(selection.claude_config(self.folder,{})[1])
        selection.save(self.folder,'claude','selected',['removed'])
        data=selection.listing(self.folder)
        self.assertFalse(next(s for s in data['claude']['servers_available'] if s['name']=='removed')['available'])
        with self.assertRaisesRegex(ValueError,'unavailable'):selection.selected('claude',self.folder)
    def test_selection_isolated_by_provider_and_folder(self):
        selection.save(self.folder,'codex','selected',['graph'])
        selection.save(self.folder,'claude','selected',[])
        self.assertEqual(selection.load(self.folder)['codex']['servers'],['graph'])
        self.assertEqual(selection.load(self.root),{})
    def test_malformed_configuration_is_reported_without_details(self):
        (self.home/'.codex/config.toml').write_text('secret malformed value')
        selection.save(self.folder,'codex','selected',[])
        self.assertTrue(selection.listing(self.folder)['codex']['warnings'])
        with self.assertRaisesRegex(ValueError,'unreadable'):selection.codex_options(self.folder)
    def test_claude_project_scope_and_shared_scope(self):
        (self.folder/'.mcp.json').write_text(json.dumps({'mcpServers':{'shared':{'command':'shared'}}}))
        doc=json.loads((self.home/'.claude.json').read_text());doc['projects']={self.folder.as_posix():{'mcpServers':{'local':{'command':'local'}}}}
        (self.home/'.claude.json').write_text(json.dumps(doc))
        servers,_=selection.discover('claude',self.folder)
        self.assertEqual(set(servers),{'graph','shared','local'})
    def test_validation_and_corrupt_policy_fail_closed(self):
        for names in (['x\n'],['x'*129],'graph'):
            with self.assertRaises(ValueError):selection.save(self.folder,'codex','selected',names)
        selection.save(self.folder,'codex','selected',[])
        (self.folder/'.scrum/local/mcp-selection.json').write_text('bad')
        with self.assertRaises(ValueError):selection.codex_options(self.folder)
    def test_claude_command_uses_project_specific_config(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import agent_bridge
        selection.save(self.folder,'claude','selected',['graph'])
        with patch.object(agent_bridge.shutil,'which',return_value='claude'):
            command=agent_bridge.command('claude',self.folder,'task')
        self.assertIn('--strict-mcp-config',command)
        path=Path(next(x.split('=',1)[1] for x in command if x.startswith('--mcp-config=')))
        self.assertTrue(path.is_relative_to(self.folder/'.scrum/local'))
        self.assertEqual(set(json.loads(path.read_text())['mcpServers']),{'graph','scrum-studio'})

class SelectionApiTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    def test_only_human_can_save_and_regular_task_context_is_unchanged(self):
        p,folder=self.project()
        with self.assertRaises(ValueError):run('mcp_servers',dict(project=p,action='update',actor=self.agent,client='codex',mode='selected',servers=[]))
        run('mcp_servers',dict(project=p,action='update',actor=self.human,client='codex',mode='selected',servers=[]))
        self.assertEqual(selection.load(folder)['codex']['mode'],'selected')
        self.assertNotIn('mcp_servers',run('project_settings',dict(project=p)))
