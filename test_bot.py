"""Проверки команд без подключения к Telegram."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import bot
from telegram.error import BadRequest
from parser_2ndstreet import (
    Product, SearchError, category_from_name, full_size_image_url, parse_products, search_url,
)


class ParserTests(unittest.TestCase):
    def test_full_size_photo_keeps_original_as_backup(self):
        preview = "https://cdn2.2ndstreet.jp/img/pc/goods/232096/53/00747/1_tn.jpg"
        html = f'''<li class="itemCard"><a href="/goods/detail/goodsId/123">
            <div class="itemCard_img"><img src="{preview}"></div>
            <p class="itemCard_name">スニーカー</p>
            <p class="itemCard_price">&yen;1,000</p></a></li>'''
        product = parse_products(html)[0]
        self.assertEqual(product.image_url, preview.replace("_tn.jpg", ".jpg"))
        self.assertEqual(product.thumbnail_url, preview)

    def test_unrelated_image_url_is_unchanged(self):
        url = "https://example.com/1_tn.jpg"
        self.assertEqual(full_size_image_url(url), url)
    def test_category_in_search_url(self):
        params = parse_qs(urlparse(search_url("stone island", "700012")).query)
        self.assertEqual(params, {"keyword": ["stone island"], "category": ["700012"]})
    def test_photo_and_item_details(self):
        html = '''<li class="itemCard">
            <a href="/goods/detail/goodsId/123">
            <div class="itemCard_img"><img src="https://cdn2.2ndstreet.jp/item.jpg"></div>
            <p class="itemCard_brand">NIKE</p>
            <p class="itemCard_name">フリースジャケット/L/BLK</p>
            <p class="itemCard_price">&yen;6,490</p>
            <p class="itemCard_size">サイズL</p>
            <p class="itemCard_status">商品の状態 : 中古B</p>
            </a></li>'''
        product = parse_products(html)[0]
        self.assertEqual(product.category, "Флисовая куртка")
        self.assertEqual(product.size, "L")
        self.assertEqual(product.condition, "Б/у, оценка B")
        self.assertEqual(product.image_url, "https://cdn2.2ndstreet.jp/item.jpg")

    def test_unknown_category_keeps_original(self):
        self.assertEqual(category_from_name("未知の品/L"), "未知の品")
        self.assertEqual(category_from_name("カーディガン(厚手)/L"), "Кардиган")

    def test_block_page_is_not_empty_search(self):
        with self.assertRaises(SearchError):
            parse_products("<html>Access denied</html>")

    def test_missing_price_is_not_empty_search(self):
        with self.assertRaises(SearchError):
            parse_products('<li class="itemCard"><p class="itemCard_name">Nike</p></li>')


class SearchCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_query_does_not_fetch(self):
        message = AsyncMock()
        with patch("bot.search_products", new_callable=AsyncMock) as fetch:
            await bot.search(SimpleNamespace(message=message), SimpleNamespace(args=[]))
        fetch.assert_not_awaited()
        self.assertIn("/search nike", message.reply_text.await_args.args[0])

    async def test_results_are_formatted(self):
        message = AsyncMock()
        with patch("bot.search_products", new_callable=AsyncMock) as fetch:
            fetch.return_value = [Product("NIKE jacket", 6490, "https://example.com/item")]
            await bot.show_results(message, AsyncMock(), "nike")
        fetch.assert_awaited_once_with("nike", category=None, max_price=None)
        text = message.reply_text.await_args.args[0]
        self.assertIn("¥6,490", text)
        keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[0][0].url, "https://example.com/item")

    async def test_photo_card(self):
        message = AsyncMock()
        product = Product("Nike", 1000, "https://example.com/item",
                          image_url="https://example.com/image.jpg", category="Куртка")
        with patch("bot.search_products", new_callable=AsyncMock, return_value=[product]):
            await bot.show_results(message, AsyncMock(), "nike")
        self.assertEqual(message.reply_photo.await_args.kwargs["photo"], product.image_url)
        self.assertIn("Куртка", message.reply_photo.await_args.kwargs["caption"])
        message.reply_text.assert_not_awaited()

    async def test_rejected_photo_falls_back_and_continues(self):
        message = AsyncMock()
        message.reply_photo.side_effect = BadRequest("Failed to get HTTP URL content")
        products = [Product("Nike", 1000, "https://example.com/1",
                            image_url="https://example.com/image.jpg"),
                    Product("Adidas", 2000, "https://example.com/2")]
        with patch("bot.search_products", new_callable=AsyncMock, return_value=products):
            await bot.show_results(message, AsyncMock(), "shoes")
        self.assertEqual(message.reply_text.await_count, 2)
        self.assertIn("Nike", message.reply_text.await_args_list[0].args[0])
        self.assertIn("Adidas", message.reply_text.await_args_list[1].args[0])

    async def test_original_photo_falls_back_to_thumbnail(self):
        message = AsyncMock()
        message.reply_photo.side_effect = [BadRequest("Photo unavailable"), None]
        product = Product("Nike", 1000, "https://example.com/item",
                          image_url="https://example.com/full.jpg",
                          thumbnail_url="https://example.com/small.jpg")
        with patch("bot.search_products", new_callable=AsyncMock, return_value=[product]):
            await bot.show_results(message, AsyncMock(), "nike")
        self.assertEqual([call.kwargs["photo"] for call in message.reply_photo.await_args_list],
                         [product.image_url, product.thumbnail_url])
        message.reply_text.assert_not_awaited()

    async def test_site_error_has_manual_search_link(self):
        message = AsyncMock()
        status = AsyncMock()
        with patch("bot.search_products", new_callable=AsyncMock) as fetch:
            fetch.side_effect = SearchError("Unavailable")
            await bot.show_results(message, status, "stone island", "700012")
        text = status.edit_text.await_args.args[0]
        self.assertIn("keyword=stone+island", text)
        self.assertIn("category=700012", text)

    async def test_search_opens_menu_without_fetching(self):
        message = AsyncMock()
        message.reply_text.return_value.chat_id = 10
        message.reply_text.return_value.message_id = 20
        context = SimpleNamespace(args=["stone", "island"], user_data={})
        with patch("bot.search_products", new_callable=AsyncMock) as fetch:
            await bot.search(SimpleNamespace(message=message), context)
        fetch.assert_not_awaited()
        self.assertEqual(context.user_data["search_menus"][(10, 20)]["query"], "stone island")
        self.assertIn("reply_markup", message.reply_text.await_args.kwargs)

    async def test_category_uses_query_from_clicked_menu(self):
        callback = AsyncMock()
        callback.message.chat_id = 10
        callback.message.message_id = 20
        callback.data = "search:category:700012"
        callback.from_user.id = 42
        request = {"query": "nike", "max_price": 10000}
        context = SimpleNamespace(user_data={"search_menus": {(10, 20): request, (10, 21): {"query": "adidas", "max_price": None}}})
        with patch("bot.run_result_page", new_callable=AsyncMock) as show:
            await bot.choose_category(SimpleNamespace(callback_query=callback), context)
        show.assert_awaited_once_with(callback.message, callback.message, request, 42, context)
        self.assertEqual(request["category"], "700012")
        self.assertNotIn((10, 20), context.user_data["search_menus"])
        self.assertEqual(context.user_data["search_menus"][(10, 21)]["query"], "adidas")

    async def test_expired_menu_does_not_fetch(self):
        callback = AsyncMock()
        callback.message.chat_id = 10
        callback.message.message_id = 20
        callback.data = "search:category:700012"
        with patch("bot.show_results", new_callable=AsyncMock) as show:
            await bot.choose_category(SimpleNamespace(callback_query=callback), SimpleNamespace(user_data={}))
        show.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_group_selection_keeps_query(self):
        callback = AsyncMock()
        callback.message.chat_id = 10
        callback.message.message_id = 20
        callback.data = "search:group:men"
        context = SimpleNamespace(user_data={"search_menus": {(10, 20): {"query": "nike", "max_price": None}}})
        with patch("bot.show_results", new_callable=AsyncMock) as show:
            await bot.choose_category(SimpleNamespace(callback_query=callback), context)
        show.assert_not_awaited()
        self.assertEqual(context.user_data["search_menus"][(10, 20)]["query"], "nike")
        keyboard = callback.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, "search:category:700012")


if __name__ == "__main__":
    unittest.main()
