import os
import ssl

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


def database_engine():
    raw_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    production = os.getenv("ENVIRONMENT") == "production"
    if not raw_url:
        if production:
            raise RuntimeError("Configura POSTGRES_URL o DATABASE_URL en production")
        raw_url = "sqlite:///./mipar.db"
    url = make_url(raw_url)
    if url.get_backend_name() == "sqlite":
        if production:
            raise RuntimeError("Configura PostgreSQL en production; SQLite es solo para desarrollo")
        return create_engine(url, pool_pre_ping=True, connect_args={"check_same_thread": False})

    if url.get_backend_name() not in ("postgres", "postgresql"):
        raise ValueError("Solo se admite SQLite o PostgreSQL")
    url = url.set(drivername="postgresql+pg8000")
    sslmode = url.query.get("sslmode")
    connect_args = {}
    if sslmode in ("require", "verify-ca", "verify-full") or (production and sslmode is None):
        # Require TLS and verify the server certificate and hostname.
        connect_args["ssl_context"] = ssl.create_default_context()
    elif sslmode == "disable" and not production:
        connect_args["ssl_context"] = False
    elif sslmode is not None:
        raise ValueError("Usa sslmode=require, verify-ca o verify-full para PostgreSQL")
    # These libpq/Prisma/integration parameters are not pg8000 arguments.
    url = url.difference_update_query(["sslmode", "pgbouncer", "supa"])
    options = {"poolclass": NullPool} if production or url.port == 6543 else {}
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args, **options)
