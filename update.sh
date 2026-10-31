#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ALCA_KOK yalniz testler icindir (test_pi_gecis.py); uretimde bostur.
KOK="${ALCA_KOK:-}"

cd "$REPO_DIR"
echo "[ALCA] Repo guncelleniyor..."
git pull --ff-only

echo "[ALCA] Ajan tekrar kuruluyor; /etc/alca-pi-agent.env korunacak..."
bash "$REPO_DIR/install.sh" --no-config

# GERIYE DONUK UYUMLULUK: gecis, ESKI update.sh'nin son adimlari calissin diye eski
# servis adini takma ad (symlink) birakir. Buraya gelindiyse yeni betik calisiyor,
# takma ada gerek kalmadi. REPO_DIR'e bu noktadan sonra guvenilmez: install.sh eski
# adli klasoru yeni ada tasimis olabilir.
ESKI_TAKMA_AD="$KOK/etc/systemd/system/udar-pi-agent.service"
if [ -L "$ESKI_TAKMA_AD" ]; then
  sudo rm -f "${ESKI_TAKMA_AD:?}"
  sudo systemctl daemon-reload
fi

echo "[ALCA] Servis yeniden baslatiliyor..."
sudo systemctl restart alca-pi-agent

SURUM="$(sed -n 's/^AGENT_VERSION = "\(.*\)"$/\1/p' "$KOK/opt/alca-pi-agent/alca_pi_agent.py" 2>/dev/null || true)"
echo "[ALCA] Kurulu ajan surumu: ${SURUM:-bilinmiyor}"

# Tek ajan kurali: ayni Pi'de iki ajan ayni makineyi iki kez sayar. Yeni ajan
# ikinci kopyayi kilitle zaten baslatmaz; ama eski surumden kalma (kilitsiz)
# bir kopya elle ya da baska bir servisle calisiyor olabilir; eski adla calisan
# kopya da ayni makineyi sayar, bu yuzden iki dosya adi da aranir.
AJANLAR="$(pgrep -af '(alca|udar)_pi_agent[.]py' || true)"
AJAN_SAYISI="$(printf '%s\n' "$AJANLAR" | grep -c . || true)"
if [ "${AJAN_SAYISI:-0}" -gt 1 ]; then
  echo "[ALCA] UYARI: $AJAN_SAYISI ajan sureci calisiyor; ayni makine birden fazla sayilir:"
  printf '%s\n' "$AJANLAR"
  echo "[ALCA] Fazla olani durdurun (eski servis ya da elle baslatilmis kopya)."
fi

sudo systemctl --no-pager --full status alca-pi-agent
