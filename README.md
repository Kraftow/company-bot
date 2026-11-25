# Бот «Мои компании» (Telegram)

Запуск: `python bot.py`. Нужны переменные окружения:
- `BOT_TOKEN` — токен от @BotFather
- `DATABASE_URL` — строка подключения к Postgres (Supabase, Session pooler)
- `PORT` — задаёт хостинг (Render делает это сам)

Данные хранятся в Postgres, поэтому не пропадают при перезапуске хостинга.
