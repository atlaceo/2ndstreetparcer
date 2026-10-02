"""Учебный бот: поиск товаров на 2nd STREET и повторение сообщений."""

import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from parser_2ndstreet import SearchError, find_new_products, search_products, search_url
from search_history import SearchHistory
from csv_export import products_csv

HISTORY = SearchHistory(Path(__file__).with_name("search_history.sqlite3"))


# Идентификаторы разделов из фильтров 2nd STREET.
CATEGORY_GROUPS = {
    "men": ("Мужское", {
        "700012": "Кроссовки: низкие", "700087": "Кроссовки: высокие",
        "961001": "Вся обувь", "911010": "Верхняя одежда",
        "911006": "Куртки / жакеты", "810001": "Футболки",
        "810037": "Худи", "810033": "Худи на молнии",
        "810035": "Свитшоты", "911009": "Брюки / шорты",
    }),
    "women": ("Женское", {
        "700028": "Кроссовки: низкие", "830016": "Кроссовки: высокие",
        "961003": "Вся обувь", "921010": "Верхняя одежда",
        "921006": "Куртки / жакеты", "840069": "Футболки",
        "840044": "Худи", "840040": "Худи на молнии",
        "840042": "Свитшоты", "921009": "Брюки / шорты",
        "921007": "Юбки", "921011": "Платья",
    }),
    "bags": ("Сумки", {
        "950001": "Все сумки", "951001": "Мужские сумки", "951002": "Женские сумки",
    }),
    "accessories": ("Аксессуары", {
        "931009": "Головные уборы", "931010": "Часы",
        "931002": "Украшения", "931011": "Кошельки / мелочи",
        "931012": "Очки", "931008": "Другие аксессуары",
    }),
}
CATEGORY_LABELS = {
    category: f"{group_name} · {label}"
    for group_name, choices in CATEGORY_GROUPS.values()
    for category, label in choices.items()
}


def category_keyboard(group: str | None = None) -> InlineKeyboardMarkup:
    if group is None:
        buttons = [InlineKeyboardButton(name, callback_data=f"search:group:{key}")
                   for key, (name, _) in CATEGORY_GROUPS.items()]
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("Все товары", callback_data="search:category:all")])
    else:
        buttons = [InlineKeyboardButton(label, callback_data=f"search:category:{category}")
                   for category, label in CATEGORY_GROUPS[group][1].items()]
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("← Назад", callback_data="search:group:root")])
    return InlineKeyboardMarkup(rows)


def price_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"До ¥{amount:,}", callback_data=f"search:price:{amount}")
         for amount in amounts]
        for amounts in ((5000, 10000), (20000, 50000))
    ] + [[InlineKeyboardButton("Без ограничения", callback_data="search:price:any")]])


def parse_search_args(args: list[str]) -> tuple[str, int | None]:
    max_price = None
    words = list(args)
    if "--max" in words:
        index = words.index("--max")
        if index + 2 != len(words):
            raise ValueError("Укажи цену в конце: /search nike --max 10000")
        try:
            max_price = int(words[index + 1])
        except ValueError:
            raise ValueError("Цена должна быть целым числом в иенах: --max 10000") from None
        if not 1 <= max_price <= 100_000_000:
            raise ValueError("Цена должна быть от 1 до 100 000 000 иен.")
        words = words[:index]
    query = " ".join(words).strip()
    if not query:
        raise ValueError("Что ищем? Например: /search nike")
    if len(query) > 100:
        raise ValueError("Сократи запрос до 100 символов.")
    return query, max_price


def remember_menu(context, name, message, value):
    menus = context.user_data.setdefault(name, {})
    menus[(message.chat_id, message.message_id)] = value
    while len(menus) > 10:
        menus.pop(next(iter(menus)))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отвечает на команду /start."""
    if update.message is not None:
        await update.message.reply_text(
            "Привет! Я ищу товары на 2nd STREET.\n"
            "Напиши /search nike или /search stone island.\n"
            "Затем выбери раздел и категорию кнопками.\n"
            "Выбери предел цены или укажи свой: /search nike --max 10000\n"
            "Покажу фото, цену, тип товара, размер и состояние.\n"
            "Под результатами — «Ещё 5» и выгрузка в CSV для Excel.\n"
            "Повторный поиск покажет другие товары. /reset_history — сброс истории.\n"
            "Обычный текст я пока повторяю."
        )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет запрос для конкретного меню и предлагает выбрать категорию."""
    if update.message is None:
        return
    try:
        query, max_price = parse_search_args(context.args)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    menu = await update.message.reply_text(
        f"Что ищем: {query}\nВыбери раздел:", reply_markup=category_keyboard()
    )
    remember_menu(context, "search_menus", menu, {
        "query": query, "max_price": max_price, "category_selected": False,
    })


