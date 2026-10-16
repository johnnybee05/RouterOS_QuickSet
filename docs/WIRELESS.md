# Aktualizace bezdrátových zařízení a jejich hledání v síti

Status: **implementováno**, ale **neověřeno na rádiu**. Testovací hardware (hEX S
RB760iGS mmips a hEX S E60iUGS arm) nemá anténní konektor, takže všechno, co se týká
wireless balíčků, je zatím NUTNO OVĚŘIT. Co ověřeno je, a jak, říká tenhle dokument
u každého pravidla zvlášť; souhrn je v `docs/DIALECT_NOTES.md`.

Cíl celé funkce je jediný: **po updatu nesmí zmizet rádio.** Na konci PtP spoje je
zmizelé rozhraní zároveň ztracená cesta zpátky, takže všechno níž je postavené tak, aby
spíš odmítlo update, než aby ho udělalo s nejistou sadou balíčků.

---

## 1. Proč nestačí nahrát `routeros.npk`

Od RouterOS 7.13 jsou ovladače rádií samostatné balíčky:

| balíček | co obsluhuje |
|---|---|
| `wireless` | legacy `/interface wireless` (a/b/g/n/ac) **a všech 60 GHz** (`/interface w60g`) |
| `wifi-qcom` | 802.11ax přes `/interface wifi` |
| `wifi-qcom-ac` | 802.11ac Wave2 na novém ovladači |

Dřívější `wifiwave2` se na 7.13 rozdělil na poslední dva. Verze **každého** balíčku se
musí přesně rovnat verzi `routeros`: `wireless-7.15.3` vedle `routeros-7.16.1` je přesně
ten stav, po kterém rádio zmizí, a RouterOS si na něj sám nestěžuje.

Z toho plyne všechno ostatní: ruční upload `.npk` znamená nahrát **celou sadu**, a tu je
nutné odvodit ze zařízení.

## 2. Sada balíčků se kopíruje, neodhaduje

`src/mqs/apply/package_set.py`, čisté funkce, pravidla v
`src/mqs/data/wireless_packages.yaml` (uživatelská kopie v `%LOCALAPPDATA%` přebíjí).

### 2.1 Co je důkaz rádia

**Rozhraní, nikdy seznam balíčků.** v6 bundle `routeros-<arch>` obsahuje balíček
`wireless` na *každé* desce — RB760iGS, která nemá kam zapojit anténu, ho vypisuje přesně
jako SXT (OVĚŘENO 2026-09-27). Kdyby se bral jako důkaz rádia, jel by každý v6 router
objížďkou přes 7.12 a aplikace by hledala soubor, který nepotřebuje.

Ovladač se tedy považuje za *potřebný rádiem* jen tam, kde jsou rozhraní v menu, které
obsluhuje. Balíček ovladače nainstalovaný jako vlastní soubor se přenáší tak jako tak —
jako každý nainstalovaný balíček.

### 2.2 Bez snapshotu se neplánuje nic

`DeviceInventory.radios.collected` je `False`, dokud se zařízení na rádia skutečně
nezeptalo. Prázdný snapshot **není** „zařízení nemá rádio", je to „nikdo se nedíval" — a
plán postavený na něm by ovladač prostě vynechal. `plan_version` i `plan_update` proto na
nenačteném snapshotu odmítnou cokoliv naplánovat (`version.no_snapshot`).

### 2.3 Jak se balíček jmenuje v cílové verzi

| zařízení má | cíl | potřebný soubor |
|---|---|---|
| legacy `wireless` | jakýkoli v7 | `wireless` |
| `wifiwave2` | < 7.13 | `wifiwave2` |
| `wifiwave2` | ≥ 7.13 | `wifi-qcom` **nebo** `wifi-qcom-ac` — viz 2.4 |
| `wifi-qcom` / `-ac` | < 7.13 | `wifiwave2` (obě se slučují zpět na jedno jméno) |
| `wifi-qcom` / `-ac` | ≥ 7.13 | totéž, **nikdy to druhé** |
| cokoliv | v6 | žádný — bundle ovladač nese |
| `wifi-*` s rádiem | v6 | **odmítnuto**: v6 ten ovladač nemá vůbec |

Cíl ve v7 potřebuje soubor pro každý ovladač. Je to záměrně ta bezpečnější varianta:
kdyby některá verze v7 ovladač nesla uvnitř `routeros`, odmítne aplikace update, který by
prošel — a to je směr, kterým tohle smí selhat. Ztráta rádia ne.

### 2.4 Rozdělení wifiwave2 — jediné místo, kde zařízení neporadí

V pořadí:

1. **`--wifi-package`**, pokud ho uživatel zadal.
2. **Co nabízí samo zařízení.** 7.23 vypisuje v `/system package print` každý balíček,
   který pro tu desku MikroTik vydává, s příznakem `A` (OVĚŘENO na hEX S 7.23.5,
   2026-09-27). Deska, která nabízí právě jednu polovinu rozdělení, tím řekla, který
   ovladač na ni patří — a je to lepší zdroj než jakákoli tabulka modelů.
3. **Vzor v mapovacím souboru.** Jediné pravidlo stojí na vlastním názvosloví MikroTiku:
   označení rádia u 802.11ax desky obsahuje `ax` (`C52iG-5HaxD2HaxD`, `L41G-2axD`),
   u Wave2 desky ne (`RBD53iG-5HacD2HnD`). NUTNO OVĚŘIT.
4. **Nic z toho → chyba a otázka na uživatele.** Nehádá se. Špatná volba smaže
   konfiguraci rozhraní, a to je horší výsledek než zastavený update.

### 2.5 Chybějící protějšek

- **Ovladač** bez souboru pro cílovou verzi = chyba, kterou nejde přebít.
- **Běžný balíček** (`container`, `gps`, `ups`, `iot`, …) = chyba, kterou lze povolit
  jmenovitě: `--allow-missing-package gps`.

Do 2026-10-05 bylo obojí jen varování a update pokračoval.

## 3. Trasa a zastávky

`plan_route` v `package_set.py`, pravidla v sekci `gates` datového souboru.

| brána | podmínka | zastávka |
|---|---|---|
| `v6_to_v7` | na v6, cíl ≥ 7 | nejvyšší v6 v adresáři (aspoň 6.49.22); kdo ji už má nebo je výš, jde přímo |
| `pre_712_to_713` | pod 7.12, cíl ≥ 7.13, **a zařízení má rádio** | nejnovější 7.12.x |

Brány se vyhodnocují znovu z každé zastávky, takže se skládají samy: anténa na 6.48.6
mířící na 7.16.1 jede `6.48.6 → 6.49.23 → 7.12.x → 7.16.1` (6.49.23 jako nejvyšší v6
v adresáři), tři restarty.

**Proč je 7.12 jen pro zařízení s rádiem:** ta zastávka existuje kvůli konverzi wireless
balíčků, takže zařízení bez nich nemá co konvertovat. Je to zároveň to, co smiřuje
pravidlo ze zadání s terénem — mmips hEX S (bez rádia) přešel `6.49.12 → 6.49.22 → 7.23.7`
dvěma skoky a vrátil se v pořádku (OVĚŘENO 2026-09-28, `docs/VERSION_CHANGE.md` §11).

## 4. Odkud se berou soubory

`src/mqs/apply/download.py`. Pořadí: adresář `Update` vedle exe → lokální cache →
`download.mikrotik.com`. Stahování je **vypnuté**, dokud se nezapne `--download`.

Názvy jsou MikroTikovy a v každé verzi jiné:

```
v7   routeros-7.23.7-arm.npk      wifi-qcom-7.23.7-arm.npk
v6   routeros-mmips-6.49.21.npk   wireless-6.49.23-mmips.npk
x86  routeros-7.23.7.npk          (bez architektury; v6 balík routeros-x86-6.49.23.npk)
ppc  routeros-7.24.4-ppc.npk      (zařízení říká powerpc; v6 balík routeros-powerpc-6.49.23.npk)
```

Architektura v URL je `architecture-name` ze zařízení, **ne** ta z hlavičky: soubor pro
mipsbe se jmenuje `-mipsbe.npk` a uvnitř má `mips`.

**Nic se nevěří podle jména.** Stažený soubor se přečte `read_npk()` a přijme jen tehdy,
když hlavička odpovídá balíčku, verzi i architektuře; jinak se smaže. Případ, kvůli
kterému to tak je: chybová stránka uložená jako `wireless-7.16.1-arm.npk` by se nahrála,
zařízení by restartovalo a rádio by bylo pryč.

MikroTik nevydává seznam verzí, takže „nejnovější 7.12.x" se hledá dotazy HEAD směrem
nahoru a končí po dvou po sobě jdoucích chybách — stažená verze uprostřed řady tedy
neskryje ty nad ní.

## 5. Průběh updatu jednoho zařízení

1. Záloha: `/system backup save` + `/export file=`, obojí stažené k sobě.
2. Kontrola místa. Deska s adresářem `flash/` má kořen v RAM disku a balíček jde **do
   paměti** — tak se vůbec dá updatovat 16MB anténa (OVĚŘENO na hEX S 6.49.10).
3. Upload přes SFTP (SSH), pak SCP, pak `/tool fetch` z lokálního HTTP serveru. FTP se
   nepoužívá.
4. Ověření, že soubory na zařízení **jsou**, teprve pak restart.
5. Reconnect s timeoutem, přes známé adresy i přes MAC.
6. Kontrola: verze RouterOS, **shoda verzí všech balíčků**, nic `scheduled`/`disabled`,
   **snapshot rozhraní** proti stavu před updatem.
