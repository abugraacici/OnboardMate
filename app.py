import os
import streamlit as st
import pdfplumber
import ollama
from database import (
    init_db, 
    verify_user, 
    save_chat_message, 
    create_conversation,
    get_user_conversations,
    get_session_chat_history,
    delete_conversation,
    create_request,
    get_user_requests,
    update_request_status,
    get_all_pending_requests
)

# 1. SAYFA YAPILANDIRMASI
st.set_page_config(
    page_title="OnboardMate - Çimtaş İK Asistanı",
    page_icon="🤖",
    layout="wide"
)

# Veritabanını başlat
init_db()

# Oturum durumları (Session State)
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "user_info" not in st.session_state:
    st.session_state.user_info = None
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []

# --- PDF DOKÜMANINI PARÇALAYARAK OKUMA ---
@st.cache_data
def load_and_split_pdf(pdf_path: str):
    chunks = []
    if os.path.exists(pdf_path):
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 15]
                    chunks.extend(paragraphs)
    return chunks

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_FILE_PATH = os.path.join(BASE_DIR, "BELGELER", "cimtas_ik_rehberi.pdf")
pdf_chunks = load_and_split_pdf(PDF_FILE_PATH)

def get_relevant_context(query: str, chunks: list, top_k: int = 5) -> str:
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

def generate_chat_title(first_prompt: str) -> str:
    """Yapay zekaya ilk soruyu analiz ettirip 3-4 kelimelik başlık ürettirir."""
    try:
        res = ollama.chat(
            model='qwen2.5:7b',
            messages=[{
                'role': 'user', 
                'content': f"Şu kullanıcı sorusunu 3-4 kelimelik, kısa, net ve anlaşılır bir sohbet başlığı yap. Sadece başlığı yaz, başka hiç açıklama yazma: '{first_prompt}'"
            }]
        )
        title = res['message']['content'].strip().replace('"', '').replace("'", "")
        return title[:35]  # Çok uzun başlıkları sınırla
    except:
        return first_prompt[:25] + "..."

# --- GİRİŞ EKRANI ---
if not st.session_state.logged_in:
    st.title("🤖 OnboardMate - Giriş Yap")
    
    with st.form("login_form"):
        username = st.text_input("Kullanıcı Adı")
        password = st.text_input("Şifre", type="password")
        submit_button = st.form_submit_button("Giriş Yap")
        
        if submit_button:
            user = verify_user(username, password)
            if user:
                st.session_state.logged_in = True
                st.session_state.username = username
                st.session_state.user_info = {"full_name": user[0], "role": user[1]}
                st.session_state.current_session_id = None
                st.session_state.messages = []
                st.rerun()
            else:
                st.error("Kullanıcı adı veya şifre hatalı!")
    st.stop()

# --- YAN PANEL (SIDEBAR) ---
st.sidebar.title(f"👤 {st.session_state.user_info['full_name']}")
st.sidebar.write(f"**Erişim Rolü:** `{st.session_state.user_info['role']}`")

# ➕ YENİ SOHBET BUTONU
if st.sidebar.button("➕ Yeni Sohbet Başlat", use_container_width=True):
    st.session_state.current_session_id = None
    st.session_state.messages = []
    st.rerun()

# 💬 GEÇMİŞ SOHBET BAŞLIKLARI PANELİ
st.sidebar.markdown("---")
st.sidebar.subheader("💬 Sohbet Geçmişi")
user_convs = get_user_conversations(st.session_state.username)

if user_convs:
    for conv_id, title, c_time in user_convs:
        col_title, col_del = st.sidebar.columns([5, 1])
        
        # Seçili olan sohbeti vurgula
        is_active = (conv_id == st.session_state.current_session_id)
        btn_label = f"📌 {title}" if is_active else f"📝 {title}"
        
        with col_title:
            if st.button(btn_label, key=f"conv_{conv_id}", use_container_width=True):
                st.session_state.current_session_id = conv_id
                st.session_state.messages = get_session_chat_history(conv_id)
                st.rerun()
                
        with col_del:
            if st.button("🗑️", key=f"del_{conv_id}"):
                delete_conversation(conv_id)
                if st.session_state.current_session_id == conv_id:
                    st.session_state.current_session_id = None
                    st.session_state.messages = []
                st.rerun()
else:
    st.sidebar.caption("Henüz sohbet geçmişiniz yok.")

# İZİN VE TALEP OLUŞTURMA SEKMESİ
st.sidebar.markdown("---")
with st.sidebar.expander("📝 İzin / Talep Oluştur"):
    with st.form("request_form"):
        req_type = st.selectbox("Talep Türü", ["Yıllık İzin", "Mazeret İzni", "Hastalık Raporu / İzin", "Diğer"])
        s_date = st.date_input("Başlangıç Tarihi")
        e_date = st.date_input("Bitiş Tarihi")
        desc = st.text_area("Açıklama / Not", placeholder="Talep detayını yazınız...")
        submit_req = st.form_submit_button("Talebi Gönder")
        
        if submit_req:
            create_request(st.session_state.username, req_type, s_date, e_date, desc)
            st.sidebar.success("✅ Talebiniz İK birimine iletildi!")

# TALEPLERİMİ GÖSTER SEKMESİ
with st.sidebar.expander("📋 Geçmiş Taleplerim"):
    user_reqs = get_user_requests(st.session_state.username)
    if user_reqs:
        for r in user_reqs:
            st.write(f"**{r[0]}** ({r[1]} / {r[2]})")
            st.caption(f"Durum: `{r[3]}` | Tarih: {r[4][:10]}")
            st.divider()
    else:
        st.write("Henüz oluşturulmuş bir talebiniz yok.")

