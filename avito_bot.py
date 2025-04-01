import requests
import time
import telebot
import json
import sqlite3
from telebot import types
from flask import Flask, request
import threading

# Конфигурация Avito
AVITO_API_URL = "https://api.avito.ru"
TOKEN_URL = f"{AVITO_API_URL}/token"
CHATS_URL = lambda user_id: f"{AVITO_API_URL}/messenger/v2/accounts/{user_id}/chats"
MESSAGES_URL = lambda user_id, chat_id: f"{AVITO_API_URL}/messenger/v3/accounts/{user_id}/chats/{chat_id}/messages/?limit=100&offset=0"
SEND_MESSAGE_URL = lambda user_id, chat_id: f"{AVITO_API_URL}/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages"
WEBHOOK_URL = f"{AVITO_API_URL}/messenger/v3/webhook"

client_id = "TsJb-EKo-BTUGdWpoysj"
client_secret = "axT6WN4wj9lvZ2QqKp6KJrns0MwUKLC1f3DX9PEC"
user_id = "184453956"  # Ваш подтвержденный user_id

# Telegram
TELEGRAM_BOT_TOKEN = "8188439498:AAHD4rByGWVq9_ee0EH4YjrwgUiVM0MvbfA"
TELEGRAM_CHAT_ID = "1200223081"  # Ваш Telegram ID
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Нейросеть (proxyapi.ru)
NEURO_API_URL = "https://api.proxyapi.ru/openai/v1/chat/completions"
NEURO_API_KEY = "sk-36MaATLIXgF8HbIDfyoy6Or9x2eUPJl0"

# Flask приложение
app = Flask(__name__)

