from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload
from sqlalchemy import event
from sqlalchemy.engine import Engine
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sqlite3
import jdatetime
import bcrypt
import secrets
import os

TEHRAN_TZ = ZoneInfo('Asia/Tehran')

def now_tehran():
    return datetime.now(TEHRAN_TZ).replace(tzinfo=None)

basedir = os.path.abspath(os.path.dirname(__file__))
frontend_dir = os.environ.get('FRONTEND_DIR', os.path.join(basedir, '..', 'frontend'))

app = Flask(__name__, static_folder=frontend_dir, static_url_path='')
CORS(app)

@app.route('/')
def index():
    return send_from_directory(frontend_dir, 'index.html')

data_dir = os.environ.get('DATA_DIR', basedir)
os.makedirs(data_dir, exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(data_dir, "warehouse.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

db = SQLAlchemy(app)

# ── SQLite performance: WAL mode + faster sync ──────────────────────────────
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")       # نوشتن بدون قفل کامل — سریع‌ترین حالت
        cursor.execute("PRAGMA synchronous=NORMAL")      # امنیت کافی + سرعت بیشتر
        cursor.execute("PRAGMA busy_timeout=8000")       # به‌جای خطای فوری «database locked»، تا ۸ ثانیه صبر کن
        cursor.execute("PRAGMA cache_size=-32000")       # 32 MB کش (منفی = کیلوبایت)
        cursor.execute("PRAGMA temp_store=MEMORY")       # temp table در RAM
        cursor.execute("PRAGMA mmap_size=268435456")     # 256MB memory-mapped I/O
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# ═══════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════

class User(db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    display_name  = db.Column(db.String(100))
    role          = db.Column(db.String(20), default='viewer')
    is_active     = db.Column(db.Boolean, default=True, index=True)
    created_at    = db.Column(db.DateTime, default=now_tehran)

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password):
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        last = LoginLog.query.filter_by(user_id=self.id).order_by(LoginLog.logged_in_at.desc()).first()
        return {
            'id': self.id,
            'username': self.username,
            'display_name': self.display_name or self.username,
            'role': self.role,
            'is_active': self.is_active,
            'created_at': jdt.strftime('%Y/%m/%d'),
            'last_login': jdatetime.datetime.fromgregorian(datetime=last.logged_in_at).strftime('%Y/%m/%d %H:%M') if last else '',
        }


class Session(db.Model):
    __tablename__ = 'sessions'
    id         = db.Column(db.Integer, primary_key=True)
    token      = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=now_tehran)
    expires_at = db.Column(db.DateTime, nullable=False)
    user       = db.relationship('User', backref='sessions')

    def is_valid(self):
        return now_tehran() < self.expires_at


class LoginLog(db.Model):
    __tablename__ = 'login_logs'
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    logged_in_at = db.Column(db.DateTime, default=now_tehran, index=True)
    ip_address   = db.Column(db.String(50))
    user         = db.relationship('User', backref='login_logs')


class Warehouse(db.Model):
    __tablename__ = 'warehouses'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    location    = db.Column(db.String(200))
    description = db.Column(db.Text)
    created_at  = db.Column(db.DateTime, default=now_tehran)
    is_active   = db.Column(db.Boolean, default=True, index=True)
    products    = db.relationship('Product', backref='warehouse', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        active_products = [p for p in self.products if p.is_active]
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location or '',
            'description': self.description or '',
            'created_at': jdt.strftime('%Y/%m/%d'),
            'is_active': self.is_active,
            'product_count': len(active_products),
            'total_items': sum(p.quantity for p in active_products),
            'total_value': sum(p.stock_value() for p in active_products),
        }


class Department(db.Model):
    __tablename__ = 'departments'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.Text)
    is_active   = db.Column(db.Boolean, default=True, index=True)
    created_at  = db.Column(db.DateTime, default=now_tehran)

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'is_active': self.is_active,
            'created_at': jdt.strftime('%Y/%m/%d'),
        }


