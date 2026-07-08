import streamlit as st
from google import genai
from google.genai import types
import json
import os
import re
import time
import traceback
import wave
import tempfile
import uuid
import base64

# ============================================================
# otoXtra — Otomatik Reels + Threads Asistanı
# FİNAL VERSİYON: Tüm özellikler bir arada
#   - 3 API key akıllı yönetimi (SmartRouter)
#   - Video analizi (Türkiye odaklı viral strateji)
#   - Reels caption (katmanlı, hashtag'li)
#   - Threads caption (sohbet havasında, kısa)
#   - 5 kapak başlığı alternatifi
#   - Token limit, base64 decode, tempfile korumaları
# ============================================================

# ------------------------------------------------------------
# API KEY YAPILANDIRMASI (Streamlit Secrets'tan)
# ------------------------------------------------------------
try:
    API_KEYS = dict(st.secrets["GEMINI_KEYS"])
    if not API_KEYS:
        raise ValueError("API_KEYS boş")
except Exception as e:
    st.error(f"🔑 API anahtarları Streamlit Secrets'ta bulunamadı: {e}")
    st.stop()

# ------------------------------------------------------------
# COOLDOWN SÜRELERİ (saniye)
# ------------------------------------------------------------
COOLDOWN_KOTA = 24 * 60 * 60       # 24 saat (429 - o key o modeli kullanamaz)
COOLDOWN_SUNUCU = 15 * 60           # 15 dk (503 - o model herkes için çöktü)
COOLDOWN_BULUNAMADI = 24 * 60 * 60  # 24 saat (404 - o model yok)
COOLDOWN_DIGER = 5 * 60             # 5 dk (belirsiz hata)
IP_BAN_KORUMA = 1.0                 # 1 sn (istekler arası bekleme)

# ------------------------------------------------------------
# MODEL LİSTELERİ (Temmuz 2026 - Güncel)
# ------------------------------------------------------------
METIN_MODELLERI = [
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]

SES_MODELLERI = [
    "gemini-3.1-flash-tts",
    "gemini-2.5-flash-preview-tts",
]

VIDEO_ANALIZ_MODELLERI = [
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]

THREADS_MODELLERI = [
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]

MAX_INPUT_KARAKTER = 900_000  # Token limit aşımına karşı güvenli sınır

# ------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ------------------------------------------------------------
def markdown_temizle(metin: str) -> str:
    if not isinstance(metin, str):
        return ""
    return re.sub(r"[*_`]+", "", metin).strip()


def kapak_basliklarini_formatla(liste) -> str:
    if not isinstance(liste, list) or not liste:
        return markdown_temizle(str(liste)) if liste else "(Kapak başlığı üretilemedi.)"
    satirlar = []
    for i, secenek in enumerate(liste, start=1):
        if isinstance(secenek, dict):
            ana = markdown_temizle(str(secenek.get("ana", "")))
            alt = markdown_temizle(str(secenek.get("alt", "")))
        else:
            ana, alt = markdown_temizle(str(secenek)), ""
        if alt:
            satirlar.append(f"{i}) {ana}\n    {alt}")
        else:
            satirlar.append(f"{i}) {ana}")
    return "\n\n".join(satirlar)


def guvenli_json_yukle(response_text: str):
    """Gemini bazen markdown bloğu içinde JSON döndürür, bu onu temizler."""
    if not response_text:
        raise ValueError("Model boş yanıt döndürdü.")

    temiz = response_text.strip()
    try:
        return json.loads(temiz)
    except json.JSONDecodeError:
        # Önce standart markdown temizliğini dene (Satır bölünmesi engellendi)
        temiz_md = re.sub(r"^```json\s*|^```\s*|```\s*$", "", temiz, flags=re.IGNORECASE | re.MULTILINE).strip()
        try:
            return json.loads(temiz_md)
        except json.JSONDecodeError:
            pass

        # En sağlam yöntem: İçindeki ilk { ve son } bulup çıkarmak
        start = temiz.find('{')
        end = temiz.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(temiz[start:end+1])
            except json.JSONDecodeError:
                pass

        raise ValueError(f"JSON parse edilemedi. Ham yanıt: {temiz[:200]}...")


