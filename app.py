import streamlit as st
import streamlit.components.v1 as components
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
from datetime import datetime

# ============================================================
# otoXtra — Otomatik Reels + Threads Asistanı
# ============================================================

# ------------------------------------------------------------
# PROMPT DOSYALARINI YÜKLEME YARDIMCILARI
# ------------------------------------------------------------
def prompt_dosyasini_oku(dosya_adi: str) -> str:
    try:
        with open(dosya_adi, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        st.error(f"⚠️ Prompt dosyası bulunamadı: '{dosya_adi}'!")
        st.stop()

def guncellik_talimati_uret() -> str:
    sablon = prompt_dosyasini_oku("guncellik_talimati.txt")
    return sablon.format(bugunun_tarihi=guncel_tarih_metni())

def video_analiz_promptunu_olustur(ek_notlar_bolumu: str) -> str:
    sablon = prompt_dosyasini_oku("video_analiz_promptu.txt")
    return sablon.format(
        ek_notlar_bolumu=ek_notlar_bolumu,
        guncellik_talimati=guncellik_talimati_uret()
    )

def sistem_talimati_olustur(sure_saniye: int) -> str:
    sablon = prompt_dosyasini_oku("sistem_talimati.txt")
    return sablon.format(
        sure_saniye=sure_saniye,
        guncellik_talimati=guncellik_talimati_uret()
    )

# ------------------------------------------------------------
# API KEY YAPILANDIRMASI
# ------------------------------------------------------------
try:
    API_KEYS = dict(st.secrets["GEMINI_KEYS"])
    if not API_KEYS:
        raise ValueError("API_KEYS boş")
except Exception as e:
    st.error(f"🔑 API anahtarları bulunamadı: {e}")
    st.stop()

# ------------------------------------------------------------
# COOLDOWN SÜRELERİ
# ------------------------------------------------------------
COOLDOWN_KOTA = 24 * 60 * 60
COOLDOWN_SUNUCU = 15 * 60
COOLDOWN_BULUNAMADI = 24 * 60 * 60
COOLDOWN_DIGER = 5 * 60
IP_BAN_KORUMA = 1.0

# ------------------------------------------------------------
# MODEL LİSTELERİ
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

MAX_INPUT_KARAKTER = 900_000

# ------------------------------------------------------------
# GÜNCELLİK DESTEĞİ
# ------------------------------------------------------------
GEMINI3_ARAMA_DESTEKLI_ONEK = "gemini-3"

TURKCE_AYLAR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}

def guncel_tarih_metni() -> str:
    simdi = datetime.now()
    return f"{simdi.day} {TURKCE_AYLAR[simdi.month]} {simdi.year}"

def model_arama_destekliyor_mu(model_adi: str) -> bool:
    return model_adi.startswith(GEMINI3_ARAMA_DESTEKLI_ONEK)

# ------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ------------------------------------------------------------
def markdown_temizle(metin: str) -> str:
    if not isinstance(metin, str):
        return ""
    return re.sub(r"[\*\_\`\[\]]+", "", metin).strip()

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
            satirlar.append(f"{i}) {ana}\n {alt}")
        else:
            satirlar.append(f"{i}) {ana}")
    return "\n\n".join(satirlar)

