# Blender Android — Z Fold v0.1.0

## Bas och status

- Fork: `lillsvahn/blender_for_android-z-fold`, arbetsbranch `zfold-v0.1.0`.
- **Exakt bas:** `76dc70df95ae32dcd15f3f86abfd82f4c5691140`.
- Verifierad upstream-release: `v5.3.0-alpha.2-android.20260901`.
- Ingen senare upstream/main har blandats in. Blender/Python runtime-versionen
  förblir `5.3`/`3.13`; inga DNA-fält eller allmänna editor-layouts ändras.
- Egen APK-version: `5030100`, `Z Fold 0.1.0 (Blender 5.3 Alpha 2)`.
  Paketnamnet förblir `org.blender.blender`.
- Källkod och billiga kontroller är färdiga. **Ingen Full Android-build eller
  fysisk Fold-verifiering har körts.** Ingen färdig APK levereras ännu.

## Ändrade filer och ansvar

| Filer | Ansvar |
| --- | --- |
| `build_files/android/apk/app/src/main/java/org/blender/blender/BlenderActivity.java` | System-IME toggle, befintlig composing/commit, Enter/Paste och Copilot-lifecycle |
| samma Java-katalog: `CopilotBridge.java` | Android Keystore, HTTPS, cancel, poll, rate-limit |
| `intern/ghost/intern/GHOST_AndroidMain.cc` | Java-text till vanlig UTF-8 |
| `intern/ghost/intern/GHOST_SystemAndroid.cc`, `.hh` | IME FIFO, bulk-text, touch-filter, JNI/Python-brygga, clipboard UTF-8 |
| `intern/ghost/intern/GHOST_AndroidMobile.hh` | Finger-ägande, två analoga axelpar, Frame-knapp, release/cancel |
| `intern/ghost/GHOST_Types.hh`, `intern/ghost/CMakeLists.txt` | Internt text-event och Android-källista |
| `source/blender/windowmanager/intern/wm_virtual_keyboard.cc` | Android KEYBOARD till system-IME; behåll intern fallback och desktop-kod |
| `source/blender/windowmanager/intern/wm_event_system.cc`, `wm_event_types.hh` | Köat multiline-commit till befintlig Text Insert |
| `source/blender/python/intern/bpy.cc` | Android-only privata `_bpy`-funktioner |
| `scripts/modules/bl_android_navigation.py` | Viewpoint-matematik, GPU-overlay, normal Frame Selected |
| `scripts/modules/bl_android_copilot/{__init__,registry,gemini,runtime}.py` | Lokala tools, separat provider-adapter och användarstyrd loop |
| `scripts/startup/bl_android_zfold.py` | Inbyggda N-paneler, registrering, referensbild och små runtime-inställningar |
| `build_files/android/{build.py,build_apk.sh,fastdeploy.sh,deps/build.sh}` | Respektera provisionerad SDK/NDK, begränsa parallellism och gemensam signering |
| `build_files/android/apk/{package.sh,sign.sh}`, manifest | Kompilera båda Java-klasserna, färsk payload, stabil signering/version |
| `build_files/android/zfold_preflight.py`, `tests/*` | Billiga kontroller, resursgate och kontroll av färdig APK |
| `.github/workflows/zfold-android.yml` | Endast manuell Full ARM64-build på provisionerad runner |
| `ANDROID_AI_GUIDE.md`, detta dokument | Hänvisning och projektspecifik överlämning |

`space_statusbar.py`, `interface_handlers.cc` och vanliga keymaps är oförändrade:
deras befintliga operator/IME-vägar återanvänds.
`build.py` anropar shell-skripten genom `bash`, eftersom basens Git-filer saknar
executable-bit. Ett färskt checkout behöver därför ingen odokumenterad `chmod`.

## System-IME

KEYBOARD ligger kvar i den befintliga fasta LEFT-regionen i statusbaren.
Android-grenen av `WM_virtual_keyboard_toggle` stänger eventuell intern
fallback och anropar Activity `toggleKeyboard()`. Den läser faktisk IME-
synlighet via WindowInsets, så Android Back följt av KEYBOARD fungerar utan en
separat bool som kan bli fel. Befintliga `showKeyboard()`, `hideKeyboard()`,
InputView och composing-diff används fortfarande.

