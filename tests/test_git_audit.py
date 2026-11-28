import subprocess
import unittest
from unittest.mock import patch

import git_audit
import git_sync


class GitAuditTest(unittest.TestCase):
    def test_native_attempts_include_results_without_credentials(self):
        sha='a'*40
        ref='refs/heads/main'
        failure=subprocess.CompletedProcess([],1,'','Failed to connect https://auth:secret@example.invalid')
        with git_audit.capture() as actions, patch.object(git_sync.subprocess,'run',side_effect=[
                failure, subprocess.TimeoutExpired('git',5),
                subprocess.CompletedProcess([],0,sha+'\t'+ref+'\n','')]):
            result,count=git_sync.publish_verified('.', 'https://auth:secret@example.invalid', sha+':main')
        self.assertEqual((result.returncode,count),(0,2))
        self.assertEqual([entry.split(' · ')[0] for entry in actions],['git push','git push','git ls-remote'])
        self.assertIn('connection failed',actions[0])
        self.assertIn('timed out',actions[1])
        self.assertTrue(all('Native publisher · Sandbox: Unknown' in entry for entry in actions))
        self.assertNotIn('secret',' '.join(actions))

    def test_git_worker_context_uses_execution_metadata(self):
        item=dict(command='git -c http.extraHeader="Authorization: Bearer secret" push origin feature',
                  status='failed',exitCode=1,aggregatedOutput='authentication failed private-token')
        for profile,approved,expected in (
            ('scrum-worker',False,'Sandboxed worker · Sandbox: active'),
            ('scrum-worker',True,'Approved execution outside the sandbox · Sandbox: inactive'),
            (':danger-full-access',False,'Full access worker · Sandbox: inactive'),
            (None,False,'Unknown · Sandbox: Unknown'),
        ):
            with self.subTest(profile=profile,approved=approved):
                action,=git_audit.worker(item,profile,approved)
                self.assertIn(expected,action)
                self.assertIn('git push · failed (shell exit 1), access denied',action)
                self.assertNotIn('secret',action)
                self.assertNotIn('private-token',action)

    def test_remote_verification_requires_the_exact_ref(self):
        sha='b'*40
        result=subprocess.CompletedProcess([],0,sha+'\trefs/heads/source\n','')
        self.assertEqual(git_audit.remote_sha(result,'refs/heads/source'),sha)
        self.assertEqual(git_audit.remote_sha(result,'refs/heads/target'),'')

    def test_claude_tool_results_keep_unknown_sandbox_status(self):
        recovery=git_audit.ClaudeRecovery(1,2,3)
        with patch.object(git_audit,'save_worker') as save:
            recovery({'message':{'content':[{'type':'tool_use','name':'Bash','id':'tool',
                                           'input':{'command':'git ls-remote origin'}}]}})
            recovery({'message':{'content':[{'type':'tool_result','tool_use_id':'tool'}]}})
        save.assert_called_once_with(1,2,3,['git ls-remote · completed · Unknown · Sandbox: Unknown'])
