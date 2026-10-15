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
import json

# بخش‌های قابل‌دسترسی در منوی برنامه (permissions)
APP_SECTIONS = (
    'dashboard',
    'warehouses',
    'products',
    'departments',
    'dispatch',
    'transactions',
    'dept_report',
    'report',
    'users',
    'merge_products',
    'trash',
)

# بخش‌هایی که فقط مدیر می‌بیند
ADMIN_ONLY_SECTIONS = ('users', 'merge_products', 'trash')

# پیش‌فرض دسترسی هر نقش — اگر فیلد permissions خالی باشد
DEFAULT_PERMISSIONS = {
    'admin': list(APP_SECTIONS),
    'operator': [s for s in APP_SECTIONS if s not in ADMIN_ONLY_SECTIONS],
    'viewer': [s for s in APP_SECTIONS if s not in ADMIN_ONLY_SECTIONS and s != 'dispatch'],
}

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
    # JSON list of section keys the user may open in the UI
    permissions   = db.Column(db.Text, default='')
    is_active     = db.Column(db.Boolean, default=True, index=True)
    created_at    = db.Column(db.DateTime, default=now_tehran)

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password):
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def get_permissions(self):
        """لیست بخش‌های مجاز — نقش همچنان سطح نوشتن را کنترل می‌کند."""
        raw = (self.permissions or '').strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    perms = [p for p in parsed if p in APP_SECTIONS]
                    if perms:
                        if self.role == 'admin':
                            for sec in ADMIN_ONLY_SECTIONS:
                                if sec not in perms:
                                    perms.append(sec)
                        if self.role == 'viewer':
                            perms = [p for p in perms if p != 'dispatch']
                        if self.role != 'admin':
                            perms = [p for p in perms if p not in ADMIN_ONLY_SECTIONS]
                        return perms
            except Exception:
                pass
        return list(DEFAULT_PERMISSIONS.get(self.role, DEFAULT_PERMISSIONS['viewer']))

    def set_permissions(self, perms):
        if not isinstance(perms, list):
            perms = []
        clean = [p for p in perms if p in APP_SECTIONS]
        if self.role == 'admin':
            for sec in ADMIN_ONLY_SECTIONS:
                if sec not in clean:
                    clean.append(sec)
        if self.role != 'admin':
            clean = [p for p in clean if p not in ADMIN_ONLY_SECTIONS]
        if self.role == 'viewer':
            clean = [p for p in clean if p != 'dispatch']
        # حداقل یک بخش برای جلوگیری از کاربر قفل‌شده
        if not clean:
            clean = ['dashboard']
        self.permissions = json.dumps(clean, ensure_ascii=False)

    def can_access(self, section):
        return section in self.get_permissions()

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        last = LoginLog.query.filter_by(user_id=self.id).order_by(LoginLog.logged_in_at.desc()).first()
        return {
            'id': self.id,
            'username': self.username,
            'display_name': self.display_name or self.username,
            'role': self.role,
            'is_active': self.is_active,
            'permissions': self.get_permissions(),
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
    deleted_at  = db.Column(db.DateTime, nullable=True, index=True)
    products    = db.relationship('Product', backref='warehouse', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        # آمار انبار: کالاهای حذف‌نشده (فعال + غیرفعال)
        alive = [p for p in self.products if not p.deleted_at]
        live_active = [p for p in alive if p.is_active]
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location or '',
            'description': self.description or '',
            'created_at': jdt.strftime('%Y/%m/%d'),
            'is_active': self.is_active,
            'deleted_at': jdatetime.datetime.fromgregorian(datetime=self.deleted_at).strftime('%Y/%m/%d %H:%M') if self.deleted_at else '',
            'product_count': len(alive),
            'active_product_count': len(live_active),
            'total_items': sum(p.quantity for p in live_active),
            'total_value': sum(p.stock_value() for p in live_active),
        }


class Department(db.Model):
    __tablename__ = 'departments'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.Text)
    is_active   = db.Column(db.Boolean, default=True, index=True)
    deleted_at  = db.Column(db.DateTime, nullable=True, index=True)
    created_at  = db.Column(db.DateTime, default=now_tehran)

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'is_active': self.is_active,
            'deleted_at': jdatetime.datetime.fromgregorian(datetime=self.deleted_at).strftime('%Y/%m/%d %H:%M') if self.deleted_at else '',
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
    deleted_at    = db.Column(db.DateTime, nullable=True, index=True)
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
            'deleted_at': jdatetime.datetime.fromgregorian(datetime=self.deleted_at).strftime('%Y/%m/%d %H:%M') if self.deleted_at else '',
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
            if self.is_reversed:
                return 'purchase_void', '⬆️ ورود خرید (حذف‌شده)'
            note = (self.note or '').strip()
            if note == 'موجودی اولیه':
                return 'initial', '📦 موجودی اولیه'
            return 'purchase', '⬆️ ورود خرید'
        if self.type == 'out':
            if self.department_id:
                if self.is_reversed:
                    return 'dispatch_reversed', '⬇️ خروج به بخش (بازگشت‌خورده)'
                return 'dispatch', '⬇️ خروج به بخش'
            if self.reversed_by_id:
                return 'purchase_cancel', '🗑️ حذف ورود خرید'
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