Tap på den fasta KEYBOARD-regionen fångas före UI-handlers. Därmed behåller
textfält och editor sitt input-context när knappen trycks. Desktop fortsätter
använda originalets interna tangentbord. I ett gammalt sparat fönster med endast
en statusbar-region hittas KEYBOARD genom befintlig UI button/operator-identity,
utan gissad knappbredd. Popup-regioner över knappen behåller sitt touch-ägande.

Java keys och commit-text går i **samma FIFO**, tömd på Blender-tråden. Det
förhindrar att composing-rättelser infogar text före sina backspace-events.
CRLF normaliseras, Enter och delete följer befintliga key-events. Java String
översätts till vanlig UTF-8, inklusive tecken utanför BMP.

Multiline `commitText` blir ett köat bulk-event. I ett Text Editor WINDOW utan
aktivt UI-fält/popup anropas befintlig `TEXT_OT_insert` med hela texten i
ExecDefault: bevara indentering/brackets och en undo-operation. Övriga fält och
Console får den befintliga key-routing i rätt köordning. IME `performContextMenuAction`
Paste skickar Ctrl+V till Blenders befintliga Paste-operator och befintliga
Android ClipboardManager-brygga. Ingen egen clipboard-manager finns.
Modifier keys, F1–F12 och Numpad-period mappas för system-IME-genvägar.

## Mobile Navigation

Slå på **3D Viewport → Sidebar/N → Z Fold → Mobile Navigation**.
Default är av för att bevara Alpha 2:s vanliga navigation tills användaren väljer
läget. GPU-overlay och hit-cirklar finns bara i Viewport WINDOW-regioner; öppna
Sidebar/Tools-regioner undantas från deras tillgängliga yta.

GHOST får verkliga Android pointer-ID:n före äldre pan/pinch. Endast fingrar som
börjar i en kontroll ägs av den kontrollen. MOVE och LOOK kan hållas samtidigt;
en extra finger på upptagen stick stjäl inte första fingern. Utanför cirklarna
går single-touch vidare normalt. Musens befintliga tidiga kodväg behålls och S
Pen-pointerdata tas från rätt kvarvarande pointer. Pinch i aktiva Viewport-
ytor konsumeras innan den kan generera magnify/zoom.

Privata `_bpy.android_mobile_layout/state` överför små numeriska rader mellan
GHOST och den inbyggda Python-timern, på samma tråd. Movement flyttar eye och
view target relativt aktuell riktning. LOOK ändrar yaw/pitch och behåller eye,
med world-Z som up och pitch begränsad till ±89°. Ingen roll, ändring av lens,
FOV eller view-distance används som movement. Första rörelsen går till PERSP
utan att flytta en verklig scene-camera. FRAME använder exakt
`bpy.ops.view3d.view_selected('EXEC_DEFAULT', use_all_regions=False)`.

UP nollställer axlar; cancel, disable, resize och focus-loss tar bort rörelse.
Gamla pointer-äganden behålls till release för att hindra läckta editor-klick.
Ett nytt Android ACTION_DOWN återställer en tidigare förlorad input-sekvens.
Timern går vid 30 Hz med högst 0,04 s rörelsesteg och orsakar ingen kontinuerlig
viewport-redraw när kontrollerna och Copiloten är inaktiva.

| Runtime-värde | Default | Enhet |
| --- | --- | --- |
| Movement Speed | 3,0 | Blender units/s |
| Look Sensitivity | 1,6 | radians/s vid full stick |
| Joystick Size | 140 | diameter i UI-pixlar, begränsas för att rymmas |
| Joystick Opacity | 0,45 | alpha |

Värdena, toggle, provider/model och Max Iterations sparas atomiskt i
`bpy.utils.user_resource('CONFIG')/zfold_v0_1.json`. API key, Goal och referens-
bild sparas inte där. Inga nya preferences/DNA-strukturer krävs.

## BlenderToolRegistry och Copilot

Registry är provider-oberoende: varje `BlenderTool` har name, description,
JSON input/output schema, executor och mutating-flagga. Det importerar ingen
Gemini-kod. Validering och exekvering kräver Blender main thread.

