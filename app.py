import os, json, time, hmac, hashlib, asyncio, jwt, csv, io
from datetime import datetime, timedelta
from urllib.parse import unquote, parse_qsl
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
from aiogram import Bot, Dispatcher, types, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
SECRET_KEY = os.getenv("SECRET_KEY", "change_this_secret_key")
DATABASE_PATH = os.getenv("DATABASE_PATH", "app.db")
APP_NAME = os.getenv("APP_NAME", "Premium Earning Bot")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@AdminSupport")
PORT = int(os.getenv("PORT", "8000"))

app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

bot = None
dp = None
router = Router()
if BOT_TOKEN and ":" in BOT_TOKEN and "ABC" not in BOT_TOKEN:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    dp = Dispatcher()
    dp.include_router(router)

RATE = {}
async def rate_limiter(request: Request):
    ip = request.client.host if request.client else "local"
    now = time.time()
    RATE.setdefault(ip, [])
    RATE[ip] = [t for t in RATE[ip] if now - t < 1]
    if len(RATE[ip]) > 25:
        raise HTTPException(429, "Too many requests")
    RATE[ip].append(now)

async def db_query(query: str, args: tuple = (), one: bool = False):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, args) as cur:
            rows = await cur.fetchall()
            if one:
                return dict(rows[0]) if rows else None
            return [dict(r) for r in rows]

async def db_execute(query: str, args: tuple = ()):  
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(query, args) as cur:
            await db.commit()
            return cur.lastrowid

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript('''
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER UNIQUE, username TEXT, first_name TEXT, last_name TEXT, photo_url TEXT, lang TEXT DEFAULT 'en', country TEXT DEFAULT '', ref_by INTEGER, role TEXT DEFAULT 'user', is_banned INTEGER DEFAULT 0, is_verified INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS balances (user_id INTEGER PRIMARY KEY, usdt REAL DEFAULT 0, xp REAL DEFAULT 0, keys INTEGER DEFAULT 0, tickets INTEGER DEFAULT 0, streak INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS payment_methods (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, network TEXT, wallet TEXT, qr_url TEXT, status TEXT DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, image_url TEXT, description TEXT, price REAL DEFAULT 0, duration_hours INTEGER DEFAULT 24, category TEXT DEFAULT 'Investment', reward_type TEXT DEFAULT 'USDT', hourly_reward REAL DEFAULT 0, daily_reward REAL DEFAULT 0, total_return REAL DEFAULT 0, total_spins INTEGER DEFAULT 0, instant_bonus REAL DEFAULT 0, stock INTEGER DEFAULT -1, status TEXT DEFAULT 'active', terms TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS product_purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER, start_time DATETIME, end_time DATETIME, status TEXT DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS product_rewards (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, purchase_id INTEGER, reward_time DATETIME, amount REAL, type TEXT);
        CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, type TEXT, link TEXT, reward_type TEXT DEFAULT 'XP', reward_amount REAL DEFAULT 0, cooldown_hours INTEGER DEFAULT 24, daily_limit INTEGER DEFAULT 1, status TEXT DEFAULT 'active', country_target TEXT DEFAULT '', level_target TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS task_claims (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task_id INTEGER, claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS referrals (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referee_id INTEGER, level INTEGER, reward_paid REAL DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS referral_milestones (id INTEGER PRIMARY KEY AUTOINCREMENT, req_refs INTEGER UNIQUE, reward_type TEXT DEFAULT 'USDT', reward_amount REAL DEFAULT 0, status TEXT DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS milestone_claims (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, milestone_id INTEGER, claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, txid TEXT UNIQUE, amount REAL, method TEXT, status TEXT DEFAULT 'pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, address TEXT, amount REAL, fee REAL DEFAULT 0, network TEXT DEFAULT 'TRC20', status TEXT DEFAULT 'pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS redeem_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, reward_type TEXT DEFAULT 'USDT', reward_amount REAL DEFAULT 0, max_uses INTEGER DEFAULT 1, current_uses INTEGER DEFAULT 0, expires_at DATETIME, status TEXT DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS redeem_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, code_id INTEGER, claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS store_items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, image_url TEXT, price_type TEXT DEFAULT 'USDT', price_amount REAL DEFAULT 0, reward_type TEXT DEFAULT 'XP', reward_amount REAL DEFAULT 0, stock INTEGER DEFAULT -1, status TEXT DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS store_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, item_id INTEGER, purchased_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS seasons (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, banner_url TEXT, reward_pool TEXT, start_time DATETIME, end_time DATETIME, status TEXT DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS season_users (id INTEGER PRIMARY KEY AUTOINCREMENT, season_id INTEGER, user_id INTEGER, xp REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message TEXT, is_read INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS live_activity (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT, type TEXT DEFAULT 'real', created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS broadcasts (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, amount REAL, currency TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS login_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ip TEXT, device TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS admin_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, admin TEXT, action TEXT, target TEXT, details TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        ''')
        defaults = {
            'app_name': APP_NAME, 'support_username': SUPPORT_USERNAME, 'deposit_min': '1', 'withdraw_min': '10', 'withdraw_fee': '1',
            'deposit_wallet': 'TRC20_WALLET_ADDRESS_HERE', 'ref_com_1': '10', 'ref_com_2': '5', 'ref_com_3': '2',
            'fake_activity': '1', 'maintenance': '0', 'default_lang': 'en', 'ticket_timer_hours': '1', 'daily_checkin_reward': '10', 'theme_color': '#D4AF37'
        }
        for k, v in defaults.items():
            await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))
        await db.execute("INSERT OR IGNORE INTO payment_methods (id,name,network,wallet,status) VALUES (1,'USDT TRC20','TRC20','TRC20_WALLET_ADDRESS_HERE','active')")
        await db.execute("INSERT OR IGNORE INTO payment_methods (id,name,network,wallet,status) VALUES (2,'USDT BEP20','BEP20','BEP20_WALLET_ADDRESS_HERE','active')")
        await db.execute("INSERT OR IGNORE INTO payment_methods (id,name,network,wallet,status) VALUES (3,'Binance Pay','Binance Pay','BINANCE_PAY_ID','active')")
        await db.execute("INSERT OR IGNORE INTO products (id,name,image_url,description,price,duration_hours,category,reward_type,hourly_reward,daily_reward,total_return,instant_bonus,stock,status,terms) VALUES (1,'Starter Auto Node','','Collect hourly USDT automatically.',10,24,'Auto-Collect','USDT',0.1,2.4,2.4,0,-1,'active','Rewards are calculated hourly.')")
        await db.execute("INSERT OR IGNORE INTO tasks (id,title,description,type,link,reward_type,reward_amount,cooldown_hours,status) VALUES (1,'Daily Check-in','Claim daily XP reward','daily','', 'XP', 20, 24, 'active')")
        await db.execute("INSERT OR IGNORE INTO redeem_codes (code,reward_type,reward_amount,max_uses,expires_at,status) VALUES ('WELCOME','USDT',1,100,'2099-01-01 00:00:00','active')")
        await db.execute("INSERT OR IGNORE INTO referral_milestones (id,req_refs,reward_type,reward_amount,status) VALUES (1,5,'USDT',2,'active')")
        await db.execute("INSERT OR IGNORE INTO referral_milestones (id,req_refs,reward_type,reward_amount,status) VALUES (2,10,'USDT',5,'active')")
        await db.execute("INSERT OR IGNORE INTO seasons (id,title,banner_url,reward_pool,start_time,end_time,status) VALUES (1,'Genesis Season','','Prize Pool 500 USDT',datetime('now'),datetime('now','+30 days'),'active')")
        await db.commit()

