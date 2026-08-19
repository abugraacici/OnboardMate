import json
import os
import sqlite3
import hashlib
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

# .env içindeki metni JSON olarak Python listesine dönüştürür
test_users_raw = os.getenv("TEST_USERS")
test_users = json.loads(test_users_raw) if test_users_raw else []

# Veritabanı Yolu
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_data.db")


# --- ŞİFRE HASH'LEME (stdlib hashlib.pbkdf2_hmac, ekstra paket gerekmez) ---
def _hash_password(password: str, salt: bytes = None) -> str:
    """'pbkdf2$<salt_hex>$<hash_hex>' formatında bir string döndürür."""
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def _is_hashed(stored_password: str) -> bool:
    return stored_password.startswith("pbkdf2$")


def _verify_password(password: str, stored_password: str) -> bool:
    if _is_hashed(stored_password):
        try:
            _, salt_hex, hash_hex = stored_password.split("$")
        except ValueError:
            return False
        salt = bytes.fromhex(salt_hex)
        candidate = _hash_password(password, salt)
        return candidate == stored_password
    # Eski düz metin şifre (henüz migrate edilmemiş)
    return password == stored_password


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    # Aynı anda birden fazla kullanıcı yazınca "database is locked" hatasını
    # azaltmak için WAL modu + 15 sn bekleme süresi.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    # FOREIGN KEY kısıtlamalarının fiilen uygulanması için (SQLite'ta
    # varsayılan kapalıdır).
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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

    # 5. Hazır Sorular (Preset Questions / Çipler) Tablosu
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

    # --- ŞEMA ONARIMI (Migration) ---
    # Eğer tablo daha önce farklı/eksik bir şema ile oluşturulmuşsa
    # (örn. eski bir denemeden kalma), CREATE TABLE IF NOT EXISTS bunu
    # atlar ve eski şema kalır. Burada eksik kolonları tespit edip
    # tabloyu güvenli şekilde yeniden kuruyoruz.
    def _ensure_columns(table_name, expected_columns_sql, create_sql):
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_cols = {row[1] for row in cursor.fetchall()}
        expected_cols = set(expected_columns_sql)
        if not expected_cols.issubset(existing_cols):
            # Şema uyuşmuyor: eski tabloyu yedekleyip doğru şemayla yeniden oluştur
            cursor.execute(f"ALTER TABLE {table_name} RENAME TO {table_name}_old_backup")
            cursor.execute(create_sql)
            conn.commit()

    _ensure_columns(
        "preset_questions",
        {"id", "question_text", "created_by", "click_count", "created_at"},
        """
        CREATE TABLE preset_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_text TEXT NOT NULL,
            created_by TEXT,
            click_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )

    _ensure_columns(
        "company_locations",
        {"id", "building_name", "description", "dress_code", "created_at"},
        """
        CREATE TABLE company_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            building_name TEXT NOT NULL,
            description TEXT NOT NULL,
            dress_code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )

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
                    (u, _hash_password(p) if p else p, fn, r),
                )

    conn.commit()
    conn.close()


def verify_user(username, password):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT full_name, role, password FROM users WHERE username = ?",
        (username,),
    )
    row = cursor.fetchone()

    if row is None:
        conn.close()
        return None

    full_name, role, stored_password = row
    if not _verify_password(password, stored_password):
        conn.close()
        return None

    # Eski düz metin şifreyse, başarılı girişte sessizce hash'e migrate et
    if not _is_hashed(stored_password):
        cursor.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            (_hash_password(password), username),
        )
        conn.commit()

    conn.close()
    return (full_name, role)


def add_user(username, password, full_name, role="Çalışan"):
    """Yeni kullanıcı kaydı oluşturur. Kullanıcı adı zaten varsa False döner."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        return False

    cursor.execute(
        "INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
        (username, _hash_password(password), full_name, role),
    )
    conn.commit()
    conn.close()
    return True


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


# --- HAZIR SORU (PRESET QUESTION) FONKSİYONLARI ---
def add_preset_question(question_text, created_by=None):
    """Yeni bir hazır soru / çip ekler."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO preset_questions (question_text, created_by) VALUES (?, ?)",
        (question_text, created_by),
    )
    conn.commit()
    conn.close()


def get_preset_questions(limit=8):
    """En çok tıklananlar önce olacak şekilde hazır soruları getirir.
    Dönüş: [(id, question_text, click_count), ...]
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, question_text, click_count FROM preset_questions "
        "ORDER BY click_count DESC, id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def delete_preset_question(question_id):
    """Bir hazır soruyu siler."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM preset_questions WHERE id = ?", (question_id,))
    conn.commit()
    conn.close()


def increment_question_click(question_id):
    """Bir hazır sorunun tıklanma sayacını 1 artırır."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE preset_questions SET click_count = click_count + 1 WHERE id = ?",
        (question_id,),
    )
    conn.commit()
    conn.close()


# --- ŞİRKET BİNA / LOKASYON FONKSİYONLARI ---
def add_company_location(building_name, description, dress_code=None):
    """Yeni bir bina/lokasyon kaydı ekler."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO company_locations (building_name, description, dress_code) VALUES (?, ?, ?)",
        (building_name, description, dress_code),
    )
    conn.commit()
    conn.close()


def get_all_company_locations():
    """Tüm bina/lokasyon kayıtlarını getirir.
    Dönüş: [(building_name, description, dress_code), ...]
    (app.py bu sıralamayla loc[0], loc[1], loc[2] olarak kullanıyor)
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT building_name, description, dress_code FROM company_locations ORDER BY id ASC"
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


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