| Tool | Input / resultat |
| --- | --- |
| `inspect_scene` | limit/offset; kompakt aktuell scen, mode, urval, objekt, transforms, collections, units och viewport |
| `inspect_object` | namn i aktuell scen; transforms/world bounds, hierarchy, modifiers, materials och grund-mesh; tydligt missing-object-fel |
| `capture_viewport` | max_size (default 768); GPUOffScreen med aktuell shading/view, färghanterad PNG; tydligt fel om viewport/GPU saknas |
| `mesh_stats` | object/selected/scene/collection; vertices/edges/faces/triangles från viewport-evaluated mesh och instanser; render-hidden filtreras |
| `run_bpy` | raw source; compile före mutation, undo före/efter, begränsat stdout/stderr, success, exception och traceback |
| `undo` | befintlig Blender Undo |

Copilot är automatiskt registrerad genom startup-script på Android, ingen
efterinstallation. N-panelen är kompakt och Copilot är hopfälld som default.
Provider är Gemini; **ange aktuellt model ID** från AI Studio. Ingen modell är
hårdkodad som default. En PNG/JPEG/WebP upp till 16 MiB kan väljas; en privat
bildkopia skalas till högst 1024 px och konverteras för API:t. Originalet ändras
inte. Färdig konvertering får vara högst 4 MiB.

Run startar ett jobb och ett API-anrop. Ett färdigt svar får högst sex lokala
tools och **en** muterande tool. Hela batchen valideras före körning; flera
mutationer i samma svar nekas. Continue gör nästa API-anrop först efter granskning.
Polling gör aldrig nya API-anrop. Max Iterations (default 20) stoppar fortsatt
arbete. Stop annullerar väntande nätverk och hindrar sena svar från att köra
Python. Scene och gjorda ändringar behålls. Undo använder registry/Blender.

Generated Python visas **före** körning i `AI_Copilot_Log` i Text Editor; stdout,
tool-results och traceback följer där. Review Log ger en kompakt popup. Loggen
hålls också i sessionsminne så Blender Undo inte tar bort granskningshistoriken.

Gemini-adaptern använder stödda HTTPS `v1beta/models/{model}:generateContent`.
Den skickar aktuella kompakta facts, korta senaste progress-noteringar, högst
en referens och aktuell viewport. Bara senaste modellsvaret och motsvarande
function-responses behålls för nästa tur, inklusive opaque thoughtSignature
och function-call id. Capture-bytes är inlineData, inte stora JSON/text-dumpar.
Framtida adapters kan använda samma registry; inga andra providers/MCP ingår.

## API key, nätverk och lifecycle

Set / Clear API Key öppnar en Android password-dialog. Key lagras krypterad med
AES-256-GCM i `getNoBackupFilesDir()/gemini-key.enc`; ny slumpmässig IV används
vid varje sparning. Krypteringsnyckeln är icke-exporterbar i AndroidKeyStore,
alias `blender.gemini.v1`. Android avgör hardware backing; StrongBox krävs inte.
Nyckeln återställs inte från Android backup och behöver anges igen efter
avinstallation/clear-data. Clear tar bort cipherfilen.

Ingen get-key-metod finns i JNI/Python. API key skickas endast i
`x-goog-api-key` till Google, aldrig i URL, prompt/tool-context, inställnings-JSON
eller logcat. Redirects är avstängda. API/server-fel visas som generiska status-
meddelanden utan body/headers/exceptiontext som kan återge credentials.

HTTPS kör på en Java worker; inga Blender-operationer kör där. Connect/read-
timeouts är 15/90 s och reply är begränsad till 2 MiB. Cancel/disconnect väntar
inte på nätverk på UI/main thread. Activity pause/destroy avbryter request;
generation-id hindrar gamla svar. HTTP 429/RESOURCE_EXHAUSTED sätter cooldown
från Retry-After/RPC retryDelay eller default 60 s. **Ingen automatisk retry**
finns. Användaren måste försöka igen efter cooldown.

## Billig verifiering

Kör från repo-roten:

```bash
ZFOLD_ANDROID_JAR="$ANDROID_HOME/platforms/android-35/android.jar" \
  python3 build_files/android/tests/run_checks.py
blender --background --factory-startup --threads 1 --python-exit-code 1 \
  --python build_files/android/tests/test_blender.py
```

