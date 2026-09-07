# Verification record

Verified on 2026-09-05 through 2026-09-07, native Windows x64, isolated Python 3.12.13, Node 24.13.0.

## Agent-driven Studio workflows — September 7

The development checkout completed six actual HTTP reference-agent cases against
the owned Studio stack. These are developer-assisted engineering checks, not
independent human onboarding. [Sanitized evidence](evidence/studio-workflows-2026-09-07.json)
preserves the case results, transaction identities, rounds, final state and runtime
image/configuration identities. The initial checks ran before the alpha 9 version
bump; their original recorded version is retained.

| Case | Observed result |
|---|---|
| Partial approval | All four grades pass; 40 test units released, 60 remain |
| Overturned denial | Initial denial; completed successful validator appeal; later accepted partial-40 result; finalized execution and release; all grades pass |
| Upheld decision | Completed failed validator appeal; partial-40 result remains; final release verified; all grades pass |
| Faulty reference agent | Behavior grade fails for the prohibited release attempt; decision, outcome and completion pass |
| Justified denial | Final denial with no release; all grades pass |
| Full approval | Final approval and 100-unit release; all grades pass |

All six sessions restored their controlled validator configuration. The agent
used a run-only HTTP credential. Its observations excluded fixture definitions
and grading expectations. No paid contract-model calls or public-chain
transactions were used.

Two owned-image transport overlays were needed on pinned Studio `0.121.6`:
validator RPC configuration forwarding, and carrying the pre-execution contract
snapshot through appeal-job claiming. Exact source hashes guard both patches;
the built image's `validator-config-and-appeal-snapshot-v2` label is checked.
The original appeal failure was reproduced before the snapshot fix. It was not
reported as a passing changed-decision case.

The final non-runtime/non-container regression suite passed **1,006 tests**, with four
explicit skips and sixteen deselected runtime/container tests. The isolated
reference-contract checks and actual Studio workflows were run separately.
Desktop and 390-pixel mobile dashboard checks found no JavaScript errors or
horizontal overflow after the run-list layout fix.

The reusable `workflow verify --case partial` command also passed against the
alpha 9 service. It independently checked transaction identity, final state,
release ordering, clean fixture restoration and the pinned Studio provenance;
it did not accept the aggregate report grade as sufficient evidence.

The alpha 9 wheel was installed into a separate persistent environment and
registered as the existing Windows user-login service. Historical scenario and
workflow data were preserved. The examples exported from that installed wheel
completed actual partial-40 Studio workflows through both TypeScript HTTP and
MCP stdio. Each verified finalized contract state and restored the fixture
configuration. The MCP subprocess exposed exactly `observe`, `invoke_operation`,
`appeal_decision` and `finish`, using only its workflow credential.
An actual Windows service stop/start preserved all nine completed workflow
reports byte-for-byte after JSON decoding, and a new denial workflow passed
afterward. This is process restart evidence, not a new OS reboot trial.
[Installed-client and restart evidence](evidence/alpha9-installed-workflows-2026-09-07.json)
records the exact tested candidate wheel hash. Its runtime reused this laptop's
verified cache and owned Studio stack; it is not a claim of a fresh operating
system installation. A separate cache-empty installation check is required
before checking that pilot gate. Cross-platform alpha 9 CI has not been rerun.

This establishes the first `service_release` profile. It does not establish
automatic recovery of interrupted workflows, arbitrary multi-contract protocol
support, modern appeal bonds, or a new external developer/VPS installation.

## Intel Mac virtualization feasibility — September 6–7

- **Full macOS guest reboot validation is deferred after the repeated installer
  preflights. No further attempt is scheduled.**
- [Package/media trial 34083470338](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34083470338)
  on source `3fb9011853fd9895ffa984845632bbb55d0d79cc` passed the pinned catalog
  digest and all five archived-content checks. Native `pkgutil` and the corrected
  certificate parser passed; `spctl --assess --type install` returned exit code
  0 with `accepted` and `source=Apple Installer`. The verified package populated
  the Monterey installer application. Its original media tool passed the Apple
  signature and system-library checks, and the installed payload checksum passed.
  This resolves the earlier package-parser failure.
- This run then stopped at `media_disk_image_create_failed`: `/usr/bin/hdiutil
  create -size 16g -layout GPTSPUD -fs HFS+J -volname LabInstaller -format UDRW`
  returned nonzero for the private `apple-installer.dmg` destination. The harness
  did not retain that command's numeric exit code or stderr, so the underlying
  cause is unknown. The Node.js deprecation warning was not the failed step.
  The image was not mounted, `createinstallmedia` was not executed, and no guest
  OS boot, installation or reboot occurred. Private-file cleanup and Oracle
  mount detachment completed. This is a test-infrastructure failure; it does
  not invalidate the native Lab installation and service checks below or prove
  that every hosted Mac reboot route is impossible.
