| [English](README.md) | [فارسی](README_FA.md) |
|---|---|

<div align="center">

# 📦 Anbar-X

**A self-hosted, multi-warehouse inventory management system built for LAN environments.**

![Version](https://img.shields.io/badge/version-2.2.0-blue)
![Python](https://img.shields.io/badge/python-3.11-green)
![Flask](https://img.shields.io/badge/flask-3.0-lightgrey)
![Docker](https://img.shields.io/badge/docker-ready-blue)
![License](https://img.shields.io/badge/license-MIT-orange)

</div>

---

## ✨ Features

- **Multi-warehouse management** — create, edit, and monitor multiple warehouses with live statistics
- **Product inventory** — track items with SKU, category, unit price, and low-stock alerts
- **Formal dispatch** — issue stock to named departments (e.g. Radiology, Reception) with reversal support
- **Transaction history** — every stock movement is logged with timestamp, user, and notes
- **Role-based access** — Admin / Operator / Viewer with session management and login logs
- **Reports & Export** — inventory reports and department reports, printable and exportable to Excel
- **Jalali (Shamsi) calendar** — all dates displayed in Persian calendar
- **Dark / Light mode** — smooth theme toggle with preference saved per browser
- **Responsive UI** — works on desktop and mobile
- **Automatic daily backup** — database backed up every night at 2 AM (Tehran time), 30-day retention
- **Offline-ready** — designed for air-gapped LAN servers with no internet dependency

---

## 🛠 Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask 3.0, SQLAlchemy, SQLite |
| Frontend | Vanilla HTML / CSS / JavaScript (single file) |
| Server | Waitress (production WSGI) |
| Deployment | Docker + Docker Compose |
| Auth | bcrypt password hashing, token-based sessions |

---

## 🚀 Quick Start (Docker)

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/)

```bash
git clone https://github.com/YOUR_USERNAME/anbar-x.git
cd anbar-x
docker compose up --build -d
```

Open your browser at `http://localhost:5000`

**Default credentials:**
```
Username: admin
Password: admin123
```
> ⚠️ Change the password immediately after first login.

---

## 🌐 LAN Deployment (Multi-user)

All users on the same network can access the system through the server's IP:

```
http://192.168.x.x:5000
```

Find the server IP:
```bash
# Windows
ipconfig

# Linux
hostname -I
```

---

## 📁 Project Structure

```
anbar-x/
├── backend/
│   ├── app.py              ← Flask server (all business logic)
│   └── requirements.txt    ← Python dependencies
├── frontend/
│   ├── index.html          ← Full UI (HTML + CSS + JS in one file)
│   └── xlsx.full.min.js    ← SheetJS for Excel export (offline)
├── data/
│   └── .gitkeep            ← Database lives here (mounted as Docker volume)
├── Dockerfile
├── docker-compose.yml
├── start.bat               ← Windows: start the app
├── stop.bat                ← Windows: stop the app
└── make-tar.bat            ← Windows: export Docker image for offline transfer
```

---

## 🔐 User Roles

| Role | View | Edit Stock | Manage Warehouses | Manage Users |
|---|---|---|---|---|
| 👑 Admin | ✅ | ✅ | ✅ | ✅ |
| ✏️ Operator | ✅ | ✅ | ✅ | ❌ |
| 👁 Viewer | ✅ | ❌ | ❌ | ❌ |

---

## 📦 Offline Transfer

To move the app to a server without internet access:

```bash
# On the internet-connected machine
docker compose build
docker save -o warehouse-app.tar anbar-x-warehouse:latest

# Transfer warehouse-app.tar and docker-compose.yml to the target server

# On the offline server
docker load -i warehouse-app.tar
docker compose up -d
```

---

## 🗄 Database & Backup

- Database: `data/warehouse.db` (SQLite, mounted as Docker volume — persists across rebuilds)
- Automatic backup: runs nightly at 02:00 Tehran time → `data/backups/warehouse_YYYY-MM-DD.db`
- Retention: last 30 daily backups are kept automatically

To browse or edit the database manually: [DB Browser for SQLite](https://sqlitebrowser.org)

---

## 📡 API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | /api/auth/login | Login |
| GET | /api/warehouses | List warehouses |
| POST | /api/warehouses | Create warehouse |
| GET | /api/warehouses/:id/products | Products in a warehouse |
| GET | /api/products | All products |
| POST | /api/products | Create product |
| POST | /api/products/:id/transactions | Add transaction |
| GET | /api/departments | List departments |
| POST | /api/dispatch | Formal dispatch to department |
| POST | /api/dispatch/:id/reverse | Reverse a dispatch |
| GET | /api/dispatch/report | Dispatch report |
| GET | /api/dashboard | Dashboard stats |
| GET | /api/report/inventory | Inventory report |
| GET | /api/users | List users (admin only) |
| GET | /api/login-logs | Login history (admin only) |

---

## 📄 License

MIT © 2025