Utfört: Python syntax, shell syntax, diff-whitespace, 12 host-tester av registry/
loop/quota/cancel/PNG, C++ touch-ägande och Java-kompilering mot SDK 35/JDK 17.
Endast sju befintliga immersive-API deprecation-warnings finns i Java.
GHOST_SystemAndroid.cc och GHOST_AndroidMain.cc passerar NDK 28.2 ARM64 API 31
syntaxkontroll med warnings-as-errors; privata `_bpy`-wrappers passerar host-
C++ syntax mot Python headers. Riktig Blender **4.5.3 LTS** verifierar inspect,
cube=12/subdivision=48 triangles, run_bpy, exception/partiell mutation, Undo, referensbild-konvertering, viewpoint-matematik,
no zoom/roll/drift, panelgeometri och två register/unregister-cykler.

Blender 4.5.3 är en billig API/runtime-kontroll, inte den avsedda 5.3 Android-
binären. Kompletta WM-filer kunde inte kompileras isolerat utan Full-profilens
dependencies/generated headers; deras ändrade API-signaturer kontrollerades i
den exakta 5.3-källan. GPU/Vulkan capture och IME-lifecycle kräver APK/device.
Inga riktiga Gemini-anrop eller quota har förbrukats i testerna.

## Build-budget och manuell workflow

`ANDROID_AI_GUIDE.md` beskriver cirka 60 GB workspace. Standard GitHub Linux-
runner garanterar **14 GB SSD**, vilket inte räcker för en pålitlig färsk Full-
build. Ingen pålitlig extra ephemeral-disk är provisionerad i denna fork; inga
disk-clearing/loop-device-hacks eller chansartade CI-försök används.
Den aktuella utvecklingsmiljön har cirka 21,6 GiB ledigt och 8 GiB RAM, saknar
Full toolchain/dependency-prefix och kan inte köra detta bygge pålitligt.

Minsta praktiska lösning: en befintlig Linux x86_64-dator/VM (även Linux i WSL2)
med minst 16 GB RAM och **cirka 100 GiB ledigt före setup**, eller en provisionerad
GitHub larger runner med exempelvis 150 GB disk. Lägg checkout, host-libraries,
SDK och BUILD_BASE på samma rymliga workspace. Exakt peak är inte mätt eftersom
en Full-build inte körts; 70 GiB fria efter SDK/source är en konservativ gate,
inte en uppmätt storleksgaranti.

Workflow har enbart `workflow_dispatch`, ingen push/pull_request-trigger. Den
kräver runner-labels `[self-hosted, linux, x64, blender-android]`; standardrunner
väljs aldrig automatiskt. Provisionskrav: JDK 17, SDK android-35/build-tools
35.0.1, NDK 28.2.13676358, GCC ≥14/Clang ≥17, CMake ≥3.26 och vanliga Linux
byggverktyg (se preflight/BUILDING.md). Exportera ANDROID_HOME, ANDROID_NDK_ROOT
och JAVA_HOME i runner-processens miljö. Sätt två parallella jobb vid 16 GB RAM.

**GitHub kräver workflow-filen på default branch för Actions UI-dispatch.**
Efter granskning kan enbart denna workflow-fil kopieras/mergas till main, eller
arbetsbranchen göras till default branch. Workflow checkar alltid ut
`zfold-v0.1.0`; main-kod ska aldrig automatiskt blandas in i bygget. Detta arbete
ändrar inte main/default branch och startar inte någon run.

Workspace-livslängd:

- En enda ARM64 dependency-prefix och en enda Full host/target/stage används.
  Linux host-lib-submodulen checkas ut vid repots pinnade SHA, inte upstream main.
- Source-LFS hämtas publikt från projects.blender.org, med guide-dokumenterade
  tomma credentials för unattended access. Ingen separat dependency-copy görs.
- Dependency download-arkiv tas bort först efter färdig installation. Work/host-
  trees behålls genom link/package eftersom generated config kan referera till dem.
  Host-codegen med matchande Full flags måste finnas tills target är klar.
- `--no-archive` undviker en extra lokal APK-arkivkopia. Inga caches eller
  dependency/build/SDK/NDK/staging artifacts laddas upp. Endast färdig APK, en dags
  retention och utan extra artifact-komprimering. Ephemeral runner rensas efteråt.

Manuell build på rätt provisionerad miljö:

