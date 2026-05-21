import asyncio
import logging
import uuid
import traceback
import io
import os
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, LabeledPrice, PreCheckoutQuery
import database as db
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# ===========================================
# НАСТРОЙКИ (измени под себя!)
# ===========================================
BOT_TOKEN = "8918794962:AAGMCCr86CkgL6ASFmFoJnqNgc-Kp6Vsvtw"
ADMIN_IDS = [1781331191]  # ваш ID
SPREADSHEET_ID = "1Z70dNBhBC6Qb84Tiig8PJWaTpU3YoN_QC-zdEb4hzfM"
CREDENTIALS_FILE = "credentials.json"
# ===========================================

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    gclient = gspread.authorize(creds)
    sheet = gclient.open_by_key(SPREADSHEET_ID).sheet1
    print("✅ Google Sheets подключена")
except Exception as e:
    print(f"⚠️ Ошибка Google Sheets: {e}")
    sheet = None

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ========== АНАЛИЗ ТОНАЛЬНОСТИ ==========
NEGATIVE_WORDS = [
    'уёбище', 'уебище', 'ублюдок', 'урод', 'мудак', 'мудила', 'пиздюк', 'гондон',
    'пидор', 'педик', 'пидорас', 'петух', 'козёл', 'скотина', 'быдло', 'чмо',
    'мразь', 'сволочь', 'падла', 'гнида', 'тварь', 'выродок', 'шлюха', 'сучара',
    'ебанат', 'еблан', 'хуесос', 'долбоёб', 'конченый', 'отбитый', 'чмошник',
    'шестёрка', 'шавка', 'паршивец', 'негодяй', 'подлец', 'гад', 'гадина',
    'шваль', 'курва', 'проститутка', 'ебучий', 'пиздобол', 'говнюк', 'гнилой',
    'падаль', 'стерва', 'разъёба', 'недоносок', 'недоебан', 'недоделок'
]
POSITIVE_WORDS = [
    'спасибо', 'отлично', 'хорошо', 'супер', 'классно', 'прекрасно', 'работает',
    'помогло', 'нравится', 'благодарю', 'молодцы', 'круто', 'замечательно',
    'приятно', 'доволен', 'восторг'
]

def analyze_sentiment(text: str) -> tuple:
    text_lower = text.lower()
    neg = sum(1 for w in NEGATIVE_WORDS if w in text_lower)
    pos = sum(1 for w in POSITIVE_WORDS if w in text_lower)
    if neg > pos:
        return "🔴", "негатив"
    elif pos > neg:
        return "🟢", "позитив"
    else:
        return "🟡", "нейтрально"

# ========== УДАЛЕНИЕ СТАРЫХ СООБЩЕНИЙ БОТА ==========
last_bot_messages = {}

async def delete_previous_bot_message(chat_id: int):
    if chat_id in last_bot_messages:
        try:
            await bot.delete_message(chat_id, last_bot_messages[chat_id])
        except:
            pass
        del last_bot_messages[chat_id]

async def send_new_message(chat_id: int, text: str, keep=False, parse_mode=None, reply_markup=None):
    if not keep:
        await delete_previous_bot_message(chat_id)
    msg = await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    if not keep:
        last_bot_messages[chat_id] = msg.message_id
    return msg

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def get_user_display_name(user_id: int) -> str:
    try:
        user = await bot.get_chat(user_id)
        if user.username:
            return user.username
    except:
        pass
    return str(user_id)

def is_admin(user_id: int) -> bool:
    return db.get_role(user_id) == "admin"

def is_moderator(user_id: int) -> bool:
    role = db.get_role(user_id)
    return role in ["admin", "moderator"]

# ========== РЕЙТИНГИ (ratings.txt) ==========
RATINGS_FILE = "ratings.txt"

def load_ratings():
    ratings = {}
    try:
        with open(RATINGS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        user_id = int(parts[0])
                        username = parts[1]
                        role = parts[2]
                        scores = [int(x) for x in parts[3].split(",") if x.strip().isdigit()] if len(parts) > 3 else []
                        ratings[user_id] = {"username": username, "role": role, "scores": scores}
    except FileNotFoundError:
        with open(RATINGS_FILE, "w", encoding="utf-8") as f:
            f.write("# Формат: Telegram ID|username|роль|оценки через запятую\n")
    return ratings

def save_ratings(ratings):
    with open(RATINGS_FILE, "w", encoding="utf-8") as f:
        f.write("# Формат: Telegram ID|username|роль|оценки через запятую\n")
        for user_id, data in ratings.items():
            scores_str = ",".join(str(s) for s in data["scores"])
            f.write(f"{user_id}|{data['username']}|{data['role']}|{scores_str}\n")

async def ensure_user_in_ratings(user_id: int):
    ratings = load_ratings()
    role = db.get_role(user_id)
    username = await get_user_display_name(user_id)
    if user_id not in ratings:
        ratings[user_id] = {"username": username, "role": role, "scores": []}
        save_ratings(ratings)
    else:
        if ratings[user_id]["username"] != username:
            ratings[user_id]["username"] = username
            save_ratings(ratings)
        if ratings[user_id]["role"] != role:
            ratings[user_id]["role"] = role
            save_ratings(ratings)
    return ratings

async def get_user_rating(user_id: int) -> tuple:
    await ensure_user_in_ratings(user_id)
    ratings = load_ratings()
    if user_id not in ratings:
        return 0, 0
    scores = ratings[user_id]["scores"]
    if not scores:
        return 0, 0
    avg = sum(scores) / len(scores)
    return round(avg, 1), len(scores)

async def add_rating(user_id: int, rating: int, ticket_id: str, from_user_id: int):
    if from_user_id == user_id:
        return False
    await ensure_user_in_ratings(user_id)
    ratings = load_ratings()
    ratings[user_id]["scores"].append(rating)
    save_ratings(ratings)
    return True

# ========== НУМЕРАЦИЯ ТИКЕТОВ ==========
ticket_counter_file = "ticket_counter.txt"

def get_next_ticket_number() -> int:
    try:
        with open(ticket_counter_file, "r") as f:
            return int(f.read().strip()) + 1
    except:
        return 1

def save_ticket_number(number: int):
    with open(ticket_counter_file, "w") as f:
        f.write(str(number))

def generate_ticket_id() -> str:
    number = get_next_ticket_number()
    save_ticket_number(number)
    suffix = str(uuid.uuid4())[:5]
    return f"{number:03d}-{suffix}"

# ========== ШАБЛОНЫ ОТВЕТОВ (templates.txt) ==========
TEMPLATES_FILE = "templates.txt"

def load_templates():
    templates = {}
    try:
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    key, value = line.split("|", 1)
                    templates[key.strip()] = value.strip()
    except FileNotFoundError:
        with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
            f.write("# Формат: ключ|текст ответа\n")
            f.write("принято|✅ Ваше обращение принято в работу.\n")
            f.write("отклонено|❌ Ваше обращение отклонено.\n")
            f.write("бан|🚫 Вы заблокированы.\n")
            f.write("решение|💡 Ваша проблема решена.\n")
            f.write("закрыт|🔒 Тикет закрыт.\n")
        templates = {
            "принято": "✅ Ваше обращение принято в работу.",
            "отклонено": "❌ Ваше обращение отклонено.",
            "бан": "🚫 Вы заблокированы.",
            "решение": "💡 Ваша проблема решена.",
            "закрыт": "🔒 Тикет закрыт."
        }
    return templates

def save_templates(templates):
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        for key, value in templates.items():
            f.write(f"{key}|{value}\n")

# ========== ЛОГИРОВАНИЕ ==========
LOG_FILE = "admin_logs.txt"

def log_action(admin_id, admin_name, action, ticket_id=None, details=""):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] Админ {admin_id} (@{admin_name}) - {action}"
        if ticket_id:
            log_line += f" | Тикет: {ticket_id}"
        if details:
            log_line += f" | {details}"
        f.write(log_line + "\n")

