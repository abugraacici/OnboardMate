import os
import urllib.parse
import streamlit as st
import streamlit.components.v1 as components
import pdfplumber
import ollama
from database import (
    init_db, 
    verify_user, 
    add_user,
    save_chat_message, 
    create_conversation,
    get_user_conversations,
    get_session_chat_history,
    delete_conversation,
    add_preset_question,
    get_preset_questions,
    delete_preset_question,
    increment_question_click,
)

# Fabrika Konumu (Bursa Serbest Bölgesi) - sabit adres.
# İleride PDF ile bina x/y koordinatları ve iç yönlendirme eklenecek.
FACTORY_ADDRESS = "Ata Sb.Mah. Müge Cad, Bursa Serbest Bölgesi No:17, 16600 Gemlik/Bursa"
FACTORY_MAPS_EMBED_URL = f"https://www.google.com/maps?q={urllib.parse.quote(FACTORY_ADDRESS)}&output=embed"
FACTORY_MAPS_LINK_URL = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(FACTORY_ADDRESS)}"

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
if "preset_prompt" not in st.session_state:
    st.session_state.preset_prompt = None
if "qs_panel_expanded" not in st.session_state:
    st.session_state.qs_panel_expanded = False
if "pdf_panel_expanded" not in st.session_state:
    st.session_state.pdf_panel_expanded = False

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
    try:
        res = ollama.chat(
            model='qwen2.5:7b',
            messages=[{
                'role': 'user', 
                'content': f"Şu kullanıcı sorusunu 3-4 kelimelik, kısa, net ve anlaşılır bir sohbet başlığı yap. Sadece başlığı yaz: '{first_prompt}'"
            }]
        )
        title = res['message']['content'].strip().replace('"', '').replace("'", "")
        return title[:35]
    except:
        return first_prompt[:25] + "..."

# --- GİRİŞ VE KAYIT EKRANI ---
if not st.session_state.logged_in:
    st.title("🏗️ OnboardMate - Çimtaş İK Asistanı")
    
    tab_login, tab_register = st.tabs(["🔑 Giriş Yap", "📝 Kayıt Ol"])
    
    with tab_login:
        login_user = st.text_input("Kullanıcı Adı", key="login_user")
        login_pass = st.text_input("Şifre", type="password", key="login_pass")
        if st.button("Giriş Yap", use_container_width=True):
            user = verify_user(login_user, login_pass)
            if user:
                st.session_state.logged_in = True
                st.session_state.username = login_user
                st.session_state.user_info = {"full_name": user[0], "role": user[1]}
                st.session_state.current_session_id = None
                st.session_state.messages = []
                st.success("Giriş başarılı!")
                st.rerun()
            else:
                st.error("Kullanıcı adı veya şifre hatalı!")

    with tab_register:
        reg_fullname = st.text_input("Ad Soyad", key="reg_name")
        reg_user = st.text_input("Kullanıcı Adı Belirleyin", key="reg_user")
        reg_pass = st.text_input("Şifre Belirleyin", type="password", key="reg_pass")
        
        reg_role = st.selectbox("Rolünüzü Seçin", ["Çalışan", "İnsan Kaynakları (Admin)"], key="reg_role")
        
        ik_key_input = ""
        if reg_role == "İnsan Kaynakları (Admin)":
            ik_key_input = st.text_input("İK Gizli Katılım Anahtarı", type="password", help="İnsan Kaynakları yetkisi için şirketinizin verdiği anahtarı girin.", key="reg_ik_key")

        if st.button("Kayıt Ol", use_container_width=True):
            if reg_fullname and reg_user and reg_pass:
                IK_SECRET_KEY = os.getenv("IK_SECRET_KEY")
                
                selected_role = "Çalışan"
                if reg_role == "İnsan Kaynakları (Admin)":
                    if ik_key_input.strip() == IK_SECRET_KEY:
                        selected_role = "İnsan Kaynakları"
                    else:
                        st.error("Girdiğiniz İK Katılım Anahtarı hatalı! Kayıt tamamlanamadı.")
                        st.stop()

                success = add_user(reg_user.strip(), reg_pass.strip(), reg_fullname.strip(), role=selected_role)
                if success:
                    st.success(f"Kayıt başarılı! [{selected_role}] rolüyle 'Giriş Yap' sekmesinden giriş yapabilirsiniz.")
                else:
                    st.warning("Bu kullanıcı adı zaten alınmış!")
            else:
                st.warning("Lütfen tüm alanları doldurun.")
    st.stop()