class Product(db.Model):
    __tablename__ = 'products'
    id            = db.Column(db.Integer, primary_key=True)
    warehouse_id  = db.Column(db.Integer, db.ForeignKey('warehouses.id'), nullable=False, index=True)
    name          = db.Column(db.String(200), nullable=False, index=True)
    sku           = db.Column(db.String(100), index=True)
    category      = db.Column(db.String(100))
    quantity      = db.Column(db.Float, default=0)
    unit          = db.Column(db.String(50), default='عدد')
    unit_price    = db.Column(db.Float, default=0)   # آخرین قیمت خرید
    avg_cost      = db.Column(db.Float, default=0)   # میانگین موزون هزینه
    min_stock     = db.Column(db.Float, default=0)
    description   = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, default=now_tehran)
    updated_at    = db.Column(db.DateTime, default=now_tehran, onupdate=now_tehran)
    is_active     = db.Column(db.Boolean, default=True, index=True)
    manual_status = db.Column(db.String(20), default=None)
    transactions  = db.relationship('Transaction', backref='product', lazy=True, cascade='all, delete-orphan')

    def cost_basis(self):
        """قیمت مبنای ارزش‌گذاری: میانگین موزون، وگرنه آخرین قیمت خرید"""
        if self.avg_cost and self.avg_cost > 0:
            return self.avg_cost
        return self.unit_price or 0

    def stock_value(self):
        return (self.quantity or 0) * self.cost_basis()

    def apply_purchase_cost(self, qty, unit_price, before_qty):
        """به‌روزرسانی آخرین قیمت و میانگین موزون بعد از ورود خرید"""
        price = float(unit_price or 0)
        qty = float(qty or 0)
        before = max(float(before_qty or 0), 0)
        if qty <= 0:
            return
        self.unit_price = price
        old_avg = self.avg_cost if self.avg_cost and self.avg_cost > 0 else (self.unit_price or 0)
        if before <= 0 or old_avg <= 0:
            self.avg_cost = price
        else:
            self.avg_cost = ((before * old_avg) + (qty * price)) / (before + qty)

    def get_status(self):
        if self.manual_status == 'unavailable': return 'out'
        if self.manual_status == 'low':         return 'low'
        if self.manual_status == 'available':   return 'normal'
        if self.quantity <= 0:                  return 'out'
        if self.min_stock and self.quantity <= self.min_stock: return 'low'
        return 'normal'

    def to_dict(self):
        jdt_c = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        jdt_u = jdatetime.datetime.fromgregorian(datetime=self.updated_at)
        cost = self.cost_basis()
        return {
            'id': self.id,
            'warehouse_id': self.warehouse_id,
            'warehouse_name': self.warehouse.name if self.warehouse else '',
            'name': self.name,
            'sku': self.sku or '',
            'category': self.category or '',
            'quantity': self.quantity,
            'unit': self.unit,
            'unit_price': self.unit_price or 0,
            'avg_cost': self.avg_cost or 0,
            'cost_basis': cost,
            'min_stock': self.min_stock or 0,
            'description': self.description or '',
            'created_at': jdt_c.strftime('%Y/%m/%d'),
            'updated_at': jdt_u.strftime('%Y/%m/%d %H:%M'),
            'is_active': self.is_active,
            'status': self.get_status(),
            'manual_status': self.manual_status or '',
            'total_value': self.stock_value(),
        }


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id            = db.Column(db.Integer, primary_key=True)
    product_id    = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True, index=True)
    type          = db.Column(db.String(20), nullable=False, index=True)
    quantity      = db.Column(db.Float, nullable=False)
    before_qty    = db.Column(db.Float)
    after_qty     = db.Column(db.Float)
    unit_price    = db.Column(db.Float)   # قیمت واحد این حرکت (برای ورود = قیمت خرید)
    total_cost    = db.Column(db.Float)   # مبلغ کل این حرکت
    note          = db.Column(db.Text)
    ref_number    = db.Column(db.String(100))
    created_at    = db.Column(db.DateTime, default=now_tehran, index=True)
    created_by    = db.Column(db.String(100), default='کاربر')
    is_reversed   = db.Column(db.Boolean, default=False, index=True)
    reversed_by_id= db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=True)
    department    = db.relationship('Department', backref='transactions')

    def movement_meta(self):
        """برچسب شفاف برای گزارش‌ها — بدون کلمه مبهم «تنظیم»"""
        if self.type == 'in':
            if self.reversed_by_id:
                return 'return', '↩️ بازگشت به انبار'
            note = (self.note or '').strip()
            if note == 'موجودی اولیه':
                return 'initial', '📦 موجودی اولیه'
            return 'purchase', '⬆️ ورود خرید'
        if self.type == 'out':
            if self.department_id:
                if self.is_reversed:
                    return 'dispatch_reversed', '⬇️ خروج به بخش (بازگشت‌خورده)'
                return 'dispatch', '⬇️ خروج به بخش'
            return 'issue', '⬇️ خروج از انبار'
        before = self.before_qty if self.before_qty is not None else 0
        after = self.after_qty if self.after_qty is not None else self.quantity
        delta = (after or 0) - (before or 0)
        if delta > 0:
            return 'correction_up', '📈 افزایش موجودی'
        if delta < 0:
            return 'correction_down', '📉 کاهش موجودی'
        return 'correction', '📝 ثبت موجودی'

    def signed_delta(self):
        if self.type == 'in':
            return self.quantity
        if self.type == 'out':
            return -self.quantity
        before = self.before_qty if self.before_qty is not None else 0
        after = self.after_qty if self.after_qty is not None else self.quantity
        return (after or 0) - (before or 0)

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        kind, label = self.movement_meta()
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'warehouse_id': self.product.warehouse_id if self.product else None,
            'warehouse_name': self.product.warehouse.name if self.product and self.product.warehouse else '',
            'department_id': self.department_id,
            'department_name': self.department.name if self.department else '',
            'type': self.type,
            'movement_kind': kind,
            'movement_label': label,
            'quantity': self.quantity,
            'delta': self.signed_delta(),
            'before_qty': self.before_qty,
            'after_qty': self.after_qty,
            'unit_price': self.unit_price if self.unit_price is not None else None,
            'total_cost': self.total_cost if self.total_cost is not None else None,
            'note': self.note or '',
            'ref_number': self.ref_number or '',
            'created_at': jdt.strftime('%Y/%m/%d %H:%M'),
            'created_by': self.created_by,
            'is_reversed': self.is_reversed or False,
            'reversed_by_id': self.reversed_by_id,
            'unit_name': self.product.unit if self.product else '',
        }


# ═══════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════

SESSION_HOURS = 8

def get_current_user():
    token = request.headers.get('X-Auth-Token') or request.args.get('token')
    if not token: return None
    sess = Session.query.filter_by(token=token).first()
    if not sess or not sess.is_valid(): return None
    return sess.user

