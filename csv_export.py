"""CSV с UTF-8 BOM для открытия в Excel, без временных файлов на диске."""

import csv
from io import BytesIO, StringIO

from parser_2ndstreet import Product


def safe_cell(value: str) -> str:
    # Текст с сайта не должен превращаться в формулу при открытии таблицы.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def products_csv(products: list[Product]) -> BytesIO:
    text = StringIO(newline="")
    writer = csv.writer(text, delimiter=";")
    writer.writerow(["Название", "Цена (JPY)", "Тип товара", "Размер", "Состояние", "Ссылка", "Фото"])
    for product in products:
        writer.writerow([
            safe_cell(product.title), product.price_yen, safe_cell(product.category),
            safe_cell(product.size), safe_cell(product.condition), product.url, product.image_url or "",
        ])
    output = BytesIO(text.getvalue().encode("utf-8-sig"))
    output.name = "secondstreet-products.csv"
    return output
