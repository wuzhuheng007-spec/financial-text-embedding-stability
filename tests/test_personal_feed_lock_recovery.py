"""Local failure injection only; no provider or model requests."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.quant_personal_feed import cli, collector, store


class LockRecoveryTests(unittest.TestCase):
    def test_failed_schema_initialization_closes_connection(self):
        connection=Mock()
        connection.executescript.side_effect=sqlite3.OperationalError('database is locked')
        with tempfile.TemporaryDirectory() as directory, patch.object(store.sqlite3, 'connect', return_value=connection):
            with self.assertRaises(sqlite3.OperationalError):
                store.PersonalFeedStore(Path(directory)/'cache.sqlite3', 1)
            connection.close.assert_called_once()

    def test_repeated_locks_outlive_old_three_attempt_limit(self):
        stop = Mock()
        stop.is_set.return_value = False
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, 'ROOT', Path(directory)), patch.object(cli, 'run_jin10') as run:
            run.side_effect = [sqlite3.OperationalError('database is locked') for _ in range(4)] + [None]
            cli.recover_jin10_sqlite({}, 1, stop)
            self.assertEqual(run.call_count, 5)
            self.assertEqual([x.args[0] for x in stop.wait.call_args_list], [60, 120, 240, 300])

    def test_non_lock_error_is_not_retried(self):
        stop = Mock()
        stop.is_set.return_value = False
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, 'ROOT', Path(directory)), patch.object(cli, 'run_jin10', side_effect=sqlite3.OperationalError('no such table: private_table')):
            with self.assertRaises(sqlite3.OperationalError):
                cli.recover_jin10_sqlite({}, 1, stop)
            stop.wait.assert_not_called()
            log=(Path(directory)/'apps/quant_portal/var/personal_feed_sqlite_errors.log').read_text()
            self.assertNotIn('private_table', log)

    def test_stop_interrupts_recovery_without_another_provider_start(self):
        stop=Mock();stop.is_set.return_value=False
        stop.wait.side_effect=lambda _: setattr(stop.is_set,'return_value',True)
        with tempfile.TemporaryDirectory() as directory, patch.object(cli,'ROOT',Path(directory)), patch.object(cli,'run_jin10',side_effect=sqlite3.OperationalError('database is locked')) as run:
            cli.recover_jin10_sqlite({},1,stop)
            self.assertEqual(run.call_count,1)

    def test_jin10_closes_connection_even_if_exit_metric_is_locked(self):
        db=Mock();db.provider_metric.side_effect=sqlite3.OperationalError('database is locked')
        config={'cache_db':'apps/quant_portal/var/test.sqlite3',
                'jin10':{'poll_interval_seconds':60,'max_business_calls_per_utc_day':10}}
        with patch.object(collector,'PersonalFeedStore',return_value=db), patch.object(collector,'_jin10_session') as supplier:
            with self.assertRaises(sqlite3.OperationalError):
                collector.run_jin10(config,1,Mock())
            db.close.assert_called_once()
            supplier.assert_not_called()

if __name__=='__main__':
    unittest.main()
