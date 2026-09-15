import io
import json
import unittest
from unittest.mock import patch

from wechat_gallery_bot.management import child_intake


class WorkerOutputTests(unittest.TestCase):
    def test_library_prints_do_not_corrupt_protocol(self):
        protocol, discarded = io.StringIO(), io.StringIO()

        def backend(output):
            print('初始化成功：private account')
            output.write(json.dumps({'state': 'ready'}) + '\n')
            print('another library message')

        with patch('sys.stdout', protocol), patch('sys.stderr', discarded), patch.object(child_intake, '_worker', backend):
            child_intake.worker()
        self.assertEqual([json.loads(line) for line in protocol.getvalue().splitlines()], [{'state': 'ready'}])
        self.assertNotIn('private', protocol.getvalue())

    def test_stdout_restored_on_failure(self):
        protocol, discarded = io.StringIO(), io.StringIO()
        with patch('sys.stdout', protocol), patch('sys.stderr', discarded), patch.object(child_intake, '_worker', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                child_intake.worker()
            print('restored')
        self.assertEqual(protocol.getvalue(), 'restored\n')
