# PRD: Dashboard monitoringu GPW Radar na M5Stack Cardputer ADV

**Status:** projekt (do zatwierdzenia)
**Data:** 2026-08-28
**Projekt:** `monitor-cardputter`
**Systemy powiązane:** [`gpw_radar`](/home/michal/PycharmProjects/gpw_radar) (źródło metryk), [`kmp_monitoring`](/home/michal/AndroidStudioProjects/kmp_monitoring) (wzorzec backendu)

---

## 1. Wprowadzenie / przegląd

`gpw_radar` to nocny pipeline decyzyjny dla akcji z GPW, który po każdym przebiegu wypycha
27 własnych serii metryk (`custom.googleapis.com/gpw_*`) do **Google Cloud Monitoring**.
Dziś, żeby dowiedzieć się „czy nocny przebieg się udał i czy egzekucja przepuszcza zlecenia",
trzeba otworzyć przeglądarkę i dashboard Grafany. To bariera na tyle duża, że stan systemu
sprawdza się rzadko i zwykle dopiero wtedy, gdy coś już nie działa.

Ten projekt buduje **fizyczny dashboard biurkowy** na M5Stack Cardputer ADV: urządzenie leży
na biurku, jest stale zasilane i w kółko przewija kilka ekranów z najważniejszymi liczbami.
Stan „wszystko dobrze / coś jest nie tak" ma być czytelny kątem oka, bez sięgania po cokolwiek.

Urządzenie jest **wyłącznie do odczytu**. Nie wykonuje żadnych akcji na infrastrukturze,
nie składa zleceń, nie zatrzymuje maszyn. Zgubiony lub skradziony Cardputer nie może zrobić
niczego poza pokazaniem liczb.

Ponieważ Cloud Monitoring API wymaga uwierzytelnienia OAuth2 (podpis RS256 kluczem Service
Accountu), między urządzeniem a GCP staje **nowa Cloud Function (proxy)**. Proxy odpytuje
Cloud Monitoring, redukuje odpowiedź do małego JSON-a i liczy przyrosty. Klucz Service
Accountu nigdy nie trafia na urządzenie.

### Słowniczek

| Pojęcie | Znaczenie |
|---|---|
| **nightly** | Nocny przebieg pipeline'u `gpw nightly` (7 kroków: ingest, factors, events, sentiment, analyze, resolve, report) |
| **M8 / egzekucja** | Moduł składania zleceń przez IBKR (Interactive Brokers) |
| **Gateway** | IB Gateway — lokalna aplikacja brokera, przez którą idą zlecenia |
| **inhibitor** | Zatrzask (`execution_inhibitor`) blokujący składanie zleceń; zdejmuje go człowiek |
| **rekoncyliacja** | `exec-reconcile` — uzgodnienie stanu zleceń z brokerem |
| **GAUGE** | Typ metryki w Cloud Monitoring: wartość w punkcie czasu (nie licznik przyrostowy) |
| **proxy** | Nowa Cloud Function pośrednicząca między Cardputerem a Cloud Monitoring |

---

## 2. Cele

- **C-1** Stan nocnego przebiegu jest widoczny z odległości biurka w **poniżej 2 sekund** —
  bez dotykania urządzenia, bez odblokowywania, bez otwierania przeglądarki.
- **C-2** Awaria nocnego przebiegu (brak udanego nightly ponad próg czasu) jest sygnalizowana
  **jednoznacznie i niemożliwa do przeoczenia** — pełnoekranowy stan alarmowy.
- **C-3** Urządzenie **nigdy nie prezentuje starych danych jako aktualnych**. Brak łączności
  daje osobny ekran, a nie ostatnią znaną wartość bez adnotacji.
- **C-4** Żaden sekret (klucz Service Accountu, token) nie jest przechowywany w repozytorium
  ani w artefaktach builda; klucz do GCP nie opuszcza chmury.
- **C-5** Urządzenie jest **wyłącznie do odczytu** — nie istnieje ścieżka kodu zmieniająca
  cokolwiek w `gpw_radar`, GCP czy u brokera.
- **C-6** Koszt zapytań do Cloud Monitoring pozostaje pomijalny dzięki cache'owi w proxy
  i adaptacyjnej częstotliwości odpytywania.

---

## 3. Historyjki użytkownika

Historyjki są ułożone w kolejności implementacji. Każda jest na tyle mała, żeby zmieścić się
w jednej sesji pracy. Podział na dwa komponenty: **proxy** (US-1xx, Python/GCP) i
**firmware** (US-2xx, C++/PlatformIO).

### Część A — Proxy (Cloud Function)

#### US-101: Szkielet Cloud Function z uwierzytelnianiem Bearer

**Opis:** Jako operator chcę, żeby proxy odrzucało nieuwierzytelnione żądania, żeby endpoint
wystawiony publicznie nie ujawniał niczego o moim systemie.

**Kryteria akceptacji:**
- [ ] Nowy katalog `proxy/` w repozytorium `monitor-cardputter`, Python 3.12,
      Cloud Functions gen2, struktura wzorowana na `kmp_monitoring/backend`