async def settings_dict():
    rows = await db_query("SELECT key,value FROM settings")
    return {r['key']: r['value'] for r in rows}

async def credit(user_id: int, rtype: str, amount: float, reason: str):
    rtype = (rtype or 'USDT').upper()
    if rtype == 'USDT': field = 'usdt'
    elif rtype == 'XP': field = 'xp'
    elif rtype in ['KEY','KEYS']: field = 'keys'
    elif rtype in ['TICKET','TICKETS']: field = 'tickets'
    else: field = 'xp'
    await db_execute(f"UPDATE balances SET {field} = {field} + ? WHERE user_id=?", (amount, user_id))
    await db_execute("INSERT INTO transactions (user_id,type,amount,currency) VALUES (?,?,?,?)", (user_id, reason, amount, rtype))

async def debit(user_id: int, rtype: str, amount: float):
    rtype = (rtype or 'USDT').upper()
    field = {'USDT':'usdt','XP':'xp','KEY':'keys','KEYS':'keys','TICKET':'tickets','TICKETS':'tickets'}.get(rtype, 'usdt')
    bal = await db_query(f"SELECT {field} as b FROM balances WHERE user_id=?", (user_id,), one=True)
    if not bal or float(bal['b'] or 0) < amount:
        raise HTTPException(400, f"Insufficient {rtype}")
    await db_execute(f"UPDATE balances SET {field} = {field} - ? WHERE user_id=?", (amount, user_id))

async def notify(user_id: int, msg: str):
    await db_execute("INSERT INTO notifications (user_id,message) VALUES (?,?)", (user_id, msg))
    if bot:
        try:
            u = await db_query("SELECT tg_id FROM users WHERE id=?", (user_id,), one=True)
            if u: await bot.send_message(u['tg_id'], msg)
        except Exception: pass

def verify_telegram_data(init_data: str):
    if not BOT_TOKEN or not init_data: return None
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        hash_val = data.pop('hash', None)
        if not hash_val: return None
        check = '\n'.join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(calc, hash_val):
            return json.loads(data.get('user', '{}'))
    except Exception:
        return None
    return None

def create_jwt(user_id: int, role='user'):
    return jwt.encode({'user_id': user_id, 'role': role, 'exp': datetime.utcnow()+timedelta(days=7)}, SECRET_KEY, algorithm='HS256')

async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401, 'Unauthorized')
    try:
        p = jwt.decode(authorization.split(' ')[1], SECRET_KEY, algorithms=['HS256'])
        if p.get('role') == 'admin':
            return {'id': 0, 'role': 'admin', 'first_name': 'Admin', 'is_banned': 0}
        u = await db_query("SELECT * FROM users WHERE id=?", (p['user_id'],), one=True)
        if not u: raise HTTPException(404, 'User not found')
        if u['is_banned']: raise HTTPException(403, 'Account banned')
        return u
    except jwt.ExpiredSignatureError: raise HTTPException(401, 'Token expired')
    except jwt.InvalidTokenError: raise HTTPException(401, 'Invalid token')

async def get_admin_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401, 'Unauthorized')
    try:
        p = jwt.decode(authorization.split(' ')[1], SECRET_KEY, algorithms=['HS256'])
        if p.get('role') != 'admin': raise HTTPException(403, 'Admin only')
        return {'username': ADMIN_USERNAME}
    except Exception:
        raise HTTPException(401, 'Invalid admin token')

async def admin_log(action, target='', details=''):
    await db_execute("INSERT INTO admin_logs (admin,action,target,details) VALUES (?,?,?,?)", (ADMIN_USERNAME, action, str(target), str(details)))

if bot:
    @router.message(Command('start'))
    async def start_handler(message: types.Message):
        tg = message.from_user
        arg = message.text.split()[1] if len(message.text.split()) > 1 else None
        user = await db_query("SELECT id FROM users WHERE tg_id=?", (tg.id,), one=True)
        if not user:
            ref_by = int(arg) if arg and arg.isdigit() else None
            uid = await db_execute("INSERT INTO users (tg_id,username,first_name,last_name,lang,ref_by) VALUES (?,?,?,?,?,?)", (tg.id, tg.username or '', tg.first_name or 'User', tg.last_name or '', tg.language_code or 'en', ref_by))
            await db_execute("INSERT INTO balances (user_id) VALUES (?)", (uid,))
            if ref_by:
                await db_execute("INSERT INTO referrals (referrer_id,referee_id,level) VALUES (?,?,1)", (ref_by, uid))
                r2 = await db_query("SELECT ref_by FROM users WHERE id=?", (ref_by,), one=True)
                if r2 and r2.get('ref_by'):
                    await db_execute("INSERT INTO referrals (referrer_id,referee_id,level) VALUES (?,?,2)", (r2['ref_by'], uid))
                    r3 = await db_query("SELECT ref_by FROM users WHERE id=?", (r2['ref_by'],), one=True)
                    if r3 and r3.get('ref_by'):
                        await db_execute("INSERT INTO referrals (referrer_id,referee_id,level) VALUES (?,?,3)", (r3['ref_by'], uid))
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🚀 Open App', web_app=WebAppInfo(url=WEBAPP_URL))]])
        await message.reply(f"Welcome to {APP_NAME}!", reply_markup=kb)

    @router.message(Command('support'))
    async def support_handler(message: types.Message):
        await message.reply(f"Support: {SUPPORT_USERNAME}")

