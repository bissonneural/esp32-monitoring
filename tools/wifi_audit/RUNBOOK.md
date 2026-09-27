# RUNBOOK — audyt siły hasła WPA2 z handshake Pwnagotchi

Operacyjna instrukcja krok po kroku dla `crack_pwnagotchi.py`.
Cel: sprawdzić, jak szybko dałoby się złamać hasło **własnej** sieci Wi-Fi.

> ⚠️ **Autoryzacja.** Uruchamiaj wyłącznie na własnych sieciach lub z pisemną zgodą
> właściciela. Łamanie cudzego Wi-Fi to nieautoryzowany dostęp — czyn karalny.

---

## 0. Wymagania (jednorazowo)

| Sprawdzenie | Polecenie | Oczekiwane |
|---|---|---|
| hashcat | `hashcat --version` | `v5.1.0` (lub nowszy) |
| GPU widziane | `hashcat -I \| grep Name` | nazwa Twojej karty (np. GTX 750 Ti) |
| Python | `python3 --version` | 3.x |

Brak hashcata: `sudo apt install -y hashcat`

Uwaga: hashcat 5.1.0 obsługuje tryb `-m 2500` (`.hccapx`), którego skrypt używa.
Nowszy format `-m 22000` wymaga hashcat 6.x (w tym środowisku niedostępny).

---

## 1. Wejdź do katalogu narzędzia

```bash
cd /home/michal/projects/monitor-cardputter/tools/wifi_audit
```

Zmienna pomocnicza (podmień na swój plik):

```bash
F=/home/michal/Dokumenty/test_tama/8C_E5_EF_42_4C_1C_SwinkaMango24_ID_591.pcap
```

---

## 2. Szybki test: czy KONKRETNE hasło pasuje  (sekundy)

Sprawdza jedno podane hasło — **nie zgaduje**. Kończy w kilka sekund. To poprawne.

```bash
python3 crack_pwnagotchi.py "$F" --only-verify 'Lalkaa1!'
```

- `... -> PASUJE`, kod wyjścia `0` → hasło pasuje do handshake'a.
- `... -> NIE pasuje`, kod `1` → to nie jest hasło tej sieci (albo zły handshake).

Użycie: potwierdzenie, że plik/handshake jest dobry, lub sprawdzenie hipotezy hasła.

---

## 3. Pełny audyt: ZGADYWANIE hasła  (minuty–godziny)

Buduje hccapx i uruchamia kaskadę ataków hashcatem na GPU. **To trwa.**

```bash
python3 crack_pwnagotchi.py "$F"
```

### Kaskada ataków (kolejność)

| # | Atak | Pokrywa | Orient. czas (GTX 750 Ti) |
|---|---|---|---|
| 1 | słownik + best64 | hasła słownikowe | zależy od słownika (tylko z `--wordlist`) |
| 2 | 8 cyfr | PIN, daty, numer telefonu | ~28 min |
| 3 | wielka+5 małych+2 cyfry | `Xxxxx99` | ~2 godz |
| 4 | wielka+5 małych+cyfra+znak | `Xxxxx9!` ← typowy wzorzec | ~10 min–3 godz |
| 5 | 8 znaków alfanum | reszta 8-znakowych | bardzo długo |

Atak przerywa się po pierwszym trafieniu. Wynik na końcu:
`[+] HASLO ZLAMANE: ...` lub `[-] Nie zlamano ... Haslo odporne na typowe wzorce`.

---

## 4. Z własnym słownikiem (skuteczniejsze)

Słownik jest uruchamiany **pierwszy** (z regułami best64, jeśli są):

```bash
python3 crack_pwnagotchi.py "$F" --wordlist /sciezka/rockyou.txt
```

---

## 5. Uruchomienie w tle (zalecane dla pełnego audytu)

Bo maski liczą długo — żeby nie blokować terminala:

```bash
nohup python3 crack_pwnagotchi.py "$F" > audyt.log 2>&1 &
echo "PID: $!"

tail -f audyt.log        # podgląd na żywo; Ctrl-C wychodzi z podglądu, atak leci dalej
```

Zatrzymanie ataku w tle:

```bash
pkill -f crack_pwnagotchi        # albo: kill <PID>
pkill -f 'hashcat -m 2500'       # dobicie samego hashcata, jeśli został
```

Podgląd, czy GPU faktycznie liczy:

```bash
nvidia-smi                       # Util powinien być ~90–100% podczas maski
pgrep -af 'hashcat -m 2500'      # pokaże aktualną maskę
```

---

## 6. Gdy ESSID nie wykrywa się z nazwy pliku

Skrypt czyta nazwę sieci z formatu `MAC_ESSID_ID.pcap`. Inny schemat → podaj ręcznie:

```bash
python3 crack_pwnagotchi.py plik.pcap --essid MojaSiec
```

Objaw braku: `BLAD: nie wykryto ESSID — podaj --essid NAZWA.`

---

## 7. Interpretacja wyniku

- **Złamane w minuty** → hasło **słabe**. Ma przewidywalny wzorzec (słowo + cyfry/znak).
  Zmień je (patrz niżej).
- **Nie złamane po kaskadzie** → hasło oparło się typowym wzorcom. Dobry znak, ale nie
  gwarancja: silniejszy słownik/dłuższy czas mogą dać inny wynik.

### Uwaga o "czasie teoretycznym"

Pełny brute-force 8-znakowego hasła ze wszystkich znaków (95⁸) to ~1800 lat na tej
karcie — ale **nikt tak nie atakuje**. Realny atak to słowniki i maski wzorcowe, które
biją hasła typu `Slowo1!` w minuty. Siła hasła = **brak wzorca**, nie liczba znaków
specjalnych.

### Mocne hasło Wi-Fi

- passphrase z 4+ niepowiązanych słów: `beczka-turkus-wiszacy-parometr`
- albo losowe 12–16 znaków
- bez słownikowego rdzenia i przewidywalnej końcówki (`...1!`, `...123`)

---

## 8. Troubleshooting (błędy napotkane realnie)

| Objaw | Przyczyna | Rozwiązanie |
|---|---|---|
| Pełny atak kończy w kilka sekund z "brak trafienia" | zła kolejność argumentów hashcat (maska brana za plik) | naprawione w skrypcie; jeśli wróci — hccapx musi być PRZED maską |
| `--only-verify` PASUJE, ale kaskada nic nie łamie | j.w. — hashcat nic nie liczył | sprawdź `audyt.log` czy jest `[!] BLAD hashcat` |
| MIC nigdy się nie zgadza mimo dobrego hasła | pcap ma KILKA handshake'ów; zmieszane nonce z dwóch sesji | skrypt paruje nonce w obrębie sesji — używaj go, nie ręcznej konwersji |
| hashcat: `Exhausted, Recovered 0/1` dla poprawnego hasła | pole MIC w EAPOL nie było wyzerowane | skrypt zeruje MIC w hccapx (offset 81, 16 bajtów) |
| Ostrzeżenia `Kernel exec timeout` / `wrongdriver` | stary hashcat na nowym sterowniku | nieszkodliwe; skrypt używa `--force` |
| `BLAD: brak hashcat w PATH` | hashcat niezainstalowany | `sudo apt install -y hashcat` |

---

## Powiązane

- `README.md` — opis narzędzia i wnioski o sile haseł
- `crack_pwnagotchi.py` — kod (parser pcap, budowa hccapx, kaskada)
