from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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

db = SQLAlchemy(app)

# ─── Models ───────────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    display_name  = db.Column(db.String(100))
    role          = db.Column(db.String(20), default='viewer')  # admin | operator | viewer
    is_active     = db.Column(db.Boolean, default=True, index=True)
    created_at    = db.Column(db.DateTime, default=now_tehran)
    sessions      = db.relationship('Session', backref='user', lazy=True, cascade='all, delete-orphan')
    login_logs    = db.relationship('LoginLog', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password):
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        last_login = LoginLog.query.filter_by(user_id=self.id).order_by(LoginLog.logged_in_at.desc()).first()
        last_login_str = ''
        if last_login:
            jl = jdatetime.datetime.fromgregorian(datetime=last_login.logged_in_at)
            last_login_str = jl.strftime('%Y/%m/%d %H:%M')
        return {
            'id': self.id,
            'username': self.username,
            'display_name': self.display_name or self.username,
            'role': self.role,
            'is_active': self.is_active,
            'created_at': jdt.strftime('%Y/%m/%d'),
            'last_login': last_login_str,
        }


class Session(db.Model):
    __tablename__ = 'sessions'
    id         = db.Column(db.Integer, primary_key=True)
    token      = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=now_tehran)
    expires_at = db.Column(db.DateTime, nullable=False)

    def is_valid(self):
        return now_tehran() < self.expires_at


class LoginLog(db.Model):
    __tablename__ = 'login_logs'
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    logged_in_at = db.Column(db.DateTime, default=now_tehran, index=True)
    ip_address   = db.Column(db.String(50))


class Warehouse(db.Model):
    __tablename__ = 'warehouses'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    location   = db.Column(db.String(200))
    description= db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now_tehran)
    is_active  = db.Column(db.Boolean, default=True, index=True)
    products   = db.relationship('Product', backref='warehouse', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        total_items = sum(p.quantity for p in self.products if p.is_active)
        total_value = sum(p.quantity * (p.unit_price or 0) for p in self.products if p.is_active)
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location or '',
            'description': self.description or '',
            'created_at': jdt.strftime('%Y/%m/%d'),
            'is_active': self.is_active,
            'product_count': len([p for p in self.products if p.is_active]),
            'total_items': total_items,
            'total_value': total_value,
        }


class Product(db.Model):
    __tablename__ = 'products'
    id           = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id'), nullable=False, index=True)
    name         = db.Column(db.String(200), nullable=False, index=True)
    sku          = db.Column(db.String(100), index=True)
    category     = db.Column(db.String(100))
    quantity     = db.Column(db.Float, default=0)
    unit         = db.Column(db.String(50), default='عدد')
    unit_price   = db.Column(db.Float, default=0)
    min_stock    = db.Column(db.Float, default=0)
    description  = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=now_tehran)
    updated_at   = db.Column(db.DateTime, default=now_tehran, onupdate=now_tehran)
    is_active    = db.Column(db.Boolean, default=True, index=True)
    manual_status= db.Column(db.String(20), default=None)
    transactions = db.relationship('Transaction', backref='product', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        jdt_c = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        jdt_u = jdatetime.datetime.fromgregorian(datetime=self.updated_at)
        auto_status = 'normal'
        if self.quantity <= 0:
            auto_status = 'out'
        elif self.min_stock and self.quantity <= self.min_stock:
            auto_status = 'low'
        if self.manual_status == 'unavailable':
            status = 'out'
        elif self.manual_status == 'low':
            status = 'low'
        elif self.manual_status == 'available':
            status = 'normal'
        else:
            status = auto_status
        return {
            'id': self.id,
            'warehouse_id': self.warehouse_id,
            'name': self.name,
            'sku': self.sku or '',
            'category': self.category or '',
            'quantity': self.quantity,
            'unit': self.unit,
            'unit_price': self.unit_price or 0,
            'min_stock': self.min_stock or 0,
            'description': self.description or '',
            'created_at': jdt_c.strftime('%Y/%m/%d'),
            'updated_at': jdt_u.strftime('%Y/%m/%d %H:%M'),
            'is_active': self.is_active,
            'status': status,
            'manual_status': self.manual_status or '',
            'total_value': self.quantity * (self.unit_price or 0),
        }


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id           = db.Column(db.Integer, primary_key=True)
    product_id   = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    type         = db.Column(db.String(20), nullable=False, index=True)
    quantity     = db.Column(db.Float, nullable=False)
    before_qty   = db.Column(db.Float)
    after_qty    = db.Column(db.Float)
    note         = db.Column(db.Text)
    ref_number   = db.Column(db.String(100))
    created_at   = db.Column(db.DateTime, default=now_tehran, index=True)
    created_by   = db.Column(db.String(100), default='کاربر')

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'warehouse_name': self.product.warehouse.name if self.product and self.product.warehouse else '',
            'type': self.type,
            'quantity': self.quantity,
            'before_qty': self.before_qty,
            'after_qty': self.after_qty,
            'note': self.note or '',
            'ref_number': self.ref_number or '',
            'created_at': jdt.strftime('%Y/%m/%d %H:%M'),
            'created_by': self.created_by,
        }


