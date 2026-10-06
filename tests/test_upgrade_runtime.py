"""Real isolated installer regression; no macOS service or private user data."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workledger.config import Config


class UpgradeRuntimeTests(unittest.TestCase):
    def test_repeat_install_retains_existing_runtime_and_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve() / 'user home'
            runtime = home / '.local/share/workledger-py/bin/python'
            runtime.parent.mkdir(parents=True)
            runtime.symlink_to(sys.executable)
            cfg = Config(home / 'Library/Application Support/WorkLedger')
            cfg.save({'timezone': 'UTC', 'capture_paused': True})
            before_config, before_token = cfg.path.read_bytes(), (cfg.home / 'token').read_bytes()
            commands = home / 'test-bin'
            commands.mkdir()
            uname = commands / 'uname'
            uname.write_text('#!/bin/sh\nprintf "Linux\\n"\n')
            uname.chmod(0o755)
            env = dict(os.environ, HOME=str(home), PATH=str(commands) + os.pathsep + os.environ['PATH'])
            env.pop('WORKLEDGER_PYTHON', None)
            env.pop('WORKLEDGER_HOME', None)
            root = Path(__file__).resolve().parents[1]
            for _ in range(2):
                subprocess.run(['bash', str(root / 'scripts/install.sh')], cwd=root,
                               env=env, check=True, capture_output=True, timeout=30)
                self.assertIn(str(runtime), (home / '.local/bin/workledger').read_text())
                self.assertEqual(cfg.path.read_bytes(), before_config)
                self.assertEqual((cfg.home / 'token').read_bytes(), before_token)
