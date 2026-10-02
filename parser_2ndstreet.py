"""Получение товаров из публичной страницы поиска 2nd STREET."""

from dataclasses import dataclass
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import httpx

BASE_URL = "https://www.2ndstreet.jp"

# В названии товара сайт обычно указывает тип до первого символа «/».
CATEGORIES = {
    "ジャケット": "Куртка / жакет",
    "ナイロンジャケット": "Нейлоновая куртка",
    "フリースジャケット": "Флисовая куртка",
    "ダウンジャケット": "Пуховик",
    "マウンテンパーカー": "Куртка с капюшоном",
    "レザージャケット": "Кожаная куртка",
    "フライトジャケット": "Бомбер",
    "ミリタリージャケット": "Куртка в стиле милитари",
    "ハイカットスニーカー": "Высокие кроссовки",
    "ローカットスニーカー": "Низкие кроссовки",
    "スニーカー": "Кроссовки",
    "ブーツ": "Ботинки / сапоги",
    "サンダル": "Сандалии",
    "ローファー": "Лоферы",
    "Tシャツ": "Футболка",
    "長袖Tシャツ": "Лонгслив",
    "ロングスリーブT": "Лонгслив",
    "シャツ": "Рубашка",
    "半袖シャツ": "Рубашка с коротким рукавом",
    "長袖シャツ": "Рубашка с длинным рукавом",
    "スウェット": "Свитшот",
    "パーカー": "Худи",
    "ジップパーカー": "Худи на молнии",
    "カーディガン": "Кардиган",
    "ニット・セーター": "Свитер",
    "セーター": "Свитер",
    "ベスト": "Жилет",
    "コート": "Пальто",
    "パンツ": "Брюки",
    "カーゴパンツ": "Брюки карго",
    "ストレートパンツ": "Прямые брюки",
    "ショートパンツ": "Шорты",
    "デニムパンツ": "Джинсы",
    "ショルダーバッグ": "Сумка через плечо",
    "トートバッグ": "Сумка-тоут",
    "バックパック": "Рюкзак",
    "リュック": "Рюкзак",
    "キャップ": "Кепка",
    "ハット": "Шляпа",
    "腕時計": "Наручные часы",
}


def category_from_name(name: str) -> str:
    """Переводит тип из названия; неизвестные типы сохраняет на японском."""
    item_type = name.split("/", 1)[0].strip()
    # Например, «カーディガン(厚手)» — разновидность кардигана.
    base_type = re.split(r"[（(]", item_type, maxsplit=1)[0].strip()
    return CATEGORIES.get(base_type, item_type or "Не указана")


def full_size_image_url(url: str) -> str:
    """У 2nd STREET файл 1_tn.jpg — превью, а 1.jpg — большое фото."""
    parts = urlsplit(url)
    if parts.hostname in {"cdn2.2ndstreet.jp", "www.2ndstreet.jp"} and parts.path.startswith("/img/pc/goods/"):
        path = re.sub(r"/(\d+)_tn\.jpg$", r"/\1.jpg", parts.path)
        return urlunsplit(parts._replace(path=path))
    return url


class SearchError(Exception):
    """Сайт недоступен или вернул неожиданную страницу."""


@dataclass(frozen=True)
class Product:
    title: str
    price_yen: int
    url: str
    image_url: str | None = None
    category: str = "Не указана"
    size: str = "Не указан"
    condition: str = "Не указано"
    thumbnail_url: str | None = None

    @property
    def product_id(self) -> str:
        match = re.search(r"/goodsId/(\d+)", self.url)
        return match[1] if match else self.url


@dataclass(frozen=True)
class SearchBatch:
    products: list[Product]
    page: int
    has_more: bool


def search_url(query: str, category: str | None = None, page: int = 1, max_price: int | None = None) -> str:
    params = {"keyword": query}
    if category:
        params["category"] = category
    if page > 1:
        params["page"] = str(page)
    if max_price is not None:
        params["maxPrice"] = str(max_price)
    return f"{BASE_URL}/search?{urlencode(params)}"


