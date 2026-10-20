from alembic import context

from app.core.config import Config
from app.db.models import Base
from app.db.session import Database

target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=Config().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    else:
        database = Database(Config().database_url)
        with database.engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
