# 📦 انبارگردان — راهنمای نصب و راه‌اندازی

## ساختار پروژه

```
warehouse-app/
├── backend/
│   ├── app.py           ← سرور اصلی (Flask)
│   ├── requirements.txt ← کتابخانه‌های Python
│   └── warehouse.db     ← دیتابیس (خودکار ساخته می‌شود)
└── frontend/
    └── index.html       ← رابط کاربری (یک فایل کامل)
```

---

## پیش‌نیازها

- **Python 3.9+** — دانلود: https://python.org
- مرورگر کروم یا فایرفاکس

---

## نصب و راه‌اندازی

### ۱. نصب کتابخانه‌های Python

```bash
cd warehouse-app/backend
pip install -r requirements.txt
```

### ۲. اجرای سرور

```bash
python app.py
```

> پیام زیر نشان می‌دهد سرور آماده است:
> ```
> * Running on http://0.0.0.0:5000
> ```

### ۳. باز کردن رابط کاربری

فایل `frontend/index.html` را مستقیماً در مرورگر باز کنید.

---

## استفاده روی شبکه داخلی (LAN) — چند کاربر

### روی سرور / کامپیوتر اصلی:
```bash
python app.py
```
IP سرور را پیدا کنید:
```bash
# Windows:
ipconfig

# Linux/Mac:
hostname -I
```
فرض کنید IP سرور `192.168.1.100` است.

### ویرایش فایل index.html برای سایر کاربران:
خط زیر را در `index.html` پیدا کنید:
```javascript
const API = 'http://localhost:5000/api';
```
و تغییر دهید به:
```javascript
const API = 'http://192.168.1.100:5000/api';
```

### سایر کاربران:
فایل `index.html` ویرایش‌شده را روی کامپیوترشان کپی کنند و در مرورگر باز کنند.

---

## امکانات سیستم

### 🏭 مدیریت انبار
- ساخت، ویرایش، حذف انبار
- هر انبار دارای نام، مکان و توضیحات
- مشاهده آمار هر انبار (تعداد کالا، موجودی کل، ارزش)

### 📦 مدیریت کالا
- تعریف کالا با: نام، کد SKU، دسته‌بندی، واحد، قیمت
- تعیین حداقل موجودی (هشدار خودکار)
- هشدار موجودی کم (زرد) و اتمام (قرمز)

### 🔄 تراکنش‌ها
- **ورود کالا** ⬆️ — افزایش موجودی
- **خروج کالا** ⬇️ — کاهش موجودی
- **تنظیم موجودی** ⚖️ — تعیین مستقیم
- ثبت شماره مرجع (فاکتور/سند)
- تاریخچه کامل تراکنش‌ها

### 📊 داشبورد
- آمار کلی سیستم
- لیست کالاهای بحرانی
- آخرین تراکنش‌ها

### 📋 گزارش موجودی
- گزارش فیلتر بر اساس انبار
- تاریخ هجری شمسی
- قابل چاپ

---

## اجرای خودکار هنگام روشن شدن سرور (اختیاری)

### Windows — Task Scheduler:
یک فایل `start.bat` بسازید:
```batch
@echo off
cd /d "C:\path\to\warehouse-app\backend"
python app.py
```
سپس در Task Scheduler تنظیم کنید که هنگام ورود به Windows اجرا شود.

### Linux — Systemd:
```ini
# /etc/systemd/system/warehouse.service
[Unit]
Description=Warehouse App

[Service]
WorkingDirectory=/path/to/warehouse-app/backend
ExecStart=/usr/bin/python3 app.py
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable warehouse
sudo systemctl start warehouse
```

---

## بک‌آپ دیتابیس

فقط کافیه فایل `backend/warehouse.db` را کپی کنید!

```bash
# مثال بک‌آپ روزانه (Linux cron):
0 2 * * * cp /path/to/warehouse.db /backups/warehouse_$(date +\%Y\%m\%d).db
```

---

## API مرجع (برای توسعه‌دهندگان)

| Method | Endpoint | توضیح |
|--------|----------|-------|
| GET    | /api/warehouses | لیست انبارها |
| POST   | /api/warehouses | انبار جدید |
| PUT    | /api/warehouses/:id | ویرایش انبار |
| DELETE | /api/warehouses/:id | حذف انبار |
| GET    | /api/warehouses/:id/products | کالاهای یک انبار |
| GET    | /api/products | همه کالاها |
| POST   | /api/products | کالای جدید |
| PUT    | /api/products/:id | ویرایش کالا |
| DELETE | /api/products/:id | حذف کالا |
| POST   | /api/products/:id/transactions | ثبت تراکنش |
| GET    | /api/products/:id/transactions | تاریخچه تراکنش |
| GET    | /api/transactions | همه تراکنش‌ها |
| GET    | /api/dashboard | آمار داشبورد |
| GET    | /api/report/inventory | گزارش موجودی |
