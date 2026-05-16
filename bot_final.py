import os
import re
from pathlib import Path
from dotenv import load_dotenv
import yt_dlp
import anthropic
import subprocess
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
            "Keep same meaning, topic, language. "
            "Remove all emojis from output. "
            "Return ONLY the new caption, nothing else.\n\n"
            f"Original: {original}"
        )}]
    )
    return msg.content[0].text.strip()


def download_video(url, out_dir):
    opts = {
        "format": "best",
        "outtmpl": os.path.join(out_dir, "input.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": False,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
        },
    }
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


def get_video_size(video_path):
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


def clean_caption(text):
    # Emoji hata do
    text = re.sub(
        r"[\U00010000-\U0010ffff\U00002702-\U000027B0"
        r"\U0001f600-\U0001f64f\U0001f300-\U0001f5ff"
        r"\U0001f680-\U0001f6ff\U0001f1e0-\U0001f1ff"
        r"\u2640-\u2642\u2600-\u2B55\u200d\u23cf"
        r"\u23e9\u231a\ufe0f\u3030]+",
        "", text, flags=re.UNICODE
    ).strip()
    # Hashtags hata do
    text = re.sub(r"#\w+", "", text).strip()
    # Extra spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text or "Amazing Video"


def process_video(video_path, caption, out_path):
    W, H = get_video_size(video_path)
    W = W if W % 2 == 0 else W - 1
    H = H if H % 2 == 0 else H - 1

    plain = clean_caption(caption)

    font_size = max(36, W // 18)
    top_h = int(H * 0.22)
    top_h = top_h if top_h % 2 == 0 else top_h + 1
    bot_h = int(H * 0.08)
    bot_h = bot_h if bot_h % 2 == 0 else bot_h + 1
    total_h = top_h + H + bot_h
    total_h = total_h if total_h % 2 == 0 else total_h + 1

    # Text wrap for ffmpeg
    words = plain.split()
    lines_list, current = [], ""
    for word in words:
        if len(current) + len(word) + 1 <= 22:
            current = (current + " " + word).strip()
        else:
            if current:
                lines_list.append(current)
            current = word
    if current:
        lines_list.append(current)
    # ffmpeg drawtext mein \n se newline
    wrapped = r"\n".join(lines_list[:3])
    # Single quotes escape karo
    wrapped = wrapped.replace("'", "")

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if not os.path.exists(font_path):
        font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

    # pad filter: video ke upar top_h white + neeche bot_h white add karo
    # drawtext: upar white area mein center mein text
    text_y = max(10, (top_h // 2) - (font_size))

    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=disable,setsar=1,"
        f"pad={W}:{total_h}:0:{top_h}:color=white,"
        f"drawtext=fontfile='{font_path}':"
        f"text='{wrapped}':"
        f"fontcolor=black:fontsize={font_size}:"
        f"x=(w-text_w)/2:y={text_y}:line_spacing=10"
    )

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-map", "0:a?",
        "-c:a", "copy",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        out_path
    ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    if result.returncode != 0:
        err = result.stderr.decode()[-800:]
        raise Exception(f"FFmpeg error:\n{err}")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "👋 *Video Repurpose Bot*\n\n"
        "📎 YouTube / Instagram / TikTok link bhejo\n\n"
        "✅ AI Caption Auto Rewrite\n"
        "✅ White Background Upar + Neeche\n"
        "✅ Black Bars White Ho Jayenge\n\n"
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
            final_caption = clean_caption(caption_override)
        else:
            with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                info     = ydl.extract_info(url, download=False)
                original = info.get("description") or info.get("title") or "Amazing video"
            final_caption = rewrite_caption(original)
            final_caption = clean_caption(final_caption)

        await message.reply_text("🎨 White background + caption add ho raha hai...")
        out_path = os.path.join(TMP_DIR, "output.mp4")
        process_video(video_path, final_caption, out_path)

        await message.reply_text("📤 Video bhej raha hoon...")
        with open(out_path, "rb") as vf:
            await message.reply_video(
                video=vf,
                caption=f"📝 {final_caption}",
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
