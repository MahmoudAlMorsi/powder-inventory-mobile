import tkinter as tk
import traceback
from typing import Any
from tkinter import ttk, messagebox, filedialog, simpledialog
import sqlite3, hashlib, secrets, os, sys, subprocess, shutil, json, threading, datetime, re, tempfile
from pathlib import Path

APP_NAME = 'مخزون البودرة'
DATA_DIR = Path(__file__).resolve().parent / 'powder_data'
DB_PATH = DATA_DIR / 'inventory.sqlite3'
UPLOAD_DIR = DATA_DIR / 'stickers'
COLORS = ['1001','1013','1015','1019','2004','3001','3010','3015','4000','5010','5007','5015','5016','6005','7001','7003','7015','7035','8001','8002','8003','8004','8006','8007','8014','8015','8016','8017','9010','9016']
TEXTURES = ['غامق محبب','غامق 7','فاتح 8','أبيض 3028','أحمر 308']
COMPANIES = ['AkzoNobel','Union','Jetton']

DATA_DIR.mkdir(exist_ok=True); UPLOAD_DIR.mkdir(exist_ok=True)

def connect():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def ocr_code_variants(value):
    compact=re.sub(r'[^A-Z0-9]','',value.upper())
    variants=[compact]
    for index in range(2,len(compact)-1):
        if compact[index]=='O' and compact[index+1].isdigit():
            variants.extend((compact[:index]+'0'+compact[index+1:],compact[:index]+compact[index+1:]))
    return list(dict.fromkeys(variants))

def hash_password(password, salt=None):
    salt=salt or secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode('utf-8'),salt=salt,n=2**14,r=8,p=1)
    return salt.hex()+':'+digest.hex()

def verify_password(password, stored):
    try:
        salt_hex,digest_hex=stored.split(':',1)
        return secrets.compare_digest(hash_password(password,bytes.fromhex(salt_hex)),salt_hex+':'+digest_hex)
    except Exception: return False

