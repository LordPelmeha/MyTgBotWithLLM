import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import threading
from datetime import datetime

# Импорт токена из конфигурации
try:
    from config import TELEGRAM_BOT_TOKEN
except ImportError:
    print("=" * 50)
    print("ОШИБКА: Файл config.py не найден!")
    print("Создайте файл config.py на основе config.example.py")
    print("=" * 50)
    exit(1)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

LM_STUDIO_API_URL = "http://localhost:1234/v1/chat/completions"
MODEL_NAME = "qwen2.5-1.5b-instruct"

user_contexts = {}
context_lock = threading.Lock()


def get_context(user_id: int) -> str:
    """Получает или создаёт пустую строку контекста для пользователя"""
    with context_lock:
        if user_id not in user_contexts:
            user_contexts[user_id] = ""
        return user_contexts[user_id]


def add_to_context(user_id: int, role: str, content: str):
    """Добавляет сообщение в строку контекста"""
    with context_lock:
        if user_id not in user_contexts:
            user_contexts[user_id] = ""
        
        user_contexts[user_id] += f"role: {role}\n{content}\n\n"


def clear_user_context(user_id: int) -> bool:
    """Очищает контекст пользователя"""
    with context_lock:
        if user_id in user_contexts:
            user_contexts[user_id] = ""
            logger.info(f"Контекст очищен для пользователя {user_id}")
            return True
        return False


def parse_context_to_messages(context_string: str) -> list:
    """Преобразует строку контекста в формат messages для API"""
    messages = []
    
    if not context_string.strip():
        return messages
    
    blocks = context_string.strip().split("\n\n")
    
    for block in blocks:
        if not block.strip():
            continue
        
        lines = block.strip().split("\n", 1)
        if len(lines) >= 2:
            role_line = lines[0]
            content = lines[1] if len(lines) > 1 else ""
            
            if role_line.startswith("role: "):
                role = role_line.replace("role: ", "").strip()
                if role in ["user", "assistant"]:
                    messages.append({"role": role, "content": content})
    
    return messages


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    clear_user_context(user_id)
    
    welcome_text = (
        f"Привет, {user_name}!\n\n"
        "Я бот с поддержкой контекста диалога.\n"
        "Я помню нашу беседу и могу отвечать с учетом предыдущих сообщений.\n\n"
        "Доступные команды:\n"
        "/start - начать диалог заново\n"
        "/clear - очистить историю диалога\n\n"
        "Просто напишите мне сообщение!"
    )
    await update.message.reply_text(welcome_text)
    logger.info(f"Пользователь {user_id} ({user_name}) начал диалог")


async def clear_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /clear - очищает контекст пользователя"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    clear_user_context(user_id)
    await update.message.reply_text("История диалога очищена. Начинаем новый разговор!")
    logger.info(f"Пользователь {user_id} ({user_name}) очистил контекст")


def get_llm_response(context_string: str, user_id: int) -> str:
    """Получение ответа от LM Studio API"""
    try:
        system_message = {
            "role": "system",
            "content": (
                "Ты - ассистент с отличной памятью. "
                "ВАЖНО: Внимательно запоминай информацию, которую сообщает пользователь, "
                "и используй её в своих ответах. "
                "Отвечай на русском языке."
            )
        }
        
        messages = parse_context_to_messages(context_string)
        full_messages = [system_message] + messages
        
        payload = {
            "model": MODEL_NAME,
            "messages": full_messages,
            "temperature": 0.7,
            "max_tokens": -1,
            "stream": False
        }
        
        logger.info(f"Запрос для пользователя {user_id}, сообщений: {len(full_messages)}")
        
        response = requests.post(LM_STUDIO_API_URL, json=payload, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        return result['choices'][0]['message']['content']
        
    except requests.exceptions.ConnectionError:
        logger.error(f"Ошибка подключения к LM Studio для пользователя {user_id}")
        return "Не удалось подключиться к LM Studio. Убедитесь, что сервер запущен."
    except requests.exceptions.Timeout:
        logger.error(f"Таймаут для пользователя {user_id}")
        return "Превышено время ожидания ответа."
    except Exception as e:
        logger.error(f"Ошибка для пользователя {user_id}: {e}")
        return "Произошла ошибка при генерации ответа."


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    user_message = update.message.text
    
    logger.info(f"Сообщение от {user_id} ({user_name}): {user_message}")
    
    add_to_context(user_id, "user", user_message)

    context_string = get_context(user_id)
    
    await update.message.chat.send_action(action="typing")

    llm_response = get_llm_response(context_string, user_id)

    add_to_context(user_id, "assistant", llm_response)
    
    await update.message.reply_text(llm_response)
    logger.info(f"Ответ для {user_id}: {llm_response[:100]}...")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Произошла ошибка. Попробуйте ещё раз или используйте /clear."
        )


def main():
    """Основная функция запуска бота"""

    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("ОШИБКА: Укажите корректный токен бота в config.py!")
        return
    
    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("clear", clear_context_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_error_handler(error_handler)
        
        print("=" * 50)
        print("Telegram-бот с поддержкой контекста запущен!")
        print(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Модель: {MODEL_NAME}")
        print(f"API: {LM_STUDIO_API_URL}")
        print("=" * 50)
        
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        print(f"Ошибка запуска: {e}")


if __name__ == "__main__":
    main()