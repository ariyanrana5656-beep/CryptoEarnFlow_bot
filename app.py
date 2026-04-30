import os, json, time, hmac, hashlib, asyncio, jwt, csv, io
from datetime import datetime, timedelta
from urllib.parse import unquote, parse_qsl
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
from dotenv import load_dotenv

try:
    from aiogram import Bot, Dispatcher, types, Router
    from aiogram.filters import Command
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    from aiogram.client.default import DefaultBotProperties
except Exception:
    Bot = Dispatcher = Router = None

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN','').strip()
WEBAPP_URL = os.getenv('WEBAPP_URL','http://localhost:8000').strip().rstrip('/')
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME','admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD','admin123')
SECRET_KEY = os.getenv('SECRET_KEY','change-this-secret')
DATABASE_PATH = os.getenv('DATABASE_PATH','app.db')
APP_NAME = os.getenv('APP_NAME','Crypto Earn Flow')
SUPPORT_USERNAME = os.getenv('SUPPORT_USERNAME','@Support')
ADMIN_IDS = [x.strip() for x in os.getenv('ADMIN_IDS','').split(',') if x.strip()]
BOT_USERNAME = os.getenv('BOT_USERNAME','YourBot')
PORT = int(os.getenv('PORT','8000'))

app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

bot = dp = router = None
if BOT_TOKEN and ':' in BOT_TOKEN and Bot:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    dp = Dispatcher(); router = Router(); dp.include_router(router)

RATE = {}
async def rate_limiter(request: Request):
    ip=request.client.host if request.client else 'local'; now=time.time()
    RATE[ip]=[t for t in RATE.get(ip,[]) if now-t<1]
    if len(RATE[ip])>25: raise HTTPException(429,'Too many requests')
    RATE[ip].append(now)

async def q(sql,args=(),one=False):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory=aiosqlite.Row
        cur=await db.execute(sql,args); rows=await cur.fetchall(); await cur.close()
        return (dict(rows[0]) if rows else None) if one else [dict(r) for r in rows]
async def x(sql,args=()):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur=await db.execute(sql,args); await db.commit(); return cur.lastrowid
async def setting(key, default=None):
    r=await q('SELECT value FROM settings WHERE key=?',(key,),True); return r['value'] if r else default
