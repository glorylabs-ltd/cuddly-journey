import asyncio
import aiohttp
import time
import os
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
MARZBAN_URL = os.environ.get("MARZBAN_URL", "https://ваша-панель.marzban.url")
MARZBAN_USERNAME = os.environ.get("MARZBAN_USERNAME", "admin")
MARZBAN_PASSWORD = os.environ.get("MARZBAN_PASSWORD", "ваш_пароль")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))

# ИСПРАВЛЕНО: Указываем боту читать HTML-теги (жирный шрифт и тд)
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

    # ИСПРАВЛЕНО: Убрали лимиты, чтобы точно подтягивало всех юзеров
    async def get_all_users(self):
        s, d = await self._req("GET", "/api/users")
        if s == 200 and isinstance(d, dict):
            return d.get("users", [])
        return []

    async def get_inbounds(self):
        s, d = await self._req("GET", "/api/users")
        if s != 200 or not isinstance(d, dict): return []
        inbs = {}
        for u in d.get("users", []):
            for p, tags in u.get("inbounds", {}).items():
                if p not in inbs: inbs[p] = []
                for t in tags:
                    if t not in inbs[p]: inbs[p].append(t)
        return [(p, t) for p, ts in inbs.items() for t in ts]

    # ИСПРАВЛЕНО: Функция возвращает данные созданного юзера (чтобы достать ссылку)
    async def add_user(self, username, data_gb, ip_limit, days, sel_inbs):
        expire = int(time.time()) + (days * 86400) if days > 0 else 0
        limit_b = int(data_gb * 1073741824) if data_gb > 0 else 0
        proxies, inbounds = {}, {}
        for p, t in sel_inbs:
            if p not in proxies:
                proxies[p] = {"flow": ""} if p == "vless" else {}
                inbounds[p] = []
            inbounds[p].append(t)
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
    add_inbounds = State()
    add_params = State()
    find_user = State()
    edit_data = State()
    edit_ip = State()

def admin_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Создать юзера"), KeyboardButton(text="👤 Найти юзера")],
        [KeyboardButton(text="👥 Список юзеров"), KeyboardButton(text="📊 Статистика сервера")]
    ], resize_keyboard=True)

def user_actions_kb(username):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Сброс трафика", callback_data=f"reset_{username}")],
        [InlineKeyboardButton(text="📝 Изменить ГБ", callback_data=f"edata_{username}"),
         InlineKeyboardButton(text="📱 Изменить IP", callback_data=f"eip_{username}")],
        [InlineKeyboardButton(text="🚫 Блок/Разблок", callback_data=f"block_{username}"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{username}")]
    ])