7. Opakuje se pro každou zastávku.
8. RouterBOOT po poslední zastávce, volitelně.

### 5.1 Co se kontroluje po restartu

`compare_radios` rozlišuje tři způsoby, jak se rádio nevrátí, protože každý znamená něco
jiného:

| nález | význam |
|---|---|
| **chybí menu** | ovladač se vůbec nenačetl |
| **chybí rozhraní** | ovladač se načetl, rádio ne |
| **neběží** | obojí proběhlo a spoj je pořád dole |

Rozhraní, které bylo dole už předtím, se neřeší. Rozhraní navíc se vypíše, ale není chyba.

**Zmizelé rádio zastaví trasu okamžitě**, i na mezizastávce: rádio ztracené na 7.12 je
ztracené dřív, než je trasa v půlce, a další restart by ho dostal jen dál z dosahu.
Zařízení v tu chvíli běží a odpovídá — což je víc, než bude platit o tom, co je za ním.

Balíček na staré verzi je naproti tomu jen varování: rádia jsou tam, spoj drží, a je
odkud to opravit.

### 5.2 Návrat stanic

Po **poslední** zastávce (mezi zastávkami nemá smysl čekat na stanice, které za chvíli
zase spadnou) dostanou stanice AP čas na re-asociaci. Čeká se jen na rozhraní, která
stanice měla i předtím — prázdná tabulka AP bez klientů by se čekala donekonečna.
Nevrácené stanice shodí verifikaci, takže dávka zastaví: zařízení je v pořádku, protistrana
ne, a někdo se na to má podívat.

Čekání jde vypnout — na stránce *Hromadný update* přepínačem **Čekat na návrat stanic**,
v `mqs fleet` volbou `--no-wait-stations`. AP je pak hotové, jakmile mu naběhnou rádia
(kontrola rádií z 5.1 platí dál), a stanice, které se nevrátí, dávku nezastaví.

---

## 6. Hledání zařízení v síti

`src/mqs/discover/`. Výstup detekce je vstup update planneru.

### 6.1 Tři stavy, jen jeden se aktualizuje

| stav | kdy | update |
|---|---|---|
| `CONFIRMED` | přihlášeno, data čtená přímo ze zařízení | **ano** |
| `PROBABLE` | pasivní zdroje se shodují, nikdo se nepřihlásil | ne |
| `UNKNOWN` | odpovídá, nic neukazuje na MikroTik | ne |

Otevřený port je `UNKNOWN` záměrně: 8291 je Winbox jen zvykem a na 8729 může poslouchat
kdokoli. MAC OUI je slabý signál ze stejného důvodu. Zdroj, který hlásí `platform` jiného
výrobce, se bere vážně — MNDP mluví i cizí technika.

### 6.2 Zdroje

| zdroj | co umí | výchozí |
|---|---|---|
| MNDP (UDP 5678) | pasivní poslech + aktivní dotaz, jen vlastní L2 doména | **zapnuto** |
| `/ip neighbor` z už dosažených routerů | vidí za první rádiový skok | se `--login` |
| registrační tabulky AP a spojů | stanice + **hrany topologie** | se `--login` |
| SNMPv2c | `sysObjectID` pod `1.3.6.1.4.1.14988` | `--snmp-community` |
| sken portů 8729/122/22/8291 | kde zkusit přihlášení | `--scan-ports` |

MNDP je zapnuté, protože je pasivní, lokální a nic nestojí. Všechno ostatní chce rozsah,
community nebo přihlašovací údaje — tedy rozhodnutí. Každý zdroj běží ve vlastním vlákně
s vlastním deadlinem; ten, který selže nebo se zasekne, stojí svůj timeout a nic víc.

Sken portů je omezený na padesát adres za sekundu a odmítne rozsah větší než /20 — podle
prefixu, ne podle vygenerovaného seznamu, aby `/8` nezatuhlo okno na jedenáct sekund.

### 6.3 Deduplikace

Slučuje se podle **sériového čísla**, jinak podle **sdílené MAC**. Podle IP adresy
nikdy: adresy se stěhují a přidělují znovu. Dvě zařízení na jedné adrese se proto
zobrazí jako konflikt a nechají oddělená — rozdělit jedno zařízení na dva řádky stojí
duplicitu, sloučit dvě do jednoho stojí update naplánovaný ze stavu cizího zařízení.

Stejná MAC s jiným sériovým číslem = jiné zařízení (přesunutá karta nebo deska).

