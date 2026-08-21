import hmac
import json
import os
import sqlite3
import hashlib
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

# .env içindeki TEST_USERS JSON verisini güvenli şekilde oku
test_users_raw = os.getenv("TEST_USERS")
test_users = []
if test_users_raw:
    try:
        test_users = json.loads(test_users_raw)
        if not isinstance(test_users, list):
            test_users = []
    except (json.JSONDecodeError, TypeError):
        test_users = []

# Veritabanı Yolu
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_data.db")


# --- ŞİFRE HASH'LEME (PBKDF2-HMAC-SHA256) ---
def _hash_password(password: str, salt: bytes = None) -> str:
    """'pbkdf2$<salt_hex>$<hash_hex>' formatında güvenli hash üretir."""
    if not password:
        password = ""
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def _is_hashed(stored_password: str) -> bool:
    return bool(stored_password and stored_password.startswith("pbkdf2$"))


def _verify_password(password: str, stored_password: str) -> bool:
    if not stored_password or not password:
        return False

    if _is_hashed(stored_password):
        try:
            parts = stored_password.split("$")
            if len(parts) != 3:
                return False
            _, salt_hex, hash_hex = parts
            salt = bytes.fromhex(salt_hex)
            candidate = _hash_password(password, salt)
            # Zamanlama saldırılarına (timing attack) karşı güvenli karşılaştırma
            return hmac.compare_digest(candidate, stored_password)
        except (ValueError, TypeError):
            return False

    # Eski düz metin şifre kontrolü
    return hmac.compare_digest(password, stored_password)


def get_connection():
    """SQLite bağlantısını optimize edilmiş ayarlarla açar."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_table_columns(cursor, table_name: str, required_columns: dict):
    """Mevcut tablodaki eksik kolonları veri kaybı olmadan ekler."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_cols = {row[1] for row in cursor.fetchall()}
    for col_name, col_def in required_columns.items():
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")


def init_db():
    conn = get_connection()
    try:
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

        # 3. Sohbet Mesaj Geçmişi Tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES conversations (id) ON DELETE CASCADE
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

        # 5. Hazır Sorular Tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preset_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_text TEXT NOT NULL,
                created_by TEXT,
                click_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 6. Şirket Bina / Lokasyon Bilgileri Tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS company_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                building_name TEXT NOT NULL,
                description TEXT NOT NULL,
                dress_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Güvenli Kolon Kontrolü (Veri kaybetmeden kolon tamamlama)
        _migrate_table_columns(cursor, "preset_questions", {
            "created_by": "TEXT",
            "click_count": "INTEGER DEFAULT 0",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        })
        _migrate_table_columns(cursor, "company_locations", {
            "dress_code": "TEXT",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        })

        # .env'den okunan test kullanıcılarını ekle
        for user in test_users:
            if not isinstance(user, dict):
                continue
            u = user.get("username")
            p = user.get("password", "123456")
            fn = user.get("full_name", u)
            r = user.get("role", "Çalışan")

            if u and p:
                cursor.execute("SELECT id FROM users WHERE username = ?", (u.strip(),))
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
                        (u.strip(), _hash_password(p), fn.strip(), r.strip()),
                    )

        conn.commit()
    finally:
        conn.close()


def verify_user(username, password):
    if not username or not password:
        return None

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT full_name, role, password FROM users WHERE username = ?",
            (username.strip(),),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        full_name, role, stored_password = row
        if not _verify_password(password, stored_password):
            return None

        # Eski düz metin şifreyse PBKDF2'ye migrate et
        if not _is_hashed(stored_password):
            cursor.execute(
                "UPDATE users SET password = ? WHERE username = ?",
                (_hash_password(password), username.strip()),
            )
            conn.commit()

        return (full_name, role)
    finally:
        conn.close()


def add_user(username, password, full_name, role="Çalışan"):
    """Yeni kullanıcı kaydı oluşturur. Kullanıcı adı zaten varsa False döner."""
    if not username or not password or not full_name:
        return False

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (username.strip(),))
        if cursor.fetchone():
            return False

        cursor.execute(
            "INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
            (username.strip(), _hash_password(password), full_name.strip(), role.strip()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# --- SOHBET OTURUMU VE MESAJ FONKSİYONLARI ---
def create_conversation(username, title):
    """Yeni bir sohbet oturumu başlatır ve session_id döndürür."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (username, title) VALUES (?, ?)",
            (username.strip(), title.strip()),
        )
        session_id = cursor.lastrowid
        conn.commit()
        return session_id
    finally:
        conn.close()


def get_user_conversations(username):
    """Kullanıcının tüm sohbet başlıklarını getirir."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, created_at FROM conversations WHERE username = ? ORDER BY id DESC",
            (username,),
        )
        return cursor.fetchall()
    finally:
        conn.close()


def save_chat_message(session_id, role, content):
    """Belli bir oturuma ait mesajı kaydeder."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_history (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.commit()
    finally:
        conn.close()


def get_session_chat_history(session_id):
    """Seçilen oturumun mesajlarını getirir."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, content FROM chat_history WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        rows = cursor.fetchall()
        return [{"role": row[0], "content": row[1]} for row in rows]
    finally:
        conn.close()


def delete_conversation(session_id):
    """Bir sohbet oturumunu ve mesajlarını siler."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM conversations WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


# --- HAZIR SORU (PRESET QUESTION) FONKSİYONLARI ---
def add_preset_question(question_text, created_by=None):
    """Yeni bir hazır soru ekler."""
    if not question_text:
        return
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO preset_questions (question_text, created_by) VALUES (?, ?)",
            (question_text.strip(), created_by),
        )
        conn.commit()
    finally:
        conn.close()


def get_preset_questions(limit=8):
    """En çok tıklanan hazır soruları getirir."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, question_text, click_count FROM preset_questions "
            "ORDER BY click_count DESC, id DESC LIMIT ?",
            (limit,),
        )
        return cursor.fetchall()
    finally:
        conn.close()