# ========== ПАГИНАЦИЯ (общая функция) ==========
pagination_data = {}

async def show_page(chat_id: int, page: int, items: list, per_page: int, text_func, keyboard_func=None):
    data = pagination_data.get(chat_id)
    if not data or data.get("items") != items:
        pagination_data[chat_id] = {"items": items, "page": page, "message_id": None}
        data = pagination_data[chat_id]
    total_pages = (len(items) + per_page - 1) // per_page
    if page < 0 or page >= total_pages:
        return
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]
    text = text_func(page_items, page+1, total_pages)
    keyboard = []
    if page > 0:
        keyboard.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"page_{chat_id}_{page-1}"))
    if page < total_pages - 1:
        if page > 0:
            keyboard.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"page_{chat_id}_{page+1}"))
        else:
            keyboard = [InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"page_{chat_id}_{page+1}")]
    if keyboard_func:
        extra = keyboard_func()
        if extra:
            if isinstance(extra, list):
                keyboard.extend(extra)
            else:
                keyboard.append(extra)
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[keyboard]) if keyboard else None
    if data["message_id"] is None:
        msg = await send_new_message(chat_id, text, parse_mode="Markdown", reply_markup=reply_markup)
        pagination_data[chat_id]["message_id"] = msg.message_id
    else:
        try:
            await bot.edit_message_text(text, chat_id, data["message_id"], parse_mode="Markdown", reply_markup=reply_markup)
        except:
            msg = await send_new_message(chat_id, text, parse_mode="Markdown", reply_markup=reply_markup)
            pagination_data[chat_id]["message_id"] = msg.message_id

# ========== ВЕБ-СЕРВЕР ДЛЯ WEBHOOK ==========
async def health_check(request):
    return web.Response(text="OK")

async def on_startup(base_url: str):
    await bot.set_webhook(f"{base_url}/webhook")
    print(f"✅ Webhook установлен: {base_url}/webhook")

async def on_shutdown():
    await bot.delete_webhook()
    print("✅ Webhook удалён")

async def start_webhook_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path='/webhook')
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://your-bot-url.onrender.com")
    app.on_startup.append(lambda _: on_startup(base_url))
    app.on_shutdown.append(lambda _: on_shutdown())
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"✅ Веб-сервер (webhook) запущен на порту {port}")

async def main():
    await bot.delete_webhook()
    await start_webhook_server()
    await asyncio.Event().wait()

# ========== ОСНОВНЫЕ ХЕНДЛЕРЫ (ПРОДОЛЖЕНИЕ В ЧАСТИ 2) ==========
# Часть 2 будет содержать все команды /start, /create_ticket и т.д.
# ========== КОМАНДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if db.is_blacklisted(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ ДОСТУП ЗАБЛОКИРОВАН\n\nВы в чёрном списке.")
        return
    try:
        await message.delete()
    except:
        pass
    await send_new_message(
        message.chat.id,
        "🌿 Добро пожаловать в ARVION Support!\n\n"
        "📌 Основные команды:\n"
        "/create_ticket — новое обращение\n"
        "/my_tickets — мои обращения\n"
        "/get_user — мой ID\n"
        "/top_staff — топ персонала\n"
        "/donate — поддержать проект\n"
        "/help — полная инструкция\n\n"
        "👉 /create_ticket"
    )

@dp.message(Command("create_ticket"))
async def cmd_create_ticket(message: types.Message, state: FSMContext):
    if db.is_blacklisted(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Вы заблокированы!")
        return
    try:
        await message.delete()
    except:
        pass
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Жалоба", callback_data="type_complaint")],
        [InlineKeyboardButton(text="❓ Вопрос", callback_data="type_question")],
        [InlineKeyboardButton(text="⚖️ Апелляция", callback_data="type_appeal")],
        [InlineKeyboardButton(text="💡 Предложение", callback_data="type_suggestion")],
        [InlineKeyboardButton(text="📌 Другое", callback_data="type_other")]
    ])
    await send_new_message(message.chat.id, "📌 Выберите тип обращения:", reply_markup=kb)

