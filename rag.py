import os
import re
import hashlib
import pdfplumber
import ollama
import chromadb
import streamlit as st

from config import (
    CHROMA_DIR,
    CHROMA_COLLECTION_NAME,
    EMBEDDING_MODEL,
    CHUNKER_VERSION,
    RAG_MAX_DISTANCE,
    OLLAMA_HOST,
)

# Maksimum parça uzunluğu ve örtüşme (uzun düz metinler için koruma)
MAX_CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150


@st.cache_resource
def _get_chroma_collection():
    """ChromaDB'ye kalıcı bağlantı açar ve koleksiyonu Cosine metriğiyle yapılandırır."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    # hnsw:space = cosine ile benzerlik metriğini garantiye alıyoruz
    return client.get_or_create_collection(
        name=CHROMA_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )


def _embed(text: str):
    """Metni Ollama embedding modeliyle vektöre dönüştürür."""
    if not text or not text.strip():
        return None
    try:
        response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=text.strip())
        return response.get("embedding")
    except Exception as e:
        print(f"Ollama embedding hatası: {e}")
        return None


_HEADING_RE = re.compile(r"^\d+[\.\)]\s+\S")  # "8. İşe başladıktan sonra..."


def _is_new_unit_start(line: str) -> bool:
    """Satırın yeni bir anlamsal birim başlangıcı olup olmadığını kontrol eder."""
    if line.startswith(("•", "-", "*", "–", "●")):
        return True
    if _HEADING_RE.match(line):
        return True
    if re.match(r"^(Soru|Cevap|Alternatif sorular?|Konu|Anahtar kelimeler)\s*:", line, re.IGNORECASE):
        return True
    return False


def _group_lines_into_units(text: str) -> list:
    """PDF satır kaydırmalarını anlamsal birimler halinde gruplar."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    units = []
    current = ""
    for line in lines:
        if _is_new_unit_start(line) and current:
            units.append(current.strip())
            current = line
        else:
            current = f"{current} {line}".strip() if current else line
    if current:
        units.append(current.strip())
    return units


_BULLET_PREFIX_RE = re.compile(r"^[•\-*–●]\s*")


def _strip_bullet(line: str) -> str:
    return _BULLET_PREFIX_RE.sub("", line).strip()


def _merge_soru_cevap_pairs(units: list) -> list:
    """
    Başlık (örn: 8. İşe başladıktan...), Soru, Alternatif Sorular, Cevap,
    Konu ve Anahtar Kelimeler gibi birbiriyle ilişkili TÜM içeriği TEK bir chunk'ta toplar.
    Başlığı asla cevaptan ayırmaz!
    """
    merged = []
    buffer = []

    def flush():
        nonlocal buffer
        if buffer:
            merged.append("\n".join(buffer))
            buffer = []

    for unit in units:
        stripped = _strip_bullet(unit)
        is_heading = bool(_HEADING_RE.match(stripped))
        is_soru_start = bool(re.match(r"^Soru\s*:", stripped, re.IGNORECASE))

        # Yeni bir numaralı ana başlık geldiyse önceki bloğu kaydet ve yeni blok başlat
        if is_heading:
            flush()
            buffer = [unit]
            continue

        # Numaralı başlık olmayan ama "Soru:" ile başlayan yeni soru bloğu durumu
        if is_soru_start:
            buffer_lower = "\n".join(buffer).lower()
            if "cevap:" in buffer_lower or "soru:" in buffer_lower:
                flush()
                buffer = [unit]
                continue

        buffer.append(unit)

    flush()
    return merged


