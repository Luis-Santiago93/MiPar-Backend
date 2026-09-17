import os
import ssl
import unittest
from unittest.mock import patch

from sqlalchemy.pool import NullPool

from app.database import database_engine


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("DATABASE_URL", "POSTGRES_URL", "ENVIRONMENT", "DATABASE_SSL_CA_CERT"):
            os.environ.pop(name, None)

    def test_supabase_url_is_compatible_with_driver(self):
        for extra in ("supa=base-pooler.x", "pgbouncer=true"):
            with self.subTest(extra=extra), patch.dict(os.environ, {
                "ENVIRONMENT": "production",
                "POSTGRES_URL": "postgres://postgres.project:p%40ss@pooler.example.com:6543/postgres?sslmode=require&" + extra,
            }):
                engine = database_engine()
                self.addCleanup(engine.dispose)
                self.assertIsInstance(engine.pool, NullPool)
                self.assertEqual(engine.url.password, "p@ss")
                with patch("pg8000.connect", side_effect=RuntimeError("connection intercepted")) as connect:
                    with self.assertRaisesRegex(RuntimeError, "connection intercepted"):
                        engine.connect()
                args = connect.call_args.kwargs
                self.assertNotIn("sslmode", args)
                self.assertNotIn("supa", args)
                self.assertNotIn("pgbouncer", args)
                self.assertEqual(args["ssl_context"].verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(args["ssl_context"].check_hostname)

    def test_local_database_takes_precedence(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:", "POSTGRES_URL": "invalid"}):
            engine = database_engine()
            self.addCleanup(engine.dispose)
            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql("select 1").scalar(), 1)

    def test_custom_ca_is_loaded_without_disabling_verification(self):
        # Reuse a trusted public root as a valid PEM fixture; no network needed.
        cert = ssl.DER_cert_to_PEM_cert(ssl.create_default_context().get_ca_certs(binary_form=True)[0])
        for value in (cert, cert.replace("\n", "\\n")):
            with self.subTest(escaped="\\n" in value), patch.dict(os.environ, {
                "ENVIRONMENT": "production",
                "POSTGRES_URL": "postgres://user:password@pooler.example.com:6543/postgres?sslmode=require",
                "DATABASE_SSL_CA_CERT": value,
            }):
                engine = database_engine()
                self.addCleanup(engine.dispose)
                with patch("pg8000.connect", side_effect=RuntimeError("intercepted")) as connect:
                    with self.assertRaisesRegex(RuntimeError, "intercepted"):
                        engine.connect()
                context = connect.call_args.kwargs["ssl_context"]
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(context.check_hostname)
                self.assertIn(ssl.PEM_cert_to_DER_cert(cert), context.get_ca_certs(binary_form=True))

    def test_invalid_ca_fails_closed(self):
        with patch.dict(os.environ, {
            "ENVIRONMENT": "production",
            "POSTGRES_URL": "postgres://user:password@pooler.example.com:6543/postgres",
            "DATABASE_SSL_CA_CERT": "invalid certificate",
        }):
            with self.assertRaises(ssl.SSLError):
                database_engine()

    def test_production_requires_postgres(self):
        for url in ("", "sqlite:///./mipar.db"):
            with self.subTest(url=url), patch.dict(os.environ, {"ENVIRONMENT": "production", "DATABASE_URL": url}):
                with self.assertRaises(RuntimeError):
                    database_engine()
