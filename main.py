import asyncio
import logging
import uuid
import traceback
import io
import os
import json
from datetime import datetime
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
# НАСТРОЙКИ (измените при необходимости)
# ===========================================
BOT_TOKEN = "8918794962:AAGMCCr86CkgL6ASFmFoJnqNgc-Kp6Vsvtw"
ADMIN_IDS = [1781331191]
SPREADSHEET_ID = "1Z70dNBhBC6Qb84Tiig8PJWaTpU3YoN_QC-zdEb4hzfM"
CREDENTIALS_FILE = "credentials.json"
# =================================================

# Google Sheets (опционально)
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

def analyze_sentiment(text: str):
    text_lower = text.lower()
    neg = sum(1 for w in NEGATIVE_WORDS if w in text_lower)
    pos = sum(1 for w in POSITIVE_WORDS if w in text_lower)
    return ("🔴", "негатив") if neg > pos else (("🟢", "позитив") if pos > neg else ("🟡", "нейтрально"))

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
            return f"@{user.username}"
        else:
            return user.first_name or str(user_id)
    except:
        return str(user_id)

def is_admin(user_id: int) -> bool:
    return db.get_role(user_id) == "admin"

def is_moderator(user_id: int) -> bool:
    return db.get_role(user_id) in ["admin", "moderator"]

# ========== РЕЙТИНГИ ==========
RATINGS_FILE = "ratings.txt"
def load_ratings():
    ratings = {}
    try:
        with open(RATINGS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) >= 3:
                    uid = int(parts[0])
                    username = parts[1]
                    role = parts[2]
                    scores = [int(x) for x in parts[3].split(",") if x.strip().isdigit()] if len(parts) > 3 else []
                    ratings[uid] = {"username": username, "role": role, "scores": scores}
    except FileNotFoundError:
        with open(RATINGS_FILE, "w", encoding="utf-8") as f:
            f.write("# Формат: ID|username|роль|оценки\n")
    return ratings

def save_ratings(ratings):
    with open(RATINGS_FILE, "w", encoding="utf-8") as f:
        f.write("# Формат: ID|username|роль|оценки\n")
        for uid, data in ratings.items():
            f.write(f"{uid}|{data['username']}|{data['role']}|{','.join(str(s) for s in data['scores'])}\n")

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

async def get_user_rating(user_id: int):
    await ensure_user_in_ratings(user_id)
    ratings = load_ratings()
    scores = ratings.get(user_id, {}).get("scores", [])
    if not scores:
        return 0, 0
    return round(sum(scores)/len(scores), 1), len(scores)

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
def get_next_ticket_number():
    try:
        with open(ticket_counter_file, "r") as f:
            return int(f.read().strip()) + 1
    except:
        return 1
def save_ticket_number(number: int):
    with open(ticket_counter_file, "w") as f:
        f.write(str(number))
def generate_ticket_id():
    number = get_next_ticket_number()
    save_ticket_number(number)
    return f"{number:03d}-{str(uuid.uuid4())[:5]}"

# ========== ШАБЛОНЫ ==========
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
                    k, v = line.split("|", 1)
                    templates[k.strip()] = v.strip()
    except FileNotFoundError:
        with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
            f.write("# ключ|текст\n")
            f.write("принято|✅ Ваше обращение принято.\n")
            f.write("отклонено|❌ Отклонено.\n")
            f.write("бан|🚫 Вы заблокированы.\n")
        templates = {"принято": "✅ Принято.", "отклонено": "❌ Отклонено.", "бан": "🚫 Заблокирован."}
    return templates

def save_templates(templates):
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        for k, v in templates.items():
            f.write(f"{k}|{v}\n")

# ========== ЛОГИ ==========
LOG_FILE = "admin_logs.txt"
def log_action(admin_id, admin_name, action, ticket_id=None, details=""):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] Админ {admin_id} (@{admin_name}) - {action}" + (f" | Тикет: {ticket_id}" if ticket_id else "") + (f" | {details}" if details else "") + "\n")

# ========== ПАГИНАЦИЯ (с поддержкой асинхронных функций) ==========
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
    if asyncio.iscoroutinefunction(text_func):
        text = await text_func(page_items, page+1, total_pages)
    else:
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
        msg = await send_new_message(chat_id, text, parse_mode=None, reply_markup=reply_markup)
        pagination_data[chat_id]["message_id"] = msg.message_id
    else:
        try:
            await bot.edit_message_text(text, chat_id, data["message_id"], parse_mode=None, reply_markup=reply_markup)
        except:
            msg = await send_new_message(chat_id, text, parse_mode=None, reply_markup=reply_markup)
            pagination_data[chat_id]["message_id"] = msg.message_id

# ========== ВЕБХУК ==========
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
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path='/webhook')
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://arvion-ticket-bot.onrender.com")
    app.on_startup.append(lambda _: on_startup(base_url))
    app.on_shutdown.append(lambda _: on_shutdown())
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, '0.0.0.0', port).start()
    print(f"✅ Веб-сервер на порту {port}")
async def main():
    await bot.delete_webhook()
    await start_webhook_server()
    await asyncio.Event().wait()

# ========== СОСТОЯНИЯ ==========
class TicketState(StatesGroup):
    waiting_text = State()
    waiting_reply = State()
    waiting_user_reply = State()

class AddModerator(StatesGroup):
    waiting_username = State()

# ========== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if db.is_blacklisted(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ ДОСТУП ЗАБЛОКИРОВАН")
        return
    await send_new_message(message.chat.id,
        "🌿 ARVION Support\n/create_ticket — обращение\n/my_tickets — мои обращения\n/get_user — мой ID\n/top_staff — топ\n/donate — поддержать\n/faq — частые вопросы\n/help — инструкция")

@dp.message(Command("create_ticket"))
async def cmd_create_ticket(message: types.Message, state: FSMContext):
    try:
        await message.delete()
    except:
        pass
    if db.is_blacklisted(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Вы заблокированы!")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Жалоба", callback_data="type_complaint")],
        [InlineKeyboardButton(text="❓ Вопрос", callback_data="type_question")],
        [InlineKeyboardButton(text="⚖️ Апелляция", callback_data="type_appeal")],
        [InlineKeyboardButton(text="💡 Предложение", callback_data="type_suggestion")],
        [InlineKeyboardButton(text="📌 Другое", callback_data="type_other")]
    ])
    await send_new_message(message.chat.id, "Выберите тип:", reply_markup=kb)

