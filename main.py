import os
import sqlite3
import time
from datetime import datetime
import telebot
from telebot import types
from openai import OpenAI
from dotenv import load_dotenv

# Загружаем токены из файла .env, чтобы не спалить их на Гитхабе!
load_dotenv()

TG_TOKEN = os.getenv("TG_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Настройки контекста
# TODO: Позже вынести в админку или конфиг
MAX_CONTEXT_MESSAGES = 15 # Сколько последних сообщений помнит бот (чтобы не разориться на токенах API)
SYSTEM_PROMPT = "Ты ИИ-ассистент по имени Валли. Отвечай кратко, дружелюбно, опирайся на контекст беседы."

# Инициализация
bot = telebot.TeleBot(TG_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)


class DatabaseManager:
    """
    Класс для работы с SQLite. 
    Хранит всю историю переписок, чтобы бот не был "золотой рыбкой".
    """
    def __init__(self, db_name="bot_memory.db"):
        self.db_name = db_name
        self._create_tables()

    def _create_tables(self):
        # Подключаемся и создаем таблицу, если это первый запуск
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT,
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def add_message(self, user_id, role, content):
        """Сохраняет сообщение в базу"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content)
            )
            conn.commit()

    def get_context(self, user_id, limit=MAX_CONTEXT_MESSAGES):
        """
        Достает последние N сообщений юзера.
        Важно: достаем DESC (чтобы взять самые свежие), но потом переворачиваем ASC,
        чтобы нейронка читала диалог в правильном хронологическом порядке.
        """
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT role, content FROM messages 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (user_id, limit))
            
            rows = cursor.fetchall()
            
            # Переворачиваем историю из базы
            rows.reverse()
            
            # Формируем список словарей в формате OpenAI
            context = [{"role": "system", "content": SYSTEM_PROMPT}]
            for row in rows:
                context.append({"role": row[0], "content": row[1]})
                
            return context

    def clear_history(self, user_id):
        """Сброс памяти для конкретного юзера (полезно, если контекст забился)"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            conn.commit()


# Подрубаем базу
db = DatabaseManager()


@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    bot.reply_to(message, "Привет! Я Валли 🤖. Я запоминаю наш контекст. Напиши мне что-нибудь!")
    print(f"[{datetime.now()}] Новый юзер: {user_id}")


@bot.message_handler(commands=['clear'])
def clear_memory(message):
    user_id = message.from_user.id
    db.clear_history(user_id)
    bot.reply_to(message, "🧹 Память очищена! Начинаем с чистого листа.")
    print(f"[{datetime.now()}] Юзер {user_id} очистил историю.")


@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id
    user_text = message.text

    # 1. Сохраняем запрос юзера в базу (помечаем как 'user')
    db.add_message(user_id, "user", user_text)

    # Показываем статус "Печатает...", чтобы юзер понимал, что бот не завис
    bot.send_chat_action(message.chat.id, 'typing')

    try:
        # 2. Достаем историю переписки (сортированную!)
        messages_context = db.get_context(user_id)

        # 3. Стучимся в OpenAI
        response = client.chat.completions.create(
            model="gpt-3.5-turbo", # Можно поменять на 4o-mini
            messages=messages_context,
            temperature=0.7, # Чуть-чуть креативности
        )

        bot_reply = response.choices[0].message.content

        # 4. Сохраняем ответ бота в базу (помечаем как 'assistant')
        db.add_message(user_id, "assistant", bot_reply)

        # 5. Отправляем ответ в ТГ
        bot.reply_to(message, bot_reply)

    except Exception as e:
        # Если API упадет, не крашим скрипт, а честно говорим об ошибке
        error_msg = f"⚠️ Ой, что-то пошло не так на стороне нейросети. Ошибка: {e}"
        print(f"[{datetime.now()}] Ошибка у юзера {user_id}: {e}")
        bot.reply_to(message, "Сейчас проблемы с сетью, попробуй чуть позже.")


if __name__ == "__main__":
    print("🤖 Бот-ассистент с долгосрочной памятью запущен...")
    # Используем infinity_polling для защиты от падений по таймауту (как в mail_bot)
    bot.infinity_polling(timeout=60)
