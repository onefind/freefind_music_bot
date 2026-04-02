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
SEARCH_COUNT = 15
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


# ========= СКАЧИВАНИЕ С КОНВЕРТАЦИЕЙ В M4A =========

def download_track(url: str, title: str):
    safe_name = "".join(c for c in title if c.isalnum() or c in " ._-")
    # Временный шаблон (без расширения, добавится автоматически)
    temp_template = os.path.join(TEMP_DIR, f"{safe_name}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio',  # Приоритет M4A
        'outtmpl': temp_template,
        'quiet': True,
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'm4a',      # Конвертируем в M4A
            'preferredquality': '192',     # Битрейт 192 kbps
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Получаем имя файла без расширения
        base_filename = ydl.prepare_filename(info)
        # Убираем старое расширение и добавляем .m4a
        filename = os.path.splitext(base_filename)[0] + '.m4a'

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
    await message.answer("🎵 Напиши название трека")


@dp.message()
async def search_music(message: types.Message):
    query = message.text.strip()

    msg = await message.answer("🔍 Ищу...")

    tracks = search_tracks(query)

    if not tracks:
        await msg.edit_text("❌ Ничего не найдено")
        return

    # Кеш с пагинацией
    if not hasattr(search_music, "cache"):
        search_music.cache = {}
    
    search_music.cache[message.chat.id] = {
        "tracks": tracks,
        "page": 0
    }

    keyboard = create_pagination_keyboard(tracks, 0)
    await msg.edit_text("🎵 Выбери трек:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith("page_"))
async def change_page(callback: types.CallbackQuery):
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
    status_msg = await callback.message.answer(f"⏳ Качаю и конвертирую: {track['artist']} - {track['title']}...")

    try:
        filepath = download_track(track["url"], track["title"])

        # Отправляем аудио с указанием исполнителя (теперь точно M4A)
        await callback.message.answer_audio(
            audio=FSInputFile(filepath),
            title=track['title'],
            performer=track['artist']
        )

        # Удаляем файл и статусное сообщение
        os.remove(filepath)
        await status_msg.delete()
        
        await callback.answer(f"✅ {track['artist']} - {track['title']} отправлен!")

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

    # Не удаляем исходное сообщение с вариантами


@dp.callback_query(lambda c: c.data == "info")
async def info_button(callback: types.CallbackQuery):
    await callback.answer("Используй кнопки Назад/Вперед для навигации")


# ========= ЗАПУСК =========

async def main():
    print("🚀 Music Bot started!")
    print(f"📊 Search: {SEARCH_COUNT} tracks, {TRACKS_PER_PAGE} per page")
    print("🎵 Все треки конвертируются в M4A для правильного отображения")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
