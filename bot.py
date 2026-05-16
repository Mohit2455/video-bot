import os
import re
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
BOT_TOKEN  = os.environ["BOT_TOKEN"]
MY_ID      = int(os.environ["MY_ID"])
CLIENT_ID  = int(os.environ["CLIENT_ID"])
CLAUDE_KEY = os.environ["CLAUDE_KEY"]

ALLOWED      = {MY_ID, CLIENT_ID}
TMP_DIR      = "/tmp/videobot"
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
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
        "quiet": False,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
        },
    }
    # Instagram, TikTok aur YouTube ke liye cookies use karo
    if os.path.exists(COOKIES_FILE):
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


def crop_black_bars(video_path, out_path):
    # cropdetect se black bars detect karo
    result = subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vf", "cropdetect=24:16:0",
        "-frames:v", "60",
        "-f", "null", "-"
    ], capture_output=True, text=True)

    crops = re.findall(r"crop=(\d+:\d+:\d+:\d+)", result.stderr)

    if not crops:
        import shutil
        shutil.copy(video_path, out_path)
        return

    best_crop = max(set(crops), key=crops.count)
    parts = best_crop.split(":")
    cw = int(parts[0])
    ch = int(parts[1])
    cx = parts[2]
    cy = parts[3]

    # Even numbers ensure karo
    cw = cw if cw % 2 == 0 else cw - 1
    ch = ch if ch % 2 == 0 else ch - 1

    result2 = subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"crop={cw}:{ch}:{cx}:{cy}",
        "-c:a", "copy",
        out_path
    ], capture_output=True)

    if result2.returncode != 0:
        import shutil
        shutil.copy(video_path, out_path)


def make_caption_banner(path, w, h, text_line):
    img  = Image.new("RGB", (w, h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    font_size = max(38, w // 18)
    font = None
    for fp in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]:
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except:
            continue
    if font is None:
        font = ImageFont.load_default()

    wrapped = textwrap.fill(text_line, width=24)
    bbox    = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (w - tw) // 2
    ty = (h - th) // 2

    draw.multiline_text((tx, ty), wrapped, fill=(0, 0, 0),
                        font=font, align="center", spacing=10)
    img.save(path)


def make_empty_banner(path, w, h):
    img = Image.new("RGB", (w, h), color=(255, 255, 255))
    img.save(path)


def process_video_file(video_path, caption, out_path):
    # Step 1: Black bars crop
    cropped_path = os.path.join(TMP_DIR, "cropped.mp4")
    crop_black_bars(video_path, cropped_path)

    # Step 2: Dimensions
    w, h = get_video_info(cropped_path)
    w = w if w % 2 == 0 else w + 1
    h = h if h % 2 == 0 else h + 1

    # Step 3: Banner heights
    top_h = int(h * 0.25)
    bot_h = int(h * 0.08)
    top_h = top_h if top_h % 2 == 0 else top_h + 1
    bot_h = bot_h if bot_h % 2 == 0 else bot_h + 1

    top_path = os.path.join(TMP_DIR, "top.png")
    bot_path = os.path.join(TMP_DIR, "bot.png")

    make_caption_banner(top_path, w, top_h, caption)
    make_empty_banner(bot_path, w, bot_h)

    filter_complex = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=disable,setsar=1[vid];"
        f"[1:v]scale={w}:{top_h}:force_original_aspect_ratio=disable[top];"
        f"[2:v]scale={w}:{bot_h}:force_original_aspect_ratio=disable[bot];"
        f"[top][vid][bot]vstack=inputs=3[out]"
    )

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", cropped_path,
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
    ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    if result.returncode != 0:
        err = result.stderr.decode()[-600:]
        raise Exception(f"FFmpeg error:\n{err}")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "👋 *Video Repurpose Bot*\n\n"
        "📎 YouTube / Instagram / TikTok link bhejo\n\n"
        "✅ AI Caption Auto Rewrite\n"
        "✅ White Background Upar + Neeche\n"
        "✅ Black Bars Auto Remove\n"
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

        if "😭" not in final_caption:
            final_caption = final_caption.rstrip() + " 😭"

        await message.reply_text("🎨 Black bars remove + white background + caption add ho raha hai...")
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
        err_msg = str(e)[:800]
        await message.reply_text(f"❌ Error aaya:\n`{err_msg}`", parse_mode="Markdown")


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