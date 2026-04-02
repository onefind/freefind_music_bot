import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
import yt_dlp

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError

# ========= НАСТРОЙКИ ИЗ .ENV =========
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN")
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))

TEMP_DIR = "downloads"
SEARCH_COUNT = 15
TRACKS_PER_PAGE = 5

# Проверки обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

if not CHANNEL_USERNAME:
    raise ValueError("CHANNEL_USERNAME не найден в .env")

if not CHANNEL_ID:
    raise ValueError("CHANNEL_ID не найден в .env")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Создаём отдельного бота для отправки логов (если токен указан)
log_bot = Bot(token=LOG_BOT_TOKEN) if LOG_BOT_TOKEN else None

if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)


# ========= ФУНКЦИИ ДЛЯ ЛОГОВ =========

async def send_log(message: str):
    """Отправляет лог в отдельного бота"""
    if not log_bot or not LOG_CHAT_ID:
        return
    
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_text = f"📅 `{timestamp}`\n📝 {message}"
        
        await log_bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=log_text,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"❌ Не удалось отправить лог: {e}")


async def send_startup_log():
    """Отправляет лог при запуске бота"""
    if not log_bot or not LOG_CHAT_ID:
        return
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    log_text = f"""
🚀 **БОТ ЗАПУЩЕН!**

📅 **Время:** `{timestamp}`
🔐 **Канал:** {CHANNEL_USERNAME}
🎵 **Формат:** MP3
    """
    
    await log_bot.send_message(
        chat_id=LOG_CHAT_ID,
        text=log_text,
        parse_mode="Markdown"
    )
    print("✅ Стартовый лог отправлен")


# ========= ПРОВЕРКА ПОДПИСКИ =========