# ─── Auth Helpers ─────────────────────────────────────────────────────────────

SESSION_HOURS = 8

def get_current_user():
    token = request.headers.get('X-Auth-Token') or request.args.get('token')
    if not token:
        return None
    sess = Session.query.filter_by(token=token).first()
    if not sess or not sess.is_valid():
        return None
    return sess.user

def require_auth(role=None):
    """Decorator: نیاز به لاگین. role میتونه 'admin' یا 'operator' باشه."""
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


# ─── Auth Routes ──────────────────────────────────────────────────────────────

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
    # ساخت session
    token = secrets.token_hex(32)
    sess = Session(
        token=token,
        user_id=user.id,
        expires_at=now_tehran() + timedelta(hours=SESSION_HOURS)
    )
    db.session.add(sess)
    # ثبت لاگ ورود
    log = LoginLog(user_id=user.id, ip_address=request.remote_addr)
    db.session.add(log)
    db.session.commit()
    return jsonify({
        'token': token,
        'user': user.to_dict(),
        'expires_in_hours': SESSION_HOURS,
    })

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
    old_pass = data.get('old_password', '')
    new_pass = data.get('new_password', '')
    if not user.check_password(old_pass):
        return jsonify({'error': 'رمز عبور فعلی اشتباه است'}), 400
    if len(new_pass) < 4:
        return jsonify({'error': 'رمز عبور جدید باید حداقل ۴ کاراکتر باشد'}), 400
    user.set_password(new_pass)
    db.session.commit()
    return jsonify({'ok': True})


# ─── User Management (admin only) ─────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
@require_auth('admin')
def get_users():
    users = User.query.order_by(User.id.desc()).all()
    return jsonify([u.to_dict() for u in users])

@app.route('/api/users', methods=['POST'])
@require_auth('admin')
def create_user():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role     = data.get('role', 'viewer')
    display_name = data.get('display_name', '').strip()
    if not username or not password:
        return jsonify({'error': 'نام کاربری و رمز عبور الزامی است'}), 400
    if role not in ('admin', 'operator', 'viewer'):
        return jsonify({'error': 'نقش نامعتبر است'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'این نام کاربری قبلاً ثبت شده'}), 400
    user = User(username=username, role=role, display_name=display_name)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify(user.to_dict()), 201

@app.route('/api/users/<int:uid>', methods=['PUT'])
@require_auth('admin')
def update_user(uid):
    current = get_current_user()
    user = User.query.get_or_404(uid)
    data = request.json or {}
    if 'display_name' in data:
        user.display_name = data['display_name']
    if 'role' in data and data['role'] in ('admin', 'operator', 'viewer'):
        if user.id == current.id and data['role'] != 'admin':
            return jsonify({'error': 'نمیتوانید نقش خودتان را تغییر دهید'}), 400
        user.role = data['role']
    if 'is_active' in data:
        if user.id == current.id:
            return jsonify({'error': 'نمیتوانید حساب خودتان را غیرفعال کنید'}), 400
        user.is_active = data['is_active']
    if 'password' in data and data['password']:
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

@app.route('/api/users/<int:uid>/login-logs', methods=['GET'])
@require_auth('admin')
def get_login_logs(uid):
    logs = LoginLog.query.filter_by(user_id=uid).order_by(LoginLog.logged_in_at.desc()).limit(50).all()
    result = []
    for log in logs:
        jdt = jdatetime.datetime.fromgregorian(datetime=log.logged_in_at)
        result.append({
            'id': log.id,
            'logged_in_at': jdt.strftime('%Y/%m/%d %H:%M'),
            'ip_address': log.ip_address or '—',
        })
    return jsonify(result)

@app.route('/api/login-logs', methods=['GET'])
@require_auth('admin')
def get_all_login_logs():
    logs = LoginLog.query.order_by(LoginLog.logged_in_at.desc()).limit(200).all()
    result = []
    for log in logs:
        jdt = jdatetime.datetime.fromgregorian(datetime=log.logged_in_at)
        result.append({
            'id': log.id,
            'username': log.user.username if log.user else '—',
            'display_name': (log.user.display_name or log.user.username) if log.user else '—',
            'logged_in_at': jdt.strftime('%Y/%m/%d %H:%M'),
            'ip_address': log.ip_address or '—',
        })
    return jsonify(result)