@dp.message(Command("my_tickets"))
async def cmd_my_tickets(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    tickets = db.get_user_tickets(message.from_user.id)
    if not tickets:
        await send_new_message(message.chat.id, "📭 Нет обращений.")
        return
    text = "📋 Ваши обращения:\n\n"
    for t in tickets:
        tid, txt, status, created = t
        date = created[:16] if created else "дата неизвестна"
        text += f"{'🟡' if status=='open' else '🔴'} {tid} | {status} | {date}\n   {txt[:80]}...\n\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("get_user"))
async def cmd_get_user(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    u = message.from_user
    name = await get_user_display_name(u.id)
    await send_new_message(message.chat.id, f"Ваш ID: {u.id}\nИмя: {name}\nUsername: @{u.username or ''}")

@dp.message(Command("top_staff"))
async def cmd_top_staff(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    ratings = load_ratings()
    staff = []
    for uid, data in ratings.items():
        role = data.get("role", db.get_role(uid))
        if role in ["admin", "moderator"]:
            avg, cnt = await get_user_rating(uid)
            name = data["username"] if data["username"] != str(uid) else await get_user_display_name(uid)
            staff.append((name, avg, cnt, role))
    if not staff:
        await send_new_message(message.chat.id, "🏆 Нет данных.")
        return
    staff.sort(key=lambda x: x[1], reverse=True)
    text = "🏆 ТОП ПЕРСОНАЛА\n"
    for i, (name, avg, cnt, role) in enumerate(staff[:10], 1):
        icon = "👑" if role == "admin" else "🛡️"
        text += f"{i}. {icon} {name} — {avg} ⭐ ({cnt} оценок)\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("donate"))
async def cmd_donate(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if db.is_blacklisted(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Заблокированы!")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 5", callback_data="donate_5"), InlineKeyboardButton(text="⭐ 10", callback_data="donate_10")],
        [InlineKeyboardButton(text="⭐ 25", callback_data="donate_25"), InlineKeyboardButton(text="⭐ 50", callback_data="donate_50")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ])
    await send_new_message(message.chat.id, "Поддержать проект Telegram Stars:", reply_markup=kb)

@dp.callback_query(F.data.startswith("donate_"))
async def process_donate(callback: types.CallbackQuery):
    amount = int(callback.data.split("_")[1])
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id, title="Поддержка ARVION",
        description=f"Донат {amount} Stars", payload="donation", currency="XTR",
        prices=[LabeledPrice(label="Донат", amount=amount)], start_parameter="donation"
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
    await message.answer("❤️ Спасибо за поддержку!")
    log_action(message.from_user.id, message.from_user.username or "user", "ДОНАТ", details=f"Сумма: {message.successful_payment.total_amount} Stars")

# ========== УПРАВЛЕНИЕ МОДЕРАТОРАМИ ==========
@dp.message(Command("new_moderator"))
async def cmd_new_moderator(message: types.Message, state: FSMContext):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только администратор.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="cancel_new_moderator")]])
    await send_new_message(message.chat.id, "Отправьте username или ID пользователя.", reply_markup=kb)
    await state.set_state(AddModerator.waiting_username)