def guvenli_json_yukle(response_text: str):
    if not response_text:
        raise ValueError("Model boş yanıt döndürdü.")

    temiz = response_text.strip()
    try:
        return json.loads(temiz)
    except json.JSONDecodeError:
        temiz_md = re.sub(r"^\`\`\`json\s*|^\`\`\`\s*|\`\`\`\s*$", "", temiz, flags=re.IGNORECASE | re.MULTILINE).strip()
        try:
            return json.loads(temiz_md)
        except json.JSONDecodeError:
            pass

    start = temiz.find('{')
    end = temiz.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(temiz[start:end+1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"JSON parse edilemedi. Ham yanıt: {temiz[:200]}...")

# ------------------------------------------------------------
# SEKMEYİ AKTİF TUT (Safari arka plan koruması)
# ------------------------------------------------------------
def sekmeyi_aktif_tut():
    components.html("""
    <script>
    // Sessiz ses dosyası oluştur ve çal (tarayıcı uyanık kalsın)
    try {
        var audioContext = new (window.AudioContext || window.webkitAudioContext)();
        var oscillator = audioContext.createOscillator();
        var gainNode = audioContext.createGain();
        gainNode.gain.value = 0.001; // Neredeyse sessiz
        oscillator.connect(gainNode);
        gainNode.connect(audioContext.destination);
        oscillator.start(0);
        
        // Sayfa kapanana kadar çal
        window.addEventListener('beforeunload', function() {
            oscillator.stop();
        });
    } catch(e) {
        console.log("Audio context hatası:", e);
    }
    </script>
    """, height=0)

# ------------------------------------------------------------
# AKILLI ROUTER
# ------------------------------------------------------------
class SmartRouter:
    def __init__(self):
        if "blacklist" not in st.session_state:
            st.session_state.blacklist = {}

    def _is_banned(self, mail: str, model: str) -> bool:
        now = time.time()
        bl = st.session_state.blacklist

        model_key = f"*+{model}"
        if model_key in bl:
            if now < bl[model_key]:
                return True
            else:
                del bl[model_key]

        key_ban = f"{mail}+*"
        if key_ban in bl:
            if now < bl[key_ban]:
                return True
            else:
                del bl[key_ban]

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
            log_ekle(f" ❌ {model} MODEL bazlı hata → TÜM key'ler için {ban_sure} banlandı")
            self._ban(mail, model, cooldown, "model")
            time.sleep(IP_BAN_KORUMA)
            return "break_model"
        else:
            log_ekle(f" ⚠️ {mail} hatası → {model} ile {ban_sure} banlandı, diğer key deneniyor")
            self._ban(mail, model, cooldown, scope)
            time.sleep(IP_BAN_KORUMA)
            return "devam"

    def metin_uret(self, video_icerigi, system_prompt, response_schema, log_ekle, model_listesi=None, arama_kullan=True):
        if model_listesi is None:
            model_listesi = METIN_MODELLERI

        son_hata = None
        for model_adi in model_listesi:
            log_ekle(f"🧠 Model deneniyor: {model_adi}")
            model_denendi = False

            for mail, api_key in API_KEYS.items():
                if self._is_banned(mail, model_adi):
                    log_ekle(f" ⏸️ {mail} + {model_adi} banlı, atlanıyor")
                    continue

                model_denendi = True
                log_ekle(f" 🚀 {mail} ile {model_adi} deneniyor...")

                try:
                    client = genai.Client(api_key=api_key)

                    arama_bu_modelde_aktif = arama_kullan and model_arama_destekliyor_mu(model_adi)
                    config_parametreleri = dict(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    )
                    if arama_bu_modelde_aktif:
                        config_parametreleri["tools"] = [types.Tool(google_search=types.GoogleSearch())]
                        log_ekle(f" 🔎 {model_adi} için güncel bilgi araması aktif")

                    response = client.models.generate_content(
                        model=model_adi,
                        contents=video_icerigi,
                        config=types.GenerateContentConfig(**config_parametreleri),
                    )
                    veri = guvenli_json_yukle(getattr(response, "text", ""))
                    log_ekle(f" ✅ Başarılı → {mail} + {model_adi}")
                    time.sleep(IP_BAN_KORUMA)
                    return veri, f"{mail}+{model_adi}"

                except Exception as e:
                    son_hata = e
                    aksiyon = self._handle_hata(mail, model_adi, str(e), log_ekle)
                    if aksiyon == "break_model":
                        break

            if not model_denendi:
                log_ekle(f" ⏸️ {model_adi} tüm key'ler için banlı, atlanıyor")

        raise son_hata if son_hata else Exception("Tüm model+key kombinasyonları başarısız.")

    def ses_uret(self, metin, ses_adi, cikti_dosyasi, log_ekle):
        son_hata = None
        for model_adi in SES_MODELLERI:
            log_ekle(f"🎙️ Model deneniyor: {model_adi}")
            model_denendi = False

            for mail, api_key in API_KEYS.items():
                if self._is_banned(mail, model_adi):
                    log_ekle(f" ⏸️ {mail} + {model_adi} banlı, atlanıyor")
                    continue

                model_denendi = True
                log_ekle(f" 🚀 {mail} ile {model_adi} deneniyor...")

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

                    if isinstance(audio_data, str):
                        audio_data = base64.b64decode(audio_data)

                    with wave.open(cikti_dosyasi, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(24000)
                        wf.writeframes(audio_data)

                    log_ekle(f" ✅ Başarılı → {mail} + {model_adi}")
                    time.sleep(IP_BAN_KORUMA)
                    return True, f"{mail}+{model_adi}"

                except Exception as e:
                    son_hata = e
                    aksiyon = self._handle_hata(mail, model_adi, str(e), log_ekle)
                    if aksiyon == "break_model":
                        break

            if not model_denendi:
                log_ekle(f" ⏸️ {model_adi} tüm key'ler için banlı, atlanıyor")

        log_ekle("❌ Hiçbir ses modeli başarılı olamadı.")
        return False, None

    def video_analiz_et(self, video_bytes, mime_type, analiz_notlari, log_ekle):
        son_hata = None
        video_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)

        ek_notlar_bolumu = ""
        if analiz_notlari.strip():
            ek_notlar_bolumu = f"""
 ÖNEMLİ: Kullanıcı videoyu analiz ettirirken sana şu VİDEO ANALİZ NOTLARINI iletti.
 --- VİDEO ANALİZ NOTLARI ---
 {analiz_notlari}
 -------------------------------
 """

        analiz_promptu = video_analiz_promptunu_olustur(ek_notlar_bolumu)

        for model_adi in VIDEO_ANALIZ_MODELLERI:
            log_ekle(f"🔍 Model deneniyor: {model_adi}")
            model_denendi = False

            for mail, api_key in API_KEYS.items():
                if self._is_banned(mail, model_adi):
                    log_ekle(f" ⏸️ {mail} + {model_adi} banlı, atlanıyor")
                    continue

                model_denendi = True
                log_ekle(f" 🚀 {mail} ile {model_adi} deneniyor...")

                try:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=model_adi,
                        contents=[video_part, analiz_promptu],
                        config=types.GenerateContentConfig(
                            tools=[types.Tool(google_search=types.GoogleSearch())],
                        ),
                    )
                    log_ekle(f" ✅ Başarılı → {mail} + {model_adi}")
                    time.sleep(IP_BAN_KORUMA)
                    return getattr(response, "text", ""), f"{mail}+{model_adi}"

                except Exception as e:
                    son_hata = e
                    aksiyon = self._handle_hata(mail, model_adi, str(e), log_ekle)
                    if aksiyon == "break_model":
                        break

            if not model_denendi:
                log_ekle(f" ⏸️ {model_adi} tüm key'ler için banlı, atlanıyor")

        raise son_hata if son_hata else Exception("Hiçbir model videoyu analiz edemedi.")

# ------------------------------------------------------------
# SAYFA AYARLARI
# ------------------------------------------------------------
st.set_page_config(page_title="otoXtra", page_icon="🏎️", layout="wide")

# Mobile-first kompakt CSS
st.markdown("""
<style>
    /* Kompakt spacing */
    .main .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    .stTextArea textarea { font-size: 14px; }
    .stButton button { width: 100%; height: 48px; font-size: 16px; font-weight: 600; }
    
    /* Küçük caption'lar */
    [data-testid="stCaptionContainer"] { font-size: 12px; margin-bottom: 0.25rem; }
    
    /* Video preview daha küçük */
    [data-testid="stVideo"] video { max-height: 200px; }
    
    /* Expander daha kompakt */
    .streamlit-expanderHeader { font-size: 14px; }
</style>
""", unsafe_allow_html=True)

st.markdown("### 🏎️ otoXtra")
st.caption("Reels + Threads otomatik üretim")

if "sonuc" not in st.session_state:
    st.session_state.sonuc = None
if "log_satirlari" not in st.session_state:
    st.session_state.log_satirlari = []

router = SmartRouter()

# ------------------------------------------------------------
# SOL MENÜ
# ------------------------------------------------------------
with st.sidebar:
    st.markdown("**🎙️ Ses**")
    ses_secimi = st.selectbox("Seslendiren", [
        "Autonoe (Parlak - Kadın)", "Puck (Enerjik - Erkek)",
        "Aoede (Yumuşak - Kadın)", "Callirrhoe (Doğal - Kadın)",
        "Kore (Net - Kadın)", "Leda (Dinamik - Kadın)",
        "Zephyr (Parlak - Kadın)", "Charon (Bilgi - Erkek)",
        "Orus (Sert - Erkek)", "Iapetus (Akıcı - Erkek)", "Umbriel (Rahat - Erkek)"
    ], label_visibility="collapsed")

    st.markdown("**🔑 Key'ler**")
    for mail in API_KEYS.keys():
        st.caption(f"• {mail}")

    if st.session_state.blacklist:
        st.markdown("**🚫 Banlar**")
        now = time.time()
        aktif_ban = {k: v for k, v in st.session_state.blacklist.items() if v > now}
        if aktif_ban:
            for ban_key, bitis in aktif_ban.items():
                kalan = int(bitis - now)
                if kalan > 3600:
                    kalan_str = f"{kalan // 3600}s"
                else:
                    kalan_str = f"{kalan // 60}dk"
                st.caption(f"⛔ {ban_key} ({kalan_str})")
        else:
            st.caption("✅ Temiz")

