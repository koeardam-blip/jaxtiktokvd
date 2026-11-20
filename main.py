import asyncio
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from dotenv import load_dotenv

load_dotenv()
from telegram import Update, InputMediaPhoto
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")
MAX_UPLOAD_BYTES = 49 * 1024 * 1024  # leave a little room below Telegram Bot API limit
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "180"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("tiktok-downloader")

URL_RE = re.compile(r"https?://[^\s<>]+")


def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at INTEGER,
                is_banned INTEGER DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                url TEXT,
                created_at INTEGER,
                success INTEGER
            )
        """)


def save_user(user):
    if not user:
        return
    with db() as con:
        con.execute("""
            INSERT INTO users(user_id, username, first_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
        """, (user.id, user.username, user.first_name, int(time.time())))


def is_banned(user_id):
    with db() as con:
        row = con.execute(
            "SELECT is_banned FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
    return bool(row and row[0])


def set_banned(user_id, banned):
    with db() as con:
        con.execute(
            "UPDATE users SET is_banned=? WHERE user_id=?",
            (1 if banned else 0, user_id),
        )


def log_download(user_id, url, success):
    with db() as con:
        con.execute(
            "INSERT INTO downloads(user_id, url, created_at, success) VALUES (?, ?, ?, ?)",
            (user_id, url[:1000], int(time.time()), 1 if success else 0),
        )


def valid_tiktok_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "tiktok.com" or host.endswith(".tiktok.com")
    except Exception:
        return False


def is_admin(user_id):
    return user_id in ADMIN_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)
    if is_banned(user.id):
        return
    await update.message.reply_text(
        "🎬 TikTok Video & Photo Downloader Bot\n\n"
        "TikTok Video နဲ့ Photo Carousel link တွေကို ပို့ပြီး Save လုပ်နိုင်ပါတယ်။\n\n"
        "TikTok link ကို ဒီမှာပို့ပေးပါ။"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "အသုံးပြုပုံ\n"
        "1. TikTok video link ကို copy လုပ်ပါ။\n"
        "2. ဒီ Bot ထဲ paste လုပ်ပြီး ပို့ပါ။\n"
        "3. Download ပြီးရင် video ပြန်ပို့ပေးပါမယ်။\n\n"
        "မှတ်ချက်: Private, deleted, region-restricted သို့မဟုတ် "
        "download ခွင့်မရှိတဲ့ video တွေကို ရယူနိုင်မယ်လို့ အာမမခံပါ။"
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "TikTok Downloader Bot\n"
        "Only use this service for videos you own or have permission to download."
    )



IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}


def _gallery_dl_download(url: str, folder: str):
    """
    gallery-dl handles TikTok photo-mode/carousel posts.
    It may also download other supported media; caller classifies the files.
    """
    executable = shutil.which("gallery-dl")
    if executable:
        command = [executable, "--no-mtime", "-D", folder, url]
    else:
        command = [sys.executable, "-m", "gallery_dl", "--no-mtime", "-D", folder, url]

    timeout = int(os.getenv("GALLERY_DL_TIMEOUT", "90"))
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gallery-dl failed").strip()
        logger.info("gallery-dl did not return media: %s", detail[:500])


async def download_tiktok_photos(url: str, folder: str):
    """Return (image_paths, title); an empty list means no photo carousel found."""
    def run():
        _gallery_dl_download(url, folder)
        images = sorted(
            p for p in Path(folder).rglob("*")
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
            and p.stat().st_size > 0
        )
        title = "TikTok Photo"
        return [str(p) for p in images], title

    return await asyncio.wait_for(asyncio.to_thread(run), timeout=DOWNLOAD_TIMEOUT)


async def download_tiktok(url: str, folder: str):
    # Video fallback: yt-dlp is used for TikTok video posts.
    outtmpl = str(Path(folder) / "video.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 20,
        "max_filesize": MAX_UPLOAD_BYTES,
    }

    def run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title") or "TikTok video"
            requested = info.get("requested_downloads") or []
            candidates = [d.get("filepath") for d in requested if d.get("filepath")]
            candidates.extend(str(p) for p in Path(folder).glob("video.*"))
            for candidate in candidates:
                path = Path(candidate)
                if path.exists() and path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                    return str(path), title
            raise FileNotFoundError(
                "Video file not found. This may be a TikTok photo carousel; "
                "gallery-dl is required for photo posts."
            )

    return await asyncio.wait_for(asyncio.to_thread(run), timeout=DOWNLOAD_TIMEOUT)


async def send_photo_files(message, image_paths, title):
    """Send photos in Telegram albums (maximum 10 items per album)."""
    valid = []
    for item in image_paths:
        path = Path(item)
        if path.exists() and path.stat().st_size <= MAX_UPLOAD_BYTES:
            valid.append(path)

    if not valid:
        raise ValueError("ပုံဖိုင်မတွေ့ပါ သို့မဟုတ် Telegram upload limit ထက်ကြီးနေပါတယ်။")

    caption = f"🖼️ {title[:700]}\n\nPhoto download ပြီးပါပြီ။"
    if len(valid) == 1:
        with valid[0].open("rb") as photo:
            await message.reply_photo(
                photo=photo,
                caption=caption,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )
        return

    # Telegram media groups accept 2–10 items per request.
    for offset in range(0, len(valid), 10):
        batch = valid[offset:offset + 10]
        opened = []
        try:
            media = []
            for index, path in enumerate(batch):
                file_obj = path.open("rb")
                opened.append(file_obj)
                media.append(
                    InputMediaPhoto(
                        media=file_obj,
                        caption=caption if offset == 0 and index == 0 else None,
                    )
                )
            await message.get_bot().send_media_group(
                chat_id=message.chat_id,
                media=media,
                reply_to_message_id=message.message_id,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )
        finally:
            for file_obj in opened:
                file_obj.close()



async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    if not user or not message:
        return
    save_user(user)
    if is_banned(user.id):
        await message.reply_text("ဒီ Bot ကို အသုံးပြုခွင့် ပိတ်ထားပါတယ်။")
        return

    match = URL_RE.search(message.text or "")
    if not match:
        return
    url = match.group(0).rstrip(").,!?]")
    if not valid_tiktok_url(url):
        await message.reply_text("TikTok link ပဲ ပို့ပေးပါ။")
        return

    status = await message.reply_text("⏳ TikTok Link ကို စစ်ဆေးပြီး Download လုပ်နေပါတယ်...")
    folder = tempfile.mkdtemp(prefix=f"tt_{user.id}_")
    success = False
    try:
        # First try photo-mode/carousel with gallery-dl.
        await context.bot.send_chat_action(message.chat_id, ChatAction.UPLOAD_PHOTO)
        image_paths, photo_title = await download_tiktok_photos(url, folder)

        if image_paths:
            await send_photo_files(message, image_paths, photo_title)
            success = True
            await status.edit_text(f"✅ Photo {len(image_paths)} ပုံ Download ပြီးပါပြီ။")
            return

        # If there were no images, fall back to the existing yt-dlp video flow.
        await context.bot.send_chat_action(message.chat_id, ChatAction.UPLOAD_VIDEO)
        file_path, title = await download_tiktok(url, folder)
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError("Video file missing")
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise ValueError("ဒီ Video က Telegram upload limit ထက်ကြီးနေပါတယ်။")
        with path.open("rb") as video:
            await message.reply_video(
                video=video,
                caption=f"🎬 {title[:700]}\n\nDownload ပြီးပါပြီ။ Save လုပ်နိုင်ပါပြီ။",
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )
        success = True
        await status.edit_text("✅ Video Download ပြီးပါပြီ။")
    except asyncio.TimeoutError:
        await status.edit_text("⌛ Download ကြာလွန်းနေပါတယ်။ နောက်မှ ပြန်စမ်းပါ။")
    except Exception as exc:
        logger.exception("Download failed")
        safe_error = str(exc)[:250]
        await status.edit_text(
            "❌ Download မအောင်မြင်ပါ။\n"
            "Link က private ဖြစ်တာ၊ TikTok က format ပြောင်းထားတာ၊ "
            "gallery-dl/yt-dlp version ဟောင်းနေတာ သို့မဟုတ် network ပြဿနာ ဖြစ်နိုင်ပါတယ်။\n"
            f"အသေးစိတ်: {safe_error}"
        )
    finally:
        log_download(user.id, url, success)
        shutil.rmtree(folder, ignore_errors=True)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with db() as con:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        banned = con.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
        success = con.execute("SELECT COUNT(*) FROM downloads WHERE success=1").fetchone()[0]
    await update.message.reply_text(
        f"📊 Bot Statistics\n\n"
        f"Users: {users}\nBanned: {banned}\n"
        f"Total requests: {total}\nSuccessful downloads: {success}"
    )


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with db() as con:
        rows = con.execute(
            "SELECT user_id, username, first_name, is_banned "
            "FROM users ORDER BY joined_at DESC LIMIT 30"
        ).fetchall()
    if not rows:
        await update.message.reply_text("User မရှိသေးပါ။")
        return
    lines = ["👥 နောက်ဆုံး User 30 ယောက်:"]
    for uid, username, first_name, banned in rows:
        name = first_name or "Unknown"
        handle = f"@{username}" if username else "No username"
        state = "⛔ Banned" if banned else "✅ Active"
        lines.append(f"{uid} | {name} | {handle} | {state}")
    await update.message.reply_text("\n".join(lines)[:4000])


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ: /ban USER_ID")
        return
    uid = int(context.args[0])
    set_banned(uid, True)
    await update.message.reply_text(f"⛔ {uid} ကို Ban လုပ်ပြီးပါပြီ။")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ: /unban USER_ID")
        return
    uid = int(context.args[0])
    set_banned(uid, False)
    await update.message.reply_text(f"✅ {uid} ကို Unban လုပ်ပြီးပါပြီ။")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("အသုံးပြုပုံ: /broadcast မင်းပို့ချင်တဲ့စာ")
        return
    with db() as con:
        ids = [r[0] for r in con.execute(
            "SELECT user_id FROM users WHERE is_banned=0"
        ).fetchall()]
    sent = 0
    failed = 0
    for uid in ids:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await update.message.reply_text(f"Broadcast ပြီးပါပြီ။\nSent: {sent}\nFailed: {failed}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable မသတ်မှတ်ရသေးပါ။")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    logger.info("Bot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