def init_db():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('manager','operator')),active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS color_codes(code TEXT PRIMARY KEY,color TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cartons(id INTEGER PRIMARY KEY AUTOINCREMENT,company TEXT NOT NULL,category TEXT NOT NULL,color TEXT NOT NULL,product_code TEXT,sticker_code TEXT,batch TEXT,production TEXT,expiry TEXT,initial_weight REAL NOT NULL,remaining_weight REAL NOT NULL,quantity_source INTEGER NOT NULL DEFAULT 1,sender TEXT,notes TEXT,image_path TEXT,opened INTEGER NOT NULL DEFAULT 0,factory INTEGER NOT NULL DEFAULT 0,consumed INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_by TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY AUTOINCREMENT,carton_id INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,weight TEXT,user TEXT NOT NULL,FOREIGN KEY(carton_id) REFERENCES cartons(id));
        CREATE TABLE IF NOT EXISTS custom_powder_items(category TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(category,name));''')
        carton_columns={row['name'] for row in c.execute('PRAGMA table_info(cartons)').fetchall()}
        if 'deleted' not in carton_columns:c.execute('ALTER TABLE cartons ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
        for color in COLORS: c.execute('INSERT OR IGNORE INTO color_codes(code,color) VALUES(?,?)',(f'__COLOR__{color}',color))
        c.execute('INSERT OR IGNORE INTO color_codes(code,color) VALUES(?,?)',('KA0718','9016'))

def now(): return datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')

class PowderApp:
    def __init__(self,root):
        self.root=root; self.root.title(APP_NAME); self.root.geometry('1120x780'); self.root.minsize(900,650); self.root.configure(bg='#f3f6f5')
        self.user: dict[str, Any] | None = None; self.photo_path=''; self.ocr_bin=shutil.which('tesseract') or next((str(p) for p in (Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe'), Path(r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe')) if p.exists()), None)
        self.style=ttk.Style(); self.style.theme_use('clam'); self.style.configure('TNotebook',background='#f3f6f5',borderwidth=0); self.style.configure('TNotebook.Tab',padding=(15,9),font=('Segoe UI',10)); self.style.configure('Treeview',rowheight=29,font=('Segoe UI',9)); self.style.configure('Treeview.Heading',font=('Segoe UI',9,'bold'))
        init_db(); self.show_login()
    def clear(self):
        for w in self.root.winfo_children(): w.destroy()
    def label(self,parent,text,**kw): return tk.Label(parent,text=text,bg=kw.pop('bg','#fff'),fg=kw.pop('fg','#263844'),font=kw.pop('font',('Segoe UI',10)),anchor=kw.pop('anchor','e'),**kw)
    def entry(self,parent,show: str = ''):
        e=tk.Entry(parent,justify='right',font=('Segoe UI',10),relief='solid',bd=1,show=show); return e
    def button(self,parent,text,cmd,primary=False):
        return tk.Button(parent,text=text,command=cmd,bg='#168c84' if primary else '#e8efed',fg='white' if primary else '#263844',activebackground='#117b74' if primary else '#dce7e4',relief='flat',padx=13,pady=7,cursor='hand2',font=('Segoe UI',10,'bold' if primary else 'normal'))
    def current_user(self) -> dict[str, Any]:
        if self.user is None:
            raise RuntimeError('لا يوجد مستخدم مسجل الدخول')
        return self.user
    def show_login(self):
        self.clear(); shell=tk.Frame(self.root,bg='#f3f6f5'); shell.pack(fill='both',expand=True); box=tk.Frame(shell,bg='white',padx=42,pady=32,highlightbackground='#dfe7e5',highlightthickness=1); box.place(relx=.5,rely=.48,anchor='center')
        self.label(box,'مخزون البودرة',font=('Segoe UI',22,'bold'),fg='#152c3b').pack(pady=(0,5)); self.label(box,'تسجيل الدخول إلى قاعدة بيانات المخزون',fg='#71828b').pack(pady=(0,20))
        with connect() as c: count=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']
        if not count:
            self.label(box,'تهيئة أول مرة: أنشئ الحسابين. اختر كلمات مرور جديدة.',fg='#a56b26',wraplength=390).pack(pady=(0,13))
            self.setup_fields=[]
            for name,role in [('MOSTAFA','operator'),('mrcarbon','manager')]:
                self.label(box,f'كلمة مرور {name}',bg='white').pack(anchor='e'); e=self.entry(box,'●');e.pack(fill='x',pady=(3,9),ipady=6);self.setup_fields.append((name,role,e))
            self.button(box,'إنشاء الحسابين وفتح البرنامج',self.create_initial_users,True).pack(fill='x',pady=(7,0))
            self.label(box,'كلمات المرور لا تُخزّن كنص واضح؛ تُحفظ بصيغة مشتقة آمنة.',fg='#71828b',wraplength=390).pack(pady=(13,0))
        else:
            self.label(box,'اسم المستخدم',bg='white').pack(anchor='e'); self.login_name=self.entry(box);self.login_name.pack(fill='x',pady=(3,10),ipady=6)
            self.label(box,'كلمة المرور',bg='white').pack(anchor='e'); self.login_pass=self.entry(box,'●');self.login_pass.pack(fill='x',pady=(3,15),ipady=6)
            self.button(box,'دخول',self.login,True).pack(fill='x');self.login_pass.bind('<Return>',lambda e:self.login())
        self.label(shell,'نسخة تجريبية محلية على هذا الكمبيوتر — للمشاركة بين الأجهزة يلزم استضافة خادم مشترك.',fg='#71828b',bg='#f3f6f5').pack(side='bottom',pady=18)
    def create_initial_users(self):
        values=[(name,role,e.get()) for name,role,e in self.setup_fields]
        if any(len(p)<10 for _,_,p in values): messagebox.showerror('كلمة المرور','استخدم كلمة مرور من 10 أحرف على الأقل لكل حساب.');return
        with connect() as c:
            for name,role,pw in values: c.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',(name,hash_password(pw),role))
        self.show_login();messagebox.showinfo('تم الإعداد','تم إنشاء MOSTAFA و mrcarbon. سجّل الدخول باسم المستخدم وكلمة المرور التي اخترتها.')
    def login(self):
        name=self.login_name.get().strip(); pw=self.login_pass.get()
        with connect() as c: row=c.execute('SELECT * FROM users WHERE username=? AND active=1',(name,)).fetchone()
        if row and verify_password(pw,row['password_hash']): self.user=dict(row);self.show_main()
        else: messagebox.showerror('تعذر الدخول','اسم المستخدم أو كلمة المرور غير صحيحة.')
    def show_main(self):
        self.clear(); self.root.geometry('1320x900'); self.root.minsize(1050,720)
        shell=tk.Frame(self.root,bg='#f3f6f5');shell.pack(fill='both',expand=True)
        side=tk.Frame(shell,bg='#152c3b',width=270);side.pack(side='left',fill='y');side.pack_propagate(False)
        self.label(side,'▦  مخزون البودرة',bg='#152c3b',fg='white',font=('Segoe UI',15,'bold')).pack(anchor='e',padx=18,pady=(20,2))
        self.label(side,'إدارة ومتابعة الرصيد',bg='#152c3b',fg='#a9bdc4',font=('Segoe UI',9)).pack(anchor='e',padx=18,pady=(0,16))
        tk.Frame(side,bg='#304755',height=1).pack(fill='x',padx=14,pady=5)
        self.label(side,'القائمة الرئيسية',bg='#152c3b',fg='#91a9b2',font=('Segoe UI',9)).pack(anchor='e',padx=18,pady=5)
        self.nav_buttons={}
        b=tk.Button(side,text='▤   لوحة المعلومات',command=lambda:self.show_page('dashboard'),anchor='e',bg='#152c3b',fg='white',activebackground='#304755',activeforeground='white',relief='flat',padx=16,pady=9,font=('Segoe UI',10),cursor='hand2');b.pack(fill='x',padx=9,pady=2);self.nav_buttons['dashboard']=b
        self.powder_open=False;self.powder_kind=None
        self.powder_toggle=tk.Button(side,text='▸   قائمة البودرة',command=self.toggle_powder_menu,anchor='e',bg='#152c3b',fg='white',activebackground='#304755',activeforeground='white',relief='flat',padx=16,pady=9,font=('Segoe UI',10),cursor='hand2');self.powder_toggle.pack(fill='x',padx=9,pady=2)
        self.powder_panel=tk.Frame(side,bg='#152c3b')
        self.color_tab=tk.Button(self.powder_panel,text='ألوان',command=lambda:self.set_powder_kind('ألوان'),bg='#203b4b',fg='white',relief='flat',anchor='e',padx=14,pady=7);self.color_tab.pack(fill='x',padx=13,pady=(4,2))
        self.texture_tab=tk.Button(self.powder_panel,text='خشامنيوم',command=lambda:self.set_powder_kind('خشامنيوم'),bg='#203b4b',fg='white',relief='flat',anchor='e',padx=14,pady=7);self.texture_tab.pack(fill='x',padx=13,pady=2)
        self.powder_list=tk.Frame(self.powder_panel,bg='#152c3b');self.powder_list.pack(fill='x',padx=12,pady=7)
        self.first_powder_sibling=None
        for title,key,icon in [('إضافة كراتين','add','＋'),('متابعة الكراتين','track','◷'),('الأرصدة الحالية','stock','▥'),('سجل الحركات','moves','↻')]:
            b=tk.Button(side,text=f'{icon}   {title}',command=lambda pg=key:self.show_page(pg),anchor='e',bg='#152c3b',fg='white',activebackground='#304755',activeforeground='white',relief='flat',padx=16,pady=9,font=('Segoe UI',10),cursor='hand2');b.pack(fill='x',padx=9,pady=2);self.nav_buttons[key]=b
            if key=='add':self.first_powder_sibling=b
        if self.current_user()['role']=='manager':
            b=tk.Button(side,text='⚙   المستخدمون والصلاحيات',command=lambda:self.show_page('users'),anchor='e',bg='#152c3b',fg='white',activebackground='#304755',activeforeground='white',relief='flat',padx=16,pady=9,font=('Segoe UI',10),cursor='hand2');b.pack(fill='x',padx=9,pady=2);self.nav_buttons['users']=b
        profile=tk.Frame(side,bg='#152c3b');profile.pack(side='bottom',fill='x',padx=16,pady=16)
        self.label(profile,self.current_user()['username'],bg='#152c3b',fg='white',font=('Segoe UI',10,'bold')).pack(anchor='e')
        self.label(profile,'مدير النظام' if self.current_user()['role']=='manager' else 'مستخدم مخزون',bg='#152c3b',fg='#a9bdc4').pack(anchor='e')
        self.button(profile,'خروج',self.logout).pack(anchor='e',pady=(8,0))
        main=tk.Frame(shell,bg='#f3f6f5');main.pack(side='right',fill='both',expand=True)
        head=tk.Frame(main,bg='white',height=58);head.pack(fill='x');head.pack_propagate(False)
        self.breadcrumb=self.label(head,'الرئيسية  ›  لوحة المعلومات',bg='white',fg='#71828b');self.breadcrumb.pack(side='right',padx=22,pady=18)
        self.label(head,'●  متصل',bg='white',fg='#25866c').pack(side='left',padx=22)
        self.page_canvas=tk.Canvas(main,bg='#f3f6f5',highlightthickness=0);self.page_canvas.pack(side='left',fill='both',expand=True)
        sb=ttk.Scrollbar(main,orient='vertical',command=self.page_canvas.yview);sb.pack(side='left',fill='y');self.page_canvas.configure(yscrollcommand=sb.set)
        self.page_host=tk.Frame(self.page_canvas,bg='#f3f6f5');self.page_window=self.page_canvas.create_window((0,0),window=self.page_host,anchor='nw')
        self.page_host.bind('<Configure>',lambda e:self.page_canvas.configure(scrollregion=self.page_canvas.bbox('all')));self.page_canvas.bind('<Configure>',lambda e:self.page_canvas.itemconfigure(self.page_window,width=e.width));self.page_canvas.bind_all('<MouseWheel>',lambda e:self.page_canvas.yview_scroll(int(-e.delta/120),'units'))
        self.show_page('dashboard')
    def toggle_powder_menu(self):
        self.powder_open=not self.powder_open
        self.powder_toggle.configure(text=('▾' if self.powder_open else '▸')+'   قائمة البودرة',bg='#304755' if self.powder_open else '#152c3b')
        if self.powder_open:self.powder_panel.pack(fill='x',padx=0,pady=2,before=self.first_powder_sibling)
        else:self.powder_panel.pack_forget()
    def powder_items(self,kind):
        if kind=='ألوان':
            with connect() as c:custom=[r['color'] for r in c.execute("SELECT DISTINCT color FROM color_codes WHERE substr(code,1,9)='__COLOR__' ORDER BY color").fetchall()]
            return list(dict.fromkeys(COLORS+custom))
        with connect() as c:custom=[r['name'] for r in c.execute('SELECT name FROM custom_powder_items WHERE category=? ORDER BY name',(kind,)).fetchall()]
        return list(dict.fromkeys(TEXTURES+custom))
    def render_powder_menu(self):
        for w in self.powder_list.winfo_children():w.destroy()
        if not self.powder_kind:return
        vals=self.powder_items(self.powder_kind)
        for i,value in enumerate(vals):
            b=tk.Button(self.powder_list,text=value,command=lambda v=value:self.show_stock_for(v),bg='#203b4b',fg='white',activebackground='#168c84',activeforeground='white',relief='flat',font=('Segoe UI',8),padx=2,pady=4,cursor='hand2');b.grid(row=i//5,column=i%5,sticky='ew',padx=2,pady=2)
        for col in range(5):self.powder_list.grid_columnconfigure(col,weight=1)
        self.button(self.powder_list,'＋ إضافة لون/نوع',self.add_powder_item).grid(row=(len(vals)+4)//5,column=0,columnspan=5,sticky='ew',padx=2,pady=(6,2))
    def set_powder_kind(self,kind):
        self.powder_kind=kind
        self.color_tab.configure(bg='#168c84' if kind=='ألوان' else '#203b4b')
        self.texture_tab.configure(bg='#168c84' if kind=='خشامنيوم' else '#203b4b')
        self.render_powder_menu()
    def add_powder_item(self):
        if not self.powder_kind:return
        value=simpledialog.askstring('إضافة فئة','أدخل رقم اللون أو اسم نوع الخشامنيوم:')
        if not value:return
        value=value.strip()
        if self.powder_kind=='ألوان':
            with connect() as c:c.execute('INSERT OR IGNORE INTO color_codes(code,color) VALUES(?,?)',('__COLOR__'+value,value))
        else:
            with connect() as c:c.execute('INSERT OR IGNORE INTO custom_powder_items(category,name) VALUES(?,?)',(self.powder_kind,value))
        self.render_powder_menu()
    def show_stock_for(self,value):
        self.stock_filter=(self.powder_kind,value)
        self.show_page('powder')
    def show_page(self,page):
        if page=='users' and self.current_user()['role']!='manager':return
        self.current_page=page;names={'dashboard':'لوحة المعلومات','add':'إضافة كراتين','track':'متابعة الكراتين','stock':'الأرصدة الحالية','moves':'سجل الحركات','users':'المستخدمون والصلاحيات','powder':'قائمة البودرة'}
        self.breadcrumb.config(text='الرئيسية  ›  '+names[page])
        for key,b in self.nav_buttons.items():b.configure(bg='#304755' if key==page else '#152c3b')
        if hasattr(self,'powder_toggle') and page=='powder':self.powder_toggle.configure(bg='#304755')
        for w in self.page_host.winfo_children():w.destroy()
        self.add_tab=self.track_tab=self.stock_tab=self.moves_tab=self.users_tab=self.page_host
        if page=='dashboard':self.build_dashboard()
        elif page=='add':self.build_add()
        elif page=='track':self.refresh_tracking()
        elif page=='stock':self.refresh_stock()
        elif page=='powder':self.build_powder_inventory()
        elif page=='moves':self.refresh_moves()
        elif page=='users':self.build_users()
        self.page_canvas.yview_moveto(0)
    def build_dashboard(self):
        f=self.page_host
        stats=self.card(f,'ملخص المخزون')
        with connect() as c:
            totals=c.execute('SELECT COUNT(*) boxes,SUM(CASE WHEN factory=1 AND consumed=0 THEN 1 ELSE 0 END) factory,SUM(CASE WHEN consumed=1 THEN 1 ELSE 0 END) used FROM cartons WHERE deleted=0').fetchone()
            ncolors=c.execute('SELECT COUNT(DISTINCT category||color) n FROM cartons WHERE consumed=0 AND deleted=0').fetchone()['n']
        row=tk.Frame(stats,bg='white');row.pack(fill='x',pady=7)
        for value,title in [(totals['boxes'] or 0,'إجمالي الكراتين'),(totals['factory'] or 0,'داخل المصنع'),(totals['used'] or 0,'تم استهلاكها'),(ncolors,'فئات بها رصيد')]:
            tile=tk.Frame(row,bg='#f4f8f7',padx=14,pady=12)
            tile.pack(side='right',fill='x',expand=True,padx=5)
            self.label(tile,str(value),bg='#f4f8f7',fg='#168c84',font=('Segoe UI',20,'bold')).pack(anchor='e')
            self.label(tile,title,bg='#f4f8f7',fg='#71828b',font=('Segoe UI',9)).pack(anchor='e')
        recent=self.card(f,'آخر الحركات','اضغط على عنوان الكرتونة أو التاريخ أو اللون لتغيير الترتيب؛ اضغط مرة ثانية لعكسه.')
        columns=('date','carton','action','color','user')
        tree=ttk.Treeview(recent,columns=columns,show='headings',height=11)
        self.dashboard_tree=tree
        self.dashboard_sort_key='date';self.dashboard_sort_reverse=True
        self.dashboard_heading_labels={'date':'التاريخ','carton':'الكرتونة','action':'الحركة','color':'اللون','user':'المستخدم'}
        for col,title in self.dashboard_heading_labels.items():
            if col in ('date','carton','color'):
                tree.heading(col,text=title+(' ▼' if col=='date' else ''),command=lambda key=col:self.sort_dashboard_moves(key))
            else:tree.heading(col,text=title)
        tree.column('date',width=150,anchor='center');tree.column('carton',width=85,anchor='center');tree.column('action',width=220,anchor='e');tree.column('color',width=90,anchor='center');tree.column('user',width=120,anchor='center')
        scroll=ttk.Scrollbar(recent,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scroll.set);tree.pack(side='right',fill='x',expand=True);scroll.pack(side='left',fill='y')
        with connect() as c:
            rows=c.execute('SELECT m.id movement_id,m.at,m.carton_id,m.action,m.user,x.color FROM movements m JOIN cartons x ON x.id=m.carton_id ORDER BY m.id DESC LIMIT 1000').fetchall()
        self.dashboard_rows=[dict(r) for r in rows]
        self.draw_dashboard_moves()
    def draw_dashboard_moves(self):
        tree=self.dashboard_tree
        for item in tree.get_children():tree.delete(item)
        key=self.dashboard_sort_key;reverse=self.dashboard_sort_reverse
        if key=='carton':sort_value=lambda r:int(r['carton_id'])
        elif key=='color':sort_value=lambda r:str(r['color'] or '').casefold()
        else:sort_value=lambda r:str(r['at'] or '')
        for r in sorted(self.dashboard_rows,key=sort_value,reverse=reverse):
            tree.insert('','end',iid=str(r['movement_id']),values=(r['at'],r['carton_id'],r['action'],r['color'],r['user']))
        for col,title in self.dashboard_heading_labels.items():
            marker=(' ▼' if reverse else ' ▲') if col==key else ''
            tree.heading(col,text=title+marker)
    def sort_dashboard_moves(self,key):
        if key==self.dashboard_sort_key:self.dashboard_sort_reverse=not self.dashboard_sort_reverse
        else:self.dashboard_sort_key=key;self.dashboard_sort_reverse=False
        self.draw_dashboard_moves()
    def selected_powder_row(self):
        selection=self.powder_tree.selection() if hasattr(self,'powder_tree') else ()
        if not selection:return None
        with connect() as c:return c.execute('SELECT * FROM cartons WHERE id=? AND deleted=0',(int(selection[0]),)).fetchone()
    def build_powder_inventory(self):
        category,color=self.stock_filter
        with connect() as c:rows=c.execute('SELECT * FROM cartons WHERE category=? AND color=? AND consumed=0 AND deleted=0 ORDER BY id DESC',(category,color)).fetchall()
        card=self.card(self.page_host,f'{category} / {color}',f'عدد الكراتين المتاحة: {len(rows)}. اختر كرتونة لعرض بياناتها وتعديلها.')
        self.powder_tree=ttk.Treeview(card,columns=('id','batch','product','weight','production','expiry','state'),show='headings',height=10)
        for col,title in [('id','رقم الكرتونة'),('batch','رقم التشغيلة'),('product','كود المنتج'),('weight','الوزن الحالي'),('production','الإنتاج'),('expiry','الانتهاء'),('state','الحالة')]:self.powder_tree.heading(col,text=title)
        self.powder_tree.pack(fill='x',expand=True)
        for r in rows:self.powder_tree.insert('','end',iid=str(r['id']),values=(r['id'],r['batch'] or '—',r['product_code'] or '—',f"{r['remaining_weight']:g} / {r['initial_weight']:g} كجم",r['production'] or '—',r['expiry'] or '—','مفتوحة' if r['opened'] else 'داخل المصنع' if r['factory'] else 'في المخزن'))
        self.powder_details=self.card(self.page_host,'بيانات الكرتونة المحددة','حدد كرتونة من الجدول لعرض بياناتها الكاملة.')
        self.powder_tree.bind('<<TreeviewSelect>>',lambda e:self.show_powder_details())
        actions=tk.Frame(self.powder_details,bg='white');actions.pack(fill='x',pady=(8,0))
        self.button(actions,'تعديل البيانات',self.edit_powder_carton,True).pack(side='right',padx=4)
        self.button(actions,'حذف الكرتونة من الرصيد',self.delete_powder_carton).pack(side='right',padx=4)
        self.powder_info=tk.Frame(self.powder_details,bg='white');self.powder_info.pack(fill='x')
    def show_powder_details(self):
        row=self.selected_powder_row()
        if not row:return
        for w in self.powder_info.winfo_children():w.destroy()
        values=[('الشركة',row['company']),('الفئة',row['category']),('اللون',row['color']),('كود المنتج',row['product_code']),('كود الاستيكر',row['sticker_code']),('رقم التشغيلة',row['batch']),('تاريخ الإنتاج',row['production']),('تاريخ الانتهاء',row['expiry']),('الوزن الأصلي',f"{row['initial_weight']:g} كجم"),('الوزن المتبقي',f"{row['remaining_weight']:g} كجم"),('الطرف المرسل',row['sender']),('حالة الفتح','مفتوحة' if row['opened'] else 'غير مفتوحة'),('ملاحظات',row['notes']),('تاريخ الإضافة',row['created_at']),('أضيفت بواسطة',row['created_by']),('صورة الاستيكر',row['image_path'])]
        for i,(label,value) in enumerate(values):
            cell=tk.Frame(self.powder_info,bg='white');cell.grid(row=i//2,column=i%2,sticky='ew',padx=6,pady=5);self.powder_info.grid_columnconfigure(i%2,weight=1)
            self.label(cell,label,bg='white',fg='#71828b',font=('Segoe UI',9)).pack(anchor='e')
            self.label(cell,str(value or '—'),bg='white',font=('Segoe UI',10)).pack(anchor='e')
    def edit_powder_carton(self):
        row=self.selected_powder_row()
        if not row:return messagebox.showwarning('اختر كرتونة','حدد الكرتونة التي تريد تعديلها.')
        win=tk.Toplevel(self.root);win.title('تعديل بيانات الكرتونة '+str(row['id']));win.geometry('650x660');win.configure(bg='#f3f6f5')
        form=tk.Frame(win,bg='white',padx=18,pady=16);form.pack(fill='both',expand=True,padx=12,pady=12);widgets={}
        definitions=[('company','الشركة',['AkzoNobel','Union','Jetton']),('category','الفئة',['ألوان','خشامنيوم']),('color','اللون / النوع',self.powder_items(row['category'])),('product_code','كود المنتج',None),('sticker_code','كود اللون على الاستيكر',None),('batch','رقم التشغيلة',None),('production','تاريخ الإنتاج',None),('expiry','تاريخ الانتهاء',None),('initial_weight','الوزن الأصلي',None),('remaining_weight','الوزن المتبقي',None),('sender','الطرف المرسل',['M','H','A']),('notes','ملاحظات',None)]
        def category_changed(event=None):
            selected_category=widgets['category'].get();w=widgets['color'];values=self.powder_items(selected_category);w.configure(values=values)
            if w.get() not in values:w.set(values[0] if values else '')
        for i,(key,label,values) in enumerate(definitions):
            cell=tk.Frame(form,bg='white');cell.grid(row=i//2,column=i%2,sticky='ew',padx=6,pady=5);form.grid_columnconfigure(i%2,weight=1);self.label(cell,label,bg='white').pack(anchor='e')
            if values is not None:
                w=ttk.Combobox(cell,values=values,state='readonly',justify='right');w.pack(fill='x',pady=4,ipady=3)
                if key=='category':w.bind('<<ComboboxSelected>>',category_changed)
            else:w=self.entry(cell)
            value=row[key]
            if value is not None:
                if isinstance(w,ttk.Combobox):w.set(value)
                else:w.insert(0,str(value))
            widgets[key]=w
        def save_edit():
            try:
                initial=float(widgets['initial_weight'].get());remaining=float(widgets['remaining_weight'].get())
                if initial<=0 or remaining<0 or remaining>initial:raise ValueError
            except ValueError:return messagebox.showerror('وزن غير صحيح','تحقق من الوزن الأصلي والمتبقي.',parent=win)
            data={k:w.get().strip() for k,w in widgets.items() if k not in ('initial_weight','remaining_weight')};at=now();consumed=int(remaining==0)
            with connect() as c:
                c.execute('''UPDATE cartons SET company=?,category=?,color=?,product_code=?,sticker_code=?,batch=?,production=?,expiry=?,initial_weight=?,remaining_weight=?,sender=?,notes=?,consumed=?,updated_at=? WHERE id=?''',(data['company'],data['category'],data['color'],data['product_code'],data['sticker_code'],data['batch'],data['production'],data['expiry'],initial,remaining,data['sender'],data['notes'],consumed,at,row['id']))
                c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(row['id'],at,'تعديل بيانات الكرتونة',f"{row['remaining_weight']:g} ← {remaining:g} كجم",self.current_user()['username']))
            win.destroy();self.show_page('powder')
        self.button(form,'حفظ التعديلات',save_edit,True).grid(row=6,column=0,columnspan=2,sticky='ew',padx=6,pady=12)
    def delete_powder_carton(self):
        row=self.selected_powder_row()
        if not row:return messagebox.showwarning('اختر كرتونة','حدد الكرتونة التي تريد حذفها.')
        if not messagebox.askyesno('حذف من الرصيد','ستختفي الكرتونة من الرصيد النشط، مع الاحتفاظ ببياناتها وسجل حركتها. هل تريد المتابعة؟'):return
        at=now()
        with connect() as c:
            c.execute('UPDATE cartons SET deleted=1,updated_at=? WHERE id=?',(at,row['id']))
            c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(row['id'],at,'حذف الكرتونة من الرصيد',f"{row['remaining_weight']:g} كجم",self.current_user()['username']))
        self.show_page('powder')

    def logout(self): self.user=None;self.show_login()
    def card(self,parent,title,sub=None):
        f=tk.Frame(parent,bg='white',padx=18,pady=15,highlightbackground='#e0e8e6',highlightthickness=1); f.pack(fill='x',padx=10,pady=8)
        self.label(f,title,bg='white',font=('Segoe UI',13,'bold'),fg='#152c3b').pack(anchor='e')
        if sub:self.label(f,sub,bg='white',fg='#71828b').pack(anchor='e',pady=(3,9))
        return f
    def build_add(self):
        f=self.add_tab; self.button(f,'حفظ البيانات وإضافة الكراتين',self.save_cartons,True).pack(anchor='e',padx=10,pady=(10,0)); top=self.card(f,'إضافة رصيد بودرة','أدخل بيانات الاستيكر؛ كل كرتونة ستُسجل كوحدة مستقلة لمتابعة حركتها.')
        self.form={}; fields=[('company','الشركة',COMPANIES),('category','الفئة',['ألوان','خشامنيوم']),('color','اللون',COLORS),('sender','الطرف المرسل',['M','H','A'])]
        grid=tk.Frame(top,bg='white');grid.pack(fill='x')
        for i,(key,title,values) in enumerate(fields):
            cell=tk.Frame(grid,bg='white');cell.grid(row=i//2,column=i%2,sticky='ew',padx=8,pady=5);grid.columnconfigure(i%2,weight=1);self.label(cell,title,bg='white').pack(anchor='e')
            cb=ttk.Combobox(cell,values=values,state='readonly',justify='right');cb.set('AkzoNobel' if key=='company' else 'ألوان' if key=='category' else '9016' if key=='color' else 'M');cb.pack(fill='x',pady=4,ipady=4);self.form[key]=cb
        labels=[('product','كود المنتج'),('code','كود اللون على الاستيكر'),('batch','رقم التشغيلة'),('production','تاريخ الإنتاج YYYY-MM-DD'),('expiry','تاريخ الانتهاء YYYY-MM-DD'),('weight','وزن الكرتونة بالكجم'),('quantity','عدد الكراتين'),('notes','ملاحظات')]
        for i,(key,title) in enumerate(labels):
            cell=tk.Frame(grid,bg='white');cell.grid(row=(i+4)//2,column=(i+4)%2,sticky='ew',padx=8,pady=5);self.label(cell,title,bg='white').pack(anchor='e');e=self.entry(cell);e.pack(fill='x',pady=4,ipady=5);self.form[key]=e
        # date defaults and input types are intentionally plain text for flexible label formats
        image=tk.Frame(top,bg='white');image.pack(fill='x',pady=7);self.label(image,'صورة الاستيكر (اختياري)',bg='white').pack(side='right');self.photo_label=self.label(image,'لم يتم اختيار صورة',bg='white',fg='#71828b');self.photo_label.pack(side='right',padx=12);self.button(image,'اختيار صورة',self.choose_image).pack(side='left')
        if self.ocr_bin:self.button(image,'قراءة نص الصورة',self.run_ocr).pack(side='left',padx=8)
        else:self.label(top,'للقراءة التلقائية: ثبّت Tesseract OCR ثم أعد تشغيل البرنامج. القراءة تحتاج مراجعة قبل الحفظ.',bg='#fff8ec',fg='#80633d',wraplength=850).pack(anchor='e',pady=5)
        self.label(top,'النص المستخرج والبيانات التي تم التعرف عليها',bg='white',fg='#152c3b',font=('Segoe UI',10,'bold')).pack(anchor='e',pady=(9,3))
        self.ocr_preview=tk.Text(top,height=7,wrap='word',font=('Segoe UI',10),relief='solid',bd=1)
        self.ocr_preview.pack(fill='x',pady=(0,8));self.ocr_preview.insert('1.0','سيظهر نص الاستيكر هنا بعد القراءة، وستُملأ الحقول التي يمكن تمييزها تلقائيًا.')
        mapcard=self.card(f,'ربط كود الاستيكر باللون','مثال: KA0718 ← 9016. بعد حفظ الربط، يُستخدم لتصنيف الرصيد القادم.')
        mf=tk.Frame(mapcard,bg='white');mf.pack(fill='x');self.map_code=self.entry(mf);self.map_code.insert(0,'KA0718');self.map_code.pack(side='right',fill='x',expand=True,padx=4,ipady=5);self.map_color=ttk.Combobox(mf,values=COLORS,state='readonly',justify='right');self.map_color.set('9016');self.map_color.pack(side='right',padx=5);self.button(mf,'حفظ الربط',self.save_mapping).pack(side='left')
    def choose_image(self):
        p=filedialog.askopenfilename(filetypes=[('صور','*.png *.jpg *.jpeg *.bmp *.tif *.tiff'),('كل الملفات','*.*')])
        if p:
            self.photo_path=p;self.photo_label.config(text='تم اختيار: '+Path(p).name+' — جاري قراءة الاستيكر...')
            self.root.update_idletasks()
            self.run_ocr()
    def run_ocr(self):
        if not self.photo_path:
            return messagebox.showwarning('اختر صورة','اختر صورة استيكر أولاً.')
        ocr_bin = self.ocr_bin
        if ocr_bin is None:
            return messagebox.showerror('قارئ الصور غير موجود','لم يتم العثور على Tesseract. تحقق من مسار التثبيت ثم أعد تشغيل البرنامج.')
        try:
            with connect() as c:
                known_codes={row['code'].upper() for row in c.execute('SELECT code FROM color_codes').fetchall()}
            candidates = []
            errors = []
            preprocessing_note=''
            try:
                from PIL import Image, ImageEnhance, ImageFilter, ImageOps
            except ImportError:
                ocr_inputs=[
                    (self.photo_path,'eng','6'),(self.photo_path,'eng','11'),
                    (self.photo_path,'eng','4'),(self.photo_path,'eng+ara','6'),
                    (self.photo_path,'eng+ara','11'),(self.photo_path,'eng+ara','4')
                ]
                preprocessing_note='\n\nتنبيه: لم تتوفر مكتبة Pillow لتحسين الصورة.'
                self._collect_ocr_candidates(ocr_bin,ocr_inputs,candidates,errors)
            else:
                with tempfile.TemporaryDirectory(prefix='powder-ocr-') as work_dir:
                    with Image.open(self.photo_path) as source:
                        image=ImageOps.exif_transpose(source)
                        image=ImageOps.autocontrast(ImageOps.grayscale(image),cutoff=1)
                        scale=min(2.5,2400/max(image.size))
                        if scale>1:
                            image=image.resize((round(image.width*scale),round(image.height*scale)),Image.Resampling.LANCZOS)
                        image=ImageEnhance.Contrast(image).enhance(1.35)
                        image=image.filter(ImageFilter.UnsharpMask(radius=2,percent=140,threshold=3))
                        image_width,image_height=image.size
                        full_path=Path(work_dir)/'enhanced.png'
                        top_path=Path(work_dir)/'top.png'
                        bottom_path=Path(work_dir)/'bottom.png'
                        image.save(full_path)
                        image.crop((0,0,image_width,round(image_height*0.38))).save(top_path)
                        image.crop((0,round(image_height*0.80),image_width,image_height)).save(bottom_path)
                    ocr_inputs=[
                        (self.photo_path,'eng','6'),(str(full_path),'eng','6'),
                        (str(full_path),'eng','11'),(str(top_path),'eng','6'),
                        (str(bottom_path),'eng','6'),(str(full_path),'eng+ara','6')
                    ]
                    self._collect_ocr_candidates(ocr_bin,ocr_inputs,candidates,errors)
            def quality(text):
                upper=text.upper()
                score=sum(bool(re.search(pattern,upper)) for pattern in (
                    r'\b\d{6,10}\b',r'\bBATCH\b',r'\bNET\s*WEIGHT\b',
                    r'\b(?:MANUF|MFG|MFD)\b',r'\bEXP\b'
                ))
                code_pattern=r'\b[A-Z]{2,3}O?\s*[- ]?\s*\d{3,8}\b'
                if any(set(ocr_code_variants(match.group(0)))&known_codes for match in re.finditer(code_pattern,upper)):
                    score+=3
                return score,len(text)
            out = max(candidates, key=quality, default='')
            if not out:
                detail = errors[-1] if errors else 'لم يستخرج Tesseract أي حروف من الصورة.'
                self.photo_label.config(text='تمت المحاولة ولم يُقرأ نص؛ الصورة مرفقة')
                return messagebox.showwarning(
                    'لم يُقرأ نص من الصورة',
                    'جرّب قصّ الاستيكر وحده داخل الصورة أو تصويره أقرب وبشكل مستقيم.\n\n' + detail[:900]
                )
            found = self.fill_from_ocr(out)
            self.ocr_preview.delete('1.0','end')
            self.ocr_preview.insert('1.0',out[:12000])
            self.photo_label.config(text='اكتملت القراءة: '+Path(self.photo_path).name)
            messagebox.showinfo('اكتملت قراءة الاستيكر',('تم ملء الحقول التالية: '+('، '.join(found) if found else 'لم نتعرف على حقول مؤكدة')+preprocessing_note+'\n\nراجع النص والحقول قبل الحفظ.'))
        except Exception as e:
            self.photo_label.config(text='تعذرت القراءة؛ الصورة ما زالت مرفقة')
            messagebox.showerror('تعذرت القراءة','حدثت مشكلة أثناء تشغيل Tesseract.\n'+str(e)[:1200])
    def _collect_ocr_candidates(self,ocr_bin,ocr_inputs,candidates,errors):
        for image_path,lang,psm in ocr_inputs:
            result=subprocess.run(
                [ocr_bin,image_path,'stdout','-l',lang,'--psm',psm,'--dpi','300'],
                capture_output=True,text=True,timeout=45,check=False
            )
            text=(result.stdout or '').strip()
            if result.returncode==0 and text:candidates.append(text)
            elif result.stderr:errors.append(result.stderr.strip())
    def fill_entry(self,key,value):
        value=(value or '').strip()
        if not value:return False
        widget=self.form.get(key)
        if widget is None:return False
        widget.delete(0,'end')
        widget.insert(0,value)
        return True
    def fill_from_ocr(self,text):
        normalized=text.replace('\r','\n')
        upper=normalized.upper()
        filled=[]
        def put(key,value,title):
            if self.fill_entry(key,value):filled.append(title)
        code_pattern=r'\b[A-Z]{2,3}O?\s*[- ]?\s*\d{3,8}\b'
        with connect() as c:
            code_mappings={row['code'].upper():row['color'] for row in c.execute('SELECT code,color FROM color_codes').fetchall()}
        code_match=None
        fallback_code=None
        for match in re.finditer(code_pattern,upper):
            variants=ocr_code_variants(match.group(0))
            mapped_code=next((variant for variant in variants if variant in code_mappings),None)
            if mapped_code:
                code_match=mapped_code
                break
            line_start=upper.rfind('\n',0,match.start())+1
            line_end=upper.find('\n',match.end())
            if line_end<0:line_end=len(upper)
            context=upper[line_start:line_end]
            if fallback_code is None and not re.search(r'\b(?:SDS|QUALICOAT)\b',context):
                fallback_code=variants[0]
        code=code_match or fallback_code
        if code:
            put('code',code,'كود اللون')
            mapped_color=code_mappings.get(code)
            if mapped_color in COLORS:
                self.form['color'].set(mapped_color);filled.append('تصنيف اللون '+mapped_color)
        # Product identifiers on these labels are unlabelled numeric codes (e.g. 5562037).
        labeled_product=re.search(r'(?:product\s*(?:code|no\.?|number)|item\s*code|كود\s*المنتج|رقم\s*المنتج)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{1,})',normalized,re.I)
        if labeled_product:
            put('product',labeled_product.group(1),'كود المنتج')
        else:
            before_batch=upper.split('BATCH',1)[0]
            numeric_codes=re.findall(r'(?<![A-Z0-9])\d{6,10}(?![A-Z0-9])',before_batch)
            if numeric_codes:put('product',numeric_codes[0],'كود المنتج')
        # Batch values can contain spaces introduced by OCR; join them back together.
        batch_match=re.search(r'\bBATCH\s*[:#-]?\s*([A-Z0-9][A-Z0-9 \t-]{4,})',upper)
        if batch_match:
            batch=re.sub(r'[ \t-]','',batch_match.group(1)).strip()
            put('batch',batch,'رقم التشغيلة')
        date_pattern=r'(\d{1,2}[./-]\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2})'
        production=re.search(r'(?:manuf\.?|mfg\.?|mfd\.?|manufactur(?:e|ed|ing)?\s*date|production\s*date|prod\.?\s*date|تاريخ\s*الإنتاج)[\s:#+-]*'+date_pattern,normalized,re.I)
        if production:put('production',production.group(1).replace('.','/'),'تاريخ الإنتاج')
        expiry=re.search(r'(?:exp\.?\s*(?:date)?|expiry\s*date|expiration\s*date|use\s*by|best\s*before|تاريخ\s*الانتهاء|تاريخ\s*الصلاحية)[\s:#+-]*'+date_pattern,normalized,re.I)
        if expiry:put('expiry',expiry.group(1).replace('.','/'),'تاريخ الانتهاء')
        weight=re.search(r'(?:net\s*weight|weight|وزن\s*(?:الكرتونة)?|الوزن)[\s:#+-]*(\d+(?:[.,]\d+)?)[ \t]*(kg|kgs|kilograms|g|grams|جم|كجم)?',normalized,re.I)
        if weight:
            value=float(weight.group(1).replace(',','.'))
            if (weight.group(2) or '').lower() in ('g','grams','جم'):value/=1000
            put('weight',f'{value:g}','الوزن لكل كرتونة')
        quantity=re.search(r'(?:qty|quantity|cartons|carton\s*count|boxes|عدد\s*الكراتين|الكمية)\s*[:#-]?\s*(\d+)',normalized,re.I)
        if quantity:put('quantity',quantity.group(1),'عدد الكراتين')
        sender=re.search(r'(?:sender|destination|طرف\s*الوصول|الطرف\s*المرسل)\s*[:#-]?\s*([MHA])\b',normalized,re.I)
        if sender:self.form['sender'].set(sender.group(1).upper());filled.append('الطرف المرسل')
        for name in COMPANIES:
            if name.casefold() in normalized.casefold():self.form['company'].set(name);filled.append('الشركة');break
        texture=next((t for t in TEXTURES if t.casefold() in normalized.casefold()),None)
        if texture:
            self.form['category'].set('خشامنيوم')
            notes=self.form['notes'].get().strip();self.form['notes'].delete(0,'end');self.form['notes'].insert(0,(notes+' | ' if notes else '')+'نوع الخشامنيوم: '+texture)
            filled.append('الفئة')
        return list(dict.fromkeys(filled))
    def save_mapping(self):
        code=self.map_code.get().strip().upper()
        if not code:return messagebox.showwarning('كود ناقص','اكتب كود الاستيكر.')
        with connect() as c:c.execute('INSERT INTO color_codes(code,color) VALUES(?,?) ON CONFLICT(code) DO UPDATE SET color=excluded.color',(code,self.map_color.get()))
        messagebox.showinfo('تم','تم حفظ ربط '+code+' باللون '+self.map_color.get()+'.')
    def save_cartons(self):
        try:
            qty=int(self.form['quantity'].get());weight=float(self.form['weight'].get())
            if qty<1 or weight<=0:raise ValueError()
        except Exception:return messagebox.showerror('بيانات ناقصة','أدخل عدد كراتين صحيحًا ووزنًا أكبر من صفر.')
        company=self.form['company'].get();category=self.form['category'].get();color=self.form['color'].get();code=self.form['code'].get().strip().upper()
        with connect() as c:
            if code:
                mapped=c.execute('SELECT color FROM color_codes WHERE code=?',(code,)).fetchone()
                if mapped:color=mapped['color']
            image=''
            if self.photo_path:
                src=Path(self.photo_path); dest=UPLOAD_DIR/(datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+src.suffix.lower());shutil.copy2(src,dest);image=str(dest)
            for _ in range(qty):
                at=now();cur=c.execute('''INSERT INTO cartons(company,category,color,product_code,sticker_code,batch,production,expiry,initial_weight,remaining_weight,sender,notes,image_path,created_at,updated_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(company,category,color,self.form['product'].get().strip(),code,self.form['batch'].get().strip(),self.form['production'].get().strip(),self.form['expiry'].get().strip(),weight,weight,self.form['sender'].get(),self.form['notes'].get().strip(),image,at,at,self.current_user()['username']))
                c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(cur.lastrowid,at,'إضافة رصيد جديد',f'{weight:g} كجم',self.current_user()['username']))
        self.photo_path='';self.photo_label.config(text='لم يتم اختيار صورة');self.refresh_all();messagebox.showinfo('تم الحفظ',f'تم تسجيل {qty} كرتونة تحت {category} / {color}.')
    def build_users(self):
        f=self.users_tab;card=self.card(f,'حسابات المستخدمين','صلاحية الإدارة متاحة لحساب mrcarbon فقط.')
        self.user_tree=ttk.Treeview(card,columns=('role','active'),show='headings',height=7);self.user_tree.heading('role',text='الصلاحية');self.user_tree.heading('active',text='الحالة');self.user_tree.pack(fill='x',pady=6)
        row=tk.Frame(card,bg='white');row.pack(fill='x',pady=8);self.new_user=self.entry(row);self.new_user.pack(side='right',fill='x',expand=True,padx=4,ipady=5);self.new_user.insert(0,'اسم مستخدم جديد');self.new_pw=self.entry(row,'●');self.new_pw.pack(side='right',fill='x',expand=True,padx=4,ipady=5);self.button(row,'إضافة مستخدم',self.add_user,True).pack(side='left')
        self.button(card,'تغيير كلمة مرور المستخدم المحدد',self.change_password).pack(anchor='e',pady=5);self.button(card,'تفعيل / تعطيل المستخدم المحدد',self.toggle_user).pack(anchor='e',pady=5);self.refresh_users()
    def refresh_users(self):
        if not hasattr(self,'user_tree'):return
        for x in self.user_tree.get_children():self.user_tree.delete(x)
        with connect() as c: rows=c.execute('SELECT username,role,active FROM users ORDER BY username').fetchall()
        for r in rows:self.user_tree.insert('', 'end',iid=r['username'],values=('مدير' if r['role']=='manager' else 'مشغل','فعال' if r['active'] else 'معطل'))
    def selected_username(self):
        sel=self.user_tree.selection();return sel[0] if sel else None
    def add_user(self):
        name=self.new_user.get().strip();pw=self.new_pw.get()
        if not name or name=='اسم مستخدم جديد' or len(pw)<10:return messagebox.showerror('بيانات غير مكتملة','اكتب اسمًا وكلمة مرور من 10 أحرف على الأقل.')
        try:
            with connect() as c:c.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',(name,hash_password(pw),'operator'))
            self.new_pw.delete(0,'end');self.refresh_users()
        except sqlite3.IntegrityError:messagebox.showerror('موجود','اسم المستخدم مستخدم بالفعل.')
    def change_password(self):
        name=self.selected_username()
        if not name:return messagebox.showwarning('اختر مستخدمًا','حدد المستخدم من القائمة.')
        pw=simpledialog.askstring('تغيير كلمة المرور','أدخل كلمة مرور جديدة (10 أحرف على الأقل):',show='●')
        if pw and len(pw)>=10:
            with connect() as c:c.execute('UPDATE users SET password_hash=? WHERE username=?',(hash_password(pw),name))
            messagebox.showinfo('تم','تم تغيير كلمة المرور.')
        elif pw:messagebox.showerror('قصيرة','كلمة المرور يجب أن تكون 10 أحرف على الأقل.')
    def toggle_user(self):
        name=self.selected_username()
        if not name:return messagebox.showwarning('اختر مستخدمًا','حدد المستخدم من القائمة.')
        if name==self.current_user()['username']:return messagebox.showwarning('غير مسموح','لا يمكن تعطيل الحساب المستخدم حاليًا.')
        with connect() as c:c.execute('UPDATE users SET active=1-active WHERE username=?',(name,))
        self.refresh_users()
    def refresh_all(self):self.show_page(getattr(self,'current_page','dashboard'))
    def refresh_tracking(self):
        for w in self.track_tab.winfo_children():w.destroy()
        f=self.card(self.track_tab,'متابعة الكراتين','سجّل دخول المصنع ثم حدّث الوزن المتبقي. عند صفر تنتقل الكرتونة إلى المستهلكة.')
        self.carton_tree=ttk.Treeview(f,columns=('id','cat','color','batch','weight','state','updated'),show='headings',height=15)
        heads=[('id','رقم'),('cat','الفئة'),('color','اللون'),('batch','التشغيلة'),('weight','الوزن الحالي'),('state','الحالة'),('updated','آخر تعديل')]
        for col,title in heads:self.carton_tree.heading(col,text=title)
        self.carton_tree.pack(fill='both',expand=True)
        with connect() as c:rows=c.execute('SELECT * FROM cartons WHERE consumed=0 AND deleted=0 ORDER BY id DESC').fetchall()
        for r in rows:self.carton_tree.insert('','end',iid=str(r['id']),values=(r['id'],r['category'],r['color'],r['batch'] or '—',f"{r['remaining_weight']:g} / {r['initial_weight']:g} كجم",'داخل المصنع' if r['factory'] else 'في المخزن',r['updated_at']))
        actions=tk.Frame(f,bg='white');actions.pack(fill='x',pady=8)
        self.button(actions,'تم دخول المصنع',self.mark_factory).pack(side='right',padx=4);self.button(actions,'تغيير حالة: مفتوحة',self.toggle_carton_opened).pack(side='right',padx=4);self.button(actions,'تحديث الوزن بعد الاستهلاك',self.update_weight,True).pack(side='right',padx=4);self.button(actions,'إظهار المستهلكة',self.show_consumed).pack(side='left')
    def selected_carton(self):
        sel=getattr(self,'carton_tree',None) and self.carton_tree.selection()
        if not sel:messagebox.showwarning('اختر كرتونة','حدد كرتونة من الجدول أولاً.');return None
        with connect() as c:return c.execute('SELECT * FROM cartons WHERE id=?',(int(sel[0]),)).fetchone()
    def mark_factory(self):
        r=self.selected_carton()
        if not r:return
        if r['factory']:return messagebox.showinfo('مسجلة','تم تسجيل دخول هذه الكرتونة للمصنع بالفعل.')
        at=now()
        with connect() as c:c.execute('UPDATE cartons SET factory=1,updated_at=? WHERE id=?',(at,r['id']));c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(r['id'],at,'تم دخولها للمصنع',f"{r['remaining_weight']:g} كجم",self.current_user()['username']))
        self.refresh_all()
    def toggle_carton_opened(self):
        r=self.selected_carton()
        if not r:return
        at=now();new=0 if r['opened'] else 1
        with connect() as c:
            c.execute('UPDATE cartons SET opened=?,updated_at=? WHERE id=?',(new,at,r['id']))
            c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(r['id'],at,'تم فتح الكرتونة' if new else 'إلغاء حالة الفتح',f"{r['remaining_weight']:g} كجم",self.current_user()['username']))
        self.refresh_all()
    def update_weight(self):
        r=self.selected_carton()
        if not r:return
        if not r['factory']:return messagebox.showwarning('لم تدخل المصنع','سجّل دخول الكرتونة للمصنع أولاً.')
        val=simpledialog.askfloat('الوزن بعد الاستهلاك',f"الوزن الحالي {r['remaining_weight']:g} كجم — أدخل الوزن الجديد:",minvalue=0,maxvalue=r['initial_weight'])
        if val is None:return
        at=now();consumed=int(val==0);action='تم استهلاكها بالكامل' if consumed else 'تحديث الوزن بعد الاستهلاك'
        with connect() as c:c.execute('UPDATE cartons SET remaining_weight=?,consumed=?,updated_at=? WHERE id=?',(val,consumed,at,r['id']));c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(r['id'],at,action,f"{r['remaining_weight']:g} ← {val:g} كجم",self.current_user()['username']))
        self.refresh_all()
    def show_consumed(self):
        win=tk.Toplevel(self.root);win.title('الكراتين المستهلكة');win.geometry('800x440');tree=ttk.Treeview(win,columns=('id','cat','color','batch','weight','date'),show='headings')
        for col,title in [('id','رقم'),('cat','الفئة'),('color','اللون'),('batch','التشغيلة'),('weight','الوزن'),('date','تاريخ التحديث')]:tree.heading(col,text=title)
        tree.pack(fill='both',expand=True,padx=12,pady=12)
        with connect() as c:rows=c.execute('SELECT * FROM cartons WHERE consumed=1 AND deleted=0 ORDER BY updated_at DESC').fetchall()
        for r in rows:tree.insert('','end',values=(r['id'],r['category'],r['color'],r['batch'] or '—',f"{r['remaining_weight']:g} كجم",r['updated_at']))
    def refresh_stock(self):
        for w in self.stock_tab.winfo_children():w.destroy()
        f=self.card(self.stock_tab,'الأرصدة الحالية','عدد الكراتين والأوزان المتبقية حسب الفئة واللون.');tree=ttk.Treeview(f,columns=('category','color','count','production','expiry','weight','opened'),show='headings',height=18)
        for col,title in [('category','الفئة'),('color','اللون'),('count','عدد الكراتين'),('production','الإنتاج'),('expiry','الانتهاء'),('weight','الوزن المتبقي'),('opened','مفتوحة')]:tree.heading(col,text=title)
        tree.pack(fill='both',expand=True)
        with connect() as c:
            if getattr(self,'stock_filter',None):
                category,color=self.stock_filter;rows=c.execute('SELECT category,color,COUNT(*) count,MIN(production) production,MIN(expiry) expiry,SUM(remaining_weight) weight,SUM(opened) opened FROM cartons WHERE consumed=0 AND deleted=0 AND category=? AND color=? GROUP BY category,color ORDER BY category,color',(category,color)).fetchall()
            else:rows=c.execute('SELECT category,color,COUNT(*) count,MIN(production) production,MIN(expiry) expiry,SUM(remaining_weight) weight,SUM(opened) opened FROM cartons WHERE consumed=0 AND deleted=0 GROUP BY category,color ORDER BY category,color').fetchall()
        for r in rows:tree.insert('','end',values=(r['category'],r['color'],r['count'],r['production'] or '—',r['expiry'] or '—',f"{r['weight'] or 0:g} كجم",r['opened'] or 0))
        self.stock_tree=tree; self.button(f,'تغيير حالة مفتوحة للصف المحدد',self.toggle_opened).pack(anchor='e',pady=8)
    def toggle_opened(self):
        sel=self.stock_tree.selection()
        if not sel:return messagebox.showwarning('اختر لونًا','حدد صف الفئة واللون.')
        cat,col=self.stock_tree.item(sel[0],'values')[:2];at=now()
        with connect() as c:rows=c.execute('SELECT id,opened,remaining_weight FROM cartons WHERE category=? AND color=? AND consumed=0 AND deleted=0',(cat,col)).fetchall()
        if not rows:return
        new=0 if all(r['opened'] for r in rows) else 1
        with connect() as c:
            for r in rows:c.execute('UPDATE cartons SET opened=?,updated_at=? WHERE id=?',(new,at,r['id']));c.execute('INSERT INTO movements(carton_id,at,action,weight,user) VALUES(?,?,?,?,?)',(r['id'],at,'تغيير حالة الفتح إلى مفتوحة' if new else 'إلغاء حالة الفتح',f"{r['remaining_weight']:g} كجم",self.current_user()['username']))
        self.refresh_all()
    def refresh_moves(self):
        for w in self.moves_tab.winfo_children():w.destroy()
        f=self.card(self.moves_tab,'سجل الحركات','تاريخ كامل من إضافة الرصيد حتى الاستهلاك.');tree=ttk.Treeview(f,columns=('at','carton','company','color','action','weight','user'),show='headings',height=20)
        for col,title in [('at','التاريخ والوقت'),('carton','الكرتونة'),('company','الشركة'),('color','اللون'),('action','الحركة'),('weight','الوزن'),('user','المستخدم')]:tree.heading(col,text=title)
        tree.pack(fill='both',expand=True)
        with connect() as c:rows=c.execute('SELECT m.*,x.company,x.color FROM movements m JOIN cartons x ON x.id=m.carton_id ORDER BY m.id DESC LIMIT 2000').fetchall()
        for r in rows:tree.insert('','end',values=(r['at'],r['carton_id'],r['company'],r['color'],r['action'],r['weight'],r['user']))

def main():
    root=tk.Tk()
    def show_callback_error(exc_type, exc_value, exc_tb):
        details=''.join(traceback.format_exception(exc_type,exc_value,exc_tb))
        try:
            with open(DATA_DIR/'error.log','a',encoding='utf-8') as log:
                log.write('\n--- '+now()+' ---\n'+details)
        except OSError:
            pass
        messagebox.showerror('تعذر فتح الصفحة','حدث خطأ أثناء بناء الواجهة. أرسل صورة هذه الرسالة أو ملف powder_data/error.log للدعم.\n\n'+details[-1800:])
    root.report_callback_exception=show_callback_error
    PowderApp(root)
    root.mainloop()
if __name__=='__main__':main()
