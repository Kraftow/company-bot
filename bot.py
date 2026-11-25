import asyncio
import html
import logging
import os

import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
PORT = int(os.getenv("PORT", "10000"))

MESSENGERS = ["Telegram", "WhatsApp", "Viber", "Signal", "Другой"]

router = Router()
pool: asyncpg.Pool = None


# ───────────────────────── База данных (Postgres / Supabase) ─────────────────────────

async def init_db():
    global pool
    # statement_cache_size=0 — чтобы работало и через pooler Supabase
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3, statement_cache_size=0)
    await pool.execute(
        """CREATE TABLE IF NOT EXISTS companies (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            niche TEXT NOT NULL,
            phone TEXT NOT NULL,
            messenger TEXT NOT NULL,
            percent DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )"""
    )


async def add_company(user_id, name, niche, phone, messenger, percent):
    await pool.execute(
        "INSERT INTO companies (user_id, name, niche, phone, messenger, percent) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        user_id, name, niche, phone, messenger, percent,
    )


async def get_niches(user_id):
    rows = await pool.fetch(
        "SELECT MIN(niche) AS niche, COUNT(*) AS cnt FROM companies WHERE user_id = $1 "
        "GROUP BY LOWER(niche) ORDER BY LOWER(niche)",
        user_id,
    )
    return [(r["niche"], r["cnt"]) for r in rows]


async def get_companies(user_id, niche=None):
    if niche is None:
        return await pool.fetch(
            "SELECT * FROM companies WHERE user_id = $1 ORDER BY LOWER(niche), LOWER(name)",
            user_id,
        )
    return await pool.fetch(
        "SELECT * FROM companies WHERE user_id = $1 AND LOWER(niche) = LOWER($2) "
        "ORDER BY LOWER(name)",
        user_id, niche,
    )


async def delete_company(user_id, company_id):
    await pool.execute(
        "DELETE FROM companies WHERE id = $1 AND user_id = $2", company_id, user_id
    )


# ───────────────────────── Клавиатуры ─────────────────────────

BTN_ADD = "➕ Добавить компанию"
BTN_VIEW = "📋 Просмотр компаний"


def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_VIEW)]],
        resize_keyboard=True,
    )


def messenger_kb():
    rows = [
        [InlineKeyboardButton(text=m, callback_data=f"msgr:{m}") for m in MESSENGERS[:3]],
        [InlineKeyboardButton(text=m, callback_data=f"msgr:{m}") for m in MESSENGERS[3:]],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💾 Сохранить", callback_data="save"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel"),
            ]
        ]
    )


# ───────────────────────── Добавление компании ─────────────────────────

class AddCompany(StatesGroup):
    name = State()
    niche = State()
    phone = State()
    messenger = State()
    percent = State()
    confirm = State()


def fmt_percent(p):
    return f"{p:g}%"


def card_text(c, header=None):
    e = html.escape
    lines = []
    if header:
        lines.append(header)
        lines.append("")
    lines += [
        f"🏢 <b>{e(c['name'])}</b>",
        "━━━━━━━━━━━━━━━",
        f"🗂 <b>Ниша:</b> {e(c['niche'])}",
        f"📞 <b>Телефон:</b> <code>{e(c['phone'])}</code>",
        f"💬 <b>Мессенджер:</b> {e(c['messenger'])}",
        f"💰 <b>Процент:</b> {fmt_percent(c['percent'])}",
    ]
    return "\n".join(lines)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Я веду список ваших компаний-партнёров.\n"
        "Выберите действие в меню ниже.",
        reply_markup=main_kb(),
    )


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_kb())


@router.message(F.text == BTN_ADD)
async def add_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddCompany.name)
    await message.answer("🏢 Введите <b>название компании</b>:\n(отмена — /cancel)")


@router.message(AddCompany.name, F.text)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddCompany.niche)
    await message.answer("🗂 Введите <b>нишу</b> (например: логистика, IT, маркетинг):")


@router.message(AddCompany.niche, F.text)
async def add_niche(message: Message, state: FSMContext):
    await state.update_data(niche=message.text.strip())
    await state.set_state(AddCompany.phone)
    await message.answer("📞 Введите <b>номер телефона</b>:")


@router.message(AddCompany.phone, F.text)
async def add_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    digits = [ch for ch in phone if ch.isdigit()]
    if len(digits) < 5:
        await message.answer("⚠️ Похоже, это не номер телефона. Попробуйте ещё раз:")
        return
    await state.update_data(phone=phone)
    await state.set_state(AddCompany.messenger)
    await message.answer("💬 Выберите <b>мессенджер</b>:", reply_markup=messenger_kb())


@router.callback_query(AddCompany.messenger, F.data.startswith("msgr:"))
async def add_messenger(call: CallbackQuery, state: FSMContext):
    messenger = call.data.split(":", 1)[1]
    await state.update_data(messenger=messenger)
    await state.set_state(AddCompany.percent)
    await call.message.edit_text(f"💬 Мессенджер: <b>{html.escape(messenger)}</b>")
    await call.message.answer("💰 Введите <b>процент</b> (например: 10 или 7.5):")
    await call.answer()


@router.message(AddCompany.messenger, F.text)
async def add_messenger_text(message: Message):
    await message.answer("Пожалуйста, выберите мессенджер кнопкой выше 👆")


