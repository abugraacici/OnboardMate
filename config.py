import os
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

# --- YOLLAR ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BELGELER_DIR = os.path.join(BASE_DIR, "BELGELER")

# --- MODEL ---
OLLAMA_MODEL = "qwen2.5:7b"

# --- İK KAYIT ANAHTARI ---
IK_SECRET_KEY = os.getenv("IK_SECRET_KEY")

# --- FABRİKA KONUMU (Bursa Serbest Bölgesi) ---
# İleride PDF ile bina x/y koordinatları ve iç yönlendirme eklenecek.
FACTORY_ADDRESS = "Ata Sb.Mah. Müge Cad, Bursa Serbest Bölgesi No:17, 16600 Gemlik/Bursa"
FACTORY_MAPS_EMBED_URL = f"https://www.google.com/maps?q={urllib.parse.quote(FACTORY_ADDRESS)}&output=embed"
FACTORY_MAPS_LINK_URL = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(FACTORY_ADDRESS)}"
