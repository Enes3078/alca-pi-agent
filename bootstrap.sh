#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/Enes3078/alca-pi-agent.git"
TARGET_DIR="${HOME}/alca-pi-agent"
# GERIYE DONUK UYUMLULUK: eski adli klon yeniden indirilmez, yeni ada tasinir.
# Eski ajanin kurulu kalani icin gecisi install.sh yapar.
ESKI_KLON_DIR="${HOME}/udar-pi-agent"

echo "[ALCA] Raspberry Pi ajan kurulumu basliyor..."
echo "[ALCA] Gerekli paketler kuruluyor: git, python GPIO, requests, sqlite"
if command -v dpkg >/dev/null 2>&1; then
  echo "[ALCA] Yarım kalmış dpkg işlemleri toparlanıyor..."
  sudo dpkg --configure -a
fi
sudo apt-get update
sudo apt-get install -y git ca-certificates python3-gpiozero python3-requests python3-rpi.gpio sqlite3

if [ ! -e "$TARGET_DIR" ] && [ -d "$ESKI_KLON_DIR/.git" ]; then
  echo "[ALCA] Eski adli repo yeni ada tasiniyor: $ESKI_KLON_DIR -> $TARGET_DIR"
  mv "$ESKI_KLON_DIR" "$TARGET_DIR"
  # Yeni depo erisilemiyorsa origin degismez: eski adres calisir, git pull durmaz.
  if GIT_TERMINAL_PROMPT=0 git ls-remote "$REPO_URL" HEAD >/dev/null 2>&1; then
    git -C "$TARGET_DIR" remote set-url origin "$REPO_URL"
  else
    echo "[ALCA] UYARI: yeni depoya erisilemedi, origin degistirilmedi: $REPO_URL" >&2
  fi
fi

if [ -d "$TARGET_DIR/.git" ]; then
  echo "[ALCA] Mevcut repo bulundu, guncelleniyor: $TARGET_DIR"
  git -C "$TARGET_DIR" pull --ff-only
else
  if [ -e "$TARGET_DIR" ]; then
    BACKUP_DIR="${TARGET_DIR}.backup.$(date +%Y%m%d%H%M%S)"
    echo "[ALCA] Git olmayan eski klasor bulundu, yedekleniyor: $BACKUP_DIR"
    mv "$TARGET_DIR" "$BACKUP_DIR"
  fi
  echo "[ALCA] Public repo indiriliyor..."
  git clone "$REPO_URL" "$TARGET_DIR"
fi

cd "$TARGET_DIR"
chmod +x install.sh update.sh diagnose_gpio.py

# Bu betik genelde `wget -qO- ... | bash` ile calisir; o durumda stdin BU
# BETIGIN KENDISIDIR. Sihirbaz stdin'den okursa soru sormadan betigin kalan
# satirlarini cevap sanar (2026-09-21 saha arizasi). Sihirbaz bu yuzden
# kullanicinin terminaline baglanir. Terminal yoksa (otomatik kurulum) SORMADAN
# kurulur ve ne yapilacagi yazilir — yarim/cop ayarla ajan baslatilmaz.
# `[ -r /dev/tty ]` yetmez: terminali olmayan surecte de dogru doner, acilisi ise
# hata verir. Bu yuzden gercekten acilip acilmadigina bakilir.
if { : </dev/tty; } 2>/dev/null; then
  bash install.sh --configure </dev/tty
else
  bash install.sh --no-config
  echo
  echo "[ALCA] Terminal bulunamadi; ayar sorulmadi."
  echo "[ALCA] Ayarlamak icin Pi'nin terminalinde:  cd $TARGET_DIR && bash install.sh --configure"
fi

echo
echo "[ALCA] Kurulum tamamlandi."
echo "[ALCA] Servisi baslatmak icin:"
echo "  sudo systemctl restart alca-pi-agent"
echo "  journalctl -u alca-pi-agent -f"
