import os
import pdfplumber

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
pdf_yolu = os.path.join(BASE_DIR, "BELGELER", "cimtas_ik_rehberi.pdf")

if os.path.exists(pdf_yolu):
    with pdfplumber.open(pdf_yolu) as pdf:
        print(f"✅ PDF Açıldı! Toplam Sayfa Sayısı: {len(pdf.pages)}\n")
        
        for i, page in enumerate(pdf.pages):
            metin = page.extract_text()
            print(f"=== SAYFA {i+1} (TAM METİN) ===")
            print(metin)  # Sınırlandırma kaldırıldı, tamamını basar
            print("=" * 50)