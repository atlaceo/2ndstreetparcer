"""История и переход на следующие страницы без сетевых запросов."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import httpx
from telegram.error import NetworkError

import bot
from parser_2ndstreet import Product, SearchBatch, find_new_products
from search_history import SearchHistory


def card(product_id):
    return f'''<li class="itemCard"><a href="/goods/detail/goodsId/{product_id}">
        <p class="itemCard_name">Nike</p><p class="itemCard_price">&yen;1,000</p>
        </a></li>'''


class HistoryTests(TestCase):
    def test_history_survives_reopening_and_is_per_user(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            history = SearchHistory(path)
            history.mark(1, "123")
            history.save_page(1, "NIKE", "700012", 3)
            reopened = SearchHistory(path)
            self.assertEqual(reopened.seen_ids(1), {"123"})
            self.assertEqual(reopened.seen_ids(2), set())
            self.assertEqual(reopened.page(1, "nike", "700012"), 3)
            self.assertEqual(reopened.page(1, "nike", "810001"), 1)
            reopened.reset(1)
            self.assertEqual(reopened.seen_ids(1), set())
            self.assertEqual(reopened.page(1, "nike", "700012"), 1)


class PaginationTests(IsolatedAsyncioTestCase):
    async def test_skips_seen_and_moves_to_next_page(self):
        requested = []

        def response(request):
            page = int(request.url.params.get("page", "1"))
            requested.append(page)
            html = card(1) + '<a href="/search?keyword=nike&page=2">2</a>' if page == 1 else card(1) + card(2)
            return httpx.Response(200, text=html)

        client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch("parser_2ndstreet.httpx.AsyncClient", return_value=client):
            batch = await find_new_products("nike", excluded_ids={"1"})
        self.assertEqual(requested, [1, 2])
        self.assertEqual([p.product_id for p in batch.products], ["2"])
        self.assertFalse(batch.has_more)

    async def test_batch_limit_preserves_remaining_items_on_page(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=card(1) + card(2) + card(3))
        ))
        with patch("parser_2ndstreet.httpx.AsyncClient", return_value=client):
            batch = await find_new_products("nike", limit=2)
        self.assertEqual([p.product_id for p in batch.products], ["1", "2"])
        self.assertEqual(batch.page, 1)
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=card(1) + card(2) + card(3))
        ))
        with patch("parser_2ndstreet.httpx.AsyncClient", return_value=client):
            batch = await find_new_products("nike", limit=2, excluded_ids={"1", "2"}, start_page=batch.page)
        self.assertEqual([p.product_id for p in batch.products], ["3"])

    async def test_scan_limit_saves_next_page(self):
        def response(request):
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, text=card(1) + f'<a href="/search?page={page + 1}">Next</a>')
        client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch("parser_2ndstreet.httpx.AsyncClient", return_value=client):
            batch = await find_new_products("nike", excluded_ids={"1"}, start_page=3, max_pages=2)
        self.assertEqual(batch.products, [])
        self.assertEqual(batch.page, 5)
        self.assertTrue(batch.has_more)

    async def test_only_delivered_items_are_recorded(self):
        with TemporaryDirectory() as directory:
            history = SearchHistory(Path(directory) / "history.sqlite3")
            message = AsyncMock()
            message.reply_text.side_effect = [None, NetworkError("Disconnected")]
            batch = SearchBatch([Product("One", 1000, "https://example.com/goodsId/1"),
                                 Product("Two", 2000, "https://example.com/goodsId/2")], 3, True)
            with patch("bot.HISTORY", history), patch("bot.find_new_products", new_callable=AsyncMock, return_value=batch):
                with self.assertRaises(NetworkError):
                    await bot.show_results(message, AsyncMock(), "nike", user_id=42)
            self.assertEqual(history.seen_ids(42), {"1"})
            self.assertEqual(history.page(42, "nike", None), 1)