# ------------------------------------------------------------
# ANA ARAYÜZ - KOMPAKT
# ------------------------------------------------------------
uploaded_video = st.file_uploader(
    "🎥 Referans Video",
    type=['mp4', 'mov', 'webm'],
    help="Yüklersen AI analiz eder, yüklemezsen aşağıya kendi analizini yazarsın"
)

video_buyuk = uploaded_video is not None and uploaded_video.size > 20 * 1024 * 1024

if uploaded_video is not None:
    st.video(uploaded_video)
    if video_buyuk:
        st.warning("⚠️ Video 20 MB üstü! Sıkıştırın.")

c1, c2 = st.columns(2)
with c1:
    video_analiz_notlari = st.text_area(
        "🔍 Analiz Notları",
        height=90,
        placeholder="Video varsa: 'Motor sesi bul'\nVideo yoksa: Kendi analizin",
    )
with c2:
    metin_uretim_notlari = st.text_area(
        "✍️ Üretim Notları",
        height=90,
        placeholder="'Fiyat söyleme'\n'Performans vurgula'",
    )

sure_saniye = st.number_input("⏱️ Hedef Süre (sn)", min_value=5, max_value=180, value=30, step=5)

buton_tiklandi = st.button("🚀 ÜRET!", disabled=video_buyuk, use_container_width=True)
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
                video_bytes, mime_type, video_analiz_notlari, log_ekle
            )
            log_ekle("🧠 Analiz tamamlandı, üretiliyor...")
        else:
            if not video_analiz_notlari.strip():
                st.warning("⚠️ Video yok, analiz notu yazın.")
                st.stop()
            analiz_metni = video_analiz_notlari.strip()
            log_ekle("📝 Manuel analiz kullanılıyor...")

        video_icerigi = (
            f"VİDEO ANALİZ SONUCU:\n{analiz_metni}\n\n"
            f"METİN ÜRETİM NOTLARI:\n"
            f"{metin_uretim_notlari.strip() if metin_uretim_notlari.strip() else 'Ek not yok.'}"
        )

        if len(video_icerigi) > MAX_INPUT_KARAKTER:
            kirpilmis = video_icerigi[:MAX_INPUT_KARAKTER]
            son_bosluk = kirpilmis.rfind(" ")
            son_nokta = kirpilmis.rfind(".")
            kesim_noktasi = max(son_bosluk, son_nokta)

            if kesim_noktasi > int(MAX_INPUT_KARAKTER * 0.9):
                video_icerigi = kirpilmis[:kesim_noktasi].strip()
            else:
                video_icerigi = kirpilmis.strip()

            log_ekle("⚠️ İçerik kısaltıldı.")

        BENIM_GEM_KURALLARIM = prompt_dosyasini_oku("kurallar.txt")
        system_prompt = BENIM_GEM_KURALLARIM + sistem_talimati_olustur(sure_saniye)

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

        veri, kullanilan_metin_modeli = router.metin_uret(
            video_icerigi, system_prompt, response_schema, log_ekle
        )

        log_ekle("🧵 Threads üretiliyor...")
        threads_icerigi = f"""INSTAGRAM AÇIKLAMASI:
{veri.get('reels_aciklamasi', '')}

GÖREV: Bu Instagram açıklamasını Threads ve X için daha sohbet havasında, kısa ve akıcı bir metne dönüştür.
"""
        threads_system_prompt = prompt_dosyasini_oku("threads_promptu.txt")
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
                arama_kullan=False,
            )
            veri["threads_aciklamasi"] = str(threads_veri.get("threads_aciklamasi", "")).strip()
        except Exception as threads_hata:
            log_ekle(f"⚠️ Threads fallback.")
            fallback = re.sub(r"#\w+", "", veri.get("reels_aciklamasi", "")).strip()
            fallback = re.sub(r"\s+", " ", fallback).strip()
            veri["threads_aciklamasi"] = fallback[:500].rstrip()
            kullanilan_threads_modeli = "fallback"

        secilen_ses_ingilizce = ses_secimi.split(" ")[0]
        ses_dosyasi = os.path.join(tempfile.gettempdir(), f"ses_{uuid.uuid4().hex[:8]}.wav")
        ses_basarili, kullanilan_ses_modeli = router.ses_uret(
            veri["seslendirme_metni"], secilen_ses_ingilizce, ses_dosyasi, log_ekle
        )

        log_ekle("🏁 Tamamlandı.")

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
        if "StopExecution" in str(type(e)) or "RerunException" in str(type(e)):
            raise

        hata_detay = traceback.format_exc()
        for api_key in API_KEYS.values():
            hata_detay = hata_detay.replace(api_key, "***")
        log_ekle("❌ HATA:")
        log_ekle(hata_detay)
        st.error("Hata oluştu. Logu kopyalayın.")