# ─── Warehouse Routes ──────────────────────────────────────────────────────────

@app.route('/api/warehouses', methods=['GET'])
@require_auth()
def get_warehouses():
    whs = Warehouse.query.filter_by(is_active=True).all()
    return jsonify([w.to_dict() for w in whs])

@app.route('/api/warehouses', methods=['POST'])
@require_auth('operator')
def create_warehouse():
    data = request.json
    if not data.get('name'):
        return jsonify({'error': 'نام انبار الزامی است'}), 400
    wh = Warehouse(name=data['name'], location=data.get('location',''), description=data.get('description',''))
    db.session.add(wh)
    db.session.commit()
    return jsonify(wh.to_dict()), 201

@app.route('/api/warehouses/<int:wid>', methods=['PUT'])
@require_auth('operator')
def update_warehouse(wid):
    wh = Warehouse.query.get_or_404(wid)
    data = request.json
    if 'name' in data: wh.name = data['name']
    if 'location' in data: wh.location = data['location']
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


# ─── Product Routes ────────────────────────────────────────────────────────────

@app.route('/api/warehouses/<int:wid>/products', methods=['GET'])
@require_auth()
def get_products(wid):
    prods = Product.query.filter_by(warehouse_id=wid, is_active=True).order_by(Product.id.desc()).all()
    return jsonify([p.to_dict() for p in prods])

@app.route('/api/products', methods=['GET'])
@require_auth()
def get_all_products():
    search = request.args.get('search', '')
    query = Product.query.filter_by(is_active=True)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    return jsonify([p.to_dict() for p in query.order_by(Product.id.desc()).all()])

@app.route('/api/products', methods=['POST'])
@require_auth('operator')
def create_product():
    data = request.json
    user = get_current_user()
    if not data.get('name') or not data.get('warehouse_id'):
        return jsonify({'error': 'نام کالا و انبار الزامی است'}), 400
    p = Product(
        warehouse_id=data['warehouse_id'], name=data['name'],
        sku=data.get('sku',''), category=data.get('category',''),
        quantity=float(data.get('quantity',0)), unit=data.get('unit','عدد'),
        unit_price=float(data.get('unit_price',0)), min_stock=float(data.get('min_stock',0)),
        description=data.get('description',''),
    )
    db.session.add(p)
    db.session.flush()
    if p.quantity > 0:
        tx = Transaction(product_id=p.id, type='in', quantity=p.quantity,
                         before_qty=0, after_qty=p.quantity, note='موجودی اولیه',
                         created_by=user.display_name or user.username)
        db.session.add(tx)
    db.session.commit()
    return jsonify(p.to_dict()), 201

@app.route('/api/products/<int:pid>', methods=['PUT'])
@require_auth('operator')
def update_product(pid):
    p = Product.query.get_or_404(pid)
    user = get_current_user()
    data = request.json
    for f in ['name','sku','category','unit','unit_price','min_stock','description']:
        if f in data: setattr(p, f, data[f])
    if 'quantity' in data:
        new_qty = float(data['quantity'])
        if new_qty != p.quantity:
            before = p.quantity
            p.quantity = new_qty
            user_note = data.get('note', '').strip()
            tx = Transaction(
                product_id=pid, type='adjust', quantity=new_qty,
                before_qty=before, after_qty=new_qty,
                note=user_note if user_note else 'ویرایش مستقیم موجودی از فرم',
                ref_number=data.get('ref_number',''),
                created_by=user.display_name or user.username,
            )
            db.session.add(tx)
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
    data = request.json
    s = data.get('manual_status')
    if s not in ('available','unavailable','low',None,''):
        return jsonify({'error': 'وضعیت نامعتبر'}), 400
    p.manual_status = s if s else None
    p.updated_at = now_tehran()
    db.session.commit()
    return jsonify(p.to_dict())


# ─── Transaction Routes ────────────────────────────────────────────────────────

