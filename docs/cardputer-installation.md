# Instalacja na Cardputerze

## Stan wdrożenia — 27 września 2026

Monitor 0.1.5 działa na M5Stack Cardputer ADV z rzeczywistymi danymi
(`USE_MOCK_DATA=0`). Backend `gpw-cardputer-proxy` jest wdrożony w projekcie
`zippy-shift-411110`, w regionie `europe-central2`. Konfiguracja Wi-Fi, adres
proxy i token znajdują się w ignorowanym `src/secrets.h`.

Potwierdzono na urządzeniu połączenie Wi-Fi, synchronizację czasu, pierwsze
pobranie danych bez błędów i zmianę strony podczas automatycznej rotacji.
Backend zwrócił status `ok`; żądanie bez tokenu otrzymało HTTP 401.
Po aktualizacji 27 września potwierdzono zwykłą rotację obu ekranów w niedzielę
przy wieku ostatniego udanego nightly około 40 godzin.

## Pamięć urządzenia

Zachowano bootloader, Launcher, kod m5gotchi i partycje danych. Rezerwę
partycji m5gotchi zmniejszono, a monitor umieszczono w osobnej partycji:

| Aplikacja | Partycja | Początek | Rozmiar |
| --- | --- | --- | --- |
| Launcher | `app0` / test | `0x10000` | 1344 KiB |
| m5gotchi | `m5gotc` / OTA 0 | `0x170000` | 2048 KiB |
| GPW Radar | `GPW-Radar` / OTA 1 | `0x370000` | 1088 KiB |

Sumy kontrolne potwierdziły poprawność zapisów i zachowanie pozostałych
aplikacji, NVS, `storage` oraz `spiffs`. Pełna kopia pamięci sprzed instalacji
i pliki weryfikacji są w `~/.local/share/gpw-cardputer/backups/20260923/`.
Kopia zawiera konfigurację urządzenia; należy przechowywać ją prywatnie.

Aktualizacja 0.1.5 zapisała wyłącznie obraz aplikacji pod adresem `0x370000`;
esptool potwierdził sumę kontrolną zapisu. Kopia poprzedniej partycji GPW-Radar,
tablica partycji i wyniki weryfikacji są w
`~/.local/share/gpw-cardputer/backups/20260927-0.1.5/`.

## Aktualizacje i obsługa

Buduj poleceniem `pio run -e cardputer`. Do instalacji przez Launcher używaj
samego `.pio/build/cardputer/firmware.bin`, wybierając partycję `GPW-Radar`.
Obecna partycja mieści maksymalnie 1 114 112 bajtów; większy obraz wymaga
ponownego przydzielenia miejsca w Launcherze.

Standardowe `pio run --target upload` zapisuje także domyślną tablicę partycji
i zastępuje układ Launchera. Nie używaj go do aktualizacji tej instalacji.

Klawisz `r` odświeża dane, `h` otwiera pomoc, a `,` i `.` zmieniają stronę.
Od wersji 0.1.1 rotacja i strzałki obejmują tylko NIGHTLY oraz EGZEKUCJĘ.
Ekran URZĄDZENIE otwiera się klawiszem `i`; ponowne `i` lub `Esc` wraca do
pulpitu. Informacje o urządzeniu pozostają otwarte podczas odświeżania danych.
Od wersji 0.1.2 `[` przyciemnia wyświetlacz, a `]` go rozjaśnia, na każdym
ekranie. Regulacja działa krokami po 20 w zakresie 20–255; ustawienie jest
zapamiętywane po restarcie. Bieżący poziom procentowy pokazuje ekran URZĄDZENIE.
Od wersji 0.1.3 błąd pierwszego pobrania od razu pokazuje BRAK ŁĄCZNOŚCI.
Po błędach aplikacja ponawia pobieranie po 30 sekundach; udane pobranie przywraca
zwykły harmonogram. Ekran startowy zawsze zawiera komunikat.
Od wersji 0.1.4 NIGHTLY pokazuje wynik ostatniej raportowanej próby (`OK`, `BLAD`
lub `---`), jej czas, wiek ostatniego sukcesu, kroki, łączną liczbę ESPI oraz
pozostałe kredyty Firecrawl i limit planu. ESPI i kredyty są ostatnim pomiarem
raportowanym przez nightly, nie odczytem na żywo. Pominięte kroki nie są błędami.
EGZEKUCJA pokazuje osobno stan sesji brokera, wymaganie logowania/2FA i blokadę
egzekucji. Nie dodano kolejnej strony. Brak lub przeterminowanie metryki to `---`.
Od wersji 0.1.5 aktywna blokada egzekucji (`blokada: TAK`) jest czerwona.
Wdrożone proxy pomija alarm BRAK NIGHTLY w niedzielę według `Europe/Warsaw`;
firmware uwzględnia tę decyzję także w kolorze wieku ostatniego sukcesu.
Od poniedziałku o północy ponownie obowiązuje próg 26 godzin.
Automatyczne pobieranie następuje co 90 sekund w godzinach sesji i co 15 minut
poza nimi. Szczegóły backendu opisuje [proxy/README.md](../proxy/README.md).