# База данных SQLite
DB_PATH = "/root/avito_bot/avito_bot.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (chat_id TEXT, message_id TEXT, user_id TEXT, content TEXT, timestamp INTEGER, response TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS prompts 
                 (ad_id TEXT PRIMARY KEY, title TEXT, description TEXT, prompt TEXT)''')
    conn.commit()
    conn.close()

# Состояния для диалога
REPLY_STATE = {}
filtered_item_ids = None

# Получение токена Avito
def get_avito_token():
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    response = requests.post(TOKEN_URL, data=data)
    if response.status_code != 200:
        print(f"Ошибка при получении токена Avito: {response.status_code}, {response.text}")
        return None
    return response.json()["access_token"]

# Получение списка всех чатов
def get_chats(token, user_id, item_ids=None):
    headers = {"Authorization": f"Bearer {token}"}
    params = {"unread_only": "true"}
    if item_ids:
        params["item_ids"] = ",".join(map(str, item_ids))
    response = requests.get(CHATS_URL(user_id), headers=headers, params=params)
    if response.status_code != 200:
        print(f"Ошибка при получении чатов: {response.status_code}, {response.text}")
        return []
    return response.json().get("chats", [])

# Получение сообщений для конкретного чата
def get_messages(token, user_id, chat_id):
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(MESSAGES_URL(user_id, chat_id), headers=headers)
    if response.status_code != 200:
        print(f"Ошибка при получении сообщений для чата {chat_id}: {response.status_code}, {response.text}")
        return []
    data = response.json()
    return data.get("messages", [])

# Отправка сообщения в Avito
def send_avito_message(token, user_id, chat_id, message):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"message": {"text": message}, "type": "text"}
    response = requests.post(SEND_MESSAGE_URL(user_id, chat_id), headers=headers, json=payload)
    if response.status_code != 200:
        print(f"Ошибка при отправке сообщения в чат {chat_id}: {response.status_code}, {response.text}")
    return response.status_code == 200

# Настройка вебхука
def set_webhook(token):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    webhook_url = "http://212.193.24.4:5000/webhook"  # Твой публичный IP
    payload = {"url": webhook_url}
    response = requests.post(WEBHOOK_URL, headers=headers, json=payload)
    if response.status_code in [200, 201]:
        print(f"Вебхук установлен: {webhook_url}")
    else:
        print(f"Ошибка установки вебхука: {response.status_code}, {response.text}")

# Сохранение сообщения в БД
def save_message(chat_id, message_id, user_id, content, timestamp, response=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO messages (chat_id, message_id, user_id, content, timestamp, response) VALUES (?, ?, ?, ?, ?, ?)",
              (chat_id, message_id, user_id, content, timestamp, response))
    conn.commit()
    conn.close()

# Получение истории чата из БД
def get_chat_history(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT content, response FROM messages WHERE chat_id = ? ORDER BY timestamp ASC", (chat_id,))
    history = c.fetchall()
    conn.close()
    return [{"role": "user", "content": row[0]} if row[1] is None else {"role": "assistant", "content": row[1]} for row in history]

# Получение промпта из БД по ad_id
def get_prompt(ad_id, ad_title, ad_description):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT prompt FROM prompts WHERE ad_id = ?", (ad_id,))
    result = c.fetchone()
    if result:
        conn.close()
        return result[0]
    
    default_prompt = (
        f"Ты лучший продавец цифровых товаров. Будь вежливым и профессиональным. "
        f"Отвечай кратко, если запрос не связан с товаром. "
        f"Если спрашивают о товаре '{ad_title}', опиши его на основе: '{ad_description}'. "
        f"Если просят разработку бота, парсинг или инвайт, скажи: 'Мы изучим этот вопрос и ответим в течение часа.'"
    )
    c.execute("INSERT INTO prompts (ad_id, title, description, prompt) VALUES (?, ?, ?, ?)",
              (ad_id, ad_title, ad_description, default_prompt))
    conn.commit()
    conn.close()
    return default_prompt

# Запрос к нейросети с учетом истории
def get_neuro_response(chat_id, user_message, ad_id, ad_title, ad_description):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {NEURO_API_KEY}"}
    history = get_chat_history(chat_id)
    prompt = get_prompt(ad_id, ad_title, ad_description)
    messages = [{"role": "system", "content": prompt}] + history + [{"role": "user", "content": user_message}]
    data = {"model": "gpt-4o", "messages": messages}
    response = requests.post(NEURO_API_URL, headers=headers, json=data)
    if response.status_code != 200:
        print(f"Ошибка нейросети: {response.status_code}, {response.text}")
        return "Извините, не могу ответить сейчас."
    return response.json()["choices"][0]["message"]["content"]

# Отправка уведомления в Telegram
def send_telegram_notification(chat_id_avito, ad_title, user_id_avito, user_message, neuro_response):
    try:
        notification = (
            f"Новое сообщение в Avito чате {chat_id_avito}!\n"
            f"Название объявления: {ad_title}\n"
            f"ID пользователя: {user_id_avito}\n"
            f"Сообщение пользователя: {user_message}\n"
            f"Ответ нейросети: {neuro_response}"
        )
        bot.send_message(TELEGRAM_CHAT_ID, text=notification)
    except Exception as e:
        print(f"Ошибка при отправке в Telegram: {e}")

# Отправка отчета в Telegram
def send_report():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE response IS NOT NULL")
    total_messages = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT chat_id) FROM messages")
    total_chats = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT user_id) FROM messages")
    total_users = c.fetchone()[0]
    conn.close()
    
    report = (
        f"Отчет по активности:\n"
        f"Обработано сообщений: {total_messages}\n"
        f"Активных чатов: {total_chats}\n"
        f"Уникальных пользователей: {total_users}"
    )
    bot.send_message(TELEGRAM_CHAT_ID, report)

# Вебхук для обработки сообщений
@app.route('/webhook', methods=['POST'])
def webhook():
    token = get_avito_token()
    if not token:
        return "Ошибка авторизации", 500
    
    data = request.json
    chat_id = data.get("chat_id")
    message = data.get("message", {})
    message_id = message.get("id")
    user_id_avito = message.get("author_id")
    msg_content = message.get("content", {}).get("text", "Нет текста")
    msg_type = message.get("type", "unknown")
    flow_id = message.get("flow_id")
    is_read = message.get("isRead", True)
    timestamp = message.get("created", int(time.time()))

    if not is_read and msg_type != "system" and flow_id is None:
        chats = get_chats(token, user_id, filtered_item_ids)
        ad_title = "Неизвестное объявление"
        ad_id = None
        ad_description = "Нет описания"
        for chat in chats:
            if chat["id"] == chat_id:
                context = chat.get("context", {}).get("value", {})
                ad_title = context.get("title", "Неизвестное объявление")
                ad_id = context.get("id")
                ad_description = context.get("description", "Нет описания")
                break
        
        neuro_response = get_neuro_response(chat_id, msg_content, ad_id, ad_title, ad_description)
        if send_avito_message(token, user_id, chat_id, neuro_response):
            print(f"Отправлен ответ в чат {chat_id}: {neuro_response}")
            save_message(chat_id, message_id, user_id_avito, msg_content, timestamp, neuro_response)
            send_telegram_notification(chat_id, ad_title, user_id_avito, msg_content, neuro_response)
    
    return "OK", 200

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    reply_button = types.InlineKeyboardButton("Ответить клиенту", callback_data="reply_client")
    report_button = types.InlineKeyboardButton("Получить отчет", callback_data="get_report")
    markup.add(reply_button, report_button)
    bot.send_message(message.chat.id, "Бот запущен! Используйте кнопки ниже.", reply_markup=markup)

# Обработчик команды /filter
@bot.message_handler(commands=['filter'])
def set_filter(message):
    try:
        item_ids = [int(x.strip()) for x in message.text.split()[1:]]
        global filtered_item_ids
        filtered_item_ids = item_ids
        bot.send_message(message.chat.id, f"Установлен фильтр по объявлениям: {item_ids}")
    except:
        bot.send_message(message.chat.id, "Укажите ID объявлений через пробел после /filter, например: /filter 123 456")

# Обработчик нажатия кнопок
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.data == "reply_client":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "Введите ID клиента Avito (например, 342812080):")
        REPLY_STATE[call.message.chat.id] = {"step": "awaiting_id"}
    elif call.data == "get_report":
        bot.answer_callback_query(call.id)
        send_report()

# Обработчик текстовых сообщений для диалога
@bot.message_handler(content_types=['text'])
def handle_text(message):
    chat_id = message.chat.id
    if chat_id in REPLY_STATE:
        state = REPLY_STATE[chat_id]
        if state["step"] == "awaiting_id":
            try:
                target_user_id = int(message.text.strip())
                state["target_user_id"] = target_user_id
                state["step"] = "awaiting_message"
                bot.send_message(chat_id, f"ID клиента: {target_user_id}. Введите сообщение для отправки:")
            except ValueError:
                bot.send_message(chat_id, "Пожалуйста, введите корректный числовой ID.")
        elif state["step"] == "awaiting_message":
            token = get_avito_token()
            if not token:
                bot.send_message(chat_id, "Ошибка авторизации в Avito.")
                return
            target_chat_id = find_chat_by_user_id(token, user_id, state["target_user_id"])
            if target_chat_id:
                if send_avito_message(token, user_id, target_chat_id, message.text):
                    bot.send_message(chat_id, f"Сообщение успешно отправлено клиенту с ID {state['target_user_id']} в чат {target_chat_id}!")
                else:
                    bot.send_message(chat_id, "Ошибка при отправке сообщения клиенту.")
            else:
                bot.send_message(chat_id, f"Чат с клиентом ID {state['target_user_id']} не найден.")
            del REPLY_STATE[chat_id]

# Поиск chat_id по user_id клиента
def find_chat_by_user_id(token, user_id_avito, target_user_id):
    chats = get_chats(token, user_id_avito)
    for chat in chats:
        chat_id = chat["id"]
        messages = get_messages(token, user_id_avito, chat_id)
        for message in messages:
            if str(message.get("author_id")) == str(target_user_id):
                return chat_id
    return None

# Инициализация
init_db()
filtered_item_ids = None

# Настройка вебхука при запуске
token = get_avito_token()
if token:
    set_webhook(token)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
