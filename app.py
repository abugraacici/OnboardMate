import os
import re
import streamlit as st
import streamlit.components.v1 as components
import ollama
from config import (
    BELGELER_DIR,
    OLLAMA_MODEL,
    IK_SECRET_KEY,
    FACTORY_ADDRESS,
    FACTORY_MAPS_EMBED_URL,
    FACTORY_MAPS_LINK_URL,
)
from rag import sync_pdf_index, get_relevant_context, delete_pdf_from_index, NOT_FOUND_MESSAGES
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
if "pdf_index_synced" not in st.session_state:
    st.session_state.pdf_index_synced = False
if "saved_pdf_names" not in st.session_state:
    st.session_state.saved_pdf_names = set()

# --- İK REHBER DOKÜMANLARI SENKRONİZASYONU ---
if not st.session_state.pdf_index_synced:
    with st.spinner("Doküman rehberi kontrol ediliyor ve indeksleniyor..."):
        sync_pdf_index(BELGELER_DIR)
    st.session_state.pdf_index_synced = True


def generate_chat_title(first_prompt: str) -> str:
    """İlk mesajdan kısa ve anlamlı bir sohbet başlığı üretir."""
    try:
        res = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{
                'role': 'user', 
                'content': f"Şu Türkçe kullanıcı sorusunu 3-4 kelimelik, kısa ve net bir Türkçe sohbet başlığı yap. Sadece başlığı yaz: '{first_prompt}'"
            }],
            options={'temperature': 0.1}
        )
        title = res['message']['content'].strip().replace('"', '').replace("'", "")
        return title[:35]
    except Exception:
        return first_prompt[:25] + "..."


def clean_context_for_prompt(context_text: str) -> str:
    """Dokümandaki teknik etiketleri (Konu, Anahtar kelimeler) temizler."""
    if not context_text:
        return ""
    cleaned_lines = []
    for line in context_text.split("\n"):
        stripped = line.strip()
        if re.match(r"^(Konu|Anahtar kelimeler)\s*:", stripped, re.IGNORECASE):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def is_greeting_or_general(text: str) -> bool:
    """Kullanıcının selamlaşma veya genel yetenek sorusu sorup sormadığını kontrol eder."""
    t = text.lower().strip()
    keywords = [
        "merhaba", "selam", "günaydın", "iyi günler", "iyi akşamlar", "hey",
        "nasılsın", "naber", "ne haber", "ne yapabilirsin", "neler yapabilirsin", 
        "hangi konularda", "yardımcı olabilirsin", "kimsin", "sen kimsin", 
        "teşekkür", "sağol", "kolay gelsin", "yardım"
    ]
    return any(k in t for k in keywords)