# -------------------------------------------------------------
# İK YÖNETİM PANELİ (Sadece IK_ADMIN ve Admin Rolleri Görebilir)
# -------------------------------------------------------------
user_role = str(st.session_state.user_info.get("role", "")).upper()
if "IK_ADMIN" in user_role or "ADMIN" in user_role:
    st.sidebar.markdown("---")
    with st.sidebar.expander("👑 İK Yönetici Paneli"):
        pending_requests = get_all_pending_requests()
        if pending_requests:
            for req in pending_requests:
                req_id, full_name, req_type, s_date, e_date, desc, c_time = req
                st.write(f"👤 **{full_name}**")
                st.caption(f"**Tür:** {req_type} | **Tarih:** {s_date} / {e_date}")
                if desc:
                    st.caption(f"**Not:** {desc}")
                
                col_approve, col_reject = st.columns(2)
                with col_approve:
                    if st.button("✅ Onayla", key=f"app_{req_id}"):
                        update_request_status(req_id, "Onaylandı")
                        st.sidebar.success("Onaylandı!")
                        st.rerun()
                with col_reject:
                    if st.button("❌ Reddet", key=f"rej_{req_id}"):
                        update_request_status(req_id, "Reddedildi")
                        st.sidebar.error("Reddedildi!")
                        st.rerun()
                st.divider()
        else:
            st.write("Bekleyen izin talebi yok.")

st.sidebar.markdown("---")
if st.sidebar.button("🚪 Çıkış Yap", use_container_width=True):
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.user_info = None
    st.session_state.current_session_id = None
    st.session_state.messages = []
    st.rerun()

# --- ANA EKRAN ---
st.title("🤖 OnboardMate - Çimtaş İK Asistanı")

# Geçmiş mesajları ekrana çiz
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# KULLANICI MESAJ GİRDİSİ
if prompt := st.chat_input("İnsan kaynakları hakkında bir soru sorun..."):
    
    # Oturum yoksa OTOMATİK BAŞLIK ÜRET ve Veritabanında Oturum Aç
    if st.session_state.current_session_id is None:
        with st.spinner("Sohbet oturumu oluşturuluyor..."):
            auto_title = generate_chat_title(prompt)
            st.session_state.current_session_id = create_conversation(st.session_state.username, auto_title)

    # 1. Kullanıcı mesajını kaydet ve ekrana bas
    st.session_state.messages.append({"role": "user", "content": prompt})
    save_chat_message(st.session_state.current_session_id, "user", prompt)
    
    with st.chat_message("user"):
        st.markdown(prompt)

    relevant_pdf_info = get_relevant_context(prompt, pdf_chunks)

    system_instruction = (
        f"Sen Çimtaş şirketinin kibar, samimi ve son derece yardımsever İK Asistanı OnboardMate'sin.\n"
        f"Konuştuğun Kullanıcı: {st.session_state.user_info['full_name']} (Unvan: {st.session_state.user_info['role']}).\n\n"
        f"GÖREV VE TALİMATLAR:\n"
        f"1. SELAMLAŞMA VE GENEL SORULAR: Kullanıcı selam verdiğinde veya 'ne yapabilirsin?', 'hangi konularda yardımcı olursun?' gibi genel sorular sorduğunda, kibarca selamla. Çimtaş İK Rehberi kapsamında çalışma saatleri, izin hakları, servisler, sağlık sigortası, yan haklar, masraf ve İSG gibi konularda bilgi verebileceğini belirt.\n"
        f"2. DOKÜMANA SADAKAT: Şirket kural ve prosedürleri sorulduğunda SADECE 'ŞİRKET DOKÜMAN BİLGİSİ' alanındaki verilere dayanarak cevap ver. Asla uydurma kural veya süreç ekleme.\n"
        f"3. DOKÜMAN DIŞI SORULAR: Sordukları spesifik konu dokümanda yoksa 'Bu konu hakkında şirket rehberimizde detaylı bir bilgi yer almamaktadır. Doğru bilgi için İK Yetkiliniz ile iletişime geçmenizi öneririm.' yanıtını ver.\n"
        f"4. SADECE TÜRKÇE: Yanıtlarında asla İngilizce, Çince veya yabancı karakterler kullanma. Sadece düzgün Türkçe yanıt ver."
    )

    ollama_messages = [{'role': 'system', 'content': system_instruction}]
    
    # Sohbet geçmişinden son 4 mesajı al
    for msg in st.session_state.messages[-5:-1]:
        ollama_messages.append({'role': msg['role'], 'content': msg['content']})

    # Kullanıcı girdisi
    user_content = f"--- ŞİRKET DOKÜMAN BİLGİSİ ---\n{relevant_pdf_info}\n-----------------------------\n\nKullanıcı Sorduğu Soru: {prompt}"
    
    ollama_messages.append({'role': 'user', 'content': user_content})

    with st.chat_message("assistant"):
        with st.status("Yerel İK modeli yanıt hazırlıyor...", expanded=False) as status:
            try:
                response = ollama.chat(
                    model='qwen2.5:7b',
                    messages=ollama_messages,
                    options={'temperature': 0.1}
                )
                
                response_text = response['message']['content']
                status.update(label="Yanıt hazırlandı!", state="complete", expanded=False)

            except Exception as e:
                status.update(label="Ollama bağlantı hatası!", state="error", expanded=False)
                response_text = "⚠️ Ollama uygulamasının açık olduğundan emin olun."

        st.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})
        save_chat_message(st.session_state.current_session_id, "assistant", response_text)
        st.rerun()