@dp.message(Command("my_tickets"))
async def cmd_my_tickets(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    user_id = message.from_user.id
    tickets = db.get_user_tickets(user_id)
    if not tickets:
        await send_new_message(message.chat.id, "📭 У вас нет обращений.")
        return
    status_emoji = {"open": "🟡", "closed": "🔴"}
    text = "📋 Ваши обращения:\n\n"
    for t in tickets:
        ticket_id, ticket_text, status, created_at = t
        try:
            date = datetime.fromisoformat(created_at).strftime("%d.%m %H:%M")
        except:
            date = "дата неизвестна"
        emoji = status_emoji.get(status, "⚪")
        short_text = ticket_text[:80]
        text += f"{emoji} {ticket_id} | {status} | {date}\n   {short_text}...\n\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("get_user"))
async def cmd_get_user(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    user = message.from_user
    username = user.username if user.username else "нет username"
    await send_new_message(message.chat.id, f"👤 ВАШ АККАУНТ\n\nID: {user.id}\nUsername: @{username}\nИмя: {user.first_name}")

@dp.message(Command("top_staff"))
async def cmd_top_staff(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    ratings = load_ratings()
    if not ratings:
        await send_new_message(message.chat.id, "🏆 Нет данных для рейтинга.")
        return
    staff_list = []
    for user_id, data in ratings.items():
        role = data.get("role", db.get_role(user_id))
        if role in ["admin", "moderator"]:
            avg, count = await get_user_rating(user_id)
            display_name = data["username"] if data["username"] != str(user_id) else await get_user_display_name(user_id)
            staff_list.append((user_id, display_name, avg, count, role))
    staff_list.sort(key=lambda x: x[2], reverse=True)
    text = "🏆 ТОП ПЕРСОНАЛА ARVION\n\n"
    for i, (user_id, display_name, avg, count, role) in enumerate(staff_list[:10], 1):
        role_icon = "👑" if role == "admin" else "🛡️"
        role_name = "Админ" if role == "admin" else "Модератор"
        text += f"{i}. {role_icon} @{display_name} ({role_name}) — {avg} ⭐ ({count} оценок)\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("donate"))
async def cmd_donate(message: types.Message):
    if db.is_blacklisted(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Вы заблокированы!")
        return
    try:
        await message.delete()
    except:
        pass
    text = "Поддержать проект ARVION Support\n\nВы можете отправить донат через Telegram Stars.\nВсе средства пойдут на развитие бота.\n\nСпасибо за поддержку!"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 5 Stars", callback_data="donate_5")],
        [InlineKeyboardButton(text="⭐ 10 Stars", callback_data="donate_10")],
        [InlineKeyboardButton(text="⭐ 25 Stars", callback_data="donate_25")],
        [InlineKeyboardButton(text="⭐ 50 Stars", callback_data="donate_50")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ])
    await send_new_message(message.chat.id, text, reply_markup=kb)

@dp.callback_query(F.data.startswith("donate_"))
async def process_donate(callback: types.CallbackQuery):
    amount = int(callback.data.split("_")[1])
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Поддержка ARVION Support",
        description=f"Донат в размере {amount} Stars",
        payload="donation",
        currency="XTR",
        prices=[LabeledPrice(label="Донат", amount=amount)],
        need_name=False, need_phone_number=False, need_email=False,
        start_parameter="donation"
    )
    await callback.answer()
    await delete_previous_bot_message(callback.message.chat.id)

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.answer()
    await delete_previous_bot_message(callback.message.chat.id)
    await cmd_start(callback.message)

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    await message.answer("❤️ Огромное спасибо за поддержку! Ваши средства пойдут на развитие бота ARVION Support.")
    log_action(message.from_user.id, message.from_user.username or "user", "ДОНАТ", details=f"Сумма: {message.successful_payment.total_amount} Stars")

# ========== УПРАВЛЕНИЕ МОДЕРАТОРАМИ ==========
class AddModerator(StatesGroup):
    waiting_username = State()

@dp.message(Command("new_moderator"))
async def cmd_new_moderator(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только администратор может назначать модераторов.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="cancel_new_moderator")]])
    await send_new_message(message.chat.id, "📌 Отправьте username пользователя (например, @ivan) или его Telegram ID.\nОн будет назначен модератором.", reply_markup=kb)
    await state.set_state(AddModerator.waiting_username)

