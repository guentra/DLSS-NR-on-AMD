# DLSS-NR on AMD for Linux

A per-game installer and Wine/Proton launch wrapper for [danielblnc's DLSS-NR on AMD](https://github.com/danielblnc/DLSS-NR-on-AMD), using upstream mod v0.2.11. **Steam is optional.**

This is experimental. Compatibility and rendering quality vary by game; a successful installation does not guarantee correct in-game rendering.

## Install

1. Close the game. Download **`dlssnr-linux-portable.tar.gz`** from [Releases](https://github.com/guentra/DLSS-NR-on-AMD-Linux/releases), not GitHub's "Source code" download. Extract it **inside the game's directory**. Keep the archive's `dlssnr-linux-portable` subfolder to avoid overwriting unrelated game files.
2. Open a terminal in that subfolder and run:

   ```sh
   ./install.sh
   ```

3. The wizard detects the game in this directory, including Unreal's `Binaries/Win64` executable, and asks if there are multiple candidates. Choose your GPU and provide the **Wine/Proton runner directory already used by the game**, plus your weights or NVIDIA DLL. Enter `steam` at the runner prompt only if you want to list Steam's Proton installations. It checks ROCm and asks permission to install it if needed. **No Steam game search or separate ROCm command is required.**
4. Add the printed wrapper to your usual launch method:
   - **Lutris / another launcher with a command-prefix field:** use the printed **Command prefix**, without `%command%`. Keep the game's existing runner and Wine prefix.
   - **Steam:** select the same Proton in **Properties > Compatibility**, then paste the printed **Steam launch options** into **Properties > General > Launch Options**.
   - **Existing Wine shell command:** put the wrapper before the runner command; see the example below.
5. Launch the game normally, enable **FSR** in its graphics settings, and press **End** to open the mod menu. FSR is the mod's injection point; neural rendering uses the upstream mod's kernels.

The installer does not launch the game or edit launcher settings. It also works with the installer files directly beside the executable; the default is always relative to `install.sh`, not your terminal's working directory. To target a different location, use `--game-dir` or `--exe`.

### Outside Steam

No AppID, Steam library or Steam client is required. The game must already run through a compatible Wine/Proton runner. The installer checks the runner's `ntdll`, `win32u` and DXVK; ordinary or stripped Wine builds may be rejected. This is not a claim that every Lutris/Heroic/Bottles runner is compatible. Sandboxed launchers must be able to access the game, wrapper, native cache and ROCm runtime.

For an existing, working Wine command, preserve its prefix, working directory and arguments, and insert the generated wrapper before Wine:

```sh
WINEPREFIX='/path/to/existing-prefix' '/path/to/game/.dlssnr-linux/launch.sh' '/path/to/runner/bin/wine' '/path/to/game/Game.exe'
```

Use the actual paths printed by the installer; Unreal's wrapper is beside the selected shipping executable. For Proton/UMU-managed games, keep the launcher's normal command instead of replacing it with raw Wine. A **pre-launch script** that runs and exits is not a command prefix: the wrapper must execute the runner so its environment reaches the game. Copying DLLs alone is insufficient on Linux.

## Requirements

- Linux x86_64, Python 3.10+, Bash and glibc 2.34 or newer. No compiler is needed.
- A supported AMD GPU. The bundled kernels target `gfx1100`, `gfx1101`, `gfx1102` and `gfx1201`.
- A working AMD kernel driver and permission to access `/dev/kfd` and GPU render devices. The installer cannot install the driver or fix device permissions.
- An installed, compatible Wine/Proton runner. Supported directory layouts contain `files/bin/wine`, `dist/bin/wine` or `bin/wine`, plus the required x64 libraries. The wizard checks it; it does not download runners or create a game prefix.
- A Windows x64 DirectX 12 game with FSR and a usable `version.dll` loading path. The installer refuses detected anti-cheat; it does not bypass it.
- Your own legitimately obtained **`nvngx_dlssnr.dll` version 310.8.0.0**, or weights already converted to the `DLSSNRW1` format. Neither is included in the archive. Existing weights beside the executable are reused first. Otherwise, the installer automatically detects `nvngx_dlssnr.dll` beside the executable or at the game root (including Unreal games), verifies its hash and converts it without asking for its path. Explicit `--weights` / `--nvidia-dll` selections take priority. If nothing is found, the wizard asks for a path.

If ROCm needs to be downloaded, allow **3 GiB of free disk space**. Downloads require your consent and stay in your user account; do not run the installer with `sudo`.

<details>
<summary>Accepted NVIDIA DLL and weight conversion</summary>

The accepted `nvngx_dlssnr.dll` has this SHA256:

```text
e16bcf15e16e13f527491cdf7845b2fe6521a738d8f7c9c721866a8496e1fc8e
```

Conversion runs the original setup under Proton/Wine in a separate temporary prefix, not in the game directory. Check that your license permits your intended use. Supplying existing weights skips this conversion.

</details>

## Optional commands

These are shortcuts for inspection and troubleshooting. They are **not additional installation steps**.

| Command | Purpose |
| --- | --- |
| `./install.sh list-games` | List detected Steam games. |
| `./install.sh list-protons` | List Proton distributions and their static compatibility checks. |
| `./install.sh doctor` | Check a game's prerequisites without installing anything. |
| `./install.sh runtime` | Check the available HIP7 runtime. |
| `./install.sh runtime --install-rocm` | Allow a runtime download if no compatible one is found. |
| `./install.sh runtime --self-test` | Also run a small GPU memory-copy test. |
| `./install.sh install --help` | Show all installation arguments. |

`doctor` does not write, download or convert. It may load HIP in a separate process to check symbols and detect GPUs. The GPU memory-copy test runs only when you request `runtime --self-test`.

## Optional installation arguments

Use `./install.sh install` followed by whichever arguments you need. In a terminal, the wizard asks for the remaining choices and confirmations.

### Select the game and inputs

| Argument | Purpose |
| --- | --- |
| `--game-dir PATH` | Search this game directory rather than the directory containing the installer. |
| `--exe PATH` | Select the actual game executable, not a launcher or crash reporter. |
| `--appid ID` | Select a Steam game by its application ID. Add `--exe` if its executable is ambiguous. |
| `--steam-root PATH` | Search a different Steam installation. |
| `--runner PATH` | Select the installed Wine/Proton runner used by your launcher. `--proton` remains an alias. |
| `--gpu INDEX_OR_NAME` | Select a HIP device index or exact GPU name. |
| `--weights PATH` | Use an existing `DLSSNRW1` weights file. |
| `--nvidia-dll PATH` | Convert your NVIDIA DLL instead. Do not combine this with `--weights`. |

### Runtime and storage

| Argument | Purpose |
| --- | --- |
| `--hip-library PATH` | Use a specific HIP7 library or SDK directory. Invalid selections do not silently fall back. |
| `--install-rocm` | Authorize a user-local runtime download if needed. |
| `--data-dir PATH` | Choose an absolute directory for managed ROCm and converted weights. See storage details below. |

### Checks and confirmations

| Argument | Purpose |
| --- | --- |
| `--dry-run` | Simulate deployment without writing, downloading or converting. Requires an existing runtime and converted weights. |
| `--confirm-runner` | Confirm that your launcher/command uses the checked runner. `--confirm-proton` remains an alias; neither changes launcher settings. |
| `--accept-risk` | Accept the risks of experimental injection. |
| `--replace-existing` | Back up and replace conflicting DLLs already in the game directory. |
| `--allow-unconfirmed-loader` | Allow installation when static inspection cannot confirm how the mod will load. Activation must still be checked in the game log. |
| `--json` | Return JSON and disable interactive prompts. |

Quote paths containing spaces. For scripts or JSON output, supply the required choices and confirmations explicitly; the installer will not guess or consent on your behalf.

## Troubleshooting

### Game executable not found or multiple candidates

Point the installer at the actual game executable:

```sh
./install.sh install --exe '/path/to/game/Game.exe'
```

The installer recognizes Unreal bootstrap launchers and ignores known crash-report/CEF helpers and its own bundled assets. If detection still fails, use the rendering executable, often under `Binaries/Win64`, rather than the root launcher. If the file is missing, repair the installation in your launcher (Steam: **Properties > Installed Files > Verify integrity of game files**).

`--exe` selects a file; it does not bypass compatibility checks. You do not need a pre-existing `version.dll`: the installer supplies the mod's proxy. It follows imports through game dependencies, including Unreal's `Engine/` directories, rather than requiring the executable itself to import `version.dll`.

If static inspection cannot confirm a loading path, `doctor` reports a warning. The wizard asks before proceeding; non-interactive installation requires `--allow-unconfirmed-loader` in addition to the usual confirmations. This permits installation, not guaranteed activation: check `dlssnr_on_amd.log` afterward. Do not simply rename the proxy to `winmm.dll` or `dxgi.dll`; those libraries require different exports.

### ROCm or Proton check fails

- Missing `/dev/kfd` or inaccessible GPU devices: fix the AMD driver or device permissions at the OS level. Downloading another runtime will not fix this.
- No compatible HIP7 runtime: accept the wizard's download offer, or use `runtime --install-rocm` manually.
- Incompatible runner: supply a compatible distribution with `--runner`; for Steam, `list-protons` lists available candidates. Use the same runner in the launcher. Passing these checks does not guarantee game compatibility.

### Resident Evil Requiem

The current mod v0.2.11 configuration has reported `unsupported colour format 67`, failed NR setup, access violations and GPU MES/TLB errors. No working fix is validated. Do not keep retrying this configuration after GPU faults.

### Where to find logs

Look beside the game's actual executable:

- `dlssnr_on_amd.log`: upstream mod log.
- `.dlssnr-linux/logs/hip.log`: Linux HIP bridge log.
- `.dlssnr-linux/logs/vkd3d.log`: D3D12/Vulkan log.

Legacy PE diagnostics may also be written under `/tmp`.

## Status and uninstall

Close the game before uninstalling. Use the same game executable you installed for:

```sh
./install.sh status --exe '/path/to/game/Game.exe'
./install.sh uninstall --exe '/path/to/game/Game.exe'
```

Uninstall asks for confirmation; add `--yes` to confirm in advance. It restores backed-up originals, including the previous NR INI. **Remove the wrapper from your launcher's command prefix or Steam launch options afterward.** With the installer still in the game directory, `./install.sh status` and `./install.sh uninstall` also use local detection.

If files have changed since installation, uninstall refuses to overwrite them. Resolve the conflict instead of deleting the backups or transaction journal. Use uninstall to recover an interrupted installation. Shared runtime files remain available for other games.

## How the wrapper works

Your launcher runs `.dlssnr-linux/launch.sh` before its normal Wine/Proton command (Steam uses `%command%`). The wrapper checks the bridge and HIP runtime hashes, sets per-game GPU filters and DLL overrides, then forwards the original command and arguments unchanged. It preserves the existing Wine prefix and working directory. Steam container mounts are also supplied when applicable; they do not require Steam to be installed.

Inside Proton, the mod's `version.dll` hooks FSR. A small `amdhip64_7.dll` trampoline passes Windows HIP calls to a Linux bridge loaded through `LD_PRELOAD`. The bridge loads ROCm when HIP is first needed. Patched vkd3d-proton DLLs provide the D3D12/Vulkan side of buffer sharing with HIP.

The installer sets NR to `Enabled=1`, `Inline=1` and `Interop=1`. It adds files locally to the game and keeps backups; it does not modify shared Proton, system ROCm, saves or display settings.

<details>
<summary>Advanced: runtime version and storage</summary>

- The optional download is AMD's `rocm-sdk-core 7.14.0a20260612` wheel from `rocm.nightlies.amd.com`, checked against a pinned SHA256. It is a nightly build, not a guarantee across Linux distributions. HIP's own library dependencies may require a newer system than the bridge's glibc 2.34 minimum.
- Installer-managed runtime and support files default to `~/.local/share/dlssnr-linux/`, or `$XDG_DATA_HOME/dlssnr-linux/` when set. Keep these files while installed games use them.
- `--data-dir` changes where managed ROCm and converted weights are stored. It does not move the shared native bridge under `$XDG_DATA_HOME/dlssnr-linux/native/`.
- A custom `XDG_DATA_HOME` must be absolute, user-owned, and contain no whitespace or colons because of `LD_PRELOAD` path parsing. Game paths may contain spaces; mount paths cannot contain colons or newlines.
- With default paths, the extracted installer folder is not needed to run an installed game. Keep it if you want to run status or uninstall later. Do not move active runtime/support files without reinstalling affected games.
- On another PC, rerun the installer. Do not copy a generated `launch.sh` containing another machine's paths.

</details>

<details>
<summary>For developers: source tree and packaging</summary>

This Git tree contains the installer, launch-wrapper generator, packaging code and documentation. Tests stay local and are not published in Git or the installation archive. Binaries are release assets, not Git files. Native component sources, rebuild material and license notices are retained in the installation archive.

To package a source checkout, provide an extracted release with the matching components:

```sh
python3 -B build_release.py --components-root /path/to/extracted/dlssnr-linux-portable
(cd dist && sha256sum -c dlssnr-linux-portable.tar.gz.sha256)
```

The builder checks pinned binary hashes and uses a fixed file list. It excludes tests, private logs, Python environments, NVIDIA DLLs and weights. A checksum is not a signature or a license grant. Third-party rights remain with their owners; the upstream mod has no identified redistribution license. See `THIRD-PARTY.md`, `PROVENANCE.json` and the archive's `sources/README.md` for details.

</details>

## Legal

This project is not affiliated with or endorsed by NVIDIA or AMD. DLSS is a trademark of NVIDIA Corporation. The software here contains no NVIDIA code or data; it requires the user's own legitimately obtained copy of the DLSS 5 DLL. Provided as-is, without warranty; use at your own risk, your computer may explode or worse