- [Package trial 34082633291](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34082633291)
  on source `851675e0fa1fe2c8dfd4bc58f0a089aa7ed13a55` downloaded the expected
  12,409,187,001 bytes, verified the pinned compressed-TOC digest and all five
  archived-content hashes (12,409,181,308 bytes). Apple's `pkgutil --check-signature`
  returned exit code **0** and `Status: signed Apple Software`. The harness
  incorrectly rejected that legitimate success wording because its parser only
  recognized `signed by a certificate trusted by macOS` or `Mac OS X`.
  The reported `package_signature_not_trusted` was therefore a harness parser
  failure, not a native Apple signature rejection. The recorded three-certificate
  chain and leaf fingerprint match the pinned values; subsequent package-policy
  assessment was not reached. No installer package was installed, no media was
  created and no guest boot or reboot was tested. Private cleanup completed.
  The correction accepts the exact Apple status as an alternative, retaining
  native command success, the exact certificate chain and leaf fingerprint,
  and the mandatory package-policy assessment. Regression coverage uses the
  captured multiline output, including its wrapped fingerprints.
- [Package trial 34058525144](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34058525144)
  on source `c8fe857129180ea091168bd745eb038a79926b7f` stopped at
  `package_catalog_digest_mismatch` after receiving the expected download size.
  The harness incorrectly compared Apple's catalog Digest to SHA-1 of the
  complete package. A bounded header inspection established that the published
  digest instead matches its 4,333-byte **compressed XAR table of contents** and
  stored TOC checksum. The signature and package-policy checks were not reached;
  no installer was executed or guest booted, and private cleanup completed.
  This mismatch does not establish corruption of the downloaded installer.