```bash
export BUILD_BASE=/path/to/dedicated/zfold-build
export CMAKE_BUILD_PARALLEL_LEVEL=2
python3 build_files/android/zfold_preflight.py
# Source-LFS + lib/linux_x64 enligt ANDROID_AI_GUIDE.md/BUILDING.md.
bash build_files/android/deps/build.sh
python3 build_files/android/build.py full --reconfigure --repackage --no-archive
python3 build_files/android/tests/check_apk.py \
  "$BUILD_BASE/android_apk_stage_full/blender-full.apk"
```

**Repackage krävs** när scripts/assets/release data ändras. Java-kompilering tar
alla Java-klasser; host `__pycache__` följer inte med. Runtime-revisionen är
befintlig SHA256-hash och invaliderar device-extraction när payload ändras.
`check_apk.py` jämför de nya skripten byte för byte, revision/CRC, DEX-brygga och
ARM64/native-symboler. Den kontrollen har inte körts mot någon APK ännu.

## Stabil signering och GitHub Secrets

Skapa en egen signing key **en gång på en betrodd lokal dator**, och behåll en
säker backup av JKS/PKCS12, alias och lösenord. Lägg aldrig filen i Git.
Exempel (keytool frågar efter lösenord):

```bash
keytool -genkeypair -keystore zfold-signing.jks -alias zfold \
  -keyalg RSA -keysize 3072 -validity 10000
base64 -w 0 zfold-signing.jks > zfold-signing.base64
```

Skapa repository Actions secrets:

| Secret | Värde |
| --- | --- |
| `ZFOLD_KEYSTORE_BASE64` | innehållet i base64-filen ovan |
| `ZFOLD_KEYSTORE_PASSWORD` | keystore-lösenord |
| `ZFOLD_KEY_ALIAS` | exempelvis `zfold` |
| `ZFOLD_KEY_PASSWORD` | key-lösenord, ofta samma som keystore-lösenord för PKCS12 |

Workflow återställer privat key med umask 077 i runner.temp, verifierar alias/
store-password och åtkomst till privat key före dependency-build, signerar via `apksigner` med lösenord från
environment (inte command-line text) och raderar tillfällig JKS även vid fel.
Samma `sign.sh` används även vid fastdeploy och validation-repack. CI vägrar
generera en slumpmässig debug-key. Lokala utvecklingsbyggen utan signing-env
behåller upstreams persistenta `$BUILD_BASE/android-debug.keystore`-fallback.

Lokal release-signering kan använda `BLENDER_ANDROID_KEYSTORE`,
`BLENDER_ANDROID_KEYSTORE_PASSWORD`, `BLENDER_ANDROID_KEY_ALIAS` och
`BLENDER_ANDROID_KEY_PASSWORD`. Öka versionCode vid framtida egna releaser och
återanvänd exakt samma certifikat. Upstream-APK med annat certifikat kan inte
uppdateras direkt: första fork-installationen kan kräva upstream-avinstallation.
Spara egna .blend-filer/preferences före det; avinstallation raderar app-data.
Efter första egna installationen kan egna APK:er uppdatera den med samma key.

## Kända begränsningar

- Ingen slutlig Full ARM64-kompilering/packaging, live Gemini-validering eller
  Fold/Unexpected Keyboard/Vulkan-verifiering ännu. Kontrollera listan nedan
  innan APK:n bedöms som device-verifierad.
- Registry-statistik använder viewport-depsgraph. Render-only modifier levels,
  viewport-hidden data och ej realiserade Geometry Nodes-instanser kan skilja
  sig från en exporterad mesh. Använd stats efter relevanta modifiers och
  kontrollera exporterad asset-budget när det behövs.
- Capture visar aktuell shading och view; EEVEE/material/rendered resultat
  och GPUOffScreen på Android måste verifieras på den verkliga Vulkan-driver som
  används. Capture-fel rapporteras och hindrar inte lokala inspect/stats.
- run_bpy kräver aktiverad Global Undo och är avsiktligt inte en sandbox. Synkron lång Python/native-operation
  kan inte avbrytas mitt i main-thread-exekveringen; Stop avbryter nätverk och
  framtida actions. Kod som stänger Blender eller stänger av undo kan påverka
  recovery. Logg och checkpoints finns för vanlig bpy-modellering, även partiella
  Python-exceptioner. Användarens explicit godkända raw-bpy-kraft bevaras.