async def check_subscription(user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        chat_member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        
        subscribed_statuses = ["creator", "administrator", "member"]
        
        return chat_member.status in subscribed_statuses
        
    except TelegramForbiddenError:
        print(f"⚠️ Бот не может проверить подписку пользователя {user_id}")
        return False
    except Exception as e:
        print(f"❌ Ошибка проверки подписки: {e}")
        return False


async def require_subscription(message: types.Message) -> bool:
    """Проверяет подписку и отправляет сообщение, если не подписан"""
    user_id = message.from_user.id
    
    if await check_subscription(user_id):
        return True
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📢 ПОДПИСАТЬСЯ НА КАНАЛ",
                url=f"https://t.me/{CHANNEL_USERNAME.replace('@', '')}"
            )],
            [InlineKeyboardButton(
                text="✅ ПРОВЕРИТЬ ПОДПИСКУ",
                callback_data="check_sub"
            )]
        ])
        
        await message.answer(
            "❌ **Доступ запрещён!**\n\n"
            "Для использования бота необходимо подписаться на наш канал.\n\n"
            "👇 **Нажми на кнопку ниже, подпишись, а затем нажми «ПРОВЕРИТЬ ПОДПИСКУ»**",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return False


# ========= ПОИСК (С ИСПОЛНИТЕЛЕМ) =========

def search_tracks(query: str):
    ydl_opts = {
        'quiet': True,
        'default_search': f'scsearch{SEARCH_COUNT}',
        'noplaylist': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=False)

    if "entries" not in info:
        return []

    results = []
    for entry in info["entries"]:
        artist = (
            entry.get("artist") or 
            entry.get("uploader") or 
            entry.get("creator") or
            entry.get("channel") or
            "Unknown Artist"
        )
        
        results.append({
            "title": entry.get("title"),
            "artist": artist,
            "url": entry.get("webpage_url")
        })

    return results


# ========= СКАЧИВАНИЕ С КОНВЕРТАЦИЕЙ В MP3 =========

def download_track(url: str, title: str):
    safe_name = "".join(c for c in title if c.isalnum() or c in " ._-")
    temp_template = os.path.join(TEMP_DIR, f"{safe_name}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio',
        'outtmpl': temp_template,
        'quiet': True,
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        base_filename = ydl.prepare_filename(info)
        filename = os.path.splitext(base_filename)[0] + '.mp3'

    return filename


# ========= КЛАВИАТУРА С ПАГИНАЦИЕЙ =========

def create_pagination_keyboard(tracks: list, page: int):
    keyboard = []
    
    start_idx = page * TRACKS_PER_PAGE
    end_idx = min(start_idx + TRACKS_PER_PAGE, len(tracks))
    
    for i in range(start_idx, end_idx):
        track = tracks[i]
        button_text = f"{i+1}. {track['artist']} - {track['title']}"[:60]
        keyboard.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"dl_{i}"
        )])
    
    nav_buttons = []
    total_pages = (len(tracks) + TRACKS_PER_PAGE - 1) // TRACKS_PER_PAGE
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️ Назад",
            callback_data=f"page_{page - 1}"
        ))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(
            text="Вперед ▶️",
            callback_data=f"page_{page + 1}"
        ))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton(
        text=f"📄 Страница {page + 1} из {total_pages} | Всего: {len(tracks)} треков",
        callback_data="info"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ========= ХЕНДЛЕРЫ =========

@dp.message(CommandStart())
async def start(message: types.Message):
    # Лог только команды /start
    await send_log(f"👤 Пользователь @{message.from_user.username or message.from_user.id} нажал /start")
    
    if await require_subscription(message):
        await message.answer(
            "🎵 **Привет от @mpsfind**\n\n"
            "Просто напиши название трека или исполнителя, и я найду музыку на SoundCloud.\n\n"
            "📌 **Доступно:**\n\n"
            "• Поиск до 15 треков\n"
            "• Пагинация результатов\n"
            "• Качество 192 kbps в формате MP3\n"
            "• Отображение исполнителя\n\n"
            "🔍 **Попробуй:** `Imagine Dragons` или `Billie Eilish`",
            parse_mode="Markdown"
        )


@dp.callback_query(lambda c: c.data == "check_sub")
async def check_subscription_callback(callback: types.CallbackQuery):
    """Проверка подписки по кнопке"""
    user_id = callback.from_user.id
    
    if await check_subscription(user_id):
        await callback.message.delete()
        await callback.message.answer(
            "✅ **Подписка подтверждена!**\n\n"
            "🎵 Теперь ты можешь пользоваться ботом.\n"
            "Просто напиши название трека или исполнителя.",
            parse_mode="Markdown"
        )
        await callback.answer("Доступ разрешён!")
    else:
        await callback.answer(
            "❌ Вы ещё не подписались на канал!\n"
            "Подпишитесь и нажмите «ПРОВЕРИТЬ ПОДПИСКУ» снова",
            show_alert=True
        )


@dp.message()
async def search_music(message: types.Message):
    if not await require_subscription(message):
        return
    
    query = message.text.strip()
    msg = await message.answer("🔍 Ищу...")

    try:
        tracks = search_tracks(query)

        if not tracks:
            await msg.edit_text("❌ Ничего не найдено")
            return

        if not hasattr(search_music, "cache"):
            search_music.cache = {}
        
        search_music.cache[message.chat.id] = {
            "tracks": tracks,
            "page": 0
        }

        keyboard = create_pagination_keyboard(tracks, 0)
        await msg.edit_text("🎵 Выбери трек:", reply_markup=keyboard)
        
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка при поиске: {e}")


@dp.callback_query(lambda c: c.data.startswith("page_"))
async def change_page(callback: types.CallbackQuery):
    if not await check_subscription(callback.from_user.id):
        await callback.answer("❌ Доступ запрещён! Подпишись на @mpsfindblog", show_alert=True)
        return
    
    page = int(callback.data.split("_")[1])
    
    cache_data = getattr(search_music, "cache", {}).get(callback.message.chat.id)
    
    if not cache_data:
        await callback.answer("❌ Поиск устарел, введи запрос заново")
        return
    
    tracks = cache_data["tracks"]
    cache_data["page"] = page
    
    keyboard = create_pagination_keyboard(tracks, page)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("dl_"))
async def download_selected(callback: types.CallbackQuery):
    if not await check_subscription(callback.from_user.id):
        await callback.answer("❌ Доступ запрещён! Подпишитесь на канал.", show_alert=True)
        return
    
    index = int(callback.data.split("_")[1])
    
    cache_data = getattr(search_music, "cache", {}).get(callback.message.chat.id)
    
    if not cache_data:
        await callback.answer("❌ Поиск устарел, введи запрос заново")
        return
    
    tracks = cache_data["tracks"]
    
    if index >= len(tracks):
        await callback.answer("❌ Ошибка")
        return
    
    track = tracks[index]
    status_msg = await callback.message.answer(f"⏳ Качаю и конвертирую: {track['artist']} - {track['title']}...")

    try:
        filepath = download_track(track["url"], track["title"])

        await callback.message.answer_audio(
            audio=FSInputFile(filepath),
            title=track['title'],
            performer=track['artist']
        )

        os.remove(filepath)
        await status_msg.delete()
        await callback.answer(f"✅ {track['artist']} - {track['title']} отправлен!")

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "info")
async def info_button(callback: types.CallbackQuery):
    await callback.answer("Используй кнопки Назад/Вперед для навигации")


# ========= ЗАПУСК =========

async def main():
    print("🚀 Music Bot started!")
    
    # Лог запуска бота
    await send_startup_log()
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
