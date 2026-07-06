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
# otoXtra — Otomatik Reels Asistanı
# ============================================================

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

VIDEO_ANALIZ_MODELLERI = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

THREADS_MODELLERI = [
    "gemini-2.5-pro",      # ← DÜZELTME: Fallback şansını artırmak için eklendi
    "gemini-2.5-flash",
    "gemini-1.5-flash",
]

MODEL_DURUM_DOSYASI = "model_durumlari.json"
MODEL_BEKLEME_SURESI_SN = 24 * 60 * 60
HATA_SAKLAMA_SURESI_SN = 7 * 24 * 60 * 60
MAX_INPUT_KARAKTER = 900_000  # ← DÜZELTME: Token limit aşımına karşı güvenli sınır


def markdown_temizle(metin: str) -> str:
    if not isinstance(metin, str):
        return ""
    return re.sub(r"[*_`]+", "", metin).strip()


def _simdi_ts() -> int:
    return int(time.time())


def model_durumlarini_yukle() -> dict:
    temel = {"son_basari": {}, "hatalar": []}
    if not os.path.exists(MODEL_DURUM_DOSYASI):
        return temel

    try:
        with open(MODEL_DURUM_DOSYASI, "r", encoding="utf-8") as f:
            veri = json.load(f)
        if not isinstance(veri, dict):
            return temel
        veri.setdefault("son_basari", {})
        veri.setdefault("hatalar", [])
        return veri
    except Exception:
        return temel