def require_auth(role=None):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user or not user.is_active:
                return jsonify({'error': 'لاگین نشدید', 'code': 'UNAUTHORIZED'}), 401
            if role == 'admin' and user.role != 'admin':
                return jsonify({'error': 'دسترسی ندارید', 'code': 'FORBIDDEN'}), 403
            if role == 'operator' and user.role not in ('admin', 'operator'):
                return jsonify({'error': 'دسترسی ندارید', 'code': 'FORBIDDEN'}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


def parse_date_arg(value, end_of_day=False):
    """پارس تاریخ از کوئری‌استرینگ (ISO یا YYYY-MM-DD)"""
    if not value:
        return None
    raw = str(value).strip().replace('Z', '')
    try:
        if 'T' in raw:
            return datetime.fromisoformat(raw)
        dt = datetime.fromisoformat(raw)
        if end_of_day:
            return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    except Exception:
        return None


def apply_created_at_range(query, model=Transaction):
    start = parse_date_arg(request.args.get('from_date', '').strip(), end_of_day=False)
    end = parse_date_arg(request.args.get('to_date', '').strip(), end_of_day=True)
    if start:
        query = query.filter(model.created_at >= start)
    if end:
        query = query.filter(model.created_at <= end)
    return query, start, end


# ═══════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'نام کاربری و رمز عبور الزامی است'}), 400
    user = User.query.filter_by(username=username, is_active=True).first()
    if not user or not user.check_password(password):
        return jsonify({'error': 'نام کاربری یا رمز عبور اشتباه است'}), 401
    token = secrets.token_hex(32)
    sess = Session(token=token, user_id=user.id, expires_at=now_tehran() + timedelta(hours=SESSION_HOURS))
    db.session.add(sess)
    db.session.add(LoginLog(user_id=user.id, ip_address=request.remote_addr))
    db.session.commit()
    return jsonify({'token': token, 'user': user.to_dict(), 'expires_in_hours': SESSION_HOURS})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    token = request.headers.get('X-Auth-Token')
    if token:
        Session.query.filter_by(token=token).delete()
        db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/auth/me', methods=['GET'])
def me():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'لاگین نشدید', 'code': 'UNAUTHORIZED'}), 401
    return jsonify(user.to_dict())

@app.route('/api/auth/change-password', methods=['POST'])
@require_auth()
def change_password():
    user = get_current_user()
    data = request.json or {}
    if not user.check_password(data.get('old_password', '')):
        return jsonify({'error': 'رمز عبور فعلی اشتباه است'}), 400
    new_pass = data.get('new_password', '')
    if len(new_pass) < 4:
        return jsonify({'error': 'رمز عبور جدید باید حداقل ۴ کاراکتر باشد'}), 400
    user.set_password(new_pass)
    db.session.commit()
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/users', methods=['GET'])
@require_auth('admin')
def get_users():
    users = User.query.order_by(User.id.desc()).all()
    # یک query برای آخرین لاگین همه کاربران — نه N query جداگانه
    user_ids = [u.id for u in users]
    last_logins = {}
    if user_ids:
        from sqlalchemy import func
        rows = (db.session.query(LoginLog.user_id, func.max(LoginLog.logged_in_at))
                .filter(LoginLog.user_id.in_(user_ids))
                .group_by(LoginLog.user_id).all())
        last_logins = {uid: dt for uid, dt in rows}

    result = []
    for u in users:
        jdt = jdatetime.datetime.fromgregorian(datetime=u.created_at)
        last_dt = last_logins.get(u.id)
        result.append({
            'id': u.id,
            'username': u.username,
            'display_name': u.display_name or u.username,
            'role': u.role,
            'is_active': u.is_active,
            'created_at': jdt.strftime('%Y/%m/%d'),
            'last_login': jdatetime.datetime.fromgregorian(datetime=last_dt).strftime('%Y/%m/%d %H:%M') if last_dt else '',
        })
    return jsonify(result)

@app.route('/api/users', methods=['POST'])
@require_auth('admin')
def create_user():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role     = data.get('role', 'viewer')
    if not username or not password:
        return jsonify({'error': 'نام کاربری و رمز عبور الزامی است'}), 400
    if role not in ('admin', 'operator', 'viewer'):
        return jsonify({'error': 'نقش نامعتبر است'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'این نام کاربری قبلاً ثبت شده'}), 400
    u = User(username=username, role=role, display_name=data.get('display_name', ''))
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return jsonify(u.to_dict()), 201

