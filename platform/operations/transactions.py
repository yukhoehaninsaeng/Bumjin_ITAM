from contextlib import contextmanager
from django.db import connection, transaction


@contextmanager
def read_snapshot():
    """Keep dashboard counts and lists in one snapshot without long-lived caching."""
    outer_transaction = connection.in_atomic_block
    with transaction.atomic():
        if connection.vendor == "postgresql" and not outer_transaction:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield
