import streamlit as st
from google import genai
from google.genai import types
import json
import requests
import wave
import os

# Sayfa ayarları
st.set_page_config(page_title="otoXtra Asistanım", page_icon="🏎️", layout="wide")

st.title("🏎️ otoXtra — Otomatik Reels Asistanı")
st.write("Videonun konusunu, süresini ve varsa özel notunu yaz; otoXtra gerisini halletsin!")

# API Şifreleri
try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
    pixabay_key = st.secrets["PIXABAY_API_KEY"]
except Exception:
    st.error("🔑 API anahtarları bulunamadı! Lütfen Streamlit ayarlarından (Secrets) şifrelerinizi girin.")
    st.stop()

# Sol menü - Ses Seçimi
with st.sidebar:
    st.header("🎙️ Ses Ayarları")
    ses_secimi = st.selectbox("Seslendiren Seçimi", [
        "Autonoe (Parlak ve Canlı - Kadın)",
        "Puck (Eğlenceli ve Enerjik - Erkek)",
        "Aoede (Havadar ve Yumuşak - Kadın)",
        "Callirrhoe (Rahat ve Doğal - Kadın)",
        "Kore (Net ve Kendinden Emin - Kadın)",
        "Leda (Genç ve Dinamik - Kadın)",
        "Zephyr (Parlak - Kadın)",
        "Charon (Bilgilendirici - Erkek)",
        "Orus (Net ve Sert - Erkek)",
        "Iapetus (Temiz ve Akıcı - Erkek)",
        "Umbriel (Rahat - Erkek)"
    ])

video_icerigi = st.text_area("Videonun konusunu, tahmini süresini (örn: 30 sn) ve varsa özel isteklerini yaz:", height=150)