def parse_products(html: str, limit: int = 5) -> list[Product]:
    """Разбирает карточки, пропуская неполные и повторяющиеся товары."""
    if limit <= 0:
        return []
    soup = BeautifulSoup(html, "html.parser")
    products = []
    seen = set()
    cards = soup.select("li.itemCard")
    for card in cards:
        link = card.select_one('a[href^="/goods/detail/"]')
        name = card.select_one(".itemCard_name")
        brand = card.select_one(".itemCard_brand")
        price = card.select_one(".itemCard_price")
        if link is None or name is None or price is None:
            continue
        price_match = re.search(r"[¥￥]\s*([\d,]+)", price.get_text(" ", strip=True))
        if price_match is None:
            continue
        title = " ".join(
            part.get_text(" ", strip=True) for part in (brand, name) if part is not None
        )
        url = urljoin(BASE_URL, link["href"])
        if not title or url in seen:
            continue
        seen.add(url)
        image = card.select_one(".itemCard_img img")
        image_src = (image.get("data-src") or image.get("src")) if image else None
        image_url = urljoin(BASE_URL, image_src) if image_src else None
        if image_url and not image_url.startswith("https://"):
            image_url = None
        thumbnail_url = image_url
        if image_url:
            image_url = full_size_image_url(image_url)
        size = card.select_one(".itemCard_size")
        condition = card.select_one(".itemCard_status")
        size_text = size.get_text(" ", strip=True).removeprefix("サイズ").strip() if size else ""
        if size_text == "その他":
            size_text = "Другой (см. сайт)"
        condition_text = condition.get_text(" ", strip=True) if condition else ""
        condition_text = re.sub(r"^商品の状態\s*[:：]\s*", "", condition_text)
        condition_text = condition_text.replace("中古", "Б/у, оценка ")
        products.append(Product(
            title=title,
            price_yen=int(price_match[1].replace(",", "")),
            url=url,
            image_url=image_url,
            category=category_from_name(name.get_text(" ", strip=True)),
            size=size_text or "Не указан",
            condition=condition_text or "Не указано",
            thumbnail_url=thumbnail_url if thumbnail_url != image_url else None,
        ))
        if len(products) >= limit:
            break

    if not products:
        # Не выдаём страницу блокировки или изменённую разметку за пустой поиск.
        page_text = soup.get_text(" ", strip=True)
        if cards or not any(text in page_text for text in (
            "該当する商品が見つかりません", "該当する商品はありません",
            "商品が見つかりません", "0件", "0 件",
        )):
            raise SearchError("Не удалось прочитать карточки товаров.")
    return products


async def search_products(query: str, limit: int = 5, category: str | None = None, max_price: int | None = None) -> list[Product]:
    return (await find_new_products(query, category=category, limit=limit, max_price=max_price)).products


async def find_new_products(
    query: str, category: str | None = None, limit: int = 5,
    excluded_ids: set[str] | None = None, start_page: int = 1, max_pages: int = 5,
    max_price: int | None = None,
) -> SearchBatch:
    """Ищет непоказанные товары, проходя до пяти страниц за один запрос."""
    excluded = set(excluded_ids or ())
    products = []
    page = start_page
    has_more = False
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for page in range(start_page, start_page + max_pages):
                response = await client.get(search_url(query, category, page, max_price))
                response.raise_for_status()
                page_products = parse_products(response.text, limit=1000)
                soup = BeautifulSoup(response.text, "html.parser")
                has_more = any(
                    parse_qs(urlsplit(link.get("href", "")).query).get("page") == [str(page + 1)]
                    for link in soup.select('a[href]')
                )
                for product in page_products:
                    if product.product_id in excluded:
                        continue
                    if max_price is not None and product.price_yen > max_price:
                        continue
                    products.append(product)
                    excluded.add(product.product_id)
                    if len(products) >= limit:
                        # Остаёмся на этой странице: на ней могут быть ещё новые товары.
                        return SearchBatch(products, page, True)
                if not has_more:
                    break
    except httpx.HTTPError as exc:
        if products:
            # Уже найденные товары можно показать; следующую страницу повторим позже.
            return SearchBatch(products, page, True)
        raise SearchError("Не удалось получить страницу 2nd STREET.") from exc
    return SearchBatch(products, page + 1 if has_more else page, has_more)