@dp.callback_query(F.data == "cancel_new_moderator")
async def cancel_new_moderator(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.delete()
    await cmd_start(callback.message)

@dp.message(AddModerator.waiting_username)
async def process_moderator_username(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    input_text = message.text.strip()
    if input_text.startswith('@'):
        input_text = input_text[1:]
    try:
        if input_text.isdigit():
            user = await bot.get_chat(int(input_text))
        else:
            user = await bot.get_chat(input_text)
        user_id = user.id
        user_name = user.username or user.first_name
    except Exception:
        await message.answer(f"❌ Пользователь '{input_text}' не найден. Убедитесь, что он написал боту хотя бы раз.")
        return
    current_role = db.get_role(user_id)
    if current_role != "user":
        await message.answer(f"⚠️ Пользователь @{user_name} уже имеет роль '{current_role}'.")
        await state.clear()
        return
    db.set_role(user_id, "moderator")
    await message.answer(f"✅ Пользователь @{user_name} назначен модератором!")
    try:
        await bot.send_message(user_id, "🛡️ Вы назначены модератором в ARVION Support! Теперь вы можете отвечать на тикеты и закрывать их.\n\nИспользуйте /help для списка команд.")
    except:
        pass
    log_action(message.from_user.id, message.from_user.username or "admin", "НАЗНАЧЕН МОДЕРАТОР", details=f"Модератор: {user_id} (@{user_name})")
    await state.clear()

@dp.message(Command("del_moderator"))
async def cmd_del_moderator(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только администратор может удалять модераторов.")
        return
    roles = db.get_all_roles()
    staff = [{"user_id": uid, "name": await get_user_display_name(uid), "role": role} for uid, role in roles.items() if role in ["admin", "moderator"] and uid != message.from_user.id]
    if not staff:
        await send_new_message(message.chat.id, "📭 Нет других администраторов или модераторов.")
        return
    items = staff
    pagination_data[message.chat.id] = {"items": items, "page": 0, "message_id": None}
    await show_page(message.chat.id, 0, items, 5,
                    lambda page_items, page, total: "📋 ВЫБЕРИТЕ ПОЛЬЗОВАТЕЛЯ ДЛЯ УДАЛЕНИЯ\n\n" + "\n".join([f"{i+1}. @{u['name']} ({u['role']})" for i, u in enumerate(page_items)]),
                    lambda: None)

# ========== ОБРАБОТЧИКИ ТИКЕТОВ ==========
class TicketState(StatesGroup):
    waiting_text = State()
    waiting_reply = State()
    waiting_user_reply = State()

@dp.callback_query(F.data.startswith("type_"))
async def process_type_selection(callback: types.CallbackQuery, state: FSMContext):
    if db.is_blacklisted(callback.from_user.id):
        await callback.answer("⛔ Заблокированы!", show_alert=True)
        return
    type_key = callback.data.split("_")[1]
    await callback.answer()
    await state.update_data(ticket_type=type_key)
    template = {
        "complaint": "📋 ФОРМА ЖАЛОБЫ | ARVION\n\nDiscord тег: \nTelegram username: \nНарушитель (ник/ID): \nСуть нарушения: \nДоказательства (скриншоты, ссылки): \nДата и время инцидента: \nПодробное описание:",
        "question": "❓ ФОРМА ВОПРОСА | ARVION\n\nDiscord тег: \nTelegram username: \nТема вопроса: \nПодробное описание:",
        "appeal": "⚖️ ФОРМА АПЕЛЛЯЦИИ | ARVION\n\nDiscord тег: \nTelegram username: \nПричина наказания (если известна): \nСсылка на решение (если есть): \nВаше объяснение: \nДополнительные доказательства:",
        "suggestion": "💡 ФОРМА ПРЕДЛОЖЕНИЯ | ARVION\n\nDiscord тег: \nTelegram username: \nСуть предложения: \nПочему это улучшит проект: \nПример реализации (если есть):",
        "other": "📌 ОБРАЩЕНИЕ (ДРУГОЕ) | ARVION\n\nDiscord тег: \nTelegram username: \nСуть обращения: \nПодробности:"
    }.get(type_key, "Опишите проблему")
    await send_new_message(callback.message.chat.id, f"{template}\n\n➡️ Заполните форму и отправьте одним сообщением", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_type_selection")]]))
    await state.set_state(TicketState.waiting_text)

@dp.callback_query(F.data == "back_to_type_selection")
async def back_to_type_selection(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await delete_previous_bot_message(callback.message.chat.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Жалоба", callback_data="type_complaint")],
        [InlineKeyboardButton(text="❓ Вопрос", callback_data="type_question")],
        [InlineKeyboardButton(text="⚖️ Апелляция", callback_data="type_appeal")],
        [InlineKeyboardButton(text="💡 Предложение", callback_data="type_suggestion")],
        [InlineKeyboardButton(text="📌 Другое", callback_data="type_other")]
    ])
    await send_new_message(callback.message.chat.id, "📌 Выберите тип обращения:", reply_markup=kb)

@dp.message(TicketState.waiting_text)
async def process_ticket_message(message: types.Message, state: FSMContext):
    if db.is_blacklisted(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Вы заблокированы!")
        await state.clear()
        return
    try:
        data = await state.get_data()
        ticket_type = data.get("ticket_type", "other")
        ticket_text = message.text or message.caption or ""
        if not ticket_text.strip():
            await send_new_message(message.chat.id, "❌ Пустое сообщение")
            return
        await delete_previous_bot_message(message.chat.id)
        try:
            await message.delete()
        except:
            pass
        ticket_id = generate_ticket_id()
        user_id = message.from_user.id
        username_raw = message.from_user.username or message.from_user.full_name
        user_link = f"@{username_raw}" if message.from_user.username else f"ID: {user_id}"
        db.create_ticket(ticket_id, user_id, username_raw, ticket_text)
        file_id = None
        file_type = None
        file_link = ""
        if message.photo:
            file_id = message.photo[-1].file_id
            file_type = "photo"
            file_link = "Фото"
            ticket_text += "\n\n📎 [Фото]"
        elif message.document:
            file_id = message.document.file_id
            file_type = "document"
            file_link = f"Файл: {message.document.file_name}"
            ticket_text += f"\n\n📎 [{message.document.file_name}]"
        db.save_message(ticket_id, "user", ticket_text, file_id, file_type)
        if sheet:
            def add_to_google_sheets(tid, uid, uname, ttype, txt, stat, flink):
                try:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sheet.append_row([now, tid, uid, uname, ttype, txt, stat, "", flink])
                except Exception as e:
                    print(f"Ошибка Google Sheets: {e}")
            add_to_google_sheets(ticket_id, user_id, username_raw, ticket_type, ticket_text[:500], "open", file_link)
        await send_new_message(message.chat.id, f"✅ Обращение #{ticket_id} принято!")
        sentiment_emoji, sentiment_text = analyze_sentiment(ticket_text)
        user_role = db.get_role(user_id)
        role_prefix = "👑 АДМИН " if user_role == "admin" else ""
        admin_message = f"{role_prefix}🆔 НОВЫЙ ТИКЕТ #{ticket_id} {sentiment_emoji}[{sentiment_text}]\n\n👤 {user_link} (ID: {user_id})\n\n{ticket_text}"
        roles = db.get_all_roles()
        for admin_id in roles.keys():
            try:
                assigned = db.get_assigned_admin(ticket_id)
                if assigned:
                    continue
                if file_id and file_type == "photo":
                    await bot.send_photo(admin_id, file_id, caption=admin_message, reply_markup=get_ticket_keyboard(ticket_id, roles.get(admin_id, "user"), False))
                elif file_id and file_type == "document":
                    await bot.send_document(admin_id, file_id, caption=admin_message, reply_markup=get_ticket_keyboard(ticket_id, roles.get(admin_id, "user"), False))
                else:
                    await bot.send_message(admin_id, admin_message, reply_markup=get_ticket_keyboard(ticket_id, roles.get(admin_id, "user"), False))
            except Exception as e:
                print(f"Ошибка админу {admin_id}: {e}")
        await state.clear()
    except Exception as e:
        print(f"Ошибка: {e}")
        traceback.print_exc()
        await send_new_message(message.chat.id, "❌ Ошибка")

def get_ticket_keyboard(ticket_id: str, user_role: str, is_assigned: bool = False, is_view_mode: bool = False):
    if is_view_mode:
        kb = [
            [InlineKeyboardButton(text="💬 Ответить", callback_data=f"admin_reply_{ticket_id}")],
            [InlineKeyboardButton(text="✅ Закрыть тикет", callback_data=f"close_{ticket_id}")],
            [InlineKeyboardButton(text="📨 Передать", callback_data=f"show_transfer_{ticket_id}")]
        ]
        if user_role == "admin":
            kb.append([InlineKeyboardButton(text="🚫 В чёрный список", callback_data=f"blacklist_user_{ticket_id}")])
        kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"back_to_ticket_{ticket_id}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)
    if is_assigned:
        kb = [
            [InlineKeyboardButton(text="💬 Ответить", callback_data=f"admin_reply_{ticket_id}")],
            [InlineKeyboardButton(text="✅ Закрыть тикет", callback_data=f"close_{ticket_id}")],
            [InlineKeyboardButton(text="📨 Передать", callback_data=f"show_transfer_{ticket_id}")]
        ]
        if user_role == "admin":
            kb.append([InlineKeyboardButton(text="🚫 В чёрный список", callback_data=f"blacklist_user_{ticket_id}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)
    else:
        kb = [[InlineKeyboardButton(text="💬 Принять", callback_data=f"accept_{ticket_id}")]]
        if user_role == "admin":
            kb.append([InlineKeyboardButton(text="👁️ Посмотреть", callback_data=f"view_{ticket_id}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.callback_query(F.data.startswith("accept_"))
async def accept_ticket(callback: types.CallbackQuery):
    await ensure_user_in_ratings(callback.from_user.id)
    ticket_id = callback.data.split("_")[1]
    admin_id = callback.from_user.id
    role = db.get_role(admin_id)
    if role not in ["admin", "moderator"]:
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    assigned = db.get_assigned_admin(ticket_id)
    if assigned:
        await callback.answer(f"❌ Тикет уже принят!", show_alert=True)
        return
    db.assign_ticket(ticket_id, admin_id)
    ticket = db.get_ticket(ticket_id)
    if ticket:
        user_id = ticket[2]
        await bot.send_message(user_id, f"✅ Ваш тикет #{ticket_id} принят в работу. Ожидайте ответа!")
    await callback.message.edit_reply_markup(reply_markup=get_ticket_keyboard(ticket_id, role, True))
    await callback.answer("✅ Тикет принят!")
    log_action(admin_id, callback.from_user.username or "admin", "ПРИНЯЛ ТИКЕТ", ticket_id)

@dp.callback_query(F.data.startswith("view_"))
async def view_ticket(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Только для админов!", show_alert=True)
        return
    ticket_id = callback.data.split("_")[1]
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    assigned = db.get_assigned_admin(ticket_id)
    status = "✅ ПРИНЯТ" if assigned else "🆓 СВОБОДЕН"
    assigned_text = f"\n👨‍💼 Принял: {assigned}" if assigned else ""
    text = f"🔍 ПРОСМОТР ТИКЕТА #{ticket_id}\n{status}{assigned_text}\n\n{ticket[4]}"
    await callback.message.answer(text, reply_markup=get_ticket_keyboard(ticket_id, "admin", False, True))
    await callback.answer()

@dp.callback_query(F.data.startswith("close_"))
async def close_ticket(callback: types.CallbackQuery):
    await ensure_user_in_ratings(callback.from_user.id)
    role = db.get_role(callback.from_user.id)
    if role not in ["admin", "moderator"]:
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    ticket_id = callback.data.split("_")[1]
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    if ticket[5] == "waiting_rating":
        await callback.answer("⏳ Пользователь ещё не оценил тикет", show_alert=True)
        return
    assigned = db.get_assigned_admin(ticket_id)
    if assigned != callback.from_user.id:
        await callback.answer("❌ Тикет принят другим!", show_alert=True)
        return
    db.update_ticket_status(ticket_id, "waiting_rating")
    user_id = ticket[2]
    await bot.send_message(user_id,
        f"🔒 Тикет #{ticket_id} ожидает закрытия.\n\nПожалуйста, оцените работу администратора:",
        reply_markup=get_rating_keyboard(ticket_id))
    await callback.answer("✅ Запрос оценки отправлен пользователю")
    await callback.message.edit_reply_markup(reply_markup=None)
    log_action(callback.from_user.id, callback.from_user.username or "admin", "ЗАПРОС ОЦЕНКИ", ticket_id)

def get_rating_keyboard(ticket_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐1", callback_data=f"rate_1_{ticket_id}"),
         InlineKeyboardButton(text="⭐2", callback_data=f"rate_2_{ticket_id}"),
         InlineKeyboardButton(text="⭐3", callback_data=f"rate_3_{ticket_id}"),
         InlineKeyboardButton(text="⭐4", callback_data=f"rate_4_{ticket_id}"),
         InlineKeyboardButton(text="⭐5", callback_data=f"rate_5_{ticket_id}")]
    ])

@dp.callback_query(F.data.startswith("rate_"))
async def process_rating(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    rating = int(parts[1])
    ticket_id = parts[2]
    user_id = callback.from_user.id
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    assigned_admin = db.get_assigned_admin(ticket_id)
    if not assigned_admin:
        await callback.answer("❌ Не удалось определить администратора", show_alert=True)
        return
    if await add_rating(assigned_admin, rating, ticket_id, user_id):
        db.update_ticket_status(ticket_id, "closed")
        db.unassign_ticket(ticket_id)
        if sheet:
            def update_rating_in_google_sheets(tid, rat):
                try:
                    cell = sheet.find(tid, in_column=2)
                    if cell:
                        sheet.update_cell(cell.row, 9, rat)
                except Exception as e:
                    print(f"Ошибка Google Sheets: {e}")
            update_rating_in_google_sheets(ticket_id, rating)
        await callback.answer(f"⭐ Спасибо за оценку {rating}!")
        await callback.message.edit_text(f"✅ Тикет #{ticket_id} закрыт. Спасибо за оценку {rating}⭐!")
        for admin_id in db.get_all_roles().keys():
            try:
                await bot.send_message(admin_id, f"📊 Пользователь {user_id} оценил тикет #{ticket_id} на {rating}⭐")
            except:
                pass
        log_action(0, "system", "ОЦЕНКА И ЗАКРЫТИЕ", ticket_id, f"Оценка: {rating} от {user_id} для админа {assigned_admin}")
    else:
        await callback.answer("❌ Нельзя оценить самого себя!", show_alert=True)

@dp.callback_query(F.data.startswith("admin_reply_"))
async def admin_reply(callback: types.CallbackQuery, state: FSMContext):
    role = db.get_role(callback.from_user.id)
    if role not in ["admin", "moderator"]:
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    ticket_id = callback.data.split("_")[2]
    assigned = db.get_assigned_admin(ticket_id)
    if assigned != callback.from_user.id:
        await callback.answer("❌ Тикет принят другим!", show_alert=True)
        return
    await callback.answer()
    # Показываем клавиатуру с шаблонами (простая версия, без пагинации для простоты)
    templates = load_templates()
    if templates:
        keyboard = []
        for k, v in list(templates.items())[:10]:  # максимум 10 шаблонов
            keyboard.append([InlineKeyboardButton(text=k, callback_data=f"template_{k}_{ticket_id}")])
        keyboard.append([InlineKeyboardButton(text="✍️ Свой ответ", callback_data=f"custom_reply_{ticket_id}")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await send_new_message(callback.message.chat.id, "📋 Выберите шаблон или напишите свой ответ:", reply_markup=reply_markup)
    else:
        await send_new_message(callback.message.chat.id, f"✍️ Введите ответ для тикета #{ticket_id}:")
    await state.update_data(reply_ticket_id=ticket_id)
    await state.set_state(TicketState.waiting_reply)

@dp.callback_query(F.data.startswith("template_"))
async def template_callback(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer()
        return
    template_key = parts[1]
    ticket_id = parts[2]
    templates = load_templates()
    text = templates.get(template_key)
    if not text:
        await callback.answer("❌ Шаблон не найден")
        return
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    user_id = ticket[2]
    admin_username = callback.from_user.username or callback.from_user.full_name
    avg_rating, rating_count = await get_user_rating(callback.from_user.id)
    rating_text = f" (рейтинг: {avg_rating}/5 ⭐)" if rating_count > 0 else ""
    role = db.get_role(callback.from_user.id)
    role_prefix = "👑 АДМИН " if role == "admin" else ""
    await bot.send_message(user_id, f"{role_prefix}👨‍💼 {role.capitalize()} @{admin_username}{rating_text} ответил:\n\n{text}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить администратору", callback_data=f"user_reply_{ticket_id}")]]))
    db.save_message(ticket_id, "admin", text)
    await callback.answer("✅ Шаблон отправлен")
    await callback.message.delete()
    await state.clear()

@dp.callback_query(F.data.startswith("custom_reply_"))
async def custom_reply(callback: types.CallbackQuery, state: FSMContext):
    ticket_id = callback.data.split("_")[2]
    await callback.answer()
    await send_new_message(callback.message.chat.id, f"✍️ Введите ответ для тикета #{ticket_id}:")
    await state.update_data(reply_ticket_id=ticket_id)
    await state.set_state(TicketState.waiting_reply)

@dp.message(TicketState.waiting_reply)
async def send_admin_reply(message: types.Message, state: FSMContext):
    role = db.get_role(message.from_user.id)
    if role not in ["admin", "moderator"]:
        return
    data = await state.get_data()
    ticket_id = data.get("reply_ticket_id")
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await send_new_message(message.chat.id, "❌ Тикет не найден!")
        await state.clear()
        return
    assigned = db.get_assigned_admin(ticket_id)
    if assigned != message.from_user.id:
        await send_new_message(message.chat.id, "❌ Тикет принят другим!")
        await state.clear()
        return
    user_id = ticket[2]
    reply_text = message.text or ""
    file_id = None
    file_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
        reply_text = message.caption or ""
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        reply_text = message.caption or ""
    db.save_message(ticket_id, "admin", reply_text, file_id, file_type)
    avg_rating, rating_count = await get_user_rating(message.from_user.id)
    rating_text = f" (рейтинг: {avg_rating}/5 ⭐)" if rating_count > 0 else ""
    admin_username = message.from_user.username or message.from_user.full_name
    role = db.get_role(message.from_user.id)
    role_prefix = "👑 АДМИН " if role == "admin" else ""
    try:
        if file_id and file_type == "photo":
            await bot.send_photo(user_id, file_id, caption=f"{role_prefix}👨‍💼 {role.capitalize()} @{admin_username}{rating_text} ответил:\n\n{reply_text}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить администратору", callback_data=f"user_reply_{ticket_id}")]]))
        elif file_id and file_type == "document":
            await bot.send_document(user_id, file_id, caption=f"{role_prefix}👨‍💼 {role.capitalize()} @{admin_username}{rating_text} ответил:\n\n{reply_text}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить администратору", callback_data=f"user_reply_{ticket_id}")]]))
        else:
            await bot.send_message(user_id, f"{role_prefix}👨‍💼 {role.capitalize()} @{admin_username}{rating_text} ответил:\n\n{reply_text}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить администратору", callback_data=f"user_reply_{ticket_id}")]]))
        await delete_previous_bot_message(message.chat.id)
        await send_new_message(message.chat.id, f"✅ Ответ отправлен для #{ticket_id}")
    except Exception as e:
        await send_new_message(message.chat.id, f"❌ Ошибка: {e}")
    await state.clear()

