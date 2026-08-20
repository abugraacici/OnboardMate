import os
import urllib.parse
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

# --- YOLLAR ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BELGELER_DIR = os.path.join(BASE_DIR, "BELGELER")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")

# Gerekli çalışma klasörlerini otomatik oluştur
os.makedirs(BELGELER_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)

# --- MODEL VE OLLAMA ---
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# --- RAG / VEKTÖR VERİTABANI ---
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "cimtas_ik_belgeleri")
CHUNKER_VERSION = "v3"

# Cosine mesafesinde (0.0: tam eşleşme, 2.0: zıt)
# nomic-embed-text için 0.80 - 0.90 arası alakasız soruları filtrelemede en yüksek doğruluğu verir.
RAG_MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", "0.85"))

# --- İK KAYIT ANAHTARI (Admin Yetkilendirme) ---
IK_SECRET_KEY = os.getenv("IK_SECRET_KEY", "cimtas_ik_2024")

# --- FABRİKA KONUMU (Bursa Serbest Bölgesi) ---
FACTORY_ADDRESS = os.getenv(
    "FACTORY_ADDRESS",
    "Ata Sb.Mah. Müge Cad, Bursa Serbest Bölgesi No:17, 16600 Gemlik/Bursa"
)
FACTORY_MAPS_EMBED_URL = f"https://www.google.com/maps?q={urllib.parse.quote(FACTORY_ADDRESS)}&output=embed"
FACTORY_MAPS_LINK_URL = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(FACTORY_ADDRESS)}"