async def background_loop():
    while True:
        try:
            now = datetime.utcnow()
            rows = await db_query("SELECT pp.*,p.name,p.category,p.reward_type,p.hourly_reward,p.total_return FROM product_purchases pp JOIN products p ON pp.product_id=p.id WHERE pp.status='active'")
            for p in rows:
                end = datetime.strptime(p['end_time'], '%Y-%m-%d %H:%M:%S')
                if now >= end:
                    await db_execute("UPDATE product_purchases SET status='expired' WHERE id=?", (p['id'],))
                    await notify(p['user_id'], f"Your pack {p['name']} expired.")
                    continue
                last = await db_query("SELECT MAX(reward_time) as last FROM product_rewards WHERE purchase_id=?", (p['id'],), one=True)
                last_time = datetime.strptime(last['last'], '%Y-%m-%d %H:%M:%S') if last and last['last'] else datetime.strptime(p['start_time'], '%Y-%m-%d %H:%M:%S')
                hours = int((now-last_time).total_seconds()//3600)
                if hours > 0 and p['hourly_reward'] > 0:
                    amount = p['hourly_reward'] * hours
                    await credit(p['user_id'], p['reward_type'], amount, 'product_reward')
                    await db_execute("INSERT INTO product_rewards (user_id,purchase_id,reward_time,amount,type) VALUES (?,?,?,?,?)", (p['user_id'], p['id'], now.strftime('%Y-%m-%d %H:%M:%S'), amount, p['reward_type']))
        except Exception as e:
            print('BG error', e)
        await asyncio.sleep(60)

class AuthReq(BaseModel): initData: str
@app.post('/api/auth', dependencies=[Depends(rate_limiter)])
async def auth(req: AuthReq):
    tg = verify_telegram_data(req.initData)
    if not tg:
        # local test fallback only when BOT_TOKEN is not real
        if BOT_TOKEN and ':' in BOT_TOKEN and 'ABC' not in BOT_TOKEN:
            raise HTTPException(401, 'Invalid Telegram data')
        tg = {'id': 999001, 'username':'localtest', 'first_name':'Local User', 'language_code':'en'}
    user = await db_query("SELECT * FROM users WHERE tg_id=?", (tg['id'],), one=True)
    if not user:
        uid = await db_execute("INSERT INTO users (tg_id,username,first_name,lang) VALUES (?,?,?,?)", (tg['id'], tg.get('username',''), tg.get('first_name','User'), tg.get('language_code','en')))
        await db_execute("INSERT INTO balances (user_id,usdt,xp,keys,tickets) VALUES (?,20,0,0,0)", (uid,))
        user = await db_query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if user['is_banned']: raise HTTPException(403, 'Account banned')
    return {'token': create_jwt(user['id'], user['role'])}

@app.get('/api/me', dependencies=[Depends(rate_limiter)])
async def me(u=Depends(get_current_user)):
    bal = await db_query("SELECT * FROM balances WHERE user_id=?", (u['id'],), one=True)
    refs = await db_query("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND level=1", (u['id'],), one=True)
    unread = await db_query("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0", (u['id'],), one=True)
    season = await db_query("SELECT * FROM seasons WHERE status='active' ORDER BY id DESC LIMIT 1", one=True)
    return {'user': u, 'balance': bal, 'referrals': refs['c'], 'unread_notifications': unread['c'], 'settings': await settings_dict(), 'season': season, 'payment_methods': await db_query("SELECT * FROM payment_methods WHERE status='active'")}

@app.get('/api/products', dependencies=[Depends(rate_limiter)])
async def products(): return await db_query("SELECT * FROM products WHERE status='active' ORDER BY id DESC")

class BuyReq(BaseModel): product_id: int
@app.post('/api/buy', dependencies=[Depends(rate_limiter)])
async def buy(req: BuyReq, u=Depends(get_current_user)):
    p = await db_query("SELECT * FROM products WHERE id=? AND status='active'", (req.product_id,), one=True)
    if not p: raise HTTPException(404, 'Product not found')
    if p['stock'] == 0: raise HTTPException(400, 'Out of stock')
    await debit(u['id'], 'USDT', float(p['price']))
    now = datetime.utcnow(); end = now + timedelta(hours=int(p['duration_hours']))
    purchase_id = await db_execute("INSERT INTO product_purchases (user_id,product_id,start_time,end_time) VALUES (?,?,?,?)", (u['id'], p['id'], now.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')))
    if p['stock'] and p['stock'] > 0: await db_execute("UPDATE products SET stock=stock-1 WHERE id=?", (p['id'],))
    if float(p['instant_bonus'] or 0) > 0: await credit(u['id'], p['reward_type'], p['instant_bonus'], 'instant_bonus')
    st = await settings_dict()
    for r in await db_query("SELECT * FROM referrals WHERE referee_id=?", (u['id'],)):
        rate = float(st.get(f"ref_com_{r['level']}", 0) or 0)
        if rate > 0:
            amt = float(p['price']) * rate / 100
            await credit(r['referrer_id'], 'USDT', amt, 'ref_commission')
            await db_execute("UPDATE referrals SET reward_paid=reward_paid+? WHERE id=?", (amt, r['id']))
            await notify(r['referrer_id'], f"Referral commission +{amt:.2f} USDT")
    await db_execute("INSERT INTO live_activity (message,type) VALUES (?,?)", (f"User {u['first_name'][:3]}*** bought {p['name']}", 'buy'))
    return {'message':'Purchase successful', 'purchase_id': purchase_id}

@app.get('/api/my-packs', dependencies=[Depends(rate_limiter)])
async def mypacks(u=Depends(get_current_user)):
    return await db_query("SELECT pp.*,p.name,p.image_url,p.category,p.reward_type,p.hourly_reward,p.total_return FROM product_purchases pp JOIN products p ON pp.product_id=p.id WHERE pp.user_id=? ORDER BY pp.id DESC", (u['id'],))

@app.get('/api/rewards/history', dependencies=[Depends(rate_limiter)])
async def rewards(u=Depends(get_current_user)):
    return await db_query("SELECT * FROM product_rewards WHERE user_id=? ORDER BY id DESC LIMIT 100", (u['id'],))

class DepReq(BaseModel): txid: str; amount: float; method: str
@app.post('/api/deposit', dependencies=[Depends(rate_limiter)])
async def deposit(req: DepReq, u=Depends(get_current_user)):
    st = await settings_dict()
    if req.amount < float(st.get('deposit_min', 1)): raise HTTPException(400, 'Below minimum deposit')
    if await db_query("SELECT id FROM deposits WHERE txid=?", (req.txid,), one=True): raise HTTPException(400, 'TxID already exists')
    await db_execute("INSERT INTO deposits (user_id,txid,amount,method) VALUES (?,?,?,?)", (u['id'], req.txid, req.amount, req.method))
    await db_execute("INSERT INTO live_activity (message,type) VALUES (?,?)", (f"User {u['first_name'][:3]}*** submitted deposit {req.amount} USDT", 'deposit'))
    return {'message':'Deposit pending'}

class WdReq(BaseModel): address: str; amount: float; network: str = 'TRC20'
@app.post('/api/withdraw', dependencies=[Depends(rate_limiter)])
async def withdraw(req: WdReq, u=Depends(get_current_user)):
    st = await settings_dict(); fee = float(st.get('withdraw_fee', 1)); minw = float(st.get('withdraw_min', 10))
    if req.amount < minw: raise HTTPException(400, f'Minimum withdraw {minw} USDT')
    await debit(u['id'], 'USDT', req.amount + fee)
    await db_execute("INSERT INTO withdrawals (user_id,address,amount,fee,network) VALUES (?,?,?,?,?)", (u['id'], req.address, req.amount, fee, req.network))
    return {'message':'Withdraw pending'}

@app.get('/api/tasks', dependencies=[Depends(rate_limiter)])
async def tasks(u=Depends(get_current_user)):
    ts = await db_query("SELECT * FROM tasks WHERE status='active'")
    for t in ts:
        last = await db_query("SELECT claimed_at FROM task_claims WHERE user_id=? AND task_id=? ORDER BY id DESC LIMIT 1", (u['id'], t['id']), one=True)
        t['claimed'] = bool(last and (datetime.utcnow()-datetime.strptime(last['claimed_at'], '%Y-%m-%d %H:%M:%S')).total_seconds() < float(t['cooldown_hours'])*3600)
    return ts
class ClaimTask(BaseModel): task_id:int
@app.post('/api/tasks/claim', dependencies=[Depends(rate_limiter)])
async def claim_task(req: ClaimTask, u=Depends(get_current_user)):
    t = await db_query("SELECT * FROM tasks WHERE id=? AND status='active'", (req.task_id,), one=True)
    if not t: raise HTTPException(404, 'Task not found')
    last = await db_query("SELECT claimed_at FROM task_claims WHERE user_id=? AND task_id=? ORDER BY id DESC LIMIT 1", (u['id'], t['id']), one=True)
    if last and (datetime.utcnow()-datetime.strptime(last['claimed_at'], '%Y-%m-%d %H:%M:%S')).total_seconds() < float(t['cooldown_hours'])*3600: raise HTTPException(400, 'Cooldown active')
    await db_execute("INSERT INTO task_claims (user_id,task_id) VALUES (?,?)", (u['id'], t['id']))
    await credit(u['id'], t['reward_type'], float(t['reward_amount']), 'task_reward')
    return {'reward': t['reward_amount'], 'type': t['reward_type']}

class RedeemReq(BaseModel): code: str
@app.post('/api/redeem', dependencies=[Depends(rate_limiter)])
async def redeem(req: RedeemReq, u=Depends(get_current_user)):
    c = await db_query("SELECT * FROM redeem_codes WHERE code=? AND status='active'", (req.code.strip().upper(),), one=True)
    if not c: raise HTTPException(404, 'Invalid code')
    if c['expires_at'] and datetime.utcnow() > datetime.strptime(c['expires_at'], '%Y-%m-%d %H:%M:%S'): raise HTTPException(400, 'Code expired')
    if c['current_uses'] >= c['max_uses']: raise HTTPException(400, 'Usage limit reached')
    if await db_query("SELECT id FROM redeem_history WHERE user_id=? AND code_id=?", (u['id'], c['id']), one=True): raise HTTPException(400, 'Already used')
    await credit(u['id'], c['reward_type'], c['reward_amount'], 'redeem')
    await db_execute("UPDATE redeem_codes SET current_uses=current_uses+1 WHERE id=?", (c['id'],))
    await db_execute("INSERT INTO redeem_history (user_id,code_id) VALUES (?,?)", (u['id'], c['id']))
    return {'message':'Redeemed', 'amount': c['reward_amount'], 'type': c['reward_type']}

@app.get('/api/store', dependencies=[Depends(rate_limiter)])
async def store(): return await db_query("SELECT * FROM store_items WHERE status='active' ORDER BY id DESC")
class StoreBuy(BaseModel): item_id:int
@app.post('/api/store/buy', dependencies=[Depends(rate_limiter)])
async def storebuy(req: StoreBuy, u=Depends(get_current_user)):
    it = await db_query("SELECT * FROM store_items WHERE id=? AND status='active'", (req.item_id,), one=True)
    if not it: raise HTTPException(404, 'Item not found')
    if it['stock'] == 0: raise HTTPException(400, 'Out of stock')
    await debit(u['id'], it['price_type'], it['price_amount'])
    await credit(u['id'], it['reward_type'], it['reward_amount'], 'store_order')
    if it['stock'] and it['stock'] > 0: await db_execute("UPDATE store_items SET stock=stock-1 WHERE id=?", (it['id'],))
    await db_execute("INSERT INTO store_orders (user_id,item_id) VALUES (?,?)", (u['id'], it['id']))
    return {'message':'Purchased'}

@app.get('/api/referrals/milestones', dependencies=[Depends(rate_limiter)])
async def milestones(u=Depends(get_current_user)):
    ms = await db_query("SELECT * FROM referral_milestones WHERE status='active' ORDER BY req_refs")
    claimed = {x['milestone_id'] for x in await db_query("SELECT milestone_id FROM milestone_claims WHERE user_id=?", (u['id'],))}
    refs = await db_query("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND level=1", (u['id'],), one=True)
    for m in ms: m['claimed'] = m['id'] in claimed; m['can_claim'] = refs['c'] >= m['req_refs'] and not m['claimed']
    return ms
class MileReq(BaseModel): milestone_id:int
@app.post('/api/referrals/claim', dependencies=[Depends(rate_limiter)])
async def claim_mile(req:MileReq, u=Depends(get_current_user)):
    m = await db_query("SELECT * FROM referral_milestones WHERE id=? AND status='active'", (req.milestone_id,), one=True)
    if not m: raise HTTPException(404, 'Not found')
    refs = await db_query("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND level=1", (u['id'],), one=True)
    if refs['c'] < m['req_refs']: raise HTTPException(400, 'Not enough referrals')
    if await db_query("SELECT id FROM milestone_claims WHERE user_id=? AND milestone_id=?", (u['id'], m['id']), one=True): raise HTTPException(400, 'Already claimed')
    await db_execute("INSERT INTO milestone_claims (user_id,milestone_id) VALUES (?,?)", (u['id'], m['id']))
    await credit(u['id'], m['reward_type'], m['reward_amount'], 'ref_milestone')
    return {'message':'Claimed'}

@app.get('/api/notifications', dependencies=[Depends(rate_limiter)])
async def notifications(u=Depends(get_current_user)):
    rows = await db_query("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", (u['id'],))
    await db_execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (u['id'],))
    return rows
@app.get('/api/activity', dependencies=[Depends(rate_limiter)])
async def activity():
    rows = await db_query("SELECT * FROM live_activity ORDER BY id DESC LIMIT 15")
    st = await settings_dict()
    if st.get('fake_activity','1') == '1' and len(rows) < 5:
        rows += [{'message':'User Ari*** claimed 100 XP'}, {'message':'User Max*** bought Auto Node'}, {'message':'User Ben*** withdrew 12 USDT'}]
    return rows

# Admin
class AdminLogin(BaseModel): username:str; password:str
@app.post('/admin/api/login')
async def admin_login(req:AdminLogin):
    if req.username == ADMIN_USERNAME and req.password == ADMIN_PASSWORD:
        return {'token': create_jwt(0, 'admin')}
    raise HTTPException(401, 'Invalid admin login')
@app.get('/admin/api/data', dependencies=[Depends(get_admin_user)])
async def admin_data():
    return {
        'stats': await db_query("SELECT (SELECT COUNT(*) FROM users) users,(SELECT COUNT(*) FROM users WHERE is_banned=1) banned,(SELECT SUM(usdt) FROM balances) usdt,(SELECT COUNT(*) FROM deposits WHERE status='pending') dep,(SELECT COUNT(*) FROM withdrawals WHERE status='pending') wd,(SELECT COUNT(*) FROM products) products,(SELECT COUNT(*) FROM product_purchases) purchases", one=True),
        'users': await db_query("SELECT u.*,b.usdt,b.xp,b.keys,b.tickets,b.streak FROM users u LEFT JOIN balances b ON u.id=b.user_id ORDER BY u.id DESC"),
        'products': await db_query("SELECT * FROM products ORDER BY id DESC"),
        'tasks': await db_query("SELECT * FROM tasks ORDER BY id DESC"),
        'payment_methods': await db_query("SELECT * FROM payment_methods ORDER BY id"),
        'deposits': await db_query("SELECT * FROM deposits ORDER BY id DESC"),
        'withdrawals': await db_query("SELECT * FROM withdrawals ORDER BY id DESC"),
        'redeem_codes': await db_query("SELECT * FROM redeem_codes ORDER BY id DESC"),
        'store_items': await db_query("SELECT * FROM store_items ORDER BY id DESC"),
        'milestones': await db_query("SELECT * FROM referral_milestones ORDER BY req_refs"),
        'seasons': await db_query("SELECT * FROM seasons ORDER BY id DESC"),
        'settings': await settings_dict(),
        'logs': await db_query("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 100")
    }
class AdminAction(BaseModel): amount: Optional[float]=None; ban: Optional[int]=None; status: Optional[str]=None
@app.post('/admin/api/action/{typ}/{id}/{action}', dependencies=[Depends(get_admin_user)])
async def admin_action(typ:str, id:int, action:str, data:dict=None):
    data = data or {}
    if typ == 'user':
        if action == 'balance': await credit(id, data.get('type','USDT'), float(data.get('amount',0)), 'admin_adjust'); await notify(id, f"Admin adjusted +{data.get('amount')} {data.get('type','USDT')}")
        if action == 'minus': await debit(id, data.get('type','USDT'), float(data.get('amount',0)))
        if action == 'ban': await db_execute("UPDATE users SET is_banned=? WHERE id=?", (int(data.get('ban',1)), id))
        if action == 'verify': await db_execute("UPDATE users SET is_verified=? WHERE id=?", (int(data.get('verified',1)), id))
    elif typ == 'deposit':
        d = await db_query("SELECT * FROM deposits WHERE id=?", (id,), one=True)
        if not d or d['status'] != 'pending': raise HTTPException(400, 'Invalid deposit')
        if action == 'approve': await db_execute("UPDATE deposits SET status='approved' WHERE id=?", (id,)); await credit(d['user_id'], 'USDT', d['amount'], 'deposit'); await notify(d['user_id'], f"Deposit {d['amount']} USDT approved")
        else: await db_execute("UPDATE deposits SET status='rejected' WHERE id=?", (id,)); await notify(d['user_id'], f"Deposit rejected")
    elif typ == 'withdrawal':
        w = await db_query("SELECT * FROM withdrawals WHERE id=?", (id,), one=True)
        if not w or w['status'] != 'pending': raise HTTPException(400, 'Invalid withdrawal')
        if action == 'approve': await db_execute("UPDATE withdrawals SET status='approved' WHERE id=?", (id,)); await notify(w['user_id'], f"Withdrawal {w['amount']} USDT approved")
        else: await db_execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (id,)); await credit(w['user_id'], 'USDT', w['amount']+w['fee'], 'withdraw_refund'); await notify(w['user_id'], 'Withdrawal rejected and refunded')
    elif typ == 'product' and action == 'delete': await db_execute("DELETE FROM products WHERE id=?", (id,))
    elif typ == 'task' and action == 'delete': await db_execute("DELETE FROM tasks WHERE id=?", (id,))
    elif typ == 'redeem' and action == 'delete': await db_execute("DELETE FROM redeem_codes WHERE id=?", (id,))
    elif typ == 'store' and action == 'delete': await db_execute("DELETE FROM store_items WHERE id=?", (id,))
    await admin_log(f'{typ}_{action}', id, data)
    return {'message':'OK'}

@app.post('/admin/api/products', dependencies=[Depends(get_admin_user)])
async def save_product(d:dict):
    vals = (d.get('name',''), d.get('image_url',''), d.get('description',''), float(d.get('price',0)), int(d.get('duration_hours',24)), d.get('category','Investment'), d.get('reward_type','USDT'), float(d.get('hourly_reward',0)), float(d.get('daily_reward',0)), float(d.get('total_return',0)), int(d.get('total_spins',0)), float(d.get('instant_bonus',0)), int(d.get('stock',-1)), d.get('status','active'), d.get('terms',''))
    if d.get('id'): await db_execute("UPDATE products SET name=?,image_url=?,description=?,price=?,duration_hours=?,category=?,reward_type=?,hourly_reward=?,daily_reward=?,total_return=?,total_spins=?,instant_bonus=?,stock=?,status=?,terms=? WHERE id=?", vals+(int(d['id']),))
    else: await db_execute("INSERT INTO products (name,image_url,description,price,duration_hours,category,reward_type,hourly_reward,daily_reward,total_return,total_spins,instant_bonus,stock,status,terms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
    await admin_log('save_product', d.get('name',''), '')
    return {'message':'Saved'}
@app.post('/admin/api/tasks', dependencies=[Depends(get_admin_user)])
async def save_task(d:dict):
    vals=(d.get('title',''),d.get('description',''),d.get('type','custom'),d.get('link',''),d.get('reward_type','XP'),float(d.get('reward_amount',0)),int(d.get('cooldown_hours',24)),int(d.get('daily_limit',1)),d.get('status','active'))
    if d.get('id'): await db_execute("UPDATE tasks SET title=?,description=?,type=?,link=?,reward_type=?,reward_amount=?,cooldown_hours=?,daily_limit=?,status=? WHERE id=?", vals+(int(d['id']),))
    else: await db_execute("INSERT INTO tasks (title,description,type,link,reward_type,reward_amount,cooldown_hours,daily_limit,status) VALUES (?,?,?,?,?,?,?,?,?)", vals)
    return {'message':'Saved'}
@app.post('/admin/api/payment_methods', dependencies=[Depends(get_admin_user)])
async def save_payment(d:dict):
    vals=(d.get('name',''),d.get('network',''),d.get('wallet',''),d.get('qr_url',''),d.get('status','active'))
    if d.get('id'): await db_execute("UPDATE payment_methods SET name=?,network=?,wallet=?,qr_url=?,status=? WHERE id=?", vals+(int(d['id']),))
    else: await db_execute("INSERT INTO payment_methods (name,network,wallet,qr_url,status) VALUES (?,?,?,?,?)", vals)
    return {'message':'Saved'}
@app.post('/admin/api/redeem', dependencies=[Depends(get_admin_user)])
async def save_redeem(d:dict):
    vals=(d.get('code','').upper(),d.get('reward_type','USDT'),float(d.get('reward_amount',0)),int(d.get('max_uses',1)),d.get('expires_at','2099-01-01 00:00:00'),d.get('status','active'))
    if d.get('id'): await db_execute("UPDATE redeem_codes SET code=?,reward_type=?,reward_amount=?,max_uses=?,expires_at=?,status=? WHERE id=?", vals+(int(d['id']),))
    else: await db_execute("INSERT INTO redeem_codes (code,reward_type,reward_amount,max_uses,expires_at,status) VALUES (?,?,?,?,?,?)", vals)
    return {'message':'Saved'}
@app.post('/admin/api/store', dependencies=[Depends(get_admin_user)])
async def save_store(d:dict):
    vals=(d.get('name',''),d.get('description',''),d.get('image_url',''),d.get('price_type','USDT'),float(d.get('price_amount',0)),d.get('reward_type','XP'),float(d.get('reward_amount',0)),int(d.get('stock',-1)),d.get('status','active'))
    if d.get('id'): await db_execute("UPDATE store_items SET name=?,description=?,image_url=?,price_type=?,price_amount=?,reward_type=?,reward_amount=?,stock=?,status=? WHERE id=?", vals+(int(d['id']),))
    else: await db_execute("INSERT INTO store_items (name,description,image_url,price_type,price_amount,reward_type,reward_amount,stock,status) VALUES (?,?,?,?,?,?,?,?,?)", vals)
    return {'message':'Saved'}
@app.post('/admin/api/settings', dependencies=[Depends(get_admin_user)])
async def save_settings(d:dict):
    for k,v in d.items(): await db_execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))
    await admin_log('settings','all',d)
    return {'message':'Saved'}