async def set_setting(key,value):
    await x('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))

def nowstr(): return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
def jwt_make(payload, days=7):
    payload=dict(payload); payload['exp']=datetime.utcnow()+timedelta(days=days)
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')
def jwt_read(auth):
    if not auth or not auth.startswith('Bearer '): raise HTTPException(401,'Unauthorized')
    try: return jwt.decode(auth.split(' ',1)[1], SECRET_KEY, algorithms=['HS256'])
    except jwt.ExpiredSignatureError: raise HTTPException(401,'Token expired')
    except Exception: raise HTTPException(401,'Invalid token')
async def current_user(authorization: str = Header(None)):
    p=jwt_read(authorization)
    if p.get('role')!='user': raise HTTPException(403,'User token required')
    u=await q('SELECT * FROM users WHERE id=?',(p['uid'],),True)
    if not u: raise HTTPException(404,'User not found')
    if u['is_banned']: raise HTTPException(403,'Account banned')
    return u
async def current_admin(authorization: str = Header(None)):
    p=jwt_read(authorization)
    if p.get('role')!='admin': raise HTTPException(403,'Admin only')
    return {'username': p.get('username','admin')}
async def log_admin(admin,action,target='',details=''):
    await x('INSERT INTO admin_logs(admin,action,target,details) VALUES(?,?,?,?)',(admin,action,str(target),str(details)))
async def notify(uid,msg):
    await x('INSERT INTO notifications(user_id,message) VALUES(?,?)',(uid,msg))
async def reward_user(uid, rtype, amount, source='reward'):
    rtype=(rtype or 'USDT').upper(); amount=float(amount)
    field={'USDT':'usdt','XP':'xp','KEY':'keys','KEYS':'keys','TICKET':'tickets','TICKETS':'tickets'}.get(rtype,'xp')
    await x(f'UPDATE balances SET {field}={field}+? WHERE user_id=?',(amount,uid))
    await x('INSERT INTO transactions(user_id,type,amount,currency) VALUES(?,?,?,?)',(uid,source,amount,rtype))

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,tg_id INTEGER UNIQUE,username TEXT,first_name TEXT,last_name TEXT,photo_url TEXT,lang TEXT DEFAULT 'en',country TEXT DEFAULT '',ref_by INTEGER,role TEXT DEFAULT 'user',is_banned INTEGER DEFAULT 0,is_verified INTEGER DEFAULT 1,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,last_login DATETIME);
CREATE TABLE IF NOT EXISTS balances(user_id INTEGER PRIMARY KEY,usdt REAL DEFAULT 0,xp REAL DEFAULT 0,keys REAL DEFAULT 0,tickets REAL DEFAULT 0,streak INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,image_url TEXT,description TEXT,price REAL DEFAULT 0,duration_hours INTEGER DEFAULT 24,category TEXT DEFAULT 'Investment',reward_type TEXT DEFAULT 'USDT',hourly_reward REAL DEFAULT 0,daily_reward REAL DEFAULT 0,total_return REAL DEFAULT 0,total_spins INTEGER DEFAULT 0,instant_bonus REAL DEFAULT 0,stock INTEGER DEFAULT -1,status TEXT DEFAULT 'active',terms TEXT DEFAULT '',created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS product_purchases(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,product_id INTEGER,start_time DATETIME,end_time DATETIME,status TEXT DEFAULT 'active',price_paid REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS product_rewards(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,purchase_id INTEGER,reward_time DATETIME,amount REAL,type TEXT);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,description TEXT,type TEXT DEFAULT 'custom',link TEXT,reward_type TEXT DEFAULT 'XP',reward_amount REAL DEFAULT 0,cooldown_hours INTEGER DEFAULT 24,daily_limit INTEGER DEFAULT 1,status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS task_claims(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,task_id INTEGER,claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS referrals(id INTEGER PRIMARY KEY AUTOINCREMENT,referrer_id INTEGER,referee_id INTEGER,level INTEGER,reward_paid REAL DEFAULT 0,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,UNIQUE(referrer_id,referee_id,level));
CREATE TABLE IF NOT EXISTS milestone_claims(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,req_refs INTEGER,claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,req_refs));
CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,txid TEXT UNIQUE,amount REAL,method TEXT,status TEXT DEFAULT 'pending',created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,address TEXT,network TEXT DEFAULT 'TRC20',amount REAL,fee REAL,status TEXT DEFAULT 'pending',created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,message TEXT,is_read INTEGER DEFAULT 0,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,type TEXT,amount REAL,currency TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS admin_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,admin TEXT,action TEXT,target TEXT,details TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS live_activity(id INTEGER PRIMARY KEY AUTOINCREMENT,message TEXT,type TEXT DEFAULT 'real',created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS redeem_codes(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT UNIQUE,reward_type TEXT,reward_amount REAL,max_uses INTEGER DEFAULT 1,current_uses INTEGER DEFAULT 0,expires_at DATETIME,status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS redeem_history(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,code_id INTEGER,claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,code_id));
CREATE TABLE IF NOT EXISTS store_items(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,description TEXT,price_type TEXT DEFAULT 'USDT',price_amount REAL DEFAULT 0,reward_type TEXT DEFAULT 'XP',reward_amount REAL DEFAULT 0,stock INTEGER DEFAULT -1,status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS store_orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,item_id INTEGER,purchased_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS checkins(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,day TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,day));
'''); await db.commit()
    defaults={
        'deposit_wallet_trc20':'TRC20_WALLET_ADDRESS_HERE','deposit_wallet_bep20':'BEP20_WALLET_ADDRESS_HERE','binance_pay':'BINANCE_PAY_ID_HERE',
        'min_withdraw':'5','withdraw_fee':'1','ref_com_1':'10','ref_com_2':'5','ref_com_3':'2','fake_activity':'1','app_name':APP_NAME,
        'support_username':SUPPORT_USERNAME,'season_title':'Genesis Season','season_pool':'Prize Pool 500 USDT','season_end':'2099-12-31 23:59:59','bot_username':BOT_USERNAME
    }
    for k,v in defaults.items(): await set_setting(k,v)
    if not await q('SELECT id FROM products LIMIT 1',one=True):
        await x('INSERT INTO products(name,description,price,duration_hours,category,reward_type,hourly_reward,daily_reward,total_return,total_spins,instant_bonus,stock,status,terms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('Starter Auto Node','Collect hourly USDT automatically. Deposit balance is required before buying.',10,24,'Auto-Collect','USDT',0.1,2.4,2.4,24,0,-1,'active','Pack rewards are credited hourly while active.'))
        await x('INSERT INTO products(name,description,price,duration_hours,category,reward_type,hourly_reward,daily_reward,total_return,total_spins,instant_bonus,stock,status,terms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('VIP Growth Pack','Premium pack with XP bonus and hourly rewards.',25,72,'VIP','USDT',0.15,3.6,10.8,72,10,-1,'active','Returns depend on active duration.'))
    if not await q('SELECT id FROM tasks LIMIT 1',one=True):
        await x('INSERT INTO tasks(title,description,type,link,reward_type,reward_amount,cooldown_hours,status) VALUES(?,?,?,?,?,?,?,?)',('Daily Check-in','Claim daily XP bonus','daily','','XP',20,24,'active'))
        await x('INSERT INTO tasks(title,description,type,link,reward_type,reward_amount,cooldown_hours,status) VALUES(?,?,?,?,?,?,?,?)',('Join Support Channel','Open the support channel and claim reward','link',f'https://t.me/{SUPPORT_USERNAME.replace("@","")}', 'XP',50,168,'active'))
    if not await q('SELECT id FROM store_items LIMIT 1',one=True):
        await x('INSERT INTO store_items(name,description,price_type,price_amount,reward_type,reward_amount,stock,status) VALUES(?,?,?,?,?,?,?,?)',('Key Box','Buy keys using XP','XP',100,'KEY',1,-1,'active'))
    await x('INSERT INTO live_activity(message,type) VALUES(?,?)',('System upgraded successfully','system'))

def verify_telegram_data(init_data):
    if not init_data:
        return None
    try:
        data=dict(parse_qsl(init_data, keep_blank_values=True)); recv_hash=data.pop('hash',None)
        if not recv_hash or not BOT_TOKEN: return None
        check='\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
        secret=hmac.new(b'WebAppData',BOT_TOKEN.encode(),hashlib.sha256).digest()
        calc=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if hmac.compare_digest(calc,recv_hash): return json.loads(data.get('user','{}'))
    except Exception: pass
    return None

if router:
    @router.message(Command('start'))
    async def start_handler(message: types.Message):
        tg=message.from_user; ref=None
        parts=(message.text or '').split(maxsplit=1)
        if len(parts)>1 and parts[1].isdigit(): ref=int(parts[1])
        u=await q('SELECT id FROM users WHERE tg_id=?',(tg.id,),True)
        if not u:
            uid=await x('INSERT INTO users(tg_id,username,first_name,last_name,lang,ref_by) VALUES(?,?,?,?,?,?)',(tg.id,tg.username or '',tg.first_name or 'User',tg.last_name or '',tg.language_code or 'en',ref))
            await x('INSERT INTO balances(user_id) VALUES(?)',(uid,))
            parent=ref; level=1
            while parent and level<=3:
                if parent != uid:
                    await x('INSERT OR IGNORE INTO referrals(referrer_id,referee_id,level) VALUES(?,?,?)',(parent,uid,level))
                    pp=await q('SELECT ref_by FROM users WHERE id=?',(parent,),True); parent=pp['ref_by'] if pp else None; level+=1
                else: break
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🚀 Open App', web_app=WebAppInfo(url=WEBAPP_URL))],[InlineKeyboardButton(text='💬 Support', url=f'https://t.me/{SUPPORT_USERNAME.replace("@","")}')]])
        await message.reply(f'Welcome to {APP_NAME}! Open the app below.', reply_markup=kb)
    @router.message(Command('admin'))
    async def admin_handler(message: types.Message):
        if str(message.from_user.id) in ADMIN_IDS or not ADMIN_IDS:
            await message.reply(f'Admin panel: {WEBAPP_URL}/admin')
        else:
            await message.reply('Admin access denied.')

async def background_loop():
    while True:
        try:
            now=datetime.utcnow()
            rows=await q('SELECT pp.*,p.hourly_reward,p.reward_type,p.name FROM product_purchases pp JOIN products p ON p.id=pp.product_id WHERE pp.status="active"')
            for p in rows:
                end=datetime.strptime(p['end_time'],'%Y-%m-%d %H:%M:%S')
                if now>=end:
                    await x('UPDATE product_purchases SET status="expired" WHERE id=?',(p['id'],)); continue
                last=await q('SELECT MAX(reward_time) last FROM product_rewards WHERE purchase_id=?',(p['id'],),True)
                last_dt=datetime.strptime(last['last'],'%Y-%m-%d %H:%M:%S') if last and last['last'] else datetime.strptime(p['start_time'],'%Y-%m-%d %H:%M:%S')
                hours=int((now-last_dt).total_seconds()//3600)
                if hours>0 and p['hourly_reward']>0:
                    amount=float(p['hourly_reward'])*min(hours,24)
                    await reward_user(p['user_id'],p['reward_type'],amount,'auto_collect')
                    await x('INSERT INTO product_rewards(user_id,purchase_id,reward_time,amount,type) VALUES(?,?,?,?,?)',(p['user_id'],p['id'],nowstr(),amount,p['reward_type']))
                    await notify(p['user_id'],f'Auto reward credited: +{amount:g} {p["reward_type"]} from {p["name"]}')
        except Exception as e:
            print('BG error:',e)
        await asyncio.sleep(60)

class AuthReq(BaseModel):
    initData: str = ''
    unsafeUser: dict | None = None

@app.post('/api/auth', dependencies=[Depends(rate_limiter)])
async def auth(req: AuthReq):
    # Primary secure method: Telegram signed initData.
    tg = verify_telegram_data(req.initData)

    # Fallback for Telegram Desktop / menu-button cases where initData is empty
    # but Telegram still exposes initDataUnsafe.user. This keeps the app usable.
    if not tg and req.unsafeUser and req.unsafeUser.get('id'):
        tg = req.unsafeUser

    if not tg:
        raise HTTPException(401, 'Telegram user data not found. Open from the bot Open App button and refresh once.')

    u = await q('SELECT * FROM users WHERE tg_id=?', (tg['id'],), True)
    if not u:
        uid = await x(
            'INSERT INTO users(tg_id,username,first_name,last_name,photo_url,lang) VALUES(?,?,?,?,?,?)',
            (tg['id'], tg.get('username',''), tg.get('first_name','User'), tg.get('last_name',''), tg.get('photo_url',''), tg.get('language_code','en'))
        )
        await x('INSERT INTO balances(user_id) VALUES(?)', (uid,))
        u = await q('SELECT * FROM users WHERE id=?', (uid,), True)
    await x('UPDATE users SET last_login=? WHERE id=?', (nowstr(), u['id']))
    return {'token': jwt_make({'uid': u['id'], 'role': 'user'}), 'user': u}
@app.get('/api/me', dependencies=[Depends(rate_limiter)])
async def me(u=Depends(current_user)):
    bal=await q('SELECT * FROM balances WHERE user_id=?',(u['id'],),True)
    refs=(await q('SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND level=1',(u['id'],),True))['c']
    unread=(await q('SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0',(u['id'],),True))['c']
    settings={r['key']:r['value'] for r in await q('SELECT key,value FROM settings')}
    botname=settings.get('bot_username') or BOT_USERNAME
    return {'user':u,'balance':bal,'referrals':refs,'unread_notifications':unread,'settings':settings,'ref_link':f'https://t.me/{botname}?start={u["id"]}'}
@app.get('/api/products', dependencies=[Depends(rate_limiter)])
async def products(): return await q('SELECT * FROM products WHERE status="active" AND (stock<>0 OR stock=-1) ORDER BY id DESC')
class BuyReq(BaseModel): product_id:int
@app.post('/api/buy', dependencies=[Depends(rate_limiter)])
async def buy(req:BuyReq,u=Depends(current_user)):
    p=await q('SELECT * FROM products WHERE id=? AND status="active"',(req.product_id,),True)
    if not p: raise HTTPException(404,'Product not active')
    if p['stock']==0: raise HTTPException(400,'Stock finished')
    b=await q('SELECT usdt FROM balances WHERE user_id=?',(u['id'],),True)
    if float(b['usdt']) < float(p['price']): raise HTTPException(400,f'Insufficient balance. Deposit at least {p["price"]} USDT first.')
    await x('UPDATE balances SET usdt=usdt-? WHERE user_id=?',(p['price'],u['id']))
    if p['stock'] and p['stock']>0: await x('UPDATE products SET stock=stock-1 WHERE id=?',(p['id'],))
    start=datetime.utcnow(); end=start+timedelta(hours=int(p['duration_hours']))
    pid=await x('INSERT INTO product_purchases(user_id,product_id,start_time,end_time,price_paid) VALUES(?,?,?,?,?)',(u['id'],p['id'],start.strftime('%Y-%m-%d %H:%M:%S'),end.strftime('%Y-%m-%d %H:%M:%S'),p['price']))
    await x('INSERT INTO transactions(user_id,type,amount,currency) VALUES(?,?,?,?)',(u['id'],'buy_product',-float(p['price']),'USDT'))
    if float(p['instant_bonus'] or 0)>0: await reward_user(u['id'],p['reward_type'],p['instant_bonus'],'instant_bonus')
    refs=await q('SELECT * FROM referrals WHERE referee_id=?',(u['id'],))
    sets={r['key']:r['value'] for r in await q('SELECT key,value FROM settings')}
    for r in refs:
        rate=float(sets.get(f'ref_com_{r["level"]}',0) or 0); amt=float(p['price'])*rate/100
        if amt>0:
            await reward_user(r['referrer_id'],'USDT',amt,f'ref_level_{r["level"]}')
            await x('UPDATE referrals SET reward_paid=reward_paid+? WHERE id=?',(amt,r['id']))
            await notify(r['referrer_id'],f'Referral commission: +{amt:.2f} USDT')
    await x('INSERT INTO live_activity(message,type) VALUES(?,?)',(f'{u["first_name"][:3]}*** bought {p["name"]}','buy'))
    return {'msg':'Purchase successful','purchase_id':pid}
@app.get('/api/my-packs', dependencies=[Depends(rate_limiter)])
async def mypacks(u=Depends(current_user)): return await q('SELECT pp.*,p.name,p.image_url,p.category,p.reward_type,p.hourly_reward,p.description FROM product_purchases pp JOIN products p ON p.id=pp.product_id WHERE pp.user_id=? ORDER BY pp.id DESC',(u['id'],))
@app.get('/api/rewards/history', dependencies=[Depends(rate_limiter)])
async def rh(u=Depends(current_user)): return await q('SELECT * FROM product_rewards WHERE user_id=? ORDER BY id DESC LIMIT 50',(u['id'],))
@app.get('/api/tasks', dependencies=[Depends(rate_limiter)])
async def tasks(u=Depends(current_user)):
    ts=await q('SELECT * FROM tasks WHERE status="active" ORDER BY id DESC'); claims=await q('SELECT task_id,MAX(claimed_at) claimed_at FROM task_claims WHERE user_id=? GROUP BY task_id',(u['id'],))
    cmap={c['task_id']:c['claimed_at'] for c in claims}
    for t in ts:
        t['claimed']=False
        if t['id'] in cmap:
            last=datetime.strptime(cmap[t['id']],'%Y-%m-%d %H:%M:%S'); t['claimed']=(datetime.utcnow()-last).total_seconds()<int(t['cooldown_hours'])*3600
    return ts
class ClaimTask(BaseModel): task_id:int
@app.post('/api/tasks/claim', dependencies=[Depends(rate_limiter)])
async def claimtask(req:ClaimTask,u=Depends(current_user)):
    t=await q('SELECT * FROM tasks WHERE id=? AND status="active"',(req.task_id,),True)
    if not t: raise HTTPException(404,'Task not found')
    last=await q('SELECT claimed_at FROM task_claims WHERE user_id=? AND task_id=? ORDER BY id DESC LIMIT 1',(u['id'],t['id']),True)
    if last and (datetime.utcnow()-datetime.strptime(last['claimed_at'],'%Y-%m-%d %H:%M:%S')).total_seconds()<int(t['cooldown_hours'])*3600: raise HTTPException(400,'Cooldown active')
    await x('INSERT INTO task_claims(user_id,task_id) VALUES(?,?)',(u['id'],t['id']))
    await reward_user(u['id'],t['reward_type'],t['reward_amount'],'task_reward')
    return {'reward':t['reward_amount'],'type':t['reward_type']}
class DepReq(BaseModel): txid:str; amount:float; method:str='TRC20'
@app.post('/api/deposit', dependencies=[Depends(rate_limiter)])
async def dep(req:DepReq,u=Depends(current_user)):
    if req.amount<=0: raise HTTPException(400,'Invalid amount')
    if await q('SELECT id FROM deposits WHERE txid=?',(req.txid,),True): raise HTTPException(400,'TxID already submitted')
    await x('INSERT INTO deposits(user_id,txid,amount,method) VALUES(?,?,?,?)',(u['id'],req.txid,req.amount,req.method))
    await notify(u['id'],f'Deposit request submitted: {req.amount} USDT')
    return {'msg':'Deposit pending'}
class WdReq(BaseModel): address:str; amount:float; network:str='TRC20'
@app.post('/api/withdraw', dependencies=[Depends(rate_limiter)])
async def wd(req:WdReq,u=Depends(current_user)):
    minw=float(await setting('min_withdraw','5')); fee=float(await setting('withdraw_fee','1'))
    if req.amount<minw: raise HTTPException(400,f'Minimum withdraw {minw} USDT')
    b=await q('SELECT usdt FROM balances WHERE user_id=?',(u['id'],),True)
    if float(b['usdt'])<req.amount+fee: raise HTTPException(400,'Insufficient balance including fee')
    await x('UPDATE balances SET usdt=usdt-? WHERE user_id=?',(req.amount+fee,u['id']))
    await x('INSERT INTO withdrawals(user_id,address,network,amount,fee) VALUES(?,?,?,?,?)',(u['id'],req.address,req.network,req.amount,fee))
    return {'msg':'Withdraw pending'}
@app.get('/api/activity', dependencies=[Depends(rate_limiter)])
async def activity():
    real=await q('SELECT * FROM live_activity ORDER BY id DESC LIMIT 10')
    if await setting('fake_activity','1')=='1' and len(real)<5:
        real += [{'message':'ron*** claimed +20 XP'},{'message':'ali*** deposited 15 USDT'},{'message':'dev*** bought Auto Node'}]
    return real
@app.get('/api/notifications', dependencies=[Depends(rate_limiter)])
async def notifs(u=Depends(current_user)):
    rows=await q('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50',(u['id'],)); await x('UPDATE notifications SET is_read=1 WHERE user_id=?',(u['id'],)); return rows
class RedeemReq(BaseModel): code:str
@app.post('/api/redeem', dependencies=[Depends(rate_limiter)])
async def redeem(req:RedeemReq,u=Depends(current_user)):
    c=await q('SELECT * FROM redeem_codes WHERE code=? AND status="active"',(req.code.strip(),),True)
    if not c: raise HTTPException(404,'Invalid code')
    if c['expires_at'] and datetime.utcnow()>datetime.strptime(c['expires_at'],'%Y-%m-%d %H:%M:%S'): raise HTTPException(400,'Code expired')
    if c['current_uses']>=c['max_uses']: raise HTTPException(400,'Code limit reached')
    if await q('SELECT id FROM redeem_history WHERE user_id=? AND code_id=?',(u['id'],c['id']),True): raise HTTPException(400,'Already used')
    await x('INSERT INTO redeem_history(user_id,code_id) VALUES(?,?)',(u['id'],c['id'])); await x('UPDATE redeem_codes SET current_uses=current_uses+1 WHERE id=?',(c['id'],)); await reward_user(u['id'],c['reward_type'],c['reward_amount'],'redeem')
    return {'msg':'Redeemed','amount':c['reward_amount'],'type':c['reward_type']}
@app.get('/api/store', dependencies=[Depends(rate_limiter)])
async def store(): return await q('SELECT * FROM store_items WHERE status="active" AND (stock<>0 OR stock=-1)')
class StoreReq(BaseModel): item_id:int
@app.post('/api/store/buy', dependencies=[Depends(rate_limiter)])
async def storebuy(req:StoreReq,u=Depends(current_user)):
    it=await q('SELECT * FROM store_items WHERE id=? AND status="active"',(req.item_id,),True)
    if not it: raise HTTPException(404,'Item not found')
    bal=await q('SELECT * FROM balances WHERE user_id=?',(u['id'],),True); field={'USDT':'usdt','XP':'xp','KEY':'keys','TICKET':'tickets'}.get(it['price_type'].upper(),'usdt')
    if float(bal[field])<float(it['price_amount']): raise HTTPException(400,'Insufficient '+it['price_type'])
    await x(f'UPDATE balances SET {field}={field}-? WHERE user_id=?',(it['price_amount'],u['id']))
    if it['stock']>0: await x('UPDATE store_items SET stock=stock-1 WHERE id=?',(it['id'],))
    await x('INSERT INTO store_orders(user_id,item_id) VALUES(?,?)',(u['id'],it['id'])); await reward_user(u['id'],it['reward_type'],it['reward_amount'],'store_buy')
    return {'msg':'Purchased'}
class MilestoneReq(BaseModel): req_refs:int; reward:float
@app.post('/api/referrals/claim', dependencies=[Depends(rate_limiter)])
async def cm(req:MilestoneReq,u=Depends(current_user)):
    refs=(await q('SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND level=1',(u['id'],),True))['c']
    if refs<req.req_refs: raise HTTPException(400,'Not enough referrals')
    await x('INSERT INTO milestone_claims(user_id,req_refs) VALUES(?,?)',(u['id'],req.req_refs)); await reward_user(u['id'],'USDT',req.reward,'milestone')
    return {'msg':'Claimed'}
@app.get('/api/referrals/milestones', dependencies=[Depends(rate_limiter)])
async def gm(u=Depends(current_user)): return await q('SELECT req_refs FROM milestone_claims WHERE user_id=?',(u['id'],))

class AdminLogin(BaseModel): username:str; password:str
@app.post('/admin/api/login')
async def alogin(req:AdminLogin):
    if req.username==ADMIN_USERNAME and req.password==ADMIN_PASSWORD: return {'token':jwt_make({'role':'admin','username':req.username})}
    raise HTTPException(401,'Invalid login')
@app.get('/admin/api/data', dependencies=[Depends(current_admin)])
async def adata():
    return {'stats':await q('SELECT (SELECT COUNT(*) FROM users) users,(SELECT COALESCE(SUM(usdt),0) FROM balances) total_usdt,(SELECT COUNT(*) FROM deposits WHERE status="pending") pending_deposits,(SELECT COUNT(*) FROM withdrawals WHERE status="pending") pending_withdrawals,(SELECT COUNT(*) FROM products) products',one=True),'users':await q('SELECT u.*,b.usdt,b.xp,b.keys,b.tickets FROM users u LEFT JOIN balances b ON b.user_id=u.id ORDER BY u.id DESC'),'products':await q('SELECT * FROM products ORDER BY id DESC'),'deposits':await q('SELECT * FROM deposits ORDER BY id DESC'),'withdrawals':await q('SELECT * FROM withdrawals ORDER BY id DESC'),'tasks':await q('SELECT * FROM tasks ORDER BY id DESC'),'redeems':await q('SELECT * FROM redeem_codes ORDER BY id DESC'),'store':await q('SELECT * FROM store_items ORDER BY id DESC'),'settings':{r['key']:r['value'] for r in await q('SELECT * FROM settings')},'logs':await q('SELECT * FROM admin_logs ORDER BY id DESC LIMIT 100')}
@app.post('/admin/api/settings', dependencies=[Depends(current_admin)])
async def aset(d:dict,admin=Depends(current_admin)):
    for k,v in d.items(): await set_setting(k,v)
    await log_admin(admin['username'],'settings','all',d); return {'msg':'saved'}
@app.post('/admin/api/products', dependencies=[Depends(current_admin)])
async def aprod(d:dict,admin=Depends(current_admin)):
    vals=(d.get('name',''),d.get('image_url',''),d.get('description',''),float(d.get('price',0)),int(d.get('duration_hours',24)),d.get('category','Investment'),d.get('reward_type','USDT'),float(d.get('hourly_reward',0)),float(d.get('daily_reward',0)),float(d.get('total_return',0)),int(d.get('total_spins',0)),float(d.get('instant_bonus',0)),int(d.get('stock',-1)),d.get('status','active'),d.get('terms',''))
    if d.get('id'):
        await x('UPDATE products SET name=?,image_url=?,description=?,price=?,duration_hours=?,category=?,reward_type=?,hourly_reward=?,daily_reward=?,total_return=?,total_spins=?,instant_bonus=?,stock=?,status=?,terms=? WHERE id=?',vals+(int(d['id']),))
    else:
        await x('INSERT INTO products(name,image_url,description,price,duration_hours,category,reward_type,hourly_reward,daily_reward,total_return,total_spins,instant_bonus,stock,status,terms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',vals)
    await log_admin(admin['username'],'save_product',d.get('id','new'),d.get('name','')); return {'msg':'saved'}
@app.post('/admin/api/tasks', dependencies=[Depends(current_admin)])
async def atask(d:dict,admin=Depends(current_admin)):
    vals=(d.get('title',''),d.get('description',''),d.get('type','custom'),d.get('link',''),d.get('reward_type','XP'),float(d.get('reward_amount',0)),int(d.get('cooldown_hours',24)),int(d.get('daily_limit',1)),d.get('status','active'))
    if d.get('id'): await x('UPDATE tasks SET title=?,description=?,type=?,link=?,reward_type=?,reward_amount=?,cooldown_hours=?,daily_limit=?,status=? WHERE id=?',vals+(int(d['id']),))
    else: await x('INSERT INTO tasks(title,description,type,link,reward_type,reward_amount,cooldown_hours,daily_limit,status) VALUES(?,?,?,?,?,?,?,?,?)',vals)
    await log_admin(admin['username'],'save_task',d.get('id','new'),d.get('title','')); return {'msg':'saved'}
@app.post('/admin/api/redeem', dependencies=[Depends(current_admin)])
async def aredeem(d:dict,admin=Depends(current_admin)):
    await x('INSERT INTO redeem_codes(code,reward_type,reward_amount,max_uses,expires_at,status) VALUES(?,?,?,?,?,?)',(d['code'],d.get('reward_type','USDT'),float(d.get('reward_amount',0)),int(d.get('max_uses',1)),d.get('expires_at') or None,d.get('status','active'))); await log_admin(admin['username'],'create_redeem',d['code'],''); return {'msg':'created'}
@app.post('/admin/api/store', dependencies=[Depends(current_admin)])
async def astore(d:dict,admin=Depends(current_admin)):
    await x('INSERT INTO store_items(name,description,price_type,price_amount,reward_type,reward_amount,stock,status) VALUES(?,?,?,?,?,?,?,?)',(d.get('name',''),d.get('description',''),d.get('price_type','USDT'),float(d.get('price_amount',0)),d.get('reward_type','XP'),float(d.get('reward_amount',0)),int(d.get('stock',-1)),d.get('status','active'))); await log_admin(admin['username'],'create_store',d.get('name',''),''); return {'msg':'created'}
@app.post('/admin/api/action/{typ}/{id}/{action}', dependencies=[Depends(current_admin)])
async def aact(typ:str,id:int,action:str,d:dict=None,admin=Depends(current_admin)):
    d=d or {}
    if typ=='user' and action=='balance': await reward_user(id,d.get('currency','USDT'),float(d.get('amount',0)),'admin_adjust'); await notify(id,f'Admin adjusted balance: {d.get("amount")} {d.get("currency","USDT")}')
    elif typ=='user' and action=='ban': await x('UPDATE users SET is_banned=? WHERE id=?',(1 if d.get('ban') else 0,id))
    elif typ=='product' and action=='delete': await x('DELETE FROM products WHERE id=?',(id,))
    elif typ=='task' and action=='delete': await x('DELETE FROM tasks WHERE id=?',(id,))
    elif typ=='deposit':
        dep=await q('SELECT * FROM deposits WHERE id=?',(id,),True)
        if not dep or dep['status']!='pending': raise HTTPException(400,'Invalid deposit')
        if action=='approve': await x('UPDATE deposits SET status="approved" WHERE id=?',(id,)); await reward_user(dep['user_id'],'USDT',dep['amount'],'deposit'); await notify(dep['user_id'],f'Deposit approved: {dep["amount"]} USDT')
        else: await x('UPDATE deposits SET status="rejected" WHERE id=?',(id,)); await notify(dep['user_id'],'Deposit rejected')
    elif typ=='withdrawal':
        wd=await q('SELECT * FROM withdrawals WHERE id=?',(id,),True)
        if not wd or wd['status']!='pending': raise HTTPException(400,'Invalid withdraw')
        if action=='approve': await x('UPDATE withdrawals SET status="approved" WHERE id=?',(id,)); await notify(wd['user_id'],f'Withdrawal approved: {wd["amount"]} USDT')
        else: await x('UPDATE withdrawals SET status="rejected" WHERE id=?',(id,)); await reward_user(wd['user_id'],'USDT',float(wd['amount'])+float(wd['fee']),'withdraw_refund'); await notify(wd['user_id'],'Withdrawal rejected and refunded')
    await log_admin(admin['username'],f'{typ}_{action}',id,d); return {'msg':'ok'}
@app.post('/admin/api/notify', dependencies=[Depends(current_admin)])
async def anotify(d:dict,admin=Depends(current_admin)):
    if d.get('target')=='all':
        for u in await q('SELECT id FROM users'): await notify(u['id'],d.get('msg',''))
    else: await notify(int(d.get('target')),d.get('msg',''))
    await log_admin(admin['username'],'notify',d.get('target'),d.get('msg')); return {'msg':'sent'}
@app.get('/admin/api/export/users', dependencies=[Depends(current_admin)])
async def export_users():
    rows=await q('SELECT u.id,u.tg_id,u.username,u.first_name,u.created_at,b.usdt,b.xp,b.keys,b.tickets FROM users u LEFT JOIN balances b ON b.user_id=u.id')
    out=io.StringIO(); w=csv.DictWriter(out, fieldnames=rows[0].keys() if rows else ['id']); w.writeheader(); w.writerows(rows)
    return StreamingResponse(iter([out.getvalue()]), media_type='text/csv', headers={'Content-Disposition':'attachment; filename=users.csv'})

ADMIN_HTML = r'''
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin</title><style>body{margin:0;background:#090909;color:#fff;font-family:Arial}button{cursor:pointer}.wrap{display:flex;min-height:100vh}.side{width:230px;background:#111;border-right:1px solid #333;padding:18px;position:fixed;height:100vh}.main{margin-left:260px;padding:24px;max-width:1100px}.brand{color:#ffd43b;font-weight:900;font-size:22px;margin-bottom:22px}.nav button{display:block;width:100%;background:#181818;color:#fff;border:1px solid #333;border-radius:10px;padding:12px;margin:8px 0;text-align:left}.card{background:#121212;border:1px solid #333;border-radius:16px;padding:16px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.inp{background:#070707;border:1px solid #444;border-radius:10px;color:#fff;padding:10px;margin:6px;width:calc(100% - 34px)}.gold{background:#ffd43b;color:#111;border:0;border-radius:10px;padding:10px 14px;font-weight:800}.red{background:#b91c1c;color:#fff;border:0;border-radius:8px;padding:8px}.blue{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:8px}table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #333;padding:8px;text-align:left;font-size:13px}.hide{display:none}@media(max-width:760px){.side{position:relative;width:auto;height:auto}.wrap{display:block}.main{margin-left:0}}</style></head><body><div id="login" style="max-width:360px;margin:12vh auto" class="card"><h2 style="color:#ffd43b">Admin Login</h2><input id="lu" class="inp" placeholder="username"><input id="lp" class="inp" type="password" placeholder="password"><button class="gold" onclick="login()">Login</button></div><div id="app" class="wrap hide"><div class="side"><div class="brand">Admin Panel</div><div class="nav" id="nav"></div><button class="red" onclick="logout()">Logout</button></div><div class="main" id="main"></div></div><script>let tk=localStorage.tk||'';const tabs=['Dashboard','Users','Products','Tasks','Deposits','Withdrawals','Settings','Redeem','Store','Notify','Logs'];async function req(u,m='GET',b){let r=await fetch('/admin/api'+u,{method:m,headers:{Authorization:'Bearer '+tk,'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});if(r.status==401){logout();throw 0}return r.headers.get('content-type')?.includes('json')?r.json():r.text()}async function login(){let r=await fetch('/admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:lu.value,password:lp.value})});if(r.ok){tk=(await r.json()).token;localStorage.tk=tk;show()}else alert('wrong')}function logout(){localStorage.removeItem('tk');location.reload()}function show(){login.classList.add('hide');app.classList.remove('hide');nav.innerHTML=tabs.map(t=>`<button onclick="load('${t}')">${t}</button>`).join('');load('Dashboard')}if(tk)show();let D={};async function data(){D=await req('/data');return D}async function load(t){await data();let m=main;if(t=='Dashboard')m.innerHTML=`<h1>Dashboard</h1><div class=grid><div class=card>Users<br><b>${D.stats.users}</b></div><div class=card>Total USDT<br><b>${(+D.stats.total_usdt).toFixed(2)}</b></div><div class=card>Pending Deposit<br><b>${D.stats.pending_deposits}</b></div><div class=card>Pending Withdraw<br><b>${D.stats.pending_withdrawals}</b></div><div class=card>Products<br><b>${D.stats.products}</b></div></div>`;if(t=='Users')m.innerHTML=`<h1>Users <button class=gold onclick="location.href='/admin/api/export/users'">Export CSV</button></h1><table><tr><th>ID</th><th>Name</th><th>USDT</th><th>XP</th><th>Action</th></tr>${D.users.map(u=>`<tr><td>${u.id}</td><td>${u.first_name}</td><td>${(+u.usdt).toFixed(2)}</td><td>${u.xp}</td><td><button class=blue onclick="bal(${u.id})">Add</button> <button class=red onclick="act('user',${u.id},'ban',{ban:${u.is_banned?0:1}})">${u.is_banned?'Unban':'Ban'}</button></td></tr>`).join('')}</table>`;if(t=='Products')m.innerHTML=`<h1>Products</h1>${formP()}<table><tr><th>Name</th><th>Cat</th><th>Price</th><th>Status</th><th>Act</th></tr>${D.products.map(p=>`<tr><td>${p.name}</td><td>${p.category}</td><td>${p.price}</td><td>${p.status}</td><td><button class=blue onclick='editP(${JSON.stringify(p)})'>Edit</button> <button class=red onclick="act('product',${p.id},'delete')">Del</button></td></tr>`).join('')}</table>`;if(t=='Tasks')m.innerHTML=`<h1>Tasks</h1>${formT()}<table>${D.tasks.map(x=>`<tr><td>${x.title}</td><td>${x.reward_amount} ${x.reward_type}</td><td><button class=red onclick="act('task',${x.id},'delete')">Del</button></td></tr>`).join('')}</table>`;if(t=='Deposits')m.innerHTML=`<h1>Deposits</h1><table>${D.deposits.map(x=>`<tr><td>${x.user_id}</td><td>${x.txid}</td><td>${x.amount}</td><td>${x.status}</td><td>${x.status=='pending'?`<button class=gold onclick="act('deposit',${x.id},'approve')">Approve</button> <button class=red onclick="act('deposit',${x.id},'reject')">Reject</button>`:''}</td></tr>`).join('')}</table>`;if(t=='Withdrawals')m.innerHTML=`<h1>Withdrawals</h1><table>${D.withdrawals.map(x=>`<tr><td>${x.user_id}</td><td>${x.address}</td><td>${x.amount}</td><td>${x.status}</td><td>${x.status=='pending'?`<button class=gold onclick="act('withdrawal',${x.id},'approve')">Approve</button> <button class=red onclick="act('withdrawal',${x.id},'reject')">Reject</button>`:''}</td></tr>`).join('')}</table>`;if(t=='Settings')m.innerHTML=`<h1>Settings</h1><div class=card>${['deposit_wallet_trc20','deposit_wallet_bep20','binance_pay','min_withdraw','withdraw_fee','ref_com_1','ref_com_2','ref_com_3','support_username','bot_username','season_title','season_pool','season_end','fake_activity'].map(k=>`<label>${k}</label><input id='s_${k}' class=inp value='${D.settings[k]||''}'>`).join('')}<button class=gold onclick=saves()>Save</button></div>`;if(t=='Redeem')m.innerHTML=`<h1>Redeem Codes</h1><div class=card><input id=rc class=inp placeholder=CODE><select id=rr class=inp><option>USDT</option><option>XP</option><option>KEY</option><option>TICKET</option></select><input id=ra class=inp placeholder=Amount><input id=ru class=inp placeholder=MaxUses value=1><input id=re class=inp placeholder='Expiry YYYY-MM-DD HH:MM:SS'><button class=gold onclick=redeem()>Create</button></div><table>${D.redeems.map(r=>`<tr><td>${r.code}</td><td>${r.reward_amount} ${r.reward_type}</td><td>${r.current_uses}/${r.max_uses}</td></tr>`).join('')}</table>`;if(t=='Store')m.innerHTML=`<h1>Store</h1><div class=card><input id=sn class=inp placeholder=Name><input id=sd class=inp placeholder=Description><select id=spt class=inp><option>USDT</option><option>XP</option><option>KEY</option><option>TICKET</option></select><input id=spa class=inp placeholder=Price><select id=srt class=inp><option>XP</option><option>KEY</option><option>TICKET</option><option>USDT</option></select><input id=sra class=inp placeholder='Reward amount'><button class=gold onclick=storeSave()>Create</button></div><table>${D.store.map(s=>`<tr><td>${s.name}</td><td>${s.price_amount} ${s.price_type}</td><td>${s.reward_amount} ${s.reward_type}</td></tr>`).join('')}</table>`;if(t=='Notify')m.innerHTML=`<h1>Notify</h1><div class=card><select id=nt class=inp><option value=all>All Users</option>${D.users.map(u=>`<option value=${u.id}>${u.id} ${u.first_name}</option>`)}</select><textarea id=nm class=inp placeholder=Message></textarea><button class=gold onclick=notify()>Send</button></div>`;if(t=='Logs')m.innerHTML=`<h1>Logs</h1><div class=card>${D.logs.map(l=>`<p>[${l.created_at}] ${l.action} ${l.target}</p>`).join('')}</div>`}function formP(){return `<div class=card><input id=pid type=hidden><input id=pn class=inp placeholder=Name><input id=pimg class=inp placeholder=ImageURL><input id=pd class=inp placeholder=Description><input id=pp class=inp placeholder=Price><input id=ph class=inp placeholder=Hours><select id=pc class=inp><option>Investment</option><option>Auto-Collect</option><option>VIP</option><option>Special</option></select><select id=prt class=inp><option>USDT</option><option>XP</option><option>KEY</option><option>TICKET</option></select><input id=phr class=inp placeholder=HourlyReward><input id=pdr class=inp placeholder=DailyReward><input id=ptr class=inp placeholder=TotalReturn><input id=pib class=inp placeholder=InstantBonus><input id=pst class=inp placeholder='Stock -1'><select id=ps class=inp><option>active</option><option>inactive</option></select><textarea id=pt class=inp placeholder=Terms></textarea><button class=gold onclick=saveP()>Save Product</button></div>`}function editP(p){pid.value=p.id;pn.value=p.name;pimg.value=p.image_url||'';pd.value=p.description;pp.value=p.price;ph.value=p.duration_hours;pc.value=p.category;prt.value=p.reward_type;phr.value=p.hourly_reward;pdr.value=p.daily_reward;ptr.value=p.total_return;pib.value=p.instant_bonus;pst.value=p.stock;ps.value=p.status;pt.value=p.terms||'';scrollTo(0,0)}async function saveP(){await req('/products','POST',{id:pid.value,name:pn.value,image_url:pimg.value,description:pd.value,price:pp.value,duration_hours:ph.value,category:pc.value,reward_type:prt.value,hourly_reward:phr.value,daily_reward:pdr.value,total_return:ptr.value,instant_bonus:pib.value,stock:pst.value,status:ps.value,terms:pt.value});load('Products')}function formT(){return `<div class=card><input id=tt class=inp placeholder=Title><input id=td class=inp placeholder=Description><select id=tr class=inp><option>XP</option><option>USDT</option><option>KEY</option><option>TICKET</option></select><input id=ta class=inp placeholder=Amount><input id=tc class=inp placeholder=CooldownHours value=24><button class=gold onclick=saveT()>Save Task</button></div>`}async function saveT(){await req('/tasks','POST',{title:tt.value,description:td.value,reward_type:tr.value,reward_amount:ta.value,cooldown_hours:tc.value,status:'active'});load('Tasks')}async function bal(id){let amount=prompt('Amount');let cur=prompt('Currency USDT/XP/KEY/TICKET','USDT');if(amount)await act('user',id,'balance',{amount,currency:cur})}async function act(t,id,a,d={}){if(confirm(a+'?')){await req(`/action/${t}/${id}/${a}`,'POST',d);load(t=='product'?'Products':t=='task'?'Tasks':t=='deposit'?'Deposits':t=='withdrawal'?'Withdrawals':'Users')}}async function saves(){let d={};document.querySelectorAll('[id^=s_]').forEach(i=>d[i.id.slice(2)]=i.value);await req('/settings','POST',d);alert('saved')}async function redeem(){await req('/redeem','POST',{code:rc.value,reward_type:rr.value,reward_amount:ra.value,max_uses:ru.value,expires_at:re.value,status:'active'});load('Redeem')}async function storeSave(){await req('/store','POST',{name:sn.value,description:sd.value,price_type:spt.value,price_amount:spa.value,reward_type:srt.value,reward_amount:sra.value,stock:-1,status:'active'});load('Store')}async function notify(){await req('/notify','POST',{target:nt.value,msg:nm.value});alert('sent')}</script></body></html>
'''
@app.get('/admin', response_class=HTMLResponse)
async def admin_page(): return ADMIN_HTML
@app.get('/', response_class=FileResponse)
async def index(): return 'index.html'
@app.on_event('startup')
async def startup():
    await init_db(); asyncio.create_task(background_loop())
    if dp and bot: asyncio.create_task(dp.start_polling(bot))
if __name__=='__main__':
    import uvicorn; uvicorn.run('app:app', host='0.0.0.0', port=PORT)