- [ ] Trasa `GET /v1/dashboard`; każda inna ścieżka daje `404`, zła metoda daje `405`
      z nagłówkiem `Allow`
- [ ] Żądanie bez nagłówka `Authorization` lub ze źle sformułowanym nagłówkiem → `401`
      z kodem `unauthorized`
- [ ] Żądanie z niepoprawnym tokenem → `403` z kodem `forbidden`
- [ ] Token odczytywany **per żądanie** z Secret Managera (`versions/latest`); w kodzie,
      zmiennych środowiskowych i argumentach wdrożenia jest wyłącznie **identyfikator**
      sekretu (`AUTH_SECRET_ID`), nigdy jego wartość
- [ ] Porównanie tokenu jest odporne na atak czasowy (`hmac.compare_digest`)
- [ ] Jednolita koperta błędu: `{"error": {"code": ..., "message": ...}}` ze **stałymi**
      komunikatami — żaden tekst od dostawcy, id projektu ani stos wywołań nie trafia do ciała
- [ ] `ruff check` przechodzi; testy jednostkowe pokrywają 401/403/404/405

#### US-102: Odczyt metryk nightly z Cloud Monitoring

**Opis:** Jako operator chcę, żeby proxy potrafiło odczytać stan nocnego przebiegu z Cloud
Monitoring, bo to najważniejsza informacja w całym systemie.

**Kryteria akceptacji:**
- [ ] Proxy odpytuje Cloud Monitoring API (`MetricServiceClient.list_time_series`)
      o serie: `gpw_nightly_last_success_timestamp_seconds`, `gpw_nightly_duration_seconds`,
      `gpw_step_status` (etykieta `step`, 7 wartości)
- [ ] Uwierzytelnianie przez Application Default Credentials; runtime Service Account ma
      **wyłącznie** rolę `roles/monitoring.viewer` (bez Owner, bez Editor, bez uprawnień zapisu)
