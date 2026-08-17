import os
from database import (
    get_all_pending_requests,
    get_user_conversations,
    init_db,
    verify_user,
)

# 1. Veritabanını ve tabloları başlat
print("1. Veritabanı başlatılıyor...")
init_db()

# 2. .db dosyasının diskte oluştuğunu kontrol et
db_exists = os.path.exists("app_data.db")
print(f"2. 'app_data.db' dosyası mevcut mu? -> {db_exists}")

# 3. .env'den çekilen bir kullanıcı ile giriş test et
# Note: '.env' dosyanızdaki geçerli bir kullanıcı adı ve şifreyi yazın
test_user = "admin"  # Kendi kullanıcı adınızla değiştirin
test_pass = "1234"  # Kendi şifrenizle değiştirin

user = verify_user(test_user, test_pass)
if user:
    print(
        f"3. Kullanıcı Doğrulandı! -> Adı: {user[0]}, Rolü: {user[1]}"
    )
else:
    print(
        "3. Kullanıcı doğrulanamadı. .env verilerini ve kullanıcı adı/şifreyi kontrol edin."
    )

# 4. Fonksiyonların hata vermeden çalıştığını doğrula
conversations = get_user_conversations(test_user)
print(f"4. Sohbet geçmişi sorgusu başarılı (Kayıt sayısı: {len(conversations)})")

pending_requests = get_all_pending_requests()
print(f"5. Bekleyen talepler sorgusu başarılı (Kayıt sayısı: {len(pending_requests)})")