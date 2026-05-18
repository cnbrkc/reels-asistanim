import streamlit as st
from google import genai
from google.genai import types
import json
import edge_tts
import asyncio
import requests

# Sayfa ayarları
st.set_page_config(page_title="Reels Asistanım", page_icon="🎬", layout="centered")

st.title("🎬 Otomatik Reels Asistanı")
st.write("Videonun konusunu yaz; seslendirmeni, açıklamanı ve videoya uygun müziği anında al!")

# Sol menü - API Ayarları
with st.sidebar:
    st.header("🔑 Ayarlar")
    st.write("Güvenlik için API anahtarlarınızı buraya girin.")
    gemini_key = st.text_input("Google AI Studio API Key", type="password")
    pixabay_key = st.text_input("Pixabay API Key", type="password")
    ses_secimi = st.selectbox("Seslendiren Seçimi", ["tr-TR-AhmetNeural (Erkek)", "tr-TR-EmelNeural (Kadın)"])

# Kullanıcıdan video içeriği alma
video_icerigi = st.text_area("Videoda ne var? Kısaca anlat:", height=150)

# Asenkron (Ses) oluşturma fonksiyonu
def run_async(coroutine):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coroutine)

if st.button("🚀 Reels İçeriğini Üret!"):
    if not gemini_key or not pixabay_key:
        st.error("Lütfen sol menüden Gemini ve Pixabay API anahtarlarınızı girin!")
        st.stop()
        
    if not video_icerigi:
        st.warning("Lütfen videoda ne olduğunu yazın.")
        st.stop()

    with st.spinner("Senaryo yazılıyor, seslendiriliyor ve müzik aranıyor... (Bu işlem 10-15 saniye sürebilir)"):
        try:
            # 1. GEMINI İLE İÇERİK ÜRETİMİ
            client = genai.Client(api_key=gemini_key)
            system_prompt = """Sen uzman bir sosyal medya danışmanı ve Reels metin yazarısın. 
Kullanıcının verdiği video fikrine göre şunları oluştur:
1. 'seslendirme_metni': Videoda arka planda okunacak kısa, dikkat çekici, enerjik ve akıcı bir Türkçe metin.
2. 'reels_aciklamasi': Videonun altına yazılacak, emojiler ve hashtagler içeren etkileşim alacak Türkçe açıklama.
3. 'muzik_turu': Bu videonun duygu durumuna (mood) uygun, İngilizce tek kelimelik bir müzik türü (örneğin: upbeat, chill, lofi, vlog, cinematic, epic).

Çıktıyı SADECE geçerli bir JSON formatında ver."""
            
            response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents=video_icerigi,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json", # BU YENİ: Gemini'yi direkt JSON vermeye zorlar, hata riskini sıfırlar.
                )
            )
            
            # Gelen cevabı direkt okuyoruz (temizlemeye gerek kalmadı)
            veri = json.loads(response.text)
            
            # 2. SESLENDİRME (METİNDEN SESE)
            secilen_ses = ses_secimi.split()[0]
            ses_dosyasi = "seslendirme.mp3"
            
            async def tts_olustur():
                communicate = edge_tts.Communicate(veri["seslendirme_metni"], secilen_ses)
                await communicate.save(ses_dosyasi)
                
            run_async(tts_olustur())
            
            # 3. PİXABAY'DAN MÜZİK BULMA VE İNDİRME
            muzik_dosyasi = "muzik.mp3"
            muzik_basarili = False
            
            try:
                pixabay_url = f"https://pixabay.com/api/audio/?key={pixabay_key}&q={veri['muzik_turu']}&per_page=3"
                pixabay_cevap = requests.get(pixabay_url).json()
                
                if pixabay_cevap.get("totalHits", 0) > 0:
                    ses_verisi = pixabay_cevap["hits"][0]
                    indirme_linki = ses_verisi.get("audio", ses_verisi.get("preview"))
                        
                    if indirme_linki:
                        muzik_indir = requests.get(indirme_linki)
                        with open(muzik_dosyasi, "wb") as f:
                            f.write(muzik_indir.content)
                        muzik_basarili = True
            except Exception as e:
                st.warning(f"Müzik aranırken ufak bir sorun oldu: {str(e)}")

            # 4. SONUÇLARI EKRANA YAZDIRMA
            st.success("✅ İçerik Başarıyla Oluşturuldu!")
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("📝 Reels Açıklaması")
                st.text_area("Bunu kopyala:", veri["reels_aciklamasi"], height=150)
                
                st.subheader("💡 Seçilen Müzik Türü")
                st.info(f"Tavsiye edilen tarz: **{veri['muzik_turu'].upper()}**")

            with col2:
                st.subheader("🎙️ Seslendirme")
                st.audio(ses_dosyasi)
                with open(ses_dosyasi, "rb") as f:
                    st.download_button("⬇️ Seslendirmeyi İndir", f, file_name="seslendirme.mp3", mime="audio/mp3")
                
                st.subheader("🎵 Arka Plan Müziği")
                if muzik_basarili:
                    st.audio(muzik_dosyasi)
                    with open(muzik_dosyasi, "rb") as file:
                        st.download_button("⬇️ Müziği İndir", file, file_name="muzik.mp3", mime="audio/mp3")
                else:
                    st.warning("Uygun müzik otomatik indirilemedi. Lütfen Pixabay'dan manuel seçin.")

        except Exception as e:
            # BU KISIM ÇOK ÖNEMLİ: Hatayı gizlemek yerine ekrana yazdırıyoruz!
            st.error("Sistemde bir hata oluştu ve işlem tamamlanamadı.")
            st.code(f"Hata Detayı: {str(e)}") # Artık gizli kalmayacak