@dp.callback_query(F.data.startswith("user_reply_"))
async def user_reply(callback: types.CallbackQuery, state: FSMContext):
    if db.is_blacklisted(callback.from_user.id):
        await callback.answer("⛔ Вы заблокированы!", show_alert=True)
        return
    ticket_id = callback.data.split("_")[2]
    await callback.answer()
    await state.update_data(user_reply_ticket_id=ticket_id)
    await send_new_message(callback.message.chat.id, f"✍️ Ваш ответ по тикету #{ticket_id}:")
    await state.set_state(TicketState.waiting_user_reply)

@dp.message(TicketState.waiting_user_reply)
async def send_user_reply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("user_reply_ticket_id")
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await send_new_message(message.chat.id, "❌ Тикет не найден!")
        await state.clear()
        return
    reply_text = message.text or ""
    file_id = None
    file_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
        reply_text = message.caption or ""
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        reply_text = message.caption or ""
    db.save_message(ticket_id, "user", reply_text, file_id, file_type)
    assigned_admin = db.get_assigned_admin(ticket_id)
    if assigned_admin:
        role = db.get_role(assigned_admin)
        try:
            if file_id and file_type == "photo":
                await bot.send_photo(assigned_admin, file_id, caption=f"💬 Ответ пользователя по #{ticket_id}:\n\n{reply_text}", reply_markup=get_ticket_keyboard(ticket_id, role, True))
            elif file_id and file_type == "document":
                await bot.send_document(assigned_admin, file_id, caption=f"💬 Ответ пользователя по #{ticket_id}:\n\n{reply_text}", reply_markup=get_ticket_keyboard(ticket_id, role, True))
            else:
                await bot.send_message(assigned_admin, f"💬 Ответ пользователя по #{ticket_id}:\n\n{reply_text}", reply_markup=get_ticket_keyboard(ticket_id, role, True))
        except Exception as e:
            print(f"Ошибка админу: {e}")
    await delete_previous_bot_message(message.chat.id)
    await send_new_message(message.chat.id, f"✅ Ответ для #{ticket_id} отправлен")
    await state.clear()