def model_durumlarini_kaydet(veri: dict):
    try:
        with open(MODEL_DURUM_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def model_durumlarini_temizle(veri: dict):
    simdi = _simdi_ts()
    temiz_hatalar = []
    for kayit in veri.get("hatalar", []):
        zaman = int(kayit.get("zaman", 0))
        if simdi - zaman <= HATA_SAKLAMA_SURESI_SN:
            temiz_hatalar.append(kayit)
    veri["hatalar"] = temiz_hatalar


def model_hata_ekle(veri: dict, kategori: str, model: str, hata_metni: str):
    veri.setdefault("hatalar", []).append(
        {
            "kategori": kategori,
            "model": model,
            "hata": hata_metni[:240],
            "zaman": _simdi_ts(),
        }
    )


def model_basari_isle(veri: dict, kategori: str, model: str):
    veri.setdefault("son_basari", {})[kategori] = {"model": model, "zaman": _simdi_ts()}


def model_karantinada_mi(veri: dict, kategori: str, model: str) -> bool:
    simdi = _simdi_ts()
    en_son_hata = 0
    for kayit in veri.get("hatalar", []):
        if kayit.get("kategori") == kategori and kayit.get("model") == model:
            en_son_hata = max(en_son_hata, int(kayit.get("zaman", 0)))
    if en_son_hata == 0:
        return False
    return simdi - en_son_hata < MODEL_BEKLEME_SURESI_SN


def modelleri_sirala_ve_filtrele(model_listesi, kategori: str, model_durumlari: dict):
    son = model_durumlari.get("son_basari", {}).get(kategori, {})
    son_model = son.get("model") if isinstance(son, dict) else None

    sirali = []
    if son_model in model_listesi and not model_karantinada_mi(model_durumlari, kategori, son_model):
        sirali.append(son_model)

    for model in model_listesi:
        if model in sirali:
            continue
        if model_karantinada_mi(model_durumlari, kategori, model):
            continue
        sirali.append(model)

    return sirali


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


def gecici_hata_mi(hata_metni: str) -> bool:
    return any(kod in hata_metni for kod in ("429", "500", "502", "503", "504"))


def guvenli_json_yukle(response_text: str):
    if not response_text:
        raise ValueError("Model boş yanıt döndürdü.")

    temiz = response_text.strip()
    try:
        return json.loads(temiz)
    except json.JSONDecodeError:
        temiz = re.sub(r"^```json\s*|^```|```$", "", temiz, flags=re.IGNORECASE | re.MULTILINE).strip()
        return json.loads(temiz)


def metin_uret(
    client,
    model_listesi,
    video_icerigi,
    system_prompt,
    response_schema,
    log_ekle,
    deneme_sayisi=2,
    kategori="metin",
):
    son_hata = None
    model_durumlari = model_durumlarini_yukle()
    model_durumlarini_temizle(model_durumlari)
    model_durumlarini_kaydet(model_durumlari)

    aday_modeller = modelleri_sirala_ve_filtrele(model_listesi, kategori, model_durumlari)
    if not aday_modeller:
        raise Exception(f"{kategori} için 24 saat karantinada olmayan model yok. Süre dolunca tekrar dene.")

    for model_adi in aday_modeller:
        log_ekle(f"🧠 Metin üretimi deneniyor: {model_adi}")
        for deneme in range(deneme_sayisi):
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
                veri = guvenli_json_yukle(getattr(response, "text", ""))
                model_basari_isle(model_durumlari, kategori, model_adi)
                model_durumlarini_temizle(model_durumlari)
                model_durumlarini_kaydet(model_durumlari)
                log_ekle(f"✅ İçerik üretildi → kullanılan model: {model_adi}")
                return veri, model_adi
            except Exception as e:
                son_hata = e
                hata_metni = str(e)
                model_hata_ekle(model_durumlari, kategori, model_adi, hata_metni)
                model_durumlarini_temizle(model_durumlari)
                model_durumlarini_kaydet(model_durumlari)
                if gecici_hata_mi(hata_metni) and deneme < deneme_sayisi - 1:
                    log_ekle(f"⏳ {model_adi} geçici hata verdi. 3 sn sonra tekrar denenecek...")
                    time.sleep(3)
                    continue
                log_ekle(f"⚠️ {model_adi} kullanılamadı ({hata_metni[:120]}...) → sıradaki modele geçiliyor")
                break
    raise son_hata if son_hata else Exception("Hiçbir model içerik üretemedi.")


def ses_uret(client, model_listesi, metin, ses_adi, cikti_dosyasi, log_ekle, kategori="ses"):
    son_hata = None
    model_durumlari = model_durumlarini_yukle()
    model_durumlarini_temizle(model_durumlari)
    model_durumlarini_kaydet(model_durumlari)

    aday_modeller = modelleri_sirala_ve_filtrele(model_listesi, kategori, model_durumlari)
    if not aday_modeller:
        log_ekle("❌ Ses için 24 saat karantinada olmayan model yok.")
        return False, None

    for model_adi in aday_modeller:
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

                candidates = getattr(tts_response, "candidates", None)
                if not candidates:
                    raise ValueError("TTS yanıtında candidates bulunamadı.")

                content = getattr(candidates[0], "content", None)
                parts = getattr(content, "parts", None) if content else None
                if not parts:
                    raise ValueError("TTS yanıtında audio part bulunamadı.")

                inline_data = getattr(parts[0], "inline_data", None)
                audio_data = getattr(inline_data, "data", None) if inline_data else None
                if not audio_data:
                    raise ValueError("TTS audio verisi boş döndü.")

                if isinstance(audio_data, str):  # ← DÜZELTME: Base64 encoded gelirse decode et
                    audio_data = base64.b64decode(audio_data)

                with wave.open(cikti_dosyasi, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(audio_data)

                model_basari_isle(model_durumlari, kategori, model_adi)
                model_durumlarini_temizle(model_durumlari)
                model_durumlarini_kaydet(model_durumlari)
                log_ekle(f"✅ Ses üretildi → kullanılan model: {model_adi}")
                return True, model_adi
            except Exception as e:
                son_hata = e
                hata_metni = str(e)
                model_hata_ekle(model_durumlari, kategori, model_adi, hata_metni)
                model_durumlarini_temizle(model_durumlari)
                model_durumlarini_kaydet(model_durumlari)
                if gecici_hata_mi(hata_metni) and deneme == 0:
                    log_ekle(f"⏳ {model_adi} geçici hata verdi. 4 sn sonra tekrar denenecek...")
                    time.sleep(4)
                    continue
                log_ekle(f"⚠️ {model_adi} ile ses üretilemedi ({hata_metni[:120]}) → sıradaki modele geçiliyor")
                break
    log_ekle("❌ Hiçbir ses modeli başarılı olamadı.")
    return False, None


def video_analiz_et(client, model_listesi, video_bytes, mime_type, kullanici_notlari, log_ekle, kategori="video_analiz"):
    son_hata = None
    video_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)

    model_durumlari = model_durumlarini_yukle()
    model_durumlarini_temizle(model_durumlari)
    model_durumlarini_kaydet(model_durumlari)

    ek_notlar_bolumu = ""
    if kullanici_notlari.strip():
        ek_notlar_bolumu = f"""
        ÖNEMLİ: Kullanıcı videoyu analiz ettirirken sana şu EK İSTEKLERİ/ODAK NOKTALARINI iletti.
        Analizini yaparken bu istekleri MUTLAKA dikkate al ve videodaki ilgili detayları bu istekler ışığında değerlendir:
        --- KULLANICININ EK İSTEKLERİ ---
        {kullanici_notlari}
        -------------------------------
        """

    analiz_promptu = f"""Sen Türkiye'de sosyal medya (Instagram Reels, TikTok, YouTube Shorts) algoritmalarını ve Türk izleyicisinin psikolojisini avucunun içi gibi bilen, 'viral DNA' çıkaran uzman bir strateistsin.
Yüklediğim videoyu kare kare, sesiyle birlikte analiz et. Amacımız bu videodaki en çarpıcı detayları bulup Türkiye'de patlama yapacak bir kurgu stratejisi oluşturmak.

Bana şu başlıklarda çok net, maddeler halinde rapor ver:

1. VİRAL DETAYLAR & TÜRK İZLEYİCİSİ KANCASI: Videoda Türk izleyicisinin gözünü durduracak, merak uyandıracak veya tartışma yaratacak spesifik detaylar neler?
2. KURGU & HIZLANDIRMA STRATEJİSİ: Videonun en vurucu 3-4 görsel anını belirt ve seslendirme temposu notu düş.
3. HOOK (GİRİŞ KANCASI) ÖNERİSİ: İlk 3 saniyede kaydırmayı durduracak spesifik cümleyi öner.
4. KAPIŞIŞ (CTA/LOOP) ÖNERİSİ: Son 3 saniyede yorum tetikleyecek veya tekrar izletecek kritik cümleyi öner.
{ek_notlar_bolumu}

Bu bilgileri, bir sonraki adımda benim 'kurallar.txt' dosyamdaki formata göre seslendirme metni üretmen için bana ham veri olarak ver. Doğrudan analiz sonucunu yaz, ekstra konuşma yapma."""

    aday_modeller = modelleri_sirala_ve_filtrele(model_listesi, kategori, model_durumlari)
    if not aday_modeller:
        raise Exception("Video analiz için 24 saat karantinada olmayan model yok. Süre dolunca tekrar dene.")

    for model_adi in aday_modeller:
        log_ekle(f"🔍 Video analizi deneniyor: {model_adi}")
        for deneme in range(2):
            try:
                response = client.models.generate_content(
                    model=model_adi,
                    contents=[video_part, analiz_promptu],
                )
                model_basari_isle(model_durumlari, kategori, model_adi)
                model_durumlarini_temizle(model_durumlari)
                model_durumlarini_kaydet(model_durumlari)
                log_ekle(f"✅ Video analiz edildi → kullanılan model: {model_adi}")
                return getattr(response, "text", ""), model_adi
            except Exception as e:
                son_hata = e
                hata_metni = str(e)
                model_hata_ekle(model_durumlari, kategori, model_adi, hata_metni)
                model_durumlarini_temizle(model_durumlari)
                model_durumlarini_kaydet(model_durumlari)
                if gecici_hata_mi(hata_metni) and deneme == 0:
                    log_ekle(f"⏳ {model_adi} geçici hata verdi. 3 sn sonra tekrar denenecek...")
                    time.sleep(3)
                    continue
                log_ekle(f"⚠️ {model_adi} kullanılamadı ({hata_metni[:120]}) → sıradaki modele geçiliyor")
                break
    raise son_hata if son_hata else Exception("Hiçbir model videoyu analiz edemedi.")


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

st.subheader("🏎️ otoXtra — Otomatik Reels Asistanı")
st.caption("Viral referans videonuzu yükleyin veya konunuzu yazın; otoXtra Türk izleyicisi için gerisini halletsin!")

if "sonuc" not in st.session_state:
    st.session_state.sonuc = None
if "log_satirlari" not in st.session_state:
    st.session_state.log_satirlari = []

try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    st.error("🔑 GEMINI_API_KEY bulunamadı!")
    st.stop()

with st.sidebar:
    st.header("🎙️ Ses Ayarları")
    ses_secimi = st.selectbox("Seslendiren Seçimi", [
        "Autonoe (Parlak ve Canlı - Kadın)", "Puck (Eğlenceli ve Enerjik - Erkek)",
        "Aoede (Havadar ve Yumuşak - Kadın)", "Callirrhoe (Rahat ve Doğal - Kadın)",
        "Kore (Net ve Kendinden Emin - Kadın)", "Leda (Genç ve Dinamik - Kadın)",
        "Zephyr (Parlak - Kadın)", "Charon (Bilgilendirici - Erkek)",
        "Orus (Net ve Sert - Erkek)", "Iapetus (Temiz ve Akıcı - Erkek)", "Umbriel (Rahat - Erkek)"
    ])

uploaded_video = st.file_uploader(
    "🎥 Viral Referans Videonu Yükle (Otomatik Analiz Edilsin)",
    type=['mp4', 'mov', 'webm'],
    help="Videoyu yüklersen, AI videoyu izleyip Türk izleyicisi için viral strateji kurar."
)

# ← DÜZELTME: Video boyutu kontrolü tek noktada, buton disable ediliyor
video_buyuk = uploaded_video is not None and uploaded_video.size > 20 * 1024 * 1024

if uploaded_video is not None:
    st.video(uploaded_video)
    if video_buyuk:
        st.warning("⚠️ Video 20 MB'tan büyük! Ücretsiz API limiti için lütfen videoyu sıkıştır (720p).")

konu_ve_istekler = st.text_area(
    "🎬 Videonun konusu ve özel istekler",
    height=150,
    placeholder="Paragraf 1: Videonun genel konusu\n\nParagraf 2: Özel istekler",
)

sc1, sc2 = st.columns([1, 3])
with sc1:
    sure_saniye = st.number_input("⏱️ Hedef Süre (saniye)", min_value=5, max_value=180, value=30, step=5)

buton_tiklandi = st.button("🚀 otoXtra İçeriğini Üret!", disabled=video_buyuk)  # ← DÜZELTME
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

if buton_tiklandi:
    st.session_state.log_satirlari = []
    log_ekle("🚀 Üretim başladı...")

    try:
        client = genai.Client(api_key=gemini_key)

        if uploaded_video is not None and uploaded_video.size > 20 * 1024 * 1024:
            log_ekle("❌ Video boyutu limit üzerinde olduğu için analiz başlatılamadı.")
            st.error("Video 20 MB limitini aşıyor. Lütfen videoyu sıkıştırıp tekrar deneyin.")
            st.stop()

        if uploaded_video is not None:
            log_ekle("🎥 Yüklenen video Gemini tarafından izlenip analiz ediliyor...")
            video_bytes = uploaded_video.getvalue()
            mime_type = uploaded_video.type or "video/mp4"

            analiz_metni, _ = video_analiz_et(
                client, VIDEO_ANALIZ_MODELLERI, video_bytes, mime_type, konu_ve_istekler, log_ekle
            )
            log_ekle("🧠 Video analiz tamamlandı, kurallara göre içerik üretiliyor...")

            video_icerigi = (
                f"ANALİZ EDİLEN VİDEODAN ÇIKARILAN BİLGİLER VE STRATEJİ:\n{analiz_metni}\n\n"
                f"KULLANICININ GİRDİĞİ TEMEL KONU / NOTLAR:\n"
                f"{konu_ve_istekler.strip() if konu_ve_istekler.strip() else 'Kullanıcı ek not düşmedi, sadece analizdeki viral detayları kullan.'}"
            )
        else:
            if not konu_ve_istekler.strip():
                st.warning("Lütfen videonun konusunu yazın veya bir referans video yükleyin.")
                st.stop()
            video_icerigi = f"VİDEO KONUSU VE ÖZEL İSTEKLER:\n{konu_ve_istekler.strip()}"

        # ← DÜZELTME: Token limit aşımına karşı içerik kısaltma
        if len(video_icerigi) > MAX_INPUT_KARAKTER:
            video_icerigi = video_icerigi[:MAX_INPUT_KARAKTER]
            log_ekle("⚠️ İçerik çok uzun, güvenli sınıra kısaltıldı.")

        try:
            with open("kurallar.txt", "r", encoding="utf-8") as f:
                BENIM_GEM_KURALLARIM = f.read()
        except FileNotFoundError:
            st.error("⚠️ 'kurallar.txt' dosyası bulunamadı!")
            st.stop()

        system_prompt = BENIM_GEM_KURALLARIM + f"""

ÖNEMLİ SİSTEM TALİMATI (otoXtra Uygulaması - ÇOK KRİTİK KURALLAR):

1. SÜRE VE KURGU MANTIĞI (ÇOK ÖNEMLİ):
Kullanıcının aşağıda belirttiği HEDEF SÜRE: {sure_saniye} saniye.
Orijinal referans video daha uzun veya kısa olsa bile BU SÜREYİ IGNORE ET.
Kullanıcı videoyu kurguda hızlandırıp/kırparak tam olarak {sure_saniye} saniyeye getirecektir.
Sen seslendirme metnini, kurallar.txt'teki kelime sayısı formülünü kullanarak TAM OLARAK {sure_saniye} saniyeye uygun uzunlukta yaz.

2. ÇIKTI FORMATI:
Yukarıdaki otoXtra kurallarına GÖRE üretim yap. NİHAİ ÇIKTIYI sadece aşağıdaki JSON alanlarına göre ver:

- seslendirme_metni: {sure_saniye} saniyeye tam uyan, 4 vuruş yapısına uygun, TTS motoruna gidecek düz metin. Markdown KULLANMA.
- reels_aciklamasi: Katmanlı Instagram açıklaması + en sonda 5 hashtag. Emoji zorunlu değil; sadece konuya uygunsa ve okunurluğu artırıyorsa az ve doğal kullan. Markdown KULLANMA.
- kapak_basliklari: 5 farklı kapak başlığı. "ana" (TAMAMI BÜYÜK HARF) ve "alt" alanları. Markdown KULLANMA.

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
                        "properties": {"ana": {"type": "STRING"}, "alt": {"type": "STRING"}},
                        "required": ["ana", "alt"],
                    },
                },
            },
            "required": ["seslendirme_metni", "reels_aciklamasi", "kapak_basliklari"],
        }

        veri, kullanilan_metin_modeli = metin_uret(
            client, METIN_MODELLERI, video_icerigi, system_prompt, response_schema, log_ekle, kategori="metin"
        )

        log_ekle("🧵 Threads/X için ayrı açıklama üretiliyor...")
        threads_icerigi = f"""INSTAGRAM AÇIKLAMASI:
{veri.get('reels_aciklamasi', '')}

GÖREV:
Bu Instagram açıklamasını Threads ve X benzeri platformlar için daha sohbet eden, daha direkt ve akıcı bir metne dönüştür.
"""
        threads_system_prompt = """
Sadece JSON üret.

Kurallar:
- Çıktı 500 karakteri geçmesin.
- Hashtag kullanma.
- Konu ve ana mesaj korunsun, dil daha sohbet odaklı olsun.
- Emoji zorunlu değil; sadece uygunsa doğal ve az kullan.
- Tek parça, okunabilir bir metin ver.
"""
        threads_schema = {
            "type": "OBJECT",
            "properties": {
                "threads_aciklamasi": {"type": "STRING"},
            },
            "required": ["threads_aciklamasi"],
        }

        try:
            threads_veri, kullanilan_threads_modeli = metin_uret(
                client,
                THREADS_MODELLERI,
                threads_icerigi,
                threads_system_prompt,
                threads_schema,
                log_ekle,
                deneme_sayisi=1,
                kategori="threads",
            )
            veri["threads_aciklamasi"] = str(threads_veri.get("threads_aciklamasi", "")).strip()
        except Exception as threads_hata:
            log_ekle(f"⚠️ Threads metni ayrı üretilemedi ({str(threads_hata)[:100]}...). Kısa fallback hazırlanıyor.")
            fallback_threads = re.sub(r"(?m)^#.*$", "", veri.get("reels_aciklamasi", "")).strip()
            veri["threads_aciklamasi"] = fallback_threads[:500].rstrip()
            kullanilan_threads_modeli = "fallback"

        secilen_ses_ingilizce = ses_secimi.split(" ")[0]

        # ← DÜZELTME: Her session için benzersiz ses dosyası (eşzamanlı kullanıcı çakışmasını önler)
        ses_dosyasi = os.path.join(tempfile.gettempdir(), f"ses_{uuid.uuid4().hex[:8]}.wav")

        ses_basarili, kullanilan_ses_modeli = ses_uret(
            client, SES_MODELLERI, veri["seslendirme_metni"], secilen_ses_ingilizce, ses_dosyasi, log_ekle
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

    except Exception:
        hata_detay = traceback.format_exc()
        hata_detay = hata_detay.replace(gemini_key, "***")  # ← DÜZELTME: API key maskeleme
        log_ekle("❌ HATA OLUŞTU — işlem tamamlanamadı. Aşağıdaki tüm kutuyu kopyalayıp destek için paylaşabilirsin:")
        log_ekle(hata_detay)
        st.error("Sistemde bir hata oluştu. Yukarıdaki süreç kutusunun tamamını kopyalayıp gönderirsen hemen bakarım.")


if st.session_state.sonuc:
    sonuc = st.session_state.sonuc
    veri = sonuc["veri"]
    ses_basarili = sonuc["ses_basarili"]
    ses_dosyasi = sonuc["ses_dosyasi"]
    secilen_ses_ingilizce = sonuc["secilen_ses_ingilizce"]
    kullanilan_metin_modeli = sonuc.get("kullanilan_metin_modeli", "?")
    kullanilan_ses_modeli = sonuc.get("kullanilan_ses_modeli", "?")
    kullanilan_threads_modeli = sonuc.get("kullanilan_threads_modeli", "?")

    st.success(f"✅ otoXtra İçeriği Başarıyla Üretti! (Metin: {kullanilan_metin_modeli})")

    c1, c2 = st.columns([3, 1])
    with c2:
        if st.button("🔄 Yeniden Sorgu (Temizle)"):
            st.session_state.sonuc = None
            st.session_state.log_satirlari = []
            st.rerun()

    st.markdown("### 🎧 Medya Dosyaları")
    st.markdown(f"**🎙️ Seslendirme** (model: {kullanilan_ses_modeli})")
    if ses_basarili and os.path.exists(ses_dosyasi):
        st.audio(ses_dosyasi)
        with open(ses_dosyasi, "rb") as f:
            st.download_button(
                f"⬇️ {secilen_ses_ingilizce} Sesini İndir (.wav)",
                f,
                file_name="seslendirme.wav",
                mime="audio/wav",
            )
    else:
        st.warning("Ses dosyası bulunamadı. Lütfen tekrar üretin.")

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

    st.subheader("3️⃣ Threads Açıklaması (500 Karakter, Etiketsiz)")
    st.caption(f"Hashtag içermez. Kısa ve akıcı olacak şekilde hazırlanır. (Model: {kullanilan_threads_modeli})")
    st.code(markdown_temizle(veri.get("threads_aciklamasi", "")), language=None)

    with st.expander("🎙️ Seslendirme Metni (kontrol için)"):
        st.code(markdown_temizle(veri.get("seslendirme_metni", "")), language=None)
