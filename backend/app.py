from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload
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
            'total_value': sum(p.quantity * (p.unit_price or 0) for p in active_products),
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
    unit_price    = db.Column(db.Float, default=0)
    min_stock     = db.Column(db.Float, default=0)
    description   = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, default=now_tehran)
    updated_at    = db.Column(db.DateTime, default=now_tehran, onupdate=now_tehran)
    is_active     = db.Column(db.Boolean, default=True, index=True)
    manual_status = db.Column(db.String(20), default=None)
    transactions  = db.relationship('Transaction', backref='product', lazy=True, cascade='all, delete-orphan')

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
            'status': self.get_status(),
            'manual_status': self.manual_status or '',
            'total_value': self.quantity * (self.unit_price or 0),
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
    note          = db.Column(db.Text)
    ref_number    = db.Column(db.String(100))
    created_at    = db.Column(db.DateTime, default=now_tehran, index=True)
    created_by    = db.Column(db.String(100), default='کاربر')
    department    = db.relationship('Department', backref='transactions')

    def to_dict(self):
        jdt = jdatetime.datetime.fromgregorian(datetime=self.created_at)
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'warehouse_name': self.product.warehouse.name if self.product and self.product.warehouse else '',
            'department_id': self.department_id,
            'department_name': self.department.name if self.department else '',
            'type': self.type,
            'quantity': self.quantity,
            'before_qty': self.before_qty,
            'after_qty': self.after_qty,
            'note': self.note or '',
            'ref_number': self.ref_number or '',
            'created_at': jdt.strftime('%Y/%m/%d %H:%M'),
            'created_by': self.created_by,
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
    return jsonify([u.to_dict() for u in User.query.order_by(User.id.desc()).all()])

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
    whs = Warehouse.query.filter_by(is_active=True).all()
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
    data = request.json or {}
    user = get_current_user()
    if not data.get('name') or not data.get('warehouse_id'):
        return jsonify({'error': 'نام کالا و انبار الزامی است'}), 400
    p = Product(
        warehouse_id=data['warehouse_id'], name=data['name'],
        sku=data.get('sku', ''), category=data.get('category', ''),
        quantity=float(data.get('quantity', 0)), unit=data.get('unit', 'عدد'),
        unit_price=float(data.get('unit_price', 0)), min_stock=float(data.get('min_stock', 0)),
        description=data.get('description', ''),
    )
    db.session.add(p)
    db.session.flush()
    if p.quantity > 0:
        db.session.add(Transaction(
            product_id=p.id, type='in', quantity=p.quantity,
            before_qty=0, after_qty=p.quantity, note='موجودی اولیه',
            created_by=user.display_name or user.username,
        ))
    db.session.commit()
    return jsonify(p.to_dict()), 201