async def choose_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает кнопки разделов и запускает поиск в выбранной категории."""
    callback = update.callback_query
    if callback is None or callback.message is None:
        return
    key = (callback.message.chat_id, callback.message.message_id)
    pending = context.user_data.get("search_menus", {})
    request = pending.get(key)
    if request is None:
        await callback.answer("Это меню устарело. Отправь /search ещё раз.", show_alert=True)
        return
    _, action, choice = (callback.data or "").split(":", 2)
    query = request["query"]
    if action == "group" and (choice in CATEGORY_GROUPS or choice == "root"):
        await callback.answer()
        group = None if choice == "root" else choice
        heading = "Выбери раздел:" if group is None else f"{CATEGORY_GROUPS[group][0]} — выбери категорию:"
        await callback.edit_message_text(
            f"Что ищем: {query}\n{heading}", reply_markup=category_keyboard(group)
        )
        return
    if action == "category" and (choice == "all" or choice in CATEGORY_LABELS):
        request["category"] = None if choice == "all" else choice
        request["category_selected"] = True
        await callback.answer()
        if request["max_price"] is None:
            await callback.edit_message_text(
                f"{query}\n{CATEGORY_LABELS.get(request['category'], 'Все товары')}\nВыбери максимальную цену:",
                reply_markup=price_keyboard(),
            )
            return
    elif action == "price" and request.get("category_selected") and choice in {"any", "5000", "10000", "20000", "50000"}:
        request["max_price"] = None if choice == "any" else int(choice)
        await callback.answer()
    else:
        await callback.answer("Неизвестная категория.", show_alert=True)
        return
    pending.pop(key)
    await callback.edit_message_text("Ищу на 2nd STREET…")
    await run_result_page(callback.message, callback.message, request, callback.from_user.id, context)


async def run_result_page(message, status, request, user_id, context):
    products, has_more = await show_results(
        message, status, request["query"], request.get("category"),
        user_id=user_id, max_price=request["max_price"],
    )
    if not products and not has_more:
        return
    rows = []
    if has_more:
        rows.append([InlineKeyboardButton("Ещё 5 →", callback_data="results:more")])
    if products:
        rows.append([InlineKeyboardButton("Скачать CSV для Excel", callback_data="results:csv")])
    footer = await message.reply_text(
        "Продолжить поиск или сохранить эту подборку?" if products else "Продолжить поиск на следующих страницах?",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    remember_menu(context, "result_menus", footer, {
        **request, "products": products, "has_more": has_more, "busy": False,
    })


async def result_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback = update.callback_query
    if callback is None or callback.message is None:
        return
    key = (callback.message.chat_id, callback.message.message_id)
    session = context.user_data.get("result_menus", {}).get(key)
    if session is None:
        await callback.answer("Эта подборка устарела. Отправь /search заново.", show_alert=True)
        return
    if callback.data == "results:csv":
        await callback.answer()
        await callback.message.reply_document(
            document=products_csv(session["products"]),
            filename="secondstreet-products.csv", caption="Товары из этой подборки. Цена в JPY.",
        )
        return
    if callback.data != "results:more" or not session["has_more"] or session["busy"]:
        await callback.answer("Эта подборка уже обработана. Используй последнюю кнопку «Ещё 5».")
        return
    await callback.answer()
    session["busy"] = True
    try:
        status = await callback.message.reply_text("Ищу следующие товары…")
        await run_result_page(callback.message, status, session, callback.from_user.id, context)
    except Exception:
        session["busy"] = False
        raise
    session["has_more"] = False
    # CSV остаётся доступным для предыдущей подборки.
    rows = [[InlineKeyboardButton("Скачать CSV для Excel", callback_data="results:csv")]] if session["products"] else []
    await callback.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows) if rows else None)


async def show_results(message, status, query: str, category: str | None = None, user_id: int | None = None, max_price: int | None = None):
    """Получает товары с фильтром сайта и отправляет карточки."""
    label = CATEGORY_LABELS.get(category, "Все товары")
    price_label = f"До ¥{max_price:,}" if max_price is not None else "Без ограничения цены"
    batch = None
    try:
        if user_id is None:
            products = await search_products(query, category=category, max_price=max_price)
        else:
            batch = await find_new_products(
                query, category=category, excluded_ids=HISTORY.seen_ids(user_id),
                start_page=HISTORY.page(user_id, query, category, max_price), max_price=max_price,
            )
            products = batch.products
    except SearchError:
        await status.edit_text(
            "Не удалось получить товары с сайта. Попробуй позже.\n"
            f"Можно открыть поиск вручную: {search_url(query, category, max_price=max_price)}",
            disable_web_page_preview=True,
        )
        return [], True

    if not products:
        if batch is not None:
            HISTORY.save_page(user_id, query, category, batch.page, max_price)
            detail = "На просмотренных страницах новых товаров нет. Повтори поиск, чтобы проверить дальше." if batch.has_more else "Новых товаров больше не найдено. Попробуй другой запрос или /reset_history."
        else:
            detail = "Ничего не найдено. Попробуй другой бренд или категорию через /search."
        await status.edit_text(
            f"{query}\n{label}\n{detail}"
        )
        return [], bool(batch and batch.has_more)

    await status.edit_text(f"2nd STREET — {query}\n{label}\n{price_label}\nПокажу товаров: {len(products)}")
    for number, product in enumerate(products, start=1):
        caption = (
            f"{number}. {product.title[:300]}\n\n"
            f"Цена: ¥{product.price_yen:,}\n"
            f"Тип товара: {product.category[:100]}\n"
            f"Размер: {product.size[:80]}\n"
            f"Состояние: {product.condition[:100]}"
        )
        buttons = [[InlineKeyboardButton("Открыть на 2nd STREET ↗", url=product.url)]]
        if product.image_url:
            buttons.append([InlineKeyboardButton("Фото в полном размере ↗", url=product.image_url)])
        keyboard = InlineKeyboardMarkup(buttons)
        photo_sent = False
        for image_url in dict.fromkeys((product.image_url, product.thumbnail_url)):
            if not image_url:
                continue
            try:
                await message.reply_photo(
                    photo=image_url, caption=caption, reply_markup=keyboard
                )
                photo_sent = True
                break
            except BadRequest:
                # Если большое фото недоступно, пробуем превью из выдачи.
                pass
        if photo_sent:
            if user_id is not None:
                HISTORY.mark(user_id, product.product_id)
            continue
        await message.reply_text(
            caption, reply_markup=keyboard, disable_web_page_preview=True
        )
        if user_id is not None:
            HISTORY.mark(user_id, product.product_id)
    if batch is not None:
        HISTORY.save_page(user_id, query, category, batch.page, max_price)
    return products, bool(batch and batch.has_more)


async def reset_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is not None and update.effective_user is not None:
        HISTORY.reset(update.effective_user.id)
        await update.message.reply_text("История очищена. Следующий /search снова начнёт с первых товаров.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повторяет полученное текстовое сообщение."""
    if update.message is not None and update.message.text is not None:
        await update.message.reply_text(update.message.text)


def main():
    """Читает токен, подключает обработчики и запускает получение сообщений."""
    load_dotenv(Path(__file__).with_name(".env"))
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "PASTE_NEW_TOKEN_HERE":
        raise SystemExit("Добавь новый токен в файл .env: BOT_TOKEN=твой_токен")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("search", search))
    application.add_handler(CommandHandler("reset_history", reset_history))
    application.add_handler(CallbackQueryHandler(choose_category, pattern=r"^search:(group|category|price):[a-z0-9]+$"))
    application.add_handler(CallbackQueryHandler(result_action, pattern=r"^results:(more|csv)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    print("Запускаем бота. Для остановки нажми Ctrl+C.")
    application.run_polling()


if __name__ == "__main__":
    main()