@dp.callback_query(F.data == "cancel_new_moderator")
async def cancel_new_moderator(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.delete()
    await cmd_start(callback.message)

@dp.message(AddModerator.waiting_username)
async def process_moderator_username(message: types.Message, state: FSMContext):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    inp = message.text.strip().lstrip('@')
    try:
        if inp.isdigit():
            user = await bot.get_chat(int(inp))
        else:
            user = await bot.get_chat(inp)
        uid = user.id
        name = user.username or user.first_name
    except:
        await message.answer(f"❌ Пользователь {inp} не найден.")
        return
    if db.get_role(uid) != "user":
        await message.answer(f"⚠️ {name} уже имеет роль.")
        await state.clear()
        return
    db.set_role(uid, "moderator")
    display = await get_user_display_name(uid)
    await message.answer(f"✅ {display} назначен модератором!")
    try:
        await bot.send_message(uid, "🛡️ Вы назначены модератором ARVION Support. Используйте /help.")
    except:
        pass
    log_action(message.from_user.id, message.from_user.username or "admin", "НАЗНАЧЕН МОДЕРАТОР", details=f"Модератор: {uid}")
    await state.clear()

@dp.message(Command("del_moderator"))
async def cmd_del_moderator(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только администратор.")
        return

    args = message.text.split()
    if len(args) == 2:
        target = args[1]
        try:
            if target.isdigit():
                uid = int(target)
            else:
                user = await bot.get_chat(target.lstrip('@'))
                uid = user.id
            role = db.get_role(uid)
            if role not in ["admin", "moderator"]:
                await send_new_message(message.chat.id, f"❌ Пользователь {target} не является модератором или админом.")
                return
            db.set_role(uid, "user")
            await send_new_message(message.chat.id, f"✅ Пользователь {uid} лишён прав.")
            log_action(message.from_user.id, message.from_user.username or "admin", "УДАЛИЛ МОДЕРАТОРА", details=f"User {uid}")
            return
        except Exception as e:
            await send_new_message(message.chat.id, f"❌ Ошибка: {e}. Убедитесь, что пользователь существует.")
            return

    roles = db.get_all_roles()
    staff = [{"user_id": uid, "name": await get_user_display_name(uid), "role": role}
             for uid, role in roles.items() if role in ["admin", "moderator"] and uid != message.from_user.id]
    if not staff:
        await send_new_message(message.chat.id, "📭 Нет других модераторов или администраторов.")
        return

    keyboard = []
    for u in staff:
        keyboard.append([InlineKeyboardButton(text=f"{u['name']} ({u['role']})", callback_data=f"del_mod_{u['user_id']}")])
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del_mod")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await send_new_message(message.chat.id, "📋 Выберите модератора для удаления:", reply_markup=reply_markup)

@dp.callback_query(F.data.startswith("del_mod_"))
async def confirm_del_moderator(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    user_id = int(callback.data.split("_")[2])
    role = db.get_role(user_id)
    if role not in ["admin", "moderator"]:
        await callback.answer("❌ Этот пользователь уже не модератор.", show_alert=True)
        await callback.message.delete()
        return
    db.set_role(user_id, "user")
    await callback.answer("✅ Модератор удалён!")
    await callback.message.edit_text(f"✅ Пользователь {user_id} лишён прав модератора.")
    log_action(callback.from_user.id, callback.from_user.username or "admin", "УДАЛИЛ МОДЕРАТОРА", details=f"User {user_id}")

@dp.callback_query(F.data == "cancel_del_mod")
async def cancel_del_moderator(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()

# ========== ОБРАБОТКА ТИКЕТОВ ==========
def get_ticket_keyboard(ticket_id: str, user_role: str, is_assigned=False, is_view=False):
    if is_view:
        kb = [[InlineKeyboardButton(text="💬 Ответить", callback_data=f"admin_reply_{ticket_id}")],
              [InlineKeyboardButton(text="✅ Закрыть", callback_data=f"close_{ticket_id}")],
              [InlineKeyboardButton(text="📨 Передать", callback_data=f"show_transfer_{ticket_id}")]]
        if user_role == "admin":
            kb.append([InlineKeyboardButton(text="🚫 В чёрный список", callback_data=f"blacklist_user_{ticket_id}")])
        kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"back_to_ticket_{ticket_id}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)
    if is_assigned:
        kb = [[InlineKeyboardButton(text="💬 Ответить", callback_data=f"admin_reply_{ticket_id}")],
              [InlineKeyboardButton(text="✅ Закрыть", callback_data=f"close_{ticket_id}")],
              [InlineKeyboardButton(text="📨 Передать", callback_data=f"show_transfer_{ticket_id}")]]
        if user_role == "admin":
            kb.append([InlineKeyboardButton(text="🚫 В чёрный список", callback_data=f"blacklist_user_{ticket_id}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)
    else:
        kb = [[InlineKeyboardButton(text="💬 Принять", callback_data=f"accept_{ticket_id}")]]
        if user_role == "admin":
            kb.append([InlineKeyboardButton(text="👁️ Посмотреть", callback_data=f"view_{ticket_id}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.callback_query(F.data.startswith("type_"))
async def process_type_selection(callback: types.CallbackQuery, state: FSMContext):
    if db.is_blacklisted(callback.from_user.id):
        await callback.answer("⛔ Заблокированы!", show_alert=True)
        return
    ttype = callback.data.split("_")[1]
    await callback.answer()
    await state.update_data(ticket_type=ttype)
    template = {
        "complaint": "📋 ЖАЛОБА\nDiscord тег:\nTelegram username:\nНарушитель:\nСуть:\nДоказательства:\nДата:",
        "question": "❓ ВОПРОС\nDiscord тег:\nTelegram username:\nТема:\nОписание:",
        "appeal": "⚖️ АПЕЛЛЯЦИЯ\nDiscord тег:\nTelegram username:\nПричина наказания:\nОбъяснение:",
        "suggestion": "💡 ПРЕДЛОЖЕНИЕ\nDiscord тег:\nTelegram username:\nСуть:\nПочему улучшит:",
        "other": "📌 ДРУГОЕ\nDiscord тег:\nTelegram username:\nСуть:\nПодробности:"
    }.get(ttype, "Опишите проблему")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_type_selection")]])
    await send_new_message(callback.message.chat.id, f"{template}\n\n➡️ Заполните и отправьте одним сообщением", reply_markup=kb)
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
    await send_new_message(callback.message.chat.id, "Выберите тип обращения:", reply_markup=kb)

@dp.message(TicketState.waiting_text)
async def process_ticket_message(message: types.Message, state: FSMContext):
    if db.is_blacklisted(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Заблокированы!")
        await state.clear()
        return
    data = await state.get_data()
    ticket_type = data.get("ticket_type", "other")
    text = message.text or message.caption or ""
    if not text.strip():
        await send_new_message(message.chat.id, "❌ Пустое сообщение")
        return
    await delete_previous_bot_message(message.chat.id)
    try:
        await message.delete()
    except:
        pass
    ticket_id = generate_ticket_id()
    uid = message.from_user.id
    username = message.from_user.username or message.from_user.full_name
    db.create_ticket(ticket_id, uid, username, text)
    file_id = file_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
        text += "\n\n📎 [Фото]"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        text += f"\n\n📎 [{message.document.file_name}]"
    db.save_message(ticket_id, "user", text, file_id, file_type)
    if sheet:
        try:
            sheet.append_row([datetime.now().isoformat(), ticket_id, uid, username, ticket_type, text[:500], "open", "", ""])
        except:
            pass
    await send_new_message(message.chat.id, f"✅ Обращение #{ticket_id} принято!")
    sentiment_emoji, sentiment_text = analyze_sentiment(text)
    display_name = await get_user_display_name(uid)
    admin_msg = f"🆔 НОВЫЙ ТИКЕТ #{ticket_id} {sentiment_emoji}[{sentiment_text}]\n👤 {display_name} (ID: {uid})\n\n{text}"
    for aid in db.get_all_roles().keys():
        try:
            if not db.get_assigned_admin(ticket_id):
                if file_id and file_type == "photo":
                    await bot.send_photo(aid, file_id, caption=admin_msg, reply_markup=get_ticket_keyboard(ticket_id, db.get_role(aid), False))
                elif file_id and file_type == "document":
                    await bot.send_document(aid, file_id, caption=admin_msg, reply_markup=get_ticket_keyboard(ticket_id, db.get_role(aid), False))
                else:
                    await bot.send_message(aid, admin_msg, reply_markup=get_ticket_keyboard(ticket_id, db.get_role(aid), False))
        except:
            pass
    await state.clear()

@dp.callback_query(F.data.startswith("accept_"))
async def accept_ticket(callback: types.CallbackQuery):
    await ensure_user_in_ratings(callback.from_user.id)
    ticket_id = callback.data.split("_")[1]
    admin_id = callback.from_user.id
    if db.get_role(admin_id) not in ["admin","moderator"]:
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    if db.get_assigned_admin(ticket_id):
        await callback.answer("❌ Уже принят!", show_alert=True)
        return
    db.assign_ticket(ticket_id, admin_id)
    ticket = db.get_ticket(ticket_id)
    if ticket:
        user_id = ticket[2]
        await bot.send_message(user_id, f"✅ Ваш тикет #{ticket_id} принят в работу.")
    await callback.message.edit_reply_markup(reply_markup=get_ticket_keyboard(ticket_id, db.get_role(admin_id), True))
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
    text = f"🔍 ПРОСМОТР #{ticket_id}\n{status}\n\n{ticket[4]}"
    await callback.message.answer(text, reply_markup=get_ticket_keyboard(ticket_id, "admin", False, True))
    await callback.answer()

@dp.callback_query(F.data.startswith("close_"))
async def close_ticket(callback: types.CallbackQuery):
    await ensure_user_in_ratings(callback.from_user.id)
    role = db.get_role(callback.from_user.id)
    if role not in ["admin","moderator"]:
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    ticket_id = callback.data.split("_")[1]
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    if ticket[5] == "waiting_rating":
        await callback.answer("⏳ Пользователь ещё не оценил", show_alert=True)
        return
    assigned = db.get_assigned_admin(ticket_id)
    if assigned != callback.from_user.id:
        await callback.answer("❌ Тикет принят другим!", show_alert=True)
        return
    db.update_ticket_status(ticket_id, "waiting_rating")
    user_id = ticket[2]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⭐1", callback_data=f"rate_1_{ticket_id}"),
        InlineKeyboardButton(text="⭐2", callback_data=f"rate_2_{ticket_id}"),
        InlineKeyboardButton(text="⭐3", callback_data=f"rate_3_{ticket_id}"),
        InlineKeyboardButton(text="⭐4", callback_data=f"rate_4_{ticket_id}"),
        InlineKeyboardButton(text="⭐5", callback_data=f"rate_5_{ticket_id}")
    ]])
    await bot.send_message(user_id, f"🔒 Тикет #{ticket_id} ожидает закрытия. Оцените работу:", reply_markup=kb)
    await callback.answer("✅ Запрос оценки отправлен")
    await callback.message.edit_reply_markup(reply_markup=None)
    log_action(callback.from_user.id, callback.from_user.username or "admin", "ЗАПРОС ОЦЕНКИ", ticket_id)

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
            try:
                cell = sheet.find(ticket_id, in_column=2)
                if cell:
                    sheet.update_cell(cell.row, 9, rating)
            except:
                pass
        await callback.answer(f"⭐ Спасибо за оценку {rating}!")
        await callback.message.edit_text(f"✅ Тикет #{ticket_id} закрыт. Оценка {rating}⭐")
        for aid in db.get_all_roles().keys():
            try:
                await bot.send_message(aid, f"📊 Пользователь {user_id} оценил тикет #{ticket_id} на {rating}⭐")
            except:
                pass
        log_action(0, "system", "ОЦЕНКА И ЗАКРЫТИЕ", ticket_id, f"Оценка: {rating} от {user_id} для админа {assigned_admin}")
    else:
        await callback.answer("❌ Нельзя оценить самого себя!", show_alert=True)

@dp.callback_query(F.data.startswith("admin_reply_"))
async def admin_reply(callback: types.CallbackQuery, state: FSMContext):
    role = db.get_role(callback.from_user.id)
    if role not in ["admin","moderator"]:
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    ticket_id = callback.data.split("_")[2]
    if db.get_assigned_admin(ticket_id) != callback.from_user.id:
        await callback.answer("❌ Тикет принят другим!", show_alert=True)
        return
    await callback.answer()
    templates = load_templates()
    if templates:
        items = list(templates.items())
        keyboard = []
        for k, v in items[:10]:
            keyboard.append([InlineKeyboardButton(text=k, callback_data=f"template_{k}_{ticket_id}")])
        keyboard.append([InlineKeyboardButton(text="✍️ Свой ответ", callback_data=f"custom_reply_{ticket_id}")])
        await send_new_message(callback.message.chat.id, "Выберите шаблон:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
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
    admin_name = callback.from_user.username or callback.from_user.full_name
    avg, cnt = await get_user_rating(callback.from_user.id)
    rating_text = f" (рейтинг: {avg}/5⭐)" if cnt>0 else ""
    role = db.get_role(callback.from_user.id)
    prefix = "👑 АДМИН " if role=="admin" else ""
    await bot.send_message(user_id, f"{prefix}👨‍💼 {role.capitalize()} {admin_name}{rating_text} ответил:\n\n{text}",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"user_reply_{ticket_id}")]]))
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
    if role not in ["admin","moderator"]:
        return
    data = await state.get_data()
    ticket_id = data.get("reply_ticket_id")
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await send_new_message(message.chat.id, "❌ Тикет не найден!")
        await state.clear()
        return
    if db.get_assigned_admin(ticket_id) != message.from_user.id:
        await send_new_message(message.chat.id, "❌ Тикет принят другим!")
        await state.clear()
        return
    user_id = ticket[2]
    reply_text = message.text or message.caption or ""
    file_id = file_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
        reply_text = message.caption or ""
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        reply_text = message.caption or ""
    db.save_message(ticket_id, "admin", reply_text, file_id, file_type)
    avg, cnt = await get_user_rating(message.from_user.id)
    rating_text = f" (рейтинг: {avg}/5⭐)" if cnt>0 else ""
    admin_name = message.from_user.username or message.from_user.full_name
    role = db.get_role(message.from_user.id)
    prefix = "👑 АДМИН " if role=="admin" else ""
    try:
        if file_id and file_type == "photo":
            await bot.send_photo(user_id, file_id, caption=f"{prefix}👨‍💼 {role.capitalize()} {admin_name}{rating_text} ответил:\n\n{reply_text}",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"user_reply_{ticket_id}")]]))
        elif file_id and file_type == "document":
            await bot.send_document(user_id, file_id, caption=f"{prefix}👨‍💼 {role.capitalize()} {admin_name}{rating_text} ответил:\n\n{reply_text}",
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"user_reply_{ticket_id}")]]))
        else:
            await bot.send_message(user_id, f"{prefix}👨‍💼 {role.capitalize()} {admin_name}{rating_text} ответил:\n\n{reply_text}",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"user_reply_{ticket_id}")]]))
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
    reply_text = message.text or message.caption or ""
    file_id = file_type = None
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
                await bot.send_photo(assigned_admin, file_id, caption=f"💬 Ответ пользователя по #{ticket_id}:\n\n{reply_text}",
                                     reply_markup=get_ticket_keyboard(ticket_id, role, True))
            elif file_id and file_type == "document":
                await bot.send_document(assigned_admin, file_id, caption=f"💬 Ответ пользователя по #{ticket_id}:\n\n{reply_text}",
                                        reply_markup=get_ticket_keyboard(ticket_id, role, True))
            else:
                await bot.send_message(assigned_admin, f"💬 Ответ пользователя по #{ticket_id}:\n\n{reply_text}",
                                       reply_markup=get_ticket_keyboard(ticket_id, role, True))
        except:
            pass
    await delete_previous_bot_message(message.chat.id)
    await send_new_message(message.chat.id, f"✅ Ответ для #{ticket_id} отправлен")
    await state.clear()