# ==============================================================================
# GİRİŞ VE KAYIT EKRANI
# ==============================================================================
if not st.session_state.logged_in:
    st.title("🏗️ OnboardMate - Çimtaş İK Asistanı")
    st.markdown("##### Hoş Geldiniz! Lütfen devam etmek için giriş yapın veya kayıt olun.")
    
    tab_login, tab_register = st.tabs(["🔑 Giriş Yap", "📝 Kayıt Ol"])
    
    with tab_login:
        login_user = st.text_input("Kullanıcı Adı", key="login_user")
        login_pass = st.text_input("Şifre", type="password", key="login_pass")
        if st.button("Giriş Yap", use_container_width=True):
            if login_user.strip() and login_pass.strip():
                user = verify_user(login_user.strip(), login_pass.strip())
                if user:
                    st.session_state.logged_in = True
                    st.session_state.username = login_user.strip()
                    st.session_state.user_info = {"full_name": user[0], "role": user[1]}
                    st.session_state.current_session_id = None
                    st.session_state.messages = []
                    st.success("Giriş başarılı!")
                    st.rerun()
                else:
                    st.error("Kullanıcı adı veya şifre hatalı!")
            else:
                st.warning("Lütfen kullanıcı adı ve şifrenizi girin.")

    with tab_register:
        reg_fullname = st.text_input("Ad Soyad", key="reg_name")
        reg_user = st.text_input("Kullanıcı Adı Belirleyin", key="reg_user")
        reg_pass = st.text_input("Şifre Belirleyin", type="password", key="reg_pass")
        reg_role = st.selectbox("Rolünüzü Seçin", ["Çalışan", "İnsan Kaynakları (Admin)"], key="reg_role")
        
        ik_key_input = ""
        if reg_role == "İnsan Kaynakları (Admin)":
            ik_key_input = st.text_input(
                "İK Gizli Katılım Anahtarı", 
                type="password", 
                help="İnsan Kaynakları yetkisi için şirketinizin verdiği anahtarı girin.", 
                key="reg_ik_key"
            )

        if st.button("Kayıt Ol", use_container_width=True):
            if reg_fullname.strip() and reg_user.strip() and reg_pass.strip():
                selected_role = "Çalışan"
                is_valid = True
                
                if reg_role == "İnsan Kaynakları (Admin)":
                    if ik_key_input.strip() == IK_SECRET_KEY:
                        selected_role = "İnsan Kaynakları"
                    else:
                        st.error("Girdiğiniz İK Katılım Anahtarı hatalı! Kayıt tamamlanamadı.")
                        is_valid = False

                if is_valid:
                    success = add_user(reg_user.strip(), reg_pass.strip(), reg_fullname.strip(), role=selected_role)
                    if success:
                        st.success(f"Kayıt başarılı! [{selected_role}] rolüyle 'Giriş Yap' sekmesinden giriş yapabilirsiniz.")
                    else:
                        st.warning("Bu kullanıcı adı zaten alınmış!")
            else:
                st.warning("Lütfen tüm alanları doldurun.")
    st.stop()


# ==============================================================================
# YAN PANEL (SIDEBAR)
# ==============================================================================
user_info = st.session_state.user_info or {"full_name": "Kullanıcı", "role": "Çalışan"}
st.sidebar.title(f"👤 {user_info['full_name']}")
st.sidebar.write(f"**Erişim Rolü:** `{user_info['role']}`")

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
            if st.button("🗑️", key=f"del_{conv_id}", help="Sohbeti Sil"):
                delete_conversation(conv_id)
                if st.session_state.current_session_id == conv_id:
                    st.session_state.current_session_id = None
                    st.session_state.messages = []
                st.rerun()
else:
    st.sidebar.caption("Henüz sohbet geçmişiniz yok.")

# ------------------------------------------------------------------------------
# İK YÖNETİM PANELİ (Sadece İK Rolüne Görünür)
# ------------------------------------------------------------------------------
user_role = user_info.get("role", "")

