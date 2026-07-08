import asyncio
import aiohttp
import time
import os
import math
import base64
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# ==========================================
# ⚙️ НАСТРОЙКИ БОТА
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН")
MARZBAN_URL = os.environ.get("MARZBAN_URL", "https://vpn-51-38-140-212.sslip.io")
MARZBAN_USERNAME = os.environ.get("MARZBAN_USERNAME", "admin")
MARZBAN_PASSWORD = os.environ.get("MARZBAN_PASSWORD", "ваш_пароль")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

class MarzbanAPI:
    def __init__(self):
        self.base_url = MARZBAN_URL
        self.token = None
        self.session = None

    async def init_session(self):
        self.session = aiohttp.ClientSession()

    async def login(self):
        url = f"{self.base_url}/api/admin/token"
        data = {"username": MARZBAN_USERNAME, "password": MARZBAN_PASSWORD}
        async with self.session.post(url, data=data) as resp:
            if resp.status == 200:
                self.token = (await resp.json()).get("access_token")
                return True
            return False

    async def _req(self, method, endpoint, payload=None):
        if not self.token: await self.login()
        headers = {"Authorization": f"Bearer {self.token}"}
        url = f"{self.base_url}{endpoint}"
        async with self.session.request(method, url, json=payload, headers=headers) as resp:
            if resp.status == 401:
                await self.login()
                headers = {"Authorization": f"Bearer {self.token}"}
                async with self.session.request(method, url, json=payload, headers=headers) as r:
                    return r.status, await r.json() if r.content_type == 'application/json' else {}
            try: return resp.status, await resp.json()
            except: return resp.status, {}

    async def get_system_stats(self):
        s, d = await self._req("GET", "/api/system")
        return d if s == 200 else None

    async def get_user(self, username):
        s, d = await self._req("GET", f"/api/user/{username}")
        return d if s == 200 else None

    async def get_all_users(self):
        s, d = await self._req("GET", "/api/users")
        if s == 200 and isinstance(d, dict):
            users = d.get("users", [])
            users.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            return users
        return []

    async def get_auto_inbounds(self):
        s, d = await self._req("GET", "/api/users")
        if s != 200 or not isinstance(d, dict): return {}, {}
        inbs = {}
        proxies = {}
        for u in d.get("users", []):
            for p, tags in u.get("inbounds", {}).items():
                if p not in inbs:
                    inbs[p] = []
                    if p == "vless": proxies[p] = {"flow": "xtls-rprx-vision"}
                    else: proxies[p] = {}
                for t in tags:
                    if t not in inbs[p]: inbs[p].append(t)
        return proxies, inbs

    async def add_user(self, username, data_gb, ip_limit, days):
        expire = int(time.time()) + (days * 86400) if days > 0 else 0
        limit_b = int(data_gb * 1073741824) if data_gb > 0 else 0
        proxies, inbounds = await self.get_auto_inbounds()
        if not proxies: return None
        
        payload = {
            "username": username, "proxies": proxies, "inbounds": inbounds,
            "expire": expire, "data_limit": limit_b, "data_limit_reset_strategy": "no_reset",
            "device_limit": ip_limit
        }
        s, d = await self._req("POST", "/api/user", payload)
        return d if s == 200 else None

    async def modify_user(self, username, payload):
        s, d = await self._req("PUT", f"/api/user/{username}", payload)
        return d if s == 200 else None

    async def delete_user(self, username):
        s, _ = await self._req("DELETE", f"/api/user/{username}")
        return s == 200

    async def reset_traffic(self, username):
        s, _ = await self._req("POST", f"/api/user/{username}/reset")
        return s == 204

marzban = MarzbanAPI()

class AdminFSM(StatesGroup):
    add_username = State()
    add_data = State()
    add_ip = State()
    add_days = State()
    find_user = State()
    edit_data = State()
    edit_ip = State()