# ------------------------------------------------------------
# AKILLI ROUTER
# ------------------------------------------------------------
class SmartRouter:
    """
    3 katmanlı banlama mantığı:
      - 429 (kota)      → KEY+MODEL banlanır (24 saat)
      - 503 (sunucu)     → MODEL banlanır (15 dk, herkes için)
      - 404 (bulunamadı) → MODEL banlanır (24 saat, herkes için)
      - diğer            → KEY+MODEL banlanır (5 dk)
    """

    def __init__(self):
        if "blacklist" not in st.session_state:
            st.session_state.blacklist = {}

    def _is_banned(self, mail: str, model: str) -> bool:
        now = time.time()
        bl = st.session_state.blacklist

        # 1) MODEL banı (tüm key'ler için)
        model_key = f"*+{model}"
        if model_key in bl:
            if now < bl[model_key]:
                return True
            else:
                del bl[model_key]

        # 2) KEY banı (tüm modeller için)
        key_ban = f"{mail}+*"
        if key_ban in bl:
            if now < bl[key_ban]:
                return True
            else:
                del bl[key_ban]

        # 3) KEY+MODEL banı
        combo_key = f"{mail}+{model}"
        if combo_key in bl:
            if now < bl[combo_key]:
                return True
            else:
                del bl[combo_key]

        return False

    def _ban(self, mail: str, model: str, cooldown: int, scope: str):
        if scope == "model":
            st.session_state.blacklist[f"*+{model}"] = time.time() + cooldown
        elif scope == "key":
            st.session_state.blacklist[f"{mail}+*"] = time.time() + cooldown
        else:
            st.session_state.blacklist[f"{mail}+{model}"] = time.time() + cooldown

    def _parse_hata(self, hata_metni: str):
        h = hata_metni.lower()
        if "429" in hata_metni or "resource_exhausted" in h or "quota" in h:
            return "combo", COOLDOWN_KOTA
        if "503" in hata_metni or "unavailable" in h:
            return "model", COOLDOWN_SUNUCU
        if "404" in hata_metni or "not_found" in h:
            return "model", COOLDOWN_BULUNAMADI
        return "combo", COOLDOWN_DIGER

    def _handle_hata(self, mail, model, hata_metni, log_ekle):
        scope, cooldown = self._parse_hata(hata_metni)
        ban_sure = f"{cooldown // 60} dk" if cooldown < 3600 else f"{cooldown // 3600} saat"

        if scope == "model":
            log_ekle(f"   ❌ {model} MODEL bazlı hata → TÜM key'ler için {ban_sure} banlandı")
            self._ban(mail, model, cooldown, "model")
            time.sleep(IP_BAN_KORUMA)
            return "break_model"
        else:
            log_ekle(f"   ⚠️ {mail} hatası → {model} ile {ban_sure} banlandı, diğer key deneniyor")
            self._ban(mail, model, cooldown, scope)
            time.sleep(IP_BAN_KORUMA)
            return "devam"

    def metin_uret(self, video_icerigi, system_prompt, response_schema, log_ekle, model_listesi=None):
        """Metin üretimi. model_listesi verilmezse METIN_MODELLERI kullanılır."""
        if model_listesi is None:
            model_listesi = METIN_MODELLERI

        son_hata = None
        for model_adi in model_listesi:
            log_ekle(f"🧠 Model deneniyor: {model_adi}")
            model_denendi = False

            for mail, api_key in API_KEYS.items():
                if self._is_banned(mail, model_adi):
                    log_ekle(f"   ⏸️ {mail} + {model_adi} banlı, atlanıyor")
                    continue

                model_denendi = True
                log_ekle(f"   🚀 {mail} ile {model_adi} deneniyor...")

                try:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=model_adi,
                        contents=video_icerigi,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            response_mime_type="application/json",
                            response_schema=response_schema,
                        ),
                    )
                    veri = guvenli_json_yukle(getattr(response, "text", ""))
                    log_ekle(f"   ✅ Başarılı → {mail} + {model_adi}")
                    time.sleep(IP_BAN_KORUMA)
                    return veri, f"{mail}+{model_adi}"

                except Exception as e:
                    son_hata = e
                    aksiyon = self._handle_hata(mail, model_adi, str(e), log_ekle)
                    if aksiyon == "break_model":
                        break

            if not model_denendi:
                log_ekle(f"   ⏸️ {model_adi} tüm key'ler için banlı, atlanıyor")

        raise son_hata if son_hata else Exception("Tüm model+key kombinasyonları başarısız.")

    def ses_uret(self, metin, ses_adi, cikti_dosyasi, log_ekle):
        """Ses üretimi (base64 decode destekli)."""
        son_hata = None
        for model_adi in SES_MODELLERI:
            log_ekle(f"🎙️ Model deneniyor: {model_adi}")
            model_denendi = False

            for mail, api_key in API_KEYS.items():
                if self._is_banned(mail, model_adi):
                    log_ekle(f"   ⏸️ {mail} + {model_adi} banlı, atlanıyor")
                    continue

                model_denendi = True
                log_ekle(f"   🚀 {mail} ile {model_adi} deneniyor...")

                try:
                    client = genai.Client(api_key=api_key)
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

                    # Güvenli veri çıkarma
                    candidates = getattr(tts_response, "candidates", None)
                    if not candidates:
                        raise ValueError("TTS candidates bulunamadı")
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None) if content else None
                    if not parts:
                        raise ValueError("TTS parts bulunamadı")
                    inline_data = getattr(parts[0], "inline_data", None)
                    audio_data = getattr(inline_data, "data", None) if inline_data else None
                    if not audio_data:
                        raise ValueError("TTS audio verisi boş")

                    # Base64 decode (gerekirse)
                    if isinstance(audio_data, str):
                        audio_data = base64.b64decode(audio_data)

                    with wave.open(cikti_dosyasi, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(24000)
                        wf.writeframes(audio_data)

                    log_ekle(f"   ✅ Başarılı → {mail} + {model_adi}")
                    time.sleep(IP_BAN_KORUMA)
                    return True, f"{mail}+{model_adi}"

                except Exception as e:
                    son_hata = e
                    aksiyon = self._handle_hata(mail, model_adi, str(e), log_ekle)
                    if aksiyon == "break_model":
                        break

            if not model_denendi:
                log_ekle(f"   ⏸️ {model_adi} tüm key'ler için banlı, atlanıyor")

        log_ekle("❌ Hiçbir ses modeli başarılı olamadı.")
        return False, None

    def video_analiz_et(self, video_bytes, mime_type, kullanici_notlari, log_ekle):
        """Video analizi (Türkiye odaklı viral strateji)."""
        son_hata = None
        video_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)

        ek_notlar_bolumu = ""
        if kullanici_notlari.strip():
            ek_notlar_bolumu = f"""
            ÖNEMLİ: Kullanıcı videoyu analiz ettirirken sana şu EK İSTEKLERİ/ODAK NOKTALARINI iletti.
            --- KULLANICININ EK İSTEKLERİ ---
            {kullanici_notlari}
            -------------------------------
            """

        analiz_promptu = f"""Sen Türkiye'de sosyal medya (Instagram Reels, TikTok, YouTube Shorts, Threads) algoritmalarını ve Türk izleyicisinin psikolojisini avucunun içi gibi bilen, 'viral DNA' çıkaran uzman bir strateistsin.
Yüklediğim videoyu kare kare, sesiyle birlikte analiz et. Amacımız bu videodaki en çarpıcı detayları bulup Türkiye'de patlama yapacak bir kurgu stratejisi oluşturmak.

Bana şu başlıklarda çok net, maddeler halinde rapor ver:

1. VİRAL DETAYLAR & TÜRK İZLEYİCİSİ KANCASI: Türk izleyicisinin gözünü durduracak spesifik detaylar.
2. KURGU & HIZLANDIRMA STRATEJİSİ: En vurucu 3-4 görsel an ve seslendirme temposu notu.
3. HOOK (GİRİŞ KANCASI) ÖNERİSİ: İlk 3 saniyede kaydırmayı durduracak cümle.
4. KANIŞTIRICI KAPANIŞ (CTA/LOOP) ÖNERİSİ: Son 3 saniyede yorum tetikleyecek cümle.
{ek_notlar_bolumu}

Bu bilgileri ham veri olarak ver. Ekstra konuşma yapma."""

        for model_adi in VIDEO_ANALIZ_MODELLERI:
            log_ekle(f"🔍 Model deneniyor: {model_adi}")
            model_denendi = False

            for mail, api_key in API_KEYS.items():
                if self._is_banned(mail, model_adi):
                    log_ekle(f"   ⏸️ {mail} + {model_adi} banlı, atlanıyor")
                    continue

                model_denendi = True
                log_ekle(f"   🚀 {mail} ile {model_adi} deneniyor...")

                try:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=model_adi,
                        contents=[video_part, analiz_promptu],
                    )
                    log_ekle(f"   ✅ Başarılı → {mail} + {model_adi}")
                    time.sleep(IP_BAN_KORUMA)
                    return getattr(response, "text", ""), f"{mail}+{model_adi}"

                except Exception as e:
                    son_hata = e
                    aksiyon = self._handle_hata(mail, model_adi, str(e), log_ekle)
                    if aksiyon == "break_model":
                        break

            if not model_denendi:
                log_ekle(f"   ⏸️ {model_adi} tüm key'ler için banlı, atlanıyor")

        raise son_hata if son_hata else Exception("Hiçbir model videoyu analiz edemedi.")


# ------------------------------------------------------------
# SAYFA AYARLARI
# ------------------------------------------------------------
st.set_page_config(page_title="otoXtra Asistanım", page_icon="🏎️", layout="wide")

st.markdown(
    """
    <style>
    pre, code { white-space: pre-wrap !important; word-break: break-word !important; overflow-wrap: anywhere !important; }
    @media (max-width: 640px) {
        .block-container { padding-left: 0.9rem; padding-right: 0.9rem; padding-top: 1.2rem; }
        h2, h3 { font-size: 1.05rem !important; }
        .stButton button { width: 100%; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.subheader("🏎️ otoXtra — Otomatik Reels + Threads Asistanı")
st.caption("Viral referans videonuzu yükleyin veya konunuzu yazın; otoXtra Türk izleyicisi için gerisini halletsin!")

if "sonuc" not in st.session_state:
    st.session_state.sonuc = None
if "log_satirlari" not in st.session_state:
    st.session_state.log_satirlari = []

router = SmartRouter()

# ------------------------------------------------------------
# SOL MENÜ
# ------------------------------------------------------------
with st.sidebar:
    st.header("🎙️ Ses Ayarları")
    ses_secimi = st.selectbox("Seslendiren Seçimi", [
        "Autonoe (Parlak ve Canlı - Kadın)", "Puck (Eğlenceli ve Enerjik - Erkek)",
        "Aoede (Havadar ve Yumuşak - Kadın)", "Callirrhoe (Rahat ve Doğal - Kadın)",
        "Kore (Net ve Kendinden Emin - Kadın)", "Leda (Genç ve Dinamik - Kadın)",
        "Zephyr (Parlak - Kadın)", "Charon (Bilgilendirici - Erkek)",
        "Orus (Net ve Sert - Erkek)", "Iapetus (Temiz ve Akıcı - Erkek)", "Umbriel (Rahat - Erkek)"
    ])

    st.divider()
    st.header("🔑 API Key Havuzu")
    for mail in API_KEYS.keys():
        st.caption(f"• {mail}")

    if st.session_state.blacklist:
        st.divider()
        st.header("🚫 Aktif Banlar")
        now = time.time()
        aktif_ban = {k: v for k, v in st.session_state.blacklist.items() if v > now}
        if aktif_ban:
            for ban_key, bitis in aktif_ban.items():
                kalan = int(bitis - now)
                if kalan > 3600:
                    kalan_str = f"{kalan // 3600}s {kalan % 3600 // 60}dk"
                else:
                    kalan_str = f"{kalan // 60}dk"
                st.caption(f"⛔ {ban_key} ({kalan_str})")
        else:
            st.caption("✅ Ban yok")
    else:
        st.caption("✅ Ban yok")

# ------------------------------------------------------------
# ANA ARAYÜZ
# ------------------------------------------------------------
uploaded_video = st.file_uploader(
    "🎥 Viral Referans Videonu Yükle (Otomatik Analiz Edilsin)",
    type=['mp4', 'mov', 'webm'],
    help="Videoyu yüklersen, AI videoyu izleyip Türk izleyicisi için viral strateji kurar."
)

video_buyuk = uploaded_video is not None and uploaded_video.size > 20 * 1024 * 1024

if uploaded_video is not None:
    st.video(uploaded_video)
    if video_buyuk:
        st.warning("⚠️ Video 20 MB'tan büyük! Ücretsiz API limiti için lütfen videoyu sıkıştır (720p).")

konu_ve_istekler = st.text_area(
    "🎬 Videonun konusu ve özel istekler",
    height=150,
    placeholder="Paragraf 1: Videonun genel konusu\n\nParagraf 2: Özel istekler / odaklanılacak detaylar",
)

sc1, sc2 = st.columns([1, 3])
with sc1:
    sure_saniye = st.number_input("⏱️ Hedef Süre (saniye)", min_value=5, max_value=180, value=30, step=5)

buton_tiklandi = st.button("🚀 otoXtra İçeriğini Üret!", disabled=video_buyuk)
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

# ------------------------------------------------------------
# ÜRETİM
# ------------------------------------------------------------
if buton_tiklandi:
    st.session_state.log_satirlari = []
    log_ekle("🚀 Üretim başladı...")

    try:
        if uploaded_video is not None and uploaded_video.size > 20 * 1024 * 1024:
            log_ekle("❌ Video boyutu limit üzerinde.")
            st.error("Video 20 MB limitini aşıyor.")
            st.stop()

        if uploaded_video is not None:
            log_ekle("🎥 Video analiz ediliyor...")
            video_bytes = uploaded_video.getvalue()
            mime_type = uploaded_video.type or "video/mp4"

            analiz_metni, analiz_modeli = router.video_analiz_et(
                video_bytes, mime_type, konu_ve_istekler, log_ekle
            )
            log_ekle("🧠 Video analiz tamamlandı, içerik üretiliyor...")

            video_icerigi = (
                f"ANALİZ EDİLEN VİDEODAN ÇIKARILAN BİLGİLER:\n{analiz_metni}\n\n"
                f"KULLANICININ NOTLARI:\n"
                f"{konu_ve_istekler.strip() if konu_ve_istekler.strip() else 'Ek not yok.'}"
            )
        else:
            if not konu_ve_istekler.strip():
                st.warning("Lütfen konuyu yazın veya video yükleyin.")
                st.stop()
            video_icerigi = f"VİDEO KONUSU:\n{konu_ve_istekler.strip()}"

        # Token limit koruması (Kelime/Satır bütünlüğünü koruyarak kesme)
        if len(video_icerigi) > MAX_INPUT_KARAKTER:
            kirpilmis = video_icerigi[:MAX_INPUT_KARAKTER]
            # Kelimenin veya cümlenin ortasından kesilmesini engellemek için son boşluğu veya noktayı bul
            son_bosluk = kirpilmis.rfind(" ")
            son_nokta = kirpilmis.rfind(".")
            kesim_noktasi = max(son_bosluk, son_nokta)

            if kesim_noktasi > int(MAX_INPUT_KARAKTER * 0.9):
                video_icerigi = kirpilmis[:kesim_noktasi].strip()
            else:
                video_icerigi = kirpilmis.strip()

            log_ekle("⚠️ İçerik güvenli sınıra kısaltıldı (kelime bütünlüğü korundu).")

        # Kuralları oku
        try:
            with open("kurallar.txt", "r", encoding="utf-8") as f:
                BENIM_GEM_KURALLARIM = f.read()
        except FileNotFoundError:
            st.error("⚠️ 'kurallar.txt' bulunamadı!")
            st.stop()

        system_prompt = BENIM_GEM_KURALLARIM + f"""

ÖNEMLİ SİSTEM TALİMATI (otoXtra Uygulaması):

1. SÜRE VE KURGU MANTIĞI:
HEDEF SÜRE: {sure_saniye} saniye.
Orijinal video daha uzun olsa bile BU SÜREYİ IGNORE ET. Kullanıcı videoyu kurguda {sure_saniye} saniyeye getirecek.
Seslendirme metnini TAM OLARAK {sure_saniye} saniyeye uygun uzunlukta yaz.

2. ÇIKTI FORMATI:
- seslendirme_metni: {sure_saniye} saniyeye tam uyan, 4 vuruş yapısına uygun, düz metin. Markdown KULLANMA.
- reels_aciklamasi: Katmanlı Instagram açıklaması + en sonda 5 hashtag. Markdown KULLANMA.
- kapak_basliklari: 5 farklı kapak başlığı. "ana" (TAMAMI BÜYÜK HARF) ve "alt" alanları. Markdown KULLANMA.

alt_metin alanı İSTENMİYOR.
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
                        "properties": {"ana": {"type": "STRING"}, "alt": {"type": "STRING"}},
                        "required": ["ana", "alt"],
                    },
                },
            },
            "required": ["seslendirme_metni", "reels_aciklamasi", "kapak_basliklari"],
        }

        # Ana metin üretimi
        veri, kullanilan_metin_modeli = router.metin_uret(
            video_icerigi, system_prompt, response_schema, log_ekle
        )

        # Threads üretimi (ayrı çağrı)
        log_ekle("🧵 Threads için ayrı açıklama üretiliyor...")
        threads_icerigi = f"""INSTAGRAM AÇIKLAMASI:
{veri.get('reels_aciklamasi', '')}

GÖREV: Bu Instagram açıklamasını Threads ve X için daha sohbet havasında, kısa ve akıcı bir metne dönüştür.
"""
        threads_system_prompt = """Sadece JSON üret.
Kurallar:
- Çıktı 500 karakteri geçmesin.
- Hashtag kullanma.
- Konu ve ana mesaj korunsun, dil daha sohbet odaklı olsun.
- Emoji zorunlu değil; uygunsa az ve doğal kullan.
"""
        threads_schema = {
            "type": "OBJECT",
            "properties": {
                "threads_aciklamasi": {"type": "STRING"},
            },
            "required": ["threads_aciklamasi"],
        }

        try:
            threads_veri, kullanilan_threads_modeli = router.metin_uret(
                threads_icerigi,
                threads_system_prompt,
                threads_schema,
                log_ekle,
                model_listesi=THREADS_MODELLERI,
            )
            veri["threads_aciklamasi"] = str(threads_veri.get("threads_aciklamasi", "")).strip()
        except Exception as threads_hata:
            log_ekle(f"⚠️ Threads ayrı üretilemedi ({str(threads_hata)[:100]}). Fallback hazırlanıyor.")
            # Sadece hashtag kelimelerini sil, tüm satırı yok et
            fallback = re.sub(r"#\w+", "", veri.get("reels_aciklamasi", "")).strip()
            # Kalan fazla boşlukları temizle
            fallback = re.sub(r"\s+", " ", fallback).strip()
            veri["threads_aciklamasi"] = fallback[:500].rstrip()
            kullanilan_threads_modeli = "fallback"

        # Ses üretimi (benzersiz dosya adı)
        secilen_ses_ingilizce = ses_secimi.split(" ")[0]
        ses_dosyasi = os.path.join(tempfile.gettempdir(), f"ses_{uuid.uuid4().hex[:8]}.wav")
        ses_basarili, kullanilan_ses_modeli = router.ses_uret(
            veri["seslendirme_metni"], secilen_ses_ingilizce, ses_dosyasi, log_ekle
        )

        log_ekle("🏁 Tüm işlem tamamlandı.")

        st.session_state.sonuc = {
            "veri": veri,
            "ses_basarili": ses_basarili,
            "ses_dosyasi": ses_dosyasi,
            "secilen_ses_ingilizce": secilen_ses_ingilizce,
            "kullanilan_metin_modeli": kullanilan_metin_modeli,
            "kullanilan_ses_modeli": kullanilan_ses_modeli,
            "kullanilan_threads_modeli": kullanilan_threads_modeli,
        }

    except Exception as e:
        # Streamlit'in kendi akış kontrol exception'larını (st.stop, st.rerun) yakalama, direkt yukarı fırlat
        if "StopExecution" in str(type(e)) or "RerunException" in str(type(e)):
            raise

        hata_detay = traceback.format_exc()
        # API key'leri maskele
        for api_key in API_KEYS.values():
            hata_detay = hata_detay.replace(api_key, "***")
        log_ekle("❌ HATA OLUŞTU:")
        log_ekle(hata_detay)
        st.error("Sistemde hata oluştu. Log kutusunu kopyalayıp gönder.")


