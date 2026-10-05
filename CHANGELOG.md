# Changelog

Co se v které verzi změnilo. Novější nahoře.

**Tenhle soubor je zdroj.** [Vydání na GitHubu](https://github.com/johnnybee05/RouterOS_QuickSet/releases)
se z něj generují (`.\release.ps1 -Version X.Y.Z`), takže text změn se nepíše dvakrát;
vydání k němu přidá jen `MikrotikQuickSetPlus.exe` ke stažení a odkaz na porovnání
s předchozí verzí. Nová verze se tedy vydává tak, že se sem nejdřív napíše její oddíl
`## [X.Y.Z] – <datum>` a commitne — co není v changelogu, nevyjde.

Verze jsou `MAJOR.MINOR.PATCH` — do 1.0 se mění `MINOR` při nové funkci a `PATCH`
u opravného vydání.

## [0.3.0] – nevydáno

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
  nastavit vůbec — VLAN se musela doklikat ve Winboxu.

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
