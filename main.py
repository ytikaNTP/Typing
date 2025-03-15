from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from datetime import datetime
from PIL import Image
import os
import logging
import asyncio
import io
from hypercorn.config import Config
from hypercorn.asyncio import serve

app = Flask(__name__, static_folder='static')
CORS(app, resources={r"/*": {"origins": "*"}})

TELEGRAM_TOKEN = '7857812613:AAGXRbkr5TiJC5z7IxxoPCzw07ZvDNeHjVg'
ADMIN_CHAT_IDS = [6966335427, 7847234018]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

message_tags = {}
message_data = {}

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

def compress_image(file):
    try:
        img = Image.open(file.stream)
        if img.mode != 'RGB':
            img = img.convert("RGB")
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=90)
        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"Image error: {e}")
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

        message_text = f"""
        🚨 НОВЫЙ ЗАКАЗ ОТ {data['name']} 🚨

📅 Дата: {datetime.now().strftime("%d.%m.%Y %H:%M")}
👤 Имя: {data['name']}
📞 Телефон: {data['phone']}
📧 Контакт: {data['contact']}
🔗 Ссылка: {data['product_url']}
💬 Комментарий: 
{data.get('comment', '...')}
        """.strip()

        messages_ids = []
        for chat_id in ADMIN_CHAT_IDS:
            try:
                # Отправка текстового сообщения
                message = await bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode='HTML'
                )
                
                # Отправка изображений
                media_messages = []
                if compressed_images:
                    media = [InputMediaPhoto(img) for img in compressed_images]
                    media_messages = await bot.send_media_group(
                        chat_id=chat_id,
                        media=media
                    )

                # Сохранение информации о сообщении
                message_data[str(message.message_id)] = {
                    'media_ids': [m.message_id for m in media_messages],
                    'chat_id': chat_id
                }

                # Добавление кнопок
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    reply_markup=get_tags_keyboard(message.message_id)
                )

                messages_ids.append(message.message_id)
            except Exception as e:
                logger.error(f"Ошибка в чате {chat_id}: {str(e)}")

        return messages_ids
    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}")
        return None

def get_tags_keyboard(message_id):
    tags = ['🤡клоун', '💣спам', '❌отклонено', '✔️проверено', '❓под вопросом']
    buttons = [InlineKeyboardButton(tag, callback_data=f"tag_{message_id}_{tag}") for tag in tags]
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    keyboard.append([InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{message_id}")])
    return InlineKeyboardMarkup(keyboard)

async def handle_tag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        _, message_id, tag = query.data.split('_')
        current_tags = message_tags.get(message_id, [])
        
        if tag in current_tags:
            current_tags.remove(tag)
        else:
            current_tags.append(tag)
        
        message_tags[message_id] = current_tags
        tags_text = '\n'.join(current_tags) if current_tags else 'нет тегов'
        
        await query.edit_message_text(
            text=query.message.text.split('\n🏷')[0] + f"\n🏷 Тэги:\n{tags_text}",
            reply_markup=get_tags_keyboard(message_id),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка обработки тега: {str(e)}")

async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        message_id = query.data.split('_')[1]
        if message_id in message_data:
            async with Application.builder().token(TELEGRAM_TOKEN).build() as app:
                bot = app.bot
                data = message_data[message_id]
                
                # Удаление медиафайлов
                for media_id in data['media_ids']:
                    await bot.delete_message(
                        chat_id=data['chat_id'],
                        message_id=media_id
                    )
                
                # Удаление основного сообщения (исправленная секция)
                await bot.delete_message(
                    chat_id=data['chat_id'],
                    message_id=int(message_id)
                )

                del message_data[message_id]
                if message_id in message_tags:
                    del message_tags[message_id]
                    
    except Exception as e:
        logger.error(f"Ошибка удаления: {str(e)}")

@app.route('/save', methods=['POST', 'OPTIONS'])
async def save_handler():
    try:
        if request.method == 'OPTIONS':
            return jsonify({'status': 'ok'}), 200
            
        form_data = request.form
        files = request.files.getlist('images')
        
        # Валидация обязательных полей
        required_fields = ['name', 'phone', 'contact', 'product_url']
        if not all(form_data.get(field) for field in required_fields):
            return jsonify({
                'success': False,
                'error': 'Все обязательные поля должны быть заполнены'
            }), 400
        
        result = await async_send_to_telegram(form_data, files)
        
        return jsonify({
            'success': bool(result),
            'message': 'Сообщение успешно отправлено' if result else 'Ошибка отправки',
            'error': None if result else 'Ошибка при отправке в Telegram'
        }), 200 if result else 500
        
    except Exception as e:
        logger.error(f"Ошибка сервера: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Внутренняя ошибка сервера'
        }), 500

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
    
    logger.info("Telegram бот запущен")
    while True:
        await asyncio.sleep(3600)

async def run_web():
    config = Config()
    config.bind = ["0.0.0.0:3000"]
    await serve(app, config)
    logger.info("Веб-сервер запущен на порту 3000")

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
