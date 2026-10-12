# Changelog

Co se v které verzi změnilo. Novější nahoře.

**Tenhle soubor je zdroj.** [Vydání na GitHubu](https://github.com/johnnybee05/RouterOS_QuickSet/releases)
se z něj generují (`.\release.ps1 -Version X.Y.Z`), takže text změn se nepíše dvakrát;
vydání k němu přidá jen `MikrotikQuickSetPlus.exe` ke stažení a odkaz na porovnání
s předchozí verzí. Nová verze se tedy vydává tak, že se sem nejdřív napíše její oddíl
`## [X.Y.Z] – <datum>` a commitne — co není v changelogu, nevyjde.

Verze jsou `MAJOR.MINOR.PATCH` — do 1.0 se mění `MINOR` při nové funkci a `PATCH`
u opravného vydání.

## [0.4.0] – 2026-10-12

### Přidáno

- **Bezpečná aktualizace zařízení s rádiem.** Od RouterOS 7.13 jsou ovladače rádií
  samostatné balíčky (`wireless`, `wifi-qcom`, `wifi-qcom-ac`) a nahrát `routeros.npk`
  bez toho správného znamená zařízení bez bezdrátového rozhraní — na druhém konci PtP
  spoje i bez cesty zpátky. Aplikace proto sadu balíčků **čte ze zařízení**, ne z názvu
  modelu: podle rozhraní v `/interface wireless`, `/interface wifi` a `/interface w60g`
  a podle nainstalovaných balíčků. Bez načtených rádií neplánuje nic — prázdný seznam
  není „zařízení nemá rádio“, ale „nikdo se nedíval“. Chybějící ovladač pro cílovou verzi
  update odmítne; běžný balíček (`container`, `gps`, …) jde povolit jmenovitě
  (`--allow-missing-package`). Pravidla jsou v editovatelném
  `wireless_packages.yaml`, uživatelská kopie v `%LOCALAPPDATA%` přebíjí.
- **Zastávky na trase.** Z verzí pod 7.12 se na 7.13+ neskáče přímo: doplní se mezikrok
  7.12.x, kde se wireless balíčky konvertují. Skládá se to s dosavadní bránou 6.49.22,
  takže anténa na 6.48.6 jede na 7.16.1 přes `6.49.22` a `7.12.x`, tři restarty.
  Zastávka na 7.12 platí **jen pro zařízení s rádiem** — zařízení bez něj nemá co
  konvertovat, a hEX S bez antény proto jede dál dvěma skoky, jak bylo ověřeno.
- **Volba mezi `wifi-qcom` a `wifi-qcom-ac`** u zařízení jedoucích na `wifiwave2`. Ptá se
  v pořadí: zadaná volba, co nabízí samo zařízení (7.23 vypisuje balíčky, které pro tu
  desku MikroTik vydává), mapovací soubor, a když nic — **zeptá se vás**
  (`--wifi-package`). Nehádá: špatná volba smaže konfiguraci rozhraní.
- **Kontrola po každém restartu.** Shoda verzí všech balíčků (nic `scheduled` ani
  `disabled`) a porovnání bezdrátových rozhraní se stavem předtím. Rozlišuje chybějící
  menu (ovladač se nenačetl), chybějící rozhraní (ovladač ano, rádio ne) a nespuštěné
  rozhraní (spoj je dole). Zmizelé rádio zastaví trasu i dávku okamžitě, i na
  mezizastávce. U AP se navíc po poslední zastávce čeká na návrat stanic.
- **Stahování balíčků** z `download.mikrotik.com` do lokální cache (`--download`,
  výchozí vypnuto). Stažený soubor se přijme, jen když jeho hlavička odpovídá balíčku,
  verzi i architektuře — chybová stránka uložená pod správným názvem by se jinak nahrála
  a vzala s sebou rádio. Adresář `Update` má přednost před cache.
- **`mqs scan` a `mqs inventory`.** Hledání MikroTiků několika zdroji naráz: MNDP,
  `/ip neighbor` a registrační tabulky už dosažených zařízení, volitelně SNMPv2c
  a omezený sken portů. Každý zdroj má vlastní timeout, takže jeden pomalý nezdrží
  ostatní. Výsledky se slučují podle sériového čísla, jinak podle sdílené MAC — podle IP
  adresy nikdy, dvě zařízení na jedné adrese se ukážou jako konflikt. Zařízení je
  bezdrátové proto, že má rádia; mapovací soubor `wireless_models.yaml` jen dodá
  kategorii. Inventář se ukládá do JSON, exportuje do CSV (středníky, kvůli Excelu)
  a druhý scan hlásí změny — **zmizelé rádio** a **verze dolů** jsou vážné a ukončí
  `mqs scan` nenulově.
- **`mqs fleet` — dávkový update.** Pořadí od nejvzdálenějších zařízení k páteři,
  sestavené z registračních a neighbor tabulek; `--here` posune zařízení, za kterým sedí
  počítač, na konec. Neúplná topologie si vyžádá `--confirm-order`. Paralelismus výchozí
  1 a zařízení, za kterým něco visí, běží vždycky samo. `--dry-run` vypíše celý plán
  bez jediné změny. Při prvním selhání se dávka zastaví a řekne, kolik zařízení
  zůstalo neaktualizovaných.
- `mqs version` vypisuje nalezená rádia a celou trasu ještě před otázkou, a umí
  `--dry-run`.
- **Záložka *Síť*** v okně: tabulka nalezených zařízení se stavem detekce v prvním
  sloupci (`++` potvrzeno a půjde aktualizovat, `+` pravděpodobně MikroTik, `?` nic
  neukazuje), pořadí dávky i se stromem topologie pod ní, export do CSV/JSON. Druhý scan
  zvýrazní, co se změnilo, a zmizelé rádio dostane varování nad pořadím.

### Změněno

- **Chybějící balíček zastaví plán.** Dřív to bylo varování a update pokračoval — balíček
  pak po restartu zmizel a zjistilo se to později. Ovladač rádia nejde přebít vůbec,
  ostatní balíčky jmenovitě.
- **Napříč hlavní verzí se wireless ovladač naopak doplňuje.** v6 ho nese v bundlu, v7
  potřebuje vlastní soubor, takže anténa jdoucí z v6 na v7 dostane `wireless-<verze>.npk`
  — dřív se všechny balíčky napříč hlavní verzí jen zahodily s varováním.

Podrobnosti, včetně toho, co je a co není ověřené na skutečném hardwaru:
[docs/WIRELESS.md](docs/WIRELESS.md).

### Opraveno

- **Nestažená záloha říká proč.** Uživatel s SSH klíčem se přes api-ssl přihlásí heslem,
  ale SSH mu heslo odmítne (`/ip ssh password-authentication=yes-if-no-key`, výchozí
  v RouterOS 7), takže záloha a export zůstaly na zařízení jen s hláškou „SSH
  authentication failed“. Aplikace se teď zařízení zeptá a napíše příčinu i nápravu:
  zadat ten klíč v připojení.
- **Zálohy se stahují přes API.** Na RouterOS 7 jdou záloha a export po spojení, na kterém
  už aplikace je, bez druhého přihlášení přes SSH — takže projdou i tam, kde SSH heslo
  odmítne, je omezené na jinou síť nebo vypnuté. Zařízení data převede do base64; holé
  čtení přes API by z binární zálohy potichu vypustilo bajty. SFTP a SCP zůstávají jako
  záloha a pro RouterOS 6.
- **Připojení přes API k zařízení bez rádia nespadne.** RouterOS 7.23 odpovídá na
  neexistující menu dvěma chybami najednou a ty aplikace nebrala jako „menu tu není“.

## [0.3.0] – 2026-10-05

### Přidáno

- **WAN umí jet v VLAN.** Na stránce *Základ* je u WAN volba *WAN je v VLAN (tagovaný
  provoz na portu)* s VLAN ID — předvyplněným na **848**, kterou pro PPPoE používá
  Cetin — a nepovinným názvem rozhraní (prázdný = `vlan848`). Skript pak na WAN portu
  vytvoří `/interface vlan add name=vlan848 interface=<port> vlan-id=848 disabled=no`
  ještě před seznamy rozhraní a WAN klient (PPPoE, DHCP i statická adresa) dostane
  `interface=vlan848`; port sám zůstane bez IP adresy. V seznamu WAN je VLAN rozhraní
  i port, takže firewall uzavře i netagovaný provoz, který na port přijde. Kontrola před
  aplikací upozorní, že spojení vedené právě přes ten port se po aplikaci nevrátí
  (stejně jako u PPPoE), a kontrolní seznam po aplikaci přidal položku „Rozhraní
  vlan848: VLAN 848 na portu <port>“. Předtím se router za CPE Cetinu nedal z aplikace
  nastavit vůbec — VLAN se musela doklikat ve Winboxu. Syntaxe je ověřená na železe
  (hEX S E60iUGS 7.23.5 a RB760iGS 6.49.21, 2026-10-05): v6 i v7 berou `/interface vlan
  add` i klienta DHCP a PPPoE na VLAN rozhraní stejně.

## [0.2.0] – 2026-10-02

Cesty, kudy se zařízení dá dosáhnout, když je ta obvyklá zavřená.

### Přidáno

- **Balíček RouterOS projde i na zařízení, které nemá kudy ho přijmout.** Přenos
  souborů má poslední strategii „zapůjčená adresa“: adresu, kterou už uživatel
  odsouhlasil k zapůjčení pro spojení přes MAC, dostane zařízení konzolí MAC-Telnetu,
  po ní jde SFTP, SCP nebo fetch, a při uvolnění přenosu se zase odebere. Nic se
  nepůjčuje, dokud funguje levnější cesta; přihlášení po zapůjčené adrese se zařízení
  nejdřív zeptá na jeho vlastní MAC adresu, takže cizí zařízení, které adresu drží,
  žádný soubor nedostane. Předtím hEX S dosažený jen přes IPv6 link-local neměl kam dát
  10,6 MB balíček (ssh omezená na management subnet, konzole MAC-Telnetu nejvýš 64 KiB,
  HTTP + fetch se pro scoped IPv6 vůbec nenabídlo) a přechod verze selhal.
- **Soukromý klíč může být chráněný heslem.** V okně políčko *Heslo SSH klíče*,
  maskované i v logu; `mqs.cli` ho bere z `MQS_KEY_PASSPHRASE`, nebo se zeptá — a jen
  u klíče, který heslo opravdu potřebuje, takže neinteraktivní běh s nešifrovaným
  klíčem nic nezastaví. Zašifrovanost se čte ze souboru (OpenSSH,
  `Proc-Type: 4,ENCRYPTED`, PKCS#8), takže chybějící nebo špatné heslo se pojmenuje
  (`KeyPassphraseError`) ještě předtím, než se na zařízení cokoli pošle. Na příkazové
  řádce heslo neskončí nikdy.
- **Zálohu udělá i samotný přechod na jinou verzi.** *Přejít na jinou verzi RouterOS
  (samostatně, bez resetu)* a `mqs.cli version --to` dřív restartovaly zařízení do
  jiného RouterOS s ničím uloženým — a přitom je záloha potřeba právě tam, protože
  downgrade konfiguraci zahodí a mezi v6 a v7 ji RouterOS převádí jen jedním směrem.
  Oba postupy teď dělají tentýž krok z `apply/backup.py`: `/system backup save` +
  `/export` pod `flash/`, stažení podle téhož zaškrtávátka a adresáře
  (`version --download-dir`) a úklid podle `--keep-backups`, který tu zároveň uvolní
  místo pro balíčky. Proběhne jednou, než jde nahoru první balíček, u cesty bez
  balíčků se vynechá a záloha, kterou nejde zapsat, run zastaví pod
  `apply.failed.backup`, ještě než se cokoli nahraje.
- **Zařízení s v6 dostane konzoli MAC-Telnetu taky.** Soubory po ní nepřenáší, ale dá
  se mu říct, aby si vzalo adresu.
- Dokumentace: `docs/MAC_ONLY.md` (návrh, jak by se zařízení dalo řídit přes MAC
  samotný, nic z toho zatím postavené není), `docs/VERSION_CHANGE.md` (reset s klíčem
  pod heslem přes MAC celou cestou).

### Opraveno

- **Na zapůjčenou adresu se čeká.** Adresa, kterou tohle PC krátce předtím ověřilo
  pingem — tedy přesně tak, jak se zjišťuje, že je volná — je z něj pak skoro minutu
  nedosažitelná, i když si ji zařízení vezme okamžitě: naměřeno na 192.168.55.207,
  TCP 22 odpovědělo 49 s po tom, co zařízení adresu mělo, zatímco na MAC-Telnet
  odpovídalo celou dobu. Spojení přes MAC to vzdalo po prvním timeoutu, adresu vrátilo
  a obvinilo zařízení. `connect_to_lent_address` teď zkouší až 75 s po třech
  sekundách, ale jen dokud je adresa pouze nedosažitelná — odmítnutí je odpověď
  zařízení a neopakuje se, zrušení adresu vrátí a každé čekání projde oknem.
- **Zamčený klíč při spojení přes MAC už neobviňuje zařízení.** Každá chyba přihlášení
  po zapůjčené adrese byla zabalená do „dočasná adresa … byla přidána, ale zařízení na
  ní není dosažitelné“. U klíče, který tohle PC neumí otevřít, to není pravda
  a schovávalo to jedinou věc, se kterou uživatel může něco udělat — heslo klíče.
  `KeyPassphraseError` se teď propíše sám, adresa se předtím odebere.

### Ověřeno na hardwaru

Všechno na hEX S (RB760iGS): balíček `routeros-7.23.7-mmips.npk` po zapůjčené adrese za
23 s se ssh omezenou na 192.168.55.0/24 a přechod 7.23.7 → 7.23.5 a zpět po jedné
link-local session; klíč s heslem přes IPv6 link-local jako uživatel jen s klíčem
(7.23.5); tři běhy ze tří dovnitř po zapůjčené adrese s čekáním (před opravou by spadly
všechny tři); 6.49.12 → 6.49.21 se zálohou `flash/pre-quickset-*.{backup,rsc}` zapsanou
a stáhnutou před prvním balíčkem.

995 testů prochází.

## [0.1.1] – 2026-09-28

Opravné vydání po prvním ostrém nasazení.

### Opraveno

- **Zapůjčená adresa, která odpovídá z jiného zařízení, se odmítne.** Aplikace přes
  MAC-Telnet si zařízení nastaví dočasnou adresu (192.168.55.254) a dál s ním mluví po
  IP. Když byla v síti už obsazená, odpověděl na ni cizí MikroTik: skript se nahrál na
  správný hEX S, ale všechno po bootstrapu šlo na neznámé zařízení, které se
  zazálohovalo a dostalo příkaz resetovat se souborem, který nikdy nevidělo — apply
  skončil chybou `input does not match any value of run-after-reset`. Bootstrap se teď
  toho, kdo na zapůjčené adrese odpoví, zeptá na jeho rozhraní a nepokračuje, dokud
  mezi nimi nenajde MAC adresu našeho zařízení; zařízení, které nevypíše žádné, se
  bere za své.

### Ověřeno na hardwaru

Stejný profil, stejná deska, stejná cesta přes MAC adresu, tentokrát volná adresa: reset
přijat na první pokus, všechny řádky kontrolního seznamu OK, a záloha před aplikací
pochází ze zařízení, které se skutečně resetuje.

950 testů prochází.

## [0.1.0] – 2026-09-28

První vydání: desktopová aplikace pro Windows, která vygeneruje výchozí konfiguraci
MikroTik RouterOS v6 i v7 a umí ji rovnou aplikovat na zařízení po síti.

### Přidáno

- WAN na **libovolném portu**, ne jen ether1
- **přejmenované porty** pozná podle `default-name`, názvy portů umí i nastavit
- plnohodnotná volba **protocol-mode** bridge (none / stp / rstp / mstp)
- změna **system identity**
- firemní **hardening blok**, editovatelný a ukládaný do profilu
- **HW offload** bridge a **FastTrack** podle toho, co zařízení skutečně umí
- vyhledání zařízení v síti přes **MNDP** ještě před připojením
- **připojení přes MAC adresu** u zařízení bez použitelné IP
- **změna verze RouterOS**: upgrade, downgrade i přechod mezi v6 a v7 (dvouskoková
  cesta přes bránu 6.49.22), včetně aktualizace RouterBOOT
- **offline režim**: skript jde jen vygenerovat do souboru nebo schránky
- **CLI**: totéž bez okna přes `python -m mqs.cli`

### Ověřeno na hardwaru

hEX S (RB760iGS, mmips) s RouterOS 6.49.10 — generování, aplikace, reset s aktualizací,
přechod 6.49.12 → 6.49.22 → 7.23.7; hEX S (arm) s RouterOS 7.

947 testů prochází.

[0.2.0]: https://github.com/johnnybee05/RouterOS_QuickSet/releases/tag/v0.2.0
[0.1.1]: https://github.com/johnnybee05/RouterOS_QuickSet/releases/tag/v0.1.1
[0.1.0]: https://github.com/johnnybee05/RouterOS_QuickSet/releases/tag/v0.1.0
