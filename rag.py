import hashlib
import os
import re
import chromadb
import ollama
import pdfplumber
import streamlit as st

from config import CHROMA_COLLECTION_NAME, CHROMA_DIR, EMBEDDING_MODEL


@st.cache_resource
def _get_chroma_collection():
    """ChromaDB'ye kalıcı (disk tabanlı) bağlantıyı önbelleğe alır."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(name=CHROMA_COLLECTION_NAME)


def _embed(text: str):
    response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=text)
    return response["embedding"]


def _split_pdf_into_chunks(pdf_path: str):
    """PDF'i dümdüz satır satır bölmek yerine; madde işaretleri (•, -, *), 
    numaralandırmalar (1., 2.) veya 'Soru:' / 'Cevap:' başlıklarına göre
    anlamsal bütünlük oluşturan gruplar halinde parçalar.
    """
    chunks = []
    current_chunk = []

    # Yeni bir madde veya başlık başlatan regex deseni
    bullet_pattern = re.compile(r"^([•\-\*]|(\d+[\.\)]))\s*")

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            lines = text.split("\n")
            for line in lines:
                clean_line = line.strip()
                if not clean_line:
                    continue

                # Satırın yeni bir madde, soru veya cevap başlığı olup olmadığını kontrol et
                is_bullet = bool(bullet_pattern.match(clean_line))
                is_qa = clean_line.lower().startswith(("soru:", "cevap:", "madde", "başlık"))

                # Yeni bir madde/başlık geldiyse elimizdeki birikmiş parçayı paketle
                if (is_bullet or is_qa) and current_chunk:
                    chunk_text = " ".join(current_chunk)
                    if len(chunk_text) > 15:
                        chunks.append(chunk_text)
                    current_chunk = []

                current_chunk.append(clean_line)

    # En son kalan bloğu ekle
    if current_chunk:
        chunk_text = " ".join(current_chunk)
        if len(chunk_text) > 15:
            chunks.append(chunk_text)

    return chunks


def _file_version(pdf_path: str) -> str:
    """Dosyanın değişip değişmediğini anlamak için mtime+boyuttan kısa bir hash üretir."""
    stat = os.stat(pdf_path)
    raw = f"{stat.st_mtime_ns}-{stat.st_size}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def sync_pdf_index(belgeler_dir: str) -> None:
    """BELGELER klasörünü ChromaDB koleksiyonuyla senkronize eder."""
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

    indexed_versions = {}
    ids_by_file = {}
    for _id, meta in zip(existing["ids"], existing["metadatas"]):
        fname = meta.get("filename")
        ver = meta.get("file_version")
        indexed_versions.setdefault(fname, set()).add(ver)
        ids_by_file.setdefault(fname, []).append(_id)

    # Klasörden silinmiş dosyaların chunk'larını kaldır
    for fname, ids in ids_by_file.items():
        if fname not in current_files and ids:
            collection.delete(ids=ids)

    # Yeni / değişmiş dosyaları işle
    for fname, version in current_files.items():
        if version in indexed_versions.get(fname, set()):
            continue  # değişmemiş, atla

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
    """Soruya en alakalı doküman parçalarını ChromaDB'den getirir."""
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