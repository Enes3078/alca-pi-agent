#!/usr/bin/env python3
"""Sahadaki Pi'nin eski addan yeni ada geçişi: GERÇEK eski update.sh akışı.

Sahadaki Pi'de ``~/udar-pi-agent`` klonunda ``bash update.sh`` çalışır. Eski betik
``git pull --ff-only`` yapar, YENİ install.sh'yi ``--no-config`` ile çağırır, sonra
(set -e altında) ESKİ servis adıyla ``systemctl restart`` ve ``status`` çalıştırır.
Bu test o akışı aynen koşar: eski dosyalar son eski commit'ten (ESKI_COMMIT) alınır,
yerel bir bare depoya konur, klonlanır, yeni içerik "yayınlanır" ve eski update.sh
gerçekten çalışır.

Sahte olan yalnız kök ve sistem komutlarıdır: ALCA_KOK bütün yolları geçici bir
klasöre taşır; sudo, systemctl, pgrep, pkill ve apt PATH'te sahtedir. GitHub adresi
git'in ``insteadOf`` ayarıyla yerel depoya yönlenir; ağa çıkılmaz (GIT_ALLOW_PROTOCOL).
Sahte systemctl, gerçek systemd'nin bu akışta önemli davranışlarını taklit eder:
birim dosyaları ancak daemon-reload ile görülür, symlink takma addır (gerçek systemd
üzerinde de doğrulandı), bilinmeyen birim hata verir, iki ajan birlikte çalışırsa iz
bırakır.

Bu dosyada eski ad BİLEREK geçer: eski kurulumun kendisini canlandırır.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

for _ad, _alanlar in (
    ("requests", {"RequestException": Exception, "Session": object}),
    ("gpiozero", {"DigitalInputDevice": object}),
):
    if _ad not in sys.modules:
        _modul = types.ModuleType(_ad)
        for _alan, _deger in _alanlar.items():
            setattr(_modul, _alan, _deger)
        sys.modules[_ad] = _modul

import alca_pi_agent

BURASI = Path(__file__).resolve().parent
ANA_DEPO = BURASI.parent.parent
# Eski adın son commit'i. HEAD değil: bu değişiklik commit edilince HEAD yeni ada geçer.
ESKI_COMMIT = "b3230bd"
ESKI_ALT_YOL = "deploy/raspberry-pi"
ESKI_REPO_URL = "https://github.com/Enes3078/udar-pi-agent.git"
YENI_REPO_URL = "https://github.com/Enes3078/alca-pi-agent.git"
SAHTE_TOKEN = "cihaz-token-degeri-0123456789"
ESKI_KUYRUK = "/var/lib/udar-pi-agent/machine_events.sqlite3"
YENI_KUYRUK = "/var/lib/alca-pi-agent/machine_events.sqlite3"

# Eski sihirbazın yazdığı biçim; 644 olabilir, boş satır ve tırnaklı değer içerir.
ESKI_ENV = f"""# saha notu: UDAR_GPIO_BCM makineye gore
UDAR_CRM_URL=https://crm.firmaniz.com
UDAR_DEVICE_TOKEN={SAHTE_TOKEN}

UDAR_GPIO_BCM=17
UDAR_PULL_UP=true
UDAR_POLL_INTERVAL_SECONDS=0.005

