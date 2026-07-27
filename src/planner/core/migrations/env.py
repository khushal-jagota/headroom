"""Alembic environment.

Panels changes its schema through one door: ``planner.core.db.create_schema`` opens the
database, takes the write lock, decides whether the database is new, already tracked, or
an old one being adopted, and hands this environment the connection it prepared. That is
why there is no path here that opens a database itself — ``alembic upgrade`` run straight
from the command line would skip every one of those decisions.

``alembic revision`` and ``alembic history`` do not run this file, so authoring new
migrations from the command line still works.
"""

from __future__ import annotations

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "Panels migrations run through planner.core.db.create_schema, which prepares the "
        "connection and decides how the database is brought up to date. Start the server "
        "or call create_schema instead of running alembic upgrade directly."
    )

context.configure(
    connection=connection,
    # This only changes how autogenerate renders migrations, and Panels has no SQLAlchemy
    # models to autogenerate from, so nothing today depends on it. What actually makes a
    # table rebuild safe is the foreign-key policy in planner.core.db and declaring the
    # table you are rebuilding — see script.py.mako.
    render_as_batch=True,
)

with context.begin_transaction():
    context.run_migrations()
