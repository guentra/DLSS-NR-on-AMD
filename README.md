# DLSS-NR on AMD — portable Linux installer

Linux/Proton wrapper and per-game installer for [danielblnc's DLSS-NR on AMD](https://github.com/danielblnc/DLSS-NR-on-AMD). This fork does not reimplement the neural renderer. See the [upstream README](https://github.com/danielblnc/DLSS-NR-on-AMD#readme) for the original Windows project.

**Download:** open this fork's [Releases](../../releases) and get `dlssnr-linux-portable.tar.gz` plus its `.sha256` file. Use that installation archive, not GitHub's automatically generated "Source code" archive, which does not contain the runtime components.

Experimental per-game installation, reusable on your own PCs. Requires **Linux x86_64, Python 3.10+, Bash, glibc 2.34+, amdgpu/KFD device access, HIP7 and a compatible Proton**. A compiler is not required for normal installation. HIP's own ELF dependencies are checked separately and may need a newer system.

Bundled mod: **v0.2.11**. Kernel targets: **gfx1100, gfx1101, gfx1102, gfx1201**. Requires a Windows x64 DX12 game with FSR and a usable `version.dll` import. Static detection is not a compatibility guarantee. Detected anti-cheat blocks installation; this is not an anti-cheat bypass. In-game rendering, performance and absence of ghosting are not guaranteed. The existing V3 native integration is unchanged.

## Start

Extract the archive into a user-owned directory, then run:

```sh
./install.sh
```

The wizard selects your game, GPU and Proton and asks before installation or downloads. It never launches the game. Close the game first. No arguments without an interactive terminal displays usage rather than guessing.

```sh
./install.sh list-games
./install.sh list-protons
./install.sh --help
```

Provide your own existing `DLSSNRW1` file using `--weights`, or a legally obtained `nvngx_dlssnr.dll` using `--nvidia-dll`. Neither NVIDIA DLLs nor derived weights are bundled. The accepted NVIDIA DLL is version 310.8.0.0 with SHA256:

```text
e16bcf15e16e13f527491cdf7845b2fe6521a738d8f7c9c721866a8496e1fc8e
```

Conversion runs the original setup under Proton/Wine in a separate temporary prefix, not in your game. Check that your license permits your intended use.

## Check without installing

Replace the example paths with your own:

```sh
./install.sh doctor --exe '/games/My Game/Game.exe' --proton '/tools/Proton' --gpu 0
./install.sh install --exe '/games/My Game/Game.exe' \
  --proton '/tools/Proton' --confirm-proton --gpu 0 \
  --weights '/data/dlssnr_on_amd_weights.bin' --accept-risk --dry-run
```

`doctor` and `install --dry-run` do not write, download or convert. A complete dry run requires existing weights and a usable runtime. HIP is loaded in an isolated subprocess for symbol checks and device enumeration; no GPU copy self-test is performed unless explicitly requested through `runtime --self-test`.

## Install

```sh
./install.sh install --exe '/games/My Game/Game.exe' \
  --proton '/tools/Proton' --confirm-proton --gpu 0 \
  --weights '/data/dlssnr_on_amd_weights.bin' --accept-risk
```

- `--appid ID` can select a Steam game. Add `--exe` if its executable is ambiguous.
- `--steam-root` selects another Steam installation.
- `--gpu` takes a HIP index or exact name. Multiple compatible GPUs require a selection.
- `--hip-library /runtime/lib/libamdhip64.so.7` selects an existing library; no silent fallback.
- `--replace-existing` explicitly backs up and replaces conflicting DLLs. The wizard also asks before this.
- `--json` produces structured output and disables interactive prompts.

**Select the same Proton manually in Steam → Properties → Compatibility.** `--confirm-proton` confirms that you did so; it does not change Steam. Copy the printed launch options, preserving unrelated existing options:

```text
'/games/My Game/.dlssnr-linux/launch.sh' %command%
```

Keep the quoting. The installer does not modify Steam configuration, shared Proton, saves, display settings or other games. It installs local DLLs, the NR INI and a per-game `.dlssnr-linux/` directory. NR is explicitly configured with **Enabled=1, Inline=1, Interop=1**. Enable FSR in the game as the mod's injection hook, not as a substitute upscaler. Press **End** for the mod menu.

## Troubleshooting

### Game executable not detected or multiple executables found

If automatic detection cannot find the game's executable or reports multiple candidates, select the actual game executable explicitly with `--exe`:

```sh
./install.sh install --exe '/path/to/game/Game.exe'
```

Use the rendering executable, not a launcher, crash reporter or browser helper. Keep paths containing spaces quoted. `--exe` selects the file; it does not bypass compatibility checks.

### Stellar Blade

**Executable detection was fixed in installer v0.1.1.** The installer recognizes the `SB.exe` Unreal bootstrap, resolves its embedded game path and ignores crash-report/CEF helpers. This was an installer detection issue, not a confirmed rendering incompatibility.

If detection still fails, select the game executable directly:

```sh
./install.sh install --exe '/path/to/StellarBlade/SB/Binaries/Win64/SB-Win64-Shipping.exe'
```

If `SB-Win64-Shipping.exe` is missing, use Steam > Properties > Installed Files > Verify integrity of game files. Do not install the mod beside the root `SB.exe` launcher or bypass the `version.dll` check. Successful executable detection does not establish in-game rendering compatibility.

### Resident Evil Requiem

A reported mod v0.2.11 run loaded HIP and the NR engine, but logged `unsupported colour format 67` / `setup failed; idle`, access violations and kernel GPU MES/TLB failures. This is NOT a working NR configuration. The exact crash cause is not established and no rendering fix is validated. Do not keep retrying the same configuration after GPU faults. Installation integrity does not establish game compatibility.

## ROCm and persistent caches

An existing HIP7 runtime is preferred. If unavailable, authorize the pinned official AMD wheel:

```sh
./install.sh runtime --install-rocm
# Optional actual GPU memory-copy test:
./install.sh runtime --self-test
```

The wheel is `rocm-sdk-core 7.14.0a20260612` from `rocm.nightlies.amd.com`, checked against a pinned SHA256 and installed in an isolated user environment. Allow at least **3 GiB free disk space**. It is a nightly, not a guarantee across distributions. Nothing installs a kernel driver, uses sudo, changes system Python or aliases an incompatible HIP major version. Missing `/dev/kfd` or render permissions must be fixed at the OS level.

`--data-dir` moves managed ROCm and conversion caches, **not** the shared native bridge cache. That lives in `$XDG_DATA_HOME/dlssnr-linux/native/`, defaulting to `~/.local/share/dlssnr-linux/native/`. XDG_DATA_HOME must be absolute, private, and contain **no whitespace or colons**, because LD_PRELOAD cannot quote these separators. Ordinary game paths with spaces are supported. Mount paths cannot contain colons or newlines. Do not move active caches after installing; reinstall affected games if paths change.

The extracted installer is not a runtime dependency after installation. On another PC, extract the same archive and rerun the installer: do not copy a generated launch wrapper containing another machine's paths.

## Status and uninstall

```sh
./install.sh status --exe '/games/My Game/Game.exe'
./install.sh uninstall --exe '/games/My Game/Game.exe' --yes
```

Backups and a durable transaction journal are kept per game. Uninstall restores verified originals, including the pre-install NR INI, and refuses to overwrite DLLs changed afterward. Preserve your modifications and resolve conflicts; do not delete the journal to force removal. Interrupted operations can be recovered using uninstall. Shared HIP and bridge caches remain for other games. Remove the wrapper manually from Steam launch options after uninstall.

Logs: `.dlssnr-linux/logs/hip.log`, `.dlssnr-linux/logs/vkd3d.log`, the mod's game-local log, and legacy PE diagnostics under `/tmp`.

## Source tree and packaging

This Git tree contains only the installer, launch-wrapper generator, packaging code and documentation. It does not contain tests, the native renderer, HIP bridge implementation, third-party source trees, binaries, NVIDIA DLLs or weights. Prebuilt installation components are supplied separately in the release archive. Native component source/rebuild material and license notices are retained in that archive rather than committed here. Tests remain local and are not included in the release archive.

To package a checkout, supply an existing extracted release containing the matching components:

```sh
python3 -B build_release.py --components-root /path/to/extracted/dlssnr-linux-portable
(cd dist && sha256sum -c dlssnr-linux-portable.tar.gz.sha256)
```

The deterministic builder checks the pinned binary hashes and uses an explicit file allowlist. It excludes private logs, proof environments, Python caches, NVIDIA DLLs and weights. A checksum is not a signature or a license grant. Third-party rights remain with their respective owners; the upstream mod has no identified redistribution license. See `THIRD-PARTY.md`, `PROVENANCE.json` and the release archive's `sources/README.md` for notices and limitations.

## Legal

This project is not affiliated with or endorsed by NVIDIA or AMD. DLSS is a trademark of NVIDIA Corporation. The software here contains no NVIDIA code or data; it requires the user's own legitimately obtained copy of the DLSS 5 DLL. Provided as-is, without warranty; use at your own risk, your computer may explode or worse
