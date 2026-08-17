import json
import os
import sqlite3
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

# .env içindeki metni JSON olarak Python listesine dönüştürür
test_users_raw = os.getenv("TEST_USERS")
test_users = json.loads(test_users_raw) if test_users_raw else []

# Veritabanı Yolu
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_data.db")


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Kullanıcılar Tablosu
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)

    # 2. Sohbet Oturumu (Konuşmalar) Tablosu
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3. Sohbet Mesaj Geçmişi Tablosu (session_id bağlı)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES conversations (id)
        )
    """)

    # 4. İzin ve Belge Talepleri Tablosu
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            request_type TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'Beklemede',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # .env'den okunan kullanıcıları veritabanına ekleme
    for user in test_users:
        u = user.get("username")
        p = user.get("password")
        fn = user.get("full_name")
        r = user.get("role")

        if u:
            cursor.execute("SELECT id FROM users WHERE username = ?", (u,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
                    (u, p, fn, r),
                )

    conn.commit()
    conn.close()


def verify_user(username, password):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT full_name, role FROM users WHERE username = ? AND password = ?",
        (username, password),
    )
    user = cursor.fetchone()
    conn.close()
    return user


# --- SOHBET OTURUMU VE MESAJ FONKSİYONLARI ---
def create_conversation(username, title):
    """Yeni bir sohbet oturumu başlatır ve session_id döndürür."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO conversations (username, title) VALUES (?, ?)",
        (username, title),
    )
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return session_id


def get_user_conversations(username):
    """Kullanıcının tüm sohbet başlıklarını getirir."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, title, created_at FROM conversations WHERE username = ? ORDER BY id DESC",
        (username,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def save_chat_message(session_id, role, content):
    """Belli bir oturuma ait mesajı kaydeder."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_history (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content),
    )
    conn.commit()
    conn.close()


def get_session_chat_history(session_id):
    """Seçilen oturumun mesajlarını getirir."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM chat_history WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in rows]


def delete_conversation(session_id):
    """Bir sohbet oturumunu ve mesajlarını siler."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM chat_history WHERE session_id = ?", (session_id,)
    )
    cursor.execute("DELETE FROM conversations WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


# --- İZİN VE TALEP FONKSİYONLARI ---
def create_request(username, request_type, start_date, end_date, description):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO requests (username, request_type, start_date, end_date, description) VALUES (?, ?, ?, ?, ?)",
        (username, request_type, str(start_date), str(end_date), description),
    )
    conn.commit()
    conn.close()


def get_user_requests(username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT request_type, start_date, end_date, status, created_at FROM requests WHERE username = ? ORDER BY id DESC",
        (username,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def update_request_status(request_id, new_status):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE requests SET status = ? WHERE id = ?", (new_status, request_id)
    )
    conn.commit()
    conn.close()


def get_all_pending_requests():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.id, u.full_name, r.request_type, r.start_date, r.end_date, r.description, r.created_at
        FROM requests r
        JOIN users u ON r.username = u.username
        WHERE r.status = 'Beklemede'
        ORDER BY r.id DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


# Bu dosya doğrudan çalıştırıldığında veritabanını ilklendirir
if __name__ == "__main__":
    init_db()
    print("Veritabanı ve tablolar başarıyla oluşturuldu/güncellendi.")