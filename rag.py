import os
import pdfplumber
import streamlit as st


@st.cache_data
def load_and_split_pdf_folder(belgeler_dir: str):
    """BELGELER klasöründeki TÜM PDF dosyalarını okuyup paragraflara ayırır.

    Not: Önceki sürüm sadece tek bir sabit dosya adını okuyordu; İK panelinden
    yüklenen diğer PDF'ler hiç işlenmiyordu. Artık klasördeki tüm .pdf
    dosyaları taranıyor.
    """
    chunks = []
    if not os.path.exists(belgeler_dir):
        return chunks

    for filename in sorted(os.listdir(belgeler_dir)):
        if not filename.lower().endswith(".pdf"):
            continue
        pdf_path = os.path.join(belgeler_dir, filename)
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        paragraphs = [
                            p.strip() for p in text.split("\n") if len(p.strip()) > 15
                        ]
                        chunks.extend(paragraphs)
        except Exception:
            # Bozuk/okunamayan bir PDF tüm sistemi durdurmasın
            continue

    return chunks


def get_relevant_context(query: str, chunks: list, top_k: int = 5) -> str:
    """Basit kelime-eşleştirmeli bağlam getirme.

    GEÇİCİ: Bu, gerçek embedding tabanlı RAG sistemi kurulana kadar
    kullanılan yer tutucu bir yöntemdir. Sıradaki adımda burası
    Ollama embedding modeli + vektör veritabanı ile değiştirilecek.
    """
    if not chunks:
        return "Şirket rehber dokümanı yüklü değil."

    query_words = set(query.lower().split())
    scored_chunks = []

    for chunk in chunks:
        chunk_lower = chunk.lower()
        score = sum(1 for word in query_words if len(word) > 2 and word in chunk_lower)
        if score > 0:
            scored_chunks.append((score, chunk))

    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    top_chunks = [item[1] for item in scored_chunks[:top_k]]

    if not top_chunks:
        return "Soruyla doğrudan ilgili bilgi PDF rehberinde bulunamadı."

    return "\n\n".join(top_chunks)
