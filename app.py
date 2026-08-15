import os
import streamlit as st
from google import genai
from dotenv import load_dotenv

# .env dosyasındaki gizli değişkenleri yükle
load_dotenv()

# Sayfa Ayarları
st.set_page_config(page_title="OnboardMate", page_icon="🏗️")
st.title("🏗️ Çimtaş OnboardMate - İK Asistanı")

# API Anahtarını Güvenli Şekilde Al
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

# Sohbet Hafızası
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Soru-Cevap Mantığı
if prompt := st.chat_input("Çimtaş kuralları hakkında bir soru sorun..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt,
    )

    with st.chat_message("assistant"):
        st.markdown(response.text)
    st.session_state.messages.append({"role": "assistant", "content": response.text})