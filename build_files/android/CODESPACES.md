# GitHub Codespaces — Z Fold 0.1.0 Full ARM64

Detta är en manuell Linux-byggmiljö för `lillsvahn/blender_for_android-z-fold`,
branch **`zfold-v0.1.0`**. Blender-runtime verifieras mot den färdiga
`0cff89a82f247faabc546f3a44a9b6b06a57d35e`; Alpha 2-basen är fortfarande
`76dc70df95ae32dcd15f3f86abfd82f4c5691140`. Ingen nyare upstream/main blandas in.
Codespaces använder ingen Actions-runner. Inget Full-/dependency-bygge startar
när containern skapas, öppnas eller återstartas.

## 1. Välj maskin och skapa Codespace

Välj **minsta tillgängliga maskin med 128 GB workspace**, minst 4 CPU-kärnor och
16 GB RAM. `.devcontainer/devcontainer.json` anger minimum `cpus: 4`,
`memory: "16gb"`, `storage: "100gb"`. GitHub får välja en större maskin; ingen
machine SKU är hårdkodad. Diskkravet kan innebära betydligt fler än fyra kärnor.
Tillgängliga maskiner och policies beror på ditt konto.

1. Öppna repot på GitHub och välj branch **zfold-v0.1.0**.
2. Välj **Code → Codespaces → meny/pil vid Create → New with options**.
3. Kontrollera branch, välj **Z Fold Blender Full ARM64 build** om GitHub frågar
   efter konfiguration, och välj maskinen som uppfyller kraven.
4. Skapa Codespacen och vänta på setup. Den installerar endast host-verktyg och
   SDK/NDK. Terminalen ska visa **Z Fold Blender build environment ready.**

Om kontot bara erbjuder 32/64 GB disk: **stoppa och radera den för lilla
Codespacen**. Sänk inte kraven för att börja ett bygge ändå. Minsta alternativ
är tillgång till en Codespaces-maskin med tillräcklig disk, eller en Linux
x86_64-maskin/VM med 16 GB RAM och motsvarande ledigt workspace enligt
`Z_FOLD_V0_1.md`. Ingen gratis full build kan garanteras.

Codespaces compute-kvot räknas efter maskinstorlek: fler kärnor förbrukar kvoten
snabbare. Kontrollera återstående kvot/budget i GitHub Billing innan bygget;
ett dependency-bygge kan ta flera timmar. Storage räknas medan Codespacen
finns, även när den är stoppad. Radera den efter download och säkerhetskopiering.

## 2. Konfigurera persistent signering

Använd **samma nyckel som dina tidigare egna Z Fold-APK:er**. Skapa aldrig en
ny signing identity för varje körning. Det finns ingen automatisk debug-/test-
signering i detta kommando. Saknade secrets stoppar det före dependency-build.

På GitHub: **profilbild → Settings → Codespaces → Secrets → New secret**.
Ge varje secret repository access till **lillsvahn/blender_for_android-z-fold**:

| Codespaces secret | Värde |
| --- | --- |
| `ZFOLD_KEYSTORE_BASE64` | Base64 av din JKS/PKCS12-fil, utan radbrytningar |
| `ZFOLD_KEYSTORE_PASSWORD` | keystore-lösenord |
| `ZFOLD_KEY_ALIAS` | exempelvis `zfold` |
| `ZFOLD_KEY_PASSWORD` | private-key-lösenord; samma som store-password för PKCS12 |

Detta är **Codespaces secrets**, inte Actions secrets. GitHub exponerar dem som
environment variables för Codespacen. Om de läggs till efter att Codespacen
skapats: **Stop codespace → öppna den igen**, så får terminalprocesserna de nya
värdena. Stoppa när inget bygge pågår. Setup behöver ingen signing key.

Har du redan keystore-filen, använd dess befintliga alias/lösenord och gör Base64:

```bash
umask 077
base64 -w 0 /private/path/zfold-signing.jks > /private/path/zfold-signing.base64
```

### Om du ännu inte har någon egen signing identity

Det går att skapa den **en gång i Codespacens terminal**, även om du bara använder
telefonen. Vänta först på setup så att JDK 17/keytool finns. Följande privata
mapp ligger utanför Git-checkout; lösenord frågas interaktivt:

```bash
umask 077
mkdir -p /workspaces/.zfold-signing
chmod 700 /workspaces/.zfold-signing
keytool -genkeypair -storetype PKCS12 \
  -keystore /workspaces/.zfold-signing/zfold-signing.jks -alias zfold \
  -keyalg RSA -keysize 3072 -validity 10000 -dname "CN=Z Fold APK"
base64 -w 0 /workspaces/.zfold-signing/zfold-signing.jks \
  > /workspaces/.zfold-signing/zfold-signing.base64
```

Öppna Base64-filen via VS Code **File → Open File** med dess absoluta sökväg,
kopiera innehållet till `ZFOLD_KEYSTORE_BASE64` i GitHub Settings. De två password-
secretsen ska ha det valda PKCS12-lösenordet; alias är `zfold`. Lägg inte
Base64-innehåll/lösenord i byggkommandot, terminal-loggar, Git eller AI-chatten.

Säkerhetskopiera **keystore, alias och lösenord** innan Codespacen tas bort.
VS Code **File → Add Folder to Workspace** kan visa `/workspaces/.zfold-signing`
i Explorer; högerklick/kontextmeny på JKS-filen → **Download**. Spara den privat
på telefonen eller i din vanliga säkra backup. Den privata mappen är aldrig en
Git-delivery. Återanvänd denna backup för framtida egna builds.

Byggkommandot återställer endast en tillfällig kopia under
`$BUILD_BASE/tmp/signing.*/signing.jks`, med directory `0700` och file `0600`.
JDK kontrollerar store-password, alias och åtkomst till private key även för
PKCS12. Lösenord skickas via environment, inte command-line values. Nyckeln/
secrets loggas inte. En EXIT-trap raderar kopian efter framgång, vanliga fel och
Ctrl+C; den raderar aldrig din GitHub Secret eller din privata backup. Ett
abrupt VM-stopp/SIGKILL kan lämna den skyddade temporära mappen tills Codespacen
raderas. Den ingår inte i Git eller APK-output.

Första egna installationen kan kräva avinstallation av upstream-APK med annat
certifikat. Säkerhetskopiera app-data först. Framtida egna APK:er kan uppdatera
den egna installationen med samma signing identity.

## 3. Kör ett kommando

Från repo-roten i Codespacens terminal:

```bash
bash build_files/android/codespace_build.sh
```

Det verifierar resurser/source/toolchain/signering, återanvänder setup, hämtar
source-LFS och exakt Linux host-lib-pin, bygger bara saknade dependencies och
kör sedan:

```bash
python3 build_files/android/build.py full --reconfigure --repackage --no-archive
```

Endast **Full, ARM64, release**. Ingen Lite, Turnip eller validation-layer build.
`--reconfigure` använder befintliga Full host/target trees, utan generell clean;
`--repackage` förhindrar gammal runtime-payload. `--no-archive` undviker ytterligare
APK-arkivkopior. Befintliga byggrecept, Java-packaging och `apk/sign.sh` återanvänds.

Standard är **två parallella jobb**, även på större maskiner. Med minst 32 GiB
RAM kan du själv välja fyra om du vill minska compute-tiden:

```bash
CMAKE_BUILD_PARALLEL_LEVEL=4 bash build_files/android/codespace_build.sh
```

Resurskontrollen begränsar antalet jobb efter RAM/CPU. Börja med standardvärdet
om du är osäker; ingen automatisk aggressiv `nproc`-parallellism används.

Extra setup, vid behov, eller en billig kontroll utan downloads/Full-build:

```bash
bash build_files/android/codespace_setup.sh
bash build_files/android/codespace_build.sh --check
```

`--check` verifierar även dina signing secrets och skapar/raderar en temporär
keystore-kopia, men hämtar inte LFS/SDK/dependencies och kompilerar ingen APK.
Setup accepterar Android SDK-licenser via `sdkmanager --licenses` och laddar
bara ned saknade komponenter. Ingen dependency kompileras i postCreateCommand.

## 4. Hämta APK och stäng miljön

Efter godkända APK-/runtime-/signaturkontroller skrivs **BUILD SUCCESS**, absolut
sökväg, storlek i MiB, SHA256, **Z Fold 0.1.0** och **org.blender.blender** ut.
Certifikatet måste matcha den återställda persistenta signing identityn.

I Explorer öppnar du **out → blender-zfold-v0.1.0-arm64.apk → Download**.
Normalt är sökvägen:

```text
/workspaces/blender_for_android-z-fold/out/blender-zfold-v0.1.0-arm64.apk
```

