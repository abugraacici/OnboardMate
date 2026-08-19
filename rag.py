import os
import hashlib
import pdfplumber
import ollama
import chromadb
import streamlit as st

from config import CHROMA_DIR, CHROMA_COLLECTION_NAME, EMBEDDING_MODEL


@st.cache_resource
def _get_chroma_collection():
    """ChromaDB'ye kalıcı (disk tabanlı) bağlantıyı önbelleğe alır.
    Streamlit her rerun'da yeniden bağlantı açmasın diye cache_resource kullanılıyor
    (cache_data DEĞİL, çünkü bu bir veri değil, canlı bir bağlantı/istemci nesnesi).
    """
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(name=CHROMA_COLLECTION_NAME)


def _embed(text: str):
    response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=text)
    return response["embedding"]


def _split_pdf_into_chunks(pdf_path: str):
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 15]
                chunks.extend(paragraphs)
    return chunks


def _file_version(pdf_path: str) -> str:
    """Dosyanın değişip değişmediğini anlamak için mtime+boyuttan kısa bir hash üretir."""
    stat = os.stat(pdf_path)
    raw = f"{stat.st_mtime_ns}-{stat.st_size}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def sync_pdf_index(belgeler_dir: str) -> None:
    """BELGELER klasörünü ChromaDB koleksiyonuyla senkronize eder.

    - Yeni eklenen PDF'leri parçalayıp embed eder ve koleksiyona ekler.
    - İçeriği değişen (mtime/boyut değişen) PDF'lerin eski chunk'larını silip
      yeniden embed eder.
    - Klasörden silinen PDF'lerin chunk'larını koleksiyondan kaldırır.
    - Değişmeyen dosyaları YENİDEN EMBED ETMEZ (versiyon hash'i eşleşiyorsa atlar) -
      böylece her Streamlit rerun'ında gereksiz Ollama çağrısı yapılmaz.

    Ollama'ya ulaşılamazsa (embedding hatası) sessizce durur; chat akışını bozmaz,
    sadece o PDF bir sonraki başarılı senkronizasyonda tekrar denenir.
    """
    try:
        collection = _get_chroma_collection()
    except Exception:
        return

    if not os.path.exists(belgeler_dir):
        current_files = {}
    else:
        current_files = {
            f: _file_version(os.path.join(belgeler_dir, f))
            for f in os.listdir(belgeler_dir)
            if f.lower().endswith(".pdf")
        }

    try:
        existing = collection.get(include=["metadatas"])
    except Exception:
        return

    indexed_versions = {}   # filename -> {version, ...}
    ids_by_file = {}        # filename -> [chunk_id, ...]
    for _id, meta in zip(existing["ids"], existing["metadatas"]):
        fname = meta.get("filename")
        ver = meta.get("file_version")
        indexed_versions.setdefault(fname, set()).add(ver)
        ids_by_file.setdefault(fname, []).append(_id)

    # Klasörden silinmiş dosyaların chunk'larını koleksiyondan kaldır
    for fname, ids in ids_by_file.items():
        if fname not in current_files and ids:
            collection.delete(ids=ids)

    # Yeni / değişmiş dosyaları işle
    for fname, version in current_files.items():
        if version in indexed_versions.get(fname, set()):
            continue  # değişmemiş, embed etmeye gerek yok

        old_ids = ids_by_file.get(fname, [])
        if old_ids:
            collection.delete(ids=old_ids)

        pdf_path = os.path.join(belgeler_dir, fname)
        try:
            chunks = _split_pdf_into_chunks(pdf_path)
        except Exception:
            continue

        if not chunks:
            continue

        try:
            embeddings = [_embed(chunk) for chunk in chunks]
        except Exception:
            # Ollama'ya ulaşılamadı; bu dosyayı bir sonraki senkronizasyona bırak
            continue

        ids = [f"{fname}::{i}::{version}" for i in range(len(chunks))]
        metadatas = [{"filename": fname, "file_version": version} for _ in chunks]

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )


def get_relevant_context(query: str, top_k: int = 5) -> str:
    """Soruya en alakalı doküman parçalarını ChromaDB'den (embedding benzerliğiyle) getirir."""
    try:
        collection = _get_chroma_collection()
        count = collection.count()
    except Exception:
        return "Şirket rehber dokümanı sisteminde bir bağlantı sorunu oluştu."

    if count == 0:
        return "Şirket rehber dokümanı yüklü değil."

    try:
        query_embedding = _embed(query)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, count),
        )
    except Exception:
        return "Şirket rehber dokümanı sisteminde bir bağlantı sorunu oluştu."

    documents = results.get("documents", [[]])[0]
    if not documents:
        return "Soruyla doğrudan ilgili bilgi PDF rehberinde bulunamadı."

    return "\n\n".join(documents)