if user_role == "İnsan Kaynakları":
    st.sidebar.markdown("---")
    with st.sidebar.expander("👑 İK İçerik Yönetici Paneli", expanded=False):
        tab_q_mgmt, tab_pdf_mgmt = st.tabs(["💡 Hazır Sorular", "📄 PDF Belgeleri"])
        
        # 1. Hazır Soru Yönetimi
        with tab_q_mgmt:
            new_q = st.text_input("Yeni Hazır Soru Ekle", placeholder="Örn: Servis güzergahları neler?")
            if st.button("Soru Ekle", key="btn_add_q", use_container_width=True):
                if new_q.strip():
                    add_preset_question(new_q.strip(), st.session_state.username)
                    st.success("Hazır soru eklendi!")
                    st.rerun()

            st.markdown("---")
            existing_qs = get_preset_questions(limit=50)
            st.caption(f"Kayıtlı Hazır Sorular ({len(existing_qs) if existing_qs else 0})")
            if existing_qs:
                for q_id, q_text, q_clicks in existing_qs:
                    c_txt, c_del = st.columns([4, 1])
                    with c_txt:
                        st.caption(f"• {q_text} ({q_clicks} tık)")
                    with c_del:
                        if st.button("🗑️", key=f"del_q_{q_id}", help="Soruyu Sil"):
                            delete_preset_question(q_id)
                            st.rerun()
            else:
                st.caption("Henüz kayıtlı soru yok.")

        # 2. PDF Doküman Yönetimi
        with tab_pdf_mgmt:
            uploaded_file = st.file_uploader("Şirket Rehberi / PDF Yükle", type=["pdf"], key="ik_pdf_uploader")
            if uploaded_file is not None:
                if uploaded_file.name not in st.session_state.saved_pdf_names:
                    save_path = os.path.join(BELGELER_DIR, uploaded_file.name)
                    with open(save_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    st.session_state.saved_pdf_names.add(uploaded_file.name)
                    st.session_state.pdf_index_synced = False
                    st.success(f"'{uploaded_file.name}' yüklendi! İndeksleniyor...")
                    st.rerun()

            st.markdown("---")
            if os.path.exists(BELGELER_DIR):
                pdf_files = [f for f in os.listdir(BELGELER_DIR) if f.lower().endswith(".pdf")]
                st.caption(f"Yüklü PDF Belgeleri ({len(pdf_files)})")
                if pdf_files:
                    for pdf_name in pdf_files:
                        c_pdf, c_del = st.columns([4, 1])
                        with c_pdf:
                            st.caption(f"• {pdf_name}")
                        with c_del:
                            if st.button("🗑️", key=f"del_pdf_{pdf_name}", help="PDF'i Sil"):
                                pdf_path = os.path.join(BELGELER_DIR, pdf_name)
                                if os.path.exists(pdf_path):
                                    os.remove(pdf_path)
                                delete_pdf_from_index(pdf_name)
                                st.session_state.pdf_index_synced = False
                                st.session_state.saved_pdf_names.discard(pdf_name)
                                st.rerun()
                else:
                    st.caption("Henüz yüklenmiş PDF yok.")

st.sidebar.markdown("---")
if st.sidebar.button("🚪 Çıkış Yap", use_container_width=True):
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.user_info = None
    st.session_state.current_session_id = None
    st.session_state.messages = []
    st.rerun()


# ==============================================================================
# ANA EKRAN (STANDART YAPAY ZEKA SOHBET DÜZENİ)
# ==============================================================================
st.title("🏗️ OnboardMate - Çimtaş İK Asistanı")

# 📍 FABRİKA KONUMU (Açılır/Kapanır Bölüm)
with st.expander("📍 Fabrika Konumu ve Harita", expanded=False):
    st.caption(FACTORY_ADDRESS)
    components.iframe(FACTORY_MAPS_EMBED_URL, height=320)
    st.link_button("🗺️ Google Haritalar'da Aç ve Yol Tarifi Al", FACTORY_MAPS_LINK_URL, use_container_width=True)

# 💡 SIKÇA SORULAN SORULAR (HAZIR SORU ÇİPLERİ)
preset_questions = get_preset_questions(limit=8)
if preset_questions:
    st.markdown("##### 💡 Sıkça Sorulan Sorular")
    cols = st.columns(min(len(preset_questions), 4))
    for idx, (q_id, q_text, q_clicks) in enumerate(preset_questions):
        with cols[idx % 4]:
            if st.button(f"❓ {q_text}", key=f"preset_{q_id}", use_container_width=True):
                increment_question_click(q_id)
                st.session_state.preset_prompt = q_text
                st.rerun()

# 💬 SOHBET MESAJLARI (Açılır / Kapanır Butonlu Bölüm)
if st.session_state.messages:
    with st.expander("💬 Sohbet Mesajlarını Gizle / Göster", expanded=True):
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

# 🔻 EKRANIN EN ALTINA SABİTLENMİŞ ARAMA/GİRİŞ ÇUBUĞU (ChatGPT / AI Standardı)
prompt = st.chat_input("İnsan kaynakları hakkında bir soru sorun...")

# Hazır bir soru butonuna tıklandıysa arama çubuğundaki gibi işlet
if st.session_state.preset_prompt:
    prompt = st.session_state.preset_prompt
    st.session_state.preset_prompt = None

if prompt:
    if st.session_state.current_session_id is None:
        with st.spinner("Sohbet oturumu başlatılıyor..."):
            auto_title = generate_chat_title(prompt)
            st.session_state.current_session_id = create_conversation(st.session_state.username, auto_title)

    st.session_state.messages.append({"role": "user", "content": prompt})
    save_chat_message(st.session_state.current_session_id, "user", prompt)
    
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.spinner("Rehber taranıyor..."):
        relevant_pdf_info = get_relevant_context(prompt)

    context_bulunamadi = relevant_pdf_info in NOT_FOUND_MESSAGES

    # --- DURUM 1: Dokümanda Bilgi Yok ve Genel Soru/Selam Değilse ---
    if context_bulunamadi and not is_greeting_or_general(prompt):
        response_text = "Bu konu hakkında şirket rehberimizde detaylı bir bilgi yer almamaktadır."
        with st.chat_message("assistant"):
            st.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})
        save_chat_message(st.session_state.current_session_id, "assistant", response_text)

    # --- DURUM 2: Dokümanda Bilgi Var veya Genel Selamlaşma İse ---
    else:
        clean_pdf_info = clean_context_for_prompt(relevant_pdf_info) if not context_bulunamadi else ""
        loc_context = f"Fabrika Adresi: {FACTORY_ADDRESS}\n"

        if context_bulunamadi:
            # Genel selamlaşma
            system_instruction = (
                f"Sen Çimtaş şirketinin resmi ve kibar İK Asistanı OnboardMate'sin.\n"
                f"Konuştuğun Kullanıcı: {user_info['full_name']} ({user_info['role']}).\n\n"
                f"Kural: Sadece Türkçe yanıt ver. Kullanıcı selam verdiğinde samimi ve kısa bir selamlama yap, "
                f"Çimtaş İK konularında (Oryantasyon, İzinler, Deneme Süresi, Servis, Fabrika) yardımcı olabileceğini belirt."
            )
            user_content = f"Kullanıcı Mesajı: {prompt}"
        else:
            # DOKÜMANDAKİ CEVABI BİREBİR VE EKSİKSİZ AKTARMA
            system_instruction = (
                f"Sen Çimtaş şirketinin resmi ve güvenilir İK Asistanı OnboardMate'sin.\n"
                f"Konuştuğun Kullanıcı: {user_info['full_name']} ({user_info['role']}).\n\n"
                f"KESİN KURALLAR:\n"
                f"1. DİL: Sadece Türkçe yanıt ver.\n"
                f"2. BİREBİR METNE SADAKAT: Aşağıdaki rehber metninde yer alan cevabı BİREBİR, EKSİKSİZ VE SADIK OLARAK AKTAR. "
                f"Kendi kafandan yeni adımlar (1. 2. 3. gibi maddeler), ekleme veya yorum KESİNLİKLE YAPMA. Rehberdeki orijinal metni aynen yaz.\n"
                f"3. DOKÜMAN BAŞLIKLARI: 'Konu:', 'Anahtar kelimeler:' gibi teknik etiketleri yazma, sadece soruya ait açıklama/cevap metnini aktar."
            )
            user_content = f"--- ŞİRKET REHBERİ BİLGİSİ ---\n{loc_context}{clean_pdf_info}\n-----------------------------\n\nKullanıcı Sorusu: {prompt}"

        ollama_messages = [{'role': 'system', 'content': system_instruction}]
        for msg in st.session_state.messages[-5:-1]:
            ollama_messages.append({'role': msg['role'], 'content': msg['content']})
        ollama_messages.append({'role': 'user', 'content': user_content})

        with st.chat_message("assistant"):
            def response_streamer():
                response = ollama.chat(
                    model=OLLAMA_MODEL,
                    messages=ollama_messages,
                    options={
                        'temperature': 0.0,
                        'repeat_penalty': 1.1,
                    },
                    stream=True,
                )
                for chunk in response:
                    yield chunk['message']['content']

            try:
                response_text = st.write_stream(response_streamer())
            except Exception as e:
                response_text = "⚠️ Ollama uygulamasıyla bağlantı kurulamadı. Modelin yüklü ve Ollama servisinin açık olduğundan emin olun."
                st.error(response_text)

            st.session_state.messages.append({"role": "assistant", "content": response_text})
            save_chat_message(st.session_state.current_session_id, "assistant", response_text)

    # Akıcı şekilde ekranı tazeleyip arama çubuğunu en altta tut
    st.rerun()