UDAR_MEASUREMENT_MODE=pulse
UDAR_PULSE_EDGE=falling
UDAR_PULSE_MIN_ACTIVE_SECONDS=0.03
UDAR_LINE_ID=
UDAR_STATION_CODE=M-07
UDAR_NOTE="GPIO17 UDAR_DENEME vurus"
UDAR_HTTP_TIMEOUT=7
UDAR_QUEUE_DB={ESKI_KUYRUK}
UDAR_SEND_RETRY_MAX_SECONDS=120
"""

SAHTE_SYSTEMCTL = r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path

KOK = Path(os.environ["ALCA_KOK"])
DURUM = Path(os.environ["SAHTE_DURUM"])
BIRIMLER = KOK / "etc/systemd/system"
DOSYA = DURUM / "systemd.json"
d = json.loads(DOSYA.read_text()) if DOSYA.exists() else {"yuklu": {}, "aktif": []}
with open(DURUM / "gunluk", "a") as g:
    g.write(" ".join(sys.argv[1:]) + "\n")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
komut, ad = args[0], (re.sub(r"\.service$", "", args[1]) if len(args) > 1 else "")


def bitir(kod, mesaj=""):
    DOSYA.write_text(json.dumps(d))
    if mesaj:
        print(mesaj, file=sys.stderr)
    sys.exit(kod)


def birim():
    if ad not in d["yuklu"]:
        bitir(5, f"Unit {ad}.service not found.")
    return d["yuklu"][ad]


def baslat(gercek):
    metin = (BIRIMLER / f"{gercek}.service").read_text()
    calistir = re.search(r"^ExecStart=\S+ (\S+)", metin, re.M).group(1)
    ayar = re.search(r"^EnvironmentFile=(\S+)", metin, re.M).group(1)
    if not (KOK / calistir.lstrip("/")).exists() or not (KOK / ayar.lstrip("/")).exists():
        return
    if (DURUM / "servis-baslamaz").exists():
        return
    if d["aktif"] and gercek not in d["aktif"]:
        (DURUM / "cift-ajan").write_text(f"{gercek} basladi, calisan: {d['aktif']}\n")
    if gercek not in d["aktif"]:
        d["aktif"].append(gercek)


if komut == "daemon-reload":
    d["yuklu"] = {}
    for yol in BIRIMLER.glob("*.service"):
        if yol.is_symlink():
            hedef = re.sub(r"\.service$", "", Path(os.readlink(yol)).name)
            if (BIRIMLER / f"{hedef}.service").exists():
                d["yuklu"][yol.stem] = hedef
        else:
            d["yuklu"][yol.stem] = yol.stem
elif komut in {"start", "restart"}:
    gercek = birim()
    if komut == "restart" and gercek in d["aktif"]:
        d["aktif"].remove(gercek)
    baslat(gercek)
elif komut == "stop":
    gercek = birim()
    if gercek in d["aktif"] and not (DURUM / "eski-servis-durmaz").exists():
        d["aktif"].remove(gercek)
elif komut == "enable":
    gercek = birim()
    baglar = BIRIMLER / "multi-user.target.wants"
    baglar.mkdir(exist_ok=True)
    bag = baglar / f"{gercek}.service"
    if not bag.is_symlink():
        bag.symlink_to(f"../{gercek}.service")
elif komut == "disable":
    gercek = birim()
    bag = BIRIMLER / "multi-user.target.wants" / f"{gercek}.service"
    if bag.is_symlink():
        bag.unlink()
elif komut == "reset-failed":
    pass
elif komut in {"is-active", "status"}:
    gercek = d["yuklu"].get(ad, ad)
    if gercek not in d["aktif"]:
        bitir(3, "" if "--quiet" in sys.argv else "inactive")
    if "--quiet" not in sys.argv:
        print(f"{gercek}.service active (running)")
else:
    bitir(1, f"sahte systemctl bilmiyor: {komut}")
bitir(0)
'''

# Süreç listesi durum/surecler dosyasıdır ("pid komut satırı"). Gerçek pgrep/pkill
# KULLANILMAZ: test gerçek bir Pi'de koşarsa çalışan ajana dokunmamalı.
SAHTE_PGREP = r'''#!/usr/bin/env python3
import os, re, sys
from pathlib import Path

DURUM = Path(os.environ["SAHTE_DURUM"])
ad = Path(sys.argv[0]).name
desen = sys.argv[-1]
dosya = DURUM / "surecler"
satirlar = dosya.read_text().splitlines() if dosya.exists() else []
bulunan = [s for s in satirlar if re.search(desen, s.split(" ", 1)[1])]
with open(DURUM / "gunluk", "a") as g:
    g.write(f"{ad} {desen}\n")
if ad == "pkill":
    if not (DURUM / "surec-inatci").exists():
        dosya.write_text("".join(s + "\n" for s in satirlar if s not in bulunan))
else:
    for s in bulunan:
        print(s if "-af" in sys.argv else s.split(" ", 1)[0])
sys.exit(0 if bulunan else 1)
'''


def _git(*args, cwd=None, env=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    ).stdout.strip()


def _eski_gecmis_var():
    return shutil.which("git") is not None and subprocess.run(
        ["git", "-C", str(ANA_DEPO), "cat-file", "-e", f"{ESKI_COMMIT}:{ESKI_ALT_YOL}/update.sh"],
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _env_oku(metin):
    """systemd EnvironmentFile gibi okur (yorum/boş satır atlanır, tırnak soyulur)."""
    ayarlar = {}
    for satir in metin.splitlines():
        satir = satir.strip()
        if not satir or satir.startswith("#") or "=" not in satir:
            continue
        anahtar, deger = satir.split("=", 1)
        if len(deger) >= 2 and deger[0] == deger[-1] and deger[0] in "\"'":
            deger = deger[1:-1]
        ayarlar[anahtar] = deger
    return ayarlar


@unittest.skipUnless(
    _eski_gecmis_var(),
    f"{ESKI_COMMIT} yok (public repo, sığ klon): eski dosyalar yalnız ana depo geçmişinde",
)
class GecisTestleri(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._arsiv = tempfile.TemporaryDirectory()
        cls.eski_dosyalar = Path(cls._arsiv.name)
        arsiv = subprocess.run(
            ["git", "-C", str(ANA_DEPO), "archive", ESKI_COMMIT, ESKI_ALT_YOL],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        subprocess.run(
            ["tar", "-x", "--strip-components=2", "-C", str(cls.eski_dosyalar)],
            input=arsiv, check=True,
        )
        cls.eski_ajan = cls._eski_ajani_yukle()

    @classmethod
    def tearDownClass(cls):
        cls._arsiv.cleanup()

    @classmethod
    def _eski_ajani_yukle(cls):
        tanim = importlib.util.spec_from_file_location("eski_ajan", cls.eski_dosyalar / "udar_pi_agent.py")
        modul = importlib.util.module_from_spec(tanim)
        # @dataclass modülü sys.modules'te arar (from __future__ import annotations).
        sys.modules["eski_ajan"] = modul
        tanim.loader.exec_module(modul)
        return modul

    def setUp(self):
        self._gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self._gecici.cleanup)
        kok = Path(self._gecici.name)
        self.kok = kok / "kok"
        self.ev = kok / "ev"
        self.durum = kok / "durum"
        self.bin = kok / "bin"
        self.bare = kok / "yayin.git"
        self.yayin = kok / "yayin"
        for klasor in (self.kok / "etc/systemd/system", self.kok / "opt", self.kok / "var/lib",
                       self.ev, self.durum, self.bin, self.yayin):
            klasor.mkdir(parents=True)
        self._sahte_komutlari_yaz()
        self.gitconfig = kok / "gitconfig"
        self.gitconfig.write_text(
            "[user]\n\tname = Test\n\temail = test@example.com\n"
            "[init]\n\tdefaultBranch = main\n"
            f'[url "{self.bare}"]\n\tinsteadOf = {ESKI_REPO_URL}\n\tinsteadOf = {YENI_REPO_URL}\n',
            encoding="utf-8",
        )
        self.ortam = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("ALCA_", "UDAR_", "GIT_"))
        }
        self.ortam.update({
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.ev),
            "ALCA_KOK": str(self.kok),
            "ALCA_GECIS_BEKLEME_SN": "0",
            "SAHTE_DURUM": str(self.durum),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        })
        _git("init", "--bare", "-q", str(self.bare), env=self.ortam)
        _git("init", "-q", str(self.yayin), env=self.ortam)
        _git("remote", "add", "origin", str(self.bare), cwd=self.yayin, env=self.ortam)

    # ------------------------------------------------------------------ düzenek

    def _sahte_komutlari_yaz(self):
        betikler = {
            "sudo": '#!/bin/sh\nexec "$@"\n',
            "apt": "#!/bin/sh\nexit 0\n",
            "apt-get": "#!/bin/sh\nexit 0\n",
            "dpkg": "#!/bin/sh\nexit 0\n",
            "systemctl": SAHTE_SYSTEMCTL,
            "pgrep": SAHTE_PGREP,
            "pkill": SAHTE_PGREP,
        }
        for ad, icerik in betikler.items():
            yol = self.bin / ad
            yol.write_text(icerik, encoding="utf-8")
            yol.chmod(0o755)

    def _yayinla(self, kaynak, mesaj):
        """Klasörün içeriğini (izinleriyle) public repoya gönderilmiş gibi yayınlar."""
        for yol in self.yayin.iterdir():
            if yol.name != ".git":
                shutil.rmtree(yol) if yol.is_dir() else yol.unlink()
        for yol in Path(kaynak).iterdir():
            if yol.is_file():
                shutil.copy2(yol, self.yayin / yol.name)
        (self.yayin / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        _git("add", "-A", cwd=self.yayin, env=self.ortam)
        _git("commit", "-q", "-m", mesaj, cwd=self.yayin, env=self.ortam)
        _git("push", "-q", "-f", "origin", "HEAD:main", cwd=self.yayin, env=self.ortam)

    def _eski_kurulum(self, env_metni=ESKI_ENV, env_modu=0o644, kuyruk=True):
        """Eski install.sh'nin bıraktığı Pi: klon, ajan, servis (çalışıyor), env, kuyruk."""
        self._yayinla(self.eski_dosyalar, "eski surum")
        self.klon = self.ev / "udar-pi-agent"
        _git("clone", "-q", ESKI_REPO_URL, str(self.klon), env=self.ortam)
        # Eski bootstrap.sh bunu yapar; mod değişirse sonraki pull çakışır.
        for ad in ("install.sh", "update.sh", "diagnose_gpio.py"):
            (self.klon / ad).chmod(0o755)
        (self.kok / "opt/udar-pi-agent").mkdir()
        for ad in ("udar_pi_agent.py", "diagnose_gpio.py"):
            shutil.copy2(self.eski_dosyalar / ad, self.kok / "opt/udar-pi-agent" / ad)
        shutil.copy2(self.eski_dosyalar / "udar-pi-agent.service",
                     self.kok / "etc/systemd/system/udar-pi-agent.service")
        env_yolu = self.kok / "etc/udar-pi-agent.env"
        env_yolu.write_text(env_metni, encoding="utf-8")
        env_yolu.chmod(env_modu)
        if kuyruk:
            self._eski_kuyruk_yaz(self.kok / ESKI_KUYRUK.lstrip("/"))
        self._systemctl("daemon-reload")
        self._systemctl("enable", "udar-pi-agent")
        self._systemctl("start", "udar-pi-agent")
        self.assertEqual(self._aktifler(), ["udar-pi-agent"])
        (self.durum / "gunluk").write_text("")

    def _eski_kuyruk_yaz(self, hedef):
        """ESKİ ajanın kendi kodundan kuyruk. İkinci satır yalnız -wal dosyasında durur:
        süreç öldürülmüş gibi; dosyalar birlikte taşınmazsa o satır kaybolur."""
        hedef.parent.mkdir(parents=True, exist_ok=True)
        hazirlik = Path(self._gecici.name) / "kuyruk-hazirlik"
        hazirlik.mkdir(exist_ok=True)
        db = hazirlik / hedef.name
        kuyruk = self.eski_ajan.PersistentQueue(db)
        kuyruk.put({"idempotency_key": "BEKLEYEN-1", "token": SAHTE_TOKEN, "counter": {"total": 41.0}})
        baglanti = sqlite3.connect(db)
        baglanti.execute("PRAGMA journal_mode=WAL")
        baglanti.execute("PRAGMA wal_autocheckpoint=0")
        baglanti.execute(
            "INSERT INTO events (idempotency_key, payload, created_at, next_attempt_at) VALUES (?, ?, ?, ?)",
            ("BEKLEYEN-2", '{"idempotency_key": "BEKLEYEN-2"}', "2026-10-30T10:00:00+00:00", ""),
        )
        baglanti.commit()
        for ek in ("", "-wal", "-shm"):
            shutil.copy2(f"{db}{ek}", f"{hedef}{ek}")
        baglanti.close()
        Path(f"{hedef}.lock").write_text("1234\n")

    def _yeni_surumu_yayinla(self):
        self._yayinla(BURASI, "yeni ad")

    def _calistir(self, *komut, cwd=None):
        return subprocess.run(
            komut, cwd=cwd, env=self.ortam, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=120,
            start_new_session=True,  # denetleyici terminal yok: /dev/tty açılamaz
        )

    def _systemctl(self, *args):
        return self._calistir(str(self.bin / "systemctl"), *args)

    def _aktifler(self):
        dosya = self.durum / "systemd.json"
        return json.loads(dosya.read_text())["aktif"] if dosya.exists() else []

    def _gunluk(self):
        dosya = self.durum / "gunluk"
        return dosya.read_text().splitlines() if dosya.exists() else []

    def _yeni_env(self):
        return (self.kok / "etc/alca-pi-agent.env").read_text(encoding="utf-8")

    def _bekleyenler(self, yol):
        baglanti = sqlite3.connect(self.kok / yol.lstrip("/"))
        try:
            return [s[0] for s in baglanti.execute("SELECT idempotency_key FROM events ORDER BY id")]
        finally:
            baglanti.close()

    def _duzen_goruntusu(self):
        """Kök altındaki her yol ve içerik özeti; idempotentlik karşılaştırması için."""
        sonuc = {}
        for yol in sorted(self.kok.rglob("*")):
            ad = str(yol.relative_to(self.kok))
            if yol.is_symlink():
                sonuc[ad] = "-> " + os.readlink(yol)
            elif yol.is_file() and not ad.endswith(("-wal", "-shm", ".sqlite3", ".lock")):
                sonuc[ad] = (yol.read_bytes(), yol.stat().st_mode & 0o777)
            else:
                sonuc[ad] = "klasor" if yol.is_dir() else "kuyruk"
        return sonuc

    def _eski_akisi_kos(self):
        """Saha: ~/udar-pi-agent içinde `bash update.sh` (o anda ESKİ update.sh)."""
        return self._calistir("bash", "update.sh", cwd=self.klon)

    def assertTamam(self, sonuc):
        self.assertEqual(sonuc.returncode, 0, sonuc.stdout)

    # ------------------------------------------------------------------ asıl akış

    def test_gercek_eski_update_akisi(self):
        self._eski_kurulum()
        self.assertIn("udar-pi-agent", (self.klon / "update.sh").read_text(encoding="utf-8"))
        self._yeni_surumu_yayinla()

        sonuc = self._eski_akisi_kos()
        self.assertTamam(sonuc)
        self.assertNotIn(SAHTE_TOKEN, sonuc.stdout, "token ekrana yazılmamalı")

        # Ayarlar: yalnız önek değişti, değerler aynı (token dahil), kuyruk yolu yeni.
        beklenen = "".join(
            ("ALCA_" + s[len("UDAR_"):] if s.startswith("UDAR_") else s.replace("UDAR_", "ALCA_"))
            for s in ESKI_ENV.splitlines(keepends=True)
        ).replace(ESKI_KUYRUK, YENI_KUYRUK)
        self.assertEqual(self._yeni_env(), beklenen)
        self.assertIn('ALCA_NOTE="GPIO17 UDAR_DENEME vurus"', self._yeni_env(), "değer aynen kalır")
        self.assertIn(f"ALCA_DEVICE_TOKEN={SAHTE_TOKEN}\n", self._yeni_env())
        self.assertEqual((self.kok / "etc/alca-pi-agent.env").stat().st_mode & 0o777, 0o600)

        # Kuyruk bütün taşındı; bekleyen satırlar (-wal içindeki de) duruyor.
        self.assertFalse((self.kok / "var/lib/udar-pi-agent").exists())
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        self.assertTrue(Path(f"{self.kok}{YENI_KUYRUK}.lock").exists())
        kuyruk = alca_pi_agent.PersistentQueue(self.kok / YENI_KUYRUK.lstrip("/"))
        self.assertEqual(kuyruk.peek().payload["idempotency_key"], "BEKLEYEN-1")

        # Servis: eski durdu ve devre dışı, yeni çalışıyor, iki ajan hiç birlikte olmadı.
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])
        self.assertFalse((self.durum / "cift-ajan").exists())
        gunluk = self._gunluk()
        self.assertLess(gunluk.index("stop udar-pi-agent"), gunluk.index("start alca-pi-agent"))
        birimler = self.kok / "etc/systemd/system"
        self.assertFalse((birimler / "multi-user.target.wants/udar-pi-agent.service").exists())
        self.assertTrue((birimler / "multi-user.target.wants/alca-pi-agent.service").is_symlink())
        # Eski update.sh'nin son adımları takma ad üzerinden başarılı oldu.
        self.assertIn("restart udar-pi-agent", gunluk)
        self.assertIn("--no-pager --full status udar-pi-agent", gunluk)
        self.assertTrue((birimler / "udar-pi-agent.service").is_symlink())
        self.assertEqual(os.readlink(birimler / "udar-pi-agent.service"), "alca-pi-agent.service")

        # Eski dosyalar: uygulama kalktı, env yalnız 600 yedek olarak duruyor.
        self.assertFalse((self.kok / "opt/udar-pi-agent").exists())
        self.assertTrue((self.kok / "opt/alca-pi-agent/alca_pi_agent.py").exists())
        self.assertFalse((self.kok / "etc/udar-pi-agent.env").exists())
        yedek = self.kok / "etc/alca-pi-agent.env.gecis-yedegi"
        self.assertEqual(yedek.read_text(encoding="utf-8"), ESKI_ENV)
        self.assertEqual(yedek.stat().st_mode & 0o777, 0o600)

        # Klon yeni ada geçti, origin yeni adres, çalışma ağacı temiz (mod çakışması yok).
        yeni_klon = self.ev / "alca-pi-agent"
        self.assertFalse(self.klon.exists())
        self.assertEqual(_git("config", "--get", "remote.origin.url", cwd=yeni_klon, env=self.ortam), YENI_REPO_URL)
        self.assertEqual(_git("status", "--porcelain", cwd=yeni_klon, env=self.ortam), "")

        # Geçirilmiş env'i ajan aynen anlıyor.
        with mock.patch.dict(os.environ, _env_oku(self._yeni_env()), clear=True):
            config = alca_pi_agent.load_config()
        self.assertEqual(config.crm_url, "https://crm.firmaniz.com")
        self.assertEqual(config.device_token, SAHTE_TOKEN)
        self.assertEqual(config.gpio_bcm, 17)
        self.assertTrue(config.pull_up)
        self.assertEqual(config.pulse_edge, "falling")
        self.assertEqual(config.station_code, "M-07")
        self.assertEqual(config.timeout, 7.0)
        self.assertEqual(str(config.queue_db), YENI_KUYRUK)

    def test_ikinci_kosu_idempotent_ve_takma_ad_kalkar(self):
        self._eski_kurulum()
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        yeni_klon = self.ev / "alca-pi-agent"
        token_oncesi = self._yeni_env()

        # Sonraki güncelleme artık YENİ update.sh ile: takma ad kalkar.
        (self.durum / "gunluk").write_text("")
        sonuc = self._calistir("bash", "update.sh", cwd=yeni_klon)
        self.assertTamam(sonuc)
        self.assertNotIn("Eski adla kurulum", sonuc.stdout)
        birimler = self.kok / "etc/systemd/system"
        self.assertFalse((birimler / "udar-pi-agent.service").is_symlink())
        self.assertFalse((birimler / "udar-pi-agent.service").exists())
        self.assertIn("restart alca-pi-agent", self._gunluk())
        self.assertFalse([s for s in self._gunluk() if "udar-pi-agent" in s and not s.startswith("p")])
        self.assertEqual(self._yeni_env(), token_oncesi)
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])

        # Üçüncü koşu hiçbir şeyi değiştirmez.
        once = self._duzen_goruntusu()
        self.assertTamam(self._calistir("bash", "update.sh", cwd=yeni_klon))
        self.assertEqual(self._duzen_goruntusu(), once)
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])
        self.assertFalse((self.durum / "cift-ajan").exists())
        self.assertEqual(_git("status", "--porcelain", cwd=yeni_klon, env=self.ortam), "")

    def test_mod_izinleri_korunur(self):
        beklenen = {
            "bootstrap.sh": "100755", "diagnose_gpio.py": "100755", "install.sh": "100755",
            "update.sh": "100755", "alca_pi_agent.py": "100644", "alca-pi-agent.service": "100644",
            "alca-pi-agent.env.example": "100644", "README.md": "100644",
            "test_alca_pi_agent.py": "100644", "test_pi_gecis.py": "100644",
        }
        self._yeni_surumu_yayinla()
        agac = _git("ls-tree", "HEAD", cwd=self.yayin, env=self.ortam)
        modlar = {s.split("\t")[1]: s.split()[0] for s in agac.splitlines()}
        for ad, mod in beklenen.items():
            with self.subTest(dosya=ad):
                self.assertEqual(modlar[ad], mod)

    def test_sunucu_sozlesmesi_degismedi(self):
        """Eski ve yeni ajan birlikte yaşar: gövdedeki alan adları aynı, surum farklı."""
        eski, yeni = self.eski_ajan, alca_pi_agent
        self.assertNotEqual(eski.AGENT_VERSION, yeni.AGENT_VERSION)

        def govde(modul):
            config = modul.Config(crm_url="https://x.test", device_token="t", line_id="5", station_code="M", operator_id="O")
            yuk = modul.build_payload(config, sequence=1, delta=1.0, total=1.0, measured_at="2026-10-31T00:00:00+00:00")
            oge = modul.QueuedEvent(1, yuk, 0, "2026-10-31T00:00:00+00:00", 1.0)
            return modul.transport_payload(oge)

        eski_govde, yeni_govde = govde(eski), govde(yeni)
        self.assertEqual(set(eski_govde), set(yeni_govde))
        self.assertEqual(set(eski_govde["counter"]), set(yeni_govde["counter"]))
        self.assertEqual(set(eski_govde["gpio"]), set(yeni_govde["gpio"]))
        self.assertEqual(eski.Config(crm_url="https://x.test", device_token="t").endpoint,
                         yeni.Config(crm_url="https://x.test", device_token="t").endpoint)
        self.assertNotIn("alca", " ".join(yeni_govde).lower())
        self.assertNotIn("udar", " ".join(yeni_govde).lower())

    # ------------------------------------------------------------------ kuyruk

    def test_hedef_doluysa_kuyruk_tasinmaz_eski_yol_kalir(self):
        self._eski_kurulum()
        dolu = self.kok / "var/lib/alca-pi-agent"
        dolu.mkdir(parents=True)
        (dolu / "machine_events.sqlite3").write_text("baska kuyruk")
        self._yeni_surumu_yayinla()

        sonuc = self._eski_akisi_kos()
        self.assertTamam(sonuc)
        self.assertIn("eski kuyruk TASINMADI", sonuc.stdout)
        self.assertEqual(self._bekleyenler(ESKI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        self.assertEqual((dolu / "machine_events.sqlite3").read_text(), "baska kuyruk")
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], ESKI_KUYRUK)
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])

        # Sonraki güncellemeler de kuyruğu ve yolu değiştirmez; hedef boşalınca taşınır.
        yeni_klon = self.ev / "alca-pi-agent"
        self.assertTamam(self._calistir("bash", "update.sh", cwd=yeni_klon))
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], ESKI_KUYRUK)
        self.assertEqual(self._bekleyenler(ESKI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        (dolu / "machine_events.sqlite3").unlink()
        self.assertTamam(self._calistir("bash", "update.sh", cwd=yeni_klon))
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], YENI_KUYRUK)
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        self.assertFalse((self.kok / "var/lib/udar-pi-agent").exists())

    def test_hedef_dolu_ve_envde_yol_yoksa_eski_yol_yazilir(self):
        for satir in ("", "UDAR_QUEUE_DB=\n", 'UDAR_QUEUE_DB=""\n'):
            with self.subTest(satir=satir):
                self.setUp()
                metin = ESKI_ENV.replace(f"UDAR_QUEUE_DB={ESKI_KUYRUK}\n", satir)
                self._eski_kurulum(env_metni=metin)
                dolu = self.kok / "var/lib/alca-pi-agent"
                dolu.mkdir(parents=True)
                (dolu / "machine_events.sqlite3").write_text("baska kuyruk")
                self._yeni_surumu_yayinla()
                self.assertTamam(self._eski_akisi_kos())
                self.assertEqual(self._yeni_env().count("ALCA_QUEUE_DB="), 1)
                self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], ESKI_KUYRUK)

    def test_ozel_kuyruk_yoluna_dokunulmaz(self):
        metin = ESKI_ENV.replace(f"UDAR_QUEUE_DB={ESKI_KUYRUK}", "UDAR_QUEUE_DB=/data/ozel/kuyruk.sqlite3")
        self._eski_kurulum(env_metni=metin)
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], "/data/ozel/kuyruk.sqlite3")

    def test_kuyruk_eski_uygulama_klasorundeyse_klasor_silinmez(self):
        """Env'in gösterdiği kuyruk `/opt/udar-pi-agent` altındaysa `rm -rf` kaydı silerdi."""
        ozel = "/opt/udar-pi-agent/kuyruk.sqlite3"
        for metin in (f"UDAR_QUEUE_DB={ozel}", f'UDAR_QUEUE_DB="{ozel}"'):
            with self.subTest(metin=metin):
                self.setUp()
                self._eski_kurulum(env_metni=ESKI_ENV.replace(f"UDAR_QUEUE_DB={ESKI_KUYRUK}", metin))
                kuyruk = self.kok / "opt/udar-pi-agent/kuyruk.sqlite3"
                kuyruk.write_bytes(b"BEKLEYEN")
                self._yeni_surumu_yayinla()
                sonuc = self._eski_akisi_kos()
                self.assertTamam(sonuc)
                self.assertIn("klasor SILINMEDI", sonuc.stdout)
                self.assertEqual(kuyruk.read_bytes(), b"BEKLEYEN")
                self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], ozel)
                self.assertEqual(self._aktifler(), ["alca-pi-agent"])

    def test_envde_gecmeyen_kuyruk_dosyasi_varsa_klasor_silinmez(self):
        self._eski_kurulum()
        kalan = self.kok / "opt/udar-pi-agent/eski.sqlite3-wal"
        kalan.write_bytes(b"WAL")
        self._yeni_surumu_yayinla()
        sonuc = self._eski_akisi_kos()
        self.assertTamam(sonuc)
        self.assertIn("kuyruk dosyasi var", sonuc.stdout)
        self.assertEqual(kalan.read_bytes(), b"WAL")

    def test_kuyruksuz_eski_uygulama_klasoru_silinir(self):
        self._eski_kurulum()
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertFalse((self.kok / "opt/udar-pi-agent").exists())

    def test_yeni_depo_yoksa_origin_degismez_sonraki_guncelleme_calisir(self):
        """Depo henüz yeniden adlandırılmadı: origin çevrilseydi sonraki `git pull` dururdu."""
        self._eski_kurulum()
        self._yeni_surumu_yayinla()
        self.gitconfig.write_text(
            self.gitconfig.read_text(encoding="utf-8").replace(f"\tinsteadOf = {YENI_REPO_URL}\n", ""),
            encoding="utf-8",
        )
        sonuc = self._eski_akisi_kos()
        self.assertTamam(sonuc)
        self.assertIn("origin degistirilmedi", sonuc.stdout)
        klon = self.ev / "alca-pi-agent"
        self.assertEqual(_git("config", "--get", "remote.origin.url", cwd=klon, env=self.ortam), ESKI_REPO_URL)
        self.assertEqual(_git("pull", "--ff-only", "-q", cwd=klon, env=self.ortam), "")

    def test_tirnakli_ve_baska_dosya_adli_kuyruk_yolu_cevrilir(self):
        metin = ESKI_ENV.replace(
            f"UDAR_QUEUE_DB={ESKI_KUYRUK}", 'UDAR_QUEUE_DB="/var/lib/udar-pi-agent/q.sqlite3"'
        )
        self._eski_kurulum(env_metni=metin)
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertIn('ALCA_QUEUE_DB="/var/lib/alca-pi-agent/q.sqlite3"', self._yeni_env())

    def test_envde_yol_yoksa_kuyruk_tasinir_ondeger_yeni_yol(self):
        self._eski_kurulum(env_metni=ESKI_ENV.replace(f"UDAR_QUEUE_DB={ESKI_KUYRUK}\n", ""))
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertNotIn("QUEUE_DB", self._yeni_env())
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])

    def test_kuyruk_klasoru_yoksa_ya_da_bossa_gecis_tamamlanir(self):
        self._eski_kurulum(kuyruk=False)
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])
        self.assertTrue((self.kok / "var/lib/alca-pi-agent").is_dir())

        self.setUp()
        self._eski_kurulum(kuyruk=False)
        (self.kok / "var/lib/udar-pi-agent").mkdir()
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertFalse((self.kok / "var/lib/udar-pi-agent").exists())
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], YENI_KUYRUK)

    def test_hedef_klasor_bossa_kuyruk_tasinir(self):
        self._eski_kurulum()
        (self.kok / "var/lib/alca-pi-agent").mkdir()
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        self.assertFalse((self.kok / "var/lib/alca-pi-agent/udar-pi-agent").exists())
        self.assertFalse((self.kok / "var/lib/udar-pi-agent").exists())

    # ------------------------------------------------------------------ env

    def test_mevcut_yeni_env_ustune_yazilmaz(self):
        self._eski_kurulum()
        mevcut = "ALCA_CRM_URL=https://baska.test\nALCA_DEVICE_TOKEN=mevcut-token\n"
        (self.kok / "etc/alca-pi-agent.env").write_text(mevcut, encoding="utf-8")
        self._yeni_surumu_yayinla()
        sonuc = self._eski_akisi_kos()
        self.assertTamam(sonuc)
        self.assertEqual(self._yeni_env(), mevcut)
        self.assertIn("ustune yazilmadi", sonuc.stdout)
        self.assertEqual(
            (self.kok / "etc/alca-pi-agent.env.gecis-yedegi").read_text(encoding="utf-8"), ESKI_ENV
        )

    def test_yedek_varsa_ezilmez(self):
        self._eski_kurulum()
        (self.kok / "etc/alca-pi-agent.env.gecis-yedegi").write_text("onceki yedek", encoding="utf-8")
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertEqual((self.kok / "etc/alca-pi-agent.env.gecis-yedegi").read_text(), "onceki yedek")
        yedekler = list((self.kok / "etc").glob("alca-pi-agent.env.gecis-yedegi.*"))
        self.assertEqual(len(yedekler), 1)
        self.assertEqual(yedekler[0].read_text(encoding="utf-8"), ESKI_ENV)
        self.assertEqual(yedekler[0].stat().st_mode & 0o777, 0o600)

    def test_gecici_dosya_kalmaz(self):
        self._eski_kurulum()
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertEqual(list((self.kok / "etc").glob("*.gecis.*")), [])

    def test_sihirbaz_eski_yerde_kalan_kuyruk_yolunu_ezmez(self):
        self._eski_kurulum()
        dolu = self.kok / "var/lib/alca-pi-agent"
        dolu.mkdir(parents=True)
        (dolu / "machine_events.sqlite3").write_text("baska kuyruk")
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        yeni_klon = self.ev / "alca-pi-agent"

        # Boş satırlar Enter: her soru mevcut değeri kabul eder.
        cevaplar = Path(self._gecici.name) / "cevaplar"
        cevaplar.write_text("\n" * 40, encoding="utf-8")
        self.ortam["ALCA_TTY"] = str(cevaplar)
        sonuc = self._calistir("bash", "install.sh", "--configure", cwd=yeni_klon)
        self.assertTamam(sonuc)
        self.assertNotIn(SAHTE_TOKEN, sonuc.stdout)
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], ESKI_KUYRUK)
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_DEVICE_TOKEN"], SAHTE_TOKEN)

    # ------------------------------------------------------------------ iki ajan

    def test_eski_servis_durmazsa_hicbir_sey_tasinmaz(self):
        self._eski_kurulum()
        (self.durum / "eski-servis-durmaz").write_text("")
        self._yeni_surumu_yayinla()
        sonuc = self._eski_akisi_kos()
        self.assertNotEqual(sonuc.returncode, 0)
        self.assertIn("eski servis durmadi", sonuc.stdout)
        self.assertEqual(self._aktifler(), ["udar-pi-agent"])
        self.assertFalse((self.durum / "cift-ajan").exists())
        self.assertFalse((self.kok / "etc/alca-pi-agent.env").exists())
        self.assertEqual((self.kok / "etc/udar-pi-agent.env").read_text(encoding="utf-8"), ESKI_ENV)
        self.assertEqual(self._bekleyenler(ESKI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])

    def test_elle_baslatilmis_eski_kopya_durdurulur(self):
        self._eski_kurulum()
        surecler = self.durum / "surecler"
        surecler.write_text("777 /usr/bin/python3 /home/pi/udar_pi_agent.py\n")
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertEqual(surecler.read_text(), "")
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])

    def test_elle_baslatilmis_kopya_durmazsa_hicbir_sey_tasinmaz(self):
        self._eski_kurulum()
        (self.durum / "surecler").write_text("777 python3 ./udar_pi_agent.py\n")
        (self.durum / "surec-inatci").write_text("")
        self._yeni_surumu_yayinla()
        sonuc = self._eski_akisi_kos()
        self.assertNotEqual(sonuc.returncode, 0)
        self.assertIn("eski ajan durmadi", sonuc.stdout)
        self.assertNotIn("alca-pi-agent", self._aktifler())
        self.assertFalse((self.kok / "etc/alca-pi-agent.env").exists())
        self.assertEqual(self._bekleyenler(ESKI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])

    def test_yeni_update_iki_dosya_adini_da_arar(self):
        self._eski_kurulum()
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        # Geçişin desenine takılmayan iki satır: yalnız update.sh'nin çift ajan denetimi görür.
        (self.durum / "surecler").write_text(
            "21 /usr/bin/python3 /opt/alca-pi-agent/alca_pi_agent.py\n22 sh -c /tmp/udar_pi_agent.py\n"
        )
        sonuc = self._calistir("bash", "update.sh", cwd=self.ev / "alca-pi-agent")
        self.assertTamam(sonuc)
        self.assertIn("UYARI: 2 ajan sureci", sonuc.stdout)
        self.assertIn("/tmp/udar_pi_agent.py", sonuc.stdout)

    # ------------------------------------------------------------------ başarısızlık

    def test_yeni_servis_baslamazsa_net_hata_kuyruk_duruyor(self):
        self._eski_kurulum()
        (self.durum / "servis-baslamaz").write_text("")
        self._yeni_surumu_yayinla()
        sonuc = self._eski_akisi_kos()
        self.assertNotEqual(sonuc.returncode, 0)
        self.assertIn("alca-pi-agent ayakta kalmadi", sonuc.stdout)
        self.assertIn("journalctl -u alca-pi-agent", sonuc.stdout)
        self.assertIn("sudo systemctl restart alca-pi-agent", sonuc.stdout)
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_DEVICE_TOKEN"], SAHTE_TOKEN)
        self.assertTrue((self.kok / "opt/udar-pi-agent").exists(), "eski kod başarıdan önce silinmez")

        # Neden giderilince yeni update.sh aynı klasörden kaldığı yerden tamamlar.
        (self.durum / "servis-baslamaz").unlink()
        self.assertTamam(self._calistir("bash", "update.sh", cwd=self.klon))
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])
        self.assertFalse((self.kok / "opt/udar-pi-agent").exists())
        self.assertFalse(self.klon.exists())
        self.assertTrue((self.ev / "alca-pi-agent/.git").is_dir())
        self.assertFalse((self.kok / "etc/systemd/system/udar-pi-agent.service").is_symlink())
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])

    def test_ayarsiz_eski_kurulumda_servis_baslatilmaz(self):
        metin = ESKI_ENV.replace(f"UDAR_DEVICE_TOKEN={SAHTE_TOKEN}", "UDAR_DEVICE_TOKEN=PASTE_DEVICE_TOKEN_HERE")
        self._eski_kurulum(env_metni=metin)
        self._yeni_surumu_yayinla()
        _git("pull", "-q", "--ff-only", cwd=self.klon, env=self.ortam)
        sonuc = self._calistir("bash", "install.sh", "--no-config", cwd=self.klon)
        self.assertTamam(sonuc)
        self.assertIn("servis baslatilmadi", sonuc.stdout)
        self.assertEqual(self._aktifler(), [])

    def test_klon_tasinamazsa_devam_edilir_ve_uyarilir(self):
        self._eski_kurulum()
        (self.ev / "alca-pi-agent").mkdir()
        self._yeni_surumu_yayinla()
        sonuc = self._eski_akisi_kos()
        self.assertTamam(sonuc)
        self.assertIn("klasor tasinamadi", sonuc.stdout)
        self.assertTrue((self.klon / ".git").is_dir())
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])
        self.assertEqual(_git("config", "--get", "remote.origin.url", cwd=self.klon, env=self.ortam), YENI_REPO_URL)

    def test_baska_depoya_bakan_origin_degismez(self):
        self._eski_kurulum()
        self._yeni_surumu_yayinla()
        _git("remote", "set-url", "origin", str(self.bare), cwd=self.klon, env=self.ortam)
        self.assertTamam(self._eski_akisi_kos())
        self.assertEqual(
            _git("config", "--get", "remote.origin.url", cwd=self.ev / "alca-pi-agent", env=self.ortam),
            str(self.bare),
        )

    # ------------------------------------------------------------------ yarım geçiş

    def test_kuyruk_tasinmis_env_cevrilmemis_geciste_devam(self):
        """Geçiş kuyruğu taşıdıktan hemen sonra elektrik kesildi."""
        self._eski_kurulum()
        (self.kok / "var/lib/udar-pi-agent").rename(self.kok / "var/lib/alca-pi-agent")
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_QUEUE_DB"], YENI_KUYRUK)
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])

    def test_env_cevrilmis_yedege_alinmamis_geciste_devam(self):
        """Geçiş yeni env'i yazdıktan hemen sonra kesildi: iş tekrarlanmaz, değerler aynı."""
        self._eski_kurulum()
        (self.kok / "var/lib/udar-pi-agent").rename(self.kok / "var/lib/alca-pi-agent")
        yeni = "".join(
            "ALCA_" + s[len("UDAR_"):] if s.startswith("UDAR_") else s
            for s in ESKI_ENV.splitlines(keepends=True)
        )
        (self.kok / "etc/alca-pi-agent.env").write_text(yeni, encoding="utf-8")
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        ayar = _env_oku(self._yeni_env())
        self.assertEqual(ayar["ALCA_QUEUE_DB"], YENI_KUYRUK)
        self.assertEqual(ayar["ALCA_DEVICE_TOKEN"], SAHTE_TOKEN)
        self.assertFalse((self.kok / "etc/udar-pi-agent.env").exists())

    def test_ek_servis_ayari_tasinir(self):
        self._eski_kurulum()
        ek = self.kok / "etc/systemd/system/udar-pi-agent.service.d"
        ek.mkdir()
        (ek / "override.conf").write_text("[Service]\nRestartSec=9\n")
        self._yeni_surumu_yayinla()
        self.assertTamam(self._eski_akisi_kos())
        self.assertFalse(ek.exists())
        self.assertEqual(
            (self.kok / "etc/systemd/system/alca-pi-agent.service.d/override.conf").read_text(),
            "[Service]\nRestartSec=9\n",
        )

    # ------------------------------------------------------------------ taze kurulum

    def _eski_ad_izi(self):
        izler = [str(y.relative_to(self.kok)) for y in self.kok.rglob("*") if "udar" in y.name.lower()]
        for dosya in (self.kok / "etc/alca-pi-agent.env", self.kok / "etc/systemd/system/alca-pi-agent.service"):
            if "udar" in dosya.read_text(encoding="utf-8").lower():
                izler.append(f"icerik: {dosya.name}")
        izler += [s for s in self._gunluk() if "udar" in s and not s.startswith("p")]
        return izler

    def test_taze_kurulumda_eski_ad_izi_olusmaz(self):
        self._yeni_surumu_yayinla()
        klon = self.ev / "alca-pi-agent"
        _git("clone", "-q", YENI_REPO_URL, str(klon), env=self.ortam)
        sonuc = self._calistir("bash", "install.sh", "--no-config", cwd=klon)
        self.assertTamam(sonuc)
        self.assertEqual(self._eski_ad_izi(), [])
        self.assertTrue((self.kok / "opt/alca-pi-agent/alca_pi_agent.py").exists())
        self.assertTrue((self.kok / "var/lib/alca-pi-agent").is_dir())
        self.assertNotIn("Eski adla", sonuc.stdout)
        self.assertEqual(self._aktifler(), [], "taze kurulum ayarsız servis başlatmaz (eskisi gibi)")
        self.assertEqual(_git("config", "--get", "remote.origin.url", cwd=klon, env=self.ortam), YENI_REPO_URL)

    def test_bootstrap_taze_kurulum_yeni_depodan(self):
        self._yeni_surumu_yayinla()
        sonuc = self._calistir("bash", str(BURASI / "bootstrap.sh"))
        self.assertTamam(sonuc)
        self.assertIn("Terminal bulunamadi", sonuc.stdout)
        klon = self.ev / "alca-pi-agent"
        self.assertTrue((klon / ".git").is_dir())
        self.assertFalse((self.ev / "udar-pi-agent").exists())
        self.assertEqual(_git("config", "--get", "remote.origin.url", cwd=klon, env=self.ortam), YENI_REPO_URL)
        self.assertEqual(_git("status", "--porcelain", cwd=klon, env=self.ortam), "")
        self.assertEqual(self._eski_ad_izi(), [])

    def test_bootstrap_eski_klonu_tasir_ve_gecisi_yapar(self):
        self._eski_kurulum()
        self._yeni_surumu_yayinla()
        sonuc = self._calistir("bash", str(BURASI / "bootstrap.sh"))
        self.assertTamam(sonuc)
        klon = self.ev / "alca-pi-agent"
        self.assertFalse(self.klon.exists())
        self.assertEqual(_git("config", "--get", "remote.origin.url", cwd=klon, env=self.ortam), YENI_REPO_URL)
        self.assertEqual(_git("status", "--porcelain", cwd=klon, env=self.ortam), "")
        self.assertEqual(_env_oku(self._yeni_env())["ALCA_DEVICE_TOKEN"], SAHTE_TOKEN)
        self.assertEqual(self._bekleyenler(YENI_KUYRUK), ["BEKLEYEN-1", "BEKLEYEN-2"])
        self.assertEqual(self._aktifler(), ["alca-pi-agent"])

    # ------------------------------------------------------------------ ajan tek başına

    def test_yeni_ajan_eski_env_ve_eski_yollarla_calisir(self):
        """Elle kopyalanırsa: install.sh hiç çalışmadan, eski ayar + eski kuyruk."""
        self._eski_kurulum()
        ayar = _env_oku(ESKI_ENV)
        ayar.pop("UDAR_QUEUE_DB")
        eski_db = self.kok / ESKI_KUYRUK.lstrip("/")
        with mock.patch.dict(os.environ, ayar, clear=True), \
                mock.patch.object(alca_pi_agent, "KUYRUK_DOSYASI", str(self.kok / YENI_KUYRUK.lstrip("/"))), \
                mock.patch.object(alca_pi_agent, "ESKI_KUYRUK_DOSYASI", str(eski_db)):
            config = alca_pi_agent.load_config()
        self.assertEqual(config.device_token, SAHTE_TOKEN)
        self.assertEqual(config.queue_db, eski_db)
        kuyruk = alca_pi_agent.PersistentQueue(config.queue_db)
        self.assertEqual(kuyruk.peek().payload["idempotency_key"], "BEKLEYEN-1")
        govde = alca_pi_agent.transport_payload(kuyruk.peek())
        self.assertEqual(govde["agent_version"], alca_pi_agent.AGENT_VERSION)
        self.assertIsNone(govde["measured_by_version"])


if __name__ == "__main__":
    unittest.main()