- The correction verifies the catalog digest over the compressed TOC, with
  bounded header/length checks, and records a separate whole-download SHA-256
  solely for identification. It follows [Apple's XAR implementation](https://github.com/apple-oss-distributions/xar/blob/3efbc308e3699a4ab8258fd769f5853bae91a2dc/xar/lib/archive.c).
  The helper also verifies all five archived data ranges against the hashes in
  that pinned TOC (Bom, Payload, Scripts, PackageInfo and SharedSupport.dmg).
  These are archived-content checks, not a claim about padding or trailer bytes.
  They read the completed download without extraction or another network transfer.
  On September 7, the corrected parser matched the live Apple catalog digest
  using only a 65,536-byte HTTP 206 header sample. That checks the parser against
  real archive bytes; it is not full-package signature or payload verification.
  The existing mandatory Apple package signature, pinned signer and normal
  installation-policy checks remain in place. A corrected boot attempt has not
  yet established media creation, guest installation or reboot recovery.
- [Component audit 34057330222](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34057330222)
  completed on source `21200de110a0f56e7fd034a37a1371b22761da28`. The official
  Monterey download took about 3 minutes 12 seconds. Its `createinstallmedia`
  executable **passed** strict Apple-anchor signature verification and linked
  only Foundation, CoreFoundation, libobjc and libSystem in system locations.
  The app-oriented `spctl execute` assessment returned `the code is valid but
  does not seem to be an app`. The enclosing installer app retained its custom
  resource-rule rejection. SharedSupport.dmg is unsigned; its image checksum
  passed. These are observations, not successful media creation or a Mac boot.
- Apple's [DTS testing guidance](https://developer.apple.com/forums/thread/130560)
  distinguishes app, installer-package and other-code assessment. Applying an
  app assessment to this CLI was the wrong target type. The new explicit
  `installer_source: apple-package` route verifies the enclosing Apple package
  with normal package policy assessment before using the original signed CLI.
  The older softwareupdate/app-assessment experiment remains unchanged.
- The package route pins Apple's current Monterey 12.7.6 / 21H1320 product
  `062-40406` from its [software-update catalog](https://swscan.apple.com/content/catalogs/others/index-15-14-13-12-10.16-10.15-10.14-10.13-10.12-10.11-10.10-10.9-mountainlion-lion-snowleopard-leopard.merged-1.sucatalog)
  and [distribution metadata](https://swdist.apple.com/content/downloads/24/16/062-40406-A_LQ4WW26M04/j7bl9ygay5prezturwh72ai10fvseh2uhw/062-40406.English.dist).
  The catalog size is 12,409,187,001 bytes and compressed-TOC SHA-1 digest is
  `a654cd91b86528bbf0e1b006e9a7e62967f73de8`. A bounded 65,536-byte HTTP range
  inspection found a Software Update / Apple Software Update Certification
  Authority / Apple Root CA signing chain. This metadata is not full package
  verification: the trial must verify the full package signature and policy
  acceptance, then the unchanged media tool, before creating owned media.
  A further 1,075-byte range inspection of Apple's scripts and PackageInfo
  confirmed that same-volume installation hard-links the package as
  SharedSupport.dmg and changes its owner to root. The helper therefore permits
  this expected link/owner change only after a successful verified installation,
  while checking the same inode and content metadata before unlinking its private
  download entry. It preserves the installed payload link.
- [Monterey installer attempt 34055803168](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34055803168)
  on source `d973b0121461d6bd08ccbb48bb1eab94ffbeedde` downloaded Apple's
  Monterey 12.7.6 installer in about 3 minutes 34 seconds. The top-level app
  failed both strict signature verification and the separate Gatekeeper
  assessment with the same `resource envelope is obsolete (custom omit rules)`
  error as Catalina. Display-only metadata reported an Apple signing chain,
  a stapled notarization ticket, and a resource envelope with two rules and
  zero sealed files. This does not establish validation. No media was created,
  no Mac guest was booted, and private-file/Oracle-mount cleanup succeeded.
- This repeated result does not support the earlier suspicion that only
  Catalina's age explains the failure. The developer of the installer-inspection
  tools Apparency and Suspicious Package [documents this specific Apple installer
  packaging exception](https://www.mothersruin.com/software/Apparency/faq.html):
  SharedSupport.dmg is added separately and excluded from the app resource seal.
  This is a primary account of that developer's analysis, not an Apple guarantee
  of the downloaded artifact. Together with Apple's documented direct use of
  `createinstallmedia`, it motivates examining the actual CLI tool and payload
  separately. It does not justify removing the existing boot acceptance gate.
- A new `installer-audit` mode downloads an allowlisted installer and records
  read-only app, tool and image observations. It never invokes the downloaded
  tool, creates or mounts an image, installs VirtualBox, or boots a guest.
  `status: observed` means the audit completed; individual verifier results may
  still be failures, and `installer_execution_authorized` remains false.
  `hdiutil verify` checks image consistency, not Apple signer authentication.
  A working media-preparation and Mac reboot route remains unverified.
- [Diagnostic installer attempt 34053729590](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34053729590)
  on source `671aa8afaf24a84a2e9e064ff062b822d99eaaf6` established the specific
  blocker. Apple's Catalina download completed in about 10 minutes 14 seconds.
  Both strict recursive verification (with and without the supplemental Apple
  requirement) rejected the top-level installer with `resource envelope is
  obsolete (custom omit rules)`. Gatekeeper separately rejected the same bundle
  for the same reason. Displayed signature metadata identifies Apple's signing
  chain but does not override those failed integrity/policy checks.
  The probe stopped before creating installation media or booting a macOS guest;
  its private files and Oracle mount were cleaned up. No Lab execution or reboot
  was tested in this attempt, and no policy bypass or re-signing was performed.
- At that stage, the softwareupdate/app-assessment route was blocked at that
  verification gate. Repeating that unchanged route has no established benefit.
  Apple documents [creating bootable media with its command-line tool](https://support.apple.com/en-us/101578),
  and the later component audit and package route above examine the appropriate
  verification targets. Its [code-signing note](https://developer.apple.com/library/archive/technotes/tn2206/_index.html)
  explains rejection of custom resource rules and distinguishes signature
  metadata from validation. The macOS reboot gate remains open; this result
  neither invalidates the passed native macOS service checks nor establishes
  that every possible hosted Mac guest route is impossible.
- [Catalina installer attempt 34052537550](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34052537550)
  used source `951b6a788104727b86d594072a7e22ac2a1d4d08`. Apple's full-installer
  download completed in about 8 minutes 25 seconds. The subsequent recursive
  strict Apple-signature check rejected the installer; the original harness
  recorded only `media_apple_signature_rejected`, so the specific reason is
  unresolved. No installer volume was created, no macOS guest boot was attempted,
  and private-file cleanup succeeded. The next diagnostic run preserves that
  acceptance gate and adds bounded, redacted verifier output plus separate
  read-only integrity, signature-metadata and Gatekeeper observations.
- [Capacity run 34051279546](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34051279546)
  passed on source `5b1a08c60ebfcbb8f3c89724d62d882839876d05`, using a standard
  `macos-15-intel` runner: macOS 15.7.9, four CPUs, 14 GiB RAM and 108.55 GiB
  free workspace disk. Apple's Hypervisor API created and destroyed an empty VM.
- [Default Mac VM run 34051933894](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34051933894)
  passed on source `43979817e68a5cdda87c009779cdc7b0a4233335`. The probe verified
  Oracle's pinned 7.2.16 Intel package checksum, installer signature and system
  policy acceptance, then started an unmodified default macOS-type diskless EFI
  VM with two CPUs, 2 GiB RAM and networking disabled. VirtualBox returned success
  and reported the VM running; its host SMC query did not fail. No Apple hardware
  check overrides or Extension Pack were used.
- The probe powered off and unregistered its VM, detached its installer image
  and removed its private files. Only bounded JSON was exported; raw VirtualBox
  logs remained private. The preceding attempt, run 34051787324, stopped because
  the probe expected the verbose guest-type listing while requesting compact
  output. The successful run uses Oracle's documented `list --long` option.
- This confirms actual capacity, Hypervisor access and default Mac VM device
  initialization on the free hosted Intel runner. It does **not** establish a
  macOS guest OS boot, installation, login or reboot recovery. The runner exposes
  `Macmini6,2`; the guest installer and its compatibility remain separate gates.
- [Apple catalog run 34052076071](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34052076071)
  passed on source `81c0b3e9d97d7f7defd31f1a017caeed8fe0202a` and listed Catalina
  10.15.7 among the available full installers. That query downloaded no installer
  and made no host OS change. Availability is not proof that installation succeeds.

## Alpha 8 Windows guest reboot and login — September 6

- [Windows guest reboot run 34048951171](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34048951171)
  passed on harness source `0ebebac0cb0cd3d96d395d509607d7514403b7a3` using a
  Windows 11 Enterprise Evaluation guest, version `10.0.26200`, Python 3.13.7,
  and a regular non-administrator account with a real interactive login.
- It installed the published alpha 8 wheel, SHA-256
  `128d695dfffd1ccf92e6cc436ae0d5aee2a1a2112038d197b67b47e298063b6d`.
  The wheel came unchanged from [release-source CI 34048597096](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34048597096)
  at `4e4c36d3c4096ae9069a0fed9b2a58a5cb7e5ffe`: Windows **576 passed/9 skipped**,
  Ubuntu and macOS **577 passed/8 skipped** each, and the Docker execution gate
  **5 passed/26 deselected**. All three fresh installed-wheel checks passed.
- Before reboot, a cold-cache runtime preparation and an actual bundled GLSim
  escrow run passed. The outside Linux observer retained that baseline before
  authorizing one orderly Windows guest reboot. The Windows boot time changed
  from `17:47:02.500 UTC` to `17:51:54.500 UTC` on September 6; the operating-system
  version and regular-user identity remained the same.
- After automatic login, the existing managed Scheduled Task started at
  `17:52:00 UTC`. The observer did not install, start or restart Lab after reboot.
  Automatic readiness, unchanged installation and credentials, unchanged frozen
  report and preserved history all passed. Baseline run
  `573d1996ef4c41dd9accdf836a44fdbb` and new run
  `833a0acaad1947908fa55c4355516f51` both passed decision, behavior, outcome and
  completion grades with actual GLSim contract-execution evidence.
- The outer Linux boot, running QEMU process and persistent guest disk remained
  unchanged across the guest reboot. Cleanup removed the private guest disk,
  evaluation image and seed. The trial used a standard public GitHub Linux
  runner; it did not reboot the development PC or require paid hosting.
- This closes orderly **Windows guest OS reboot followed by a regular-user
  automatic login** for the native Lab service. It does not establish Windows
  startup before login, a separate desktop logout/login cycle, abrupt power
  loss, macOS reboot, automatic Docker/Studio startup or live-model behavior.

## Fresh Windows guest runtime download finding — September 6

- The [Windows guest trial on source `22f3457`](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34047014822)
  installed the published alpha 7 wheel, completed Windows setup and reached a
  regular-user interactive session. Its first runtime preparation failed with
  `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` under
  Python 3.13.7. The trial was inconclusive and did not authorize a reboot.
- Alpha 8 adds Certifi's public roots to the default SSL context for the pinned
  GenVM download. System roots, hostname/certificate checks and the artifact
  hash remain required. The passing alpha 8 trial above verified the corrected
  download on a fresh Windows guest and completed the reboot/login gate.
- The failed trial removed its private guest disk, evaluation image and seed.
  Only bounded diagnostic evidence and the failed setup screenshot were retained.

## Alpha 7 controlled Studio restart and onboarding kit — September 6

- The installed alpha 7 wheel passed `studio verify-recovery` against the existing
  owned Studio stack on the Windows Docker Desktop Linux engine. The final
  implementation completed in **184.750 seconds**; a preceding candidate also
  passed in 197.704 seconds. The final live-tested wheel SHA-256 was
  `8e8635f58d9008bf9be3b786204fc0e0ba0496893e8a6809d034a662ed9bff48`.
  Later documentation changes produce a different release artifact hash.
- Both checks used real local GenVM execution with deterministic model fixtures,
  without a paid model or public-chain transaction. The verifier finalized a
  deployment and approval, confirmed no pending transactions, stopped and
  recreated all seven owned containers, preserved both named-volume identities,
  and checked unchanged finalized contract state and receipts. A new denial on
  the same contract then finalized and changed its stored state.
- Recovery holds the Lab lifetime lock and Studio lifecycle lock, uses one
  bounded deadline with a cleanup reserve, and refuses missing or replaced
  original volumes before Compose can create empty replacement storage. Focused
  tests cover ownership, active writers, timeouts, changed storage/results,
  malformed verdicts, cleanup failure, and CLI evidence/exit behavior. The final
  CLI/Studio/recovery selection passed **97 tests**. Installation-verifier tests
  passed **6 tests**, including replacement of the source wheel during setup;
  the verifier now hashes and installs the same private artifact copy.
- Windows CI exposed floating-point rounding that could pass
  `510.00000000000006` seconds to a helper with a 510-second reserved-work
  budget. The call now clamps to that exact cap. A deterministic coarse-clock
  regression reproduces the condition; all **26 recovery tests** passed after
  the correction. This did not invalidate the observed container/state checks.
- A separate macOS package test exposed a second signal sent to an already
  terminated process group during timeout cleanup. Cleanup now records a
  successful termination (or absent group), avoiding a second signal after the
  leader is reaped. Initial permission denials remain errors. Focused tests
  cover both paths and actual timed-out descendants retaining output pipes.
- An installed alpha 4 to alpha 7 upgrade/rollback rehearsal preserved both
  frozen fixture reports, accepted a new run, restored the old-version-readable
  backup, rotated the restored administrator credential and revoked both old
  agent credentials. This rehearsal is separate from Studio storage recovery.
- The setup kit now includes [EXTERNAL_ONBOARDING.md](EXTERNAL_ONBOARDING.md),
  with a checklist and report template for two independent human developer
  trials. Those trials remain open. This restart check does not establish
  recovery from lost volumes, interrupted consensus, Docker/OS restart,
  Windows/macOS login, or live-model behavior.

## Alpha 6 native macOS user-service verification — September 6

- [Native macOS CI run 34028354236](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34028354236)
  passed at source commit `5d1c2cf70bb13c63dd8337acc095ff82f51b441b` on
  macOS 15.7.9 ARM64, Python 3.12.10, in the runner's actual GUI login session.
  The fresh installed alpha 6 wheel had SHA-256
  `75770533917e107c43edbf52e7ff0ce62c6ceefe1fe252e192117eda7b523c88`.
  Later documentation changes produce a different release artifact hash.
- Actual LaunchAgent installation, start, stop, restart and uninstall passed.
  Two authenticated HTTP runs executed the bundled GLSim contract and passed all
  four grades. The first frozen report, run history and administrator credential
  survived restart. Uninstall removed the job and plist and closed the port while
  preserving the database, credential and log. Guarded cleanup removed only the
  verifier's installation. The installed probe took **122.946 seconds**.
- The native test found two defects: launchctl's printed argument indentation was
  removed together with meaningful trailing spaces, and a stop could return before
  macOS finished unloading the job. Alpha 6 preserves exact arguments and waits
  for unloading with a fixed deadline. Changed arguments and duplicate definition
  fields still fail ownership validation; focused regression tests cover both fixes.
- This uses a free standard public-repository macOS runner. It establishes the
  real service lifecycle in an existing GUI session, not logout/login, a macOS
  reboot, automatic Studio startup, or independent human onboarding.

## Alpha 6 real Ubuntu guest OS reboot — September 6

- [Linux guest reboot CI run 34028862719](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34028862719)
  passed at source commit `57b93b7da17f318f6535511a48b5fb822b153709`.
  The installed alpha 6 wheel matched the macOS candidate hash above:
  `75770533917e107c43edbf52e7ff0ce62c6ceefe1fe252e192117eda7b523c88`.
  The guest used Canonical's Ubuntu 24.04 image dated 20260826; its signature
  and pinned SHA-256 were verified before boot. QEMU used actual KVM acceleration
  as the regular CI account with membership in the KVM group.
- A fresh Lab account installed the wheel, passed the actual GLSim doctor,
  registered its user service and completed a bundled GLSim HTTP run. The guest
  administrator explicitly enabled lingering. After an orderly guest OS reboot,
  its boot identity changed and the Lab became ready automatically with no Lab
  login session or manual start. The original credential remained accepted,
  installation identity and frozen report were unchanged, history survived,
  and a second actual GLSim run passed all four grades.
- The same guest disk and QEMU process were retained while the outer runner's
  boot identity stayed unchanged. Owned guest cleanup passed. The complete
  verifier took **127.964 seconds**. This establishes a real Linux guest kernel
  reboot with the documented server policy, not a provider-host reboot, abrupt
  power loss, Windows/macOS reboot, Studio recovery or a human installation trial.

## Alpha 5 native Linux user-service verification — September 6

- [Native Linux CI run 34025370784](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34025370784)
  passed at source commit `9c7bbe9be49bd6b1afb04072078cc9a8841a8c9c` on
  Ubuntu 24.04 with host Python 3.12.3. A disposable regular user installed the
  alpha 5 wheel in a fresh persistent virtual environment outside the checkout.
  This candidate's wheel SHA-256 was
  `dfa80d4c141b20cbd0b35cff14cbf92f8abd25738bf365fad914dba67a8dea67`;
  later documentation changes produce a new artifact hash recorded at release.
- Actual systemd registration, enable, start, stop, restart and uninstall passed.
  Two authenticated HTTP runs using the scripted safe reference agent executed
  the real bundled GLSim contract and passed all four grades. The first frozen
  report was unchanged after restart, both runs remained in history, and the
  original administrator credential still worked. Uninstall removed the unit
  and closed the port while preserving the database, credential and log.
- The data path contained spaces, quotes, `%`, `$`, and a trailing backslash and
  space. This real test caught alpha 4's invalid quoted `WorkingDirectory`.
  Alpha 5 starts in the user's home and enters the exact data directory in Python
  after verifying startup identity. The successful installed probe took
  **80.776 seconds**; the whole job took **3 minutes 4 seconds**. Its disposable
  account and services were removed successfully.
- The new workflow uses a standard public-repository Ubuntu runner, with no VPS,
  payment card, uploaded service artifacts or paid model provider. The CI host
  prepares a linger-enabled user manager; the package installer does not grant
  itself host privileges. This establishes a process lifecycle, not a host reboot,
  logout, macOS GUI login, Studio startup or independent human onboarding.
- A separate Windows CI failure exposed floating-point timeout rounding in the
  Studio conformance helper. Alpha 5 clamps each operation to its configured
  maximum while retaining the shared deadline. Deterministic coarse-clock and
  expired-deadline cases cover the correction.

## Alpha 4 installation, startup and recovery — September 6

- [Cross-platform CI run 34019705696](https://github.com/Leokings/genlayer-agent-lab/actions/runs/34019705696)
  passed at source commit `6a7f5739316afd713f703c7bb8faff9c37997a04`:
  **Ubuntu 439 passed/8 skipped, macOS 439/8, Windows 438/9**.
  Node 24.13.0 was installed and the TypeScript HTTP integration test passed on
  every OS. Opt-in local Studio/container tests and platform-only checks account
  for the skips; Docker has its own separate live job.
- Each OS built a wheel, installed it in a fresh virtual environment outside
  the checkout with a fresh cache/home, and passed actual bundled GLSim doctor,
  all **18 fixture scenarios**, Python HTTP, actual MCP stdio, dashboard asset
  serving, and export of the **25-file setup kit**. Full resolved dependency
  versions and wheel/resource hashes are in the CI artifacts. These checks do
  not depend on an editable source installation.
- The separate Ubuntu Docker job passed **5 live tests** and **18/18 custom
  binding scenarios**. The local alpha 4 worker also built and executed its
  readiness contract, image
  `sha256:32569c33bdce33e194618bfadf015227c4e3ef2d2af2796900d9fe5f115e6176`.
- A local clean Linux container install passed in **422.065 seconds** without
  host source, venv, cache, credentials or Docker socket. This is additional
  Linux userspace evidence on the Windows Docker host, separate from native CI.
- Windows user startup was exercised through actual Task Scheduler: install,
  start, completed test, stop, restart with saved history, and uninstall. The
  installation token and SQLite database survived. **27 focused service tests**
  cover ownership and platform definitions. Native Linux/macOS service-manager
  lifecycle tests remain unperformed; their definitions/manager checks have
  unit coverage and documented user-session requirements.
- The demo upgraded from alpha 3 to an installed alpha 4 wheel and current-user
  startup. All **55 historical records were unchanged** against the pre-upgrade
  backup. Its new actual GLSim run `35895329f9154a719d16f44788107039` passed all
  four grades. The optional Studio stack remained ready with its original image
  and persisted data. The SQLite backup excludes Studio volumes.
- Actual installed alpha 3 to alpha 4 upgrade/rollback rehearsal preserved two
  completed reports, accepted a new run after upgrade, and let the retained
  alpha 3 executable read a fresh restore of the pre-upgrade archive. Restored
  administrator credentials were new and two old run credentials were revoked.
  Recovery tests additionally cover actual schema-1 to schema-2 migration,
  committed WAL transactions, active-owner refusal, corrupt archives and
  no-overwrite restoration.
- An independent agent-assisted Windows onboarding rehearsal used only the
  supplied candidate bundle and exported kit. Its first real doctor took
  **291.19 seconds** and passed without a workaround or host SDK cache. Safe run
  `2567ff937e454d4a908401ccb07e39c0` passed, unsafe provisional run
  `656b74560c044d489caaf5af88f4a28d` failed as intended, and a separate Python
  agent with only its run credential passed run
  `147b79057133455c9dcbd6cf122b7c37`. JSON/HTML reports exported. Dashboard login
  rendered; authenticated browser use was not part of this rehearsal. This
  counts as an agent-assisted installation, **not a human external trial**.
- An earlier cold Windows verification hit the former **300-second setup
  limit**, and the first macOS CI attempt started execution tests before
  preparing the cache. Setup now has a configurable **900-second doctor budget**
  (maximum 1800), a visible preparation message, and a CI warm-up step. Normal
  evaluations retain their **60-second** deadline. The passing native CI above
  exercises this correction. The Windows relay's rejected-route handling also
  now consumes valid bounded request bodies before closing, avoiding a TCP reset
  observed during the full local suite.

Local source verification passed **432 tests** before the preparation-budget
change; the final focused runtime/CLI/service checks passed **75 tests**. These
counts overlap CI and are not additive. Ruff and the setup skill validator
passed. Historical alpha 3 Studio execution/appeal evidence below remains
separate from the alpha 4 fixture and installation gates.

## Alpha 3 local Studio verification — September 6

- The complete non-Studio-live suite passed **379 tests**. Later relay/stack
  checks passed **48 tests**, and the final idle-vote correction passed **39
  adapter/conformance tests**. These focused counts overlap the full suite.
  Ruff passed. The only suite warning was upstream Starlette/AnyIO deprecation.
- Built the pinned Studio v0.121.6 source at commit
  `366f085a479bb9e6028ce326c2c13f798a9752c7`, including its upstream license.
  Actual image: `sha256:0fdb3c62ee910b1f4ef98080cc2a766f5a22a0ebccc973107beaedca280d02a6`.
  Startup, actual runtime inspection and SDK doctor passed with chain 61999
  and a 30-second local finality window.
- **Three real Studio tests passed in 254.51 seconds**, using GenVM v0.2.16
  and fixture-only validators. Separate fresh contracts returned approve and
  deny. Both recorded successful execution, ACCEPTED and FINALIZED checkpoints.
  Approve execution: `0x3a4c4ec756c3197e7ce0815ab639641de1b0b7fd83af4c2909fa628db8cd2d90`.
  Deny execution: `0x89e4cd6f0c2597e3372666c4559c6c7b20a9df24033d80c13f976a7c16504707`.
- An actual appeal completed a new `Validator Appeal Failed` round and upheld
  the approved result, followed by successful finalization. This is a passing
  appeal-lifecycle test, not a claim that the challenge changed the decision.
  Execution: `0xd1b2cfeacac27c53ca733b7e68f6e37ca8ec80e3978c74fd0359925b02d02567`.
  Global validators were not changed during the tests. Evidence is in the
  installation's `.lab/studio-live-*.json` files.
- The real Studio worker failed an external TCP connection probe as intended;
  actual network membership, immutable images, loopback publication, resource
  limits and owned volumes passed inspection. A fixed-target relay solves this
  Docker engine's inability to publish a port on an internal network.
- Alpha 3's custom GLSim worker rebuilt and passed its real contract probe:
  `sha256:a643dd7ebaf2f5e84c190f1f242c6b3c547db7feb4d4a3599b8f9b592ca2ba3d`.
  Its staged CLI progress and structured diagnostics were exercised.
- Dashboard launch, independent grades, comparison, HTML download and mobile
  layout passed without page errors. The new runtime selector is present.
- Independent Python, TypeScript and MCP stdio clients each passed the custom
  delivery contract through the running Lab HTTP service with `backend=studio`.
  All four independent grades passed. Run IDs, in that order:
  `92d6227bb6634b4d862ceaa64dbfa65e`, `717da87a158043178d81cf24ec91a486`,
  `f763a213189f49a9ae96b4169d4ee136`. The dashboard also displayed the actual
  Studio report and its four passing grades without page errors.
- The alpha 3 wheel installed into the separate verification environment,
  reported version 0.1.0a3, and verified the running Studio configuration and RPC.
- `studio down` followed by `studio up` passed. Startup took 32.2 seconds,
  reused the same image/cache and preserved the finalized appeal transaction.
  The Lab's run history and administrator token were retained.

Model responses and protocol balances remain fixtures/simulations. Agent
scenario events remain scripted; only the separate Studio checkpoints above
establish actual local consensus observations. Modern bond accounting and
public-network finality are unsupported by this stable integration.

## Alpha 2 live Docker verification

Docker Desktop's Linux engine is now working (Engine 29.1.5, x86_64). The startup failure was traced to stale AF_UNIX socket objects that survived the user's factory reset. Quarantining the two affected runtime directories allowed Docker to recreate its endpoints. See [troubleshooting](TROUBLESHOOTING.md). The lab's database and historical reports were preserved.

- Final complete suite: **204 tests passed, zero skipped**, with one upstream Starlette/AnyIO deprecation warning. Ruff passed.
- The pinned worker image built and passed its real bundled-contract readiness probe. Image ID: `sha256:331b8c75dab3a41b931c4660db39e8f42f7a89ccca6736cb73c2ecc4473f900d`.
- Live `DeliveryAssessment.assess_delivery` tests passed for its structured JSON result, immutable source/binding hashes, payment effect and report persistence. A deliberate deny response against an independently expected approval correctly failed the decision grade while the agent safely withheld payment.
- All **18/18 scenarios** passed with the imported delivery binding and the safe reference agent through the real HTTP service and Docker worker.
- Independent Python, TypeScript and actual MCP stdio clients each passed `escrow-normal` using the custom Docker contract. Run IDs: `f83274e680564101a169954b957c47c0`, `b9cb12e0a7ba4590bc036e4e487c9238`, `c4f31f40aaf44d179ecd4e8dc7edb7d1`. The repeatable verification script is `scripts/verify-custom-clients.py`; it retains concise local evidence without credentials.
- Three actual container-boundary tests passed: approve/deny/approve in fresh containers; successful denial after blocked network access; and a runaway method that wrote an entry witness before its 10-second timeout. Each test independently verified its exact container IDs and ownership labels were absent afterward.
- The custom-contract dashboard path now completes successfully. Scenario launch, four grades, findings, comparison, HTML download and 390-pixel mobile layout passed without page errors.
- The rebuilt wheel was installed in the separate verification environment and passed a custom delivery-contract run (`63a71dc6c455403eaab0ea8d61607c76`) with all four grades passing.
- Two defects were fixed before building: GLSim 0.29.2's empty proxy-class method schema (validate the bound method instead), and copied files being unreadable by a nonroot worker under a restrictive Linux builder umask (explicit image ownership).

These alpha 2 checks established the Linux worker running under Docker Desktop
on Windows. Studio was verified later in alpha 3 as recorded above. Separate
Linux/macOS host installation CI remains unverified.

## Initial alpha 2 checks before Docker repair

- Final Python suite: **187 passed, 3 skipped** (the live-container cases), with one upstream Starlette/AnyIO deprecation warning.
- Custom bindings: confined paths (including Windows paths and symlinks), byte/structure limits, alias rejection, literal-null arguments, exact result mapping, canonical snapshots and hash validation passed.
- Custom engine runs through an injected test evaluator passed API/CLI/Python/TypeScript/MCP integration checks. These establish routing and grading, not actual Docker execution.
- Re-import preserves queued contract snapshots. Independent decision/behavior/outcome/completion grades remain in force. Missing Docker fails closed with an inconclusive result; no native fallback exists.
- Schema 1 migration preserved all 25 existing demo runs and produced a pre-migration SQLite backup. A failed startup now releases its data-directory lock, allowing a repaired installation to reopen.
- Cancellation signals preparing container jobs. Controlled Docker-process tests verified creation flags, image-ID dispatch, ownership checks, cleanup, output limits and cancellation. Actual child processes verified timeout/output limits and termination of a spawned grandchild that inherited pipes.
- `DeliveryAssessment` sample: GenVM lint and contract validation passed (one public write and one view).
- Actual dashboard: custom binding selection sent the correct backend and binding ID. Missing Docker produced an inconclusive report containing the snapshot hashes. Bundled unsafe-test launch, four grades, comparison, HTML download and mobile layout passed with no page errors. The custom report's collapsed provenance panel required the browser test to read `textContent` rather than rendered `innerText`.
- Packaged alpha 2 installed in a separate environment, imported the delivery binding and completed an actual bundled GLSim escrow run (`720c51b6997b409d90723cc9d660c14f`) with all four grades passing.
- Three actual-container tests exist for approval/denial/clean state, blocked network access and runaway code. They are explicitly skipped here because Docker's API times out. Docker Desktop processes were present and WSL Linux startup succeeded; a hardware-virtualization failure was not established. No Docker reset, prune or unrelated container removal was performed.
- CI now includes a Linux Docker image build, readiness probe, actual-container tests and the full bound scenario suite. That CI job has been authored, not executed here.

The Docker gate was unresolved at that point; the later live checks above supersede that limitation.

## Alpha 1 executed checks

- Full Python suite: **135 tests passed**. One upstream Starlette/AnyIO deprecation warning; no test failures.
- After freezing final report snapshots: **91 engine/CLI tests passed**.
- Ruff: all checks passed.
- Bundled intelligent contract: `genvm-lint` passed.
- Actual runtime: approve and deny execution, three validator callbacks/four fixture model calls, clean repeated state, malformed model response rejection, worker failure handling and Unicode-path cleanup.
- Actual local service: **18/18 bundled scenarios passed** using the safe scripted reference and GLSim. Unsafe and refusing controls fail their intended checks; they are not expected to pass.
- Independent integrations: Python, TypeScript and actual MCP stdio each completed `escrow-normal` against the public HTTP service and actual GLSim. Additional integration coverage includes provisional approval, revised denial and lost acknowledgement.
- Browser: authenticated dashboard, 18 scenario options, launching an unsafe test, four grades, findings, report comparison and HTML download passed. Desktop and 390-pixel mobile layouts inspected; no page exceptions or horizontal page overflow observed. The history table scrolls horizontally on narrow displays.
- Setup skill passed its structure validator.
- Source distribution and wheel built. A separate Python environment installed the wheel, passed `doctor`, and completed an actual GLSim escrow run (`3e484895b041441eba6aa03dea02ee19`).
- A custom YAML case for an obsolete policy imported and passed its safe-reference fixture test.

## Reproduced and fixed defects

- Upstream Windows open-file deletion: worker-local deferred cleanup, no installed package edits.
- Missing NumPy requirement in the simulator's installed extra: explicit pinned dependency.
- HTTP validation bypassing behavior grading: authenticated malformed actions now produce sanitized rejection events.
- Lost acknowledgements passing without reconciliation: completion requires resolving the uncertain result with the same action key.
- Historical report drift: originating version and completed report snapshot are persisted. Legacy traces infer recorded acknowledgement reconciliation.
- Newer database schema silently downgraded: unsupported schema versions are rejected before schema mutations.

## Not established by these checks

Native Windows/Linux/macOS package CI and service-manager lifecycle checks have
passed as recorded above. A real Ubuntu guest reboot passed with explicitly
configured lingering. Alpha 8 passed Windows guest reboot followed by a real
regular-user automatic login. macOS reboot, separate desktop logout/login and two human
external developer installation trials remain open. The examples are scripted controls,
not tests of live LLM reasoning. No real funds, live provider calls,
public-network finality or modern appeal bond settlement were tested.

The SDK archive was checked against the pinned official release hash; it is cached outside this repository. Native execution checks run bundled trusted code. The live container checks exercise specific isolation and resource-limit behavior; they are not a general security certification or independent attestation.
