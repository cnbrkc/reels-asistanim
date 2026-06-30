import streamlit as st
from google import genai
from google.genai import types
import json
import os
import re
import time
import traceback
import wave

# ============================================================
# otoXtra — Otomatik Reels Asistanı
# AŞAMA 2 GÜNCELLEMESİ (VİDEO ANALİZİ ENTEGRASYONU):
#   1) Viral referans video yükleme alanı eklendi.
#   2) Gemini'ye videoyu izletip Türk izleyicisi için "viral DNA" analizi
#      yaptıran özel bir prompt ve fonksiyon eklendi.
#   3) Video analizi için en güncel modeller (2.5 Pro/Flash) listeye alındı.
#   4) Manuel metin girişi ile video analizi aynı anda çalışabilir hale getirildi.
#   5) Ücretsiz API limiti (20MB) için otomatik boyut kontrolü eklendi.
# ============================================================


# ------------------------------------------------------------
# MODEL LİSTELERİ (öncelik sırasına göre: en güçlü -> en garanti)
# ------------------------------------------------------------
METIN_MODELLERI = [
    "gemini-3.1-pro-preview",   
    "gemini-3.5-flash",        
    "gemini-3-flash-preview",  
    "gemini-2.5-pro",          
    "gemini-2.5-flash",        
]

SES_MODELLERI = [
    "gemini-2.5-pro-preview-tts",    
    "gemini-2.5-flash-preview-tts",  
]

# YENİ: Video analizi için en güncel ve video desteği en güçlü modeller
VIDEO_ANALIZ_MODELLERI = [
    "gemini-2.5-pro",      # En güçlü video analizi ve Türkçe nüansları yakalama
    "gemini-2.5-flash",    # Hızlı ve stabil video işleme
    "gemini-1.5-pro",      # Eski ama video konusunda çok kanıtlanmış
    "gemini-1.5-flash",    # Garantici yedek
]


# ------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ------------------------------------------------------------

def markdown_temizle(metin: str) -> str:
    if not isinstance(metin, str):
        return ""
    return re.sub(r"\*\*|__", "", metin).strip()


def kapak_basliklarini_formatla(liste) -> str:
    if not isinstance(liste, list) or not liste:
        return markdown_temizle(str(liste)) if liste else "(Kapak başlığı üretilemedi.)"

    satirlar = []
    for i, secenek in enumerate(liste, start=1):
        if isinstance(secenek, dict):
            ana = re.sub(r"[*_#`]", "", str(secenek.get("ana", ""))).strip()
            alt = re.sub(r"[*_#`]", "", str(secenek.get("alt", ""))).strip()
        else:
            ana, alt = re.sub(r"[*_#`]", "", str(secenek)).strip(), ""
        if alt:
            satirlar.append(f"{i}) {ana}\n    {alt}")
        else:
            satirlar.append(f"{i}) {ana}")
    return "\n\n".join(satirlar)


def muzik_onerisini_formatla(muzik_onerisi) -> str:
    if not isinstance(muzik_onerisi, dict):
        return "(Müzik önerisi üretilemedi.)"
    tarz = markdown_temizle(str(muzik_onerisi.get("tarz", "")))
    sarkilar = muzik_onerisi.get("sarki_onerileri", []) or []
    satirlar = [f"Tarz / Mod: {tarz}", ""]
    for s in sarkilar:
        satirlar.append(f"- {markdown_temizle(str(s))}")
    if not sarkilar:
        satirlar.append("(Şarkı önerisi üretilemedi.)")
    return "\n".join(satirlar)


def metin_uret(client, model_listesi, video_icerigi, system_prompt, response_schema, log_ekle):
    son_hata = None
    for model_adi in model_listesi:
        log_ekle(f"🧠 Metin üretimi deneniyor: {model_adi}")
        for deneme in range(2):
            try:
                response = client.models.generate_content(
                    model=model_adi,
                    contents=video_icerigi,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    ),
                )
                veri = json.loads(response.text)
                log_ekle(f"✅ İçerik üretildi → kullanılan model: {model_adi}")
                return veri, model_adi
            except Exception as e:
                son_hata = e
                hata_metni = str(e)
                if "503" in hata_metni and deneme == 0:
                    log_ekle(f"⏳ {model_adi} şu an meşgul (503). 3 sn sonra tekrar denenecek...")
                    time.sleep(3)
                    continue
                else:
                    log_ekle(f"⚠️ {model_adi} kullanılamadı ({hata_metni[:90]}...) → sıradaki modele geçiliyor")
                    break
    raise son_hata if son_hata else Exception("Hiçbir model içerik üretemedi.")


def ses_uret(client, model_listesi, metin, ses_adi, cikti_dosyasi, log_ekle):
    son_hata = None
    for model_adi in model_listesi:
        log_ekle(f"🎙️ Seslendirme deneniyor: {model_adi} (ses: {ses_adi})")
        for deneme in range(2):
            try:
                tts_response = client.models.generate_content(
                    model=model_adi,
                    contents=metin,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=ses_adi
                                )
                            )
                        ),
                    ),
                )
                audio_data = tts_response.candidates[0].content.parts[0].inline_data.data
                with wave.open(cikti_dosyasi, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(audio_data)
                log_ekle(f"✅ Ses üretildi → kullanılan model: {model_adi}")
                return True, model_adi
            except Exception as e:
                son_hata = e
                hata_metni = str(e)
                if "503" in hata_metni and deneme == 0:
                    log_ekle(f"⏳ {model_adi} meşgul (503). 4 sn sonra tekrar denenecek...")
                    time.sleep(4)
                    continue
                else:
                    log_ekle(f"⚠️ {model_adi} ile ses üretilemedi ({hata_metni[:90]}...) → sıradaki modele geçiliyor")
                    break
    log_ekle(f"❌ Hiçbir ses modeli başarılı olamadı. Son hata: {str(son_hata)[:90] if son_hata else 'yok'}")
    return False, None


# YENİ: VİDEO ANALİZ FONKSİYONU
def video_analiz_et(client, model_listesi, video_bytes, mime_type, log_ekle):
    """Yüklenen videoyu Gemini'ye gönderip Türk izleyicisi için viral analiz yaptırır."""
    son_hata = None
    # Videoyu Gemini'nin okuyabileceği formata (Part) çeviriyoruz
    video_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
    
    # TÜRK İZLEYİCİSİ İÇİN ÖZEL GİZLİ PROMPT
    analiz_promptu = """Sen Türkiye'de sosyal medya (Instagram Reels, TikTok, YouTube Shorts) algoritmalarını ve Türk izleyicisinin psikolojisini avucunun içi gibi bilen uzman bir viral strateistsin.
Yüklediğim videoyu kare kare, sesiyle birlikte analiz et. Amacımız bu videodaki 'viral DNA'yı çekip çıkarmak ve Türkiye'de patlama yapacak benzer bir içerik üretmek.
Bana şu başlıklarda detaylı rapor ver:

1. GÖRSEL AKIŞ & KURGU: Ekranda tam olarak ne görünüyor? Kamera açıları, geçişler, hızlandırma/yavaşlatma kullanımı nasıl? Türk izleyicisinin gözünü ekranda tutan görsel detaylar neler?
2. İŞİTSEL & METİN İÇERİĞİ: Seslendirme veya ekrandaki yazılar ne anlatıyor? Temel bilgi veya argüman ne?
3. VİRAL KANCASI (HOOK): Videonun ilk 3 saniyesinde izleyiciyi tutan şey ne? (Fiyat şoku, merak unsuru, tartışma yaratacak bir iddia, relatable bir dert vb.)
4. TÜRK İZLEYİCİSİ İÇİN POTANSİYEL: Bu konunun Türkiye'de neden tutacağını analiz et. (Örn: Ekonomik durum, markalar arası rekabet, günlük hayatın içinden bir kesit, 'bizden biri' hissi, yerel mizah vb.)
5. DUYGU VE TON: Video izleyicide hangi duyguyu uyandırıyor? (Gaza getirme, öfke, şaşkınlık, gülme, 'acaba?' dedirtme vb.)

ÖNEMLİ: Videodaki bilgiler (özellikle fiyat, model yılı, teknik özellik gibi) Türkiye piyasası için güncel değilse veya eksikse, lütfen kendi güncel Türkiye verilerinle (internet araştırması mantığıyla) bunları düzelt veya eksiklerini tamamla. 

Bu bilgileri, bir sonraki adımda benim 'kurallar.txt' dosyamdaki formata göre seslendirme metni ve açıklama üretmen için bana ham veri olarak, maddeler halinde ve çok net bir özetle ver. Doğrudan analiz sonucunu yaz, ekstra konuşma yapma."""

    for model_adi in model_listesi:
        log_ekle(f"🔍 Video analizi deneniyor: {model_adi}")
        for deneme in range(2):
            try:
                response = client.models.generate_content(
                    model=model_adi,
                    contents=[video_part, analiz_promptu],
                )
                log_ekle(f"✅ Video analiz edildi → kullanılan model: {model_adi}")
                return response.text, model_adi
            except Exception as e:
                son_hata = e
                hata_metni = str(e)
                if "503" in hata_metni and deneme == 0:
                    log_ekle(f"⏳ {model_adi} şu an meşgul (503). 3 sn sonra tekrar denenecek...")
                    time.sleep(3)
                    continue
                else:
                    log_ekle(f"⚠️ {model_adi} kullanılamadı ({hata_metni[:90]}...) → sıradaki modele geçiliyor")
                    break
    raise son_hata if son_hata else Exception("Hiçbir model videoyu analiz edemedi.")


# ------------------------------------------------------------
# SAYFA AYARLARI
# ------------------------------------------------------------
st.set_page_config(page_title="otoXtra Asistanım", page_icon="🏎️", layout="wide")

st.markdown(
    """
    <style>
    pre, code {
        white-space: pre-wrap !important;
        word-break: break-word !important;
        overflow-wrap: anywhere !important;
    }
    @media (max-width: 640px) {
        .block-container {
            padding-left: 0.9rem;
            padding-right: 0.9rem;
            padding-top: 1.2rem;
        }
        h2, h3 {
            font-size: 1.05rem !important;
        }
        .stButton button {
            width: 100%;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.subheader("🏎️ otoXtra — Otomatik Reels Asistanı")
st.caption("Videonun konusunu manuel yazın VEYA viral referans videonuzu yükleyin; otoXtra gerisini halletsin!")

# ------------------------------------------------------------
# UYGULAMA DURUMU
# ------------------------------------------------------------
if "sonuc" not in st.session_state:
    st.session_state.sonuc = None
if "log_satirlari" not in st.session_state:
    st.session_state.log_satirlari = []

# ------------------------------------------------------------
# API ŞİFRESİ
# ------------------------------------------------------------
try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    st.error("🔑 GEMINI_API_KEY bulunamadı! Lütfen Streamlit ayarlarından (Secrets) anahtarınızı girin.")
    st.stop()

# ------------------------------------------------------------
# SOL MENÜ - SES SEÇİMİ
# ------------------------------------------------------------
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

    with st.expander("ℹ️ Hangi modeller deneniyor?"):
        st.caption("Metin üretimi (sırayla denenir):")
        for m in METIN_MODELLERI:
            st.caption(f"• {m}")
        st.caption("Seslendirme (sırayla denenir):")
        for m in SES_MODELLERI:
            st.caption(f"• {m}")
        st.caption("Video analizi (sırayla denenir):")
        for m in VIDEO_ANALIZ_MODELLERI:
            st.caption(f"• {m}")

# YENİ: VİDEO YÜKLEME ALANI
uploaded_video = st.file_uploader(
    "🎥 Viral Referans Videonu Yükle (Otomatik Analiz Edilsin)", 
    type=['mp4', 'mov', 'webm'],
    help="Videoyu yüklersen, Gemini videoyu izleyip Türk izleyicisi için viral noktaları otomatik çıkarır."
)

if uploaded_video is not None:
    st.video(uploaded_video)
    if uploaded_video.size > 20 * 1024 * 1024:
        st.error("⚠️ Video dosyası 20 MB'tan büyük! Ücretsiz Gemini API limiti nedeniyle videoyu analiz edemeyebilir. Lütfen videoyu sıkıştırıp (720p, düşük bitrate) tekrar yükle.")
    else:
        st.info("📌 **Not:** AI, seslendirmeyi tam bu videonun süresine göre ayarlayacak. Aşağıdaki 'Süre' kısmına videonun saniyesini tam olarak yazdığından emin ol.")

konu_akisi = st.text_area(
    "🎬 Videonun konusu / akışı (Video yüklerseniz buraya ek notlar yazabilirsiniz)",
    height=130,
    placeholder="Örn: Sadece Togg T10X'in çekiş sistemine odaklan, Tucson ile karşılaştırmaya gerek yok...",
)

sc1, sc2 = st.columns([1, 3])
with sc1:
    sure_saniye = st.number_input("⏱️ Süre (saniye)", min_value=5, max_value=180, value=30, step=5)

ek_istekler = st.text_area(
    "✨ Ek istekler (opsiyonel)",
    height=80,
    placeholder="Örn: hook'ta fiyat vurgusu olsun, kapanışta soru sor... (boş bırakabilirsin)",
)

buton_tiklandi = st.button("🚀 otoXtra İçeriğini Üret!")

log_kutusu = st.empty()

def gunlugu_ciz():
    if st.session_state.log_satirlari:
        log_kutusu.code("\n".join(st.session_state.log_satirlari), language=None)
    else:
        log_kutusu.empty()

def log_ekle(satir: str):
    st.session_state.log_satirlari.append(satir)
    gunlugu_ciz()

gunlugu_ciz()
st.caption(
    "🐛 Bir sorun/hata görürsen bu kutunun tamamını (sağ üstteki kopyalama ikonuyla) "
    "kopyalayıp doğrudan Claude'a yapıştırman yeterli — orada ne olduğunu anlayıp düzeltirim."
)

if buton_tiklandi:
    st.session_state.log_satirlari = []
    log_ekle("🚀 Üretim başladı...")

    try:
        client = genai.Client(api_key=gemini_key)

        # YENİ: VİDEO ANALİZİ ENTEGRASYONU
        analiz_metni = ""
        if uploaded_video is not None and uploaded_video.size <= 20 * 1024 * 1024:
            log_ekle("🎥 Yüklenen video Gemini tarafından izlenip analiz ediliyor...")
            video_bytes = uploaded_video.getvalue()
            mime_type = uploaded_video.type
            
            analiz_metni, analiz_modeli = video_analiz_et(
                client, VIDEO_ANALIZ_MODELLERI, video_bytes, mime_type, log_ekle
            )
            log_ekle("🧠 Video analiz tamamlandı, şimdi kurallara göre içerik üretiliyor...")
            
            # Video analizini ve manuel girdileri birleştiriyoruz
            video_icerigi = (
                f"ANALİZ EDİLEN VİDEODAN ÇIKARILAN BİLGİLER:\n{analiz_metni}\n\n"
                f"MANUEL GİRİLEN KONU / EK İSTEKLER:\n"
                f"Video konusu / akışı: {konu_akisi}\n"
                f"Video süresi: {sure_saniye} saniye\n"
                f"Özel istekler: {ek_istekler.strip() if ek_istekler.strip() else 'Yok'}"
            )
        else:
            if not konu_akisi.strip():
                st.warning("Lütfen videonun konusunu/akışını yazın veya bir referans video yükleyin.")
                st.stop()
                
            video_icerigi = (
                f"Video konusu / akışı: {konu_akisi}\n"
                f"Video süresi: {sure_saniye} saniye\n"
                f"Özel istekler: {ek_istekler.strip() if ek_istekler.strip() else 'Yok'}"
            )

        # 1. KURALLARI TXT DOSYASINDAN OKUMA
        try:
            with open("kurallar.txt", "r", encoding="utf-8") as f:
                BENIM_GEM_KURALLARIM = f.read()
        except FileNotFoundError:
            st.error("⚠️ 'kurallar.txt' dosyası bulunamadı! Lütfen GitHub deponuza bu isimde bir dosya ekleyin.")
            st.stop()

        system_prompt = BENIM_GEM_KURALLARIM + """

ÖNEMLİ SİSTEM TALİMATI (otoXtra Uygulaması):
Yukarıdaki otoXtra kurallarına (ton, vuruş yapısı, caption katmanları, hashtag kuralları vb.) GÖRE üretim yap.
Ancak NİHAİ ÇIKTIYI, yukarıdaki "ÇIKTI FORMATI" bölümündeki ham metin/markdown gösterimi DEĞİL, sadece
aşağıda tanımlanan JSON alanlarına göre ver:

- seslendirme_metni: 4 vuruş yapısına uygun, TTS motoruna gidecek seslendirme metni. Düz metin, markdown KULLANMA.
- reels_aciklamasi: Katmanlı Instagram açıklaması + en sonda 5 hashtag (tek bütün metin). Markdown KULLANMA
  (yalnızca caption'a ait #etiketler kalabilir, onlar hashtag'dir, markdown değildir).
- kapak_basliklari: 5 farklı kapak başlığı seçeneği. Her biri "ana" (2-4 kelime, TAMAMI BÜYÜK HARF) ve
  "alt" (1 cümle) alanlarından oluşur. Bu alanların İÇİNDE markdown (**, _, #, vb.) veya "SEÇENEK 1" gibi
  etiket KULLANMA, sadece düz metin yaz.
- muzik_onerisi: Bu videonun moduna uygun "tarz" (TEK KELİME İngilizce mood/genre, örn: phonk, upbeat,
  cinematic) ve Instagram/Threads "Edits" uygulamasının müzik kütüphanesinde bulunma ihtimali yüksek,
  GERÇEKTEN VAR OLAN 3 adet şarkı önerisi ver ("sarki_onerileri" listesi, format: "Şarkı Adı - Sanatçı").
  Bunlar indirilecek dosyalar değil, sadece kullanıcının Instagram Edits içinde arayıp ekleyeceği öneriler.

alt_metin alanı İSTENMİYOR, üretme.
"""

        response_schema = {
            "type": "OBJECT",
            "properties": {
                "seslendirme_metni": {"type": "STRING"},
                "reels_aciklamasi": {"type": "STRING"},
                "kapak_basliklari": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "ana": {"type": "STRING"},
                            "alt": {"type": "STRING"},
                        },
                        "required": ["ana", "alt"],
                    },
                },
                "muzik_onerisi": {
                    "type": "OBJECT",
                    "properties": {
                        "tarz": {"type": "STRING"},
                        "sarki_onerileri": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                        },
                    },
                    "required": ["tarz", "sarki_onerileri"],
                },
            },
            "required": ["seslendirme_metni", "reels_aciklamasi", "kapak_basliklari", "muzik_onerisi"],
        }

        # 2. METİN ÜRETİMİ
        veri, kullanilan_metin_modeli = metin_uret(
            client, METIN_MODELLERI, video_icerigi, system_prompt, response_schema, log_ekle
        )

        # 3. SES ÜRETİMİ
        secilen_ses_ingilizce = ses_secimi.split(" ")[0]
        ses_dosyasi = "seslendirme.wav"
        ses_basarili, kullanilan_ses_modeli = ses_uret(
            client, SES_MODELLERI, veri["seslendirme_metni"], secilen_ses_ingilizce, ses_dosyasi, log_ekle
        )

        log_ekle("🎵 Müzik önerisi içerikle birlikte üretildi.")
        log_ekle("🏁 Tüm işlem tamamlandı.")

        st.session_state.sonuc = {
            "veri": veri,
            "ses_basarili": ses_basarili,
            "ses_dosyasi": ses_dosyasi,
            "secilen_ses_ingilizce": secilen_ses_ingilizce,
            "kullanilan_metin_modeli": kullanilan_metin_modeli,
            "kullanilan_ses_modeli": kullanilan_ses_modeli,
        }

    except Exception:
        hata_detay = traceback.format_exc()
        log_ekle("❌ HATA OLUŞTU — işlem tamamlanamadı. Aşağıdaki tüm kutuyu kopyalayıp Claude'a gönderebilirsin:")
        log_ekle(hata_detay)
        st.error("Sistemde bir hata oluştu. Yukarıdaki süreç kutusunun tamamını kopyalayıp bana gönderirsen hemen bakarım.")

# ------------------------------------------------------------
# 4. SONUÇLARI GÖSTER
# ------------------------------------------------------------
if st.session_state.sonuc:
    sonuc = st.session_state.sonuc
    veri = sonuc["veri"]
    ses_basarili = sonuc["ses_basarili"]
    ses_dosyasi = sonuc["ses_dosyasi"]
    secilen_ses_ingilizce = sonuc["secilen_ses_ingilizce"]
    kullanilan_metin_modeli = sonuc.get("kullanilan_metin_modeli", "?")
    kullanilan_ses_modeli = sonuc.get("kullanilan_ses_modeli", "?")

    st.success(f"✅ otoXtra İçeriği Başarıyla Üretti! (Metin: {kullanilan_metin_modeli})")

    c1, c2 = st.columns([3, 1])
    with c2:
        if st.button("🔄 Yeniden Sorgu (Temizle)"):
            st.session_state.sonuc = None
            st.session_state.log_satirlari = []
            st.rerun()

    st.markdown("### 🎧 Medya Dosyaları")
    mcol1, mcol2 = st.columns(2)

    with mcol1:
        st.markdown(f"**🎙️ Seslendirme** (model: {kullanilan_ses_modeli})")
        if ses_basarili and os.path.exists(ses_dosyasi):
            st.audio(ses_dosyasi)
            with open(ses_dosyasi, "rb") as f:
                st.download_button(
                    f"⬇️ {secilen_ses_ingilizce} Sesini İndir (.wav)",
                    f, file_name="seslendirme.wav", mime="audio/wav",
                )
        else:
            st.warning("Ses dosyası bulunamadı. Lütfen tekrar üretin.")

    with mcol2:
        st.markdown("**🎵 Müzik Önerisi** (Instagram Edits'te ara ve ekle)")
        muzik_metni = muzik_onerisini_formatla(veri.get("muzik_onerisi"))
        st.code(muzik_metni, language=None)

    st.divider()
    st.markdown("### 📝 otoXtra Metin İçerikleri")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("1️⃣ Reels Açıklaması (Caption & Etiketler)")
        st.caption("Kutunun sağ üst köşesindeki ikonla direkt kopyalayabilirsin.")
        st.code(markdown_temizle(veri.get("reels_aciklamasi", "")), language=None)

    with col2:
        st.subheader("2️⃣ Kapak Başlığı Alternatifleri")
        st.caption("Kutunun sağ üst köşesindeki ikonla direkt kopyalayabilirsin.")
        st.code(kapak_basliklarini_formatla(veri.get("kapak_basliklari")), language=None)

    with st.expander("🎙️ Seslendirme Metni (kontrol için)"):
        st.code(markdown_temizle(veri.get("seslendirme_metni", "")), language=None)