def delete_preset_question(question_id):
    """Bir hazır soruyu siler."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM preset_questions WHERE id = ?", (question_id,))
        conn.commit()
    finally:
        conn.close()


def increment_question_click(question_id):
    """Bir hazır sorunun tıklanma sayacını 1 artırır."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE preset_questions SET click_count = click_count + 1 WHERE id = ?",
            (question_id,),
        )
        conn.commit()
    finally:
        conn.close()


# --- ŞİRKET BİNA / LOKASYON FONKSİYONLARI ---
def add_company_location(building_name, description, dress_code=None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO company_locations (building_name, description, dress_code) VALUES (?, ?, ?)",
            (building_name, description, dress_code),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_company_locations():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT building_name, description, dress_code FROM company_locations ORDER BY id ASC"
        )
        return cursor.fetchall()
    finally:
        conn.close()


# --- İZİN VE TALEP FONKSİYONLARI ---
def create_request(username, request_type, start_date, end_date, description):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO requests (username, request_type, start_date, end_date, description) VALUES (?, ?, ?, ?, ?)",
            (username, request_type, str(start_date), str(end_date), description),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_requests(username):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, request_type, start_date, end_date, status, created_at, description FROM requests WHERE username = ? ORDER BY id DESC",
            (username,),
        )
        return cursor.fetchall()
    finally:
        conn.close()


def update_request_status(request_id, new_status):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE requests SET status = ? WHERE id = ?", (new_status, request_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_all_pending_requests():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.id, u.full_name, r.request_type, r.start_date, r.end_date, r.description, r.created_at
            FROM requests r
            JOIN users u ON r.username = u.username
            WHERE r.status = 'Beklemede'
            ORDER BY r.id DESC
        """)
        return cursor.fetchall()
    finally:
        conn.close()
import sqlite3

def init_feedback_db():
    """Oylamaların ve geri bildirimlerin tutulacağı tabloyu oluşturur."""
    conn = sqlite3.connect("app_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            filename TEXT,
            prompt TEXT,
            response TEXT,
            rating INTEGER, -- 1: 👍, 0: 👎
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_feedback(username, filename, prompt, response, rating):
    """Kullanıcının oyunu (👍/👎) veritabanına kaydeder."""
    conn = sqlite3.connect("app_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO feedbacks (username, filename, prompt, response, rating)
        VALUES (?, ?, ?, ?, ?)
    """, (username, filename, prompt, response, rating))
    conn.commit()
    conn.close()

def get_pdf_alarm_status():
    """
    Doküman bazında oyları analiz eder.
    En az 20 oy toplanmış VE olumsuzluk oranı %70 veya üzerindeyse alarm üretir.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COALESCE(NULLIF(filename, ''), 'cimtas_sıksorulansorular.pdf') as pdf_file,
                COUNT(*) as total_votes,
                SUM(CASE WHEN rating = 0 THEN 1 ELSE 0 END) as dislikes
            FROM feedbacks
            GROUP BY pdf_file
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()
    
    alarm_dict = {}
    for filename, total, dislikes in rows:
        dislike_rate = (dislikes / total) * 100 if total > 0 else 0
        # Eşik Şartı: En az 20 oy VE %70+ olumsuzluk
        has_alarm = (total >= 20) and (dislike_rate >= 70.0)
        alarm_dict[filename] = {
            "total": total,
            "dislikes": dislikes,
            "rate": round(dislike_rate, 1),
            "alarm": has_alarm
        }
    return alarm_dict
def reset_pdf_feedback(filename):
    """Belirli bir PDF dosyası için toplanan tüm oyları ve geri bildirimleri sıfırlar."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM feedbacks WHERE filename = ?", (filename,))
        conn.commit()
    finally:
        conn.close()
if __name__ == "__main__":
    init_db()
    init_feedback_db()
    print("Veritabanı ve tablolar başarıyla oluşturuldu/güncellendi.")