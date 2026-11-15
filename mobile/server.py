import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import default
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'mobile'
DATA = Path(os.environ.get('POWDER_DATA_DIR', os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', str(ROOT / 'powder_data'))))
UPLOADS = DATA / 'stickers'
DB = DATA / 'inventory.sqlite3'
DATA.mkdir(parents=True,exist_ok=True)
UPLOADS.mkdir(exist_ok=True)
PORT = int(os.environ.get('PORT', '8765'))
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', '').lower() in ('1', 'true', 'yes') or bool(os.environ.get('RAILWAY_ENVIRONMENT'))
SESSIONS = {}
COLORS = ['1001','1013','1015','1019','2004','3001','3010','3015','4000','5010','5007','5015','5016','6005','7001','7003','7015','7035','8001','8002','8003','8004','8006','8007','8014','8015','8016','8017','9010','9016']
TEXTURES = ['غامق محبب','غامق 7','فاتح 8','أبيض 3028','أحمر 308']
TESSERACT = shutil.which('tesseract') or next((str(p) for p in [Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe'),Path(r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe')] if p.exists()),None)

def db():
    conn=sqlite3.connect(DB, timeout=15)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

def ensure_db():
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('manager','operator')),active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS color_codes(code TEXT PRIMARY KEY,color TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cartons(id INTEGER PRIMARY KEY AUTOINCREMENT,company TEXT NOT NULL,category TEXT NOT NULL,color TEXT NOT NULL,product_code TEXT,sticker_code TEXT,batch TEXT,production TEXT,expiry TEXT,initial_weight REAL NOT NULL,remaining_weight REAL NOT NULL,quantity_source INTEGER NOT NULL DEFAULT 1,sender TEXT,notes TEXT,image_path TEXT,opened INTEGER NOT NULL DEFAULT 0,factory INTEGER NOT NULL DEFAULT 0,consumed INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_by TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY AUTOINCREMENT,carton_id INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,weight TEXT,user TEXT NOT NULL,FOREIGN KEY(carton_id) REFERENCES cartons(id));''')
        cols={r['name'] for r in c.execute('PRAGMA table_info(cartons)').fetchall()}
        if 'deleted' not in cols:c.execute('ALTER TABLE cartons ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
        c.execute('CREATE TABLE IF NOT EXISTS custom_powder_items(category TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(category,name))')
        for color in COLORS:c.execute('INSERT OR IGNORE INTO color_codes(code,color) VALUES(?,?)',('__COLOR__'+color,color))
        c.execute('INSERT OR IGNORE INTO color_codes(code,color) VALUES(?,?)',('KA0718','9016'))
        admin_name=os.environ.get('INITIAL_ADMIN_USERNAME','').strip()
        admin_password=os.environ.get('INITIAL_ADMIN_PASSWORD','')
        if bool(admin_name) != bool(admin_password):
            raise RuntimeError('INITIAL_ADMIN_USERNAME و INITIAL_ADMIN_PASSWORD يجب ضبطهما معًا.')
        if admin_password and len(admin_password)<10:
            raise RuntimeError('INITIAL_ADMIN_PASSWORD يجب أن يتكون من 10 أحرف على الأقل.')
        if admin_name and not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            c.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',(admin_name,password_hash(admin_password),'manager'))

def now():
    return time.strftime('%Y-%m-%d %H:%M:%S',time.localtime())

def password_hash(password, salt=None):
    salt=salt or secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)
    return salt.hex()+':'+digest.hex()

def verify(password, stored):
    try:
        salt,digest=stored.split(':',1)
        return secrets.compare_digest(password_hash(password,bytes.fromhex(salt)),salt+':'+digest)
    except Exception:return False

def extract_fields(text):
    upper=text.upper()
    result={}
    shade=re.search(r'\b[A-Z]{2,3}O?\s*[- ]?\s*\d{3,8}\b',upper)
    if shade:
        code=re.sub(r'[^A-Z0-9]','',shade.group(0))
        code=re.sub(r'(?<=[A-Z]{2})O(?=\d)','0',code)
        result['sticker_code']=code
        with db() as c:
            row=c.execute('SELECT color FROM color_codes WHERE code=?',(code,)).fetchone()
            if row:result['color']=row['color']
    before_batch=upper.split('BATCH',1)[0]
    numeric=re.findall(r'(?<![A-Z0-9])\d{6,10}(?![A-Z0-9])',before_batch)
    if numeric:result['product_code']=numeric[0]
    batch=re.search(r'\bBATCH\s*[:#+-]?\s*([A-Z0-9][A-Z0-9 \t-]{4,})',upper)
    if batch:result['batch']=re.sub(r'[ \t-]','',batch.group(1)).strip()
    date=r'(\d{1,2}[./-]\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2})'
    m=re.search(r'(?:MANUF\.?|MFG\.?|MFD\.?|MANUFACTUR(?:E|ED|ING)?\s*DATE|PRODUCTION\s*DATE|PROD\.?\s*DATE)[\s:#+-]*'+date,text,re.I)
    if m:result['production']=m.group(1).replace('.','/')
    m=re.search(r'(?:EXP\.?\s*(?:DATE)?|EXPIRY\s*DATE|EXPIRATION\s*DATE|USE\s*BY|BEST\s*BEFORE)[\s:#+-]*'+date,text,re.I)
    if m:result['expiry']=m.group(1).replace('.','/')
    m=re.search(r'(?:NET\s*WEIGHT|WEIGHT)[\s:#+-]*(\d+(?:[.,]\d+)?)[ \t]*(KG|KGS|KILOGRAMS|G|GRAMS)?',text,re.I)
    if m:
        value=float(m.group(1).replace(',','.'))
        if (m.group(2) or '').lower() in ('g','grams'):value/=1000
        result['weight']=f'{value:g}'
    return result

class Handler(BaseHTTPRequestHandler):
    server_version='PowderMobile/1.0'
    def log_message(self,fmt,*args):print('%s - %s'%(self.address_string(),fmt%args))
    def json(self,obj,status=200,headers=None):
        raw=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
        if headers:
            for k,v in headers.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(raw)
    def body_json(self):
        length=min(int(self.headers.get('Content-Length','0')),2_000_000)
        raw=self.rfile.read(length)
        return json.loads(raw or b'{}')
    def cookie_token(self):
        cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
        morsel=cookie.get('powder_session')
        return morsel.value if morsel else None
    def user(self):
        token=self.cookie_token();return SESSIONS.get(token)
    def require_user(self):
        user=self.user()
        if not user:self.json({'error':'يرجى تسجيل الدخول أولاً'},401);return None
        return user
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/health':return self.json({'status':'ok'})
        if path=='/' or path=='/index.html':return self.static(WEB/'index.html')
        if path=='/manifest.webmanifest':return self.static(WEB/'manifest.webmanifest','application/manifest+json')
        if path=='/sw.js':return self.static(WEB/'sw.js','application/javascript')
        if path=='/api/me':return self.json({'user':self.user()})
        if not path.startswith('/api/'):return self.static(WEB/'index.html')
        user=self.require_user()
        if not user:return
        if path=='/api/summary':
            with db() as c:
                row=c.execute('SELECT COUNT(*) total,SUM(CASE WHEN factory=1 AND consumed=0 THEN 1 ELSE 0 END) factory,SUM(CASE WHEN consumed=1 THEN 1 ELSE 0 END) consumed FROM cartons WHERE deleted=0').fetchone()
                colors=c.execute('SELECT COUNT(DISTINCT category||color) n FROM cartons WHERE consumed=0 AND deleted=0').fetchone()['n']
            return self.json({'total':row['total'] or 0,'factory':row['factory'] or 0,'consumed':row['consumed'] or 0,'categories':colors})
        if path=='/api/cartons':
            with db() as c:rows=c.execute('SELECT * FROM cartons WHERE deleted=0 ORDER BY consumed,id DESC LIMIT 5000').fetchall()
            return self.json([dict(r) for r in rows])
        if path=='/api/movements':
            with db() as c:rows=c.execute('SELECT m.id,m.carton_id,m.at,m.action,m.weight,m.user,x.color,x.category,x.batch FROM movements m JOIN cartons x ON x.id=m.carton_id ORDER BY m.id DESC LIMIT 1000').fetchall()
            return self.json([dict(r) for r in rows])
        if path=='/api/categories':
            with db() as c:custom=[r['color'] for r in c.execute("SELECT DISTINCT color FROM color_codes WHERE substr(code,1,9)='__COLOR__' ORDER BY color").fetchall()]
            with db() as c:textures=[r['name'] for r in c.execute('SELECT name FROM custom_powder_items WHERE category=? ORDER BY name',('خشامنيوم',)).fetchall()]
            return self.json({'colors':list(dict.fromkeys(COLORS+custom)),'textures':list(dict.fromkeys(TEXTURES+textures))})
        if path=='/api/users' and user['role']=='manager':
            with db() as c:rows=c.execute('SELECT username,role,active FROM users ORDER BY username').fetchall()
            return self.json([dict(r) for r in rows])
        return self.json({'error':'المسار غير موجود أو لا تملك الصلاحية'},404)
    def static(self,path,ctype=None):
        try:raw=path.read_bytes()
        except OSError:return self.send_error(404)
        self.send_response(200);self.send_header('Content-Type',ctype or mimetypes.guess_type(str(path))[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        path=urlparse(self.path).path
        if path=='/api/login':
            try:data=self.body_json();name=str(data.get('username','')).strip();pw=str(data.get('password',''))
            except Exception:return self.json({'error':'بيانات الطلب غير صحيحة'},400)
            with db() as c:row=c.execute('SELECT username,password_hash,role,active FROM users WHERE username=?',(name,)).fetchone()
            if not row or not row['active'] or not verify(pw,row['password_hash']):return self.json({'error':'اسم المستخدم أو كلمة المرور غير صحيحة'},401)
            token=secrets.token_urlsafe(32);SESSIONS[token]={'username':row['username'],'role':row['role']}
            secure='; Secure' if COOKIE_SECURE else ''
            return self.json({'user':SESSIONS[token]},headers={'Set-Cookie':f'powder_session={token}; HttpOnly; SameSite=Strict; Path=/{secure}'})
        if path=='/api/logout':
            token=self.cookie_token();SESSIONS.pop(token,None)
            secure='; Secure' if COOKIE_SECURE else ''
            return self.json({'ok':True},headers={'Set-Cookie':f'powder_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{secure}'})
        user=self.require_user()
        if not user:return
        if path=='/api/scan':return self.scan_image()
        if path=='/api/cartons':return self.add_cartons(user)
        match=re.fullmatch(r'/api/cartons/(\d+)/(factory|weight|opened)',path)
        if match:return self.update_carton(user,int(match.group(1)),match.group(2))
        if path=='/api/users':
            if user['role']!='manager':return self.json({'error':'هذه الصلاحية متاحة للمدير فقط'},403)
            try:d=self.body_json();name=str(d.get('username','')).strip();pw=str(d.get('password',''))
            except Exception:return self.json({'error':'بيانات غير صحيحة'},400)
            if not name or len(pw)<10:return self.json({'error':'اسم المستخدم مطلوب وكلمة المرور 10 أحرف على الأقل'},400)
            try:
                with db() as c:c.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',(name,password_hash(pw),'operator'))
                return self.json({'ok':True},201)
            except sqlite3.IntegrityError:return self.json({'error':'اسم المستخدم موجود بالفعل'},409)
        user_match=re.fullmatch(r'/api/users/([^/]+)/(password|active)',path)
        if user_match:
            if user['role']!='manager':return self.json({'error':'هذه الصلاحية متاحة للمدير فقط'},403)
            target,action=user_match.groups()
            with db() as c:exists=c.execute('SELECT username FROM users WHERE username=?',(target,)).fetchone()
            if not exists:return self.json({'error':'المستخدم غير موجود'},404)
            if action=='password':
                try:newpw=str(self.body_json().get('password',''))
                except Exception:return self.json({'error':'كلمة المرور غير صحيحة'},400)
                if len(newpw)<10:return self.json({'error':'كلمة المرور يجب أن تكون 10 أحرف على الأقل'},400)
                with db() as c:c.execute('UPDATE users SET password_hash=? WHERE username=?',(password_hash(newpw),target))
            else:
                if target==user['username']:return self.json({'error':'لا يمكن تعطيل حسابك أثناء تسجيل الدخول'},400)
                with db() as c:c.execute('UPDATE users SET active=1-active WHERE username=?',(target,))
            return self.json({'ok':True})
        return self.json({'error':'المسار غير موجود'},404)
    def scan_image(self):
        ctype=self.headers.get('Content-Type','')
        if 'multipart/form-data' not in ctype:return self.json({'error':'لم تصل صورة'},400)
        length=int(self.headers.get('Content-Length','0'))
        if length>20_000_000:return self.json({'error':'حجم الصورة أكبر من 20 ميجابايت'},413)
        raw=self.rfile.read(length)
        msg=BytesParser(policy=default).parsebytes(('Content-Type: '+ctype+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
        part=next((p for p in msg.iter_parts() if p.get_filename()),None)
        if part is None:return self.json({'error':'لم يتم العثور على ملف الصورة'},400)
        name=Path(part.get_filename()).name
        ext=Path(name).suffix.lower()
        if ext not in ('.png','.jpg','.jpeg','.bmp','.tif','.tiff','.webp'):return self.json({'error':'صيغة الصورة غير مدعومة'},400)
        dest=UPLOADS/(secrets.token_hex(8)+ext);dest.write_bytes(part.get_payload(decode=True) or b'')
        if not TESSERACT:return self.json({'error':'الصورة وصلت، لكن Tesseract غير مثبت على الكمبيوتر الذي يشغل الخادم.'},503)
        try:
            result=subprocess.run([TESSERACT,str(dest),'stdout','-l','eng+ara','--psm','6'],capture_output=True,text=True,timeout=60,check=False)
            text=(result.stdout or '').strip()
            if result.returncode!=0:return self.json({'error':(result.stderr or 'تعذرت قراءة الصورة')[:600]},422)
            return self.json({'text':text,'fields':extract_fields(text),'image_path':str(dest)})
        except Exception as e:return self.json({'error':'تعذرت قراءة الصورة: '+str(e)},500)
    def add_cartons(self,user):
        try:d=self.body_json();qty=int(d.get('quantity',0));weight=float(d.get('weight',0))
        except Exception:return self.json({'error':'العدد أو الوزن غير صحيح'},400)
        if qty<1 or qty>1000 or weight<=0:return self.json({'error':'أدخل عددًا صحيحًا ووزنًا أكبر من صفر'},400)
        company=str(d.get('company','AkzoNobel'));category=str(d.get('category','ألوان'));color=str(d.get('color','9016'));code=str(d.get('sticker_code','')).strip().upper()
        image_path=str(d.get('image_path',''))
        with db() as c:
            if code:
                m=c.execute('SELECT color FROM color_codes WHERE code=?',(code,)).fetchone()
                if m:color=m['color']
            for _ in range(qty):
                at=now();cur=c.execute('''INSERT INTO cartons(company,category,color,product_code,sticker_code,batch,production,expiry,initial_weight,remaining_weight,sender,notes,image_path,created_at,updated_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(company,category,color,str(d.get('product_code','')),code,str(d.get('batch','')),str(d.get('production','')),str(d.get('expiry','')),weight,weight,str(d.get('sender','M')),str(d.get('notes','')),image_path,at,at,user['username']))
                c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(cur.lastrowid,at,'إضافة رصيد جديد',f'{weight:g} كجم',user['username']))
        return self.json({'ok':True,'count':qty,'color':color},201)
    def update_carton(self,user,cid,action):
        try:d=self.body_json()
        except Exception:d={}
        with db() as c:row=c.execute('SELECT * FROM cartons WHERE id=?',(cid,)).fetchone()
        if not row:return self.json({'error':'الكرتونة غير موجودة'},404)
        at=now()
        with db() as c:
            if action=='factory':
                if row['factory']:return self.json({'error':'تم تسجيل دخول المصنع مسبقًا'},409)
                c.execute('UPDATE cartons SET factory=1,updated_at=? WHERE id=?',(at,cid));label='تم دخولها للمصنع';weight=f"{row['remaining_weight']:g} كجم"
            elif action=='weight':
                if not row['factory']:return self.json({'error':'سجّل دخولها للمصنع أولًا'},409)
                try:value=float(d.get('weight'))
                except Exception:return self.json({'error':'الوزن غير صحيح'},400)
                if value<0 or value>row['initial_weight']:return self.json({'error':'الوزن يجب أن يكون بين صفر والوزن الأصلي'},400)
                consumed=int(value==0);label='تم استهلاكها بالكامل' if consumed else 'تحديث الوزن بعد الاستهلاك';weight=f"{row['remaining_weight']:g} ← {value:g} كجم"
                c.execute('UPDATE cartons SET remaining_weight=?,consumed=?,updated_at=? WHERE id=?',(value,consumed,at,cid))
            else:
                opened=0 if row['opened'] else 1;label='تم فتح الكرتونة' if opened else 'إلغاء حالة الفتح';weight=f"{row['remaining_weight']:g} كجم"
                c.execute('UPDATE cartons SET opened=?,updated_at=? WHERE id=?',(opened,at,cid))
            c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(cid,at,label,weight,user['username']))
        return self.json({'ok':True})

def main():
    ensure_db()
    server=ThreadingHTTPServer(('0.0.0.0',PORT),Handler)
    try:
        ip=socket.gethostbyname(socket.gethostname())
    except OSError:ip='عنوان الكمبيوتر'
    print('Powder inventory server is running. Open http://127.0.0.1:%s on this computer.'%PORT)
    print('On a phone connected to this Wi-Fi, open http://%s:%s'%(ip,PORT))
    print('Local-network use only. Do not expose this development server to the public internet.')
    try:server.serve_forever()
    except KeyboardInterrupt:print('\nServer stopped.')
    finally:server.server_close()
if __name__=='__main__':main()