# ------------------------------------------------------------
# SONUÇLAR
# ------------------------------------------------------------
if st.session_state.sonuc:
    sekmeyi_aktif_tut()
    sonuc = st.session_state.sonuc
    veri = sonuc["veri"]
    ses_basarili = sonuc["ses_basarili"]
    ses_dosyasi = sonuc["ses_dosyasi"]
    secilen_ses_ingilizce = sonuc["secilen_ses_ingilizce"]
    kullanilan_metin_modeli = sonuc.get("kullanilan_metin_modeli", "?")
    kullanilan_ses_modeli = sonuc.get("kullanilan_ses_modeli", "?")
    kullanilan_threads_modeli = sonuc.get("kullanilan_threads_modeli", "?")

    st.success(f"✅ Başarılı! ({kullanilan_metin_modeli})")

    if st.button("🔄 Temizle", use_container_width=True):
        st.session_state.sonuc = None
        st.session_state.log_satirlari = []
        st.rerun()

    if ses_basarili and os.path.exists(ses_dosyasi):
        with open(ses_dosyasi, "rb") as f:
            ses_byte = f.read()
        st.audio(ses_byte, format="audio/wav")
        st.download_button(
            f"⬇️ Ses İndir",
            ses_byte, file_name="seslendirme.wav", mime="audio/wav",
            use_container_width=True
        )
        try:
            os.remove(ses_dosyasi)
        except Exception:
            pass

    st.markdown("---")
    
    # Tek kolon - dikey akış (mobile için)
    st.markdown("**📝 Reels Caption**")
    st.code(markdown_temizle(veri.get("reels_aciklamasi", "")), language=None)
    
    st.markdown("**🎭 Kapak Başlıkları**")
    st.code(kapak_basliklarini_formatla(veri.get("kapak_basliklari")), language=None)
    
    st.markdown("**🧵 Threads**")
    st.code(markdown_temizle(veri.get("threads_aciklamasi", "")), language=None)

    with st.expander("🎙️ Seslendirme Metni"):
        st.code(markdown_temizle(veri.get("seslendirme_metni", "")), language=None)