@router.message(AddCompany.percent, F.text)
async def add_percent(message: Message, state: FSMContext):
    raw = message.text.strip().replace("%", "").replace(",", ".").strip()
    try:
        percent = float(raw)
        if not 0 <= percent <= 100:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число от 0 до 100, например: 10 или 7.5")
        return
    await state.update_data(percent=percent)
    await state.set_state(AddCompany.confirm)
    data = await state.get_data()
    await message.answer(
        card_text(data, header="📝 <b>Проверьте данные:</b>"),
        reply_markup=confirm_kb(),
    )


@router.callback_query(AddCompany.confirm, F.data == "save")
async def save(call: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await add_company(
        call.from_user.id, d["name"], d["niche"], d["phone"], d["messenger"], d["percent"]
    )
    await state.clear()
    await call.message.edit_text(card_text(d, header="✅ <b>Компания сохранена!</b>"))
    await call.message.answer("Что дальше?", reply_markup=main_kb())
    await call.answer("Сохранено")


@router.callback_query(F.data == "cancel")
async def cancel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("✖️ Добавление отменено.")
    await call.answer()


# ───────────────────────── Просмотр и сортировка ─────────────────────────
# callback_data:
#   niches            — меню выбора ниши
#   show:<f>:<i>      — показать компанию i; f = "a" (все) или индекс ниши

async def niches_kb(user_id):
    niches = await get_niches(user_id)
    total = sum(c for _, c in niches)
    rows = [[InlineKeyboardButton(text=f"📚 Все компании ({total})", callback_data="show:a:0")]]
    for idx, (niche, cnt) in enumerate(niches):
        rows.append(
            [InlineKeyboardButton(text=f"🗂 {niche} ({cnt})", callback_data=f"show:{idx}:0")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == BTN_VIEW)
async def view_start(message: Message, state: FSMContext):
    await state.clear()
    if not await get_niches(message.from_user.id):
        await message.answer("Список пуст. Нажмите «➕ Добавить компанию».")
        return
    await message.answer(
        "🗂 <b>Сортировка по нишам</b>\nВыберите нишу или покажите все компании "
        "(они будут сгруппированы по нишам):",
        reply_markup=await niches_kb(message.from_user.id),
    )


@router.callback_query(F.data == "niches")
async def back_to_niches(call: CallbackQuery):
    if not await get_niches(call.from_user.id):
        await call.message.edit_text("Список пуст.")
    else:
        await call.message.edit_text(
            "🗂 <b>Сортировка по нишам</b>\nВыберите нишу:",
            reply_markup=await niches_kb(call.from_user.id),
        )
    await call.answer()


async def render_company(call: CallbackQuery, f: str, i: int):
    user_id = call.from_user.id

    niche = None
    if f != "a":
        niches = await get_niches(user_id)
        idx = int(f)
        if idx >= len(niches):
            await call.answer("Список изменился, откройте заново", show_alert=True)
            return
        niche = niches[idx][0]

    companies = await get_companies(user_id, niche)
    if not companies:
        await call.message.edit_text(
            "В этой нише пока пусто.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🗂 К нишам", callback_data="niches")]]
            ),
        )
        await call.answer()
        return

    total = len(companies)
    i %= total
    c = companies[i]
    title = f"📋 <b>{html.escape(niche) if niche else 'Все компании'}</b> — {i + 1} из {total}"

    rows = []
    if total > 1:
        rows.append(
            [
                InlineKeyboardButton(text="◀️ Назад", callback_data=f"show:{f}:{(i - 1) % total}"),
                InlineKeyboardButton(text=f"{i + 1}/{total}", callback_data="noop"),
                InlineKeyboardButton(text="Следующая ▶️", callback_data=f"show:{f}:{(i + 1) % total}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{c['id']}:{f}:{i}")])
    rows.append([InlineKeyboardButton(text="🗂 К нишам", callback_data="niches")])

    await call.message.edit_text(
        card_text(c, header=title), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await call.answer()


@router.callback_query(F.data.startswith("show:"))
async def show_company(call: CallbackQuery):
    _, f, i = call.data.split(":")
    await render_company(call, f, int(i))


@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("del:"))
async def ask_delete(call: CallbackQuery):
    _, cid, f, i = call.data.split(":")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delok:{cid}:{f}"),
                InlineKeyboardButton(text="↩️ Нет", callback_data=f"show:{f}:{i}"),
            ]
        ]
    )
    await call.message.edit_reply_markup(reply_markup=kb)
    await call.answer("Удалить эту компанию?")


@router.callback_query(F.data.startswith("delok:"))
async def do_delete(call: CallbackQuery):
    _, cid, f = call.data.split(":")
    await delete_company(call.from_user.id, int(cid))
    await call.answer("Удалено")
    niches = await get_niches(call.from_user.id)
    if not niches:
        await call.message.edit_text("Список пуст. Нажмите «➕ Добавить компанию».")
        return
    if f != "a" and int(f) >= len(niches):
        f = "a"
    await render_company(call, f, 0)


# ───────────────────────── Мини веб-сервер (для бесплатного хостинга) ─────────────────────────

async def start_web_server():
    async def health(_request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()


# ───────────────────────── Запуск ─────────────────────────

async def main():
    logging.basicConfig(level=logging.INFO)
    await start_web_server()
    await init_db()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