@app.route('/api/products/<int:pid>', methods=['PUT'])
@require_auth('operator')
def update_product(pid):
    p = Product.query.get_or_404(pid)
    user = get_current_user()
    data = request.json or {}
    for f in ['name', 'sku', 'category', 'unit', 'unit_price', 'min_stock', 'description']:
        if f in data: setattr(p, f, data[f])
    if 'quantity' in data:
        new_qty = float(data['quantity'])
        if new_qty != p.quantity:
            before = p.quantity
            p.quantity = new_qty
            note = (data.get('note') or '').strip() or 'ویرایش مستقیم موجودی'
            db.session.add(Transaction(
                product_id=pid, type='adjust', quantity=new_qty,
                before_qty=before, after_qty=new_qty, note=note,
                ref_number=data.get('ref_number', ''),
                created_by=user.display_name or user.username,
            ))
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
    qty = float(data.get('quantity', 0))
    if tx_type not in ('in', 'out', 'adjust'):
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
    else:
        p.quantity = qty
    manual_status = data.get('manual_status')
    if manual_status in ('available', 'unavailable', 'low'):
        p.manual_status = manual_status
    elif manual_status is None:
        p.manual_status = None
    p.updated_at = now_tehran()
    tx_date = now_tehran()
    if data.get('custom_date'):
        try: tx_date = datetime.fromisoformat(data['custom_date'])
        except: pass
    tx = Transaction(
        product_id=pid, type=tx_type, quantity=qty,
        before_qty=before, after_qty=p.quantity,
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
    tx_type = request.args.get('type', '').strip()
    wh_id   = request.args.get('warehouse_id', '').strip()
    dept_id = request.args.get('department_id', '').strip()

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
    if tx_type in ('in', 'out', 'adjust'):
        query = query.filter(Transaction.type == tx_type)
    if wh_id:
        query = query.filter(Product.warehouse_id == int(wh_id))
    if dept_id:
        query = query.filter(Transaction.department_id == int(dept_id))
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
    product.quantity -= quantity
    product.updated_at = now_tehran()
    tx = Transaction(
        product_id=product_id, department_id=department_id,
        type='out', quantity=quantity,
        before_qty=before, after_qty=product.quantity,
        note=note, created_by=user.display_name or user.username,
    )
    db.session.add(tx)
    db.session.commit()
    return jsonify({'transaction': tx.to_dict(), 'product': product.to_dict()}), 201

@app.route('/api/dispatch/report', methods=['GET'])
@require_auth()
def dispatch_report():
    dept_id   = request.args.get('department_id', '').strip()
    product_q = request.args.get('product', '').strip()
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
    warehouses   = Warehouse.query.filter_by(is_active=True).all()
    products     = Product.query.filter_by(is_active=True).all()
    low_stock    = [p for p in products if p.min_stock and 0 < p.quantity <= p.min_stock]
    out_of_stock = [p for p in products if p.quantity <= 0]
    total_value  = sum(p.quantity * (p.unit_price or 0) for p in products)
    recent_txs   = (Transaction.query
                    .options(
                        joinedload(Transaction.product).joinedload(Product.warehouse),
                        joinedload(Transaction.department),
                    )
                    .order_by(Transaction.created_at.desc()).limit(10).all())
    return jsonify({
        'warehouse_count':   len(warehouses),
        'product_count':     len(products),
        'low_stock_count':   len(low_stock),
        'out_of_stock_count': len(out_of_stock),
        'total_value':       total_value,
        'low_stock':         [p.to_dict() for p in low_stock[:5]],
        'out_of_stock':      [p.to_dict() for p in out_of_stock[:5]],
        'recent_transactions': [t.to_dict() for t in recent_txs],
    })

@app.route('/api/report/inventory', methods=['GET'])
@require_auth()
def inventory_report():
    wid = request.args.get('warehouse_id')
    status_filter = request.args.get('status')
    query = Product.query.filter_by(is_active=True)
    if wid: query = query.filter_by(warehouse_id=int(wid))
    products = query.all()
    product_dicts = [p.to_dict() for p in products]
    if status_filter in ('normal', 'low', 'out'):
        product_dicts = [p for p in product_dicts if p['status'] == status_filter]
    now_j = jdatetime.datetime.fromgregorian(datetime=now_tehran()).strftime('%Y/%m/%d %H:%M')
    return jsonify({
        'generated_at': now_j,
        'products': product_dicts,
        'total_value': sum(p['quantity'] * p['unit_price'] for p in product_dicts),
    })


# ═══════════════════════════════════════════════════════════════════
# INIT
# ═══════════════════════════════════════════════════════════════════

def migrate():
    """اضافه کردن ستون‌های جدید به دیتابیس قدیمی"""
    with db.engine.connect() as conn:
        try:
            conn.execute(db.text('ALTER TABLE transactions ADD COLUMN department_id INTEGER REFERENCES departments(id)'))
            conn.commit()
            print('[MIGRATE] department_id added to transactions')
        except Exception:
            pass

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
