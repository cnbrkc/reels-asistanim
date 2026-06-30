import streamlit as st
from google import genai
from google.genai import types
import json
import os
import re
import time
import wave

# ============================================================
# otoXtra — Otomatik Reels Asistanı
# AŞAMA 1 GÜNCELLEMESİ:
#   1) Pixabay müzik indirme kaldırıldı -> AI artık gerçek şarkı adı önerir
#   2) Metin üretimi için otomatik model yedekleme listesi eklendi
#   3) Ses (TTS) üretimi için otomatik model yedekleme listesi eklendi
#   4) Üretim süreci kutusu eklendi (her adım yazılır, kaybolmaz)
#   5) Kapak başlıkları artık düz/temiz biçimde geliyor (markdown çöplüğü yok)
#   6) Açıklama / Başlıklar / Müzik önerisi kutularına yerleşik kopyalama
#      ikonu eklendi (st.code kutularının sağ üstünde otomatik çıkar)
#   7) Gereksiz "Alt Metin" alanı tamamen kaldırıldı
# ============================================================


# ------------------------------------------------------------
# MODEL LİSTELERİ (öncelik sırasına göre: en güçlü -> en garanti)
# Google modellerin isimlerini zaman zaman değiştiriyor / kapatıyor.
# Bu yüzden kod, listedeki bir model çalışmazsa otomatik olarak
# bir alttakine geçer. Hiçbiri çalışmazsa en sonda duran model
# (şu an kullandığımız, garanti çalışan) devreye girer.
# ------------------------------------------------------------
METIN_MODELLERI = [
    "gemini-3.1-pro-preview",   # şu an Google'ın en gelişmiş/en akıllı modeli
    "gemini-3.5-flash",        # yeni nesil, çok güçlü ve kararlı (stabil) model
    "gemini-3-flash-preview",  # yeni nesil hızlı model
    "gemini-2.5-pro",          # önceki üst seviye model
    "gemini-2.5-flash",        # ŞU AN KULLANDIĞIMIZ — garanti çalışan ana yedek
]

SES_MODELLERI = [
    "gemini-2.5-pro-preview-tts",    # en yüksek ses kalitesi (yeni, birincil)
    "gemini-2.5-flash-preview-tts",  # ŞU AN KULLANDIĞIMIZ — garanti çalışan yedek
]


# ------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ------------------------------------------------------------

def markdown_temizle(metin: str) -> str:
    """Metnin içinden ** veya __ gibi kalın yazı işaretlerini siler.
    Hashtag (#) işaretlerine dokunmaz, açıklamadaki etiketler bozulmaz."""
    if not isinstance(metin, str):
        return ""
    return re.sub(r"\*\*|__", "", metin).strip()


def kapak_basliklarini_formatla(liste) -> str:
    """AI'dan gelen kapak başlıkları listesini (ana/alt) numaralı,
    alt alta, tertemiz bir metne çevirir. Markdown işaretleri tamamen
    silinir, böylece kutuya yazarken **, (b) gibi çöp görünmez."""
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
    """Müzik önerisini (tarz + şarkı listesi) okunaklı bir metne çevirir."""
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
    """Listedeki modelleri sırayla dener. 503 (sunucu meşgul) hatasında aynı
    modeli bir kez daha dener; başka bir hatada (kota/izin/bulunamadı) hemen
    sıradaki modele geçer. Hiçbiri başarılı olamazsa son hatayı fırlatır."""
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
    """Listedeki ses (TTS) modellerini sırayla dener. Sesler (Puck, Kore vb.)
    Google'a ait olduğu için model değişse de aynı kalır."""
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


# ------------------------------------------------------------
# SAYFA AYARLARI
# ------------------------------------------------------------
st.set_page_config(page_title="otoXtra Asistanım", page_icon="🏎️", layout="wide")
st.title("🏎️ otoXtra — Otomatik Reels Asistanı")
st.write("Videonun konusunu, tahmini süresini ve varsa özel notunu yaz; otoXtra gerisini halletsin!")

# ------------------------------------------------------------
# UYGULAMA DURUMU (sayfa yenilenince sonuçlar ve günlük kaybolmasın)
# ------------------------------------------------------------
if "sonuc" not in st.session_state:
    st.session_state.sonuc = None
if "log_satirlari" not in st.session_state:
    st.session_state.log_satirlari = []

# ------------------------------------------------------------
# API ŞİFRESİ (artık sadece Gemini anahtarı gerekiyor; Pixabay kaldırıldı)
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

video_icerigi = st.text_area(
    "Videonun konusunu, tahmini süresini (örn: 30 sn) ve varsa özel isteklerini yaz:",
    height=150,
)

buton_tiklandi = st.button("🚀 otoXtra İçeriğini Üret!")

# ------------------------------------------------------------
# ÜRETİM SÜRECİ KUTUSU — her adım buraya yazılır, üretim bitince
# kaybolmaz, butonun hemen altında sabit kalır.
# ------------------------------------------------------------
log_kutusu = st.empty()


def gunlugu_ciz():
    if st.session_state.log_satirlari:
        log_kutusu.code("\n".join(st.session_state.log_satirlari), language=None)
    else:
        log_kutusu.empty()


def log_ekle(satir: str):
    st.session_state.log_satirlari.append(satir)
    gunlugu_ciz()


gunlugu_ciz()  # sayfa her yenilendiğinde son günlüğü göster

if buton_tiklandi:
    if not video_icerigi:
        st.warning("Lütfen videoda ne olduğunu yazın.")
        st.stop()

    st.session_state.log_satirlari = []
    log_ekle("🚀 Üretim başladı...")

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

        # 2. METİN ÜRETİMİ (model listesini otomatik sırayla dener)
        veri, kullanilan_metin_modeli = metin_uret(
            client, METIN_MODELLERI, video_icerigi, system_prompt, response_schema, log_ekle
        )

        # 3. SES ÜRETİMİ (model listesini otomatik sırayla dener)
        secilen_ses_ingilizce = ses_secimi.split(" ")[0]
        ses_dosyasi = "seslendirme.wav"
        ses_basarili, kullanilan_ses_modeli = ses_uret(
            client, SES_MODELLERI, veri["seslendirme_metni"], secilen_ses_ingilizce, ses_dosyasi, log_ekle
        )

        log_ekle("🎵 Müzik önerisi içerikle birlikte üretildi.")
        log_ekle("🏁 Tüm işlem tamamlandı.")

        # Sonucu state'e al (sayfa yeniden çizilse bile içerik kalsın)
        st.session_state.sonuc = {
            "veri": veri,
            "ses_basarili": ses_basarili,
            "ses_dosyasi": ses_dosyasi,
            "secilen_ses_ingilizce": secilen_ses_ingilizce,
            "kullanilan_metin_modeli": kullanilan_metin_modeli,
            "kullanilan_ses_modeli": kullanilan_ses_modeli,
        }

    except Exception as e:
        log_ekle(f"❌ Hata: {str(e)[:150]}")
        st.error("Sistemde bir hata oluştu ve işlem tamamlanamadı.")
        st.code(f"Hata Detayı: {str(e)}")

# ------------------------------------------------------------
# 4. SONUÇLARI GÖSTER (state varsa ekranda sabit kalsın)
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