@app.route('/api/products/<int:pid>/transactions', methods=['POST'])
@require_auth('operator')
def add_transaction(pid):
    p = Product.query.get_or_404(pid)
    user = get_current_user()
    data = request.json
    tx_type = data.get('type')
    qty = float(data.get('quantity', 0))
    if tx_type not in ('in','out','adjust'):
        return jsonify({'error': 'نوع تراکنش نامعتبر'}), 400
    if qty <= 0:
        return jsonify({'error': 'مقدار باید بیشتر از صفر باشد'}), 400
    before = p.quantity
    if tx_type == 'in':
        p.quantity += qty
    elif tx_type == 'out':
        if p.quantity < qty:
            return jsonify({'error': 'موجودی کافی نیست'}), 400
        p.quantity -= qty
    elif tx_type == 'adjust':
        p.quantity = qty
    manual_status = data.get('manual_status')
    if manual_status in ('available','unavailable','low'):
        p.manual_status = manual_status
    elif manual_status is None:
        p.manual_status = None
    p.updated_at = now_tehran()
    tx_date = now_tehran()
    custom_date = data.get('custom_date')
    if custom_date:
        try:
            tx_date = datetime.fromisoformat(custom_date)
        except Exception:
            pass
    tx = Transaction(
        product_id=pid, type=tx_type, quantity=qty,
        before_qty=before, after_qty=p.quantity,
        note=data.get('note',''), ref_number=data.get('ref_number',''),
        created_by=user.display_name or user.username,
        created_at=tx_date,
    )
    db.session.add(tx)
    db.session.commit()
    return jsonify({'product': p.to_dict(), 'transaction': tx.to_dict()}), 201

@app.route('/api/products/<int:pid>/transactions', methods=['GET'])
@require_auth()
def get_transactions(pid):
    txs = Transaction.query.filter_by(product_id=pid).order_by(Transaction.created_at.desc()).limit(50).all()
    return jsonify([t.to_dict() for t in txs])

@app.route('/api/transactions', methods=['GET'])
@require_auth()
def get_all_transactions():
    search = request.args.get('search','').strip()
    tx_type = request.args.get('type','').strip()
    wh_id = request.args.get('warehouse_id','').strip()
    query = Transaction.query.join(Product, Transaction.product_id == Product.id)
    if search:
        query = query.filter(db.or_(
            Product.name.ilike(f'%{search}%'),
            Transaction.note.ilike(f'%{search}%'),
            Transaction.ref_number.ilike(f'%{search}%'),
        ))
    if tx_type in ('in','out','adjust'):
        query = query.filter(Transaction.type == tx_type)
    if wh_id:
        query = query.join(Warehouse, Product.warehouse_id == Warehouse.id).filter(Warehouse.id == int(wh_id))
    txs = query.order_by(Transaction.created_at.desc()).limit(500).all()
    return jsonify([t.to_dict() for t in txs])


# ─── Dashboard / Reports ───────────────────────────────────────────────────────

@app.route('/api/dashboard', methods=['GET'])
@require_auth()
def dashboard():
    warehouses = Warehouse.query.filter_by(is_active=True).all()
    products = Product.query.filter_by(is_active=True).all()
    low_stock = [p.to_dict() for p in products if p.min_stock and p.quantity <= p.min_stock]
    out_of_stock = [p.to_dict() for p in products if p.quantity <= 0]
    total_value = sum(p.quantity * (p.unit_price or 0) for p in products)
    recent_txs = Transaction.query.order_by(Transaction.created_at.desc()).limit(10).all()
    return jsonify({
        'warehouse_count': len(warehouses),
        'product_count': len(products),
        'low_stock_count': len(low_stock),
        'out_of_stock_count': len(out_of_stock),
        'total_value': total_value,
        'low_stock': low_stock[:5],
        'out_of_stock': out_of_stock[:5],
        'recent_transactions': [t.to_dict() for t in recent_txs],
    })

@app.route('/api/report/inventory', methods=['GET'])
@require_auth()
def inventory_report():
    wid = request.args.get('warehouse_id')
    status_filter = request.args.get('status')
    query = Product.query.filter_by(is_active=True)
    if wid:
        query = query.filter_by(warehouse_id=int(wid))
    products = query.all()
    product_dicts = [p.to_dict() for p in products]
    if status_filter in ('normal','low','out'):
        product_dicts = [p for p in product_dicts if p['status'] == status_filter]
    now_j = jdatetime.datetime.fromgregorian(datetime=now_tehran()).strftime('%Y/%m/%d %H:%M')
    return jsonify({
        'generated_at': now_j,
        'products': product_dicts,
        'total_value': sum(p['quantity'] * p['unit_price'] for p in product_dicts),
    })


# ─── Init ─────────────────────────────────────────────────────────────────────

def init_admin():
    """ساخت ادمین پیش‌فرض اگه هیچ کاربری وجود نداشت"""
    if User.query.count() == 0:
        admin_user = os.environ.get('ADMIN_USERNAME', 'admin')
        admin_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        u = User(username=admin_user, display_name='مدیر سیستم', role='admin')
        u.set_password(admin_pass)
        db.session.add(u)
        db.session.commit()
        print(f'[INIT] ادمین پیش‌فرض ساخته شد: {admin_user} / {admin_pass}')
        print('[INIT] بعد از ورود حتماً رمز عبور را تغییر دهید!')

with app.app_context():
    db.create_all()
    init_admin()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