# ========== ЧЁРНЫЙ СПИСОК ==========
@dp.callback_query(F.data.startswith("blacklist_user_"))  # из тикета
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
    await bot.send_message(user_id, "⛔ Вы добавлены в чёрный список ARVION Support.")
    log_action(callback.from_user.id, callback.from_user.username or "admin", "ЧЁРНЫЙ СПИСОК", ticket_id, f"User {user_id}")

@dp.message(Command("blacklist"))
async def cmd_blacklist(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    blacklist = db.get_blacklist()
    if not blacklist:
        await send_new_message(message.chat.id, "📭 Чёрный список пуст.")
        return

    items = []
    for row in blacklist:
        uid = row[0]
        reason = row[1]
        name = await get_user_display_name(uid)
        items.append({"user_id": uid, "name": name, "reason": reason})

    per_page = 5
    total_pages = (len(items) + per_page - 1) // per_page

    async def show_blacklist_page(chat_id, page):
        start = page * per_page
        end = start + per_page
        page_items = items[start:end]
        text = f"🚫 ЧЁРНЫЙ СПИСОК\nСтраница {page+1} из {total_pages}\n\n"
        for i, u in enumerate(page_items):
            text += f"{i+1}. {u['name']} (ID: {u['user_id']})\n"
            if u['reason']:
                text += f"   Причина: {u['reason']}\n"
        keyboard = []
        for u in page_items:
            keyboard.append([InlineKeyboardButton(text=f"🔓 Разблокировать {u['name']}", callback_data=f"unblacklist_user_{u['user_id']}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"blacklist_page_{page-1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"blacklist_page_{page+1}"))
        if nav:
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_blacklist")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        if "blacklist_msg_id" in pagination_data.get(chat_id, {}):
            try:
                await bot.edit_message_text(text, chat_id, pagination_data[chat_id]["blacklist_msg_id"], reply_markup=reply_markup)
            except:
                msg = await send_new_message(chat_id, text, reply_markup=reply_markup)
                pagination_data[chat_id]["blacklist_msg_id"] = msg.message_id
        else:
            msg = await send_new_message(chat_id, text, reply_markup=reply_markup)
            if chat_id not in pagination_data:
                pagination_data[chat_id] = {}
            pagination_data[chat_id]["blacklist_msg_id"] = msg.message_id
        pagination_data[chat_id]["blacklist_page"] = page

    await show_blacklist_page(message.chat.id, 0)

@dp.callback_query(F.data.startswith("blacklist_page_"))
async def blacklist_page_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    page = int(callback.data.split("_")[2])
    blacklist = db.get_blacklist()
    if not blacklist:
        await callback.message.edit_text("📭 Чёрный список пуст.")
        await callback.answer()
        return
    items = []
    for row in blacklist:
        uid = row[0]
        reason = row[1]
        name = await get_user_display_name(uid)
        items.append({"user_id": uid, "name": name, "reason": reason})
    per_page = 5
    total_pages = (len(items) + per_page - 1) // per_page
    if page < 0 or page >= total_pages:
        await callback.answer()
        return
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]
    text = f"🚫 ЧЁРНЫЙ СПИСОК\nСтраница {page+1} из {total_pages}\n\n"
    for i, u in enumerate(page_items):
        text += f"{i+1}. {u['name']} (ID: {u['user_id']})\n"
        if u['reason']:
            text += f"   Причина: {u['reason']}\n"
    keyboard = []
    for u in page_items:
        keyboard.append([InlineKeyboardButton(text=f"🔓 Разблокировать {u['name']}", callback_data=f"unblacklist_user_{u['user_id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"blacklist_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"blacklist_page_{page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_blacklist")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(text, reply_markup=reply_markup)
    await callback.answer()

@dp.callback_query(F.data.startswith("unblacklist_user_"))
async def unblacklist_user_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
    user_id = int(callback.data.split("_")[2])
    db.remove_from_blacklist(user_id)
    name = await get_user_display_name(user_id)
    await callback.answer(f"✅ {name} разблокирован!")
    # Обновляем текущее сообщение
    chat_id = callback.message.chat.id
    current_page = pagination_data.get(chat_id, {}).get("blacklist_page", 0)
    try:
        await callback.message.delete()
    except:
        pass
    # Перезагружаем чёрный список
    blacklist = db.get_blacklist()
    if not blacklist:
        await send_new_message(chat_id, "📭 Чёрный список пуст.")
        return
    items = []
    for row in blacklist:
        uid = row[0]
        reason = row[1]
        name = await get_user_display_name(uid)
        items.append({"user_id": uid, "name": name, "reason": reason})
    per_page = 5
    total_pages = (len(items) + per_page - 1) // per_page
    if current_page >= total_pages:
        current_page = total_pages - 1 if total_pages > 0 else 0
    start = current_page * per_page
    end = start + per_page
    page_items = items[start:end]
    text = f"🚫 ЧЁРНЫЙ СПИСОК\nСтраница {current_page+1} из {total_pages}\n\n"
    for i, u in enumerate(page_items):
        text += f"{i+1}. {u['name']} (ID: {u['user_id']})\n"
        if u['reason']:
            text += f"   Причина: {u['reason']}\n"
    keyboard = []
    for u in page_items:
        keyboard.append([InlineKeyboardButton(text=f"🔓 Разблокировать {u['name']}", callback_data=f"unblacklist_user_{u['user_id']}")])
    nav = []
    if current_page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"blacklist_page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"blacklist_page_{current_page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_blacklist")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    msg = await send_new_message(chat_id, text, reply_markup=reply_markup)
    pagination_data[chat_id]["blacklist_msg_id"] = msg.message_id
    pagination_data[chat_id]["blacklist_page"] = current_page

@dp.callback_query(F.data == "close_blacklist")
async def close_blacklist(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()

@dp.message(Command("unblacklist"))
async def cmd_unblacklist(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    args = message.text.split()
    if len(args) == 2 and args[1].isdigit():
        user_id = int(args[1])
        db.remove_from_blacklist(user_id)
        await send_new_message(message.chat.id, f"✅ Пользователь {user_id} удалён из чёрного списка.")
        return
    await send_new_message(message.chat.id, "Используйте /blacklist для просмотра списка с кнопками или /unblacklist ID")

# ========== ОСТАЛЬНЫЕ КОМАНДЫ МОДЕРАТОРА И АДМИНА ==========
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    tickets = db.get_all_tickets()
    total = len(tickets)
    open_t = len([t for t in tickets if t[5]=="open"])
    closed = len([t for t in tickets if t[5]=="closed"])
    today = datetime.now().date()
    today_t = len([t for t in tickets if t[6] and datetime.fromisoformat(t[6]).date()==today])
    await send_new_message(message.chat.id, f"📊 Статистика\nВсего: {total}\n🟡 Открыто: {open_t}\n🔴 Закрыто: {closed}\n📅 За сегодня: {today_t}")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    args = message.text.split(maxsplit=1)
    if len(args)<2:
        await send_new_message(message.chat.id, "❌ /search текст")
        return
    q = args[1].lower()
    tickets = db.get_all_tickets()
    res = [t for t in tickets if q in t[4].lower() or q in t[1].lower()]
    if not res:
        await send_new_message(message.chat.id, f"🔍 По '{q}' ничего нет")
        return
    text = f"🔍 Найдено: {len(res)}\n\n"
    for t in res[:10]:
        created = t[6][:16] if t[6] else "дата неизвестна"
        user_name = await get_user_display_name(t[2])
        text += f"🆔 {t[1]} | {t[5]}\n   {user_name}\n   {t[4][:80]}...\n\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("all_tickets"))
async def cmd_all_tickets(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    tickets = db.get_open_tickets()
    if not tickets:
        await send_new_message(message.chat.id, "📭 Открытых тикетов нет.")
        return
    async def format_open_tickets(page_items, page, total):
        lines = []
        for t in page_items:
            user_name = await get_user_display_name(t[2])
            lines.append(f"🆔 {t[1]}\n👤 {user_name}\n📅 {t[6][:16] if t[6] else 'дата неизвестна'}\n📝 {t[4][:80]}...\n")
        return f"📋 ОТКРЫТЫЕ ТИКЕТЫ (стр. {page} из {total})\n\n" + "\n".join(lines)
    pagination_data[message.chat.id] = {"items": tickets, "page": 0, "message_id": None}
    await show_page(message.chat.id, 0, tickets, 5, format_open_tickets, lambda: None)

@dp.message(Command("clear_tickets"))
async def cmd_clear_tickets(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ ДА", callback_data="confirm_clear_all"), InlineKeyboardButton(text="❌ НЕТ", callback_data="cancel_clear")]])
    await send_new_message(message.chat.id, "⚠️ Удалить ВСЕ тикеты?", reply_markup=kb)

@dp.callback_query(F.data == "confirm_clear_all")
async def confirm_clear_all(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав!", show_alert=True)
        return
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
    await callback.answer()

@dp.callback_query(F.data == "cancel_clear")
async def cancel_clear(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Отменено")
    await callback.answer()

@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    tickets = db.get_all_tickets()
    if not tickets:
        await send_new_message(message.chat.id, "📭 Нет тикетов")
        return
    out = io.StringIO()
    out.write("ID,User ID,Username,Status,Date,Text\n")
    for t in tickets:
        text = t[4].replace(","," ").replace("\n"," ").replace('"',"'")
        out.write(f"{t[1]},{t[2]},{t[3]},{t[5]},{t[6] if t[6] else ''},\"{text}\"\n")
    data = out.getvalue().encode("utf-8")
    await message.answer_document(BufferedInputFile(data, filename="tickets.csv"), caption="📊 Экспорт")
    out.close()
    log_action(message.from_user.id, message.from_user.username or "admin", "ЭКСПОРТ")

@dp.message(Command("log"))
async def cmd_log(message: types.Message):
    try:
        await message.delete()
    except:
        pass
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
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Только для админов!")
        return
    args = message.text.split(maxsplit=1)
    if len(args)<2:
        await send_new_message(message.chat.id, "❌ /announce текст")
        return
    text = args[1]
    users = set()
    for t in db.get_all_tickets():
        if t[2]:
            users.add(t[2])
    for aid in db.get_all_roles().keys():
        users.add(aid)
    if not users:
        users.add(ADMIN_IDS[0])
        await send_new_message(message.chat.id, "⚠️ База пуста, рассылка только админу.")
    success = 0
    for uid in users:
        if db.is_blacklisted(uid):
            continue
        try:
            await bot.send_message(uid, f"📢 МАССОВОЕ УВЕДОМЛЕНИЕ\n\n{text}")
            success += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await send_new_message(message.chat.id, f"✅ Отправлено: {success}")
    log_action(message.from_user.id, message.from_user.username or "admin", "РАССЫЛКА")

@dp.message(Command("templates"))
async def cmd_templates(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    templates = load_templates()
    if not templates:
        await send_new_message(message.chat.id, "📋 Шаблонов нет")
        return
    text = "📋 ШАБЛОНЫ:\n\n" + "\n".join([f"🔹 {k}: {v[:50]}..." for k,v in templates.items()])
    await send_new_message(message.chat.id, text)

@dp.message(Command("add_template"))
async def cmd_add_template(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    args = message.text.split(maxsplit=2)
    if len(args)<3:
        await send_new_message(message.chat.id, "❌ /add_template ключ текст")
        return
    k, v = args[1], args[2]
    templates = load_templates()
    templates[k] = v
    save_templates(templates)
    await send_new_message(message.chat.id, f"✅ Шаблон '{k}' добавлен!")

@dp.message(Command("del_template"))
async def cmd_del_template(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    args = message.text.split()
    if len(args)<2:
        await send_new_message(message.chat.id, "❌ /del_template ключ")
        return
    k = args[1]
    templates = load_templates()
    if k in templates:
        del templates[k]
        save_templates(templates)
        await send_new_message(message.chat.id, f"✅ Шаблон '{k}' удалён!")
    else:
        await send_new_message(message.chat.id, f"❌ Шаблон '{k}' не найден.")

@dp.message(Command("list_templates"))
async def cmd_list_templates(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_admin(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    templates = load_templates()
    if not templates:
        await send_new_message(message.chat.id, "📭 Шаблонов нет.")
        return
    items = list(templates.items())
    async def format_templates_page(page_items, page, total):
        lines = [f"🔹 {k}: {v[:50]}..." for k,v in page_items]
        return f"📋 СПИСОК ШАБЛОНОВ (стр. {page} из {total})\n\n" + "\n".join(lines)
    pagination_data[message.chat.id] = {"items": items, "page": 0, "message_id": None}
    await show_page(message.chat.id, 0, items, 11, format_templates_page, lambda: None)

@dp.message(Command("transfer"))
async def cmd_transfer(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    role = db.get_role(message.from_user.id)
    if role not in ["admin","moderator"]:
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    args = message.text.split()
    if len(args)<2:
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
        if t[8] == message.from_user.id and t[5] in ["open","waiting_rating"]:
            my_ticket = t
            break
    if not my_ticket:
        await send_new_message(message.chat.id, "❌ У вас нет принятых открытых тикетов.")
        return
    ticket_id = my_ticket[1]
    if db.get_role(target_id) not in ["admin","moderator"]:
        await send_new_message(message.chat.id, "❌ Передать можно только админу или модератору.")
        return
    db.assign_ticket(ticket_id, target_id)
    target_name = await get_user_display_name(target_id)
    await bot.send_message(target_id, f"📨 Вам передан тикет #{ticket_id} от @{message.from_user.username or message.from_user.first_name}\n\n{my_ticket[4][:300]}")
    await send_new_message(message.chat.id, f"✅ Тикет #{ticket_id} передан {target_name}")
    log_action(message.from_user.id, message.from_user.username or "admin", "ПЕРЕДАЛ ТИКЕТ", ticket_id, f"Кому: {target_id}")

@dp.message(Command("note"))
async def cmd_note(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    tickets = db.get_all_tickets()
    my_ticket = None
    for t in tickets:
        if t[8] == message.from_user.id and t[5] in ["open","waiting_rating"]:
            my_ticket = t
            break
    if not my_ticket:
        await send_new_message(message.chat.id, "❌ У вас нет принятых открытых тикетов.")
        return
    ticket_id = my_ticket[1]
    args = message.text.split(maxsplit=1)
    if len(args)<2:
        await send_new_message(message.chat.id, "❌ /note текст")
        return
    db.add_note(ticket_id, message.from_user.id, args[1])
    await send_new_message(message.chat.id, "✅ Заметка добавлена.")
    log_action(message.from_user.id, message.from_user.username or "admin", "ДОБАВИЛ ЗАМЕТКУ", ticket_id)

@dp.message(Command("notes"))
async def cmd_notes(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    tickets = db.get_all_tickets()
    my_ticket = None
    for t in tickets:
        if t[8] == message.from_user.id and t[5] in ["open","waiting_rating"]:
            my_ticket = t
            break
    if not my_ticket:
        await send_new_message(message.chat.id, "❌ У вас нет принятых открытых тикетов.")
        return
    ticket_id = my_ticket[1]
    notes = db.get_notes(ticket_id)
    if not notes:
        await send_new_message(message.chat.id, "📭 Заметок нет.")
        return
    text = f"📝 ЗАМЕТКИ ПО ТИКЕТУ {ticket_id}:\n\n"
    for n in notes:
        admin_id, note_text, created_at = n
        admin_name = await get_user_display_name(admin_id)
        created = created_at[:16] if created_at else "дата неизвестна"
        text += f"[{created}] {admin_name}: {note_text}\n"
    await send_new_message(message.chat.id, text)

@dp.message(Command("admin_stats"))
async def cmd_admin_stats(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    if not is_moderator(message.from_user.id):
        await send_new_message(message.chat.id, "⛔ Нет прав!")
        return
    ratings = load_ratings()
    stats = []
    for uid, data in ratings.items():
        role = data.get("role", db.get_role(uid))
        if role not in ["admin","moderator"]:
            continue
        avg, cnt = await get_user_rating(uid)
        name = data["username"] if data["username"] != str(uid) else await get_user_display_name(uid)
        stats.append((name, avg, cnt, role))
    if not stats:
        await send_new_message(message.chat.id, "📊 Нет данных о персонале.")
        return
    stats.sort(key=lambda x: x[1], reverse=True)
    text = "📊 СТАТИСТИКА ПЕРСОНАЛА\n\n"
    for name, avg, cnt, role in stats:
        icon = "👑" if role=="admin" else "🛡️"
        text += f"{icon} {name} — {avg} ⭐ ({cnt} оценок)\n"
    await send_new_message(message.chat.id, text)

# ========== FAQ (ЧАСТЫЕ ВОПРОСЫ) ==========
FAQ_FILE = "faq.json"

def load_faq():
    try:
        with open(FAQ_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        default_faq = {
            "Общие вопросы о проекте": [
                {"q": "Как связаться с администрацией?", "a": "Создайте тикет через /create_ticket. Администраторы увидят его и ответят."},
                {"q": "Где найти правила сервера?", "a": "Правила публикуются в канале @arvion_rules."},
                {"q": "Что делать, если меня забанили?", "a": "Создайте апелляцию через /create_ticket с типом «Апелляция»."}
            ]
        }
        with open(FAQ_FILE, "w", encoding="utf-8") as f:
            json.dump(default_faq, f, ensure_ascii=False, indent=2)
        return default_faq

# Экранирование спецсимволов для Markdown
def escape_markdown(text: str) -> str:
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

@dp.message(Command("faq"))
async def cmd_faq(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    faq = load_faq()
    categories = list(faq.keys())
    if not categories:
        await send_new_message(message.chat.id, "❓ Раздел FAQ временно пуст.")
        return
    
    keyboard = []
    for idx, cat in enumerate(categories):
        keyboard.append([InlineKeyboardButton(text=cat, callback_data=f"faq_cat_{idx}")])
    keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_faq")])
    
    await send_new_message(message.chat.id, "❓ Выберите категорию вопросов:", 
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

@dp.callback_query(F.data.startswith("faq_cat_"))
async def faq_category(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.split("_")[2])
    except:
        await callback.answer("Ошибка")
        return
    
    faq = load_faq()
    categories = list(faq.keys())
    
    if idx >= len(categories):
        await callback.answer("Категория не найдена")
        return
    
    category = categories[idx]
    questions = faq.get(category, [])
    
    if not questions:
        await callback.answer("В этой категории нет вопросов.")
        return
    
    keyboard = []
    for i, item in enumerate(questions):
        # Обрезаем слишком длинные вопросы (Telegram лимит 64 байта для callback_data)
        q_text = item["q"][:50] + "..." if len(item["q"]) > 50 else item["q"]
        keyboard.append([InlineKeyboardButton(text=q_text, callback_data=f"faq_q_{idx}_{i}")])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад к категориям", callback_data="faq_back")])
    keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_faq")])
    
    await callback.message.edit_text(
        f"📂 Категория: {category}\nВыберите вопрос:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("faq_q_"))
async def faq_question(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка формата")
        return
    
    try:
        cat_idx = int(parts[2])
        q_idx = int(parts[3])
    except:
        await callback.answer("Ошибка")
        return
    
    faq = load_faq()
    categories = list(faq.keys())
    
    if cat_idx >= len(categories):
        await callback.answer("Категория не найдена")
        return
    
    category = categories[cat_idx]
    questions = faq.get(category, [])
    
    if q_idx >= len(questions):
        await callback.answer("Вопрос не найден")
        return
    
    item = questions[q_idx]
    
    # Экранируем спецсимволы для безопасного Markdown
    safe_question = escape_markdown(item['q'])
    safe_answer = escape_markdown(item['a'])
    
    text = f"❓ *{safe_question}*\n\n📌 {safe_answer}"
    
    keyboard = [
        [InlineKeyboardButton(text="🔙 К вопросам", callback_data=f"faq_cat_{cat_idx}")],
        [InlineKeyboardButton(text="🏠 Категории", callback_data="faq_back")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="close_faq")]
    ]
    
    try:
        await callback.message.edit_text(
            text, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    except Exception as e:
        # Если Markdown не проходит, отправляем без форматирования
        print(f"Markdown error: {e}")
        await callback.message.edit_text(
            f"❓ {item['q']}\n\n📌 {item['a']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    await callback.answer()

@dp.callback_query(F.data == "faq_back")
async def faq_back(callback: types.CallbackQuery):
    await callback.message.delete()
    await cmd_faq(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "close_faq")
async def close_faq(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    
# ========== HELP БЕЗ ФОРМАТИРОВАНИЯ ==========
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    try:
        await message.delete()
    except:
        pass
    role = db.get_role(message.from_user.id)
    chat_id = message.chat.id

    if not hasattr(cmd_help, "messages"):
        cmd_help.messages = {}

    async def send_help_msg(text: str, key: str):
        if key in cmd_help.messages and chat_id in cmd_help.messages[key]:
            try:
                await bot.delete_message(chat_id, cmd_help.messages[key][chat_id])
            except:
                pass
        msg = await bot.send_message(chat_id, text, parse_mode=None)
        if key not in cmd_help.messages:
            cmd_help.messages[key] = {}
        cmd_help.messages[key][chat_id] = msg.message_id

    base = (
        "ОСНОВНЫЕ КОМАНДЫ\n"
        "/create_ticket – создать обращение\n"
        "/my_tickets – история обращений\n"
        "/get_user – узнать свой ID и username\n"
        "/donate – поддержать проект (Telegram Stars)\n"
        "/faq – частые вопросы\n"
        "/help – эта справка\n\n"
        "Оценка персонала\n"
        "/top_staff – топ персонала по рейтингу\n\n"
        "Анализ тональности\n"
        "Автоматически определяет эмоциональный оклад: 🔴 негатив, 🟢 позитив, 🟡 нейтрально."
    )

    mod = (
        "МОДЕРАТОРСКИЕ КОМАНДЫ\n"
        "Тикеты\n"
        "/all_tickets – список открытых тикетов\n"
        "/transfer @username – передать тикет другому персоналу\n"
        "/stats – общая статистика\n"
        "/search текст – поиск по тикетам\n\n"
        "Заметки\n"
        "/note текст – добавить заметку к текущему тикету\n"
        "/notes – показать заметки\n\n"
        "Шаблоны\n"
        "/templates – список шаблонов\n"
        "/add_template ключ текст – добавить шаблон (админ)\n"
        "/del_template ключ – удалить шаблон (админ)\n"
        "/list_templates – полный список с пагинацией (админ)\n\n"
        "Статистика персонала\n"
        "/admin_stats – рейтинг всего персонала\n"
        "/top_staff – топ персонала\n\n"
        "Кнопки под тикетом: Принять, Ответить, Закрыть, Передать."
    )

    admin = (
        "АДМИНСКИЕ КОМАНДЫ\n"
        "Персонал\n"
        "/new_moderator – назначить модератора\n"
        "/del_moderator – удалить модератора\n\n"
        "Чёрный список\n"
        "/blacklist – показать чёрный список с кнопками для разблокировки\n"
        "/unblacklist ID – разблокировать по ID\n\n"
        "Экспорт и логи\n"
        "/export – выгрузить тикеты в CSV\n"
        "/log – последние действия администраторов\n\n"
        "Рассылка\n"
        "/announce текст – массовая рассылка\n\n"
        "Управление тикетами\n"
        "/clear_tickets – удалить все тикеты\n\n"
        "Дополнительные кнопки под тикетом: Посмотреть, В чёрный список."
    )

    if role == "user":
        await send_help_msg(base, "user")
    elif role == "moderator":
        await send_help_msg(base + "\n\n" + mod, "mod")
    elif role == "admin":
        await send_help_msg(base, "admin_base")
        await send_help_msg(mod + "\n\n" + admin, "admin_staff")

# ========== АВТОМАТИЧЕСКОЕ НАЗНАЧЕНИЕ АДМИНИСТРАТОРА ==========
for uid in ADMIN_IDS:
    if db.get_role(uid) == "user":
        db.set_role(uid, "admin")
        print(f"✅ Администратор {uid} назначен автоматически")

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    asyncio.run(main())