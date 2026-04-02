import os
import asyncio
from dotenv import load_dotenv
import yt_dlp

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

# ========= НАСТРОЙКИ =========
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TEMP_DIR = "downloads"
SEARCH_COUNT = 15  # ← увеличил до 15 треков (3 страницы по 5)
TRACKS_PER_PAGE = 5

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)


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
        # Пробуем разные поля для исполнителя
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


# ========= СКАЧИВАНИЕ =========

def download_track(url: str, title: str):
    safe_name = "".join(c for c in title if c.isalnum() or c in " ._-")
    filepath = os.path.join(TEMP_DIR, f"{safe_name}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio',
        'outtmpl': filepath,
        'quiet': True,
        'noplaylist': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    return filename


# ========= ФУНКЦИЯ ДЛЯ СОЗДАНИЯ КЛАВИАТУРЫ С ПАГИНАЦИЕЙ =========

def create_pagination_keyboard(tracks: list, page: int):
    """Создает инлайн-клавиатуру с треками и кнопками пагинации"""
    keyboard = []
    
    # Вычисляем индексы для текущей страницы
    start_idx = page * TRACKS_PER_PAGE
    end_idx = min(start_idx + TRACKS_PER_PAGE, len(tracks))
    
    # Добавляем кнопки с треками
    for i in range(start_idx, end_idx):
        track = tracks[i]
        button_text = f"{i+1}. {track['artist']} - {track['title']}"[:60]
        keyboard.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"dl_{i}"
        )])
    
    # Добавляем кнопки пагинации (если нужно)
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
    
    # Добавляем информационную кнопку
    keyboard.append([InlineKeyboardButton(
        text=f"📄 Страница {page + 1} из {total_pages} | Всего: {len(tracks)} треков",
        callback_data="info"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ========= ХЕНДЛЕРЫ =========

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("🎵 Напиши название трека")


@dp.message()
async def search_music(message: types.Message):
    query = message.text.strip()

    msg = await message.answer("🔍 Ищу...")

    tracks = search_tracks(query)

    if not tracks:
        await msg.edit_text("❌ Ничего не найдено")
        return

    # Сохраняем в кеш
    if not hasattr(search_music, "cache"):
        search_music.cache = {}
    
    search_music.cache[message.chat.id] = {
        "tracks": tracks,
        "page": 0  # текущая страница
    }

    # Показываем первую страницу
    keyboard = create_pagination_keyboard(tracks, 0)
    await msg.edit_text("🎵 Выбери трек:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith("page_"))
async def change_page(callback: types.CallbackQuery):
    """Обработчик смены страницы"""
    page = int(callback.data.split("_")[1])
    
    cache_data = getattr(search_music, "cache", {}).get(callback.message.chat.id)
    
    if not cache_data:
        await callback.answer("❌ Поиск устарел, введи запрос заново")
        return
    
    tracks = cache_data["tracks"]
    cache_data["page"] = page
    
    # Обновляем клавиатуру
    keyboard = create_pagination_keyboard(tracks, page)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("dl_"))
async def download_selected(callback: types.CallbackQuery):
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

    # Отправляем статус в отдельное сообщение
    status_msg = await callback.message.answer(f"⏳ Качаю: {track['artist']} - {track['title']}...")

    try:
        filepath = download_track(track["url"], track["title"])

        # Отправляем аудио с указанием исполнителя
        await callback.message.answer_audio(
            audio=FSInputFile(filepath),
            title=track['title'],
            performer=track['artist']
        )

        os.remove(filepath)
        
        # Удаляем статусное сообщение
        await status_msg.delete()
        
        await callback.answer(f"✅ {track['artist']} - {track['title']} отправлен!")

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

    # Не удаляем исходное сообщение с вариантами


@dp.callback_query(lambda c: c.data == "info")
async def info_button(callback: types.CallbackQuery):
    """Просто отвечаем на кнопку с информацией"""
    await callback.answer("Используй кнопки Назад/Вперед для навигации")


# ========= ЗАПУСК =========

async def main():
    print("🚀 Bot started (with pagination!)")
    print(f"📊 Showing {TRACKS_PER_PAGE} tracks per page")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