@app.route('/api/users/<int:uid>', methods=['PUT'])
@require_auth('admin')
def update_user(uid):
    current = get_current_user()
    user = User.query.get_or_404(uid)
    data = request.json or {}
    if 'display_name' in data: user.display_name = data['display_name']
    if 'role' in data and data['role'] in ('admin', 'operator', 'viewer'):
        if user.id == current.id and data['role'] != 'admin':
            return jsonify({'error': 'نمیتوانید نقش خودتان را تغییر دهید'}), 400
        user.role = data['role']
    if 'is_active' in data:
        if user.id == current.id:
            return jsonify({'error': 'نمیتوانید حساب خودتان را غیرفعال کنید'}), 400
        user.is_active = data['is_active']
    if data.get('password'):
        if len(data['password']) < 4:
            return jsonify({'error': 'رمز عبور باید حداقل ۴ کاراکتر باشد'}), 400
        user.set_password(data['password'])
    db.session.commit()
    return jsonify(user.to_dict())

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@require_auth('admin')
def delete_user(uid):
    current = get_current_user()
    user = User.query.get_or_404(uid)
    if user.id == current.id:
        return jsonify({'error': 'نمیتوانید حساب خودتان را حذف کنید'}), 400
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/login-logs', methods=['GET'])
@require_auth('admin')
def get_all_login_logs():
    logs = LoginLog.query.options(joinedload(LoginLog.user)).order_by(LoginLog.logged_in_at.desc()).limit(200).all()
    result = []
    for log in logs:
        jdt = jdatetime.datetime.fromgregorian(datetime=log.logged_in_at)
        result.append({
            'id': log.id,
            'username': log.user.username if log.user else '',
            'display_name': (log.user.display_name or log.user.username) if log.user else '',
            'logged_in_at': jdt.strftime('%Y/%m/%d %H:%M'),
            'ip_address': log.ip_address or '',
        })
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════
# WAREHOUSE ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/warehouses', methods=['GET'])
@require_auth()
def get_warehouses():
    # joinedload جلوگیری می‌کند از N+1 query برای products
    whs = Warehouse.query.filter_by(is_active=True).options(
        joinedload(Warehouse.products)
    ).all()
    return jsonify([w.to_dict() for w in whs])

@app.route('/api/warehouses', methods=['POST'])
@require_auth('operator')
def create_warehouse():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'error': 'نام انبار الزامی است'}), 400
    wh = Warehouse(name=data['name'], location=data.get('location', ''), description=data.get('description', ''))
    db.session.add(wh)
    db.session.commit()
    return jsonify(wh.to_dict()), 201

@app.route('/api/warehouses/<int:wid>', methods=['PUT'])
@require_auth('operator')
def update_warehouse(wid):
    wh = Warehouse.query.get_or_404(wid)
    data = request.json or {}
    if 'name' in data:        wh.name = data['name']
    if 'location' in data:    wh.location = data['location']
    if 'description' in data: wh.description = data['description']
    db.session.commit()
    return jsonify(wh.to_dict())

@app.route('/api/warehouses/<int:wid>', methods=['DELETE'])
@require_auth('admin')
def delete_warehouse(wid):
    wh = Warehouse.query.get_or_404(wid)
    wh.is_active = False
    db.session.commit()
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════════
# DEPARTMENT ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/departments', methods=['GET'])
@require_auth()
def get_departments():
    depts = Department.query.filter_by(is_active=True).order_by(Department.name).all()
    return jsonify([d.to_dict() for d in depts])

@app.route('/api/departments', methods=['POST'])
@require_auth('operator')
def create_department():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'نام بخش الزامی است'}), 400
    dept = Department(name=name, description=data.get('description', ''))
    db.session.add(dept)
    db.session.commit()
    return jsonify(dept.to_dict()), 201

@app.route('/api/departments/<int:did>', methods=['PUT'])
@require_auth('operator')
def update_department(did):
    dept = Department.query.get_or_404(did)
    data = request.json or {}
    if 'name' in data:        dept.name = data['name'].strip()
    if 'description' in data: dept.description = data['description']
    if 'is_active' in data:   dept.is_active = data['is_active']
    db.session.commit()
    return jsonify(dept.to_dict())

@app.route('/api/departments/<int:did>', methods=['DELETE'])
@require_auth('admin')
def delete_department(did):
    dept = Department.query.get_or_404(did)
    dept.is_active = False
    db.session.commit()
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════════
# PRODUCT ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/warehouses/<int:wid>/products', methods=['GET'])
@require_auth()
def get_products(wid):
    prods = (Product.query
             .options(joinedload(Product.warehouse))
             .filter_by(warehouse_id=wid, is_active=True)
             .order_by(Product.id.desc()).all())
    return jsonify([p.to_dict() for p in prods])

@app.route('/api/products', methods=['GET'])
@require_auth()
def get_all_products():
    search = request.args.get('search', '')
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(max(request.args.get('per_page', 50, type=int), 1), 200)

    query = Product.query.options(joinedload(Product.warehouse)).filter_by(is_active=True)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    query = query.order_by(Product.id.desc())

    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'items': [p.to_dict() for p in items],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, -(-total // per_page)),
    })

@app.route('/api/products/<int:pid>', methods=['GET'])
@require_auth()
def get_product(pid):
    p = Product.query.options(joinedload(Product.warehouse)).get_or_404(pid)
    return jsonify(p.to_dict())

