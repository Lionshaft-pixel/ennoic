import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

import database


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://test.invalid/ennoic",
            "ENNOIC_TASK_DB": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        pool_state = patch.object(database, "_postgres_pool", None)
        pool_state.start()
        self.addCleanup(pool_state.stop)
        self.raw = MagicMock()
        self.pool = MagicMock()
        self.pool.getconn.return_value = self.raw
        self.borrow = self.pool.connection.return_value
        self.borrow.__enter__.return_value = self.raw
        self.factory = patch.object(database, "ConnectionPool", return_value=self.pool)
        self.factory_mock = self.factory.start()
        self.addCleanup(self.factory.stop)
        driver = patch.object(database, "psycopg", MagicMock())
        self.driver = driver.start()
        self.addCleanup(driver.stop)

    def test_probe_reuses_application_pool_without_special_startup_options(self):
        application_connection = database.connect()
        application_connection.close()
        database.check_readiness(timeout_seconds=2)

        self.factory_mock.assert_called_once()
        settings = self.factory_mock.call_args.kwargs
        self.assertNotIn("options", settings["kwargs"])
        self.assertEqual(settings["kwargs"]["connect_timeout"], 10)
        self.pool.getconn.assert_called_once_with(timeout=15)
        self.pool.putconn.assert_called_once_with(self.raw)
        self.pool.connection.assert_called_once_with(timeout=2)
        self.driver.connect.assert_not_called()
        self.raw.execute.assert_has_calls([
            call("SELECT set_config('statement_timeout', %s, true)", ("2000",)),
            call("SELECT 1"),
        ], any_order=False)
        self.raw.execute.return_value.fetchone.assert_called_once()
        self.raw.transaction.return_value.__exit__.assert_called_once_with(None, None, None)
        self.borrow.__exit__.assert_called_once_with(None, None, None)
        self.raw.close.assert_not_called()

    def test_query_failure_exits_transaction_and_returns_connection_before_retry(self):
        error = RuntimeError("simulated query timeout")
        self.raw.execute.side_effect = [MagicMock(), error]
        with self.assertRaisesRegex(RuntimeError, "simulated query timeout"):
            database.check_readiness()
        transaction_error = self.raw.transaction.return_value.__exit__.call_args.args
        self.assertIs(transaction_error[0], RuntimeError)
        self.assertIs(transaction_error[1], error)
        self.assertIs(self.borrow.__exit__.call_args.args[1], error)
        self.raw.close.assert_not_called()

        self.raw.execute.side_effect = None
        database.check_readiness()
        self.factory_mock.assert_called_once()
        self.assertEqual(self.pool.connection.call_count, 2)
        self.raw.transaction.return_value.__exit__.assert_called_with(None, None, None)

    def test_pool_exhaustion_propagates_and_does_not_open_a_bypass_connection(self):
        self.borrow.__enter__.side_effect = TimeoutError("pool exhausted")
        with self.assertRaises(TimeoutError):
            database.check_readiness(timeout_seconds=2)
        self.raw.execute.assert_not_called()
        self.driver.connect.assert_not_called()

    def test_sqlite_probe_keeps_its_local_timeout_and_requires_no_postgres_pool(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "ENNOIC_TASK_DB": str(Path(directory) / "probe.sqlite3"),
        }), patch.object(database.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            database.check_readiness(timeout_seconds=2)
            self.assertEqual(connect.call_args.kwargs["timeout"], 2)
            self.factory_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
