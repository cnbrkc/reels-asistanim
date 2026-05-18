import streamlit as st
from google import genai
from google.genai import types
import json
import requests
import wave
import os
import time  # Google meşgulse beklemek için yeni ekledik

# Sayfa ayarları
st.set_page_config(page_title="otoXtra Asistanım", page_icon="🏎️", layout="wide")

st.title("🏎️ otoXtra — Otomatik Reels Asistanı")
st.write("Videonun konusunu, süresini ve varsa özel notunu yaz; otoXtra gerisini halletsin!")

# Uygulama durumu (sonuçların sayfa yenilenince kaybolmaması için)
if "sonuc" not in st.session_state:
    st.session_state.sonuc = None


def pixabay_muzik_bul_ve_indir(pixabay_key: str, ilk_terim: str, cikti_dosyasi: str):
    """
    Pixabay Audio API'den uygun dosyayı bulup indirir.
    ilk_terim sonuç vermezse alternatif terimlerle yeniden dener.
    """
    fallback_terimler = ["upbeat", "phonk", "cinematic", "chill", "ambient"]
    terimler = [ilk_terim] + [t for t in fallback_terimler if t != ilk_terim]

    for terim in terimler:
        params = {"key": pixabay_key, "q": terim, "per_page": 5}
        cevap = requests.get("https://pixabay.com/api/audio/", params=params, timeout=20)
        if cevap.status_code != 200:
            continue

        veri = cevap.json()
        if veri.get("totalHits", 0) <= 0:
            continue

        for hit in veri.get("hits", []):
            audio_urls = hit.get("audio") or {}
            indirme_linki = (
                audio_urls.get("url")
                or audio_urls.get("large")
                or audio_urls.get("medium")
                or audio_urls.get("small")
                or audio_urls.get("tiny")
                or hit.get("previewURL")
            )
            if not indirme_linki:
                continue

            muzik_indir = requests.get(indirme_linki, timeout=30)
            if muzik_indir.status_code == 200 and muzik_indir.content:
                with open(cikti_dosyasi, "wb") as f:
                    f.write(muzik_indir.content)
                return True, terim

    return False, ilk_terim

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

    with st.spinner("otoXtra içerik üretiyor... (Google sunucuları yoğunsa bu işlem 30 sn sürebilir)"):
        try:
            client = genai.Client(api_key=gemini_key)
            
            # 1. KURALLARI TXT DOSYASINDAN OKUMA
            try:
                with open("kurallar.txt", "r", encoding="utf-8") as f:
                    BENIM_GEM_KURALLARIM = f.read()
            except FileNotFoundError:
                st.error("⚠️ 'kurallar.txt' dosyası bulunamadı! Lütfen GitHub deponuza bu isimde bir dosya ekleyin.")
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
            
            # 2. METİN ÜRETİMİ (Hata verirse 3 kez dener)
            veri = None
            for deneme in range(3):
                try:
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=video_icerigi,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            response_mime_type="application/json", 
                        )
                    )
                    veri = json.loads(response.text)
                    break # Başarılı olursa döngüden çık
                except Exception as e:
                    if "503" in str(e) and deneme < 2:
                        time.sleep(3) # 3 saniye bekle tekrar dene
                        continue
                    else:
                        raise e # Artık mecburen hatayı ver
            
            # 3. AI STUDIO SES ÜRETİMİ (Hata verirse 3 kez dener)
            secilen_ses_ingilizce = ses_secimi.split(" ")[0]
            ses_dosyasi = "seslendirme.wav"
            ses_basarili = False
            
            for deneme in range(3):
                try:
                    tts_response = client.models.generate_content(
                        model='gemini-2.5-flash-preview-tts',
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
                    break # Başarılı olursa döngüden çık
                except Exception as ses_hata:
                    if "503" in str(ses_hata) and deneme < 2:
                        time.sleep(4) # 4 saniye dinlenip tekrar Google'a istek atar
                        continue
                    else:
                        if deneme == 2:
                            st.warning("Google Ses Sistemleri şu an aşırı yoğun. Ses üretilemedi, lütfen 1-2 dakika sonra tekrar üretime bas.")
                            st.code(str(ses_hata))
                        break
            
            # 4. MÜZİK BULMA
            muzik_dosyasi = "muzik.mp3"
            muzik_basarili = False
            
            arama_kelimesi = "upbeat"
            try:
                arama_kelimesi = veri.get('muzik_turu', 'upbeat').strip().split()[0]
                muzik_basarili, kullanilan_terim = pixabay_muzik_bul_ve_indir(
                    pixabay_key=pixabay_key,
                    ilk_terim=arama_kelimesi,
                    cikti_dosyasi=muzik_dosyasi
                )
                arama_kelimesi = kullanilan_terim
            except Exception as m_hata:
                st.warning(f"Müzik aranırken sorun yaşandı: {m_hata}")

            # Sonucu state'e al (download sonrası sayfa yeniden çizilse bile içerik kalsın)
            st.session_state.sonuc = {
                "veri": veri,
                "ses_basarili": ses_basarili,
                "muzik_basarili": muzik_basarili,
                "ses_dosyasi": ses_dosyasi,
                "muzik_dosyasi": muzik_dosyasi,
                "secilen_ses_ingilizce": secilen_ses_ingilizce,
                "arama_kelimesi": arama_kelimesi
            }

        except Exception as e:
            st.error("Sistemde bir hata oluştu ve işlem tamamlanamadı.")
            st.code(f"Hata Detayı: {str(e)}")

# 5. SONUÇLARI GÖSTER (state varsa ekranda sabit kalsın)
if st.session_state.sonuc:
    sonuc = st.session_state.sonuc
    veri = sonuc["veri"]
    ses_basarili = sonuc["ses_basarili"]
    muzik_basarili = sonuc["muzik_basarili"]
    ses_dosyasi = sonuc["ses_dosyasi"]
    muzik_dosyasi = sonuc["muzik_dosyasi"]
    secilen_ses_ingilizce = sonuc["secilen_ses_ingilizce"]
    arama_kelimesi = sonuc["arama_kelimesi"]

    st.success("✅ otoXtra İçeriği Başarıyla Üretti!")

    c1, c2 = st.columns([3, 1])
    with c2:
        if st.button("🔄 Yeniden Sorgu (Temizle)"):
            st.session_state.sonuc = None
            st.rerun()

    st.markdown("### 🎧 Medya Dosyaları")
    mcol1, mcol2 = st.columns(2)
    with mcol1:
        st.markdown("**🎙️ Seslendirme (AI Studio)**")
        if ses_basarili and os.path.exists(ses_dosyasi):
            st.audio(ses_dosyasi)
            with open(ses_dosyasi, "rb") as f:
                st.download_button(f"⬇️ {secilen_ses_ingilizce} Sesini İndir (.wav)", f, file_name="seslendirme.wav", mime="audio/wav")
        else:
            st.warning("Ses dosyası bulunamadı. Lütfen tekrar üretin.")
    with mcol2:
        st.markdown(f"**🎵 Arka Plan Müziği** (Aranan: *{arama_kelimesi.upper()}*)")
        if muzik_basarili and os.path.exists(muzik_dosyasi):
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
