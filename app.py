import streamlit as st
import google.generativeai as genai
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
    st.write("Güvenlik için API anahtarlarınızı buraya girin (Kodlara yazmadığımız için şifreleriniz başkaları tarafından görünmez).")
    gemini_key = st.text_input("Google AI Studio API Key", type="password")
    pixabay_key = st.text_input("Pixabay API Key", type="password")
    ses_secimi = st.selectbox("Seslendiren Seçimi", ["tr-TR-AhmetNeural (Erkek)", "tr-TR-EmelNeural (Kadın)"])

# Kullanıcıdan video içeriği alma
video_icerigi = st.text_area("Videoda ne var? Kısaca anlat:", height=150, placeholder="Örn: Evde kolay soğuk kahve yapımı. Önce bardağa buzu koyuyorum, sonra süt ekliyorum, en son üzerine espresso döküyorum...")

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
            # -----------------------------------------------------
            # 1. GEMINI İLE İÇERİK ÜRETİMİ
            # -----------------------------------------------------
            genai.configure(api_key=gemini_key)
            system_prompt = """Sen uzman bir sosyal medya danışmanı ve Reels metin yazarısın. 
Kullanıcının verdiği video fikrine göre şunları oluştur:
1. 'seslendirme_metni': Videoda arka planda okunacak kısa, dikkat çekici, enerjik ve akıcı bir Türkçe metin.
2. 'reels_aciklamasi': Videonun altına yazılacak, emojiler ve hashtagler içeren etkileşim alacak Türkçe açıklama (caption).
3. 'muzik_turu': Bu videonun duygu durumuna (mood) uygun, İngilizce tek kelimelik bir müzik türü (örneğin: upbeat, chill, lofi, vlog, cinematic, epic).

Çıktıyı SADECE geçerli bir JSON formatında ver. Başka hiçbir şey yazma. Örnek:
{
  "seslendirme_metni": "Evde mükemmel soğuk kahveyi yapmak sandığından çok daha kolay! İşte sırrım...",
  "reels_aciklamasi": "Evde kendi kafenizi yaratın ☕✨ #kahve #tarif",
  "muzik_turu": "chill"
}"""
            model = genai.GenerativeModel(model_name="gemini-1.5-flash", system_instruction=system_prompt)
            response = model.generate_content(video_icerigi)
            
            # Gelen cevabı temizle ve algıla
            cevap_metni = response.text.strip()
            if cevap_metni.startswith("```json"):
                cevap_metni = cevap_metni[7:-3]
            elif cevap_metni.startswith("```"):
                cevap_metni = cevap_metni[3:-3]
                
            veri = json.loads(cevap_metni)
            
            # -----------------------------------------------------
            # 2. SESLENDİRME (METİNDEN SESE)
            # -----------------------------------------------------
            secilen_ses = ses_secimi.split()[0] # Seçili sesi ayıklar
            ses_dosyasi = "seslendirme.mp3"
            
            async def tts_olustur():
                communicate = edge_tts.Communicate(veri["seslendirme_metni"], secilen_ses)
                await communicate.save(ses_dosyasi)
                
            run_async(tts_olustur())
            
            # -----------------------------------------------------
            # 3. PİXABAY'DAN MÜZİK BULMA VE İNDİRME
            # -----------------------------------------------------
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
                pass # Hata olursa uygulama çökmesin diye es geçiyoruz

            # -----------------------------------------------------
            # 4. SONUÇLARI EKRANA YAZDIRMA
            # -----------------------------------------------------
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
            st.error("Bir hata oluştu. Muhtemelen yapay zeka beklenen formatta cevap veremedi.")
            st.info("Lütfen metin kutusunu biraz daha detaylı yazmayı deneyin.")