def paginate_args(default_per_page=50, max_per_page=200):
    page = max(1, request.args.get('page', 1, type=int) or 1)
    per_page = request.args.get('per_page', default_per_page, type=int) or default_per_page
    per_page = min(max(per_page, 1), max_per_page)
    return page, per_page


def paginated_response(query, serialize):
    """صفحه‌بندی استاندارد برای لیست‌های بزرگ"""
    page, per_page = paginate_args()
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({
        'items': [serialize(x) for x in items],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, -(-total // per_page)),
    })


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
            'permissions': u.get_permissions(),
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
    if 'permissions' in data:
        u.set_permissions(data.get('permissions'))
    else:
        u.set_permissions(DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS['viewer']))
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
    if 'permissions' in data:
        user.set_permissions(data.get('permissions'))
    db.session.commit()
    return jsonify(user.to_dict())

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@require_auth('admin')
def delete_user(uid):
    current = get_current_user()
    user = User.query.get_or_404(uid)
    if user.id == current.id:
        return jsonify({'error': 'نمیتوانید حساب خودتان را حذف کنید'}), 400
    # پاک‌سازی وابستگی‌ها قبل از حذف کاربر (FK)
    Session.query.filter_by(user_id=user.id).delete()
    LoginLog.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/login-logs', methods=['GET'])