def main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Создать"), KeyboardButton(text="👥 Список")],
        [KeyboardButton(text="🔍 Найти"), KeyboardButton(text="📊 Сервер")]
    ], resize_keyboard=True)

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def traffic_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10 ГБ", callback_data="d_10"), 
         InlineKeyboardButton(text="30 ГБ", callback_data="d_30"),
         InlineKeyboardButton(text="50 ГБ", callback_data="d_50")],
        [InlineKeyboardButton(text="100 ГБ", callback_data="d_100"), 
         InlineKeyboardButton(text="♾ Безлимит", callback_data="d_0")],
        [InlineKeyboardButton(text="✍️ Ввести своё", callback_data="d_custom")]
    ])

def ip_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 устр-во", callback_data="ip_1"), 
         InlineKeyboardButton(text="2 устр-ва", callback_data="ip_2"),
         InlineKeyboardButton(text="3 устр-ва", callback_data="ip_3")],
        [InlineKeyboardButton(text="♾ Без лимита", callback_data="ip_0")],
        [InlineKeyboardButton(text="✍️ Ввести своё", callback_data="ip_custom")]
    ])

def days_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="30 дней", callback_data="dd_30"), 
         InlineKeyboardButton(text="90 дней", callback_data="dd_90"),
         InlineKeyboardButton(text="180 дней", callback_data="dd_180")],
        [InlineKeyboardButton(text="♾ Бессрочно", callback_data="dd_0")],
        [InlineKeyboardButton(text="✍️ Ввести своё", callback_data="dd_custom")]
    ])

def user_actions_kb(username):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Ссылки", callback_data=f"links_{username}")],
        [InlineKeyboardButton(text="🔄 Сброс трафика", callback_data=f"reset_{username}"),
         InlineKeyboardButton(text="🚫 Блок/Разблок", callback_data=f"block_{username}")],
        [InlineKeyboardButton(text="📝 Изменить ГБ", callback_data=f"edata_{username}"),
         InlineKeyboardButton(text="📱 Изменить IP", callback_data=f"eip_{username}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{username}")]
    ])

def links_kb(username, sub_url):
    happ_url = f"happ://add/{base64.b64encode(sub_url.encode()).decode()}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Открыть в Happ", url=happ_url)],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_{username}")]
    ])

def fmt_size(b):
    if b == 0: return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = int(math.floor(math.log(b, 1024)))
    p = math.pow(1024, i)
    s = round(b / p, 2)
    return f"{s} {units[i]}"

def fmt_date(t):
    if t == 0: return "Бессрочно"
    return time.strftime("%d.%m.%Y", time.localtime(t))

def traffic_bar(used, limit):
    if limit == 0: return "▰▰▰▰▰▰▰▰▰▰ ♾ Безлимит"
    percent = (used / limit) * 100
    if percent > 100: percent = 100
    blocks = int(percent / 10)
    bar = "▰" * blocks + "▱" * (10 - blocks)
    return f"{bar} <b>{percent:.1f}%</b>"

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == ADMIN_ID:
        await marzban.init_session()
        await message.answer(
            "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            "🔐 <b>MARZBAN CONTROL HUB</b>\n"
            "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            "Система управления VPN готова к работе.\n"
            "Выберите раздел в меню ниже 👇",
            reply_markup=main_menu_kb()
        )
    else:
        await message.answer("🚫 <b>Access Denied</b>")

