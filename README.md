# MikroTik QuickSet Plus

Desktopová aplikace pro Windows, která rychle vygeneruje výchozí konfiguraci MikroTik
RouterOS **v6 i v7** a umí ji rovnou aplikovat na zařízení po síti. Nahrazuje Winbox
QuickSet bez jeho omezení:

- WAN může být **libovolný port**, ne jen ether1,
- plnohodnotná volba **protocol-mode** bridge (none / stp / rstp / mstp),
- změna **system identity**,
- firemní **hardening blok**, který jde v aplikaci upravit a uložit do profilu,
- **HW offload** bridge a **FastTrack** podle toho, co zařízení skutečně umí,
- vyhledání zařízení v síti (MNDP) ještě před připojením.

Aplikace funguje i **offline**: skript jde jen vygenerovat do souboru nebo schránky.

---

## Spuštění

Stačí spustit `MikrotikQuickSetPlus.exe`. Python není potřeba, nic se neinstaluje.

Aplikace si ukládá data do `%LOCALAPPDATA%\MikrotikQuickSetPlus\`:

| Složka | Obsah |
|---|---|
| `profiles\` | uložené profily (JSON, **bez hesel**) |
| `logs\` | průběh práce, `mqs-RRRRMMDD.log` (hesla jsou maskovaná) |
| `backups\` | zálohy stažené ze zařízení před aplikací |

## Jak se aplikace používá

1. **Zařízení**
   - Klikněte na *Vyhledat v síti* a dvojklikem zařízení převezměte do formuláře,
     nebo zadejte IP adresu ručně.
   - Zadejte uživatele a heslo a klikněte na *Připojit*.
   - Aplikace načte verzi, model, porty, switch čipy, balíčky a služby. V hlavičce okna
     se zobrazí dialekt RouterOS **v6** nebo **v7**. U známých modelů se pod detekcí
     vypíšou poznámky z databáze zařízení: kolik má switch čipů, které porty na nich jsou
     a co daný čip neoffloaduje.
   - Zařízení bez použitelné IP adresy připojíte tlačítkem *Připojit přes MAC*
     (viz [Připojení přes MAC adresu](#připojení-přes-mac-adresu)).
   - Bez zařízení vyberte vpravo cílovou verzi a vypište porty (offline režim).
2. **Základ**
   - Vyplňte identity.
   - Vyberte režim *Router*, *Bridge / AP* nebo *Vlastní* a klikněte na *Předvyplnit*.
     Režim jen vyplní formulář, všechno jde dál upravit.
   - Nastavte WAN: libovolný port a typ DHCP, statická IP nebo PPPoE.
3. **LAN / Bridge**
   - Nastavte bridge, protocol-mode, členské porty (WAN port je vyloučený automaticky),
     IP adresu a DHCP server.
   - *Adresace bridge* je **Statická**, **DHCP client**, nebo **Žádná**. U statické adresy jde
     zadat i **bránu**; z ní se vygeneruje výchozí trasa
     `/ip route add dst-address=0.0.0.0/0 gateway=…`. Díky tomu se i zařízení v režimu
     Bridge / AP, které nemá vlastní WAN, dostane na DNS, NTP a SNMP mimo svoji síť.
     Pokud je zapnutá WAN, výchozí trasu přidává už ona a aplikace na druhou upozorní.
   - Tabulka HW offloadu ukazuje u každého portu, zda poběží v hardwaru, a proč.
4. **Firewall**: základní filtr, správa z WAN a FastTrack. Pravidla jsou seřazená jako
   defconf (established → invalid → ICMP → LAN → správa z WAN → drop všeho, co nepřišlo
   z LAN) a každé má komentář `quickset: …`. Správa z WAN otevře api-ssl, SSH, Winbox,
   SNMP a discovery (MNDP) na portech, které nastaví hardening blok; je-li zadaná
   management podsíť, jen z ní. Při aplikaci na běžící konfiguraci se pravidla
   z dřívějšího běhu nahradí a filtr se zařadí nad pravidla, která už na zařízení jsou.
   Při zapnutí FastTracku se aplikace zeptá na tvarování provozu.
5. **Služby a hardening**: DNS, SSH port, SNMP, NTP a časové pásmo ve formuláři, pod nimi
   celý blok jako text.
6. **Náhled**: skript pro v6, v7 nebo obě vedle sebe. Tlačítko *Kopírovat* vloží skript do
   schránky (lze ho vložit rovnou do terminálu Winboxu), *Uložit .rsc* ho uloží do souboru.
7. **Aplikovat**: vyberte režim, zkontrolujte upozornění, projděte dry-run a potvrďte.

Chybně vyplněná pole se hned zbarví červeně s konkrétní hláškou. Dokud konfigurace není
v pořádku, nejde aplikovat.

### Režimy aplikace

| Režim | Co se stane |
|---|---|
| Jen vygenerovat | skript se uloží do souboru, na síť se nesahá |
| Aplikovat na běžící konfiguraci | skript se nahraje a spustí přes `/import`, přidá se k tomu, co na zařízení už je. Objekty, které na zařízení už jsou (bridge, porty, seznamy rozhraní, adresy, pool, DHCP, výchozí trasa, SNMP community, NTP servery), skript místo `add` upraví přes `set`, nebo je vynechá; pravidla firewallu z dřívějšího běhu nahradí. Stejný profil jde tedy aplikovat opakovaně. Náhled ukáže už upravený skript. |
| Reset + aplikovat | `/system reset-configuration no-defaults=yes skip-backup=no run-after-reset=…`: zařízení se restartuje a po startu samo naimportuje skript. Pro potvrzení je nutné opsat identity zařízení a zadat nové heslo pro `admin`. Skript se po dokončení sám smaže. |

Než aplikace cokoliv změní, vždy:

- uloží na zařízení zálohu `pre-quickset-<datum-čas>.backup` a export
  `pre-quickset-<datum-čas>.rsc` a nabídne jejich stažení,
- zobrazí přesně to, co se pošle (dry-run), a počká na potvrzení,
- volitelně naplánuje **rollback pojistku**. Pokud do N minut nekliknete na *Potvrdit, že
  konfigurace funguje*, zařízení obnoví zálohu a restartuje se. Úloha nejdřív smaže sama
  sebe, takže se nemůže opakovat.

Po aplikaci se aplikace sama přepojí (SSH je nově na portu 122) a ukáže checklist
OK/CHYBA: identity, služby, bridge, adresa a skutečný stav HW offloadu.

### Připojení

| Transport | Port | Poznámka |
|---|---|---|
| API-SSL | 8729 | výchozí, zůstává dostupné i po aplikaci hardening bloku |
| SSH | 22, po aplikaci 122 | záloha za API-SSL, slouží i k přenosu souborů |

Pokud api-ssl nemá přiřazený certifikát (výchozí stav RouterOS), spojení je šifrované,
ale identita zařízení není ověřená. Stavový řádek zobrazí *nešifrovaná identita
protistrany*. S certifikátem a importovanou CA se zobrazí *ověřený certifikát*.

### Připojení přes MAC adresu

Zařízení se dá nastavit i bez použitelné IP adresy, podobně jako ve Winboxu. Vyhledejte
zařízení v síti, vyberte řádek v tabulce a klikněte na *Připojit přes MAC*. Aplikace pak:

1. zkusí **IPv6 link-local** adresu (`fe80::…`), kterou zařízení hlásí v MNDP. Funguje na
   výchozím RouterOS v7, po nasazení hardening bloku ani na v6 obvykle ne;
2. jinak se přihlásí přes **MAC-Telnet** (UDP 20561, přihlášení EC-SRP5 jako od RouterOS
   6.43) a na rozhraní, ze kterého se zařízení ohlásilo, přidá **dočasnou IP adresu**
   z rozsahu vaší síťové karty s komentářem `mqs-temp`. Přes tuto adresu se pak připojí
   normálně přes api-ssl nebo SSH.

Navrženou adresu aplikace před připojením ukáže a nechá ji potvrdit. Dočasnou adresu
odstraní reset zařízení; při režimu `/import` ji odeberte ručně:

```
/ip address remove [find comment=mqs-temp]
```

Rozdíl proti Winboxu: Winbox jede celou relaci přes vlastní MAC protokol, tato aplikace
přes MAC jen nabootstrapuje spojení a dál pracuje přes api-ssl nebo SSH. Díky tomu funguje
i záloha, přenos souborů a ověření konfigurace po aplikaci.

> **Ověřeno 16. 9. 2026** na hEX S s RouterOS 7.20.7: vyhledání, přihlášení přes
> MAC-Telnet, přidání dočasné adresy i následné připojení přes api-ssl proběhly celé.
> Zjištěné odchylky protokolu jsou popsané v `docs/DIALECT_NOTES.md`.
>
> MAC-Telnet ani EC-SRP5 nejsou od MikroTiku dokumentované; implementace vychází z veřejné
> referenční implementace (viz `THIRD_PARTY_NOTICES.md`). Vyžaduje, aby na daném rozhraní
> byl povolený `/tool mac-server`, a účet nesmí být expirovaný — RouterOS to hlásí stejně
> jako špatné heslo. Přihlášení MD5 pro RouterOS starší než 6.43 implementované není.

Soubory se na zařízení přenášejí přes SFTP, SCP, nebo dočasným HTTP serverem na tomto PC
a `/tool fetch`. Pokud nic z toho nefunguje, v režimu `/import` se příkazy pošlou po
jednom. Windows Firewall se může zeptat na povolení příchozího spojení (HTTP přenos) nebo
UDP 5678 (vyhledávání MNDP).

## Co dělá hardening blok

Výchozí firemní šablona (syntaxe v7):

| Část | Účinek |
|---|---|
| `/snmp community` | vypne výchozí community, přidá `PVIP` |
| `/ip neighbor discovery-settings` | neighbor discovery jen na nedynamických rozhraních |
| `/ipv6 settings` | vypne IPv6 |
| `/ip dns` | DNS servery 192.168.200.200 a 8.8.8.8 |
| `/ip service` | vypne ftp, telnet, www a api (8728), SSH přesune na port 122 |
| `/snmp` | zapne SNMP, trap community PVIP, verze 2 |
| `/system clock` | časové pásmo Europe/Prague |
| `/system logging` | logovací pravidla 0–3 na disk (volitelně robustní varianta přes `find`) |
| `/system ntp client` | NTP klient se servery 195.113.144.201 a 195.113.144.238 |

Pro **RouterOS v6** se blok automaticky přeloží:

- NTP servery se převedou na `primary-ntp` / `secondary-ntp`,
- IPv6 se vypne vypnutím balíčku `ipv6` (projeví se po restartu), případně se blok vynechá,
- SNMP adresa `::/0` se nahradí `0.0.0.0/0`,
- příkazy, které daná verze nezná, se vynechají s upozorněním.

**Po aplikaci zůstanou přístupné:** api-ssl 8729, SSH 122 a Winbox 8291. REST API (v7)
nepoběží, protože vyžaduje www-ssl. Aplikace z upraveného bloku počítá zbývající
přístupové cesty: při vypnutí kterékoliv z nich zobrazí červené varování a pokud by
nezůstala žádná, aplikaci zablokuje.

Volitelná rozšíření (ve výchozím stavu vypnutá): přiřazení certifikátu k api-ssl a omezení
ssh, winbox a api-ssl na management subnet.

## Jak vrátit zařízení zpět ze zálohy

Zálohy vytvořené před aplikací jsou na zařízení v *Files* (u zařízení s `flash/` ve složce
`flash/`) a pokud jste zvolili stažení, také v `%LOCALAPPDATA%\MikrotikQuickSetPlus\backups\`.

**Binární záloha** (celý stav včetně uživatelů, zařízení se restartuje):

```
/system backup load name=pre-quickset-RRRRMMDD-HHMMSS.backup
```

Ve Winboxu: *Files* → vyberte soubor `.backup` → *Restore*.

**Textový export** (čitelný, lze použít i na jiném zařízení): nejdřív zařízení resetujte
bez výchozí konfigurace a potom soubor naimportujte:

```
/system reset-configuration no-defaults=yes run-after-reset=pre-quickset-RRRRMMDD-HHMMSS.rsc
```

Pokud soubor na zařízení už není, nahrajte ho ze složky `backups\` přes *Files* ve Winboxu.

Pokud byla zapnutá **rollback pojistka** a konfigurace nefunguje, stačí počkat: zařízení
se po uplynutí času samo vrátí na zálohu.

## Omezení a známé nejistoty

- Konfigurace Wi-Fi, VPN, VLAN trunků a routovacích protokolů není součástí aplikace.
- **Připojení přes MAC adresu** je ověřené na RouterOS 7.20.7 (16. 9. 2026), včetně
  přidání dočasné adresy a připojení přes ni. Pro RouterOS starší než 6.43 (přihlášení
  MD5) není MAC-Telnet implementovaný vůbec.
- **Databáze switch čipů** (`src/mqs/data/switch_chips.yaml`) vychází z dokumentace MikroTiku
  (tabulka Bridge Hardware Offloading, Switch Chip Features, Marvell Prestera a CRS1xx/2xx),
  ne z měření na zařízeních. Čip, který v databázi není, se neblokuje – použijí se obecná
  pravidla a aplikace to napíše jako důvod v tabulce offloadu.
- Rozdíly mezi verzemi RouterOS, které nebyly ověřené na zařízení, jsou vedené v
  `docs/DIALECT_NOTES.md` jako **NUTNO OVĚŘIT** a v náhledu se zobrazují jako upozornění.
  Každý takový rozdíl je v kódu na jednom místě (`src/mqs/generator/features.py` a tabulky
  v dokumentaci), takže oprava je jednoduchá.

## Vývoj

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest                      # testy (vč. golden souborů a GUI)
.\.venv\Scripts\python.exe -m pytest --update-golden      # přegenerovat golden .rsc
.\.venv\Scripts\mqs-gen.exe --target 6.49.10 --issues     # skript z příkazové řádky
.\.venv\Scripts\python.exe -m mqs                         # spustit GUI ze zdrojů
.\build.ps1                                               # dist\MikrotikQuickSetPlus.exe
```

Struktura: `model` (datový model a validace), `capabilities` (HW offload), `generator`
(čistý generátor bez sítě a GUI), `transport` (API-SSL, SSH, MNDP, přenos souborů), `apply`
(průběh aplikace), `profiles`, `ui` (PySide6). Architekturu a rozhodnutí popisuje `PLAN.md`.