Výjimka pro to, co hlásí sousedé přihlášených zařízení: neighbor tabulka uvádí MAC portu,
na kterém souseda slyšela, MNDP MAC portu, ze kterého se ohlásil. Jeden hAP ac³ tak přišel
se třemi MAC (…:18, …:1B, …:1C) ze tří míst jednoho switche (2026-10-12). Takový záznam se
proto přiřadí ke známému zařízení i podle **identity a adresy zároveň** — samotná adresa
nestačí nikdy.

### 6.4 Klasifikace

**Schopnost první, název druhý.** Zařízení je bezdrátové, protože má rádia — tečka.
Mapovací soubor `wireless_models.yaml` jen dodá kategorii, takže neznámá deska se chová
stejně jako známá. Známé jméno bez rádia se označí jako „název napovídá" a nic víc: buď
se nikdo nepřihlásil, nebo je jméno zavádějící.

Role se bere z režimu, který rádio hlásí, takže parabola nastavená jako AP **je** AP. AP
se stanicí navíc je pořád AP — nese na sobě jiná zařízení, a to rozhoduje o pořadí.

### 6.5 Pořadí updatu

Od listů ke kořeni. AP aktualizované před svými stanicemi je na dvě minuty odstaví, a
stanice, která pak selže, selhala mimo dosah. Zařízení, za kterým sedí tenhle počítač
(`--here`), jde poslední bez ohledu na hloubku.

Neúplný strom — víc kořenů, osiřelé zařízení, smyčka — udělá z pořadí **návrh**, který
chce `--confirm-order` (v okně opsání cílové verze). Smyčka se hlásí, nikdy se tiše
nerozpojí: která hrana je ta stará, není co hádat.

Hrany stromu vznikají při přihlášení: zařízení se zeptá na `/ip neighbor` a registrační
tabulky. Neighbor tabulka ale neříká směr — vidí souseda nahoře i dole. Rozhoduje proto
port, kterým přišla relace tohoto počítače: co zařízení vidí **na něm**, je vedle něj nebo
nad ním (switch s PC, router nad ním, AP, na kterém visí stanice), a hranou se nestane. Za
zařízením je jen to, co vidí na ostatních portech. Bez známého portu relace se za zařízení
nepočítá nic. Stejně se zahodí dvojice sousedů, kteří se vidí navzájem, a tenhle počítač
sám (odpovídá na discovery taky). Na jednom switchi tedy strom nemá žádnou hranu a pořadí
je poctivě jen návrh — tak to vyšlo na dvou hEX S v labu 2026-10-12.

Plán se ukazuje z inventáře, tedy z posledního přihlášení. Než se na zařízení cokoli
nahraje, naplánuje se znovu z toho, co hlásí teď; jiná trasa dávku zastaví dřív, než se
zařízení dotkne.

### 6.6 Inventář

JSON v `%LOCALAPPDATA%\MikrotikQuickSetPlus\inventory.json`, export do CSV (středníky,
kvůli českému Excelu) i JSON. Druhý scan hlásí, co se změnilo; nahoře jsou dvě vážné
změny:

- **zmizelé rádio** — to, kvůli čemu celá aplikace existuje,
- **verze dolů** — něco se nainstalovalo, co nikdo neplánoval.

Zařízení, které se neozvalo, se **nemaže**, jen označí: MNDP se nezachytí stejně často,
jako se zachytí, a záznam zmizelý kvůli špatnému portu na notebooku je horší než zastaralý.
Rádio se nehlásí jako ztracené, když se nikdo nedíval.

`mqs scan` končí nenulově, když najde vážnou změnu — spouští se z plánované úlohy a něco
si toho musí všimnout.

---

## 7. Co otestovat na jedné anténě v labu, než se to pustí do sítě

V tomhle pořadí, protože každý krok je méně vratný než ten předchozí:

1. `mqs scan --login` — ověřit, že se rádio vůbec načte a v tabulce sedí režim i počet
   stanic. **Tohle je ten krok, který ověří pole, co nejsou ověřená**: `mode` na
   `/interface wifi` a názvy sloupců registračních tabulek.
2. `mqs version --dry-run --to <o patch výš>` — vypíše rádia, trasu a sadu balíčků. Nic
   nemění. Zkontrolovat, že v seznamu souborů **je ovladač**.
3. Totéž s `--download`, pokud se mají balíčky stahovat.
4. Ostrý běh na **stanici**, ne na AP — selhání stanice stojí jeden spoj, selhání AP
   všechny za ním.
5. Teprve pak na AP, a sledovat, jestli se registrační tabulka vrátí.
6. `mqs fleet --dry-run` nad hotovým inventářem — zkontrolovat pořadí a strom.
7. Dávka na dvou zařízeních s `--parallel 1`.

Co bych čekal, že se při tom opraví: názvy polí z bodu 1, a jestli `wireless` opravdu
potřebuje vlastní soubor i na 7.12 (sekce 2.3 to předpokládá bezpečnou cestou).
