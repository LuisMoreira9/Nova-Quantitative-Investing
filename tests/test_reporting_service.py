"""Real process check: duplicate reporters blocked and crash releases lock."""
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dashboard.reporting_service import ReporterAlreadyRunning, reporter_instance


class ReportingServiceTest(unittest.TestCase):
    def test_duplicate_blocked_other_service_allowed_and_crash_releases(self):
        with TemporaryDirectory() as directory:
            child = subprocess.Popen(
                [sys.executable, '-c',
                 'from pathlib import Path; import sys,time; '
                 'from dashboard.reporting_service import reporter_instance; '
                 'lock=reporter_instance("portfolio", Path(sys.argv[1])); lock.__enter__(); '
                 'print("locked", flush=True); time.sleep(60)', directory],
                stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'locked')
                with self.assertRaises(ReporterAlreadyRunning):
                    with reporter_instance('portfolio', Path(directory)):
                        self.fail('Duplicate acquired lock')
                with reporter_instance('news', Path(directory)):
                    pass
            finally:
                child.terminate()
                child.wait(timeout=10)
                child.stdout.close()
            with reporter_instance('portfolio', Path(directory)):
                pass


if __name__ == '__main__':
    unittest.main()
