| [English](README.md) | [فارسی](README_FA.md) |
|---|---|

<div align="center">

# 📦 انبار-ایکس

**سیستم مدیریت انبار خود-میزبان، طراحی‌شده برای شبکه‌های داخلی (LAN)**

![نسخه](https://img.shields.io/badge/نسخه-2.3.0-blue)
![Python](https://img.shields.io/badge/python-3.11-green)
![Flask](https://img.shields.io/badge/flask-3.0-lightgrey)
![Docker](https://img.shields.io/badge/docker-ready-blue)

</div>

---

## ✨ امکانات

- **مدیریت چند انبار** — ساخت، ویرایش و پایش چند انبار با آمار لحظه‌ای
- **دفتر کالایی** — تعریف کالا جدا از خرید؛ قیمت و موجودی از حرکات واقعی
- **کارت کالا** — تاریخچه حرکات، گزارش خریدها، میانگین هزینه و ارزش موجودی
- **خروج رسمی** — صدور کالا به بخش‌های مشخص (مثل رادیولوژی، پذیرش) با امکان بازگشت
- **گزارش فیلترپذیر** — جستجو و بازه تاریخ در حرکات انبار و گزارش‌ها
- **سطح دسترسی** — سه نقش مدیر / اپراتور / بازدیدکننده با لاگ ورود قابل فیلتر
- **گزارش و خروجی** — گزارش موجودی و گزارش بخش‌ها، قابل چاپ و خروجی Excel
- **تقویم شمسی** — همه تاریخ‌ها به هجری شمسی نمایش داده می‌شوند
- **دارک / لایت مود** — تغییر نرم تم با ذخیره انتخاب کاربر
- **ریسپانسیو موبایل** — لیست‌های کارتی بدون اسکرول افقی روی گوشی
- **بک‌آپ خودکار روزانه** — هر شب ساعت ۲ بامداد به وقت تهران، نگهداری ۳۰ روز
- **مناسب بدون اینترنت** — طراحی‌شده برای سرورهای آفلاین داخل شبکه

---

## 🛠 تکنولوژی‌ها

| لایه | فناوری |
|---|---|
| بک‌اند | Python 3.11، Flask 3.0، SQLAlchemy، SQLite |
| فرانت‌اند | HTML / CSS / JavaScript خالص (یک فایل) |
| سرور | Waitress (WSGI پروداکشن) |
| استقرار | Docker + Docker Compose |
| احراز هویت | bcrypt، نشست مبتنی بر توکن |

---

## 🚀 راه‌اندازی سریع (Docker)

**پیش‌نیاز:** [Docker Desktop](https://www.docker.com/products/docker-desktop/)

```bash
git clone https://github.com/YOUR_USERNAME/anbar-x.git
cd anbar-x
docker compose up --build -d
```

مرورگر را باز کنید: `http://localhost:5000`

**اطلاعات ورود پیش‌فرض:**
```
نام کاربری: admin
رمز عبور:   admin123
```
> ⚠️ بلافاصله بعد از اولین ورود، رمز عبور را تغییر دهید.

---

## 🌐 استقرار روی شبکه داخلی (چند کاربر)

همه کاربران شبکه می‌توانند از طریق IP سرور وارد شوند:

```
http://192.168.x.x:5000
```

پیدا کردن IP سرور:
```bash
# ویندوز
ipconfig

# لینوکس
hostname -I
```

---

## 📁 ساختار پروژه

```
anbar-x/
├── backend/
│   ├── app.py              ← سرور Flask (تمام منطق برنامه)
│   └── requirements.txt    ← وابستگی‌های Python
├── frontend/
│   ├── index.html          ← رابط کاربری کامل (HTML + CSS + JS)
│   └── xlsx.full.min.js    ← SheetJS برای خروجی Excel (آفلاین)
├── data/
│   └── .gitkeep            ← دیتابیس اینجا ذخیره می‌شود (Docker volume)
├── Dockerfile
├── docker-compose.yml
├── start.bat               ← ویندوز: روشن کردن برنامه
├── stop.bat                ← ویندوز: خاموش کردن برنامه
└── make-tar.bat            ← ویندوز: خروجی Docker برای انتقال آفلاین
```

---

## 🔐 سطوح دسترسی

| نقش | مشاهده | ویرایش موجودی | مدیریت انبار | مدیریت کاربران |
|---|---|---|---|---|
| 👑 مدیر | ✅ | ✅ | ✅ | ✅ |
| ✏️ اپراتور | ✅ | ✅ | ✅ | ❌ |
| 👁 بازدیدکننده | ✅ | ❌ | ❌ | ❌ |

---

## 📦 انتقال به سرور آفلاین

```bash
# روی کامپیوتر با اینترنت
docker compose build
docker save -o warehouse-app.tar anbar-x-warehouse:latest

# فایل‌ها را منتقل کنید:
# warehouse-app.tar و docker-compose.yml

# روی سرور آفلاین
docker load -i warehouse-app.tar
docker compose up -d
```

---

## 🗄 دیتابیس و بک‌آپ

- دیتابیس: `data/warehouse.db` (SQLite، به صورت Docker volume — با rebuild پاک نمی‌شود)
- بک‌آپ خودکار: هر شب ساعت ۲ بامداد → `data/backups/warehouse_YYYY-MM-DD.db`
- نگهداری: ۳۰ بک‌آپ آخر به صورت خودکار حفظ می‌شوند

برای مرور یا ویرایش دستی دیتابیس: [DB Browser for SQLite](https://sqlitebrowser.org)

---

## 📡 مرجع API

| متد | آدرس | توضیح |
|---|---|---|
| POST | /api/auth/login | ورود |
| GET | /api/warehouses | لیست انبارها |
| POST | /api/warehouses | انبار جدید |
| GET | /api/warehouses/:id/products | کالاهای یک انبار |
| GET | /api/products | همه کالاها |
| POST | /api/products | کالای جدید |
| POST | /api/products/:id/transactions | ثبت تراکنش |
| GET | /api/departments | لیست بخش‌ها |
| POST | /api/dispatch | خروج رسمی به بخش |
| POST | /api/dispatch/:id/reverse | بازگشت خروج رسمی |
| GET | /api/dispatch/report | گزارش بخش‌ها |
| GET | /api/dashboard | آمار داشبورد |
| GET | /api/report/inventory | گزارش موجودی |
| GET | /api/users | لیست کاربران (فقط مدیر) |
| GET | /api/login-logs | تاریخچه ورود (فقط مدیر) |

---

## 📄 مجوز

MIT © 1404
