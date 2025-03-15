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
import nest_asyncio
from hypercorn.asyncio import serve
from hypercorn.config import Config

# Установите зависимости:
# pip install "flask[async]" python-telegram-bot==20.3 pillow hypercorn nest_asyncio

app = Flask(__name__, static_folder='static')

# Настройки
TELEGRAM_TOKEN = '7857812613:AAGXRbkr5TiJC5z7IxxoPCzw07ZvDNeHjVg'
ADMIN_CHAT_IDS = [6966335427, 7847234018]  # Указанные Chat ID

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

# Функция для сжатия изображений
def compress_image(file):
    try:
        img = Image.open(file.stream)
        img = img.convert("RGB")
        
        # Сжатие до 90% качества
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=90)
        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"Ошибка сжатия изображения: {e}")
        return None

# Отправка данных в Telegram
async def async_send_to_telegram(data, files):
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        async with application:
            bot = application.bot
            logger.info("Бот успешно инициализирован.")

            # Сжимаем и конвертируем изображения
            compressed_images = []
            for file in files:
                if file and allowed_file(file.filename):
                    compressed_img = compress_image(file)
                    if compressed_img:
                        compressed_images.append(compressed_img)
            logger.info(f"Сжато {len(compressed_images)} изображений.")

            # Форматирование сообщения
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
            """

            messages_ids = []
            for chat_id in ADMIN_CHAT_IDS:
                logger.info(f"Попытка отправить сообщение в чат {chat_id}.")
                # Отправка текстового сообщения
                message = await bot.send_message(
                    chat_id=chat_id,
                    text=message_text.strip(),
                    parse_mode='HTML'
                )
                logger.info(f"Сообщение отправлено в чат {chat_id}.")

                # Сохранение ID сообщения
                message_id = str(message.message_id)

                # Отправка медиа (если есть изображения)
                media_messages = []
                if compressed_images:
                    media = [InputMediaPhoto(img.getvalue()) for img in compressed_images]
                    media_messages = await bot.send_media_group(
                        chat_id=chat_id, 
                        media=media
                    )
                    logger.info(f"Медиа отправлено в чат {chat_id}.")

                # Сохранение данных
                message_data[message_id] = {
                    'media_ids': [m.message_id for m in media_messages],
                    'file_ids': [img.getvalue() for img in compressed_images],
                    'chat_id': chat_id
                }
                messages_ids.append(message_id)

                # Добавляем клавиатуру с тегами
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    reply_markup=get_tags_keyboard(message_id)
                )
                logger.info(f"Клавиатура добавлена в сообщение {message_id}.")

            return messages_ids
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")
        return None

# Создание клавиатуры с тегами
def get_tags_keyboard(message_id):
    tags = ['🤡клоун', '💣спам', '❌отклонено', '✔️проверено', '❓под вопросом']
    keyboard = []
    for tag in tags:
        callback_data = f"tag_{message_id}_{tag}"
        keyboard.append([InlineKeyboardButton(tag, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{message_id}")])
    return InlineKeyboardMarkup(keyboard)

# Обработчик тегов
async def handle_tag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        data = query.data.split('_')
        if len(data) != 3:
            raise ValueError("Некорректный формат callback данных")
            
        message_id = data[1]
        tag = data[2]

        current_tags = message_tags.get(message_id, [])
        
        if tag in current_tags:
            current_tags.remove(tag)
        else:
            current_tags.append(tag)
        
        message_tags[message_id] = current_tags
        
        # Обновление сообщения
        tags_text = '\n'.join(current_tags) if current_tags else 'нет тегов'
        message_text = query.message.text.split('\n🏷')[0] + f"\n🏷 Тэги:\n{tags_text}"
        
        await query.edit_message_text(
            text=message_text,
            reply_markup=get_tags_keyboard(message_id),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка обработки тега: {e}")
        await query.answer("Произошла ошибка, попробуйте позже")

# Обработчик удаления
async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        message_id = query.data.split('_')[1]
        
        if message_id in message_data:
            application = Application.builder().token(TELEGRAM_TOKEN).build()
            async with application:
                bot = application.bot
                
                # Удаление медиа
                for media_id in message_data[message_id]['media_ids']:
                    await bot.delete_message(
                        chat_id=message_data[message_id]['chat_id'],
                        message_id=media_id
                    )
                
                # Удаление основного сообщения
                await bot.delete_message(
                    chat_id=message_data[message_id]['chat_id'],
                    message_id=int(message_id)  # Преобразуем message_id в int
                )
                
                # Очистка данных
                del message_data[message_id]
                if message_id in message_tags:
                    del message_tags[message_id]
                    
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        await query.answer("Произошла ошибка при удалении сообщения")

# Обработчик формы
@app.route('/save', methods=['POST'])
def save_handler():
    try:
        data = request.form
        files = request.files.getlist('images')
        
        # Запускаем асинхронную задачу
        result = asyncio.run(async_send_to_telegram(data, files))
        
        if result:
            return jsonify({'success': True, 'message_ids': result})
        else:
            return jsonify({'success': False, 'error': 'Ошибка отправки'}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Проверка допустимых форматов файлов
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg'}

# Обработчик ошибок
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")

# Запуск бота
async def run_bot():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_error_handler(error_handler)
    application.add_handler(CallbackQueryHandler(handle_tag_callback, pattern="^tag_"))
    application.add_handler(CallbackQueryHandler(handle_delete_callback, pattern="^delete_"))
    
    await application.run_polling()

# Основной запуск
if __name__ == '__main__':
    # Создаем структуру папок
    if not os.path.exists('static'):
        os.makedirs('static')

    # Применяем nest_asyncio для вложенных асинхронных циклов
    nest_asyncio.apply()

    # Создаем асинхронный цикл
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Запуск бота
    bot_task = loop.create_task(run_bot())

    # Настройка Hypercorn для Flask
    config = Config()
    config.bind = ["0.0.0.0:3000"]

    try:
        # Запуск Flask через Hypercorn
        loop.run_until_complete(serve(app, config))
    except KeyboardInterrupt:
        # Остановка бота при завершении
        bot_task.cancel()
        loop.close()
