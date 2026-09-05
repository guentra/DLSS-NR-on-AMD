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

## How the wrapper works

Steam runs `.dlssnr-linux/launch.sh` before its normal `%command%`. The script checks the bridge and HIP runtime hashes, exposes the required paths to Steam's container, sets per-game GPU filters and DLL overrides, then forwards the original command and arguments unchanged.

Inside Proton, the mod's `version.dll` hooks the game's FSR path. A small `amdhip64_7.dll` trampoline forwards Windows HIP calls to a Linux bridge loaded through `LD_PRELOAD`; that bridge loads ROCm only when HIP is first needed. The patched vkd3d-proton DLLs handle the D3D12/Vulkan side of buffer sharing with HIP. Neural rendering still uses the upstream mod's kernels—not a replacement upscaler or a Windows GPU driver.

These launch settings are scoped to the game; the wrapper does not modify shared Proton or system ROCm files.

## Troubleshooting

### Game executable not detected or multiple executables found

If automatic detection cannot find the game's executable or reports multiple candidates, select the actual game executable explicitly with `--exe`:

```sh
./install.sh install --exe '/path/to/game/Game.exe'
```

Use the rendering executable, not a launcher, crash reporter or browser helper. Keep paths containing spaces quoted. `--exe` selects the file; it does not bypass compatibility checks.

### Resident Evil Requiem

A reported mod v0.2.11 run loaded HIP and the NR engine, but logged `unsupported colour format 67` / `setup failed; idle`, access violations and kernel GPU MES/TLB failures. This is NOT a working NR configuration. The exact crash cause is not established and no rendering fix is validated. Do not keep retrying the same configuration after GPU faults. Installation integrity does not establish game compatibility.

## ROCm setup

**No separate ROCm installation step is needed before running `./install.sh`.** The wizard checks for a compatible HIP7 runtime:

- **Already installed:** it uses that runtime.
- **Not available:** it asks permission to download and install one in your user account. Allow **3 GiB of free disk space**. No `sudo` or changes to system Python are needed.

Your AMD kernel driver must already work, and your account must have access to `/dev/kfd` and the GPU render devices. The installer cannot fix driver or device-permission problems.

<details>
<summary>Optional: check or install ROCm separately</summary>

These commands are for manual setup or troubleshooting, not extra required installation steps:

```sh
./install.sh runtime                # Check the existing runtime
./install.sh runtime --install-rocm # Allow a download if no compatible runtime is found
./install.sh runtime --self-test    # Optional GPU memory-copy test
```

</details>

<details>
<summary>Advanced: runtime version and storage locations</summary>

- The download is AMD's `rocm-sdk-core 7.14.0a20260612` wheel from `rocm.nightlies.amd.com`, verified against a pinned SHA256. It is a nightly build; compatibility with every distribution is not guaranteed.
- Installer-managed runtime and support files default to `~/.local/share/dlssnr-linux/` (or `$XDG_DATA_HOME/dlssnr-linux/`). **Keep these files after installation.** `--data-dir` changes where managed ROCm and converted weights are stored; it does not move the native bridge under `$XDG_DATA_HOME/dlssnr-linux/native/`.
- If you customize `XDG_DATA_HOME`, use an absolute, user-owned path without whitespace or colons. Game paths may contain spaces, but mount paths cannot contain colons or newlines. Reinstall affected games if you move their runtime or support files.

With default paths, the extracted installer folder is not needed to run an installed game. On another PC, rerun the installer to generate the correct local paths instead of copying a game's `launch.sh`.

</details>

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
