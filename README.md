# DLSS-NR on AMD for Linux

Run [DLSS-NR on AMD](https://github.com/danielblnc/DLSS-NR-on-AMD) on Linux through Wine/Proton.

**Experimental:** installation can succeed even if rendering does not work correctly in your game.

## Install

1. [Download **`dlssnr-linux-portable.tar.gz`**](https://github.com/guentra/DLSS-NR-on-AMD-Linux/releases/tag/linux-v0.1.2.1) ²
2. Extract it into the game folder. Open a terminal in the extracted `dlssnr-linux-portable` subfolder and run:

   ```sh
   ./install.sh
   ```

3. Follow the wizard. It detects the game, asks which Wine/Proton version you use, and offers to install ROCm if needed.
4. Copy the launch command it prints into your launcher:
   - **Steam:** paste **Steam launch options** into **Properties → General → Launch Options**. Select the same Proton in **Compatibility**.
   - **Lutris / another launcher:** paste **Command prefix** into its command-prefix field, without `%command%`. Keep your existing Wine/Proton version and prefix.
5. Launch the game, enable **FSR** in its graphics settings, and press **End** for the mod menu. FSR is the hook; neural rendering is provided by DLSS-NR.

**Already have `nvngx_dlssnr.dll`?** Copy your legitimately obtained **version 310.8.0.0** into the game folder before running the installer. It is detected automatically. Existing converted weights are also accepted. Neither is included in the download.

## Requirements

- Linux x86_64, Python 3.10+, Bash and glibc 2.34+.
- An AMD GPU targeting `gfx1100`, `gfx1101`, `gfx1102` or `gfx1201`, with working drivers and GPU permissions.
- A Windows x64 DirectX 12 game with FSR, already running through compatible Wine/Proton. Not every Wine build is supported. Detected anti-cheat blocks installation.
- **3 GiB free** if ROCm needs downloading. The wizard handles ROCm, not your GPU driver or Wine/Proton installation. **Do not use `sudo`.**

## Troubleshooting

**Wrong executable?** Select the actual game executable (often under `Binaries/Win64` for Unreal):

```sh
./install.sh install --exe '/path/to/game/Game.exe'
```

- **Unsure which Proton to select?** Type `steam` at the runner prompt to list installed Steam runners. Otherwise, provide the runner folder used by your launcher, not `bin/wine`.
- **Mod not loading?** Check that the printed wrapper is used as a command prefix, not a pre-launch script. Logs are beside the executable: `dlssnr_on_amd.log` and `.dlssnr-linux/logs/`.
- **Prerequisite errors?** Run `./install.sh doctor` for diagnostics. All optional arguments are listed by `./install.sh install --help`.
- **Resident Evil Requiem:** GPU faults and crashes have been reported with mod v0.2.11. No fix is validated; do not keep retrying after GPU faults.

## Update or uninstall

To **update**, extract the new installer and run `./install.sh` again. Managed installations are updated in place, keeping NR settings and the original backups. A newly selected GPU is applied. Modified DLLs still require review.

Close the game, then run from the installer folder:

```sh
./install.sh uninstall
```

Confirm restoration of the backed-up files, then **remove the wrapper from your launcher settings**. If uninstall reports changed files, keep the backups and resolve the conflict rather than deleting them.

## How the wrapper works

The wrapper starts your usual Wine/Proton command with the Linux HIP bridge enabled. Windows HIP calls pass through a small trampoline to ROCm, loaded only when needed. Neural rendering still uses the upstream mod's kernels.

Files are installed per game with backups. Keep `~/.local/share/dlssnr-linux/` while games use it, and rerun the installer on another PC. See [third-party notices](THIRD-PARTY.md) for component and licensing details.

## Legal

This project is not affiliated with or endorsed by NVIDIA or AMD. DLSS is a trademark of NVIDIA Corporation. The software here contains no NVIDIA code or data; it requires the user's own legitimately obtained copy of the DLSS 5 DLL. Provided as-is, without warranty; use at your own risk, your computer may explode or worse
