import os
import textwrap
from pathlib import Path
from dotenv import load_dotenv
import yt_dlp
import anthropic
import subprocess
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# ── Load env
load_dotenv()
BOT_TOKEN  = os.getenv("BOT_TOKEN")
MY_ID      = int(os.getenv("MY_ID"))
CLIENT_ID  = int(os.getenv("CLIENT_ID"))
CLAUDE_KEY = os.getenv("CLAUDE_KEY")
ALLOWED    = {MY_ID, CLIENT_ID}

TMP_DIR      = os.path.join(os.environ.get("TEMP", "C:\\temp"), "videobot")
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.json")
os.makedirs(TMP_DIR, exist_ok=True)

WAIT_CAPTION = 1
ai = anthropic.Anthropic(api_key=CLAUDE_KEY)


def allowed(uid):
    return uid in ALLOWED


def rewrite_caption(original):
    msg = ai.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": (
            "Rewrite this social media caption. "
            "Keep same meaning, topic, language and emojis style "
            "but change wording completely to avoid copy-paste detection. "
            "Return ONLY the new caption, nothing else.\n\n"
            f"Original: {original}"
        )}]
    )
    return msg.content[0].text.strip()


def download_video(url, out_dir):
    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": os.path.join(out_dir, "input.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
    }
    if ("instagram.com" in url or "tiktok.com" in url) and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        ext  = info.get("ext", "mp4")

    path = os.path.join(out_dir, f"input.{ext}")
    if not os.path.exists(path):
        for f in Path(out_dir).glob("input.*"):
            path = str(f)
            break
    return path


def get_video_info(video_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True
    )
    out = result.stdout.strip()
    if not out or "," not in out:
        return 1080, 1920
    w, h = map(int, out.split(","))
    return w, h


def make_banner(path, w, h, caption=None):
    img  = Image.new("RGB", (w, h), color=(255, 255, 255))
    if caption:
        draw      = ImageDraw.Draw(img)
        font_size = max(42, w // 17)

        # Try emoji font first, then bold, then default
        font = None
        for font_path in [
            "C:/Windows/Fonts/seguiemj.ttf",
            "C:/Windows/Fonts/NotoColorEmoji.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]:
            try:
                font = ImageFont.truetype(font_path, font_size)
                break
            except:
                continue
        if font is None:
            font = ImageFont.load_default()

        wrapped = textwrap.fill(caption, width=22)
        bbox    = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=8)
        tw      = bbox[2] - bbox[0]
        th      = bbox[3] - bbox[1]
        tx      = (w - tw) // 2
        ty      = (h - th) // 2
        draw.multiline_text((tx, ty), wrapped, fill=(0, 0, 0),
                            font=font, align="center", spacing=8)
    img.save(path)


def process_video_file(video_path, caption, out_path):
    w, h = get_video_info(video_path)

    top_h = int(h * 0.22)   # Upar — caption yahan
    bot_h = int(h * 0.10)   # Neeche — empty white

    top_path = os.path.join(TMP_DIR, "top.png")
    bot_path = os.path.join(TMP_DIR, "bot.png")

    make_banner(top_path, w, top_h, caption=caption)
    make_banner(bot_path, w, bot_h, caption=None)

    # Black bars hatao + white banners lagao
    filter_complex = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:-1:-1:color=white,setsar=1[vid];"
        f"[1:v]scale={w}:{top_h}:force_original_aspect_ratio=disable[top];"
        f"[2:v]scale={w}:{bot_h}:force_original_aspect_ratio=disable[bot];"
        f"[top][vid][bot]vstack=inputs=3[out]"
)

    subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", top_path,
        "-i", bot_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:a", "copy",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        out_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── HANDLERS

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "👋 *Video Repurpose Bot*\n\n"
        "📎 YouTube / Instagram / TikTok link bhejo\n\n"
        "✅ AI Caption Auto Rewrite\n"
        "✅ White Background Upar + Neeche\n"
        "✅ 😭 Emoji Auto Add\n\n"
        "_Bas link paste karo!_",
        parse_mode="Markdown"
    )


async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ Valid video link bhejo!")
        return
    ctx.user_data["url"] = url
    keyboard = [[
        InlineKeyboardButton("🤖 Auto AI Caption", callback_data="auto"),
        InlineKeyboardButton("✏️ Manual Caption",  callback_data="manual"),
    ]]
    await update.message.reply_text(
        "✅ Link mil gaya!\n\n*Caption kaise chahiye?*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def caption_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not allowed(query.from_user.id):
        return
    if query.data == "auto":
        await query.edit_message_text("⏳ Processing... thoda wait karo!")
        await handle_process(query.message, ctx, caption_override=None)
        return ConversationHandler.END
    else:
        await query.edit_message_text("✏️ *Apna caption bhejo:*", parse_mode="Markdown")
        return WAIT_CAPTION


async def receive_manual_caption(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return ConversationHandler.END
    caption = update.message.text.strip()
    await update.message.reply_text("⏳ Processing... thoda wait karo!")
    await handle_process(update.message, ctx, caption_override=caption)
    return ConversationHandler.END


async def handle_process(message, ctx, caption_override):
    url = ctx.user_data.get("url", "")

    for f in Path(TMP_DIR).glob("*"):
        try: f.unlink()
        except: pass

    try:
        await message.reply_text("📥 Video download ho rahi hai...")
        video_path = download_video(url, TMP_DIR)

        await message.reply_text("✍️ Caption ready ho raha hai...")
        if caption_override:
            final_caption = caption_override
        else:
            with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                info     = ydl.extract_info(url, download=False)
                original = info.get("description") or info.get("title") or "Amazing video!"
            final_caption = rewrite_caption(original)

        # 😭 emoji end mein add karo agar nahi hai
        if "😭" not in final_caption:
            final_caption = final_caption.rstrip() + " 😭"

        await message.reply_text("🎨 White background + caption add ho raha hai...")
        out_path = os.path.join(TMP_DIR, "output.mp4")
        process_video_file(video_path, final_caption, out_path)

        await message.reply_text("📤 Video bhej raha hoon...")
        with open(out_path, "rb") as vf:
            await message.reply_video(
                video=vf,
                caption=f"✅ *Ready!*\n\n📝 *Caption:*\n{final_caption}",
                parse_mode="Markdown",
                supports_streaming=True,
            )
        await message.reply_text("🎉 Done! Aur link bhejo 🔥")

    except Exception as e:
        await message.reply_text(f"❌ Error aaya:\n`{str(e)}`", parse_mode="Markdown")


async def unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text("📎 Bas video link bhejo!")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(caption_choice, pattern="^(auto|manual)$")],
        states={WAIT_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_caption)]},
        fallbacks=[],
        per_message=False,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"https?://"), handle_link))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))
    print("🤖 Bot chalu ho gaya!")
    app.run_polling()


if __name__ == "__main__":
    main()