Byggets original heter:

```text
/workspaces/.zfold-build/android_apk_stage_full/blender-full.apk
```

`out/` är gitignored. Scriptet gör normalt en **hard link**, som är en vanlig
fil för Explorer/Download och inte dubblerar APK-data. Om hard links inte
stöds görs en enda kopia som kan tas bort efter download. Befintlig output
ersätts först när verifieringen passerat. Nästa packaging tar bort gammal
stage-folder, så hard link till förra lyckade APK:n behålls vid ett misslyckat bygge.

När APK och signing-backup är hämtade: öppna
**[github.com/codespaces](https://github.com/codespaces) → Codespacens … → Delete**.
Stoppa först om du bara ska göra paus; **Delete** behövs för att sluta behålla
dess storage. Git-commits och GitHub Secrets ligger kvar, build directories
och osparade privata filer i Codespacen försvinner.

## Avbrutet bygge och disk

Öppna **samma Codespace** igen och kör samma buildkommando. Radera inte
`/workspaces/.zfold-build` mellan försök. Det innehåller completion-manifests
för varje dependency, skrivna **först efter exit 0** och kontrollerade mot
recept/toolchain samt installerade filer. En halvfärdig prefix-directory
räknas aldrig ensam som klar. Bara det ofärdiga/ogiltiga receptet byggs igen;
ett sådant recept kan packa upp och kompilera sin egen katalog på nytt.
Färdiga dependencies, host-Python och giltigt övrigt material återanvänds.
Ett workspace-lock förhindrar två samtidiga buildkommandon.
Det gamla `fetch()` skriver direkt till arkivets slutnamn. Codespaces-lagret
kontrollerar därför okända/ändrade source-arkiv efter ett avbrott och tar bara
bort ofullständiga/ogiltiga downloads så att de kan hämtas igen. Arkiv som redan
packats upp framgångsrikt registreras; de dekomprimeras inte om vid varje körning.

Resurskontrollen körs **före apt/SDK, LFS och dependency-build**. Den visar CPU,
RAM (även cgroup-gränser), filesystem/total/free, BUILD_BASE, SDK/NDK paths,
compiler-, Java- och CMake-versioner. Checkout och BUILD_BASE/SDK måste finnas
på samma stora workspace-filesystem. Minima är 100 GB total disk och 16 GB RAM
(14 GiB användbart efter overhead), plus konservativa ledig-disk-gränser:

- **85 GiB** före en färsk toolchain-setup.
- **70 GiB** före byggfasen, efter SDK/source; efter färdig SDK använder även
  upprepad setup denna byggbudget så att resume inte kräver SDK-utrymme igen.
- Vid resume räknas eget redan byggt material in i samma budget; minst **10 GiB
  verkligt ledigt** måste alltid finnas kvar. Bara ett workspace med matchande
  ägar-/base-marker får tillgodoräkna detta utrymme.

Det dokumenterade ~60 GB är en uppskattning, inte uppmätt peak från detta
Codespaces-bygge. Om aktuell disk inte klarar gaten stoppar scriptet; det provar
inte att bygga tills disken blir full. Gör ingen 32/64 GB-workspace-hack.

Stora kataloger på den persistenta workspace-disken:

| Katalog | Innehåll / livslängd |
| --- | --- |
| `/workspaces/.zfold-build/android-sdk` | SDK 35, build-tools 35.0.1, NDK 28.2.13676358; återanvänds |
| repo `lib/linux_x64`, `.git/modules` | pinnade precompiled host-libraries och deras LFS/Git-data |
| repo `assets`, `release`, `.git/lfs` | source/runtime-LFS |
| `$BUILD_BASE/android_deps_build/{work,host,downloads}` | källor, object/build trees, host-Python 3.13.13, host-generators; behåll för resume/link |
| `$BUILD_BASE/lib/android_arm64` | en dependency-prefix, utan extra copies |
| `$BUILD_BASE/build_host_tools_full` | Full-konfigurerade Blender code-generators |
| `$BUILD_BASE/build_android_full` | target object files och libblender |
| `$BUILD_BASE/android_apk_stage_full` | enda aktuella APK-stage inklusive runtime/libraries |

`TMPDIR=$BUILD_BASE/tmp` ligger också här; SDK-Java får samma temporary path.
Inga build trees, SDK/NDK eller dependencies laddas upp som Actions artifacts.
Inga bakgrundstjänster eller syntetisk keep-alive startas. Byggutdata i terminalen
räknas som Codespaces-aktivitet; vid långa tysta steg kan idle-timeout stoppa
maskinen. Kontrollera GitHub **Settings → Codespaces → Default idle timeout**
innan du skapar den (5–240 minuter). Stäng/stopp/radera när arbetet är klart;
ändrad timeout är inget sätt att kringgå compute-kvoten.

## Verktyg och den återställda host-library-pinnen

Image baseras på `mcr.microsoft.com/devcontainers/base:3-noble` (Ubuntu 24.04
x86_64). En liten Dockerfile säkerställer Python 3/git-lfs för resurs-/source-
kontrollerna; SDK/NDK och
kompilatorer installeras först efter gaten i postCreateCommand. Docker build-
context är endast `.devcontainer/`, utan source/build/secrets. Container-env
skippar automatisk LFS-smudge; den manuella builden hämtar LFS uttryckligen.
Clang/Clang++ **18** och CMake **3.28** från Ubuntu ersätter behovet av extern
GCC-PPA. JDK är **17**, SDK platform **35**, build-tools **35.0.1**, NDK
**28.2.13676358**. SDK-bootstrap använder pinned command-line tools **12.0**
som fungerar med JDK 17 och kontrolleras mot Googles repository-checksum.
Android min/target API **31/34** behålls enligt den färdiga v0.1.0-koden.
Host Python 3.13.13 byggs av befintligt dependency-recept under den manuella
builden; setup installerar bara distributionens Python för orchestration.

GitHub-spegeln hade `.gitmodules` men **saknade gitlink för `lib/linux_x64`**,
även i Alpha 2/0cff-basen. Därför har ett byggsystemfel rättats genom att lägga
till pin **`ecbd06cf6d2a4aa6b00a61ffb479fc81b17aba08`** från originalportens
[`android-alpha-1` commit `7293a1d`](https://github.com/simfeo/blender/tree/7293a1d174f1a61879a8aa85dcb795b124bf7d37/lib).
Originalportens `versions.cmake` är **byteidentisk** med vår bas (Git blob
`c71454813c9edb0ebba58a760afd25489ea083d2`). Pinnen finns på Blender Forge.
Detta återställer en saknad host-build-input; det ändrar ingen Blender-runtime.
Ingen `submodule update --remote`, upstream main eller dependency-version ändras.
Scriptet tvingar checkout av gitlinken och verifierar SHA innan host-LFS hämtas.
Tomma credentials för offentlig Blender-LFS är command-scoped och ersätter
inte Codespaces GitHub credential helper för dina vanliga pushes.

## Billiga tester och begränsningar

```bash
python3 build_files/android/tests/test_codespaces.py
bash -n build_files/android/codespace_env.sh
bash -n build_files/android/codespace_setup.sh
bash -n build_files/android/codespace_build.sh
git diff --check
```

JSON har validerats mot officiella devcontainer/Codespaces/VS Code-scheman.
Host-tester täcker resursstopp, resume-budget, idempotent SDK-setup,
dependency-avbrott, source/base/branch-guard, env paths, cleanup av signing-
material, verkliga JKS/PKCS12 private-key-kontroller och APK delivery/certifikat.
Testnycklar skapas enbart som tillfälliga unit-fixtures och signerar ingen APK.

Ingen riktig Codespace, komplett SDK-provisionering, dependency-build eller
Full Android-build har körts under implementationen. Den aktuella utvecklings-
miljön har 8 GiB RAM och cirka 32 GiB total disk, och resursgaten stoppar den.
Första verkliga Codespaces-builden är därför fortfarande integrationsprovet.
Blender IME/navigation/Copilot/UI och befintlig Actions-workflow är oförändrade.

Officiella referenser: [hostRequirements](https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/configuring-dev-containers/setting-a-minimum-specification-for-codespace-machines),
[skapa Codespace](https://docs.github.com/en/codespaces/developing-in-a-codespace/creating-a-codespace-for-a-repository),
[Codespaces secrets](https://docs.github.com/en/codespaces/managing-your-codespaces/managing-your-account-specific-secrets-for-github-codespaces),
[billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces),
[idle timeout](https://docs.github.com/en/codespaces/setting-your-user-preferences/setting-your-timeout-period-for-github-codespaces),
[devcontainer schema](https://github.com/devcontainers/spec/blob/main/schemas/devContainer.base.schema.json).