# ========== ЧЁРНЫЙ СПИСОК ==========
@dp.callback_query(F.data.startswith("blacklist_user_"))
async def blacklist_user(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Только для админов!", show_alert=True)
        return
    ticket_id = callback.data.split("_")[2]
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    user_id = ticket[2]
    db.add_to_blacklist(user_id, reason=f"Забанен из тикета {ticket_id} админом {callback.from_user.id}")
    await callback.answer("✅ Пользователь добавлен в чёрный список")
    await bot.send_message(user_id, "⛔ Вы были добавлены в чёрный список ARVION Support.")
    log_action(callback.from_user.id, callback.from_user.username or "admin", "ЧЁРНЫЙ СПИСОК", ticket_id, f"User {user_id}")

@dp.message(Command("unblacklist"))
async def cmd_unblacklist(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    blacklist = db.get_blacklist()
    if not blacklist:
        await send_new_message(message.chat.id, "📭 Чёрный список пуст.")
        return
    items = [{"user_id": row[0], "reason": row[1]} for row in blacklist]
    pagination_data[message.chat.id] = {"items": items, "page": 0, "message_id": None}
    await show_page(message.chat.id, 0, items, 5,
                    lambda page_items, page, total: "🚫 ВЫБЕРИТЕ ПОЛЬЗОВАТЕЛЯ ДЛЯ РАЗБЛОКИРОВКИ\n\n" + "\n".join([f"{i+1}. {await get_user_display_name(u['user_id'])} (ID: {u['user_id']})" for i, u in enumerate(page_items)]),
                    lambda: None)
    # Упрощённо: после показа списка ожидаем команду /unblacklist ID
    await send_new_message(message.chat.id, "Введите `/unblacklist ID` (например, /unblacklist 123456789)")

@dp.message(Command("unblacklist"))
async def unblacklist_by_id(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await send_new_message(message.chat.id, "❌ Используйте: /unblacklist ID")
        return
    user_id = int(args[1])
    db.remove_from_blacklist(user_id)
    await send_new_message(message.chat.id, f"✅ Пользователь {user_id} удалён из чёрного списка.")

# ========== ОСТАЛЬНЫЕ КОМАНДЫ (статистика, шаблоны, экспорт и т.д.) ==========
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    role = db.get_role(message.from_user.id)
    if role not in ["admin", "moderator"]:
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    tickets = db.get_all_tickets()
    total = len(tickets)
    open_tickets = len([t for t in tickets if t[5] == "open"])
    closed_tickets = len([t for t in tickets if t[5] == "closed"])
    today = datetime.now().date()
    today_tickets = len([t for t in tickets if t[6] and datetime.fromisoformat(t[6]).date() == today])
    await send_new_message(message.chat.id, f"📊 СТАТИСТИКА\n\nВсего: {total}\n🟡 Открыто: {open_tickets}\n🔴 Закрыто: {closed_tickets}\n📅 За сегодня: {today_tickets}")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    role = db.get_role(message.from_user.id)
    if role not in ["admin", "moderator"]:
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_new_message(message.chat.id, "❌ /search текст")
        return
    query = args[1].lower()
    tickets = db.get_all_tickets()
    results = [t for t in tickets if query in t[4].lower() or query in t[1].lower()]
    if not results:
        await send_new_message(message.chat.id, f"🔍 По '{query}' ничего нет")
        return
    text = f"🔍 Найдено: {len(results)}\n\n"
    for t in results[:10]:
        created = t[6][:16] if t[6] else "дата неизвестна"
        text += f"🆔 {t[1]} | {t[5]}\n   {t[4][:80]}...\n\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("all_tickets"))
async def cmd_all_tickets(message: types.Message):
    role = db.get_role(message.from_user.id)
    if role not in ["admin", "moderator"]:
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    tickets = db.get_open_tickets()
    if not tickets:
        await send_new_message(message.chat.id, "📭 Открытых тикетов нет.")
        return
    pagination_data[message.chat.id] = {"items": tickets, "page": 0, "message_id": None}
    await show_page(message.chat.id, 0, tickets, 5,
                    lambda page_items, page, total: f"📋 ОТКРЫТЫЕ ТИКЕТЫ (стр. {page} из {total})\n\n" + "\n".join([f"🆔 {t[1]}\n👤 {t[3]} (ID: {t[2]})\n📅 {t[6][:16] if t[6] else 'дата неизвестна'}\n📝 {t[4][:80]}...\n" for t in page_items]),
                    lambda: None)

@dp.message(Command("clear_tickets"))
async def cmd_clear_tickets(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    await send_new_message(message.chat.id, "⚠️ Удалить ВСЕ тикеты?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ ДА", callback_data="confirm_clear_all"), InlineKeyboardButton(text="❌ НЕТ", callback_data="cancel_clear")]]))