def _split_long_chunk(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list:
    """Aşırı uzun blok metinleri anlamsal cümle sınırlarından bölerek makul parçalara ayırır."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end >= len(text):
            chunks.append(text[start:].strip())
            break

        # Cümle sonu ayırıcıları ara
        split_pos = -1
        for sep in [". ", "?\n", "!\n", "\n", ". ", "; "]:
            pos = text.rfind(sep, start + max_chars // 2, end)
            if pos != -1:
                split_pos = pos + len(sep)
                break

        if split_pos == -1:
            split_pos = end

        chunk = text[start:split_pos].strip()
        if chunk:
            chunks.append(chunk)
        start = max(start + 1, split_pos - overlap)
    return chunks


def _split_pdf_into_chunks(pdf_path: str) -> list:
    all_units = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            all_units.extend(_group_lines_into_units(text))

    merged = _merge_soru_cevap_pairs(all_units)

    # Parça boyutu optimizasyonu
    final_chunks = []
    for chunk in merged:
        if len(chunk) > MAX_CHUNK_CHARS:
            final_chunks.extend(_split_long_chunk(chunk))
        elif len(chunk) > 10:
            final_chunks.append(chunk)

    return final_chunks


def delete_pdf_from_index(filename: str) -> None:
    """Belirtilen PDF dosyasına ait parçaları ChromaDB'den siler."""
    try:
        collection = _get_chroma_collection()
        results = collection.get(where={"filename": filename})
        if results and results.get("ids"):
            collection.delete(ids=results["ids"])
    except Exception as e:
        print(f"ChromaDB silme hatası: {e}")


def _file_version(pdf_path: str) -> str:
    """Dosya içeriği ve chunker versiyonundan hash üretir."""
    stat = os.stat(pdf_path)
    raw = f"{stat.st_mtime_ns}-{stat.st_size}-{CHUNKER_VERSION}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def sync_pdf_index(belgeler_dir: str) -> None:
    """BELGELER klasörünü ChromaDB koleksiyonuyla senkronize eder."""
    try:
        collection = _get_chroma_collection()
    except Exception as e:
        print(f"ChromaDB bağlantı hatası: {e}")
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
        existing = {"ids": [], "metadatas": []}

    indexed_versions = {}
    ids_by_file = {}
    if existing and "ids" in existing and existing["ids"]:
        for _id, meta in zip(existing["ids"], existing.get("metadatas", [])):
            if not meta:
                continue
            fname = meta.get("filename")
            ver = meta.get("file_version")
            if fname:
                indexed_versions.setdefault(fname, set()).add(ver)
                ids_by_file.setdefault(fname, []).append(_id)

    # Silinmiş dosyaları temizle
    for fname, ids in ids_by_file.items():
        if fname not in current_files and ids:
            try:
                collection.delete(ids=ids)
            except Exception:
                pass

    # Yeni veya değişmiş dosyaları indeksle
    for fname, version in current_files.items():
        if version in indexed_versions.get(fname, set()):
            continue  # Zaten güncel

        old_ids = ids_by_file.get(fname, [])
        if old_ids:
            try:
                collection.delete(ids=old_ids)
            except Exception:
                pass

        pdf_path = os.path.join(belgeler_dir, fname)
        try:
            chunks = _split_pdf_into_chunks(pdf_path)
        except Exception as e:
            print(f"PDF okuma hatası ({fname}): {e}")
            continue

        if not chunks:
            continue

        embeddings = []
        valid_chunks = []
        for chunk in chunks:
            emb = _embed(chunk)
            if emb is not None:
                embeddings.append(emb)
                valid_chunks.append(chunk)

        if not valid_chunks:
            continue

        ids = [f"{fname}::{i}::{version}" for i in range(len(valid_chunks))]
        metadatas = [{"filename": fname, "file_version": version} for _ in valid_chunks]

        try:
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=valid_chunks,
                metadatas=metadatas,
            )
        except Exception as e:
            print(f"Koleksiyona ekleme hatası ({fname}): {e}")


NOT_FOUND_MESSAGES = frozenset({
    "Şirket rehber dokümanı sisteminde bir bağlantı sorunu oluştu.",
    "Şirket rehber dokümanı yüklü değil.",
    "Soruyla doğrudan ilgili bilgi PDF rehberinde bulunamadı.",
})


def get_relevant_context(query: str, top_k: int = 5) -> str:
    """Soruya en alakalı doküman parçalarını getirir."""
    try:
        collection = _get_chroma_collection()
        count = collection.count()
    except Exception:
        return "Şirket rehber dokümanı sisteminde bir bağlantı sorunu oluştu."

    if count == 0:
        return "Şirket rehber dokümanı yüklü değil."

    try:
        query_embedding = _embed(query)
        if query_embedding is None:
            return "Şirket rehber dokümanı sisteminde bir bağlantı sorunu oluştu."

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, count),
            include=["documents", "distances"],
        )
    except Exception:
        return "Şirket rehber dokümanı sisteminde bir bağlantı sorunu oluştu."

    documents = results.get("documents", [[]])[0]
    distances = results.get("distances", [[]])[0]

    if not documents:
        return "Soruyla doğrudan ilgili bilgi PDF rehberinde bulunamadı."

    # Kosinüs mesafesinde RAG_MAX_DISTANCE eşiğini geçenleri filtrele
    relevant = [doc for doc, dist in zip(documents, distances) if dist <= RAG_MAX_DISTANCE]

    if not relevant:
        return "Soruyla doğrudan ilgili bilgi PDF rehberinde bulunamadı."

    return "\n\n".join(relevant)