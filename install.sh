#!/usr/bin/env bash
set -euo pipefail

# ALCA_KOK yalniz testler icindir (test_pi_gecis.py): butun yollari gecici bir
# klasore tasir. Uretimde bostur.
KOK="${ALCA_KOK:-}"
APP_DIR="$KOK/opt/alca-pi-agent"
STATE_DIR="$KOK/var/lib/alca-pi-agent"
ENV_FILE="$KOK/etc/alca-pi-agent.env"
SERVICE_FILE="$KOK/etc/systemd/system/alca-pi-agent.service"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIGURE_MODE="auto"

# GERIYE DONUK UYUMLULUK: urunun eski adi. Sahadaki Pi'ler bu adlarla kurulu ve
# uretim sayiyor; asagidaki tek seferlik gecis onlari yeni ada tasir (README
# "Sahadaki Pi nasil gecer"). Eski ad bu betikte yalniz gecis icin gecer.
ESKI_SERVIS="udar-pi-agent"
ESKI_APP_DIR="$KOK/opt/udar-pi-agent"
ESKI_STATE_DIR="$KOK/var/lib/udar-pi-agent"
ESKI_ENV_FILE="$KOK/etc/udar-pi-agent.env"
ESKI_SERVICE_FILE="$KOK/etc/systemd/system/udar-pi-agent.service"
ESKI_KLON_ADI="udar-pi-agent"
# Eski ajanin sureci (pgrep/pkill deseni; koseli ayrac desenin kendini bulmasini onler).
ESKI_SUREC_DESENI='python[^ ]* .*udar_pi_agent[.]py'
# Env dosyasindaki yollar KOK'suz yazilir (servis onlari boyle gorur).
ESKI_KUYRUK_KLASORU="/var/lib/udar-pi-agent/"
ESKI_KOKSUZ_APP_DIR="/opt/udar-pi-agent"
YENI_KUYRUK_KLASORU="/var/lib/alca-pi-agent/"
YENI_KLON_ADI="alca-pi-agent"
YENI_REPO_URL="https://github.com/Enes3078/alca-pi-agent.git"
# Yoklamalar arasi bekleme (sn); testler 0 verir.
GECIS_BEKLEME_SN="${ALCA_GECIS_BEKLEME_SN:-2}"

for arg in "$@"; do
  case "$arg" in
    --configure) CONFIGURE_MODE="yes" ;;
    --no-config) CONFIGURE_MODE="no" ;;
    *)
      echo "Kullanim: bash install.sh [--configure|--no-config]" >&2
      exit 2
      ;;
  esac
done

current_env_value() {
  local key="$1"
  if [ ! -f "$ENV_FILE" ]; then
    return 0
  fi
  sudo awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE" 2>/dev/null || true
}

# SORULAR DAIMA TERMINALDEN OKUNUR, stdin'den DEGIL.
#
# Tek satirlik kurulumda (`wget -qO- .../bootstrap.sh | bash`) stdin, bash'in
# okudugu BETIGIN KENDISIDIR. `read` stdin'den okursa soru sormaz; betigin kalan
# satirlarini cevap sanip yutar. 2026-09-21'de sahada bu oldu: sihirbaz kimseyi
# beklemeden bitti ve env dosyasina ALCA_BOUNCE_SECONDS='echo "  sudo systemctl
# restart alca-pi-agent"' gibi cop yazdi, ajan her acilista coktu.
# ALCA_TTY yalniz testler icindir; normalde /dev/tty (kullanicinin terminali).
SORU_TTY="${ALCA_TTY:-/dev/tty}"
# Terminal BIR KEZ acilir ve tum sorular ayni tanimlayicidan okunur. Her soruda
# yeniden acmak gercek terminalde calisir ama dosyayla (test) her seferinde ilk
# satiri okur; tek acilis ikisinde de dogru. Alt kabuklar ($(...)) tanimlayiciyi
# miras aldigi icin okuma konumu paylasilir.
SORU_FD=9

# ALCA_PULSE_MIN_INTERVAL_SECONDS onerisi (c = en kisa gercek cevrim, sn):
# cevrimin yarisi, 0.01'e asagi yuvarlanir; yarisi 0.20'nin altina duser ve
# cevrim 0.25 sn'den uzunsa ondeger 0.20 kalir. AYNI KURAL
# alca_pi_agent.min_interval_suggestion'da; test ikisini karsilastirir.
ARALIK_ONERISI_AWK='BEGIN { h = int(c * 100 / 2 + 0.000001) / 100; if (h < 0.20 && c > 0.25) h = 0.20; printf "%.2f\n", h }'

# a < b mi? (ondalikli; bash tam sayi disinda karsilastiramaz)
sayi_kucuk_mu() {
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 < b + 0) }'
}

