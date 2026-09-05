# Third-party components and redistribution limits

This is an unofficial Linux/Proton adaptation. Publication does not grant a license to third-party components or establish permission for their further redistribution. Third-party rights remain with their respective owners. No NVIDIA/AMD affiliation or endorsement is claimed. The source paths described below refer to the installation archive; the Git tree contains only the installer/wrapper and documentation; tests are not published.

## DLSS-NR on AMD (danielblnc), v0.2.11

Project: https://github.com/danielblnc/DLSS-NR-on-AMD

Release: https://github.com/danielblnc/DLSS-NR-on-AMD/releases/tag/v0.2.11

The bundled setup SHA256 matches the release digest published by GitHub. `version.dll` matches the local staged v0.2.11 payload; extraction was not repeated during the packaging audit. The other four binary assets are local Linux adaptations, not four additional upstream downloads.

GitHub's `/license` API returned HTTP 404. The accessible repository exposes README and `.github`, without an identified license. Its README Legal notice is preserved verbatim in `licenses/DLSS-NR-upstream-notice.txt`, with the verified Git blob identifier. It requires a legitimate NVIDIA DLL, disclaims affiliation and warranty, and states the upstream software contains no NVIDIA code or data. **That notice does not explicitly grant redistribution permission.** Download availability is not permission. Obtain authorization before publication. Complete upstream mod source was not found in that repository.

## Modified vkd3d-proton

`d3d12.dll` and `d3d12core.dll` match the local interop build outputs.

Base: https://github.com/HansKristian-Work/vkd3d-proton.git

Commit: `35bdee1435c94f8c3548725fcb046595b263bd7e`

The upstream project is LGPL-2.1-or-later; see `licenses/vkd3d-proton-LICENSE`, COPYING and AUTHORS. Dependency notices are preserved separately in `licenses/vkd3d-dependency-notices.txt`; those licenses are not replaced by LGPL.

`sources/vkd3d-proton-ordered.patch` contains all five modified tracked files and both added `nr_ordered_client.h` / `nr_ordered_commands.h` headers. This is not an unmodified upstream release. Existing file licenses continue to apply; this document does not invent a grant or attribution for unmarked local additions.

`sources/fetch_vkd3d.py` reconstructs the pinned base and submodules, then applies the patch. The patch was applied to a temporary export of the exact commit and all seven resulting files compared byte-for-byte with the local sources. A complete vkd3d rebuild was not repeated. This bundle supplies a source-plus-patch reconstruction recipe requiring network access, not a complete offline checkout. Before public redistribution, check the LGPL corresponding-source delivery requirements and continued source availability. This recipe is not a legal compliance certification.

## Local HIP bridge and PE trampoline

The bundled `libdlssnr_hip_bridge.so` was rebuilt byte-identically from `native/` using the documented command. `amdhip64_7.dll` matches the local PE trampoline; despite its filename, it is **not AMD's Windows runtime**. Its C/header/DEF sources are in `sources/trampoline/`. A byte-identical trampoline rebuild and its original build command were not established.

Explicit standalone license grants for local custom sources and the installer were not identified. Clarify these before publication; no MIT/LGPL grant is arbitrarily assigned here.

## ROCm and NVIDIA: not bundled

ROCm wheels are not included. With consent, `--install-rocm` downloads the pinned official AMD wheel and retains its own metadata and notices. Its licenses apply separately. It does not install a kernel driver.

Neither `nvngx_dlssnr.dll` nor converted weights are distributed. Supply your own legitimate copy and comply with its license. SHA256 identifies a file; it does not grant use, conversion or redistribution rights. DLSS is a trademark of NVIDIA Corporation.
