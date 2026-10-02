# Публикация 2ndstreetparcer на GitHub

Репозиторий проекта: https://github.com/atlaceo/2ndstreetparcer.
Исходники и README опубликованы. Основная ветка — `main`.
Ниже сохранён порядок создания репозитория для будущих проектов.

1. Создай аккаунт: https://github.com/signup.
2. Открой https://github.com/new.
3. Название: `2ndstreetparcer`.
4. Описание: `Python Telegram bot for 2nd STREET: category and price filters, product photos, deduplication, CSV export.`
5. Видимость: **Public**, чтобы проект можно было показать заказчикам.
6. Не добавляй README, `.gitignore` и лицензию в форме: подготовленные файлы уже есть локально.
7. Создай репозиторий и сохрани его ссылку.

Далее нужно настроить имя автора коммитов и email GitHub (можно приватный noreply email),
создать первый коммит и отправить ветку `main`. Пароль от аккаунта в переписке не нужен.

## Что публикуется

Python-код, тесты, README, инструкции, `.env.example`, `.gitignore` и GitHub Actions.

## Что остаётся на компьютере

`.env` с токеном, `.venv`, база истории `search_history.sqlite3`, кэш и логи.
Эти файлы исключены через `.gitignore`. Не загружай папку проекта целиком вручную.

Архив `dist/2ndstreetparcer.zip`, если он подготовлен, содержит только файлы для публикации.
GitHub не распаковывает загруженный ZIP в репозиторий: для ручной загрузки его надо сначала распаковать.

## После публикации

- Проверить README и статус тестов во вкладке Actions.
- Добавить topics: `python`, `telegram-bot`, `web-scraping`, `sqlite`, `csv`.
- Записать короткую демонстрацию по сценарию из `docs/portfolio.md`.
- Закрепить репозиторий в профиле GitHub.