# ИСПРАВЛЕНО: Добавлена очистка состояния (state.clear()) во все кнопки меню
@dp.message(F.text == "📊 Сервер")
async def stats(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID: return
    s = await marzban.get_system_stats()
    if s:
        await message.answer(
            "📊 <b>Состояние сервера</b>\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            f"👥 <b>Пользователей:</b> {s.get('total_user',0)}\n"
            f"🟢 <b>Активных:</b> {s.get('active_user',0)}\n"
            f"⚡️ <b>Онлайн:</b> {s.get('online_user',0)}\n\n"
            "⚙️ <b>Нагрузка:</b>\n"
            f"💾 RAM: <b>{s.get('mem_used',0)/1048576:.2f} MB</b>\n"
            f"🖥 CPU: <b>{s.get('cpu_usage',0)}%</b>\n\n"
            "🌐 <b>Трафик:</b>\n"
            f"Всего отдано: <b>{fmt_size(s.get('total_traffic',0))}</b>"
        )

@dp.message(F.text == "👥 Список")
async def list_users(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID: return
    users = await marzban.get_all_users()
    if not users:
        await message.answer("ℹ️ База клиентов пуста.")
        return
    await show_page(message, users, 0)

@dp.callback_query(F.data.startswith("page_"))
async def pages_cb(cb: CallbackQuery):
    page = int(cb.data.split("_")[1])
    users = await marzban.get_all_users()
    await cb.message.delete()
    await show_page(cb, users, page, is_cb=True)

async def show_page(msg_or_cb, users, page, is_cb=False):
    per_page = 5
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]
    total_pages = math.ceil(len(users) / per_page)

    text = f"📋 <b>База клиентов</b> (Стр. {page+1}/{total_pages})\n━━━━━━━━━━━━━━━━━\n\n"
    kb = []
    for u in page_users:
        st = "🟢" if u.get("status") == "active" else "🔴"
        un = u.get('username')
        text += f"{st} <code>{un}</code> — {fmt_size(u.get('used_traffic',0))}\n"
        kb.append([InlineKeyboardButton(text=f"{st} {un}", callback_data=f"view_{un}")])

    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"page_{page-1}"))
    if end < len(users): nav.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"page_{page+1}"))
    if nav: kb.append(nav)
    
    if is_cb: await msg_or_cb.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else: await msg_or_cb.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("view_"))
async def view_user_from_list(cb: CallbackQuery):
    un = cb.data.split("view_")[1]
    u = await marzban.get_user(un)
    if not u: return await cb.answer("Не найден", show_alert=True)
    await cb.message.delete()
    await cb.message.answer(await format_user_text(u), reply_markup=user_actions_kb(un))

