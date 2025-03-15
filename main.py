from flask import Flask, request, jsonify, send_from_directory
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from datetime import datetime
from PIL import Image
import os
import logging
import asyncio
import threading
import io
import multiprocessing
from hypercorn.asyncio import serve
from hypercorn.config import Config

app = Flask(__name__, static_folder='static')

# Настройки
TELEGRAM_TOKEN = '7857812613:AAGXRbkr5TiJC5z7IxxoPCzw07ZvDNeHjVg'
ADMIN_CHAT_IDS = [6966335427, 7847234018]  # Убедитесь в правильности ID

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Хранилища данных
message_tags = {}
message_data = {}

# Маршруты Flask
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

def compress_image(file):
    try:
        img = Image.open(file.stream)
        img = img.convert("RGB")
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=90)
        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"Ошибка сжатия изображения: {e}")
        return None

async def async_send_to_telegram(data, files):
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        bot = application.bot
        
        compressed_images = []
        for file in files:
            if file and allowed_file(file.filename):
                compressed_img = compress_image(file)
                if compressed_img:
                    compressed_images.append(compressed_img)

        current_date = datetime.now().strftime("%d.%m.%Y %H:%M")
        message_text = f"""
        === НОВЫЙ ТИКЕТ ОТ {data['name']} ===

📅 Дата: {current_date}
👤 Имя: {data['name']}
📞 Телефон: {data['phone']}
📧 Контакт: {data['contact']}
🔗 Ссылка: {data['product_url']}
💬 Комментарий: 
{data.get('comment', '...')}
______________________________________________
ㅤ
        """.strip()

        messages_ids = []
        for chat_id in ADMIN_CHAT_IDS:
            try:
                message = await bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode='HTML'
                )
                
                media_messages = []
                if compressed_images:
                    media = [InputMediaPhoto(img) for img in compressed_images]
                    media_messages = await bot.send_media_group(
                        chat_id=chat_id, 
                        media=media
                    )

                message_data[str(message.message_id)] = {
                    'media_ids': [m.message_id for m in media_messages],
                    'chat_id': chat_id
                }

                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    reply_markup=get_tags_keyboard(message.message_id)
                )
                
                messages_ids.append(message.message_id)
            except Exception as e:
                logger.error(f"Ошибка отправки в чат {chat_id}: {e}")

        return messages_ids
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        return None

def get_tags_keyboard(message_id):
    tags = ['🤡клоун', '💣спам', '❌отклонено', '✔️проверено', '❓под вопросом']
    keyboard = [[InlineKeyboardButton(tag, callback_data=f"tag_{message_id}_{tag}")] for tag in tags]
    keyboard.append([InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{message_id}")])
    return InlineKeyboardMarkup(keyboard)

async def handle_tag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        data = query.data.split('_')
        message_id = data[1]
        tag = data[2]

        current_tags = message_tags.get(message_id, [])
        
        if tag in current_tags:
            current_tags.remove(tag)
        else:
            current_tags.append(tag)
        
        message_tags[message_id] = current_tags
        
        tags_text = '\n'.join(current_tags) if current_tags else 'нет тегов'
        new_text = query.message.text.split('\n🏷')[0] + f"\n🏷 Тэги:\n{tags_text}"
        
        await query.edit_message_text(
            text=new_text,
            reply_markup=get_tags_keyboard(message_id),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка обработки тега: {e}")

async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        message_id = query.data.split('_')[1]
        
        if message_id in message_data:
            application = Application.builder().token(TELEGRAM_TOKEN).build()
            async with application:
                bot = application.bot
                chat_id = message_data[message_id]['chat_id']
                
                for media_id in message_data[message_id]['media_ids']:
                    await bot.delete_message(chat_id=chat_id, message_id=media_id)
                
                await bot.delete_message(chat_id=chat_id, message_id=int(message_id))
                
                del message_data[message_id]
                if message_id in message_tags:
                    del message_tags[message_id]
                    
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")

@app.route('/save', methods=['POST'])
async def save_handler():
    try:
        data = request.form
        files = request.files.getlist('images')
        
        result = await async_send_to_telegram(data, files)
        
        if result:
            return jsonify({'success': True, 'message_ids': result})
        else:
            return jsonify({'success': False, 'error': 'Ошибка отправки'}), 500
            
    except Exception as e:
        logger.error(f"Ошибка обработки запроса: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg'}

async def run_bot():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CallbackQueryHandler(handle_tag_callback, pattern="^tag_"))
    application.add_handler(CallbackQueryHandler(handle_delete_callback, pattern="^delete_"))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    while True:
        await asyncio.sleep(3600)

async def run_web():
    config = Config()
    config.bind = ["0.0.0.0:3000"]
    await serve(app, config)

async def main():
    await asyncio.gather(
        run_web(),
        run_bot()
    )

if __name__ == '__main__':
    if not os.path.exists('static'):
        os.makedirs('static')

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Сервер остановлен")
