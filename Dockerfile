FROM python:3.11-slim

WORKDIR /app

# نصب پکیج‌ها (این لایه کش میشه و سریع‌تر build میشه)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کد بک‌اند
COPY backend/ .

# کپی فرانت‌اند تا Flask بتونه سرو کنه
COPY frontend/ /frontend/
ENV FRONTEND_DIR=/frontend

# پوشه دیتابیس - این مسیر باید با volume نگاشت بشه
ENV DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 5000

CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--threads=8", "app:app"]