@dp.message(F.text == "➕ Создать")
async def add_start(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👤 Введите <b>username</b> нового клиента:", reply_markup=cancel_kb())
    await state.set_state(AdminFSM.add_username)

@dp.message(AdminFSM.add_username)
async def add_name(message: Message, state: FSMContext):
    if not message.text.isalnum():
        await message.answer("❌ Только латинские буквы и цифры!"); return
    await state.update_data(username=message.text)
    await message.answer("💾 Выберите лимит трафика:", reply_markup=traffic_kb())
    await state.set_state(AdminFSM.add_data)

@dp.callback_query(AdminFSM.add_data, F.data.startswith("d_"))
async def add_data_cb(cb: CallbackQuery, state: FSMContext):
    val = cb.data.split("_")[1]
    if val == "custom":
        await cb.message.answer("✍️ Введите лимит ГБ (число):")
        return
    await state.update_data(data=float(val))
    await cb.message.edit_text("📱 Выберите лимит устройств (IP):", reply_markup=ip_kb())
    await state.set_state(AdminFSM.add_ip)

@dp.message(AdminFSM.add_data)
async def add_data_manual(message: Message, state: FSMContext):
    try: v = float(message.text)
    except: return await message.answer("❌ Только число!")
    await state.update_data(data=v)
    await message.answer("📱 Выберите лимит устройств (IP):", reply_markup=ip_kb())
    await state.set_state(AdminFSM.add_ip)

@dp.callback_query(AdminFSM.add_ip, F.data.startswith("ip_"))
async def add_ip_cb(cb: CallbackQuery, state: FSMContext):
    val = cb.data.split("_")[1]
    if val == "custom":
        await cb.message.answer("✍️ Введите лимит IP (целое число):")
        return
    await state.update_data(ip=int(val))
    await cb.message.edit_text("⏳ Выберите срок подписки:", reply_markup=days_kb())
    await state.set_state(AdminFSM.add_days)

@dp.message(AdminFSM.add_ip)
async def add_ip_manual(message: Message, state: FSMContext):
    try: v = int(message.text)
    except: return await message.answer("❌ Только целое число!")
    await state.update_data(ip=v)
    await message.answer("⏳ Выберите срок подписки:", reply_markup=days_kb())
    await state.set_state(AdminFSM.add_days)

@dp.callback_query(AdminFSM.add_days, F.data.startswith("dd_"))
async def add_days_cb(cb: CallbackQuery, state: FSMContext):
    val = cb.data.split("_")[1]
    if val == "custom":
        await cb.message.answer("✍️ Введите количество дней (целое число):")
        return
    await state.update_data(days=int(val))
    await finish_user_creation(cb.message, state)

@dp.message(AdminFSM.add_days)
async def add_days_manual(message: Message, state: FSMContext):
    try: v = int(message.text)
    except: return await message.answer("❌ Только целое число!")
    await state.update_data(days=v)
    await finish_user_creation(message, state)

async def finish_user_creation(message, state):
    d = await state.get_data()
    msg = await message.answer("⏳ <i>Создаю конфигурацию...</i>")
    user_data = await marzban.add_user(d["username"], d["data"], d["ip"], d["days"])
    await msg.delete()
    
    if user_data:
        sub_url = user_data.get("subscription_url", "")
        if sub_url and not sub_url.startswith("http"): sub_url = MARZBAN_URL + sub_url
        await message.answer(
            "✅ <b>Клиент успешно создан</b>\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Логин:</b> <code>{d['username']}</code>\n"
            f"💾 <b>Трафик:</b> {d['data'] if d['data'] > 0 else '♾'} ГБ\n"
            f"📱 <b>Устройств:</b> {d['ip'] if d['ip'] > 0 else '♾'}\n"
            f"⏳ <b>Срок:</b> {d['days'] if d['days'] > 0 else '♾'} дней\n\n"
            f"🔗 <b>Ссылка для Happ:</b>\n<code>{sub_url}</code>",
            reply_markup=main_menu_kb()
        )
    else:
        await message.answer("❌ Ошибка создания.", reply_markup=main_menu_kb())
    await state.clear()

@dp.message(F.text == "🔍 Найти")
async def find_start(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID: return
    await message.answer("🔍 Введите <b>username</b> клиента:", reply_markup=cancel_kb())
    await state.set_state(AdminFSM.find_user)

@dp.message(AdminFSM.find_user)
async def find_end(message: Message, state: FSMContext):
    username = message.text.strip().lower()
    u = await marzban.get_user(username)
    if not u: 
        await message.answer("❌ Клиент не найден.", reply_markup=main_menu_kb())
        await state.clear()
        return
    await message.answer(await format_user_text(u), reply_markup=user_actions_kb(username))
    await state.clear()

async def format_user_text(u):
    st = "🟢 Активен" if u.get("status") == "active" else "🔴 Заблокирован"
    lim = u.get("data_limit",0)
    ip = u.get("device_limit",0) if u.get("device_limit",0) > 0 else "♾"
    used = u.get("used_traffic",0)
    expire = u.get("expire",0)
    sub_url = u.get("subscription_url", "")
    if sub_url and not sub_url.startswith("http"): sub_url = MARZBAN_URL + sub_url
    
    return (
        f"👤 <b>Профиль клиента</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"Логин: <code>{u.get('username')}</code>\n"
        f"Статус: {st}\n\n"
        f"🌐 <b>Трафик:</b>\n"
        f"{fmt_size(used)} / {fmt_size(lim) if lim > 0 else '♾'}\n"
        f"{traffic_bar(used, lim)}\n\n"
        f"📱 <b>Лимит IP:</b> {ip}\n"
        f"⏳ <b>Действует до:</b> {fmt_date(expire)}\n\n"
        f"🔗 <b>Ссылка:</b>\n<code>{sub_url}</code>"
    )

@dp.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.delete()
    await cb.message.answer("❌ Операция отменена.", reply_markup=main_menu_kb())

@dp.callback_query(F.data.startswith("links_"))
async def cb_links(cb: CallbackQuery):
    un = cb.data.split("links_")[1]
    u = await marzban.get_user(un)
    sub_url = u.get("subscription_url", "")
    if sub_url and not sub_url.startswith("http"): sub_url = MARZBAN_URL + sub_url
    await cb.message.edit_text(
        f"🔗 <b>Ссылки для {un}</b>\n\n"
        f"1️⃣ <b>Стандартная:</b>\n<code>{sub_url}</code>\n\n"
        f"2️⃣ Нажмите кнопку ниже, чтобы открыть приложение Happ и оно само всё импортирует.",
        reply_markup=links_kb(un, sub_url)
    )

@dp.callback_query(F.data.startswith("back_"))
async def cb_back(cb: CallbackQuery):
    un = cb.data.split("back_")[1]
    u = await marzban.get_user(un)
    await cb.message.edit_text(await format_user_text(u), reply_markup=user_actions_kb(un))

@dp.callback_query(F.data.startswith("reset_"))
async def cb_reset(cb: CallbackQuery):
    un = cb.data.split("reset_")[1]
    if await marzban.reset_traffic(un): await cb.answer("✅ Трафик сброшен!", show_alert=True)

@dp.callback_query(F.data.startswith("block_"))
async def cb_block(cb: CallbackQuery):
    un = cb.data.split("block_")[1]
    u = await marzban.get_user(un)
    ns = "disabled" if u.get("status") == "active" else "active"
    if await marzban.modify_user(un, {"status": ns}):
        await cb.answer(f"✅ Статус: {ns}", show_alert=True)
        u = await marzban.get_user(un)
        await cb.message.edit_text(await format_user_text(u), reply_markup=user_actions_kb(un))

@dp.callback_query(F.data.startswith("del_"))
async def cb_del(cb: CallbackQuery):
    un = cb.data.split("del_")[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Да, удалить", callback_data=f"confdel_{un}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"back_{un}")]
    ])
    await cb.message.answer(f"⚠️ Удалить <code>{un}</code>?", reply_markup=kb)