ask_default() {
  local prompt="$1"
  local default="$2"
  local value
  if [ -n "$default" ]; then
    read -r -u "$SORU_FD" -p "$prompt [$default]: " value
    printf '%s\n' "${value:-$default}"
  else
    read -r -u "$SORU_FD" -p "$prompt: " value
    printf '%s\n' "$value"
  fi
}

# Sayi sorusu. Gecersiz cevap yazilmaz, soru tekrarlanir. Mevcut deger
# (env dosyasindan gelen oneri) sayi degilse ONERILMEZ; yerine urunun ondegeri
# gelir — yoksa bozuk bir env dosyasiyla sihirbazi yeniden calistiran kisi
# Enter'a bastikca ayni copu geri yazardi.
ask_number() {
  local prompt="$1" current="$2" fallback="$3" kind="${4:-decimal}" value pattern
  if [ "$kind" = "integer" ]; then pattern='^[0-9]+$'; else pattern='^[0-9]+([.][0-9]+)?$'; fi
  [[ "$current" =~ $pattern ]] || current="$fallback"
  while true; do
    value="$(ask_default "$prompt" "$current")"
    value="${value/,/.}"   # Turkce ondalik virgul: "1,5" -> "1.5"
    if [[ "$value" =~ $pattern ]]; then printf '%s\n' "$value"; return 0; fi
    echo "  Sayi girilmeli (ornek: $fallback). Girilen: '$value'" >&2
  done
}

# Secenekli soru (true/false, high/low ...). Ayni kural: gecersiz deger yazilmaz.
ask_choice() {
  local prompt="$1" current="$2" fallback="$3"; shift 3
  local value secenek
  local gecerli=" $* "
  [[ "$gecerli" == *" $current "* ]] || current="$fallback"
  while true; do
    value="$(ask_default "$prompt" "$current")"
    [[ "$gecerli" == *" $value "* ]] && { printf '%s\n' "$value"; return 0; }
    echo "  Su degerlerden biri girilmeli:$(printf ' %s' "$@"). Girilen: '$value'" >&2
  done
}

env_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

