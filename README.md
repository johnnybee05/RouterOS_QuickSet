# MikroTik QuickSet Plus

Desktopová aplikace pro Windows, která rychle vygeneruje výchozí konfiguraci MikroTik
RouterOS **v6 i v7** a umí ji rovnou aplikovat na zařízení po síti. Nahrazuje Winbox
QuickSet bez jeho omezení:

- WAN může být **libovolný port**, ne jen ether1,
- **přejmenované porty** (ether1-uplink …) pozná podle `default-name` a názvy portů umí
  i nastavit,
- plnohodnotná volba **protocol-mode** bridge (none / stp / rstp / mstp),
- změna **system identity**,
- firemní **hardening blok**, který jde v aplikaci upravit a uložit do profilu,
- **HW offload** bridge a **FastTrack** podle toho, co zařízení skutečně umí,
- vyhledání zařízení v síti (MNDP) ještě před připojením.

Aplikace funguje i **offline**: skript jde jen vygenerovat do souboru nebo schránky.

---

## Spuštění

Stačí spustit `MikrotikQuickSetPlus.exe`. Python není potřeba, nic se neinstaluje. Vedle
exe může být adresář `Update` s balíčky RouterOS (viz
[Aktualizace RouterOS při resetu](#aktualizace-routeros-při-resetu)).

Okno umí formulář *Připojit* předplnit z příkazové řádky, což se hodí, když pořád
sedíte u stejného zařízení:

```powershell
MikrotikQuickSetPlus.exe --host 192.168.55.129 --user bee --key $HOME\.ssh\id_rsa `
    --transport ssh --ssh-port 122 --connect
```

`--connect` stiskne *Připojit* samo, hned jak je okno na obrazovce (bez `--host` nemá co
dělat, takže neudělá nic). Heslo se na příkazovou řádku nepíše — `--password-env JMENO`
ho vezme z proměnné prostředí, stejně jako `mqs.cli`. Ostatní argumenty (`-style`,
`-platform`) si přebere Qt. Celý seznam: `MikrotikQuickSetPlus.exe --help` (ze zdrojů
`python -m mqs --help`).

### SSH klíč chráněný heslem

Soukromý klíč zašifrovaný heslem (passphrase) je v okně políčko **Heslo SSH klíče** hned
pod klíčem; vyplňte ho jen u takového klíče, u nešifrovaného zůstane prázdné. Heslo klíče
je heslo *souboru*, ne přihlašovací heslo uživatele na routeru — zůstává jen v paměti,
neukládá se do profilů a v logu je maskované. Když klíč heslo potřebuje a pole je
prázdné, aplikace to rovnou napíše (*„Soukromý klíč je šifrovaný…“*) a na zařízení se
nikam nepřihlašuje; stejně tak pozná chybně zadané heslo klíče.

V CLI se stejně jako ostatní hesla nepíše do příkazu: `--key-passphrase-env JMENO`
(výchozí `MQS_KEY_PASSPHRASE`), a když je klíč šifrovaný a proměnná není nastavená,
`mqs.cli` se na heslo klíče zeptá v terminálu. Formáty klíčů: OpenSSH (`ssh-keygen`),
PKCS#8 i starší PEM, klíče RSA i Ed25519.

```powershell
# bez proměnné se CLI zeptá samo; takhle jde klíč použít i v neinteraktivním běhu
$env:MQS_KEY_PASSPHRASE = Read-Host "Heslo klíče"
.\.venv\Scripts\python.exe -m mqs.cli connect --host 192.168.55.129 --user beran --key $HOME\.ssh\id_ed25519 --transport ssh
```

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
   - *Názvy portů*: dvojklikem do sloupce *Název* port přejmenujete. Skript porty
     přejmenuje jako první (`/interface ethernet set [ find default-name=ether1 ]
     name=…`, stejně jako `/export`), takže název platí i po resetu. Po připojení se názvy,
     které porty na zařízení už mají, převezmou do profilu samy a profil, který porty
     jmenuje továrními názvy (ether1 …), se na ně přemapuje. Přejmenovat jde jen
     ethernetové porty.
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
   - *Povolit vzdálené DNS dotazy* (`/ip dns allow-remote-requests`, výchozí vypnuto) určuje,
     jaké DNS dostanou klienti z DHCP serveru: zapnuté adresu bridge (router jim DNS
     překládá), vypnuté DNS servery vyplněné v tomto formuláři. Zapnout jde jen se
     základním filtrem, aby DNS neodpovídalo z WAN; vypnutím filtru se vypne i tahle volba.
     DNS zadané přímo u DHCP serveru na stránce LAN má přednost.
6. **Náhled**: skript pro v6, v7 nebo obě vedle sebe. Tlačítko *Kopírovat* vloží skript do
   schránky (lze ho vložit rovnou do terminálu Winboxu), *Uložit .rsc* ho uloží do souboru.
7. **Aplikovat**: vyberte režim, zkontrolujte upozornění, projděte dry-run a potvrďte.

Chybně vyplněná pole se hned zbarví červeně s konkrétní hláškou. Dokud konfigurace není
v pořádku, nejde aplikovat.

### Režimy aplikace

| Režim | Co se stane |
|---|---|
| Jen vygenerovat | skript se uloží do souboru, na síť se nesahá |
| Aplikovat na běžící konfiguraci | skript se nahraje a spustí přes `/import`, přidá se k tomu, co na zařízení už je. Objekty, které na zařízení už jsou (bridge, porty, seznamy rozhraní, adresy, pool, DHCP, výchozí trasa, SNMP community, NTP servery), skript místo `add` upraví přes `set`, nebo je vynechá; pravidla firewallu z dřívějšího běhu nahradí. Pravidla NAT a MSS clampingu z dřívějšího běhu odstraní, i když je profil už nepřidává (vypnutý masquerade nebo MSS clamping); cizí pravidla nechá být. Má-li profil jinou výchozí bránu, změní ji na stávající statické trase místo přidání druhé; je-li takových tras víc (záloha přes jinou bránu), nechá je a varuje. Stejný profil jde tedy aplikovat opakovaně. Náhled ukáže už upravený skript. |
| Reset + aplikovat | `/system reset-configuration no-defaults=yes skip-backup=no run-after-reset=…`: zařízení se restartuje a po startu samo naimportuje skript. Pro potvrzení je nutné opsat identity zařízení a zadat nové heslo pro `admin`. Heslo skript nastaví hned na začátku, protože chyba na kterémkoli řádku zastaví celý skript. Skript se po dokončení sám smaže; zůstane-li na zařízení, první řádek checklistu ukáže chybu z logu, na které se zastavil. |
| Reset + aplikovat, *Zachovat uživatele* | totéž s `keep-users=yes`: uživatelé, jejich hesla i SSH klíče reset přežijí. Nové heslo pro `admin` je nepovinné (prázdné ponechá současné) a aplikace se po resetu připojí pod stejným účtem, třeba uživatelem s klíčem. V CLI `--keep-users`. |

### Aktualizace RouterOS při resetu

Vedle `MikrotikQuickSetPlus.exe` může ležet adresář **`Update`** s balíčky RouterOS tak, jak
je stahujete od MikroTiku (`routeros-7.23.7-arm.npk`, `routeros-7.23.7-mmips.npk` …). Když
je v režimu *Reset + aplikovat* zaškrtnuté *Aktualizovat RouterOS z adresáře Update*
(výchozí stav), aplikace:

1. podle `architecture-name` zařízení (`/system resource`) vybere balíček s odpovídající
   architekturou. Architekturu, jméno i verzi čte z hlavičky `.npk`, ne z názvu souboru,
   takže přejmenovaný soubor nevadí. Je-li jich víc, vezme nejnovější verzi;
2. před resetem ho nahraje do kořene souborů zařízení, hned za zálohu a před skript;
3. restart po resetu balíček nainstaluje a pak se spustí skript s konfigurací. Skript je
   proto vygenerovaný pro novou verzi a bez vlastností, které aplikace ověřuje zkouškou na
   zařízení: zkouška na staré verzi o té nové nic neříká (7.20.7 zná `hw-offload` u
   FastTracku, 7.23.7 už ne). Po aktualizaci ho můžete zapnout importem;
4. po připojení checklist ověří řádkem *RouterOS 7.23.7 (na zařízení …)*, že zařízení
   opravdu běží na nové verzi. Balíček, který by po restartu na zařízení zůstal, aplikace
   smaže, aby se nenainstaloval při některém pozdějším restartu.

Samostatné zaškrtávátko *Aktualizovat RouterBOOT* (také výchozí stav zapnuto, v CLI
`--routerboot`) přidá poslední krok: po ověření konfigurace aktualizuje **RouterBOOT**,
pokud RouterOS na zařízení nese novější firmware, než jaký běží (`upgrade-firmware` >
`current-firmware`). To platí i bez aktualizace RouterOS. Provede `/system routerboard
upgrade`, zařízení restartuje, znovu se připojí a checklist ukáže *RouterBOOT 7.23.7 (na
zařízení …)*. Stojí to jeden restart navíc; na zařízení bez RouterBOOT (CHR, x86) se krok
přeskočí.

Co se nainstaluje, ukážou kontroly před aplikací i dry-run, např. *Po resetu se nainstaluje
RouterOS 7.23.7 (zařízení má 7.20.7): routeros-7.23.7-arm.npk.* Aktualizace se přeskočí
(s upozorněním, reset proběhne dál), když adresář chybí, pro architekturu v něm balíček
není, zařízení už stejnou nebo novější verzi má, nebo jde o přechod mezi v6 a v7 (na ten
je samostatná funkce, viz níže). Pokud se balíček nepodaří nahrát, aplikace skončí dřív, než cokoliv
změní; pokud se nahraje, ale reset neproběhne, balíček ze zařízení zase smaže.

Nové verze stačí nahrát do `Update` (staré můžou zůstat, bere se nejnovější). Má-li
zařízení i další balíčky (např. `wifi-qcom`), musí být v `Update` ve stejné verzi a
architektuře, jinak aplikace upozorní, že po aktualizaci nemusí fungovat. Při spuštění ze
zdrojů se používá `dist\Update`. V CLI `--update` (volitelně `--update-dir`).

> Architektura se nedá odhadnout podle názvu modelu: hEX S E60iUGS (čip EN7562) hlásí
> `arm`, kdežto starší hEX S s čipem MT7621 patří k `mmips`.
>
> **Ověřeno 24. 9. 2026** na hEX S (7.20.7 → 7.23.7): jeden restart balíček nainstaluje,
> smaže ho ze souborů a spustí skript. Podrobnosti v `docs/DIALECT_NOTES.md`.

### Přechod na jinou verzi RouterOS (downgrade, v6 <-> v7)

Aktualizace výše umí jen dopředu a jen v rámci hlavní verze, protože ji dělá restart po
resetu. Když chcete **konkrétní verzi** — starší, nebo z druhé hlavní verze — je na to
samostatná funkce: na stránce *Aplikovat* zaškrtávátko **Přejít na jinou verzi RouterOS**,
seznam verzí z adresáře `Update` a tlačítko *Přejít na verzi…*; v CLI příkaz `version`.

```powershell
# co adresář Update umí na tohle zařízení nainstalovat (nic nemění)
.\.venv\Scripts\python.exe -m mqs.cli version --host 192.168.55.129 --user bee --key $HOME\.ssh\id_rsa --transport ssh --ssh-port 122

# downgrade; jméno zařízení potvrzuje, že to myslíte vážně
.\.venv\Scripts\python.exe -m mqs.cli version --host 192.168.55.129 ... --to 6.49.12 --yes --confirm-identity v6-ap

# verze a hned po ní konfigurace pro ni
.\.venv\Scripts\python.exe -m mqs.cli apply --host 192.168.55.129 ... --mode reset --to 7.23.5 --confirm-identity v6-ap --yes
```

Jak to funguje:

1. **Novější balíček** nainstaluje obyčejný restart, **starší** příkaz
   `/system package downgrade`, který restartuje sám. Balíček musí být před tím vidět
   v `/file` — příkaz totiž restartuje i tehdy, když nemá co nainstalovat, a nepovedené
   nahrání by tak stálo restart pro nic.
2. **Níž než na „factory firmware" se jít nedá.** Verzi, se kterou byla deska vyrobena,
   hlásí `/system routerboard` (`factory-firmware`) i `/system resource`
   (`factory-software`) a aplikace ji bere jako dno: mmips hEX S RB760iGS má 6.42.4, arm
   hEX S E60iUGS 7.20.7 — a **na v6 se tedy nikdy nedostane**. V seznamu verzí taková verze
   zůstane, jen nejde vybrat a v bublině je důvod.
3. **Na v7 se přechází jen z 6.49.22** (od 28. 9. 2026; dřív stačila 6.49.21). Bráno
   jako minimum, ne jako přesná verze — případná novější v6 by šla přímo. Zařízení
   s něčím starším aplikace nepošle nikam naslepo: naplánuje **dva kroky**, nejdřív na
   nejnovější v6 z adresáře (nejméně 6.49.22), pak na v7. Když v adresáři takový balíček
   není, odmítne to a řekne, který soubor chybí.
4. **Konfiguraci převádí RouterOS, a jen nahoru.** Z v6 na v7 ji převede sám při
   restartu (na hEX S mmips zůstaly identity, služby a jejich porty, adresy i uživatelé),
   zpět z v7 na v6 ji nepřevádí — co jste nastavil na v7, se nemusí zachovat. Aplikace
   konfiguraci nepřevádí nikdy: generuje ji pro cílovou verzi znovu, takže po přechodu je
   správný druhý krok *Reset + aplikovat* (v CLI jedním příkazem `apply --to`). Po
   přechodu zařízení znovu přečte, protože se změní balíčky, ipv6, wireless, jméno switch
   čipu i to, jestli soubory leží ve `flash/`.
5. **Než se zařízení ohlásí zpět**, hledá ho aplikace na adrese, na které odpovídalo,
   pak na adresách z profilu a nakonec přes MAC. Po přechodu z v7 na v6 se totiž může
   ohlásit jinde, nebo vůbec — pak zbývá Netinstall, což dialog říká předem.
6. **RouterBOOT jde s RouterOS, a jen nahoru.** Nová verze RouterOS položí nový firmware
   do `upgrade-firmware` a krok *Aktualizovat RouterBOOT* ho zapíše — po přechodu tedy
   běží, stejně jako po resetu s aplikací (v CLI `version --routerboot`). Po downgradu je
   `upgrade-firmware` starší než `current-firmware`, takže krok sám nic neudělá a řekne to.
   Starší RouterOS pod novějším RouterBOOTem běží bez potíží, i napříč hlavní verzí (6.49.21
   pod RouterBOOTem 7.23.7, ověřeno), takže snižovat ho není nutné. Když ho snížit chcete,
   je na to **vlastní** volba *Snížit RouterBOOT* (v CLI `--routerboot-downgrade`, jen spolu
   s `--routerboot`): výchozí je vypnutá a samo se to nikdy nestane — downgrade RouterOS
   bootloader nestahuje s sebou. Ověřeno oběma směry na hEX S. Když už je zařízení na správné
   verzi a firmware jen čeká, zapíše ho `version --routerboot` samotné (jeden restart,
   konfigurace se nemění).

**Zálohu udělá i samotný přechod na jinou verzi.** Než na zařízení půjde první balíček,
uloží na něj `pre-quickset-<datum-čas>.backup` a export `pre-quickset-<datum-čas>.rsc`
a podle zaškrtávátka *Stáhnout zálohu* je stáhne do zvoleného adresáře — úplně stejně jako
aplikace konfigurace, protože tady je záloha potřeba nejvíc: downgrade konfiguraci zahodí
a mezi v6 a v7 ji RouterOS převádí jen jedním směrem. Binární zálohu obnovíte jen na té
verzi RouterOS, která ji zapsala, takže napříč hlavní verzí zbývá čitelný export — proto
se ukládají obě. Když se záloha nepovede, přechod vůbec nezačne a na zařízení se nic
nezmění. V CLI `version --download-dir` (bez něj zůstane záloha jen na zařízení)
a `--keep-backups`.

Downgrade a přechod mezi v6 a v7 potvrzujete opsáním identity zařízení, stejně jako reset;
v CLI `--confirm-identity`.

> **Ověřeno 27. 9. 2026** na hEX S RB760iGS oběma směry: 6.49.21 → 6.49.12 příkazem
> `/system package downgrade` (jeden restart, 84 s, konfigurace i uživatelé zůstali,
> balíček zařízení samo smazalo) a zpět 6.49.12 → 6.49.21 obyčejným restartem. Přechod
> mezi v6 a v7 na železe ověřený **není** — podrobnosti a co ještě chybí v
> `docs/DIALECT_NOTES.md` a `docs/VERSION_CHANGE.md`.


V okně je předvolený režim *Reset + aplikovat*. Po úspěšné aplikaci (všechny kontroly
prošly) se aplikace od zařízení sama odpojí a vrátí případnou dočasnou adresu z připojení
přes MAC. S rollback pojistkou až po *Potvrdit, že konfigurace funguje*; když některá
kontrola selže, zůstane připojená.

Než aplikace cokoliv změní, vždy:

- uloží na zařízení zálohu `pre-quickset-<datum-čas>.backup` a export
  `pre-quickset-<datum-čas>.rsc` a nabídne jejich stažení,
- zobrazí přesně to, co se pošle (dry-run), a počká na potvrzení,
- volitelně naplánuje **rollback pojistku**. Pokud do N minut nekliknete na *Potvrdit, že
  konfigurace funguje*, zařízení obnoví zálohu a restartuje se. Úloha nejdřív smaže sama
  sebe, takže se nemůže opakovat.

Po aplikaci se aplikace sama přepojí (SSH je nově na portu 122) a ukáže checklist
OK/CHYBA: identity, služby, bridge, adresa, výchozí trasa (statické výchozí trasy musí být
přesně ty z profilu), DHCP klient (zapsaný a zapnutý, stav jen pro informaci) a skutečný
stav HW offloadu.

Každá aplikace nechá na zařízení jednu zálohu, takže aplikace po ověření smaže starší
zálohy `pre-quickset-*` a ponechá jen posledních N (výchozí 3, v CLI `--keep-backups`,
0 = nemazat). Stáří se určuje podle data a času v názvu souboru, ne podle data souboru,
protože hodiny zařízení jsou po resetu do synchronizace NTP posunuté. Právě vytvořená
záloha se nesmaže nikdy, protože z ní obnovuje rollback pojistka.

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
a `/tool fetch`. U spojení přes MAC se malé soubory (skript, záloha) přenesou i konzolí
MAC-Telnetu; balíček RouterOS se do ní nevejde (limit 64 KiB), takže když je zařízení
připojené přes link-local a jinudy to nejde, aplikace mu na tu dobu půjčí stejnou
dočasnou adresu jako při bootstrapu — po přenosu ji zase odebere. Pokud nefunguje nic
z toho, v režimu `/import` se příkazy pošlou po jednom. Windows Firewall se může zeptat na povolení příchozího spojení (HTTP přenos) nebo
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
- **Aplikace na běžící konfiguraci některé volby jen zapíná, nevypíná.** Když profil vypne
  VLAN filtering na bridge nebo management podsíť, zůstane na zařízení stav z dřívějška;
  totéž platí pro pravidla filtru, když profil vypne základní filtr i FastTrack zároveň.
  Reset + aplikovat začíná od výchozího stavu a tohle omezení nemá.
- **Změnu režimu (router ↔ bridge) dělejte resetem.** Import bridge profilu na routeru
  funguje, ale nechá po routeru prázdný původní bridge s LAN adresou, DHCP serverem a
  poolem a seznam WAN; kontrola před aplikací na to upozorní. Import router profilu na zařízení, kde je WAN port
  v bridge, je zablokovaný: import port z bridge nevyjme a WAN by nefungoval.
- Když import přidá do bridge port, přes který je aplikace připojená, spojení se na chvíli
  přeruší, než skript přesune adresu na bridge; kontrola před aplikací na to upozorní, a
  pokud profil na bridge pevnou adresu nedává, doporučí připojit se potom přes MAC.
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