# --- YAN PANEL (SIDEBAR) ---
st.sidebar.title(f"👤 {st.session_state.user_info['full_name']}")
st.sidebar.write(f"**Erişim Rolü:** `{st.session_state.user_info['role']}`")

if st.sidebar.button("➕ Yeni Sohbet Başlat", use_container_width=True):
    st.session_state.current_session_id = None
    st.session_state.messages = []
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("💬 Sohbet Geçmişi")
user_convs = get_user_conversations(st.session_state.username)

if user_convs:
    for conv_id, title, c_time in user_convs:
        col_title, col_del = st.sidebar.columns([5, 1])
        
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

# -------------------------------------------------------------
# İK YÖNETİM PANELİ (İçerik, Hazır Soru ve Harita Yönetimi)
# -------------------------------------------------------------
user_role = st.session_state.user_info.get("role", "")

if user_role == "İnsan Kaynakları":
    with st.sidebar.expander("👑 İK İçerik Yönetici Paneli", expanded=False):
        
        # 1. Hazır Soru Ekleme
        st.subheader("💡 Hazır Soru / Çip Yönetimi")
        new_q = st.text_input("Yeni Hazır Soru", placeholder="Örn: Yemek kartı limiti ne kadar?")
        if st.button("Soru Ekle", use_container_width=True):
            if new_q.strip():
                add_preset_question(new_q.strip(), st.session_state.username)
                st.sidebar.success("Hazır soru eklendi!")
                st.rerun()

        # MEVCUT SORULARI LİSTELEME VE SİLME ALANI
        st.markdown("---")
        existing_qs = get_preset_questions(limit=50)

        with st.sidebar.expander(
            f"📋 Mevcut Hazır Sorular ({len(existing_qs) if existing_qs else 0})",
            expanded=st.session_state.qs_panel_expanded,
        ):
            if existing_qs:
                for q_id, q_text, q_clicks in existing_qs:
                    col_text, col_del = st.columns([4, 1])
                    with col_text:
                        st.caption(f"• {q_text}")
                    with col_del:
                        if st.button("🗑️", key=f"del_q_{q_id}", help="Soruyu Sil"):
                            delete_preset_question(q_id)
                            st.session_state.qs_panel_expanded = True
                            st.sidebar.success("Soru silindi!")
                            st.rerun()
            else:
                st.caption("Henüz kayıtlı hazır soru yok.")

        # 2. PDF Doküman Yükleme ve Yönetimi
        st.subheader("📄 PDF Kural Yükle & Yönet")
        uploaded_file = st.file_uploader("Şirket Rehberi / PDF", type=["pdf"])
        
        if uploaded_file is not None:
            save_path = os.path.join(BASE_DIR, "BELGELER", uploaded_file.name)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            st.cache_data.clear()
            st.sidebar.success(f"'{uploaded_file.name}' yüklendi!")
            st.rerun()

        # MEVCUT PDF'LERİ LİSTELEME VE SİLME
        docs_dir = os.path.join(BASE_DIR, "BELGELER")
        if os.path.exists(docs_dir):
            pdf_files = [f for f in os.listdir(docs_dir) if f.endswith(".pdf")]

            with st.sidebar.expander(
                f"📚 Yüklü PDF Belgeleri ({len(pdf_files)})",
                expanded=st.session_state.pdf_panel_expanded,
            ):
                if pdf_files:
                    for pdf_name in pdf_files:
                        col_pdf, col_del = st.columns([4, 1])
                        with col_pdf:
                            st.caption(f"• {pdf_name}")
                        with col_del:
                            if st.button("🗑️", key=f"del_pdf_{pdf_name}", help="PDF'i Sil"):
                                pdf_path = os.path.join(docs_dir, pdf_name)
                                if os.path.exists(pdf_path):
                                    os.remove(pdf_path)
                                    st.cache_data.clear()
                                    st.session_state.pdf_panel_expanded = True
                                    st.sidebar.success(f"'{pdf_name}' silindi!")
                                    st.rerun()
                else:
                    st.caption("Henüz yüklenmiş PDF bulunmuyor.")