if st.button("🚀 otoXtra İçeriğini Üret!"):
    if not video_icerigi:
        st.warning("Lütfen videoda ne olduğunu yazın.")
        st.stop()

    with st.spinner("otoXtra senaryoyu yazıyor, AI Studio sesi üretiliyor ve müzik aranıyor... (15-20 saniye)"):
        try:
            client = genai.Client(api_key=gemini_key)
            
            # 1. KURALLARI TXT DOSYASINDAN OKUMA
            try:
                with open("kurallar.txt", "r", encoding="utf-8") as f:
                    BENIM_GEM_KURALLARIM = f.read()
            except FileNotFoundError:
                st.error("⚠️ 'kurallar.txt' dosyası bulunamadı! Lütfen GitHub deponuza bu isimde bir dosya ekleyip içine promptunuzu yapıştırın.")
                st.stop()
            
            system_prompt = BENIM_GEM_KURALLARIM + """
            
            ÖNEMLİ SİSTEM TALİMATI: 
            Yukarıdaki otoXtra kurallarına GÖRE üretim yap. Çıktıyı SADECE aşağıdaki formatta geçerli bir JSON olarak ver:
            {
              "seslendirme_metni": "4 vuruş yapısına uygun seslendirme metni",
              "reels_aciklamasi": "Katmanlı açıklama ve etiketler (tek metin halinde)",
              "kapak_basliklari": "5 farklı kapak başlığı alternatifinin tamamı (alt alta liste)",
              "alt_metin": "Instagram için 1 cümlelik alt metin",
              "muzik_turu": "Bu videonun moduna uygun TEK KELİMELİK İNGİLİZCE müzik türü (örn: upbeat, drift, phonk)"
            }
            """
            
            # 2. METİN ÜRETİMİ (Standart Flash modeli)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=video_icerigi,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json", 
                )
            )
            
            veri = json.loads(response.text)
            
            # 3. AI STUDIO SES ÜRETİMİ (ÖZEL TTS MODELİ KULLANILDI!)
            secilen_ses_ingilizce = ses_secimi.split(" ")[0]
            ses_dosyasi = "seslendirme.wav"
            ses_basarili = False
            
            try:
                # DİKKAT: Buradaki model ismini Google'ın SADECE ses üreten özel TTS modeline çevirdik.
                tts_response = client.models.generate_content(
                    model='gemini-2.5-flash-preview-tts', # Veya çalışmazsa: 'gemini-3.1-flash-tts-preview'
                    contents=veri["seslendirme_metni"],
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=secilen_ses_ingilizce
                                )
                            )
                        )
                    )
                )

                audio_data = tts_response.candidates[0].content.parts[0].inline_data.data
                with wave.open(ses_dosyasi, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(audio_data)
                
                ses_basarili = True
            except Exception as ses_hata:
                st.warning("Google Ses Sisteminde bir hata oldu (Model izni hatası olabilir).")
                st.code(str(ses_hata))
            
            # 4. MÜZİK BULMA (Daha akıllı arama)
            muzik_dosyasi = "muzik.mp3"
            muzik_basarili = False
            
            try:
                # Çoklu kelime gelirse sadece ilk kelimeyi alır (örn: 'phonk drift' -> 'phonk')
                arama_kelimesi = veri.get('muzik_turu', 'upbeat').strip().split()[0]
                
                params = {"key": pixabay_key, "q": arama_kelimesi, "per_page": 5}
                pixabay_cevap = requests.get("https://pixabay.com/api/audio/", params=params).json()
                
                if pixabay_cevap.get("totalHits", 0) > 0:
                    for hit in pixabay_cevap["hits"]:
                        indirme_linki = hit.get("audio", hit.get("preview"))
                        if indirme_linki:
                            muzik_indir = requests.get(indirme_linki)
                            with open(muzik_dosyasi, "wb") as f:
                                f.write(muzik_indir.content)
                            muzik_basarili = True
                            break
            except Exception as m_hata:
                st.warning(f"Müzik aranırken sorun yaşandı: {m_hata}")

            # 5. SONUÇLARI GÖSTER
            st.success("✅ otoXtra İçeriği Başarıyla Üretti!")
            
            st.markdown("### 🎧 Medya Dosyaları")
            mcol1, mcol2 = st.columns(2)
            with mcol1:
                st.markdown("**🎙️ Seslendirme (AI Studio)**")
                if ses_basarili:
                    st.audio(ses_dosyasi)
                    with open(ses_dosyasi, "rb") as f:
                        st.download_button(f"⬇️ {secilen_ses_ingilizce} Sesini İndir (.wav)", f, file_name="seslendirme.wav", mime="audio/wav")
            with mcol2:
                st.markdown(f"**🎵 Arka Plan Müziği** (Aranan: *{arama_kelimesi.upper()}*)")
                if muzik_basarili:
                    st.audio(muzik_dosyasi)
                    with open(muzik_dosyasi, "rb") as file:
                        st.download_button("⬇️ Müziği İndir (.mp3)", file, file_name="muzik.mp3", mime="audio/mp3")
                else:
                    st.warning("Uygun telifsiz müzik bulunamadı. Lütfen Pixabay'dan manuel bakınız.")
            
            st.divider()

            st.markdown("### 📝 otoXtra Metin İçerikleri")
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("1️⃣ Reels Açıklaması (Caption & Etiketler)")
                st.text_area("Direkt kopyalayıp yapıştır:", veri["reels_aciklamasi"], height=250)
                
                st.subheader("3️⃣ Alt Metin (Gelişmiş Ayarlar)")
                st.code(veri["alt_metin"], language="text")

            with col2:
                st.subheader("2️⃣ Kapak Başlığı Alternatifleri")
                st.text_area("Videoya eklenecek metinler:", veri["kapak_basliklari"], height=250)
                
                st.subheader("🎙️ Seslendirme Metni (Kontrol İçin)")
                st.info(veri["seslendirme_metni"])

        except Exception as e:
            st.error("Sistemde bir hata oluştu ve işlem tamamlanamadı.")
            st.code(f"Hata Detayı: {str(e)}")