@app.post('/admin/api/notify', dependencies=[Depends(get_admin_user)])
async def admin_notify(d:dict):
    if str(d.get('target','all')) == 'all':
        for u in await db_query("SELECT id FROM users"): await notify(u['id'], d.get('message',''))
    else: await notify(int(d.get('target')), d.get('message',''))
    await db_execute("INSERT INTO broadcasts (message) VALUES (?)", (d.get('message',''),))
    return {'message':'Sent'}
@app.get('/admin/api/export/users.csv', dependencies=[Depends(get_admin_user)])
async def export_users():
    rows = await db_query("SELECT u.*,b.usdt,b.xp,b.keys,b.tickets FROM users u LEFT JOIN balances b ON u.id=b.user_id")
    out=io.StringIO(); writer=csv.DictWriter(out, fieldnames=list(rows[0].keys()) if rows else ['id']); writer.writeheader(); writer.writerows(rows)
    return StreamingResponse(iter([out.getvalue()]), media_type='text/csv', headers={'Content-Disposition':'attachment; filename=users.csv'})

ADMIN_HTML = r'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin</title><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-neutral-950 text-white"><div id="login" class="min-h-screen flex items-center justify-center"><div class="bg-neutral-900 p-6 rounded-xl w-96"><h1 class="text-2xl text-yellow-400 font-bold mb-4">Admin Login</h1><input id="lu" class="w-full p-3 mb-3 bg-black rounded" placeholder="Username"><input id="lp" type="password" class="w-full p-3 mb-3 bg-black rounded" placeholder="Password"><button onclick="login()" class="w-full p-3 bg-yellow-500 text-black rounded font-bold">Login</button></div></div><div id="app" class="hidden"><div class="p-4 bg-neutral-900 sticky top-0 flex gap-2 flex-wrap" id="nav"></div><main class="p-4" id="main"></main></div><script>
let tk=localStorage.atk||''; const tabs=['Dashboard','Users','Products','Tasks','Payments','Deposits','Withdrawals','Redeem','Store','Settings','Notify','Logs']; if(tk) show();
async function req(u,m='GET',b=null){let o={method:m,headers:{'Authorization':'Bearer '+tk,'Content-Type':'application/json'}}; if(b)o.body=JSON.stringify(b); let r=await fetch('/admin/api'+u,o); if(r.status==401){localStorage.removeItem('atk');location.reload()} return await r.json()}
async function login(){let r=await fetch('/admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:lu.value,password:lp.value})}); if(r.ok){tk=(await r.json()).token;localStorage.atk=tk;show()}else alert('Wrong login')}
function show(){login.className='hidden';app.className='block';nav.innerHTML=tabs.map(t=>`<button class="px-3 py-2 bg-neutral-800 rounded" onclick="load('${t}')">${t}</button>`).join('')+'<button class="px-3 py-2 bg-red-800 rounded" onclick="localStorage.removeItem(\'atk\');location.reload()">Logout</button>';load('Dashboard')}
function table(rows,cols){return `<div class="overflow-auto"><table class="min-w-full bg-neutral-900 text-sm"><tr>${cols.map(c=>`<th class="p-2 text-left text-yellow-400">${c}</th>`).join('')}</tr>${rows}</table></div>`}
async function load(t){let d=await req('/data'); if(t=='Dashboard') main.innerHTML=`<h1 class="text-3xl text-yellow-400 mb-4">Dashboard</h1><div class="grid grid-cols-2 md:grid-cols-6 gap-3">${Object.entries(d.stats).map(([k,v])=>`<div class="bg-neutral-900 p-4 rounded"><div class="text-neutral-400">${k}</div><b class="text-2xl">${v||0}</b></div>`).join('')}</div>`;
if(t=='Users') main.innerHTML=`<h1 class="text-2xl mb-3">Users</h1><a class="text-yellow-400" href="/admin/api/export/users.csv">Export CSV</a>`+table(d.users.map(u=>`<tr class="border-t border-neutral-800"><td class="p-2">${u.id}</td><td>${u.first_name}</td><td>${u.usdt||0}</td><td>${u.xp||0}</td><td>${u.keys||0}</td><td>${u.tickets||0}</td><td>${u.is_banned?'Banned':'Active'}</td><td><button onclick="act('user',${u.id},'balance',{type:'USDT',amount:prompt('Add USDT')})" class="bg-blue-600 px-2 rounded">Add</button> <button onclick="act('user',${u.id},'minus',{type:'USDT',amount:prompt('Minus USDT')})" class="bg-orange-600 px-2 rounded">Minus</button> <button onclick="act('user',${u.id},'ban',{ban:${u.is_banned?0:1}})" class="bg-red-600 px-2 rounded">${u.is_banned?'Unban':'Ban'}</button></td></tr>`).join(''),['ID','Name','USDT','XP','Keys','Tickets','Status','Action']);
if(t=='Products'){window.P=d.products; main.innerHTML=form('Product','saveProduct()', ['id','name','image_url','description','price','duration_hours','category','reward_type','hourly_reward','daily_reward','total_return','total_spins','instant_bonus','stock','status','terms'])+table(d.products.map(p=>`<tr class="border-t border-neutral-800"><td class="p-2">${p.id}</td><td>${p.name}</td><td>${p.category}</td><td>${p.price}</td><td>${p.status}</td><td><button onclick="fill(${p.id},P)" class="text-blue-400">Edit</button> <button onclick="act('product',${p.id},'delete')" class="text-red-400">Delete</button></td></tr>`).join(''),['ID','Name','Category','Price','Status','Action'])}
if(t=='Tasks'){window.T=d.tasks; main.innerHTML=form('Task','saveTask()', ['id','title','description','type','link','reward_type','reward_amount','cooldown_hours','daily_limit','status'])+table(d.tasks.map(x=>`<tr class="border-t border-neutral-800"><td>${x.id}</td><td>${x.title}</td><td>${x.reward_amount} ${x.reward_type}</td><td>${x.status}</td><td><button onclick="fill(${x.id},T)">Edit</button> <button onclick="act('task',${x.id},'delete')">Del</button></td></tr>`).join(''),['ID','Title','Reward','Status','Action'])}
if(t=='Payments'){window.PM=d.payment_methods; main.innerHTML=form('Payment','savePay()', ['id','name','network','wallet','qr_url','status'])+table(d.payment_methods.map(x=>`<tr><td>${x.id}</td><td>${x.name}</td><td>${x.network}</td><td>${x.wallet}</td><td>${x.status}</td><td><button onclick="fill(${x.id},PM)">Edit</button></td></tr>`).join(''),['ID','Name','Network','Wallet','Status','Action'])}
if(t=='Deposits') main.innerHTML=table(d.deposits.map(x=>`<tr><td>${x.id}</td><td>${x.user_id}</td><td>${x.amount}</td><td>${x.txid}</td><td>${x.status}</td><td>${x.status=='pending'?`<button onclick="act('deposit',${x.id},'approve')" class="bg-green-700 px-2">Approve</button> <button onclick="act('deposit',${x.id},'reject')" class="bg-red-700 px-2">Reject</button>`:''}</td></tr>`).join(''),['ID','UID','Amount','TxID','Status','Action']);
if(t=='Withdrawals') main.innerHTML=table(d.withdrawals.map(x=>`<tr><td>${x.id}</td><td>${x.user_id}</td><td>${x.amount}</td><td>${x.address}</td><td>${x.status}</td><td>${x.status=='pending'?`<button onclick="act('withdrawal',${x.id},'approve')" class="bg-green-700 px-2">Approve</button> <button onclick="act('withdrawal',${x.id},'reject')" class="bg-red-700 px-2">Reject</button>`:''}</td></tr>`).join(''),['ID','UID','Amount','Address','Status','Action']);
if(t=='Redeem'){window.R=d.redeem_codes; main.innerHTML=form('Redeem','saveRedeem()', ['id','code','reward_type','reward_amount','max_uses','expires_at','status'])+table(d.redeem_codes.map(x=>`<tr><td>${x.id}</td><td>${x.code}</td><td>${x.reward_amount} ${x.reward_type}</td><td>${x.current_uses}/${x.max_uses}</td><td><button onclick="fill(${x.id},R)">Edit</button> <button onclick="act('redeem',${x.id},'delete')">Del</button></td></tr>`).join(''),['ID','Code','Reward','Used','Action'])}
if(t=='Store'){window.S=d.store_items; main.innerHTML=form('Store','saveStore()', ['id','name','description','image_url','price_type','price_amount','reward_type','reward_amount','stock','status'])+table(d.store_items.map(x=>`<tr><td>${x.id}</td><td>${x.name}</td><td>${x.price_amount} ${x.price_type}</td><td>${x.reward_amount} ${x.reward_type}</td><td><button onclick="fill(${x.id},S)">Edit</button> <button onclick="act('store',${x.id},'delete')">Del</button></td></tr>`).join(''),['ID','Name','Price','Reward','Action'])}
if(t=='Settings') main.innerHTML=`<h1 class="text-2xl mb-3">Settings</h1>${['deposit_min','withdraw_min','withdraw_fee','ref_com_1','ref_com_2','ref_com_3','fake_activity','maintenance','support_username','app_name'].map(k=>`<label>${k}</label><input id="set_${k}" class="block bg-black p-2 mb-2 w-full md:w-96" value="${d.settings[k]||''}">`).join('')}<button onclick="saveSettings()" class="bg-yellow-500 text-black px-4 py-2 rounded">Save</button>`;
if(t=='Notify') main.innerHTML=`<h1 class="text-2xl mb-3">Send Notification</h1><select id="nt" class="bg-black p-2"><option value="all">All</option>${d.users.map(u=>`<option value="${u.id}">${u.id} ${u.first_name}</option>`).join('')}</select><textarea id="nm" class="block bg-black p-2 my-3 w-full md:w-96" rows="4"></textarea><button onclick="notify()" class="bg-yellow-500 text-black px-4 py-2 rounded">Send</button>`;
if(t=='Logs') main.innerHTML=table(d.logs.map(x=>`<tr><td>${x.created_at}</td><td>${x.action}</td><td>${x.target}</td><td>${x.details}</td></tr>`).join(''),['Time','Action','Target','Details']);}
function form(title,fn,fields){return `<h1 class="text-2xl mb-3">${title}</h1><div class="grid grid-cols-1 md:grid-cols-3 gap-2 bg-neutral-900 p-3 rounded mb-4">${fields.map(f=>`<input id="f_${f}" placeholder="${f}" class="bg-black p-2 rounded">`).join('')}<button onclick="${fn}" class="bg-yellow-500 text-black p-2 rounded font-bold">Save</button></div>`}
function fill(id,arr){let o=arr.find(x=>x.id==id); Object.entries(o).forEach(([k,v])=>{let e=document.getElementById('f_'+k); if(e)e.value=v??''})}
async function act(t,id,a,d=null){if(confirm(a+'?')){await req(`/action/${t}/${id}/${a}`,'POST',d);load('Dashboard')}}
function obj(){let o={};document.querySelectorAll('[id^=f_]').forEach(e=>{if(e.value!=='')o[e.id.slice(2)]=e.value});return o}
async function saveProduct(){await req('/products','POST',obj());load('Products')} async function saveTask(){await req('/tasks','POST',obj());load('Tasks')} async function savePay(){await req('/payment_methods','POST',obj());load('Payments')} async function saveRedeem(){await req('/redeem','POST',obj());load('Redeem')} async function saveStore(){await req('/store','POST',obj());load('Store')}
async function saveSettings(){let o={};document.querySelectorAll('[id^=set_]').forEach(e=>o[e.id.slice(4)]=e.value);await req('/settings','POST',o);alert('Saved')}
async function notify(){await req('/notify','POST',{target:nt.value,message:nm.value});alert('Sent');nm.value=''}
</script></body></html>'''

@app.get('/admin', response_class=HTMLResponse)
async def admin_page(): return ADMIN_HTML
@app.get('/', response_class=FileResponse)
async def frontend(): return 'index.html'

@app.on_event('startup')
async def startup():
    await init_db()
    asyncio.create_task(background_loop())
    if dp and bot:
        asyncio.create_task(dp.start_polling(bot))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app:app', host='0.0.0.0', port=PORT)