- [ ] Dla każdej serii pobierany jest **ostatni punkt** z okna 48 h
- [ ] Brak punktu w oknie jest reprezentowany jawnie jako `null`, nigdy jako `0`
      (dokumentacja `gpw_radar`: „brak punktu znaczy »nie było przebiegu«, a nie »zero«")
- [ ] Wartość `gpw_nightly_last_success_timestamp_seconds` jest traktowana jako czas uniksowy
      w **sekundach** (nie milisekundach — ta pomyłka dała już w Grafanie „57 lat temu")
- [ ] Testy z fałszywym klientem Cloud Monitoring (bez wywołań sieciowych)

#### US-103: Odczyt metryk egzekucji M8 z przyrostem 24 h

**Opis:** Jako operator chcę wiedzieć, czy egzekucja przepuszcza zlecenia i ile ich faktycznie
zawarto od wczoraj, a nie tylko ile ich było od początku istnienia systemu.

**Kryteria akceptacji:**
- [ ] Proxy odczytuje serie M8: `gpw_broker_session_up`, `gpw_exec_inhibitor_active`,
      `gpw_exec_last_reconcile_timestamp_seconds`, `gpw_broker_orders` (etykieta `status`)
      oraz `gpw_open_positions`
- [ ] Dla `gpw_broker_orders` proxy zwraca dla statusów `filled`, `unfilled` i `rejected`
      **zarówno sumę kumulatywną, jak i przyrost za 24 h** (`{"total": 143, "delta24h": 3}`)
- [ ] Przyrost liczony jako różnica ostatniego punktu i ostatniego punktu sprzed ≥24 h;
      gdy brakuje punktu odniesienia, `delta24h` to `null`, **nigdy `0`** — „nie wiem" i
      „nic się nie zdarzyło" muszą być rozróżnialne
- [ ] `gpw_broker_session_up` i `gpw_exec_inhibitor_active` pochodzą z `exec-health-probe`,
      który biegnie co 3 h; punkt starszy niż **36 h** jest zwracany jako `null`
      (odpowiednik „brak probe" z dashboardu Grafany), a nie jako ostatnia znana wartość
- [ ] Testy pokrywają: brak punktu odniesienia dla delty, punkt probe starszy niż 36 h,
      pełną dziedzinę dziewięciu statusów zleceń

#### US-104: Złożenie odpowiedzi `/v1/dashboard` i cache

**Opis:** Jako operator chcę, żeby Cardputer dostawał jeden mały JSON, a proxy nie waliło
w płatne API przy każdym odświeżeniu.

**Kryteria akceptacji:**
- [ ] `GET /v1/dashboard` zwraca jeden obiekt JSON zgodny ze schematem z sekcji 7.2
- [ ] Odpowiedź zawiera `generatedAt` (ISO 8601, UTC, sufiks `Z`) — czas złożenia odpowiedzi
- [ ] Odpowiedź zawiera **wyliczony przez proxy** `overallStatus`: `ok` | `alarm` | `unknown`
      (reguły w FR-14) — urządzenie nie powtarza tej logiki
- [ ] Odpowiedź w formacie zwartym (`separators=(",", ":")`), **poniżej 4 KB** dla typowych danych
- [ ] Wynik jest cache'owany w pamięci procesu przez **60 s**; żądania w tym oknie są
      obsługiwane z cache'u bez wywołania Cloud Monitoring
- [ ] Nagłówek odpowiedzi `Cache-Control: max-age=60`
- [ ] Awaria Cloud Monitoring API → `502` z kodem `monitoring_api_error`, nigdy częściowa
      odpowiedź udająca komplet
- [ ] Testy: trafienie w cache nie wywołuje klienta, wygaśnięcie cache'u wywołuje

#### US-105: Skrypt wdrożeniowy i dokumentacja proxy

**Opis:** Jako operator chcę móc wdrożyć proxy jedną komendą i wiedzieć, jakie uprawnienia
nadać, żeby konfiguracja nie została odtworzona z pamięci za pół roku.

**Kryteria akceptacji:**
- [ ] `proxy/deploy.sh` (wzorowany na `kmp_monitoring/backend/deploy.sh`) wdraża funkcję
      gen2 z podanymi `GCP_PROJECT_ID`, `GCP_REGION`, `AUTH_SECRET_ID`
- [ ] `proxy/README.md` zawiera: wymagane role runtime SA, procedurę utworzenia sekretu
      z tokenem (`openssl rand`), przykładowe `curl` z tokenem oraz opis kontraktu odpowiedzi
- [ ] Skrypt **nie** przyjmuje wartości tokenu w argumentach ani zmiennych środowiskowych
- [ ] `.gitignore` wyklucza wszelkie pliki `*.json` z kluczami SA

### Część B — Firmware (Cardputer)

#### US-201: Szkielet projektu PlatformIO i połączenie WiFi

**Opis:** Jako użytkownik chcę, żeby urządzenie po włączeniu samo połączyło się z siecią
i pokazało, że to zrobiło.

**Kryteria akceptacji:**
- [ ] `platformio.ini` dla środowiska `cardputer` (ESP32-S3, framework Arduino,
      biblioteki M5Unified/M5GFX/M5Cardputer/ArduinoJson — obecne już w `.pio/libdeps`)
- [ ] `src/secrets.h` (w `.gitignore`) z SSID, hasłem WiFi, URL proxy i tokenem Bearer;
      w repozytorium jest wyłącznie `src/secrets.h.example` z pustymi wartościami
- [ ] Po starcie urządzenie łączy się z WiFi i wyświetla ekran startowy ze stanem połączenia
- [ ] Utrata WiFi uruchamia automatyczne ponawianie z narastającym odstępem
      (1 s → 2 s → 4 s → … → maks. 60 s), bez restartu urządzenia i bez blokowania rysowania
- [ ] Firmware kompiluje się i uruchamia na urządzeniu

#### US-202: Klient HTTPS proxy i parsowanie odpowiedzi

**Opis:** Jako użytkownik chcę, żeby urządzenie pobierało dane z proxy w sposób bezpieczny
i odporny na śmieciową odpowiedź.

**Kryteria akceptacji:**
- [ ] Urządzenie wykonuje `GET /v1/dashboard` przez **HTTPS** z nagłówkiem
      `Authorization: Bearer <token>` z `secrets.h`
- [ ] Certyfikat serwera jest weryfikowany wobec wbudowanego certyfikatu root CA
      (Google Trust Services); weryfikacja **nie jest** wyłączona
- [ ] Timeout żądania: 10 s
- [ ] Odpowiedź parsowana przez ArduinoJson do struktury w pamięci
- [ ] Odpowiedź niebędąca `200`, niepoprawny JSON i timeout są rozróżnialnymi stanami błędu,
      każdy z własnym komunikatem
- [ ] Żaden token ani fragment nagłówka `Authorization` nie trafia na `Serial` ani na ekran

#### US-203: Model stanu i świeżości danych

**Opis:** Jako użytkownik chcę mieć pewność, że liczba na ekranie jest aktualna, bo dashboard
pokazujący wczorajszy stan jako dzisiejszy jest gorszy niż brak dashboardu.

**Kryteria akceptacji:**
- [ ] Firmware trzyma ostatni udany odczyt wraz ze znacznikiem czasu jego pobrania
- [ ] Stan urządzenia to jedna z wartości: `Ok`, `Alarm`, `Disconnected`, `Starting`
- [ ] Po **trzech** kolejnych nieudanych próbach pobrania urządzenie przechodzi w
      `Disconnected` i **przestaje pokazywać liczby** (patrz US-206)
- [ ] Pojedyncza nieudana próba nie zmienia jeszcze ekranu — chwilowy błąd sieci nie miga
      użytkownikowi w oczy
- [ ] Wiek danych liczony jest od **czasu pobrania**, a nie od wyniku ostatniej próby
      (udany odczyt sprzed dwóch godzin to nadal dane sprzed dwóch godzin)

#### US-204: Ekran 1 — kondycja nocnego przebiegu

**Opis:** Jako użytkownik chcę zobaczyć jednym rzutem oka, czy nocny przebieg się udał.

**Kryteria akceptacji:**
- [ ] Ekran pokazuje: wiek ostatniego **udanego** nightly w formie czytelnej dla człowieka
      („8 h temu", „wczoraj 03:14"), czas trwania przebiegu oraz liczbę kroków OK z siedmiu
      (np. „kroki 7/7")
- [ ] Gdy którykolwiek krok ma status `0`, ekran wymienia **nazwy** kroków, które padły
- [ ] Wiek ostatniego udanego nightly jest największym elementem na ekranie
- [ ] Brak danych dla metryki jest rysowany jako `—`, nigdy jako `0`
- [ ] Zweryfikowane na fizycznym urządzeniu (zdjęcie/opis ekranu w komentarzu do zadania)

#### US-205: Ekran 2 — egzekucja i pozycje

**Opis:** Jako użytkownik chcę wiedzieć, czy zlecenia mogą iść do brokera i czy dziś coś
faktycznie zawarto.

**Kryteria akceptacji:**
- [ ] Ekran pokazuje stan egzekucji wyliczony z pary metryk:
      `session_up=1` **i** `inhibitor=0` → „PRZEPUSZCZA";
      `session_up=1` i `inhibitor=1` → „ZABLOKOWANA (zatrzask)";
      `session_up=0` → „GATEWAY LEŻY"; brak świeżego probe → „BRAK PROBE"
- [ ] Ekran pokazuje zawarte zlecenia jako sumę i przyrost: `filled 143 (+3)`
- [ ] Ekran pokazuje odrzucone (`rejected`) z przyrostem — niezerowy przyrost jest wyróżniony
- [ ] Ekran pokazuje liczbę otwartych pozycji (`gpw_open_positions`) oraz wiek ostatniej
      rekoncyliacji
- [ ] Przy liczbie otwartych pozycji widnieje adnotacja o jej **źródle czasowym**, bo pochodzi
      z nightly, a liczby zleceń z `exec-reconcile` — to dwa różne harmonogramy
- [ ] `delta24h` równe `null` jest rysowane jako `(?)`, nigdy jako `(+0)`
- [ ] Zweryfikowane na fizycznym urządzeniu

#### US-206: Ekran „brak łączności"

**Opis:** Jako użytkownik chcę, żeby urządzenie wprost powiedziało, że nie wie, co się dzieje,
zamiast pokazywać ostatnią znaną liczbę.

**Kryteria akceptacji:**
- [ ] Po trzech nieudanych próbach ekran zostaje **w całości** zastąpiony komunikatem
      o braku łączności — żadna liczba metryki nie jest widoczna
- [ ] Ekran podaje przyczynę na poziomie zrozumiałym dla człowieka: brak WiFi / proxy nie
      odpowiada / proxy odrzuciło token / błędna odpowiedź
- [ ] Ekran pokazuje, kiedy ostatnio udało się pobrać dane („ostatnie dane: 14 min temu")
- [ ] Ekran nie jest czerwony — brak łączności to **nie** alarm o stanie systemu (patrz FR-15)
- [ ] Po udanym pobraniu urządzenie samo wraca do normalnej rotacji ekranów

#### US-207: Ekran alarmowy nocnego przebiegu

**Opis:** Jako użytkownik chcę, żeby nieudany nocny przebieg rzucał się w oczy z drugiego
końca pokoju.

**Kryteria akceptacji:**
- [ ] Gdy wiek ostatniego udanego nightly przekroczy **26 h**, urządzenie przechodzi w
      pełnoekranowy stan alarmowy: czerwone tło, duży napis, wiek ostatniego udanego przebiegu
- [ ] Stan alarmowy **wstrzymuje rotację** ekranów — alarm nie może zniknąć sam z siebie
      po kilku sekundach
- [ ] Z ekranu alarmowego można ręcznie przejść dalej klawiszem, ale po 30 s bez naciśnięcia
      urządzenie wraca do alarmu
- [ ] Próg 26 h jest **stałą nazwaną w jednym miejscu** w kodzie, z komentarzem wyjaśniającym
      dobór (doba + 2 h marginesu na opóźniony start)
- [ ] Alarm jest **cichy** — bez dźwięku i bez diody (poza zakresem, patrz sekcja 5)
- [ ] Zweryfikowane na fizycznym urządzeniu przez podanie spreparowanej odpowiedzi proxy

#### US-208: Rotacja ekranów i sterowanie klawiaturą

**Opis:** Jako użytkownik chcę, żeby urządzenie samo przewijało ekrany, ale żebym mógł
zatrzymać się na tym, który mnie w danej chwili interesuje.

**Kryteria akceptacji:**
- [ ] Ekrany przewijają się automatycznie w pętli co **8 s**
- [ ] Strzałki lewo/prawo przechodzą do poprzedniego/następnego ekranu i **wstrzymują**
      automatyczną rotację
- [ ] Wstrzymana rotacja wznawia się po 60 s bez naciśnięcia klawisza
- [ ] Wskaźnik pozycji w rotacji (np. `• ○ ○`) jest widoczny na każdym ekranie
- [ ] Klawisz `r` wymusza natychmiastowe odświeżenie danych, niezależnie od harmonogramu
- [ ] Żaden klawisz nie uruchamia akcji zmieniającej cokolwiek poza urządzeniem
- [ ] Zweryfikowane na fizycznym urządzeniu

#### US-209: Adaptacyjna częstotliwość odpytywania

**Opis:** Jako operator chcę świeże dane wtedy, gdy się zmieniają, i oszczędność wtedy,
gdy się nie zmieniają.

**Kryteria akceptacji:**
- [ ] W godzinach sesji giełdowej (dni robocze 8:30–17:30 czasu lokalnego) odpytywanie
      co **90 s**
- [ ] Poza sesją i w weekendy odpytywanie co **15 min**
- [ ] Czas lokalny pobierany przez NTP po połączeniu z WiFi; przy nieudanej synchronizacji
      urządzenie stosuje **częstszy** wariant (bezpieczniejszy kierunek pomyłki)
- [ ] Okno sesji i oba odstępy są stałymi nazwanymi w jednym miejscu w kodzie
- [ ] Ręczne odświeżenie (US-208) działa niezależnie od harmonogramu

#### US-210: Ekran 3 — informacje o urządzeniu

**Opis:** Jako operator chcę móc zdiagnozować samo urządzenie, gdy dane wyglądają dziwnie.

**Kryteria akceptacji:**
- [ ] Ekran pokazuje: siłę sygnału WiFi (RSSI), adres IP, czas od startu urządzenia,
      wiek ostatniego udanego pobrania, liczbę nieudanych prób od startu, stan baterii
- [ ] Ekran pokazuje wersję firmware'u (stała w kodzie)
- [ ] Ekran **nie** pokazuje SSID, tokenu ani pełnego URL-a proxy — sam nagłówek `Serial`
      też ich nie ujawnia
- [ ] Zweryfikowane na fizycznym urządzeniu

---

## 4. Wymagania funkcjonalne

### Proxy

- **FR-1** Proxy MUSI wystawiać dokładnie jedną trasę odczytową: `GET /v1/dashboard`.
- **FR-2** Proxy MUSI odrzucić każde żądanie bez poprawnego tokenu Bearer (`401` przy braku
  lub złym formacie nagłówka, `403` przy złej wartości).
- **FR-3** Proxy MUSI odczytywać wartość tokenu z Secret Managera przy każdym żądaniu;
  w konfiguracji wdrożenia może występować wyłącznie identyfikator sekretu.
- **FR-4** Proxy MUSI odpytywać Cloud Monitoring o następujące serie:
  `gpw_nightly_last_success_timestamp_seconds`, `gpw_nightly_duration_seconds`,
  `gpw_step_status{step}`, `gpw_broker_session_up`, `gpw_exec_inhibitor_active`,
  `gpw_exec_last_reconcile_timestamp_seconds`, `gpw_broker_orders{status}`,
  `gpw_open_positions`.
- **FR-5** Proxy MUSI zwracać `null` dla każdej serii bez punktu w oknie 48 h. Wartość `0`
  jest zarezerwowana dla faktycznie zmierzonego zera.
- **FR-6** Proxy MUSI zwracać dla statusów `filled`, `unfilled` i `rejected` zarówno sumę
  kumulatywną, jak i przyrost za 24 h; przy braku punktu odniesienia przyrost to `null`.
- **FR-7** Proxy MUSI zwracać `null` dla `gpw_broker_session_up` i `gpw_exec_inhibitor_active`,
  gdy ostatni punkt jest starszy niż 36 h.
- **FR-8** Proxy MUSI cache'ować złożoną odpowiedź przez 60 s w pamięci procesu.
- **FR-9** Proxy MUSI zwracać błędy w jednolitej kopercie `{"error": {"code", "message"}}`
  ze stałymi komunikatami, bez tekstu od dostawcy, identyfikatora projektu i stosu wywołań.
- **FR-10** Runtime Service Account proxy MUSI mieć wyłącznie `roles/monitoring.viewer`
  oraz dostęp do odczytu jednego skonfigurowanego sekretu. NIE MOŻE mieć uprawnień zapisu
  metryk, dostępu do Compute Engine ani ról Owner/Editor.
- **FR-11** Proxy NIE MOŻE wystawiać żadnej trasy zapisującej ani modyfikującej cokolwiek.

### Firmware

- **FR-12** Urządzenie MUSI odpytywać proxy co 90 s w oknie sesji (dni robocze 8:30–17:30)
  i co 15 min poza nim.
- **FR-13** Urządzenie MUSI rotować ekrany co 8 s; strzałki wstrzymują rotację na 60 s.
- **FR-14** Stan ogólny (`overallStatus`) MUSI być wyliczany **przez proxy** wg reguł:
  - `alarm` — wiek `gpw_nightly_last_success_timestamp_seconds` przekracza 26 h **albo**
    metryka nie ma punktu w oknie 48 h;
  - `unknown` — nie da się odczytać metryki nightly z powodu błędu API;
  - `ok` — w pozostałych przypadkach.
- **FR-15** Urządzenie MUSI rozróżniać **trzy** sytuacje i nigdy ich nie mylić:
  („system jest w złym stanie" = ekran alarmowy czerwony),
  („nie mam łączności / nie wiem" = ekran braku łączności, nieczerwony),
  („wszystko dobrze" = normalna rotacja).
- **FR-16** Urządzenie MUSI wejść w stan `Disconnected` po trzech kolejnych nieudanych próbach
  i wtedy NIE MOŻE wyświetlać żadnej wartości metryki.
- **FR-17** Stan alarmowy MUSI wstrzymać automatyczną rotację ekranów.
- **FR-18** Urządzenie MUSI weryfikować certyfikat TLS proxy wobec wbudowanego root CA.
- **FR-19** Urządzenie NIE MOŻE wypisywać tokenu, hasła WiFi ani nagłówka `Authorization`
  na `Serial` ani na ekran.
- **FR-20** Urządzenie NIE MOŻE mieć żadnej ścieżki kodu wysyłającej żądanie inne niż
  `GET /v1/dashboard`.
- **FR-21** Sekrety MUSZĄ znajdować się wyłącznie w `src/secrets.h`, wykluczonym z repozytorium;
  repozytorium zawiera jedynie `src/secrets.h.example`.

---

## 5. Poza zakresem (Non-Goals)

Świadomie **nie** wchodzi do tej wersji:

- **Jakiekolwiek akcje zmieniające stan** — brak `stop-all`, brak zatrzymywania maszyn,
  brak zdejmowania zatrzasku egzekucji, brak składania i anulowania zleceń. Urządzenie jest
  wyłącznie do odczytu (C-5).
- **Sygnalizacja dźwiękiem i diodą RGB** — mimo że sprzęt ma głośnik 1 W i ES8311.
  Alarm jest wyłącznie wizualny. (Możliwe rozszerzenie — sekcja 9.)
- **Pozostałe 19 z 27 metryk** — koszty LLM i EODHD, świeżość danych per dataset, bramka
  jakości, ESPI, sentyment, NAV i alfa portfela, budżet rotacji, shortlista, rekomendacje.
  Ekran 240×135 nie ma na to miejsca, a dashboard Grafany już je pokrywa.
- **Konfiguracja przez interfejs urządzenia** — brak ekranu ustawień, brak portalu WiFi.
  Konfiguracja odbywa się przez `secrets.h` i ponowne wgranie firmware'u.
- **Historia i wykresy** — urządzenie pokazuje stan bieżący, nie przebiegi w czasie.
- **Praca na baterii jako scenariusz podstawowy** — urządzenie leży na biurku pod zasilaniem.
  Stan baterii jest tylko wyświetlany (US-210); nie ma optymalizacji zużycia ani trybu uśpienia.
- **Dostęp spoza sieci domowej** — mimo że Cloud Function jest osiągalna publicznie,
  scenariuszem docelowym jest biurko.
- **Zmiany w `gpw_radar`** — ten projekt **czyta** metryki, które już istnieją. Nie dodaje
  nowych serii, nie zmienia emitera, nie dotyka pipeline'u.
- **Powiadomienia push, e-mail, Telegram** — `gpw_radar` ma już własny kanał alertów
  (`health` → Telegram); to urządzenie go nie duplikuje.

---

## 6. Rozważania projektowe (UI)

### 6.1 Ograniczenia ekranu

Ekran to **ST7789V2, 240×135 px, 1.14"** — bardzo mało miejsca. Praktyczne konsekwencje:

- Przy czcionce czytelnej z odległości biurka mieści się **6–8 linii tekstu**.
- Jedna liczba na ekranie może być duża („bohater"), reszta musi być mała.
- Kolor jest dostępny, ale przy tej wielkości niesie więcej informacji niż tekst — stan
  koduj kolorem **i** słowem, nigdy samym kolorem.

### 6.2 Hierarchia ekranów

```
Rotacja (co 8 s):   [1] Nightly  →  [2] Egzekucja  →  [3] Urządzenie  →  (pętla)

Stan alarmowy:      pełny ekran, czerwony, rotacja wstrzymana
Brak łączności:     pełny ekran, nieczerwony, bez żadnych liczb metryk
```

### 6.3 Szkic ekranu 1 (nightly)

```
┌──────────────────────────────┐
│ NIGHTLY              • ○ ○   │
│                              │
│      8 h temu                │  ← element „bohater"
│      (03:14, 28.08)          │
│                              │
│ czas: 12 min   kroki: 7/7    │
└──────────────────────────────┘
```

W wariancie z błędem kroku ostatnia linia zmienia się na `kroki: 5/7 — sentiment, analyze`.

### 6.4 Szkic ekranu 2 (egzekucja)

```
┌──────────────────────────────┐
│ EGZEKUCJA            ○ • ○   │
│                              │
│   PRZEPUSZCZA                │  ← zielony; alternatywy w US-205
│                              │
│ filled 143 (+3)  rej 2 (+0)  │
│ pozycje 7*  rekon. 2 h temu  │
└──────────────────────────────┘
      * z nocnego przebiegu
```

### 6.5 Kodowanie kolorem

| Stan | Kolor | Słowo |
|---|---|---|
| Wszystko dobrze | zielony | „PRZEPUSZCZA", „7/7" |
| Uwaga (niezerowe odrzucenia, krok padł) | żółty | nazwa problemu |
| Alarm (nightly przeterminowany) | czerwony, pełny ekran | „BRAK NIGHTLY" |
| Brak wiedzy (`null`, brak probe) | szary | `—`, „BRAK PROBE" |
| Brak łączności | niebieski/szary, pełny ekran | „BRAK ŁĄCZNOŚCI" |

Kolor szary dla „nie wiem" jest istotny: `—` na szaro i `0` na zielono muszą wyglądać
zupełnie inaczej.

---

## 7. Rozważania techniczne

### 7.1 Architektura

```
   gpw nightly / exec-*          (istnieje, bez zmian)
          │ emit_metrics()
          ▼
   Google Cloud Monitoring       (istnieje: 27 serii custom.googleapis.com/gpw_*)
          │ list_time_series()   ← roles/monitoring.viewer
          ▼
   Cloud Function „proxy"        (NOWE — US-1xx)
          │ GET /v1/dashboard    ← HTTPS + Bearer, cache 60 s
          ▼
   Cardputer ADV                 (NOWE — US-2xx)
```

Proxy istnieje z dwóch powodów, oba twarde: (1) Cloud Monitoring API wymaga OAuth2 z podpisem
RS256 kluczem Service Accountu — realizowalne na ESP32-S3, ale wymagałoby wgrania klucza SA
na urządzenie leżące na biurku; (2) surowa odpowiedź `list_time_series` to duży JSON dla
każdej serii z osobna — parsowanie tego na mikrokontrolerze byłoby marnotrawstwem pamięci
i pasma.

### 7.2 Kontrakt odpowiedzi `/v1/dashboard`

```json
{
  "generatedAt": "2026-08-28T19:42:11Z",
  "overallStatus": "ok",
  "nightly": {
    "lastSuccessEpochSeconds": 1787012040,
    "durationSeconds": 743.2,
    "stepsOk": 7,
    "stepsTotal": 7,
    "failedSteps": []
  },
  "execution": {
    "sessionUp": true,
    "inhibitorActive": false,
    "lastReconcileEpochSeconds": 1787019000,
    "orders": {
      "filled":   {"total": 143, "delta24h": 3},
      "unfilled": {"total": 12,  "delta24h": 0},
      "rejected": {"total": 2,   "delta24h": null}
    },
    "openPositions": 7
  }
}
```

Zasady kontraktu:

- **`null` znaczy „nie wiem", nigdy „zero".** Dotyczy każdego pola liczbowego.
  Brak punktu w oknie, probe starszy niż 36 h i brak punktu odniesienia dla delty —
  wszystkie dają `null`.
- **`failedSteps` to lista nazw**, nie liczba — urządzenie ma je wypisać (US-204).
- **Znaczniki czasu są w sekundach uniksowych** (zgodnie z tym, co emituje `gpw_radar`).
  To ta sama pułapka, która w Grafanie dała „57 lat temu" — wiek liczymy w proxy i w firmware
  jednolicie w sekundach.
- **`overallStatus` liczy proxy** (FR-14). Urządzenie tylko go czyta. Dzięki temu zmiana progu
  alarmu nie wymaga ponownego wgrywania firmware'u.

### 7.3 Wybory już przesądzone

Środowisko builda jest w repozytorium **już przygotowane** (`.pio/`, `.venv/`) — obecne są
biblioteki M5Cardputer, M5Unified, M5GFX, ArduinoJson, IRremote oraz zbudowany `firmware.bin`.
Brakuje `platformio.ini` i `src/`. Wniosek: **stack jest rozstrzygnięty** (PlatformIO + Arduino
+ M5Unified) i PRD go nie rewiduje — US-201 ma odtworzyć `platformio.ini` zgodny z tym,
co już leży w `.pio/libdeps/cardputer`.

### 7.4 Sprzęt

M5Stack Cardputer ADV: ESP32-S3FN8 (dwurdzeniowy, 240 MHz), 8 MB PSRAM, 8 MB Flash,
WiFi 2.4 GHz, ekran ST7789V2 240×135, klawiatura 56 klawiszy, bateria 1750 mAh,
głośnik 1 W + ES8311, mikrofon MEMS, akcelerometr BMI270, nadajnik IR.

Zasoby są z dużym zapasem wobec potrzeb (jedno żądanie HTTPS na 90 s i rysowanie tekstu).
Wąskim gardłem jest **wyłącznie powierzchnia ekranu**, nie pamięć ani procesor.

### 7.5 Wzorce do naśladowania

`kmp_monitoring/backend` to sprawdzony w produkcji wzorzec dokładnie tego, co robi proxy:
Cloud Functions gen2, token Bearer z Secret Managera odczytywany per żądanie, jednolita
koperta błędu ze stałymi komunikatami, minimalne role runtime SA, `deploy.sh` + `Makefile`.
**Skopiuj strukturę i zasady bezpieczeństwa; nie kopiuj logiki domenowej** (tamten backend
dotyczy Compute Engine, nie Cloud Monitoring).

Z `kmp_monitoring` warto też przenieść koncepcyjnie **model świeżości danych**
(`Current` / `Aging` / `Stale`) — z tą różnicą, że tutaj podjęto decyzję o twardszym wariancie:
brak świeżych danych daje **osobny ekran**, a nie wyszarzone liczby (FR-15, FR-16).

### 7.6 Ryzyka

| Ryzyko | Skutek | Ograniczenie |
|---|---|---|
| Użycie placeholdera `gpw-radar` z `.env.example` zamiast realnego `zippy-shift-411110` | Proxy odpytuje nieistniejący projekt i zwraca same `null` | Identyfikator potwierdzony (P-1); wpisać go w konfigurację wdrożenia, nie kopiować z `.env.example` |
| Sekundy vs milisekundy w znacznikach czasu | Ekran pokazuje absurdalny wiek („57 lat temu") | Kontrakt jawnie nazywa pole `...EpochSeconds`; test jednostkowy na realistycznym znaczniku |
| Mylenie `0` z „brak danych" | Fałszywe „wszystko zero, czyli spokój" przy martwym pipelinie | `null` w kontrakcie (FR-5), szare `—` na ekranie (6.5), test na serii bez punktów |
| `open_positions` i `broker_orders` z różnych harmonogramów | Użytkownik zestawia liczby o różnym wieku | Adnotacja źródła na ekranie 2 (US-205) |
| Cloud Monitoring nalicza opłaty za odczyty | Rosnący koszt przy częstym odpytywaniu | Cache 60 s w proxy (FR-8) + adaptacyjna częstotliwość (FR-12) |
| Token w `secrets.h` trafia do repozytorium | Wyciek dostępu do proxy | `.gitignore` już zawiera `src/secrets.h`; w repo tylko `.example` (FR-21) |

---

## 8. Miary sukcesu

- **M-1** Stan nocnego przebiegu odczytywalny z odległości biurka w **poniżej 2 s**,
  bez dotykania urządzenia.
- **M-2** Nieudany nocny przebieg jest zauważony **tego samego dnia rano** — mierzone
  praktyką: przez pierwszy miesiąc żadna awaria nightly nie zostaje odkryta dopiero
  z dashboardu Grafany albo z alertu Telegrama.
- **M-3** **Zero** przypadków, w których urządzenie pokazało nieaktualne dane bez adnotacji
  (weryfikowane przez celowe odcięcie WiFi i proxy w testach akceptacyjnych).
- **M-4** Koszt zapytań do Cloud Monitoring **poniżej 1 USD/miesiąc**.
- **M-5** Przegląd kodu potwierdza, że w firmwarze nie istnieje żadne żądanie HTTP inne niż
  `GET /v1/dashboard` (FR-20).
- **M-6** Urządzenie pracuje **7 dni bez restartu** i bez zawieszenia rysowania
  (test wytrzymałościowy przed uznaniem projektu za gotowy).

---

## 9. Pytania otwarte

- ~~**P-1** Który projekt GCP zawiera serie `gpw_*`?~~ **ROZSTRZYGNIĘTE:**
  `zippy-shift-411110` (potwierdzone w `gpw_radar/.env`). Wartość `gpw-radar`
  w `.env.example` to placeholder, nie realny projekt.
- **P-2** Czy proxy ma być wdrożone w tym samym projekcie co metryki, czy w osobnym?
  Ten sam projekt upraszcza uprawnienia; osobny lepiej izoluje.
- **P-3** Czy 26 h to właściwy próg alarmu? Zależy od faktycznej godziny startu
  `gpw-nightly.timer` — jeśli przebieg startuje 03:00 i trwa ~12 min, 26 h daje alarm
  ok. 05:00 następnej doby.
- **P-4** Czy przyrost 24 h dla zleceń ma być liczony w oknie kroczącym (ostatnie 24 h),
  czy od północy? „Dziś zawarto" sugeruje intuicyjnie od północy; okno kroczące jest prostsze
  i stabilniejsze. **Propozycja: okno kroczące**, chyba że zdecydujesz inaczej.
- **P-5** Czy `unfilled` ma być w ogóle pokazywane na ekranie 2? Kontrakt je zwraca,
  ale miejsca na ekranie jest mało — być może wystarczą `filled` i `rejected`.
- **P-6** Co ma się stać, gdy proxy zwróci `overallStatus: "unknown"` (błąd API po stronie
  Cloud Monitoring, ale łączność z proxy działa)? Propozycja: ekran taki jak przy braku
  łączności, z innym komunikatem — do potwierdzenia.
- **P-7** Czy projekt ma mieć vault Obsidian z dokumentacją (jak `gpw_radar`), czy wystarczy
  README? Przy dwóch komponentach i jednym użytkowniku README + ten PRD prawdopodobnie wystarczą.

---

## 10. Kolejność implementacji

Proxy przed firmware'em — urządzenie nie ma czego czytać, dopóki kontrakt nie stoi.

```
US-101 → US-102 → US-103 → US-104 → US-105        (proxy: gotowy, wdrożony endpoint)
                                        │
                                        ▼
US-201 → US-202 → US-203 → US-204 → US-206        (firmware: pierwszy użyteczny ekran)
                                        │
                                        ▼
US-205 → US-207 → US-208 → US-209 → US-210        (pełna funkcjonalność)
```

Po US-204 i US-206 urządzenie jest **już użyteczne**: pokazuje stan nightly i uczciwie
przyznaje się do braku łączności. To dobry moment na pierwsze wystawienie na biurko
i weryfikację, czy szkice ekranów sprawdzają się w praktyce, zanim powstanie reszta.
