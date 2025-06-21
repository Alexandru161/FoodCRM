import asyncio
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command, CommandObject, Filter
from dotenv import load_dotenv
import os
import httpx
import logging

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()

# Состояния для комментирования
class Form(StatesGroup):
    waiting_for_comment = State()

user_current_client = {}

# Проверка разрешенного пользователя
async def is_allowed_user(user_id: int) -> bool:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SUPABASE_URL}/rest/v1/allowed_users",
                             headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                             params={"select": "telegram_id", "telegram_id": f"eq.{user_id}"})
        return len(r.json()) > 0 or user_id == ADMIN_ID

# Получение следующей анкеты
async def get_next_client():
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SUPABASE_URL}/rest/v1/bot",
                             headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                             params={"select": "*", "status": "eq.new", "order": "id"})
        data = r.json()
        return data[0] if data else None

# Команды админа
@router.message(Command("add_client"))
async def cmd_add_client(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split("\n")
    if len(parts) < 4:
        await message.answer("Формат: /add_client\nИмя\nТелефон\nКомпания\nКомментарий")
        return
    _, name, phone, company, *comments = parts
    comments = "\n".join(comments)
    async with httpx.AsyncClient() as client:
        await client.post(f"{SUPABASE_URL}/rest/v1/bot",
                          headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                                   "Content-Type": "application/json"},
                          json={"name": name, "phone": phone, "company": company, "comments": comments})
    await message.answer("Клиент добавлен.")

@router.message(Command("delete_client"))
async def cmd_delete_client(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /delete_client <id>")
        return
    client_id = parts[1]
    await message.answer(f"Подтвердите удаление клиента ID {client_id}, отправив команду /confirm_delete {client_id}")

@router.message(Command("confirm_delete"))
async def cmd_confirm_delete(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    client_id = message.text.split()[1]
    async with httpx.AsyncClient() as client:
        await client.delete(f"{SUPABASE_URL}/rest/v1/bot?id=eq.{client_id}",
                            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
    await message.answer("Клиент удалён.")

@router.message(Command("all_clients"))
async def cmd_all_clients(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SUPABASE_URL}/rest/v1/bot",
                             headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                             params={"select": "id,name,phone,status"})
        clients = r.json()
        text = "\n".join([f"{c['id']}: {c['name']} | {c['phone']} | {c['status']}" for c in clients])
        await message.answer(text or "Нет клиентов.")

@router.message(Command("set_status"))
async def cmd_set_status(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /set_status <id> <new|no_answer|not_interested|interested>")
        return
    client_id, status = parts[1], parts[2]
    if status not in ("new", "no_answer", "not_interested", "interested"):
        await message.answer("Недопустимый статус")
        return
    async with httpx.AsyncClient() as client:
        await client.patch(f"{SUPABASE_URL}/rest/v1/bot?id=eq.{client_id}",
                           headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                           json={"status": status})
    await message.answer("Статус обновлён.")

# Команда /next
@router.message(Command("next"))
async def send_next_client(message: Message, state: FSMContext):
    if not await is_allowed_user(message.from_user.id):
        return
    client = await get_next_client()
    if not client:
        await message.answer("Нет новых анкет.")
        return
    user_current_client[message.from_user.id] = client["id"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Нет ответа", callback_data="no_answer")],
        [InlineKeyboardButton(text="❌ Не интересно", callback_data="not_interested")],
        [InlineKeyboardButton(text="✅ Интересует", callback_data="interested")],
        [InlineKeyboardButton(text="💬 Комментарий", callback_data="comment")]
    ])
    text = f"<b>{client['name']}</b>\n📞 {client['phone']}\n🏢 {client['company']}\n💬 {client.get('comments','')}"
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.in_({"no_answer", "not_interested", "interested"}))
async def handle_status(callback: CallbackQuery, state: FSMContext):
    client_id = user_current_client.get(callback.from_user.id)
    if not client_id:
        await callback.answer("Сначала получите анкету командой /next")
        return

    new_status = callback.data
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/bot?id=eq.{client_id}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            json={"status": new_status}
        )

        if new_status == "interested":
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/bot?id=eq.{client_id}&select=id,name,phone,company,comments",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            )
            if r.status_code == 200 and r.json():
                client_data = r.json()[0]
                full_info = (
                    f"👀 <b>Интересует</b> от {callback.from_user.full_name}\n"
                    f"ID клиента: <code>{client_data['id']}</code>\n"
                    f"<b>{client_data['name']}</b>\n\n"
                    f"📞 {client_data['phone']}\n\n"
                    f"🏢 {client_data['company']}\n\n"
                    f"💬 {client_data.get('comments', '-')}"
                )
                await bot.send_message(ADMIN_ID, full_info)

    # Убираем кнопки у текущего сообщения
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Статус обновлён.")

    # Предлагаем следующую анкету с командой /next
    await callback.message.answer("Для следующей анкеты нажмите /next")




@router.callback_query(F.data == "comment")
async def start_comment(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_for_comment)
    await callback.message.answer("Введите комментарий:")
    await callback.answer()

@router.message(Form.waiting_for_comment)
async def save_comment(message: Message, state: FSMContext):
    client_id = user_current_client.get(message.from_user.id)
    if not client_id:
        await message.answer("Сначала получите анкету командой /next")
        return
    async with httpx.AsyncClient() as client:
        await client.patch(f"{SUPABASE_URL}/rest/v1/bot?id=eq.{client_id}",
                           headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                           json={"comments": message.text})
    await message.answer("Комментарий сохранён.")
    await state.clear()

# Запуск
async def main():
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