# ------------------------------------------------------------
# SONUÇLARI GÖSTER
# ------------------------------------------------------------
if st.session_state.sonuc:
    sonuc = st.session_state.sonuc
    veri = sonuc["veri"]
    ses_basarili = sonuc["ses_basarili"]
    ses_dosyasi = sonuc["ses_dosyasi"]
    secilen_ses_ingilizce = sonuc["secilen_ses_ingilizce"]
    kullanilan_metin_modeli = sonuc.get("kullanilan_metin_modeli", "?")
    kullanilan_ses_modeli = sonuc.get("kullanilan_ses_modeli", "?")
    kullanilan_threads_modeli = sonuc.get("kullanilan_threads_modeli", "?")

    st.success(f"✅ otoXtra Başarıyla Üretti! (Metin: {kullanilan_metin_modeli})")

    c1, c2 = st.columns([3, 1])
    with c2:
        if st.button("🔄 Yeniden Sorgu (Temizle)"):
            st.session_state.sonuc = None
            st.session_state.log_satirlari = []
            st.rerun()

    st.markdown("### 🎧 Medya")
    st.markdown(f"**🎙️ Seslendirme** (model: {kullanilan_ses_modeli})")
    if ses_basarili and os.path.exists(ses_dosyasi):
        with open(ses_dosyasi, "rb") as f:
            ses_byte = f.read()
        st.audio(ses_byte, format="audio/wav")
        st.download_button(
            f"⬇️ {secilen_ses_ingilizce} Sesini İndir (.wav)",
            ses_byte, file_name="seslendirme.wav", mime="audio/wav",
        )
        # Dosya okunup belleğe alındı, diskte birikmemesi için silelim
        try:
            os.remove(ses_dosyasi)
        except Exception:
            pass
    else:
        st.warning("Ses dosyası bulunamadı.")

    st.divider()
    st.markdown("### 📝 Metin İçerikleri")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("1️⃣ Reels Açıklaması")
        st.caption("Katmanlı caption + 5 hashtag")
        st.code(markdown_temizle(veri.get("reels_aciklamasi", "")), language=None)

    with col2:
        st.subheader("2️⃣ Kapak Başlıkları")
        st.caption("5 alternatif")
        st.code(kapak_basliklarini_formatla(veri.get("kapak_basliklari")), language=None)

    with col3:
        st.subheader("3️⃣ Threads Açıklaması")
        st.caption(f"Kısa, sohbet havasında, hashtagsiz (Model: {kullanilan_threads_modeli})")
        st.code(markdown_temizle(veri.get("threads_aciklamasi", "")), language=None)

    with st.expander("🎙️ Seslendirme Metni (kontrol için)"):
        st.code(markdown_temizle(veri.get("seslendirme_metni", "")), language=None)