st.sidebar.markdown("---")
if st.sidebar.button("🚪 Çıkış Yap", use_container_width=True):
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.user_info = None
    st.session_state.current_session_id = None
    st.session_state.messages = []
    st.rerun()

# --- ANA EKRAN ---
st.title("🏗️ OnboardMate - Çimtaş İK Asistanı")

# 📍 FABRİKA KONUMU
with st.expander("📍 Fabrika Konumu", expanded=False):
    st.caption(FACTORY_ADDRESS)
    components.iframe(FACTORY_MAPS_EMBED_URL, height=350)
    st.link_button("🗺️ Haritada Aç", FACTORY_MAPS_LINK_URL, use_container_width=True)

preset_questions = get_preset_questions(limit=8)
if preset_questions:
    st.markdown("##### 💡 Sıkça Sorulan Sorular (Hazır Sorular)")
    cols = st.columns(min(len(preset_questions), 4))
    for idx, (q_id, q_text, q_clicks) in enumerate(preset_questions):
        with cols[idx % 4]:
            if st.button(f"❓ {q_text}", key=f"preset_{q_id}", use_container_width=True):
                increment_question_click(q_id)
                st.session_state.preset_prompt = q_text
                st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("İnsan kaynakları hakkında bir soru sorun...")

if st.session_state.preset_prompt:
    prompt = st.session_state.preset_prompt
    st.session_state.preset_prompt = None

if prompt:
    if st.session_state.current_session_id is None:
        with st.spinner("Sohbet oturumu oluşturuluyor..."):
            auto_title = generate_chat_title(prompt)
            st.session_state.current_session_id = create_conversation(st.session_state.username, auto_title)

    st.session_state.messages.append({"role": "user", "content": prompt})
    save_chat_message(st.session_state.current_session_id, "user", prompt)
    
    with st.chat_message("user"):
        st.markdown(prompt)

    relevant_pdf_info = get_relevant_context(prompt, pdf_chunks)

    loc_context = f"--- ŞİRKET KONUM BİLGİSİ ---\nFabrika Adresi: {FACTORY_ADDRESS}\n"

    system_instruction = (
        f"Sen Çimtaş şirketinin kibar, samimi ve son derece yardımsever İK Asistanı OnboardMate'sin.\n"
        f"Konuştuğun Kullanıcı: {st.session_state.user_info['full_name']} (Unvan: {st.session_state.user_info['role']}).\n\n"
        f"GÖREV VE TALİMATLAR:\n"
        f"1. SELAMLAŞMA: Kullanıcı selam verdiğinde kibarca selamla ve İK rehberi / fabrika konumu hakkında yardımcı olabileceğini söyle.\n"
        f"2. KONUM SORULARI: Kullanıcı fabrikanın/işyerinin nerede olduğunu sorduğunda 'ŞİRKET KONUM BİLGİSİ' verisini kullanarak adresi paylaş ve sayfadaki '📍 Fabrika Konumu' bölümünden haritayı görebileceğini belirt.\n"
        f"3. DOKÜMANA SADAKAT: Şirket kural ve prosedürleri sorulduğunda SADECE 'ŞİRKET DOKÜMAN BİLGİSİ' verilerine dayanarak cevap ver.\n"
        f"4. DOKÜMAN DIŞI SORULAR: Sordukları spesifik konu dokümanda yoksa 'Bu konu hakkında şirket rehberimizde detaylı bir bilgi yer almamaktadır.' de.\n"
        f"5. SADECE TÜRKÇE yanıt ver."
    )

    ollama_messages = [{'role': 'system', 'content': system_instruction}]
    
    for msg in st.session_state.messages[-5:-1]:
        ollama_messages.append({'role': msg['role'], 'content': msg['content']})

    user_content = f"{loc_context}\n--- ŞİRKET DOKÜMAN BİLGİSİ ---\n{relevant_pdf_info}\n-----------------------------\n\nKullanıcı Sorduğu Soru: {prompt}"
    
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