write_env_file() {
  if ! exec 9<"$SORU_TTY"; then
    echo "[ALCA] Terminal acilamadi ($SORU_TTY); ayarlar sorulamiyor." >&2
    echo "[ALCA] Pi'nin terminalinde calistirin:  bash install.sh --configure" >&2
    return 1
  fi
  local crm_url device_token gpio_bcm pull_up poll_interval pulse_edge pulse_min_active pulse_rearm pulse_max_active pulse_min_interval mode duration_unit min_duration duration_active_level duration_start_stable duration_stop_stable station_code line_id operator_id note
  local edge_onerisi shortest_cycle interval_onerisi

  local url_onerisi
  url_onerisi="$(current_env_value ALCA_CRM_URL || true)"
  [[ "$url_onerisi" =~ ^https?://[^[:space:]]+$ ]] || url_onerisi=""
  while true; do
    crm_url="$(ask_default "CRM adresi (orn: https://crm.firmaniz.com)" "$url_onerisi")"
    [[ "$crm_url" =~ ^https?://[^[:space:]]+$ ]] && break
    echo "  Adres http:// ya da https:// ile baslamali. Girilen: '$crm_url'" >&2
  done

  local token_onerisi
  token_onerisi="$(current_env_value ALCA_DEVICE_TOKEN || true)"
  [[ "$token_onerisi" =~ ^[^[:space:]]+$ ]] || token_onerisi=""
  device_token="$(ask_default "CRM cihaz tokeni" "$token_onerisi")"
  while [ -z "$device_token" ] || [ "$device_token" = "PASTE_DEVICE_TOKEN_HERE" ] || [[ "$device_token" =~ [[:space:]] ]]; do
    echo "Cihaz tokeni zorunlu. CRM > Imalat Yonetimi > Cihaz & Veri ekranindan kopyala."
    device_token="$(ask_default "CRM cihaz tokeni" "")"
  done

  gpio_bcm="$(ask_number "GPIO BCM pin numarasi (fiziksel pin 13 icin 27, fiziksel pin 11 icin 17)" "$(current_env_value ALCA_GPIO_BCM || true)" 27 integer)"
  echo "Pull-up: false = kontak 3,3V ile pin arasinda (bosta pin 0);"
  echo "         true  = kontak pin ile GND arasinda (bosta pin 1). README: Kablolama kontrol listesi."
  pull_up="$(ask_choice "Pull-up kullanilsin mi? true/false" "$(current_env_value ALCA_PULL_UP || true)" false true false)"
  # ALCA_BOUNCE_SECONDS artik sorulmaz ve yazilmaz: Temmuz 2026'dan (391b844) beri
  # kullanilmiyor; daha eski ajanda gpiozero Button bounce_time idi.
  poll_interval="$(ask_number "GPIO okuma araligi saniye" "$(current_env_value ALCA_POLL_INTERVAL_SECONDS || true)" 0.002)"

  echo
  echo "Makine tipi sec:"
  echo "  1) Vurus / sayim bazli: her tam sinyal (0->1->0) 1 adet sayilir"
  echo "  2) Sure bazli: sinyal 1 kaldigi sure saniye olarak toplanir"
  local machine_choice default_choice
  default_choice="$(current_env_value ALCA_MEASUREMENT_MODE || true)"
  if [ "$default_choice" = "duration" ]; then
    default_choice="2"
  else
    default_choice="1"
  fi
  machine_choice="$(ask_choice "Secim (1 ya da 2)" "$default_choice" 1 1 2)"
  if [ "$machine_choice" = "2" ] || [ "$machine_choice" = "duration" ]; then
    mode="duration"
    duration_unit="seconds"
    min_duration="$(ask_number "En kisa sayilacak sure saniye" "$(current_env_value ALCA_MIN_DURATION_SECONDS || true)" 0.2)"
    duration_active_level="$(ask_choice "Makine calisirken pin seviyesi high/low" "$(current_env_value ALCA_DURATION_ACTIVE_LEVEL || true)" high high low)"
    duration_start_stable="$(ask_number "Baslangic sinyalinin kararlilik suresi (sn)" "$(current_env_value ALCA_DURATION_START_STABLE_SECONDS || true)" 0.20)"
    duration_stop_stable="$(ask_number "Bitis sinyalinin kararlilik suresi (sn)" "$(current_env_value ALCA_DURATION_STOP_STABLE_SECONDS || true)" 0.20)"
    note="$(ask_default "Not" "$(current_env_value ALCA_NOTE || true)")"
    note="${note:-GPIO${gpio_bcm} sure olcumu}"
  else
    mode="pulse"
    echo
    echo "Pulse kenari sec:"
    echo "  rising  : bos 0, vurus 1 ise"
    echo "  falling : bos 1, vurus 0 ise"
    edge_onerisi="$(current_env_value ALCA_PULSE_EDGE || true)"
    if [ "$edge_onerisi" = "both" ]; then
      # 'both' her zaman rising gibi calisti (tam cevrimi 1 sayar); oneri rising.
      echo "  Not: onceki ayar 'both' idi. Ajan onu zaten 'rising' gibi calistiriyordu;"
      echo "       'rising' secerseniz sayim DEGISMEZ."
    fi
    pulse_edge="$(ask_choice "Pulse kenari" "$edge_onerisi" rising rising falling)"
    if { [ "$pull_up" = "true" ] && [ "$pulse_edge" = "rising" ]; } || { [ "$pull_up" = "false" ] && [ "$pulse_edge" = "falling" ]; }; then
      echo "  UYARI: pull-up=$pull_up ile '$pulse_edge' genelde uyusmaz (bosta pin ters seviyede kalir)." >&2
      echo "         Kablolamayi README 'Kablolama kontrol listesi' 3. maddeye gore kontrol edin." >&2
    fi

    echo
    echo "Makinenin en kisa GERCEK cevrimi (bir isin basindan sonrakinin basina en kisa sure) kac saniye?"
    echo "  diagnose_gpio.py ile gercek is sayisini girerek olctuyseniz ozetteki \"install.sh 'en kisa"
    echo "  gercek cevrim' sorusunun cevabi\" satirindaki degeri yazin (tek aykiri olcum degil, iki cevrim"
    echo "  arasinin alt %10'u; bu satir yalniz is sayisiyla kontrol edilmis olcumde yazar)."
    echo "  Bilmiyorsaniz 0 yazin: oneri yapilmaz, mevcut ayar korunur."
    shortest_cycle="$(ask_number "En kisa gercek cevrim (sn)" "0" 0)"

    pulse_min_active="$(ask_number "Sinyalin en az aktif kalacagi sure (sn)" "$(current_env_value ALCA_PULSE_MIN_ACTIVE_SECONDS || true)" 0.02)"
    pulse_rearm="$(ask_number "Yeni vurus oncesi pasif kalma suresi (sn)" "$(current_env_value ALCA_PULSE_REARM_SECONDS || true)" 0.20)"
    pulse_max_active="$(ask_number "Tek vurusun azami aktif suresi (sn)" "$(current_env_value ALCA_PULSE_MAX_ACTIVE_SECONDS || true)" 10.0)"

    interval_onerisi="$(current_env_value ALCA_PULSE_MIN_INTERVAL_SECONDS || true)"
    if sayi_kucuk_mu 0 "$shortest_cycle"; then
      interval_onerisi="$(awk -v c="$shortest_cycle" "$ARALIK_ONERISI_AWK")"
      echo "  Oneri: en kisa cevrim $shortest_cycle sn -> iki vurus arasi en az $interval_onerisi sn."
      if ! sayi_kucuk_mu 0.25 "$shortest_cycle"; then
        echo "  UYARI: makine cok hizli; tek tek vurus saymak hataya acik. Mumkunse makinenin kendi" >&2
        echo "         sayacini kullanin; degilse diagnose_gpio.py onerilerini girin." >&2
      fi
    fi
    while true; do
      pulse_min_interval="$(ask_number "Iki vurus arasindaki asgari sure (sn)" "$interval_onerisi" 0.20)"
      # Bu sure en kisa gercek cevrimden kisa olmali; yoksa gercek vuruslar sayilmaz.
      if sayi_kucuk_mu 0 "$shortest_cycle" && ! sayi_kucuk_mu "$pulse_min_interval" "$shortest_cycle"; then
        echo "  Bu deger en kisa cevrimden ($shortest_cycle sn) KUCUK olmali; yoksa gercek vuruslar sayilmaz." >&2
        continue
      fi
      break
    done
    duration_unit="seconds"
    min_duration="$(current_env_value ALCA_MIN_DURATION_SECONDS || true)"
    min_duration="${min_duration:-0.2}"
    note="$(ask_default "Not" "$(current_env_value ALCA_NOTE || true)")"
    note="${note:-GPIO${gpio_bcm} vurus sayimi}"
  fi

  station_code="$(ask_default "Istasyon kodu (orn: M-01, bos kalabilir)" "$(current_env_value ALCA_STATION_CODE || true)")"
  line_id="$(ask_default "Is emri satir ID (genelde bos birak)" "$(current_env_value ALCA_LINE_ID || true)")"
  operator_id="$(ask_default "Operator ID (genelde bos birak)" "$(current_env_value ALCA_OPERATOR_ID || true)")"

  # Sihirbazin sormadigi ayarlar: mevcut deger korunur, yoksa ondeger yazilir.
  # BOS YAZILMAZ — eski ajan surumleri bos degeri float("") ile okuyup coker.
  local dropout_grace dropout_log retry_base retry_validation retry_max queue_db
  dropout_grace="$(current_env_value ALCA_DURATION_DROPOUT_GRACE_SECONDS || true)"; dropout_grace="${dropout_grace:-1.50}"
  dropout_log="$(current_env_value ALCA_DURATION_DROPOUT_LOG_INTERVAL_SECONDS || true)"; dropout_log="${dropout_log:-30}"
  retry_base="$(current_env_value ALCA_SEND_RETRY_BASE_SECONDS || true)"; retry_base="${retry_base:-5}"
  retry_validation="$(current_env_value ALCA_SEND_RETRY_VALIDATION_SECONDS || true)"; retry_validation="${retry_validation:-60}"
  retry_max="$(current_env_value ALCA_SEND_RETRY_MAX_SECONDS || true)"; retry_max="${retry_max:-300}"
  # Kuyruk yolu da korunur: gecisten eski yolda kalmis kuyruk sihirbazla kaybolmasin.
  queue_db="$(current_env_value ALCA_QUEUE_DB || true)"; queue_db="${queue_db:-${YENI_KUYRUK_KLASORU}machine_events.sqlite3}"

  sudo tee "$ENV_FILE" >/dev/null <<EOF
ALCA_CRM_URL=$crm_url
ALCA_DEVICE_TOKEN=$device_token

ALCA_GPIO_BCM=$gpio_bcm
ALCA_PULL_UP=$pull_up
ALCA_POLL_INTERVAL_SECONDS=$poll_interval

ALCA_MEASUREMENT_MODE=$mode
ALCA_PULSE_EDGE=${pulse_edge:-rising}
ALCA_PULSE_MIN_ACTIVE_SECONDS=${pulse_min_active:-0.02}
ALCA_PULSE_REARM_SECONDS=${pulse_rearm:-0.20}
ALCA_PULSE_MAX_ACTIVE_SECONDS=${pulse_max_active:-10.0}
ALCA_PULSE_MIN_INTERVAL_SECONDS=${pulse_min_interval:-0.20}
ALCA_DURATION_UNIT=$duration_unit
ALCA_MIN_DURATION_SECONDS=$min_duration
ALCA_DURATION_ACTIVE_LEVEL=${duration_active_level:-high}
ALCA_DURATION_START_STABLE_SECONDS=${duration_start_stable:-0.20}
ALCA_DURATION_STOP_STABLE_SECONDS=${duration_stop_stable:-0.20}
ALCA_DURATION_DROPOUT_GRACE_SECONDS=$dropout_grace
ALCA_DURATION_DROPOUT_LOG_INTERVAL_SECONDS=$dropout_log
ALCA_DAILY_RESET=true

ALCA_LINE_ID=$line_id
ALCA_STATION_CODE=$station_code
ALCA_OPERATOR_ID=$operator_id

ALCA_NOTE=$(env_quote "$note")
ALCA_HTTP_TIMEOUT=5
ALCA_QUEUE_DB=$queue_db
ALCA_SEND_RETRY_BASE_SECONDS=$retry_base
ALCA_SEND_RETRY_VALIDATION_SECONDS=$retry_validation
ALCA_SEND_RETRY_MAX_SECONDS=$retry_max
EOF
  sudo chmod 600 "$ENV_FILE"
  exec 9<&-
  echo "[ALCA] Env dosyasi guncellendi: $ENV_FILE"
}

# ---------------------------------------------------------------------------
# ESKI ADLA KURULU PI'NIN TEK SEFERLIK GECISI
#
# Sahadaki Pi `bash update.sh` ile buraya gelir: ESKI update.sh once `git pull`,
# sonra bu betik, sonra (set -e altinda) ESKI servis adiyla `systemctl restart` ve
# `status` calistirir. Bu yuzden eski ad bu Pi'de takma ad olarak kalir; yeni
# update.sh ilk calismasinda onu kaldirir.
#
# Her adim kendi basina idempotent: yarim kalan gecis sonraki calismada kaldigi
# yerden devam eder. Kuyruk ASLA silinmez.
# ---------------------------------------------------------------------------

eski_servis_dosyasi_var() {
  [ -f "$ESKI_SERVICE_FILE" ] && [ ! -L "$ESKI_SERVICE_FILE" ]
}

eski_kurulum_var() {
  eski_servis_dosyasi_var || [ -e "$ESKI_ENV_FILE" ] || [ -e "$ESKI_APP_DIR" ] || [ -e "$ESKI_STATE_DIR" ]
}

eski_surec_var() {
  pgrep -f "$ESKI_SUREC_DESENI" >/dev/null 2>&1
}

gecis_durdu() {
  echo "[ALCA] HATA: $1" >&2
  echo "[ALCA] Iki ajan ayni makineyi iki kez sayar; gecis YAPILMADI, hicbir dosya tasinmadi." >&2
  pgrep -af "$ESKI_SUREC_DESENI" >&2 || true
  echo "[ALCA] Kalan ajani durdurup yeniden deneyin:  bash install.sh --no-config" >&2
  echo "[ALCA] Eski ajani geri acmak icin:  sudo systemctl enable --now $ESKI_SERVIS" >&2
  exit 1
}

# (a) Yeni servis baslamadan eski ajan KESIN durmus olmali.
eski_ajani_durdur() {
  if eski_servis_dosyasi_var; then
    echo "[ALCA] Eski servis durduruluyor: $ESKI_SERVIS"
    sudo systemctl stop "$ESKI_SERVIS" 2>/dev/null || true
    sudo systemctl disable "$ESKI_SERVIS" 2>/dev/null || true
    if sudo systemctl is-active --quiet "$ESKI_SERVIS" 2>/dev/null; then
      gecis_durdu "eski servis durmadi: $ESKI_SERVIS"
    fi
  fi
  # Elle baslatilmis kopya da ayni pini sayar ve kuyruga yazar. SIGTERM ile duzgun kapanir.
  if eski_surec_var; then
    echo "[ALCA] Servis disinda calisan eski ajan durduruluyor..."
    sudo pkill -TERM -f "$ESKI_SUREC_DESENI" 2>/dev/null || true
    local deneme
    for deneme in 1 2 3 4 5; do
      eski_surec_var || break
      sleep "$GECIS_BEKLEME_SN"
    done
    if eski_surec_var; then
      gecis_durdu "servis disinda calisan eski ajan durmadi"
    fi
  fi
}

klasor_dolu_mu() {
  [ -n "$(sudo find "$1/" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]
}

# (c) Kuyruk klasoru BUTUN olarak tasinir (yanindaki -wal/-shm/.lock dahil).
# Hedef doluysa tasinmaz: iki kuyruk birlestirilemez, kayit kaybolmasin diye eski
# yerinde kalir ve ajan onu okumaya devam eder (env_gecir).
KUYRUK_ESKI_YERDE="hayir"
kuyrugu_tasi() {
  [ -e "$ESKI_STATE_DIR" ] || return 0
  if ! klasor_dolu_mu "$ESKI_STATE_DIR"; then
    sudo rmdir "$ESKI_STATE_DIR" 2>/dev/null || true
    return 0
  fi
  if klasor_dolu_mu "$STATE_DIR"; then
    KUYRUK_ESKI_YERDE="evet"
  elif sudo mv -T "$ESKI_STATE_DIR" "$STATE_DIR"; then
    echo "[ALCA] Kuyruk tasindi: $ESKI_STATE_DIR -> $STATE_DIR"
  else
    KUYRUK_ESKI_YERDE="evet"
  fi
  if [ "$KUYRUK_ESKI_YERDE" = "evet" ]; then
    echo "[ALCA] UYARI: eski kuyruk TASINMADI, yerinde kaldi: $ESKI_STATE_DIR (hedef dolu ya da tasinamadi: $STATE_DIR)." >&2
    echo "[ALCA] UYARI: kayitlar kaybolmaz; iki klasoru elle inceleyin (README \"Sahadaki Pi nasil gecer\")." >&2
  fi
}

# ALCA_QUEUE_DB eski klasoru gosteriyorsa yeni klasore cevirir; baska yola dokunmaz.
kuyruk_yolunu_yeniye_cevir() {
  local dosya="$1" anahtar='^([[:space:]]*(export[[:space:]]+)?ALCA_QUEUE_DB=["'"'"']?)'
  if sudo grep -Eq "$anahtar$ESKI_KUYRUK_KLASORU" "$dosya"; then
    sudo sed -i -E "s#$anahtar$ESKI_KUYRUK_KLASORU#\\1$YENI_KUYRUK_KLASORU#" "$dosya"
  fi
}

# (b) Eski env yeni ada cevrilir: yalniz anahtar oneki degisir, degerler aynen
# kalir (izin 600). Mevcut yeni env'in ustune yazilmaz. (f) Eski dosya token
# tasidigi icin yine 600 ile yedek adina tasinir.
env_gecir() {
  if [ -e "$ESKI_ENV_FILE" ] && [ -e "$ENV_FILE" ]; then
    echo "[ALCA] UYARI: $ENV_FILE zaten var, ustune yazilmadi; eski dosya yalniz yedege alinacak." >&2
  elif [ -e "$ESKI_ENV_FILE" ]; then
    local gecici deger
    gecici="$(sudo mktemp "$ENV_FILE.gecis.XXXXXX")"
    sudo sed -E \
      -e 's/^([[:space:]]*(export[[:space:]]+)?)UDAR_/\1ALCA_/' \
      -e '/^[[:space:]]*#/s/UDAR_/ALCA_/g' \
      -e '/^[[:space:]]*#/s/udar-pi-agent/alca-pi-agent/g' \
      "$ESKI_ENV_FILE" | sudo tee "$gecici" >/dev/null
    if [ "$KUYRUK_ESKI_YERDE" = "evet" ]; then
      # Ajan eski kuyrugu okumali. Yol yazili degilse ondeger yeni klasor olurdu: acikca yazilir.
      deger="$(sudo awk -F= '$1 == "ALCA_QUEUE_DB" { sub(/^[^=]*=/, ""); print; exit }' "$gecici")"
      deger="${deger//[\"\' ]/}"
      if [ -z "$deger" ]; then
        sudo sed -i -E '/^[[:space:]]*ALCA_QUEUE_DB=/d' "$gecici"
        printf 'ALCA_QUEUE_DB=%smachine_events.sqlite3\n' "$ESKI_KUYRUK_KLASORU" | sudo tee -a "$gecici" >/dev/null
      fi
    fi
    sudo chmod 600 "$gecici"
    sudo mv -f "$gecici" "$ENV_FILE"
    echo "[ALCA] Env dosyasi yeni ada cevrildi: $ENV_FILE (degerler aynen)"
  fi

  if [ "$KUYRUK_ESKI_YERDE" != "evet" ] && [ -e "$ENV_FILE" ]; then
    kuyruk_yolunu_yeniye_cevir "$ENV_FILE"
  fi
  if [ "$KUYRUK_ESKI_YERDE" = "evet" ] && [ -e "$ENV_FILE" ] && ! sudo grep -q "ALCA_QUEUE_DB=[\"']\?$ESKI_KUYRUK_KLASORU" "$ENV_FILE"; then
    echo "[ALCA] UYARI: ajan eski kuyrugu OKUMUYOR ($ENV_FILE baska yol gosteriyor)." >&2
    echo "[ALCA] UYARI: gonderilmemis kayit varsa ALCA_QUEUE_DB'yi oraya cevirin: ${ESKI_KUYRUK_KLASORU}machine_events.sqlite3" >&2
  fi

  if [ -e "$ESKI_ENV_FILE" ]; then
    local yedek="$ENV_FILE.gecis-yedegi"
    [ ! -e "$yedek" ] || yedek="$yedek.$(date +%Y%m%d%H%M%S)"
    sudo mv "$ESKI_ENV_FILE" "$yedek"
    sudo chmod 600 "$yedek"
    echo "[ALCA] Eski env dosyasi yedege alindi: $yedek"
  fi
}

# (e) Eski servis dosyasi kalkar; eski ad, eski update.sh'nin son adimlari
# (systemctl restart/status) calissin diye bu Pi'de takma ad olarak kalir.
eski_servisi_takma_ada_cevir() {
  eski_servis_dosyasi_var || return 0
  if [ -d "$ESKI_SERVICE_FILE.d" ] && [ ! -e "$SERVICE_FILE.d" ]; then
    sudo mv -T "$ESKI_SERVICE_FILE.d" "$SERVICE_FILE.d"
    echo "[ALCA] Servis ek ayarlari (drop-in) tasindi: $SERVICE_FILE.d"
  fi
  sudo rm -f "${ESKI_SERVICE_FILE:?}"
  sudo systemctl reset-failed "$ESKI_SERVIS" 2>/dev/null || true
  sudo systemctl daemon-reload
  sudo ln -s "$(basename "$SERVICE_FILE")" "$ESKI_SERVICE_FILE"
}

env_hazir_mi() {
  local adres token
  adres="$(current_env_value ALCA_CRM_URL || true)"
  token="$(current_env_value ALCA_DEVICE_TOKEN || true)"
  [ -n "$adres" ] && [ -n "$token" ] && [ "$token" != "PASTE_DEVICE_TOKEN_HERE" ]
}

# (g) Yeni servis baslar ve ayakta kaldigi dogrulanir. Tek yoklama yetmez: ayar
# bozuksa ajan acilista cokup RestartSec (3 sn) boyunca bekler; aralikli yoklama
# bu pencereyi kacirmaz.
yeni_servisi_baslat_ve_dogrula() {
  if ! env_hazir_mi; then
    echo "[ALCA] UYARI: CRM adresi ya da cihaz tokeni eksik; servis baslatilmadi." >&2
    echo "[ALCA] Ayarlamak icin:  bash install.sh --configure" >&2
    return 0
  fi
  echo "[ALCA] Yeni servis baslatiliyor: alca-pi-agent"
  sudo systemctl start alca-pi-agent || true
  local deneme
  for deneme in 1 2 3 4; do
    sleep "$GECIS_BEKLEME_SN"
    if ! sudo systemctl is-active --quiet alca-pi-agent; then
      echo "[ALCA] HATA: alca-pi-agent ayakta kalmadi. Makine su an SAYILMIYOR." >&2
      echo "[ALCA] Kuyruk ve ayarlar duruyor, hicbir kayit silinmedi." >&2
      echo "[ALCA] Nedeni gormek icin:  journalctl -u alca-pi-agent -n 50 --no-pager" >&2
      echo "[ALCA] Ayari duzeltmek icin:  sudo nano $ENV_FILE" >&2
      echo "[ALCA] Sonra baslatmak icin:  sudo systemctl restart alca-pi-agent" >&2
      echo "[ALCA] Eski ayar yedegi:  $ENV_FILE.gecis-yedegi" >&2
      exit 1
    fi
  done
  echo "[ALCA] Yeni servis calisiyor: alca-pi-agent"
}

# (h) Eski adli klon klasoru ve origin adresi yeni ada gecer. Betigin EN SONUNDA
# calisir: eski update.sh bu klasorde calisir, klasor adi degisse de cwd'si ve
# acik dosyasi gecerli kalir. Tasinamazsa kurulum basarili sayilir, uyarilir.
klonu_yeni_ada_gecir() {
  if [ "$(basename "$REPO_DIR")" = "$ESKI_KLON_ADI" ]; then
    local hedef
    hedef="$(dirname "$REPO_DIR")/$YENI_KLON_ADI"
    if [ -e "$hedef" ]; then
      echo "[ALCA] UYARI: klasor tasinamadi, hedef zaten var: $hedef. Eski klasor kullanilmaya devam edilebilir: $REPO_DIR" >&2
    elif mv "$REPO_DIR" "$hedef"; then
      REPO_DIR="$hedef"
      echo "[ALCA] Klasor tasindi. Bundan sonra:  cd $hedef && bash update.sh"
    else
      echo "[ALCA] UYARI: klasor tasinamadi: $REPO_DIR -> $hedef" >&2
    fi
  fi
  if [ -d "$REPO_DIR/.git" ]; then
    case "$(git -C "$REPO_DIR" config --get remote.origin.url 2>/dev/null || true)" in
      *Enes3078/udar-pi-agent*)
        # Yeni depo erisilemiyorsa origin DEGISMEZ: eski adres calisir (GitHub yeniden
        # adlandirmada yonlendirir), yoksa sonraki update.sh git pull'da dururdu.
        if ! GIT_TERMINAL_PROMPT=0 git ls-remote "$YENI_REPO_URL" HEAD >/dev/null 2>&1; then
          echo "[ALCA] UYARI: yeni depoya erisilemedi, origin degistirilmedi: $YENI_REPO_URL" >&2
        else
          git -C "$REPO_DIR" remote set-url origin "$YENI_REPO_URL" \
            || echo "[ALCA] UYARI: origin adresi degistirilemedi: $REPO_DIR" >&2
        fi
        ;;
    esac
  fi
}

# (d) Eski uygulama klasoru yalniz kuyruk icermiyorsa silinir: kuyruk ASLA silinmez.
# Ozel ALCA_QUEUE_DB bu klasoru gosterebilir; ajan orayi okumaya devam eder.
eski_uygulama_klasorunu_kaldir() {
  [ -e "$ESKI_APP_DIR" ] || return 0
  local deger
  deger="$(current_env_value ALCA_QUEUE_DB || true)"
  deger="${deger//[\"\' ]/}"
  case "$deger" in
    "${ESKI_KOKSUZ_APP_DIR}/"*)
      echo "[ALCA] UYARI: kuyruk eski uygulama klasorunde, klasor SILINMEDI: $deger" >&2
      echo "[ALCA] UYARI: ajan oradan okumaya devam eder; kuyrugu tasimak icin README \"Sahadaki Pi nasil gecer\"." >&2
      return 0
      ;;
  esac
  if [ -n "$(sudo find "$ESKI_APP_DIR" -name '*.sqlite3*' -print -quit 2>/dev/null)" ]; then
    echo "[ALCA] UYARI: eski uygulama klasorunde kuyruk dosyasi var, klasor SILINMEDI: $ESKI_APP_DIR" >&2
    echo "[ALCA] UYARI: icini inceleyin; gonderilmemis kayit olabilir." >&2
    return 0
  fi
  sudo rm -rf "${ESKI_APP_DIR:?}"
}

echo "[ALCA] Python paketleri kontrol ediliyor..."
sudo apt install -y python3-gpiozero python3-requests python3-rpi.gpio sqlite3

GECIS="hayir"
if eski_kurulum_var; then
  GECIS="evet"
  echo "[ALCA] Eski adla kurulum bulundu; ayar ve kuyruk yeni ada tasiniyor..."
  eski_ajani_durdur
fi

echo "[ALCA] Servis durduruluyor..."
sudo systemctl stop alca-pi-agent 2>/dev/null || true

if [ "$GECIS" = "evet" ]; then
  kuyrugu_tasi
  env_gecir
fi

echo "[ALCA] Dosyalar kuruluyor..."
sudo mkdir -p "$APP_DIR" "$STATE_DIR"
sudo cp "$REPO_DIR/alca_pi_agent.py" "$APP_DIR/alca_pi_agent.py"
sudo cp "$REPO_DIR/diagnose_gpio.py" "$APP_DIR/diagnose_gpio.py"
sudo cp "$REPO_DIR/alca-pi-agent.service" "$SERVICE_FILE"

if [ "$GECIS" = "evet" ]; then
  eski_servisi_takma_ada_cevir
fi

if [ ! -f "$ENV_FILE" ]; then
  sudo cp "$REPO_DIR/alca-pi-agent.env.example" "$ENV_FILE"
  echo "[ALCA] Env dosyasi olusturuldu: $ENV_FILE"
else
  echo "[ALCA] Mevcut env korundu: $ENV_FILE"
fi

if [ "$CONFIGURE_MODE" = "yes" ]; then
  write_env_file
elif [ "$CONFIGURE_MODE" = "auto" ] && [ -t 0 ]; then
  if [ ! -f "$ENV_FILE" ] || sudo grep -q "PASTE_DEVICE_TOKEN_HERE" "$ENV_FILE"; then
    write_env_file
  else
    read -r -p "[ALCA] Makine tipini/tokeni yeniden ayarlamak ister misin? [y/N]: " configure_answer
    case "$configure_answer" in
      y|Y|yes|YES|e|E|evet|EVET) write_env_file ;;
      *) echo "[ALCA] Env degistirilmedi." ;;
    esac
  fi
fi

sudo systemctl daemon-reload
sudo systemctl enable alca-pi-agent

if [ "$GECIS" = "evet" ]; then
  yeni_servisi_baslat_ve_dogrula
  # (d) En son: yeni servis ayakta kalmadiysa eski kod inceleme icin durur.
  eski_uygulama_klasorunu_kaldir
fi

echo
echo "[ALCA] Kurulum bitti."
echo "[ALCA] Ayarlari degistirmek icin: bash install.sh --configure"
echo "[ALCA] Elle duzenlemek icin: sudo nano $ENV_FILE"
if [ "$GECIS" = "hayir" ]; then
  echo "[ALCA] Sonra baslat: sudo systemctl restart alca-pi-agent"
fi

klonu_yeni_ada_gecir