def inbounds_kb(avail, sel):
    kb = [[InlineKeyboardButton(text=f"{'✅' if i in sel else '⬜'} {t} ({p})", callback_data=f"inb_{i}")] for i, (p, t) in enumerate(avail)]
    kb.append([InlineKeyboardButton(text="➡️ Далее", callback_data="conf_inb")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def fmt_size(b): return "0 ГБ" if b == 0 else f"{b/1073741824:.2f} ГБ"
def fmt_date(t): return "Бессрочно" if t == 0 else time.strftime("%d.%m.%Y", time.localtime(t))

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == ADMIN_ID:
        await marzban.init_session()
        await message.answer("👑 <b>Панель Администратора</b>\nУправление VPN-сервером:", reply_markup=admin_menu())
    else:
        await message.answer("🚫 У вас нет доступа к этому боту.")

@dp.message(F.text == "📊 Статистика сервера")
async def stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    s = await marzban.get_system_stats()
    if s:
        await message.answer(
            f"📊 <b>Статистика сервера:</b>\n\n"
            f"👥 Всего юзеров: <b>{s.get('total_user',0)}</b>\n"
            f"🟢 Активных: <b>{s.get('active_user',0)}</b>\n"
            f"⚡️ Онлайн: <b>{s.get('online_user',0)}</b>\n"
            f"🌐 Трафик: <b>{s.get('total_traffic',0)/1073741824:.2f} ГБ</b>"
        )

@dp.message(F.text == "👥 Список юзеров")
async def list_users(message: Message):
    if message.from_user.id != ADMIN_ID: return
    users = await marzban.get_all_users()
    if not users:
        await message.answer("❌ Юзеров в панели нет.")
        return
    
    text = "📋 <b>Список юзеров (последние 30):</b>\n\n"
    for u in users[:30]:
        st = "🟢" if u.get("status") == "active" else "🔴"
        text += f"{st} <code>{u.get('username')}</code> — {fmt_size(u.get('used_traffic',0))}\n"
        
    await message.answer(text)

@dp.message(F.text == "➕ Создать юзера")
async def add_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("Введите <b>username</b> (только англ буквы/цифры):")
    await state.set_state(AdminFSM.add_username)

@dp.message(AdminFSM.add_username)
async def add_name(message: Message, state: FSMContext):
    if not message.text.isalnum():
        await message.answer("❌ Только латинские буквы и цифры!"); return
    inbs = await marzban.get_inbounds()
    if not inbs:
        await message.answer("❌ В панели нет юзеров для копирования инбаундов."); await state.clear(); return
    await state.update_data(username=message.text, inbs=inbs, sel=[])
    await message.answer("Выберите инбаунсы (нажмите на нужные, затем «Далее»):", reply_markup=inbounds_kb(inbs, []))
    await state.set_state(AdminFSM.add_inbounds)

@dp.callback_query(AdminFSM.add_inbounds, F.data.startswith("inb_"))
async def add_inb_cb(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    idx = int(cb.data.split("_")[1])
    sel = d["sel"]
    if idx in sel: sel.remove(idx)
    else: sel.append(idx)
    await state.update_data(sel=sel)
    await cb.message.edit_reply_markup(reply_markup=inbounds_kb(d["inbs"], sel))

@dp.callback_query(AdminFSM.add_inbounds, F.data == "conf_inb")
async def add_inb_conf(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d["sel"]: return await cb.answer("Выберите хотя бы 1!", show_alert=True)
    await cb.message.delete()
    await cb.message.answer(
        "Введите данные через пробел в формате:\n"
        "<code>ГБ IP Дни</code>\n\n"
        "Пример: <code>30 1 30</code> (30ГБ, 1 устройство, 30 дней)\n"
        "Пример безлимита: <code>0 0 0</code>"
    )
    await state.set_state(AdminFSM.add_params)

@dp.message(AdminFSM.add_params)
async def add_params(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("❌ Нужно ровно 3 числа через пробел! Пример: <code>30 1 30</code>"); return
    
    try:
        data_gb = float(parts[0])
        ip_limit = int(parts[1])
        days = int(parts[2])
    except ValueError:
        await message.answer("❌ Ошибка формата! Пример: <code>30 1 30</code>"); return

    d = await state.get_data()
    sel_inbs = [d["inbs"][i] for i in d["sel"]]
    
    # ИСПРАВЛЕНО: Получаем данные созданного юзера
    user_data = await marzban.add_user(d["username"], data_gb, ip_limit, days, sel_inbs)
    
    if user_data:
        sub_url = user_data.get("subscription_url", "")
        if sub_url and not sub_url.startswith("http"):
            sub_url = MARZBAN_URL + sub_url
            
        await message.answer(
            f"✅ Юзер <b>{d['username']}</b> успешно создан!\n\n"
            f"💾 Лимит: {data_gb if data_gb > 0 else '♾'} ГБ\n"
            f"📱 Устройств: {ip_limit if ip_limit > 0 else '♾'}\n"
            f"⏳ Дней: {days if days > 0 else '♾'}\n\n"
            f"🔗 <b>Ссылка на подписку:</b>\n<code>{sub_url}</code>",
            reply_markup=admin_menu()
        )
    else:
        await message.answer("❌ Ошибка создания. Возможно, юзер уже существует.", reply_markup=admin_menu())
    await state.clear()

@dp.message(F.text == "👤 Найти юзера")
async def find_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("Введите username:")
    await state.set_state(AdminFSM.find_user)

@dp.message(AdminFSM.find_user)
async def find_end(message: Message, state: FSMContext):
    username = message.text.strip().lower()
    u = await marzban.get_user(username)
    if not u: 
        await message.answer("❌ Не найден", reply_markup=admin_menu())
        await state.clear()
        return
        
    st = "🟢 Активен" if u.get("status") == "active" else "🔴 Заблокирован"
    lim = fmt_size(u.get("data_limit",0)) if u.get("data_limit",0) > 0 else "Безлимит"
    ip = u.get("device_limit",0) if u.get("device_limit",0) > 0 else "Без лимита"
    
    # НОВОЕ: Получаем ссылку на подписку
    sub_url = u.get("subscription_url", "")
    if sub_url and not sub_url.startswith("http"):
        sub_url = MARZBAN_URL + sub_url
        
    text = (
        f"👤 <b>Профиль клиента:</b> <code>{username}</code>\n\n"
        f"📊 Статус: {st}\n"
        f"🌐 Трафик: {fmt_size(u.get('used_traffic',0))} / {lim}\n"
        f"📱 Лимит IP: {ip}\n"
        f"⏳ Действует до: {fmt_date(u.get('expire',0))}\n\n"
        f"🔗 <b>Ссылка на подписку:</b>\n<code>{sub_url}</code>"
    )
    await message.answer(text, reply_markup=user_actions_kb(username))
    await state.clear()

@dp.callback_query(F.data.startswith("reset_"))
async def cb_reset(cb: CallbackQuery):
    if await marzban.reset_traffic(cb.data.split("_")[1]): 
        await cb.answer("✅ Трафик сброшен!", show_alert=True)

@dp.callback_query(F.data.startswith("block_"))
async def cb_block(cb: CallbackQuery):
    un = cb.data.split("_")[1]
    u = await marzban.get_user(un)
    ns = "disabled" if u.get("status") == "active" else "active"
    if await marzban.modify_user(un, {"status": ns}):
        await cb.answer(f"Статус изменен на: {ns}", show_alert=True)

@dp.callback_query(F.data.startswith("del_"))
async def cb_del(cb: CallbackQuery):
    if await marzban.delete_user(cb.data.split("_")[1]):
        await cb.message.edit_text("🗑 Юзер удален.")

@dp.callback_query(F.data.startswith("edata_"))
async def cb_edata(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.edit_data)
    await state.update_data(u=cb.data.split("_")[1])
    await cb.message.answer("Введите новый лимит ГБ (0 - безлимит):")

@dp.message(AdminFSM.edit_data)
async def fn_edata(message: Message, state: FSMContext):
    try: v = float(message.text)
    except: return await message.answer("❌ Число!")
    d = await state.get_data()
    b = int(v * 1073741824) if v > 0 else 0
    if await marzban.modify_user(d["u"], {"data_limit": b}):
        await message.answer(f"✅ Лимит изменен на {v} ГБ.", reply_markup=admin_menu())
    await state.clear()

@dp.callback_query(F.data.startswith("eip_"))
async def cb_eip(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.edit_ip)
    await state.update_data(u=cb.data.split("_")[1])
    await cb.message.answer("Введите новый лимит IP (0 - без лимита):")

@dp.message(AdminFSM.edit_ip)
async def fn_eip(message: Message, state: FSMContext):
    try: v = int(message.text)
    except: return await message.answer("❌ Целое число!")
    d = await state.get_data()
    if await marzban.modify_user(d["u"], {"device_limit": v}):
        await message.answer(f"✅ Лимит IP изменен на {v}.", reply_markup=admin_menu())
    await state.clear()

# Веб-сервер для Hugging Face
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
    print("🌐 Веб-сервер запущен на порту 7860")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
