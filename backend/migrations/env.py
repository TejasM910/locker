from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.models import Base

target_metadata = Base.metadata


def run_migrations_online():
    engine = create_engine(settings.sync_database_url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