@dp.callback_query(F.data == "confirm_clear_all")
async def confirm_clear_all(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    try:
        conn = db.get_db_connection()
        conn.execute("DELETE FROM tickets")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM assigned_tickets")
        conn.commit()
        conn.close()
        with open("ticket_counter.txt", "w") as f:
            f.write("0")
        await callback.message.edit_text("✅ Все тикеты удалены!")
        log_action(callback.from_user.id, callback.from_user.username or "admin", "ОЧИСТКА ТИКЕТОВ")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}")
    await callback.answer()

@dp.callback_query(F.data == "cancel_clear")
async def cancel_clear(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Отменено")
    await callback.answer()

@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    tickets = db.get_all_tickets()
    if not tickets:
        await send_new_message(message.chat.id, "📭 Нет тикетов")
        return
    output = io.StringIO()
    output.write("ID Тикета,Отправитель ID,Username,Статус,Дата,Текст\n")
    for t in tickets:
        text = t[4].replace(",", " ").replace("\n", " ").replace('"', "'")
        created = t[6] if t[6] else ""
        output.write(f"{t[1]},{t[2]},{t[3]},{t[5]},{created},\"{text}\"\n")
    file_data = output.getvalue().encode("utf-8")
    await message.answer_document(BufferedInputFile(file_data, filename="tickets.csv"), caption="📊 Экспорт")
    output.close()
    log_action(message.from_user.id, message.from_user.username or "admin", "ЭКСПОРТ")

@dp.message(Command("log"))
async def cmd_log(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
        if not lines:
            await send_new_message(message.chat.id, "📭 Лог пуст")
            return
        text = "📜 ПОСЛЕДНИЕ ДЕЙСТВИЯ:\n\n" + "".join(lines[-15:])
        await send_new_message(message.chat.id, text)
    except:
        await send_new_message(message.chat.id, "📭 Лог пуст")

@dp.message(Command("announce"))
async def cmd_announce(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_new_message(message.chat.id, "❌ /announce текст")
        return
    announce_text = args[1]
    users = set()
    for t in db.get_all_tickets():
        if t[2]:
            users.add(t[2])
    for admin_id in db.get_all_roles().keys():
        users.add(admin_id)
    if not users:
        users.add(ADMIN_IDS[0])
        await send_new_message(message.chat.id, "⚠️ База пользователей пуста. Рассылка только администратору.")
    success = 0
    for user_id in users:
        if db.is_blacklisted(user_id):
            continue
        try:
            await bot.send_message(user_id, f"📢 МАССОВОЕ УВЕДОМЛЕНИЕ\n\n{announce_text}")
            success += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await send_new_message(message.chat.id, f"✅ Отправлено: {success}")
    log_action(message.from_user.id, message.from_user.username or "admin", "РАССЫЛКА")

@dp.message(Command("templates"))
async def cmd_templates(message: types.Message):
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    templates = load_templates()
    if not templates:
        await send_new_message(message.chat.id, "📋 Шаблонов нет")
        return
    text = "📋 ШАБЛОНЫ:\n\n" + "\n".join([f"🔹 {k}: {v[:50]}..." for k, v in templates.items()])
    await send_new_message(message.chat.id, text)

@dp.message(Command("add_template"))
async def cmd_add_template(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await send_new_message(message.chat.id, "❌ /add_template ключ текст ответа")
        return
    key, value = args[1], args[2]
    templates = load_templates()
    templates[key] = value
    save_templates(templates)
    await send_new_message(message.chat.id, f"✅ Шаблон '{key}' добавлен!")

@dp.message(Command("del_template"))
async def cmd_del_template(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    args = message.text.split()
    if len(args) < 2:
        await send_new_message(message.chat.id, "❌ /del_template ключ")
        return
    key = args[1]
    templates = load_templates()
    if key in templates:
        del templates[key]
        save_templates(templates)
        await send_new_message(message.chat.id, f"✅ Шаблон '{key}' удалён!")
    else:
        await send_new_message(message.chat.id, f"❌ Шаблон '{key}' не найден.")

@dp.message(Command("list_templates"))
async def cmd_list_templates(message: types.Message):
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    templates = load_templates()
    if not templates:
        await send_new_message(message.chat.id, "📭 Шаблонов нет.")
        return
    items = list(templates.items())
    pagination_data[message.chat.id] = {"items": items, "page": 0, "message_id": None}
    await show_page(message.chat.id, 0, items, 11,
                    lambda page_items, page, total: "📋 СПИСОК ШАБЛОНОВ\n\n" + "\n".join([f"🔹 *{k}*: {v[:50]}..." for k, v in page_items]),
                    lambda: None)

@dp.message(Command("transfer"))
async def cmd_transfer(message: types.Message):
    role = db.get_role(message.from_user.id)
    if role not in ["admin", "moderator"]:
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    args = message.text.split()
    if len(args) < 2:
        await send_new_message(message.chat.id, "❌ /transfer @username")
        return
    username = args[1].lstrip('@')
    try:
        user = await bot.get_chat(username)
        target_id = user.id
    except:
        await send_new_message(message.chat.id, f"❌ Пользователь @{username} не найден.")
        return
    tickets = db.get_all_tickets()
    my_ticket = None
    for t in tickets:
        if t[8] == message.from_user.id and t[5] in ["open", "waiting_rating"]:
            my_ticket = t
            break
    if not my_ticket:
        await send_new_message(message.chat.id, "❌ У вас нет принятых открытых тикетов.")
        return
    ticket_id = my_ticket[1]
    target_role = db.get_role(target_id)
    if target_role not in ["admin", "moderator"]:
        await send_new_message(message.chat.id, "❌ Передать можно только админу или модератору.")
        return
    db.assign_ticket(ticket_id, target_id)
    await bot.send_message(target_id, f"📨 Вам передан тикет #{ticket_id} от @{message.from_user.username or message.from_user.first_name}\n\n{my_ticket[4][:300]}")
    await send_new_message(message.chat.id, f"✅ Тикет #{ticket_id} передан @{username}")
    log_action(message.from_user.id, message.from_user.username or "admin", "ПЕРЕДАЛ ТИКЕТ", ticket_id, f"Кому: {target_id}")

@dp.message(Command("note"))
async def cmd_note(message: types.Message):
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    tickets = db.get_all_tickets()
    my_ticket = None
    for t in tickets:
        if t[8] == message.from_user.id and t[5] in ["open", "waiting_rating"]:
            my_ticket = t
            break
    if not my_ticket:
        await send_new_message(message.chat.id, "❌ У вас нет принятых открытых тикетов.")
        return
    ticket_id = my_ticket[1]
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_new_message(message.chat.id, "❌ Укажите текст заметки: `/note текст`", parse_mode="Markdown")
        return
    note_text = args[1]
    db.add_note(ticket_id, message.from_user.id, note_text)
    await send_new_message(message.chat.id, "✅ Заметка добавлена (видна только персоналу).")
    log_action(message.from_user.id, message.from_user.username or "admin", "ДОБАВИЛ ЗАМЕТКУ", ticket_id)

@dp.message(Command("notes"))
async def cmd_notes(message: types.Message):
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    tickets = db.get_all_tickets()
    my_ticket = None
    for t in tickets:
        if t[8] == message.from_user.id and t[5] in ["open", "waiting_rating"]:
            my_ticket = t
            break
    if not my_ticket:
        await send_new_message(message.chat.id, "❌ У вас нет принятых открытых тикетов.")
        return
    ticket_id = my_ticket[1]
    notes = db.get_notes(ticket_id)
    if not notes:
        await send_new_message(message.chat.id, "📭 Заметок по этому тикету нет.")
        return
    text = f"📝 ЗАМЕТКИ ПО ТИКЕТУ {ticket_id}:\n\n"
    for n in notes:
        admin_id, note_text, created_at = n
        admin_name = await get_user_display_name(admin_id)
        try:
            created = datetime.fromisoformat(created_at).strftime("%d.%m %H:%M")
        except:
            created = created_at[:16]
        text += f"[{created}] @{admin_name}: {note_text}\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("admin_stats"))
async def cmd_admin_stats(message: types.Message):
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    ratings = load_ratings()
    stats = []
    for admin_id, data in ratings.items():
        role = data.get("role", db.get_role(admin_id))
        if role not in ["admin", "moderator"]:
            continue
        avg, count = await get_user_rating(admin_id)
        name = data["username"] if data["username"] != str(admin_id) else await get_user_display_name(admin_id)
        stats.append((name, avg, count, role))
    if not stats:
        await send_new_message(message.chat.id, "📊 Нет данных о работе персонала.")
        return
    stats.sort(key=lambda x: x[1], reverse=True)
    text = "📊 СТАТИСТИКА ПЕРСОНАЛА\n\n"
    for name, avg, count, role in stats:
        role_icon = "👑" if role == "admin" else "🛡️"
        text += f"{role_icon} @{name} — {avg} ⭐ ({count} оценок)\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    role = db.get_role(message.from_user.id)
    user_commands = (
        "📖 ОБЩИЕ КОМАНДЫ\n"
        "/create_ticket – создать обращение\n"
        "/my_tickets – мои обращения\n"
        "/get_user – узнать свой ID\n"
        "/top_staff – топ персонала\n"
        "/donate – поддержать проект\n"
        "/help – эта инструкция\n\n"
        "После закрытия тикета вы можете оценить работу (1-5⭐).\n"
        "Анализ тональности автоматически помечает 🔴 негатив, 🟢 позитив, 🟡 нейтрально."
    )
    await send_new_message(message.chat.id, user_commands, keep=True)
    if role in ["moderator", "admin"]:
        mod_commands = (
            "🛡️ МОДЕРАТОРСКИЕ КОМАНДЫ\n"
            "/stats – статистика обращений\n"
            "/search текст – поиск по тикетам\n"
            "/all_tickets – список открытых тикетов\n"
            "/templates – показать шаблоны\n"
            "/transfer @username – передать тикет администратору\n"
            "/note текст – добавить заметку\n"
            "/notes – просмотреть заметки\n"
            "/admin_stats – статистика по персоналу\n"
            "/top_staff – топ персонала\n\n"
            "Кнопки под тикетом: Принять, Ответить, Закрыть, Передать."
        )
        await send_new_message(message.chat.id, mod_commands, keep=True)
    if role == "admin":
        admin_commands = (
            "👑 АДМИНСКИЕ КОМАНДЫ\n"
            "/clear_tickets – удалить все тикеты\n"
            "/export – экспорт тикетов в CSV\n"
            "/log – история действий\n"
            "/announce текст – массовая рассылка\n"
            "/new_moderator – назначить модератора\n"
            "/del_moderator – удалить модератора\n"
            "/add_template ключ текст – добавить шаблон\n"
            "/del_template ключ – удалить шаблон\n"
            "/list_templates – список шаблонов\n"
            "/unblacklist ID – удалить из чёрного списка\n\n"
            "Кнопки под тикетом: Принять, Посмотреть, Ответить, Закрыть, Передать, В чёрный список."
        )
        await send_new_message(message.chat.id, admin_commands, keep=True)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    asyncio.run(main())