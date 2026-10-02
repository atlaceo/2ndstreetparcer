"""История показанных товаров и позиция поиска в локальной SQLite-базе."""

from pathlib import Path
from contextlib import contextmanager
import sqlite3


class SearchHistory:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS seen (user_id INTEGER, product_id TEXT, PRIMARY KEY(user_id, product_id))")
                connection.execute("CREATE TABLE IF NOT EXISTS positions (user_id INTEGER, query TEXT, category TEXT, page INTEGER, PRIMARY KEY(user_id, query, category))")
                yield connection
        finally:
            connection.close()

    def seen_ids(self, user_id: int) -> set[str]:
        with self.connect() as connection:
            return {row[0] for row in connection.execute("SELECT product_id FROM seen WHERE user_id=?", (user_id,))}

    def page(self, user_id: int, query: str, category: str | None, max_price: int | None = None) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT page FROM positions WHERE user_id=? AND query=? AND category=?", (user_id, query.casefold().strip(), self.filter_key(category, max_price))).fetchone()
            return row[0] if row else 1

    def mark(self, user_id: int, product_id: str):
        with self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (user_id, product_id))

    def save_page(self, user_id: int, query: str, category: str | None, page: int, max_price: int | None = None):
        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO positions VALUES (?, ?, ?, ?)", (user_id, query.casefold().strip(), self.filter_key(category, max_price), page))

    @staticmethod
    def filter_key(category: str | None, max_price: int | None) -> str:
        key = category or ""
        return key if max_price is None else f"{key}|max:{max_price}"

    def reset(self, user_id: int):
        with self.connect() as connection:
            connection.execute("DELETE FROM seen WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM positions WHERE user_id=?", (user_id,))