@app.route('/api/products', methods=['POST'])
@require_auth('operator')
def create_product():
    """تعریف کالا فقط — موجودی و خرید از طریق ورود جدا ثبت می‌شود"""
    data = request.json or {}
    if not data.get('name') or not data.get('warehouse_id'):
        return jsonify({'error': 'نام کالا و انبار الزامی است'}), 400
    p = Product(
        warehouse_id=data['warehouse_id'], name=data['name'],
        sku=data.get('sku', ''), category=data.get('category', ''),
        quantity=0, unit=data.get('unit', 'عدد'),
        unit_price=0, avg_cost=0,
        min_stock=float(data.get('min_stock', 0) or 0),
        description=data.get('description', ''),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify(p.to_dict()), 201

@app.route('/api/products/<int:pid>', methods=['PUT'])
@require_auth('operator')
def update_product(pid):
    """ویرایش مشخصات کالا — تغییر موجودی از این مسیر مجاز نیست"""
    p = Product.query.get_or_404(pid)
    data = request.json or {}
    for f in ['name', 'sku', 'category', 'unit', 'min_stock', 'description']:
        if f in data:
            setattr(p, f, data[f])
    # قیمت واحد فقط از ورود خرید به‌روز می‌شود؛ اگر صریح فرستاده شد نادیده بگیر مگر admin path جدا
    p.updated_at = now_tehran()
    db.session.commit()
    return jsonify(p.to_dict())

@app.route('/api/products/<int:pid>', methods=['DELETE'])
@require_auth('operator')
def delete_product(pid):
    p = Product.query.get_or_404(pid)
    p.is_active = False
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/products/<int:pid>/status', methods=['PATCH'])
@require_auth('operator')
def set_product_status(pid):
    p = Product.query.get_or_404(pid)
    data = request.json or {}
    s = data.get('manual_status')
    if s not in ('available', 'unavailable', 'low', None, ''):
        return jsonify({'error': 'وضعیت نامعتبر'}), 400
    p.manual_status = s if s else None
    p.updated_at = now_tehran()
    db.session.commit()
    return jsonify(p.to_dict())


@app.route('/api/products/<int:pid>/card', methods=['GET'])
@require_auth()
def product_card(pid):
    """کارت کامل کالا: مشخصات + حرکات + گزارش خریدها"""
    p = Product.query.options(
        joinedload(Product.warehouse),
    ).get_or_404(pid)
    txs = (Transaction.query
           .options(joinedload(Transaction.department))
           .filter_by(product_id=pid)
           .order_by(Transaction.created_at.desc(), Transaction.id.desc())
           .all())

    total_in = 0.0
    total_out = 0.0
    purchase_qty = 0.0
    purchase_cost = 0.0
    purchases = []

    for tx in txs:
        delta = tx.signed_delta()
        if delta > 0:
            total_in += delta
        elif delta < 0:
            total_out += abs(delta)
        kind, _ = tx.movement_meta()
        if kind in ('purchase', 'initial') and tx.type == 'in':
            up = tx.unit_price if tx.unit_price is not None else (p.unit_price or 0)
            cost = tx.total_cost if tx.total_cost is not None else (tx.quantity * (up or 0))
            purchase_qty += tx.quantity
            purchase_cost += cost or 0
            purchases.append(tx.to_dict())

    return jsonify({
        'product': p.to_dict(),
        'summary': {
            'total_in': total_in,
            'total_out': total_out,
            'purchase_count': len(purchases),
            'purchase_qty': purchase_qty,
            'purchase_cost': purchase_cost,
            'avg_purchase_price': (purchase_cost / purchase_qty) if purchase_qty else 0,
            'current_value': p.stock_value(),
            'movement_count': len(txs),
        },
        'purchases': purchases,
        'movements': [t.to_dict() for t in txs],
    })


# ═══════════════════════════════════════════════════════════════════
# TRANSACTION ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/products/<int:pid>/transactions', methods=['POST'])
@require_auth('operator')
def add_transaction(pid):
    p = Product.query.get_or_404(pid)
    user = get_current_user()
    data = request.json or {}
    tx_type = data.get('type')
    qty = float(data.get('quantity', 0) or 0)
    if tx_type not in ('in', 'out', 'adjust'):
        return jsonify({'error': 'نوع حرکت نامعتبر'}), 400
    if qty <= 0:
        return jsonify({'error': 'مقدار باید بیشتر از صفر باشد'}), 400

    before = p.quantity
    unit_price = data.get('unit_price', None)
    if unit_price is not None and unit_price != '':
        unit_price = float(unit_price)
    else:
        unit_price = None

    if tx_type == 'in':
        if unit_price is None:
            return jsonify({'error': 'برای ورود کالا، قیمت خرید این محموله الزامی است'}), 400
        if unit_price < 0:
            return jsonify({'error': 'قیمت نمی‌تواند منفی باشد'}), 400
        p.apply_purchase_cost(qty, unit_price, before)
        p.quantity += qty
        total_cost = qty * unit_price
    elif tx_type == 'out':
        if p.quantity < qty:
            return jsonify({'error': 'موجودی کافی نیست'}), 400
        cost = p.cost_basis()
        unit_price = cost
        total_cost = qty * cost
        p.quantity -= qty
    else:
        # اصلاح موجودی — مقدار واردشده = موجودی جدید
        new_qty = qty
        delta = new_qty - before
        cost = p.cost_basis()
        unit_price = cost if cost else None
        total_cost = abs(delta) * cost if cost and delta != 0 else None
        p.quantity = new_qty

    manual_status = data.get('manual_status')
    if manual_status in ('available', 'unavailable', 'low'):
        p.manual_status = manual_status
    elif manual_status is None:
        p.manual_status = None
    p.updated_at = now_tehran()

    tx_date = now_tehran()
    if data.get('custom_date'):
        try:
            tx_date = datetime.fromisoformat(data['custom_date'])
        except Exception:
            pass

    tx = Transaction(
        product_id=pid, type=tx_type, quantity=qty,
        before_qty=before, after_qty=p.quantity,
        unit_price=unit_price, total_cost=total_cost,
        note=data.get('note', ''), ref_number=data.get('ref_number', ''),
        created_by=user.display_name or user.username, created_at=tx_date,
    )
    db.session.add(tx)
    db.session.commit()
    return jsonify({'product': p.to_dict(), 'transaction': tx.to_dict()}), 201

@app.route('/api/transactions', methods=['GET'])
@require_auth()
def get_all_transactions():
    search  = request.args.get('search', '').strip()
    note_q  = request.args.get('note', '').strip()
    tx_type = request.args.get('type', '').strip()
    wh_id   = request.args.get('warehouse_id', '').strip()
    dept_id = request.args.get('department_id', '').strip()
    product_id = request.args.get('product_id', '').strip()

    query = (Transaction.query
             .join(Product, Transaction.product_id == Product.id)
             .options(
                 joinedload(Transaction.product).joinedload(Product.warehouse),
                 joinedload(Transaction.department),
             ))
    if search:
        query = query.filter(db.or_(
            Product.name.ilike(f'%{search}%'),
            Transaction.note.ilike(f'%{search}%'),
            Transaction.ref_number.ilike(f'%{search}%'),
        ))
    if note_q:
        query = query.filter(Transaction.note.ilike(f'%{note_q}%'))
    if tx_type in ('in', 'out', 'adjust'):
        query = query.filter(Transaction.type == tx_type)
    if wh_id:
        query = query.filter(Product.warehouse_id == int(wh_id))
    if dept_id:
        query = query.filter(Transaction.department_id == int(dept_id))
    if product_id:
        query = query.filter(Transaction.product_id == int(product_id))
    query, _, _ = apply_created_at_range(query)
    txs = query.order_by(Transaction.created_at.desc()).limit(500).all()
    return jsonify([t.to_dict() for t in txs])


# ═══════════════════════════════════════════════════════════════════
# DISPATCH ROUTES (خروج رسمی)
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/dispatch', methods=['POST'])
@require_auth('operator')
def create_dispatch():
    user = get_current_user()
    data = request.json or {}
    product_id    = data.get('product_id')
    department_id = data.get('department_id')
    quantity      = float(data.get('quantity', 0))
    note          = (data.get('note') or '').strip()
    if not product_id or not department_id:
        return jsonify({'error': 'کالا و بخش مقصد الزامی است'}), 400
    if quantity <= 0:
        return jsonify({'error': 'مقدار باید بیشتر از صفر باشد'}), 400
    product = Product.query.get_or_404(product_id)
    dept    = Department.query.get_or_404(department_id)
    if not dept.is_active:
        return jsonify({'error': 'این بخش غیرفعال است'}), 400
    if product.quantity < quantity:
        return jsonify({'error': f'موجودی کافی نیست (موجودی: {product.quantity} {product.unit})'}), 400
    before = product.quantity
    cost = product.cost_basis()
    product.quantity -= quantity
    product.updated_at = now_tehran()
    tx = Transaction(
        product_id=product_id, department_id=department_id,
        type='out', quantity=quantity,
        before_qty=before, after_qty=product.quantity,
        unit_price=cost, total_cost=quantity * cost,
        note=note, created_by=user.display_name or user.username,
    )
    db.session.add(tx)
    db.session.commit()
    return jsonify({'transaction': tx.to_dict(), 'product': product.to_dict()}), 201

@app.route('/api/dispatch/<int:tx_id>/reverse', methods=['POST'])
@require_auth('operator')
def reverse_dispatch(tx_id):
    user = get_current_user()
    tx = Transaction.query.get_or_404(tx_id)

    if tx.type != 'out' or tx.department_id is None:
        return jsonify({'error': 'فقط خروج رسمی قابل بازگشت است'}), 400
    if tx.is_reversed:
        return jsonify({'error': 'این تراکنش قبلاً بازگشت خورده'}), 400

    product = Product.query.get_or_404(tx.product_id)

    before = product.quantity
    product.quantity += tx.quantity
    product.updated_at = now_tehran()

    # بازگشت با همان قیمت خروج (هزینه برگشتی)
    unit_price = tx.unit_price if tx.unit_price is not None else product.cost_basis()
    total_cost = tx.total_cost if tx.total_cost is not None else (tx.quantity * (unit_price or 0))

    reverse_tx = Transaction(
        product_id    = tx.product_id,
        department_id = tx.department_id,
        type          = 'in',
        quantity      = tx.quantity,
        before_qty    = before,
        after_qty     = product.quantity,
        unit_price    = unit_price,
        total_cost    = total_cost,
        note          = f'بازگشت خروج رسمی (شناسه تراکنش: {tx_id})',
        created_by    = user.display_name or user.username,
        reversed_by_id= tx_id,
    )
    db.session.add(reverse_tx)

    tx.is_reversed = True
    db.session.commit()

    return jsonify({
        'ok': True,
        'reverse_transaction': reverse_tx.to_dict(),
        'product': product.to_dict(),
    })


@app.route('/api/dispatch/report', methods=['GET'])
@require_auth()
def dispatch_report():
    dept_id   = request.args.get('department_id', '').strip()
    product_q = request.args.get('product', '').strip()
    note_q    = request.args.get('note', '').strip()
    wh_id     = request.args.get('warehouse_id', '').strip()
    query = (Transaction.query
             .join(Product, Transaction.product_id == Product.id)
             .options(
                 joinedload(Transaction.product).joinedload(Product.warehouse),
                 joinedload(Transaction.department),
             )
             .filter(Transaction.type == 'out')
             .filter(Transaction.department_id != None))
    if dept_id:   query = query.filter(Transaction.department_id == int(dept_id))
    if wh_id:     query = query.filter(Product.warehouse_id == int(wh_id))
    if product_q: query = query.filter(Product.name.ilike(f'%{product_q}%'))
    if note_q:    query = query.filter(Transaction.note.ilike(f'%{note_q}%'))
    query, _, _ = apply_created_at_range(query)
    txs = query.order_by(Transaction.created_at.desc()).limit(500).all()
    dept_summary = {}
    for tx in txs:
        dn = tx.department.name if tx.department else ''
        if dn not in dept_summary:
            dept_summary[dn] = {'count': 0, 'items': 0}
        dept_summary[dn]['count'] += 1
        dept_summary[dn]['items'] += tx.quantity
    now_j = jdatetime.datetime.fromgregorian(datetime=now_tehran()).strftime('%Y/%m/%d %H:%M')
    return jsonify({'generated_at': now_j, 'transactions': [t.to_dict() for t in txs], 'dept_summary': dept_summary})


# ═══════════════════════════════════════════════════════════════════
# DASHBOARD & REPORTS
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/dashboard', methods=['GET'])
@require_auth()
def dashboard():
    from sqlalchemy import func, and_
    # جمع‌بندی‌ها را خود دیتابیس حساب می‌کند — به‌جای لود کردن همه‌ی کالاها در پایتون
    # (با رشد تعداد کالاها دیگر کند نمی‌شود)
    low_stock_cond = and_(Product.is_active == True, Product.min_stock > 0,
                           Product.quantity > 0, Product.quantity <= Product.min_stock)
    out_stock_cond = and_(Product.is_active == True, Product.quantity <= 0)

    warehouse_count    = db.session.query(func.count(Warehouse.id)).filter(Warehouse.is_active == True).scalar()
    product_count      = db.session.query(func.count(Product.id)).filter(Product.is_active == True).scalar()
    low_stock_count    = db.session.query(func.count(Product.id)).filter(low_stock_cond).scalar()
    out_of_stock_count = db.session.query(func.count(Product.id)).filter(out_stock_cond).scalar()
    # ارزش کل بر اساس میانگین موزون (یا آخرین قیمت اگر میانگین نبود)
    active_products = Product.query.filter_by(is_active=True).all()
    total_value = sum(p.stock_value() for p in active_products)

    low_stock    = Product.query.filter(low_stock_cond).limit(5).all()
    out_of_stock = Product.query.filter(out_stock_cond).limit(5).all()
    recent_txs   = (Transaction.query
                    .options(
                        joinedload(Transaction.product).joinedload(Product.warehouse),
                        joinedload(Transaction.department),
                    )
                    .order_by(Transaction.created_at.desc()).limit(10).all())
    return jsonify({
        'warehouse_count':   warehouse_count,
        'product_count':     product_count,
        'low_stock_count':   low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'total_value':       total_value,
        'low_stock':         [p.to_dict() for p in low_stock],
        'out_of_stock':      [p.to_dict() for p in out_of_stock],
        'recent_transactions': [t.to_dict() for t in recent_txs],
    })

@app.route('/api/report/inventory', methods=['GET'])
@require_auth()
def inventory_report():
    wid = request.args.get('warehouse_id')
    status_filter = request.args.get('status')
    search = (request.args.get('search') or request.args.get('product') or '').strip()
    query = Product.query.options(joinedload(Product.warehouse)).filter_by(is_active=True)
    if wid:
        query = query.filter_by(warehouse_id=int(wid))
    if search:
        query = query.filter(db.or_(
            Product.name.ilike(f'%{search}%'),
            Product.sku.ilike(f'%{search}%'),
            Product.category.ilike(f'%{search}%'),
        ))
    products = query.order_by(Product.name.asc()).all()
    product_dicts = [p.to_dict() for p in products]
    if status_filter in ('normal', 'low', 'out'):
        product_dicts = [p for p in product_dicts if p['status'] == status_filter]
    now_j = jdatetime.datetime.fromgregorian(datetime=now_tehran()).strftime('%Y/%m/%d %H:%M')
    return jsonify({
        'generated_at': now_j,
        'products': product_dicts,
        'total_value': sum(p['total_value'] for p in product_dicts),
    })


# ═══════════════════════════════════════════════════════════════════
# INIT
# ═══════════════════════════════════════════════════════════════════

def migrate():
    """اضافه کردن ستون‌های جدید و مهاجرت نرم دیتای قدیمی"""
    with db.engine.connect() as conn:
        for sql, msg in [
            ('ALTER TABLE transactions ADD COLUMN department_id INTEGER REFERENCES departments(id)', 'department_id'),
            ('ALTER TABLE transactions ADD COLUMN is_reversed BOOLEAN DEFAULT 0', 'is_reversed'),
            ('ALTER TABLE transactions ADD COLUMN reversed_by_id INTEGER', 'reversed_by_id'),
            ('ALTER TABLE transactions ADD COLUMN unit_price REAL', 'tx.unit_price'),
            ('ALTER TABLE transactions ADD COLUMN total_cost REAL', 'tx.total_cost'),
            ('ALTER TABLE products ADD COLUMN avg_cost REAL DEFAULT 0', 'products.avg_cost'),
        ]:
            try:
                conn.execute(db.text(sql))
                conn.commit()
                print(f'[MIGRATE] {msg} added')
            except Exception:
                pass  # قبلاً اضافه شده

    # مهاجرت یک‌باره دیتا: قیمت‌های قدیمی را روی حرکات و میانگین کالا بنشان
    try:
        _backfill_ledger_from_legacy()
    except Exception as e:
        print(f'[MIGRATE] backfill skipped: {e}')
        db.session.rollback()


def _backfill_ledger_from_legacy():
    """
    دیتای قبلی را با منطق جدید هم‌راستا می‌کند بدون از دست رفتن موجودی:
    - avg_cost کالا از unit_price فعلی پر می‌شود اگر خالی باشد
    - ورودهای بدون قیمت، قیمت کالای همان زمان (فعلی) را می‌گیرند
    - خروج‌های بدون قیمت، با cost_basis کالا پر می‌شوند
    """
    flag_path = os.path.join(data_dir, '.ledger_backfill_v1')
    if os.path.exists(flag_path):
        return

    products = Product.query.all()
    updated_products = 0
    updated_txs = 0

    for p in products:
        changed = False
        if (not p.avg_cost or p.avg_cost <= 0) and (p.unit_price or 0) > 0:
            p.avg_cost = p.unit_price
            changed = True
        if changed:
            updated_products += 1

        cost = p.cost_basis()
        for tx in Transaction.query.filter_by(product_id=p.id).all():
            if tx.unit_price is not None and tx.total_cost is not None:
                continue
            if tx.type == 'in':
                price = cost if cost > 0 else (p.unit_price or 0)
                tx.unit_price = price
                tx.total_cost = (tx.quantity or 0) * price
                updated_txs += 1
            elif tx.type == 'out':
                price = cost if cost > 0 else (p.unit_price or 0)
                tx.unit_price = price
                tx.total_cost = (tx.quantity or 0) * price
                updated_txs += 1
            else:
                # اصلاح موجودی — قیمت مبنا برای گزارش ارزش
                price = cost if cost > 0 else None
                if price is not None:
                    before = tx.before_qty if tx.before_qty is not None else 0
                    after = tx.after_qty if tx.after_qty is not None else tx.quantity
                    delta = abs((after or 0) - (before or 0))
                    tx.unit_price = price
                    tx.total_cost = delta * price
                    updated_txs += 1

    db.session.commit()
    with open(flag_path, 'w', encoding='utf-8') as f:
        f.write(f'products={updated_products};txs={updated_txs}\n')
    print(f'[MIGRATE] ledger backfill done: products={updated_products}, txs={updated_txs}')
def init_admin():
    if User.query.count() == 0:
        admin_user = os.environ.get('ADMIN_USERNAME', 'admin')
        admin_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        u = User(username=admin_user, display_name='مدیر سیستم', role='admin')
        u.set_password(admin_pass)
        db.session.add(u)
        db.session.commit()
        print(f'[INIT] admin user created: {admin_user}')

def cleanup_sessions():
    """حذف session های منقضی‌شده"""
    try:
        deleted = Session.query.filter(Session.expires_at < now_tehran()).delete()
        db.session.commit()
        if deleted:
            print(f'[CLEANUP] {deleted} expired sessions removed')
    except Exception as e:
        print(f'[CLEANUP] error: {e}')
        db.session.rollback()

def auto_backup():
    import shutil
    backup_dir = os.path.join(data_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    db_path = os.path.join(data_dir, 'warehouse.db')
    if not os.path.exists(db_path):
        return
    today = jdatetime.date.today().strftime('%Y-%m-%d')
    backup_path = os.path.join(backup_dir, f'warehouse_{today}.db')
    if not os.path.exists(backup_path):
        shutil.copy2(db_path, backup_path)
        print(f'[BACKUP] created: warehouse_{today}.db')
        backups = sorted([
            f for f in os.listdir(backup_dir)
            if f.startswith('warehouse_') and f.endswith('.db')
        ])
        for bk in backups[:-30]:
            os.remove(os.path.join(backup_dir, bk))
            print(f'[BACKUP] removed old: {bk}')

def start_daily_scheduler():
    import threading, time
    def loop():
        while True:
            now = now_tehran()
            next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
            if now >= next_run:
                next_run = next_run + timedelta(days=1)
            wait = (next_run - now).total_seconds()
            print(f'[SCHEDULER] next run in {int(wait/3600)}h {int((wait%3600)/60)}m')
            time.sleep(wait)
            with app.app_context():
                cleanup_sessions()
                auto_backup()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print('[SCHEDULER] daily maintenance started')

# ─── Error Handlers ────────────────────────────────────────────────────────────

@app.errorhandler(401)
def unauthorized(e):
    return jsonify({'error': 'لاگین نشدید', 'code': 'UNAUTHORIZED'}), 401

@app.errorhandler(403)
def forbidden(e):
    return jsonify({'error': 'دسترسی ندارید', 'code': 'FORBIDDEN'}), 403

@app.errorhandler(404)
def not_found(e):
    # اگه API بود JSON برگردون، وگرنه index.html
    if request.path.startswith('/api/'):
        return jsonify({'error': 'یافت نشد'}), 404
    return send_from_directory(frontend_dir, 'index.html')

@app.errorhandler(500)
def server_error(e):
    db.session.rollback()
    print(f'[ERROR 500] {request.path}: {e}')
    return jsonify({'error': 'خطای سرور، لطفاً دوباره امتحان کنید'}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    db.session.rollback()
    print(f'[UNHANDLED] {request.path}: {type(e).__name__}: {e}')
    if request.path.startswith('/api/'):
        return jsonify({'error': 'خطای داخلی سرور'}), 500
    return send_from_directory(frontend_dir, 'index.html')

# ─── Startup ────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()
    migrate()
    init_admin()
    cleanup_sessions()
    auto_backup()
    start_daily_scheduler()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
