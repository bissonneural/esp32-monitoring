# wifi_audit — audyt siły hasła WPA2 z handshake Pwnagotchi

Narzędzie do sprawdzania, jak odporne jest hasło **własnej** sieci Wi-Fi na złamanie.
Bierze jeden plik zrzutu Pwnagotchi i próbuje odgadnąć hasło hashcatem na GPU.

## ⚠️ Autoryzacja

Używaj **wyłącznie** na własnych sieciach lub z pisemną zgodą właściciela.
Łamanie hasła cudzej sieci Wi-Fi to nieautoryzowany dostęp — czyn karalny.

## Wymagania

- `hashcat` (tryb `-m 2500`) — `sudo apt install -y hashcat`
- GPU z działającym backendem (CUDA/OpenCL). Sprawdź: `hashcat -I`
- Python 3

## Użycie

```bash
# pełny audyt — kaskada ataków (słownik -> maski wzorcowe)
python3 crack_pwnagotchi.py 80_AF_..._MojaSiec_ID_321.pcap

# z własnym słownikiem (uruchamiany pierwszy, z regułami best64 jeśli są)
python3 crack_pwnagotchi.py plik.pcap --wordlist /sciezka/rockyou.txt

# gdy ESSID nie da się wykryć z nazwy pliku
python3 crack_pwnagotchi.py plik.pcap --essid MojaSiec

# tylko sprawdź jedno hasło (bez łamania) — zwraca kod 0 gdy pasuje
python3 crack_pwnagotchi.py plik.pcap --only-verify 'Haslo123!'
```

Obsługiwane wejście: `.pcap`, `.pcapng`, `.hc22000`.

## Jak to działa

1. **Parsuje zrzut** i wyciąga wszystkie kompletne handshake'i (M1+M2).
2. **Wybiera spójny handshake** — ANONCE (z M1) i SNONCE (z M2) z **tej samej** sesji.
3. **Buduje hccapx** z wyzerowanym polem MIC w EAPOL.
4. **Uruchamia kaskadę** ataków hashcatem od najtańszych/najskuteczniejszych.
5. Podaje hasło (jeśli złamane) lub informację, że hasło oparło się typowym wzorcom.

## Dwie pułapki, które to narzędzie rozwiązuje

Zdobyte "na twardo" podczas audytu — bez nich łamanie cicho zawodzi mimo poprawnych danych:

1. **Wiele handshake'ów w jednym pcap.** Pwnagotchi często zapisuje kilka sesji.
   Jeśli zmiesza się ANONCE z jednej sesji z SNONCE/MIC z innej, MIC **nigdy** się nie
   zgadza — nawet dla poprawnego hasła. Trzeba parować nonce w obrębie jednej sesji.

2. **hccapx wymaga WYZEROWANEGO pola MIC w EAPOL.** hashcat sam wpisuje tam liczony
   MIC. Jeśli zostawić oryginalny MIC w środku ramki EAPOL, hashcat nie złamie nawet
   prawidłowego hasła (`Status: Exhausted, Recovered 0/1`).

## Wnioski o sile haseł (z realnego testu na GTX 750 Ti, ~57k H/s)

Hasło `Lalkaa1!` (8 znaków: wielka litera + słowo + cyfra + znak):

| Wiedza atakującego              | Czas złamania (ta karta) |
|---------------------------------|--------------------------|
| zna wzorzec (L…!, litery+cyfra) | ~8,5 min                 |
| pełny brute-force 95⁸           | ~1 800 lat (teoretyczny) |

Sedno: "1800 lat" to **teoria**, której nikt nie atakuje. Realny atakujący puszcza
słowniki i maski wzorcowe — a hasło ze słownikowym rdzeniem i przewidywalną końcówką
(`slowo` + `1!`) pada w **minuty**, mimo pozorów złożoności.

**Mocne hasło Wi-Fi** = brak wzorca: losowe 12–16 znaków albo passphrase z 4+
niepowiązanych słów (np. `beczka-turkus-wiszacy-parometr`). Wtedy słowniki i maski
są bezużyteczne, a brute-force wykracza poza rozsądny horyzont czasowy.