@dp.callback_query(F.data.startswith("confdel_"))
async def cb_conf_del(cb: CallbackQuery):
    un = cb.data.split("confdel_")[1]
    if await marzban.delete_user(un):
        await cb.message.edit_text(f"🗑 Клиент <code>{un}</code> удален.")

@dp.callback_query(F.data.startswith("edata_"))
async def cb_edata(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.edit_data)
    await state.update_data(u=cb.data.split("edata_")[1])
    await cb.message.answer("Введите новый лимит ГБ (0 - безлимит):", reply_markup=cancel_kb())

@dp.message(AdminFSM.edit_data)
async def fn_edata(message: Message, state: FSMContext):
    try: v = float(message.text)
    except: return await message.answer("❌ Только число!")
    d = await state.get_data()
    b = int(v * 1073741824) if v > 0 else 0
    if await marzban.modify_user(d["u"], {"data_limit": b}):
        await message.answer(f"✅ Лимит изменен на <b>{v} ГБ</b>.", reply_markup=main_menu_kb())
    await state.clear()

@dp.callback_query(F.data.startswith("eip_"))
async def cb_eip(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.edit_ip)
    await state.update_data(u=cb.data.split("eip_")[1])
    await cb.message.answer("Введите новый лимит IP (0 - без лимита):", reply_markup=cancel_kb())

@dp.message(AdminFSM.edit_ip)
async def fn_eip(message: Message, state: FSMContext):
    try: v = int(message.text)
    except: return await message.answer("❌ Только целое число!")
    d = await state.get_data()
    if await marzban.modify_user(d["u"], {"device_limit": v}):
        await message.answer(f"✅ Лимит IP изменен на <b>{v}</b>.", reply_markup=main_menu_kb())
    await state.clear()

async def handle_health(request):
    return web.Response(text="Bot is running!")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 7860)
    await site.start()
    print("🌐 Сервер запущен на порту 7860")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