@require_auth('admin')
def get_all_login_logs():
    try:
        limit = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 200))

    user_id = request.args.get('user_id')
    q = LoginLog.query.options(joinedload(LoginLog.user)).order_by(LoginLog.logged_in_at.desc())
    if user_id not in (None, '', 'all'):
        try:
            q = q.filter(LoginLog.user_id == int(user_id))
        except (TypeError, ValueError):
            pass
    logs = q.limit(limit).all()
    result = []
    for log in logs:
        jdt = jdatetime.datetime.fromgregorian(datetime=log.logged_in_at)
        result.append({
            'id': log.id,
            'user_id': log.user_id,
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
    whs = (Warehouse.query
           .filter(Warehouse.is_active == True, Warehouse.deleted_at.is_(None))
           .options(joinedload(Warehouse.products))
           .all())
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
    """حذف نرم انبار → سطل زباله؛ کالاهایش هم به سطل می‌روند"""
    wh = Warehouse.query.get_or_404(wid)
    now = now_tehran()
    wh.is_active = False
    wh.deleted_at = now
    for p in wh.products:
        if not p.deleted_at:
            p.is_active = False
            p.deleted_at = now
            p.updated_at = now
    db.session.commit()
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════════
# DEPARTMENT ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/departments', methods=['GET'])
@require_auth()
def get_departments():
    depts = (Department.query
             .filter(Department.is_active == True, Department.deleted_at.is_(None))
             .order_by(Department.name).all())
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
    dept.deleted_at = now_tehran()
    db.session.commit()
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════════
# PRODUCT ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/warehouses/<int:wid>/products', methods=['GET'])
@require_auth()
def get_products(wid):
    """کالاهای انبار: فعال + غیرفعال (برای فعال‌سازی دوباره) — حذف‌شده‌ها در سطل زباله‌اند"""
    prods = (Product.query
             .options(joinedload(Product.warehouse))
             .filter(
                 Product.warehouse_id == wid,
                 Product.deleted_at.is_(None),
             )
             .order_by(Product.is_active.desc(), Product.id.desc()).all())
    return jsonify([p.to_dict() for p in prods])

@app.route('/api/products', methods=['GET'])
@require_auth()
def get_all_products():
    search = request.args.get('search', '').strip()
    wh_id = request.args.get('warehouse_id', '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(max(request.args.get('per_page', 50, type=int), 1), 200)

    query = (Product.query.options(joinedload(Product.warehouse))
             .filter(Product.is_active == True, Product.deleted_at.is_(None)))
    if wh_id not in ('', 'all', None):
        try:
            query = query.filter(Product.warehouse_id == int(wh_id))
        except (TypeError, ValueError):
            pass
    if search:
        query = query.filter(
            db.or_(
                Product.name.ilike(f'%{search}%'),
                Product.sku.ilike(f'%{search}%'),
                Product.category.ilike(f'%{search}%'),
            )
        )
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
        is_active=bool(data['is_active']) if 'is_active' in data else True,
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
    if 'is_active' in data:
        if p.deleted_at:
            return jsonify({'error': 'این کالا در سطل زباله است؛ ابتدا بازیابی کنید'}), 400
        p.is_active = bool(data['is_active'])
    # قیمت واحد فقط از ورود خرید به‌روز می‌شود؛ اگر صریح فرستاده شد نادیده بگیر مگر admin path جدا
    p.updated_at = now_tehran()
    db.session.commit()
    return jsonify(p.to_dict())


@app.route('/api/products/<int:pid>/active', methods=['PATCH'])
@require_auth('operator')
def set_product_active(pid):
    """فعال / غیرفعال — غیرفعال در انبار می‌ماند؛ از داشبورد/همه کالاها/خروج مخفی می‌شود"""
    p = Product.query.get_or_404(pid)
    if p.deleted_at:
        return jsonify({'error': 'این کالا در سطل زباله است؛ ابتدا بازیابی کنید'}), 400
    data = request.json or {}
    if 'is_active' not in data:
        return jsonify({'error': 'مقدار is_active الزامی است'}), 400
    p.is_active = bool(data['is_active'])
    p.updated_at = now_tehran()
    db.session.commit()
    return jsonify(p.to_dict())

@app.route('/api/products/<int:pid>', methods=['DELETE'])
@require_auth('operator')
def delete_product(pid):
    """حذف نرم → سطل زباله (از انبار هم مخفی می‌شود)"""
    p = Product.query.get_or_404(pid)
    p.is_active = False
    p.deleted_at = now_tehran()
    p.updated_at = now_tehran()
    db.session.commit()
    return jsonify({'ok': True})


def _product_merge_summary(p):
    """خلاصه کالا برای پیش‌نمایش ادغام"""
    tx_q = Transaction.query.filter_by(product_id=p.id)
    tx_count = tx_q.count()
    recent = (tx_q.order_by(Transaction.created_at.desc(), Transaction.id.desc())
              .limit(8).all())
    return {
        'product': p.to_dict(),
        'tx_count': tx_count,
        'recent_transactions': [t.to_dict() for t in recent],
    }


@app.route('/api/products/merge/preview', methods=['GET'])
@require_auth('admin')
def preview_product_merge():
    """پیش‌نمایش ادغام دو کالای تکراری در یک انبار"""
    try:
        source_id = int(request.args.get('source_id', 0))
        dest_id = int(request.args.get('dest_id', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'شناسه کالا نامعتبر است'}), 400
    if not source_id or not dest_id:
        return jsonify({'error': 'کالای مبدا و مقصد را انتخاب کنید'}), 400
    if source_id == dest_id:
        return jsonify({'error': 'مبدا و مقصد نباید یکی باشند'}), 400

    source = Product.query.options(joinedload(Product.warehouse)).get_or_404(source_id)
    dest = Product.query.options(joinedload(Product.warehouse)).get_or_404(dest_id)
    if source.deleted_at or dest.deleted_at:
        return jsonify({'error': 'کالای داخل سطل زباله را نمی‌توان ادغام کرد'}), 400
    if not source.is_active or not dest.is_active:
        return jsonify({'error': 'هر دو کالا باید فعال باشند'}), 400
    if source.warehouse_id != dest.warehouse_id:
        return jsonify({'error': 'ادغام فقط برای دو کالا در یک انبار مجاز است'}), 400

    src_qty = float(source.quantity or 0)
    dst_qty = float(dest.quantity or 0)
    src_cost = float(source.cost_basis() or 0)
    dst_cost = float(dest.cost_basis() or 0)
    merged_qty = src_qty + dst_qty
    if merged_qty > 0 and (src_cost > 0 or dst_cost > 0):
        merged_avg = ((max(src_qty, 0) * src_cost) + (max(dst_qty, 0) * dst_cost)) / merged_qty
    else:
        merged_avg = dst_cost or src_cost or 0

    return jsonify({
        'source': _product_merge_summary(source),
        'destination': _product_merge_summary(dest),
        'result': {
            'name': dest.name,
            'quantity': merged_qty,
            'avg_cost': round(merged_avg, 4),
            'unit': dest.unit,
            'warehouse_name': dest.warehouse.name if dest.warehouse else '',
            'moved_tx_count': Transaction.query.filter_by(product_id=source.id).count(),
        },
    })


@app.route('/api/products/merge', methods=['POST'])
@require_auth('admin')
def merge_products():
    """
    ادغام کالای تکراری:
    - همه حرکات (ورود/خروج/قیمت/ساعت/بخش) از مبدا به مقصد منتقل می‌شود
    - موجودی‌ها جمع می‌شوند و میانگین هزینه موزون می‌شود
    - کالای مبدا غیرفعال می‌شود (تاریخچه در مقصد می‌ماند)
    """
    data = request.json or {}
    try:
        source_id = int(data.get('source_id') or 0)
        dest_id = int(data.get('dest_id') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'شناسه کالا نامعتبر است'}), 400
    if not source_id or not dest_id:
        return jsonify({'error': 'کالای مبدا و مقصد را انتخاب کنید'}), 400
    if source_id == dest_id:
        return jsonify({'error': 'مبدا و مقصد نباید یکی باشند'}), 400

    keep_name = (data.get('keep_name') or 'destination').strip()
    if keep_name not in ('destination', 'source'):
        keep_name = 'destination'

    source = Product.query.get_or_404(source_id)
    dest = Product.query.get_or_404(dest_id)
    if source.deleted_at or dest.deleted_at:
        return jsonify({'error': 'کالای داخل سطل زباله را نمی‌توان ادغام کرد'}), 400
    if not source.is_active or not dest.is_active:
        return jsonify({'error': 'هر دو کالا باید فعال باشند'}), 400
    if source.warehouse_id != dest.warehouse_id:
        return jsonify({'error': 'ادغام فقط برای دو کالا در یک انبار مجاز است'}), 400

    user = get_current_user()
    actor = (user.display_name or user.username) if user else 'مدیر'

    src_qty = float(source.quantity or 0)
    dst_qty = float(dest.quantity or 0)
    src_cost = float(source.cost_basis() or 0)
    dst_cost = float(dest.cost_basis() or 0)
    merged_qty = src_qty + dst_qty
    if merged_qty > 0 and (src_cost > 0 or dst_cost > 0):
        merged_avg = ((max(src_qty, 0) * src_cost) + (max(dst_qty, 0) * dst_cost)) / merged_qty
    else:
        merged_avg = dst_cost or src_cost or float(dest.avg_cost or 0) or float(dest.unit_price or 0)

    final_name = source.name if keep_name == 'source' else dest.name
    source_name = source.name
    source_tx_ids = [tid for (tid,) in db.session.query(Transaction.id).filter_by(product_id=source.id).all()]
    moved = len(source_tx_ids)

    if source_tx_ids:
        note_tag = f' [ادغام از «{source_name}» #{source.id}]'
        for tx in Transaction.query.filter(Transaction.id.in_(source_tx_ids)).all():
            tx.product_id = dest.id
            base_note = (tx.note or '').strip()
            if note_tag not in base_note:
                tx.note = (base_note + note_tag).strip() if base_note else note_tag.strip()

    dest.name = final_name
    dest.quantity = merged_qty
    dest.avg_cost = merged_avg
    # آخرین قیمت خرید پس از جابه‌جایی حرکات
    last_purchase = (
        Transaction.query.filter_by(product_id=dest.id, type='in', is_reversed=False)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .first()
    )
    if last_purchase and last_purchase.unit_price is not None:
        dest.unit_price = last_purchase.unit_price
    elif source.unit_price and (not dest.unit_price or source.updated_at >= dest.updated_at):
        dest.unit_price = source.unit_price

    if not (dest.sku or '').strip() and (source.sku or '').strip():
        dest.sku = source.sku
    if not (dest.category or '').strip() and (source.category or '').strip():
        dest.category = source.category

    audit_note = (
        f'ادغام کالا: «{source_name}» (#{source.id}) → «{final_name}» (#{dest.id}). '
        f'{moved} حرکت منتقل شد. موجودی مبدا {src_qty:g} + مقصد {dst_qty:g} = {merged_qty:g} {dest.unit or ""}.'
    )
    audit = Transaction(
        product_id=dest.id,
        type='adjust',
        quantity=merged_qty,
        before_qty=dst_qty,
        after_qty=merged_qty,
        unit_price=merged_avg,
        total_cost=merged_qty * merged_avg,
        note=audit_note,
        created_by=actor,
    )
    db.session.add(audit)

    source.quantity = 0
    source.is_active = False
    source.deleted_at = now_tehran()
    source.description = (
        (source.description or '').strip()
        + (('\n' if (source.description or '').strip() else '')
           + f'ادغام‌شده در «{final_name}» (#{dest.id}) در {now_tehran().strftime("%Y-%m-%d %H:%M")}')
    )
    source.updated_at = now_tehran()
    dest.updated_at = now_tehran()

    db.session.commit()
    return jsonify({
        'ok': True,
        'moved_tx_count': moved,
        'destination': dest.to_dict(),
        'source_id': source.id,
        'audit_tx_id': audit.id,
    })

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
           .limit(500)
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
        if kind in ('purchase', 'initial') and tx.type == 'in' and not tx.is_reversed:
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
    if p.deleted_at:
        return jsonify({'error': 'این کالا حذف شده و در سطل زباله است'}), 400
    if not p.is_active:
        return jsonify({'error': 'این کالا غیرفعال است؛ ابتدا از کارت کالا فعالش کنید'}), 400
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
        query = query.outerjoin(Department, Transaction.department_id == Department.id).filter(db.or_(
            Product.name.ilike(f'%{search}%'),
            Transaction.note.ilike(f'%{search}%'),
            Transaction.ref_number.ilike(f'%{search}%'),
            Department.name.ilike(f'%{search}%'),
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
    query = query.order_by(Transaction.created_at.desc(), Transaction.id.desc())
    return paginated_response(query, lambda t: t.to_dict())


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
    if product.deleted_at:
        return jsonify({'error': 'این کالا حذف شده و در سطل زباله است'}), 400
    if not product.is_active:
        return jsonify({'error': 'این کالا غیرفعال است و نمی‌توان برای آن خروج ثبت کرد'}), 400
    if dept.deleted_at or not dept.is_active:
        return jsonify({'error': 'این بخش غیرفعال یا حذف‌شده است'}), 400
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


@app.route('/api/transactions/<int:tx_id>/reverse-purchase', methods=['POST'])
@require_auth('operator')
def reverse_purchase(tx_id):
    """حذف/برگشت یک ورود خرید اشتباه — با کم کردن همان تعداد از موجودی"""
    user = get_current_user()
    tx = Transaction.query.get_or_404(tx_id)

    if tx.type != 'in' or tx.department_id is not None or tx.reversed_by_id is not None:
        return jsonify({'error': 'فقط ورود خرید قابل حذف است'}), 400
    if tx.is_reversed:
        return jsonify({'error': 'این ورود خرید قبلاً حذف شده'}), 400

    product = Product.query.get_or_404(tx.product_id)
    if product.quantity < tx.quantity:
        return jsonify({
            'error': (
                f'موجودی فعلی ({product.quantity} {product.unit}) برای حذف این ورود '
                f'({tx.quantity} {product.unit}) کافی نیست. احتمالاً بخشی از آن خارج شده است.'
            )
        }), 400

    before = product.quantity
    product.quantity -= tx.quantity
    product.updated_at = now_tehran()

    # میانگین و آخرین قیمت را از خریدهای باقی‌مانده دوباره حساب کن
    remaining = (
        Transaction.query
        .filter(
            Transaction.product_id == product.id,
            Transaction.type == 'in',
            Transaction.department_id.is_(None),
            Transaction.reversed_by_id.is_(None),
            Transaction.is_reversed.is_(False),
            Transaction.id != tx.id,
        )
        .order_by(Transaction.created_at.asc(), Transaction.id.asc())
        .all()
    )
    total_qty = 0.0
    total_cost = 0.0
    last_price = 0.0
    for ptx in remaining:
        kind, _ = ptx.movement_meta()
        if kind not in ('purchase', 'initial'):
            continue
        price = float(ptx.unit_price or 0)
        qty = float(ptx.quantity or 0)
        if qty <= 0:
            continue
        total_qty += qty
        total_cost += qty * price
        last_price = price
    if total_qty > 0:
        product.avg_cost = total_cost / total_qty
        product.unit_price = last_price
    else:
        product.avg_cost = 0
        product.unit_price = 0

    unit_price = tx.unit_price if tx.unit_price is not None else product.cost_basis()
    total_cost_tx = tx.total_cost if tx.total_cost is not None else (tx.quantity * (unit_price or 0))

    reverse_tx = Transaction(
        product_id=tx.product_id,
        type='out',
        quantity=tx.quantity,
        before_qty=before,
        after_qty=product.quantity,
        unit_price=unit_price,
        total_cost=total_cost_tx,
        note=f'حذف ورود خرید اشتباه (شناسه تراکنش: {tx_id})',
        ref_number=tx.ref_number or '',
        created_by=user.display_name or user.username,
        reversed_by_id=tx_id,
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

    from sqlalchemy import func
    # خلاصه تجمیعی در خود دیتابیس — بدون لود همه ردیف‌ها
    summary_rows = (
        query.with_entities(
            Transaction.department_id,
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.quantity), 0),
        )
        .group_by(Transaction.department_id)
        .all()
    )
    dept_ids = [r[0] for r in summary_rows if r[0]]
    dept_names = {
        d.id: d.name
        for d in Department.query.filter(Department.id.in_(dept_ids)).all()
    } if dept_ids else {}
    dept_summary = {}
    for dept_id, cnt, items in summary_rows:
        dn = dept_names.get(dept_id, '')
        dept_summary[dn] = {'count': int(cnt or 0), 'items': float(items or 0)}

    total = query.order_by(None).count()
    page, per_page = paginate_args()
    txs = (query
           .order_by(Transaction.created_at.desc(), Transaction.id.desc())
           .offset((page - 1) * per_page)
           .limit(per_page)
           .all())
    now_j = jdatetime.datetime.fromgregorian(datetime=now_tehran()).strftime('%Y/%m/%d %H:%M')
    payload = [t.to_dict() for t in txs]
    return jsonify({
        'generated_at': now_j,
        'transactions': payload,
        'items': payload,
        'dept_summary': dept_summary,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, -(-total // per_page)),
    })


# ═══════════════════════════════════════════════════════════════════
# DASHBOARD & REPORTS
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/dashboard', methods=['GET'])
@require_auth()
def dashboard():
    from sqlalchemy import func
    warehouse_count = (db.session.query(func.count(Warehouse.id))
                       .filter(Warehouse.is_active == True, Warehouse.deleted_at.is_(None))
                       .scalar())

    # آمار و لیست هشدار باید از یک منطق واحد (get_status) بیایند
    # تا با manual_status و حداقل موجودی ناسازگار نشوند
    active_products = (Product.query
                       .options(joinedload(Product.warehouse))
                       .filter(Product.is_active == True, Product.deleted_at.is_(None))
                       .order_by(Product.name.asc())
                       .all())
    product_count = len(active_products)
    total_value = sum(p.stock_value() for p in active_products)

    low_stock = [p for p in active_products if p.get_status() == 'low']
    out_of_stock = [p for p in active_products if p.get_status() == 'out']

    # حرکات اخیر داشبورد هم فقط کالاهای فعال — تاریخچهٔ غیرفعال‌ها در «حرکات انبار» می‌ماند
    recent_txs = (Transaction.query
                  .join(Product, Transaction.product_id == Product.id)
                  .options(
                      joinedload(Transaction.product).joinedload(Product.warehouse),
                      joinedload(Transaction.department),
                  )
                  .filter(Product.is_active == True, Product.deleted_at.is_(None))
                  .order_by(Transaction.created_at.desc()).limit(10).all())
    return jsonify({
        'warehouse_count':   warehouse_count,
        'product_count':     product_count,
        'low_stock_count':   len(low_stock),
        'out_of_stock_count': len(out_of_stock),
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
    query = (Product.query.options(joinedload(Product.warehouse))
             .filter(Product.is_active == True, Product.deleted_at.is_(None)))
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
# TRASH (سطل زباله — فقط ادمین)
# ═══════════════════════════════════════════════════════════════════

def _fmt_deleted_at(dt):
    if not dt:
        return ''
    return jdatetime.datetime.fromgregorian(datetime=dt).strftime('%Y/%m/%d %H:%M')


@app.route('/api/trash', methods=['GET'])
@require_auth('admin')
def list_trash():
    """لیست حذف‌شده‌ها به تفکیک نوع"""
    kind = (request.args.get('type') or 'all').strip()
    products, warehouses, departments = [], [], []

    if kind in ('all', 'products'):
        for p in (Product.query.options(joinedload(Product.warehouse))
                  .filter(Product.deleted_at.isnot(None))
                  .order_by(Product.deleted_at.desc()).all()):
            products.append({
                'id': p.id,
                'type': 'product',
                'name': p.name,
                'meta': (p.warehouse.name if p.warehouse else '') + (f' · موجودی {p.quantity:g} {p.unit or ""}' if p.quantity is not None else ''),
                'deleted_at': _fmt_deleted_at(p.deleted_at),
                'deleted_at_raw': p.deleted_at.isoformat(sep=' ') if p.deleted_at else '',
            })
    if kind in ('all', 'warehouses'):
        for w in (Warehouse.query.filter(Warehouse.deleted_at.isnot(None))
                  .order_by(Warehouse.deleted_at.desc()).all()):
            warehouses.append({
                'id': w.id,
                'type': 'warehouse',
                'name': w.name,
                'meta': w.location or '',
                'deleted_at': _fmt_deleted_at(w.deleted_at),
                'deleted_at_raw': w.deleted_at.isoformat(sep=' ') if w.deleted_at else '',
            })
    if kind in ('all', 'departments'):
        for d in (Department.query.filter(Department.deleted_at.isnot(None))
                  .order_by(Department.deleted_at.desc()).all()):
            departments.append({
                'id': d.id,
                'type': 'department',
                'name': d.name,
                'meta': (d.description or '')[:80],
                'deleted_at': _fmt_deleted_at(d.deleted_at),
                'deleted_at_raw': d.deleted_at.isoformat(sep=' ') if d.deleted_at else '',
            })

    return jsonify({
        'products': products,
        'warehouses': warehouses,
        'departments': departments,
        'counts': {
            'products': len(products) if kind in ('all', 'products') else Product.query.filter(Product.deleted_at.isnot(None)).count(),
            'warehouses': len(warehouses) if kind in ('all', 'warehouses') else Warehouse.query.filter(Warehouse.deleted_at.isnot(None)).count(),
            'departments': len(departments) if kind in ('all', 'departments') else Department.query.filter(Department.deleted_at.isnot(None)).count(),
        },
    })


@app.route('/api/trash/restore', methods=['POST'])
@require_auth('admin')
def restore_trash_item():
    data = request.json or {}
    kind = (data.get('type') or '').strip()
    try:
        item_id = int(data.get('id') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'شناسه نامعتبر'}), 400
    if not item_id or kind not in ('product', 'warehouse', 'department'):
        return jsonify({'error': 'نوع یا شناسه نامعتبر'}), 400

    if kind == 'product':
        item = Product.query.get_or_404(item_id)
        if not item.deleted_at:
            return jsonify({'error': 'این کالا در سطل زباله نیست'}), 400
        # انبار والد اگر حذف شده، اول باید بازیابی شود
        wh = Warehouse.query.get(item.warehouse_id)
        if wh and wh.deleted_at:
            return jsonify({'error': f'ابتدا انبار «{wh.name}» را از سطل زباله بازیابی کنید'}), 400
        item.deleted_at = None
        item.is_active = True
        item.updated_at = now_tehran()
    elif kind == 'warehouse':
        item = Warehouse.query.get_or_404(item_id)
        if not item.deleted_at:
            return jsonify({'error': 'این انبار در سطل زباله نیست'}), 400
        item.deleted_at = None
        item.is_active = True
        # کالاهایی که همراه انبار حذف شده بودند برمی‌گردند (فعال)
        for p in item.products:
            if p.deleted_at:
                p.deleted_at = None
                p.is_active = True
                p.updated_at = now_tehran()
    else:
        item = Department.query.get_or_404(item_id)
        if not item.deleted_at:
            return jsonify({'error': 'این بخش در سطل زباله نیست'}), 400
        item.deleted_at = None
        item.is_active = True

    db.session.commit()
    return jsonify({'ok': True, 'type': kind, 'id': item_id, 'item': item.to_dict()})


@app.route('/api/trash/purge', methods=['POST'])
@require_auth('admin')
def purge_trash_item():
    """حذف دائمی یک مورد از سطل زباله"""
    data = request.json or {}
    kind = (data.get('type') or '').strip()
    try:
        item_id = int(data.get('id') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'شناسه نامعتبر'}), 400
    if not item_id or kind not in ('product', 'warehouse', 'department'):
        return jsonify({'error': 'نوع یا شناسه نامعتبر'}), 400

    if kind == 'product':
        item = Product.query.get_or_404(item_id)
        if not item.deleted_at:
            return jsonify({'error': 'فقط موارد داخل سطل زباله قابل پاک‌سازی دائمی هستند'}), 400
        Transaction.query.filter_by(product_id=item.id).delete(synchronize_session=False)
        db.session.delete(item)
    elif kind == 'warehouse':
        item = Warehouse.query.get_or_404(item_id)
        if not item.deleted_at:
            return jsonify({'error': 'فقط موارد داخل سطل زباله قابل پاک‌سازی دائمی هستند'}), 400
        # کالاهای این انبار هم باید permanently پاک شوند
        for p in list(item.products):
            Transaction.query.filter_by(product_id=p.id).delete(synchronize_session=False)
            db.session.delete(p)
        db.session.delete(item)
    else:
        item = Department.query.get_or_404(item_id)
        if not item.deleted_at:
            return jsonify({'error': 'فقط موارد داخل سطل زباله قابل پاک‌سازی دائمی هستند'}), 400
        # حرکات مرتبط را نگه می‌داریم ولی department_id را خالی می‌کنیم
        Transaction.query.filter_by(department_id=item.id).update(
            {Transaction.department_id: None}, synchronize_session=False)
        db.session.delete(item)

    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/trash/empty', methods=['POST'])
@require_auth('admin')
def empty_trash():
    """خالی کردن کامل سطل زباله یا فقط یک نوع"""
    data = request.json or {}
    kind = (data.get('type') or 'all').strip()
    if kind not in ('all', 'products', 'warehouses', 'departments'):
        return jsonify({'error': 'نوع نامعتبر'}), 400

    purged = {'products': 0, 'warehouses': 0, 'departments': 0}

    if kind in ('all', 'products'):
        ids = [pid for (pid,) in db.session.query(Product.id).filter(Product.deleted_at.isnot(None)).all()]
        if ids:
            Transaction.query.filter(Transaction.product_id.in_(ids)).delete(synchronize_session=False)
            purged['products'] = Product.query.filter(Product.id.in_(ids)).delete(synchronize_session=False)

    if kind in ('all', 'warehouses'):
        whs = Warehouse.query.filter(Warehouse.deleted_at.isnot(None)).all()
        for wh in whs:
            for p in list(wh.products):
                Transaction.query.filter_by(product_id=p.id).delete(synchronize_session=False)
                db.session.delete(p)
                purged['products'] += 1
            db.session.delete(wh)
            purged['warehouses'] += 1

    if kind in ('all', 'departments'):
        deps = Department.query.filter(Department.deleted_at.isnot(None)).all()
        for dep in deps:
            Transaction.query.filter_by(department_id=dep.id).update(
                {Transaction.department_id: None}, synchronize_session=False)
            db.session.delete(dep)
            purged['departments'] += 1

    db.session.commit()
    return jsonify({'ok': True, 'purged': purged})


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
            ('ALTER TABLE users ADD COLUMN permissions TEXT DEFAULT \'\'', 'users.permissions'),
            ('ALTER TABLE products ADD COLUMN deleted_at DATETIME', 'products.deleted_at'),
            ('ALTER TABLE warehouses ADD COLUMN deleted_at DATETIME', 'warehouses.deleted_at'),
            ('ALTER TABLE departments ADD COLUMN deleted_at DATETIME', 'departments.deleted_at'),
            # ایندکس‌های ترکیبی برای سرعت گزارش‌ها وقتی دیتا زیاد می‌شود
            ('CREATE INDEX IF NOT EXISTS ix_tx_type_created ON transactions(type, created_at)', 'ix_tx_type_created'),
            ('CREATE INDEX IF NOT EXISTS ix_tx_dept_created ON transactions(department_id, created_at)', 'ix_tx_dept_created'),
            ('CREATE INDEX IF NOT EXISTS ix_tx_product_created ON transactions(product_id, created_at)', 'ix_tx_product_created'),
            ('CREATE INDEX IF NOT EXISTS ix_tx_created_id ON transactions(created_at, id)', 'ix_tx_created_id'),
            ('CREATE INDEX IF NOT EXISTS ix_products_wh_active ON products(warehouse_id, is_active)', 'ix_products_wh_active'),
            ('CREATE INDEX IF NOT EXISTS ix_products_active_name ON products(is_active, name)', 'ix_products_active_name'),
            ('CREATE INDEX IF NOT EXISTS ix_products_deleted ON products(deleted_at)', 'ix_products_deleted'),
            ('CREATE INDEX IF NOT EXISTS ix_warehouses_deleted ON warehouses(deleted_at)', 'ix_warehouses_deleted'),
            ('CREATE INDEX IF NOT EXISTS ix_departments_deleted ON departments(deleted_at)', 'ix_departments_deleted'),
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
