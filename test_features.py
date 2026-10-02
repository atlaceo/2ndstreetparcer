"""Проверки цен, CSV и кнопок продолжения поиска."""

import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import bot
from csv_export import products_csv
from parser_2ndstreet import Product, find_new_products, search_url
from search_history import SearchHistory
from test_history import card


class PriceAndCSVTests(TestCase):
    def test_custom_max_price(self):
        self.assertEqual(bot.parse_search_args(["stone", "island", "--max", "12345"]), ("stone island", 12345))
        for args in (["nike", "--max"], ["nike", "--max", "oops"], ["nike", "--max", "0"], ["--max", "1000"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                bot.parse_search_args(args)
        params = parse_qs(urlparse(search_url("nike", "700012", 2, 10000)).query)
        self.assertEqual(params["maxPrice"], ["10000"])
        self.assertEqual(params["page"], ["2"])

    def test_csv_unicode_and_formula_protection(self):
        products = [Product('=HYPERLINK("bad");日本語', 1000, "https://example.com/1", size="+1")]
        output = products_csv(products).getvalue()
        self.assertTrue(output.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(StringIO(output.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "'" + products[0].title)
        self.assertEqual(rows[1][1], "1000")
        self.assertEqual(rows[1][3], "'+1")

    def test_positions_are_independent_for_price_filters(self):
        with TemporaryDirectory() as directory:
            history = SearchHistory(Path(directory) / "history.sqlite3")
            history.save_page(1, "nike", "700012", 10, max_price=5000)
            self.assertEqual(history.page(1, "nike", "700012", max_price=5000), 10)
            self.assertEqual(history.page(1, "nike", "700012", max_price=10000), 1)
            self.assertEqual(history.page(1, "nike", "700012"), 1)


class FeatureFlowTests(IsolatedAsyncioTestCase):
    def callback(self, data):
        callback = AsyncMock()
        callback.data = data
        callback.message.chat_id = 10
        callback.message.message_id = 20
        callback.from_user.id = 42
        return callback

    async def test_category_then_price(self):
        callback = self.callback("search:category:700012")
        request = {"query": "nike", "max_price": None, "category_selected": False}
        context = SimpleNamespace(user_data={"search_menus": {(10, 20): request}})
        with patch("bot.run_result_page", new_callable=AsyncMock) as run:
            await bot.choose_category(SimpleNamespace(callback_query=callback), context)
            run.assert_not_awaited()
            self.assertIn("цену", callback.edit_message_text.await_args.args[0])
            callback.data = "search:price:10000"
            await bot.choose_category(SimpleNamespace(callback_query=callback), context)
            run.assert_awaited_once_with(callback.message, callback.message, request, 42, context)
        self.assertEqual(request["max_price"], 10000)
        self.assertEqual(request["category"], "700012")

    async def test_more_preserves_filters_and_disables_old_button(self):
        callback = self.callback("results:more")
        session = {"query": "nike", "category": "700012", "max_price": 10000,
                   "products": [Product("Nike", 1000, "https://example.com/1")], "has_more": True, "busy": False}
        context = SimpleNamespace(user_data={"result_menus": {(10, 20): session}})
        with patch("bot.run_result_page", new_callable=AsyncMock) as run:
            await bot.result_action(SimpleNamespace(callback_query=callback), context)
            self.assertEqual(run.await_args.args[2]["max_price"], 10000)
            self.assertEqual(run.await_args.args[2]["category"], "700012")
            await bot.result_action(SimpleNamespace(callback_query=callback), context)
            self.assertEqual(run.await_count, 1)
        self.assertFalse(session["has_more"])

    async def test_csv_uses_clicked_batch(self):
        callback = self.callback("results:csv")
        first = Product("First batch", 1000, "https://example.com/1")
        context = SimpleNamespace(user_data={"result_menus": {
            (10, 20): {"products": [first]},
            (10, 21): {"products": [Product("Second batch", 2000, "https://example.com/2")]},
        }})
        await bot.result_action(SimpleNamespace(callback_query=callback), context)
        document = callback.message.reply_document.await_args.kwargs["document"].getvalue().decode("utf-8-sig")
        self.assertIn("First batch", document)
        self.assertNotIn("Second batch", document)

    async def test_price_sent_to_site_and_enforced_locally(self):
        requested = []
        def response(request):
            requested.append(request.url.params.get("maxPrice"))
            return httpx.Response(200, text=card(1).replace("1,000", "9,000") + card(2))
        client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch("parser_2ndstreet.httpx.AsyncClient", return_value=client):
            batch = await find_new_products("nike", max_price=5000)
        self.assertEqual(requested, ["5000"])
        self.assertEqual([p.product_id for p in batch.products], ["2"])
