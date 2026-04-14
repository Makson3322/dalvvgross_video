import asyncio
import re
import sqlite3
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv
import os

load_dotenv()

# Конфигурация из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
MODERATION_CHANNEL_ID = int(os.getenv("MODERATION_CHANNEL_ID"))
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID"))

# Инициализация бота
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# Регулярка для YouTube ссылок
YOUTUBE_REGEX = re.compile(
    r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+'
)


# FSM States
class VideoSubmission(StatesGroup):
    waiting_for_link = State()
    confirming_link = State()


# База данных
def init_db():
    conn = sqlite3.connect("video_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_banned INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            video_url TEXT,
            status TEXT,
            submitted_at TEXT,
            moderated_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    """)
    conn.commit()
    conn.close()


def add_user(user_id: int, username: str, first_name: str):
    conn = sqlite3.connect("video_bot.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def is_user_banned(user_id: int) -> bool:
    conn = sqlite3.connect("video_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1


def ban_user(user_id: int):
    conn = sqlite3.connect("video_bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def add_submission(user_id: int, video_url: str) -> int:
    conn = sqlite3.connect("video_bot.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO submissions (user_id, video_url, status, submitted_at) VALUES (?, ?, ?, ?)",
        (user_id, video_url, "pending", datetime.now().isoformat())
    )
    submission_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return submission_id


def update_submission_status(submission_id: int, status: str):
    conn = sqlite3.connect("video_bot.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE submissions SET status = ?, moderated_at = ? WHERE id = ?",
        (status, datetime.now().isoformat(), submission_id)
    )
    conn.commit()
    conn.close()


def get_submission(submission_id: int) -> Optional[tuple]:
    conn = sqlite3.connect("video_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, video_url FROM submissions WHERE id = ?", (submission_id,))
    result = cursor.fetchone()
    conn.close()
    return result


# Клавиатуры
def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📹 Отправить видео", callback_data="submit_video")]
    ])
    return keyboard


def get_confirm_submit_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, отправить", callback_data="confirm_submit")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_submit")]
    ])
    return keyboard


def get_preview_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Всё верно", callback_data="approve_preview")],
        [InlineKeyboardButton(text="✏️ Изменить ссылку", callback_data="edit_link")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_submit")]
    ])
    return keyboard


def get_moderation_keyboard(submission_id: int, user_id: int):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod_approve_{submission_id}"),
            InlineKeyboardButton(text="❌ Отказать", callback_data=f"mod_reject_{submission_id}")
        ],
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"mod_ban_{submission_id}_{user_id}")]
    ])
    return keyboard


# Хендлеры
@router.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    add_user(user.id, user.username or "", user.first_name or "")

    if is_user_banned(user.id):
        await message.answer("🚫 <b>Вы заблокированы и не можете использовать бота.</b>")
        return

    welcome_text = (
        f"👋 <b>Привет, {user.first_name}!</b>\n\n"
        "🎬 Этот бот создан для отправки видео на стрим!\n\n"
        "📝 <b>Как это работает:</b>\n"
        "1️⃣ Нажми кнопку <b>«Отправить видео»</b>\n"
        "2️⃣ Отправь ссылку на YouTube видео\n"
        "3️⃣ Подтверди отправку\n"
        "4️⃣ Дождись модерации\n\n"
        "✨ Давай начнём!"
    )

    await message.answer(welcome_text, reply_markup=get_main_keyboard())


@router.callback_query(F.data == "submit_video")
async def callback_submit_video(callback: CallbackQuery):
    if is_user_banned(callback.from_user.id):
        await callback.answer("🚫 Вы заблокированы!", show_alert=True)
        return

    confirm_text = (
        "📹 <b>Отправка видео</b>\n\n"
        "Вы уверены, что хотите отправить видео на модерацию?"
    )

    await callback.message.edit_text(confirm_text, reply_markup=get_confirm_submit_keyboard())
    await callback.answer()


@router.callback_query(F.data == "confirm_submit")
async def callback_confirm_submit(callback: CallbackQuery, state: FSMContext):
    if is_user_banned(callback.from_user.id):
        await callback.answer("🚫 Вы заблокированы!", show_alert=True)
        return

    instruction_text = (
        "🔗 <b>Отправьте ссылку на YouTube видео</b>\n\n"
        "Поддерживаемые форматы:\n"
        "• youtube.com/watch?v=...\n"
        "• youtu.be/...\n"
        "• youtube.com/shorts/..."
    )

    await callback.message.edit_text(instruction_text)
    await state.set_state(VideoSubmission.waiting_for_link)
    await callback.answer()


@router.callback_query(F.data == "cancel_submit")
async def callback_cancel_submit(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    cancel_text = (
        "❌ <b>Отправка отменена</b>\n\n"
        "Вы можете начать заново в любое время!"
    )

    await callback.message.edit_text(cancel_text, reply_markup=get_main_keyboard())
    await callback.answer()


@router.message(StateFilter(VideoSubmission.waiting_for_link))
async def process_video_link(message: Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("🚫 Вы заблокированы!")
        return

    video_url = message.text.strip()

    if not YOUTUBE_REGEX.match(video_url):
        await message.answer(
            "❌ <b>Неверная ссылка!</b>\n\n"
            "Пожалуйста, отправьте корректную ссылку на YouTube видео.\n\n"
            "Примеры:\n"
            "• https://youtube.com/watch?v=dQw4w9WgXcQ\n"
            "• https://youtu.be/dQw4w9WgXcQ\n"
            "• https://youtube.com/shorts/dQw4w9WgXcQ"
        )
        return

    await state.update_data(video_url=video_url)

    preview_text = (
        "👀 <b>Предварительный просмотр</b>\n\n"
        f"🔗 Ваша ссылка:\n<code>{video_url}</code>\n\n"
        "Всё верно?"
    )

    await message.answer(preview_text, reply_markup=get_preview_keyboard())
    await state.set_state(VideoSubmission.confirming_link)


@router.callback_query(F.data == "edit_link", StateFilter(VideoSubmission.confirming_link))
async def callback_edit_link(callback: CallbackQuery, state: FSMContext):
    edit_text = (
        "✏️ <b>Изменение ссылки</b>\n\n"
        "Отправьте новую ссылку на YouTube видео:"
    )

    await callback.message.edit_text(edit_text)
    await state.set_state(VideoSubmission.waiting_for_link)
    await callback.answer()


@router.callback_query(F.data == "approve_preview", StateFilter(VideoSubmission.confirming_link))
async def callback_approve_preview(callback: CallbackQuery, state: FSMContext):
    if is_user_banned(callback.from_user.id):
        await callback.answer("🚫 Вы заблокированы!", show_alert=True)
        return

    data = await state.get_data()
    video_url = data.get("video_url")

    submission_id = add_submission(callback.from_user.id, video_url)

    user = callback.from_user
    mod_text = (
        "🎬 <b>Новое видео на модерацию</b>\n\n"
        f"👤 От: {user.first_name}"
        f"{' (@' + user.username + ')' if user.username else ''}\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"🔗 Ссылка:\n{video_url}"
    )

    try:
        await bot.send_message(
            MODERATION_CHANNEL_ID,
            mod_text,
            reply_markup=get_moderation_keyboard(submission_id, user.id)
        )

        success_text = (
            "✅ <b>Видео отправлено на модерацию!</b>\n\n"
            "Ожидайте решения модераторов.\n"
            "Вы получите уведомление о результате."
        )

        await callback.message.edit_text(success_text, reply_markup=get_main_keyboard())

    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Ошибка при отправке:</b>\n<code>{str(e)}</code>",
            reply_markup=get_main_keyboard()
        )

    await state.clear()
    await callback.answer()


# Модерация
@router.callback_query(F.data.startswith("mod_approve_"))
async def callback_mod_approve(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав!", show_alert=True)
        return

    submission_id = int(callback.data.split("_")[2])
    submission = get_submission(submission_id)

    if not submission:
        await callback.answer("❌ Заявка не найдена!", show_alert=True)
        return

    user_id, video_url = submission
    update_submission_status(submission_id, "approved")

    public_text = (
        "🎬 <b>Новое видео для стрима!</b>\n\n"
        f"🔗 {video_url}"
    )

    try:
        await bot.send_message(PUBLIC_CHANNEL_ID, public_text)
        await bot.send_message(
            user_id,
            "✅ <b>Ваше видео одобрено!</b>\n\nОно будет показано на стриме. Спасибо за участие! 🎉"
        )

        await callback.message.edit_text(
            callback.message.text + "\n\n✅ <b>ОДОБРЕНО</b>",
            reply_markup=None
        )
        await callback.answer("✅ Видео одобрено и опубликовано!")

    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("mod_reject_"))
async def callback_mod_reject(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав!", show_alert=True)
        return

    submission_id = int(callback.data.split("_")[2])
    submission = get_submission(submission_id)

    if not submission:
        await callback.answer("❌ Заявка не найдена!", show_alert=True)
        return

    user_id, video_url = submission
    update_submission_status(submission_id, "rejected")

    try:
        await bot.send_message(
            user_id,
            "❌ <b>Ваше видео было отклонено!</b>\n\nПопробуйте отправить другое видео."
        )

        await callback.message.edit_text(
            callback.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>",
            reply_markup=None
        )
        await callback.answer("❌ Видео отклонено!")

    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("mod_ban_"))
async def callback_mod_ban(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав!", show_alert=True)
        return

    parts = callback.data.split("_")
    submission_id = int(parts[2])
    user_id = int(parts[3])

    submission = get_submission(submission_id)

    if not submission:
        await callback.answer("❌ Заявка не найдена!", show_alert=True)
        return

    ban_user(user_id)
    update_submission_status(submission_id, "banned")

    try:
        await bot.send_message(
            user_id,
            "🚫 <b>Вы были заблокированы!</b>\n\nВы больше не можете отправлять видео через этого бота."
        )

        await callback.message.edit_text(
            callback.message.text + "\n\n🚫 <b>ПОЛЬЗОВАТЕЛЬ ЗАБЛОКИРОВАН</b>",
            reply_markup=None
        )
        await callback.answer("🚫 Пользователь заблокирован!")

    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


# Запуск бота
async def main():
    init_db()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    print("🚀 Бот запущен!")
    asyncio.run(main())