- Stop/pause kan inte återbetala ett API-anrop som Google redan mottagit. Jobb
  återupptas manuellt. Bild/text skickas endast när Run/Continue trycks.
- Navigation flyttar viewport-eye, inte ett scene-camera-objekt. Fysisk mus/
  keyboard behåller vanliga bindings; sticks ändrar aldrig zoom/lens och pinch
  i aktiv Mobile Viewport blockeras. Mycket små viewports kan sakna plats för
  kontroller; öppna större viewport eller minska Joystick Size.
- Konversationsjobb och loggminne fortsätter inte automatiskt över app-restart.

## Fysisk testlista — Samsung Galaxy Z Fold

1. **Install/update:** kontrollera version, Full ARM64, befintliga Android-
   features/assets/audio/extensions, permissions, scene load/save och stabil
   signing vid en senare APK-uppdatering. Fold/unfold/rotate och resume fungerar.
2. **IME:** välj Unexpected som Android system-IME. KEYBOARD finns kvar, öppnar
   endast systemtangentbordet, andra trycket och Android Back stänger korrekt.
   Upprepa show/hide samt popup/text-field-auto-show. Ingen intern keyboard visas.
3. **Text Editor:** skapa Text; skriv åäö/emoji, Enter, backspace, composing/
   suggestions. Paste ett långt indenterat script med blankrader, brackets och
   CRLF både från IME Paste-knapp och dess clipboard-pane; jämför texten och kör
   scriptet. Undo återställer en paste. Upprepa efter pause och Fold/unfold.
4. **Console/search/fields:** vanlig typing/backspace/Enter i Python Console;
   F3/search, object name och normala string/number-fält. KEYBOARD-tap ska behålla
   det aktiva textfältet. Testa äldre sparad statusbar-konfiguration separat.
5. **Sticks:** enable endast i 3D Viewport; MOVE forward/back/strafe relativt view,
   LOOK yaw/pitch utan roll. Håll båda; släpp en och sedan den andra. Neutral direkt,
   ingen drift. Extra finger får inte stjäla en stick. Pinch ändrar inte zoom.
   Kontrollera lens/view_distance före/efter joystickrörelse i Python Console.
6. **Frame/ownership:** select object → FRAME → fortsätt med båda sticks.
   Touch utanför cirklar når Blender. Testa flera viewports, öppna Sidebar/Tools,
   resize, byte till andra editors, Mobile off/on, cancel/pause/resume/rotation
   med fingrar nere. Återanvända pointer-ID:n ska fungera efter resume.
7. **Input regression/settings:** S Pen hover/pressure/buttons, mouse L/M/R/wheel,
   physical keyboard och DeX; normal pan/pinch när Mobile är av. Ändra alla fyra
   värden och starta om Blender; värdena ska överleva utan rebuild.
8. **Copilot/key/model:** Set/Clear key, restart, välj rätt model ID, one reference
   Select/Clear. Ingen key i config, AI_Copilot_Log eller logcat. Öppna/stäng N-
   panel och reload scripts; inga dubbla panels/timers eller registration-fel.
9. **Tools:** inspect_scene; inspect_object för verkligt och saknat namn;
   capture_viewport i Solid/Material och efter view/shading-byte; mesh_stats för
   object/selected/scene/collection och cube med modifier. Verifiera att Gemini
   faktiskt ser både referens och aktuell capture.
10. **Execution/loop:** be Gemini skapa/ändra ett testobjekt, granska generated
    Python/log och Undo. Be om avsiktlig Python-exception, granska stdout/
    traceback och scenens recovery. Ingen andra API-iteration utan Continue.
    Max=2 stoppar efter två; Stop/pause under request ska inte köra ett sent svar.
11. **API failures:** testa offline, invalid key/model och ett kontrollerat 429-
    svar/quotafall. Ingen crash eller scenförlust, ingen retry-loop; Retry-After
    respekteras. Fortsätt manuellt efter cooldown och kontrollera anropsantal.

Primära referenser: [GitHub standard runner-spec](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[Gemini generateContent](https://ai.google.dev/api/generate-content),
[function calling](https://ai.google.dev/gemini-api/docs/function-calling),
[thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures),
[Android Keystore](https://developer.android.com/privacy-and-security/keystore).
