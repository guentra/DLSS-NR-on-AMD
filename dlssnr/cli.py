"""English terminal interface for the per-game DLSS-NR installer."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys

from . import assets, conversion, deploy, games, runtime

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TARGETS = frozenset(('gfx1100', 'gfx1101', 'gfx1102', 'gfx1201'))


def parser():
    result = argparse.ArgumentParser(description='DLSS-NR on AMD Linux / Proton: per-game installer')
    commands = result.add_subparsers(dest='command')
    for name in ('list-games', 'list-protons'):
        command = commands.add_parser(name)
        command.add_argument('--steam-root', type=Path)
        command.add_argument('--json', action='store_true')
    for name in ('install', 'doctor', 'status', 'uninstall'):
        command = commands.add_parser(name)
        command.add_argument('--appid', help='Exact Steam application ID')
        command.add_argument('--exe', type=Path, help='Game Windows x64 executable')
        command.add_argument('--game-dir', type=Path, help='Game directory (default: directory containing install.sh)')
        command.add_argument('--steam-root', type=Path)
        command.add_argument('--json', action='store_true', help='Machine-readable JSON output')
        if name == 'uninstall':
            command.add_argument('--yes', action='store_true', help='Confirm restoring original files')
        if name in ('install', 'doctor'):
            command.add_argument('--runner', '--proton', dest='proton', type=Path,
                                 help='Installed Wine/Proton runner directory used by this game')
            command.add_argument('--confirm-runner', '--confirm-proton', dest='confirm_proton', action='store_true',
                                 help='Confirm this runner is used by your launcher or Wine command')
            command.add_argument('--gpu', help='HIP index or exact GPU name')
            command.add_argument('--hip-library', type=Path, help='Existing HIP7 library or SDK directory')
            command.add_argument('--data-dir', type=Path, help='Persistent user-local runtime/cache directory')
            command.add_argument('--install-rocm', action='store_true', help='Allow a verified AMD wheel download if needed')
            inputs = command.add_mutually_exclusive_group()
            inputs.add_argument('--weights', type=Path, help='Your existing DLSSNRW1 weights file')
            inputs.add_argument('--nvidia-dll', type=Path, help='Your legally obtained nvngx_dlssnr.dll (310.8.0.0)')
            command.add_argument('--accept-risk', action='store_true', help='Accept experimental injection risks')
            command.add_argument('--replace-existing', action='store_true', help='Back up and replace conflicting DLLs')
            command.add_argument('--allow-unconfirmed-loader', action='store_true',
                                 help='Allow installation without static evidence of mod loading; activation remains unverified')
            command.add_argument('--dry-run', action='store_true', help='No writes, downloads or conversion')
    command = commands.add_parser('runtime', help='Check or install a user-local HIP7 runtime')
    command.add_argument('--hip-library', type=Path)
    command.add_argument('--data-dir', type=Path)
    command.add_argument('--install-rocm', action='store_true', help='Allow the pinned official AMD wheel')
    command.add_argument('--self-test', action='store_true', help='Explicit GPU memory-copy test (256 bytes)')
    command.add_argument('--json', action='store_true')
    return result


def choose(items, label, display):
    for index, item in enumerate(items, 1):
        print(f'  {index}. {display(item)}')
    answer = input(f'{label} (number; empty to cancel): ').strip()
    if not answer.isascii() or not answer.isdecimal() or not 1 <= int(answer) <= len(items):
        raise RuntimeError('Selection cancelled or invalid.')
    return items[int(answer) - 1]


def select_gpu(devices, requested, interactive):
    supported = [d for d in devices if d.get('arch', '').split(':', 1)[0] in TARGETS]
    if requested is not None:
        selected = [d for d in supported if str(d['index']) == requested or d['name'] == requested]
        if len(selected) == 1:
            return selected[0]
        raise RuntimeError('GPU missing, ambiguous or unsupported; use its exact HIP index with --gpu.')
    if len(supported) == 1:
        return supported[0]
    if not supported:
        raise RuntimeError('Unsupported GPU. Bundled targets: ' + ', '.join(sorted(TARGETS)))
    if interactive:
        return choose(supported, 'GPU', lambda d: f"HIP {d['index']}: {d['name']} ({d['arch']})")
    raise RuntimeError('Multiple compatible GPUs; select one explicitly with --gpu INDEX.')


def resolve_exe(args, interactive):
    if args.appid and (not args.appid.isascii() or not args.appid.isdecimal()):
        raise RuntimeError('--appid must be an exact numeric Steam ID.')
    if args.appid:
        if args.game_dir:
            raise RuntimeError('--game-dir and --appid cannot be combined.')
        entries = games.discover_games(args.steam_root)
        entries = [g for g in entries if g['appid'] == args.appid]
        if len(entries) != 1:
            raise RuntimeError('AppID missing or ambiguous; use --steam-root or --exe without --appid.')
        return games.select_executable(entries[0]['path'], args.exe)
    if args.exe and not args.game_dir:
        path = args.exe.expanduser().absolute()
        if path.is_symlink():
            raise RuntimeError('The game executable must not be a symlink.')
        return games.select_executable(path.parent, path)
    root = args.game_dir.expanduser().absolute() if args.game_dir else PACKAGE_ROOT
    # The release may be extracted as a subfolder instead of flattened into the game.
    # Never climb arbitrary ancestors or search the user's other games implicitly.
    if not args.game_dir and root.name == 'dlssnr-linux-portable':
        root = root.parent
    try:
        return games.select_executable(root, args.exe)
    except RuntimeError as exc:
        if not interactive or args.exe or not str(exc).startswith(('No PE x64 executable found', 'Multiple possible executables')):
            raise
        print(str(exc))
        candidates = games.find_executables(root)
        if candidates:
            selected = choose(candidates, 'Game executable', lambda p: str(p.relative_to(root)))
            return games.select_executable(root, selected)
        entered = input('Game directory or .exe path (empty to cancel): ').strip()
        if entered:
            path = Path(entered).expanduser().absolute()
            return games.select_executable(path if path.is_dir() else path.parent,
                                           None if path.is_dir() else path)
    raise RuntimeError('Specify --game-dir /path/game or --exe /path/game.exe; Steam lookup is optional with --appid ID.')


def require(accepted, interactive, question, flag):
    if accepted:
        return
    if interactive and input(question + ' [y/N] ').strip().casefold() in ('y', 'yes'):
        return
    raise RuntimeError(f'Explicit confirmation required: {flag}. {question}')


def data_dir(args):
    base = args.data_dir or Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'dlssnr-linux'
    base = Path(base).expanduser()
    if not base.is_absolute():
        raise RuntimeError('--data-dir / XDG_DATA_HOME must be absolute.')
    return base


def check_host(manifest):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise RuntimeError('This package requires Linux x86_64.')
    name, version = platform.libc_ver()
    minimum = tuple(map(int, manifest.get('minimum_glibc', '2.34').split('.')))
    if name != 'glibc' or tuple(map(int, version.split('.'))) < minimum:
        raise RuntimeError('glibc >= ' + '.'.join(map(str, minimum)) + ' is required by the prebuilt bridge.')
    if os.geteuid() == 0:
        raise RuntimeError('Do not run this installer as sudo/root; use the game owner account.')
    return {'system': platform.system(), 'machine': platform.machine(), 'glibc': version}


def resolve_proton(args, interactive):
    if args.proton:
        return games.validate_proton(args.proton)
    if interactive:
        print('Enter the Wine/Proton runner directory used by this game.\n\n'
              'Examples:\n'
              '  Steam:  ~/.local/share/Steam/compatibilitytools.d/GE-Proton...\n'
              '  Lutris: ~/.local/share/lutris/runners/wine/wine-ge-...\n\n'
              "Select the runner's folder, not its bin/wine executable.\n"
              'Type "steam" to list Steam runners, or leave empty to cancel.\n')
        entered = input('Runner directory: ').strip()
        if entered and entered.casefold() != 'steam':
            return games.validate_proton(Path(entered).expanduser())
        if not entered:
            raise RuntimeError('No runner selected; specify --runner /path/to/runner.')
    elif not args.appid and not args.steam_root:
        raise RuntimeError('Specify --runner /path/to/runner (alias: --proton). Steam discovery is optional.')
    valid, errors = [], []
    for candidate in games.discover_protons(args.steam_root):
        try:
            valid.append(games.validate_proton(candidate))
        except RuntimeError as exc:
            errors.append(str(exc))
    if len(valid) == 1:
        return valid[0]
    if valid and interactive:
        return choose(valid, 'Runner (use the same one in your launcher)', lambda p: str(p['root']))
    raise RuntimeError('Specify --runner /path/to/runner; multiple candidates or none compatible.\n' + '\n'.join(errors))


def readonly_runtime(args):
    supplied = args.hip_library.expanduser().absolute() if args.hip_library else None
    if supplied and not supplied.is_dir():
        candidates = [supplied]
    else:
        candidates = runtime.discover_runtimes([supplied] if supplied else None,
                                               managed_root=data_dir(args) / 'rocm-venv')
        if supplied:
            candidates = [p for p in candidates if p.resolve().is_relative_to(supplied.resolve())]
    errors = []
    for candidate in candidates:
        try:
            return runtime.probe_runtime(candidate)
        except runtime.DriverUnavailable:
            raise
        except RuntimeError as exc:
            errors.append(str(exc))
    raise RuntimeError('HIP7 unavailable; doctor/--dry-run never download. '
                       'Use runtime --install-rocm or --hip-library.\n' + '\n'.join(errors))


def ensure_runtime(args, interactive):
    try:
        return runtime.ensure_runtime(data_dir(args), supplied=args.hip_library, allow_install=args.install_rocm)
    except runtime.DriverUnavailable:
        raise
    except RuntimeError as exc:
        if not interactive or args.hip_library or args.install_rocm or '--install-rocm' not in str(exc):
            raise
        print(str(exc))
        require(False, True, 'Download the pinned official AMD HIP7 wheel into your user directory (3 GiB free required)?', '--install-rocm')
        return runtime.ensure_runtime(data_dir(args), allow_install=True)


def emit(result, args):
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return
    if args.command == 'runtime':
        print('HIP7 verified:', result['library'])
        for device in result['devices']:
            print(f"  HIP {device['index']}: {device['name']} ({device['arch']})")
        print('GPU copy test:', 'completed' if args.self_test else 'not requested')
        return
    if args.command == 'doctor':
        print('Static and HIP prerequisites checked. No files modified.')
        print('Game:', result['game']['exe'])
        print('HIP:', result['runtime']['library'])
        print('GPU:', result['gpu']['name'])
        print('Checked Wine/Proton runner:', result['proton']['root'])
        for warning in result.get('warnings', []):
            print('Warning:', warning)
        print('Actual launcher/runner selection and in-game rendering are NOT verified.')
        return
    if result.get('dry_run'):
        print('Dry run completed; nothing installed.')
    elif result.get('updated') and result.get('valid'):
        print('Existing installation updated and verified on disk. Original backups and NR settings retained; selected GPU applied.')
    elif result.get('installed') and result.get('valid'):
        print('Installation verified on disk (not an in-game rendering validation).')
    elif result.get('installed') or result.get('pending'):
        print('Installation needs attention; read diagnostics before making changes.')
    else:
        print('No active managed installation.')
    if result.get('exe'):
        print('Game:', result['exe'])
    if result.get('gpu'):
        print('GPU:', result['gpu']['name'])
    if result.get('proton'):
        print('Use this Wine/Proton runner in your launcher:', result['proton'])
    if result.get('command_prefix'):
        print('Command prefix for Lutris / another launcher (no %command% outside Steam):')
        print(result['command_prefix'])
        print('Keep your existing runner, Wine prefix and game arguments; do not launch the .exe directly from Linux.')
    if result.get('launch_options'):
        print('Steam launch options (only if using Steam; preserve unrelated existing options):')
        print(result['launch_options'])
        print('First-install defaults: Enabled=1 / Inline=1 / Interop=1; updates preserve existing settings. Enable FSR as the injection hook; End opens the mod menu.')
    for warning in result.get('warnings', []):
        print('Warning:', warning)
    for note in result.get('notes', []):
        print('-', note)
    if args.command == 'uninstall':
        print('Remove the wrapper from your launcher command prefix or Steam launch options. Shared ROCm/bridge caches are retained.')


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments and not sys.stdin.isatty():
        print('Usage: install --exe /path/game.exe; see --help.', file=sys.stderr)
        return 2
    args = parser().parse_args(arguments or ['install'])
    interactive = sys.stdin.isatty() and not args.json
    try:
        manifest = None
        if args.command == 'install':
            manifest = assets.verify_assets(PACKAGE_ROOT)
            if not args.json:
                print('DLSS-NR Linux installer:', manifest.get('version', 'unknown'))
                print('Mod to install: DLSS-NR on AMD', manifest.get('mod_version', 'unknown'))
                print()
        if args.command in ('list-games', 'list-protons'):
            if args.command == 'list-games':
                rows = games.discover_games(args.steam_root)
            else:
                rows = []
                for candidate in games.discover_protons(args.steam_root):
                    try:
                        games.validate_proton(candidate)
                        rows.append({'path': candidate, 'compatible': True})
                    except RuntimeError as exc:
                        rows.append({'path': candidate, 'compatible': False, 'reason': str(exc)})
            if args.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
            else:
                for row in rows:
                    print(f"{row.get('appid', 'OK' if row.get('compatible') else 'INCOMPATIBLE')}  {row.get('name', '')}  {row['path']}")
                    if row.get('reason'):
                        print('  ' + row['reason'])
                if not rows:
                    print('No entries found. Use --steam-root or an explicit --exe / --proton path.')
            return 0
        if args.command == 'runtime':
            check_host({})
            rt = ensure_runtime(args, interactive)
            if args.self_test:
                rt = runtime.probe_runtime(rt['library'], self_test=True)
            emit(rt, args)
            return 0
        exe = resolve_exe(args, interactive)
        if args.command == 'status':
            status = deploy.status_game(exe)
            emit(status, args)
            return 1 if status['pending'] else 0
        if args.command == 'uninstall':
            require(args.yes, interactive, 'Restore original files and uninstall?', '--yes')
            result = deploy.uninstall_game(exe)
            actual = deploy.status_game(exe)
            if actual['installed'] or actual['pending']:
                raise RuntimeError('Restore incomplete; retain all backups and the journal.')
            emit(result, args)
            return 0
        if manifest is None:
            manifest = assets.verify_assets(PACKAGE_ROOT)
        host = check_host(manifest)
        evidence = games.inspect_game(exe)
        for key, explanation in (('dx12', 'No static DirectX 12 evidence found'),
                                 ('fsr_evidence', 'No FSR/FidelityFX evidence found')):
            if not evidence[key]:
                raise RuntimeError(explanation + '; automatic installation is not supported for this game.')
        if evidence['anti_cheat_evidence']:
            raise RuntimeError('Anti-cheat detected: refusing installation, no bypass. ' + ', '.join(evidence['anti_cheat_evidence']))
        loader_warnings = [] if evidence['version_loader'] else [
            'Mod loading is not confirmed by static imports. Dynamic loading may exist; '
            'copying version.dll alone does not guarantee activation.']
        proton = resolve_proton(args, interactive)
        if args.command == 'doctor':
            rt = readonly_runtime(args)
            gpu = select_gpu(rt['devices'], args.gpu, interactive)
            report = {'host': host, 'game': evidence, 'proton': proton, 'runtime': rt, 'gpu': gpu,
                      'gameplay_verified': False, 'steam_selection_verified': False, 'runner_selection_verified': False,
                      'warnings': loader_warnings}
            if args.weights:
                report['weights'] = assets.validate_weights(args.weights.expanduser())
            emit(report, args)
            return 0
        if loader_warnings:
            require(args.allow_unconfirmed_loader, interactive,
                    loader_warnings[0] + ' Install anyway and verify mod loading in the game log?',
                    '--allow-unconfirmed-loader')
        if interactive:
            print('Game executable:', exe)
        require(args.confirm_proton, interactive,
                f"Does your launcher or Wine command use {proton['root']} for this game?",
                '--confirm-runner (alias: --confirm-proton)')
        require(args.accept_risk, interactive, 'Experimental injection may crash, render incorrectly or trigger anti-cheat. Continue?', '--accept-risk')
        if deploy.running_game(exe):
            raise RuntimeError('Close the game before installation.')
        rt = readonly_runtime(args) if args.dry_run else ensure_runtime(args, interactive)
        gpu = select_gpu(rt['devices'], args.gpu, interactive)
        weights = args.weights.expanduser() if args.weights else exe.parent / deploy.WEIGHTS
        if not args.weights and not args.nvidia_dll and not weights.is_file():
            roots = [exe.parent]
            if exe.parent.name.lower() == 'win64' and exe.parent.parent.name.lower() == 'binaries':
                roots.append(exe.parent.parent.parent.parent)
            local_root = args.game_dir or (PACKAGE_ROOT.parent if PACKAGE_ROOT.name == 'dlssnr-linux-portable' else PACKAGE_ROOT)
            local_root = local_root.expanduser().resolve()
            if exe.is_relative_to(local_root):
                roots.append(local_root)
            for root in dict.fromkeys(roots):
                candidate = root / 'nvngx_dlssnr.dll'
                if candidate.is_file() and not candidate.is_symlink():
                    args.nvidia_dll = candidate
                    if not args.json:
                        print('Detected NVIDIA DLL:', candidate)
                    break
        if not args.weights and not args.nvidia_dll and not weights.is_file() and interactive:
            path = input('Path to your DLSSNRW1 weights file or nvngx_dlssnr.dll (empty to cancel): ').strip()
            if not path:
                raise RuntimeError('No weights selected; nothing installed.')
            supplied = Path(path).expanduser()
            if supplied.suffix.lower() == '.dll':
                args.nvidia_dll = supplied
            else:
                weights = supplied
        if args.nvidia_dll:
            if args.dry_run:
                raise RuntimeError('--dry-run cannot convert a DLL; provide existing converted weights with --weights.')
            weights = conversion.convert_weights(PACKAGE_ROOT, args.nvidia_dll, proton, data_dir(args))
        assets.validate_weights(weights)
        conflicts = [name for name in deploy.DLLS if (exe.parent / name).exists()]
        replace = args.replace_existing
        if conflicts and not replace and not (exe.parent / deploy.STORE).exists():
            require(False, interactive, 'Back up and replace existing files: ' + ', '.join(conflicts) + '?', '--replace-existing')
            replace = True
        result = deploy.install_game(exe, PACKAGE_ROOT, rt, gpu, proton, weights,
                                     acknowledge_risk=True, replace_existing=replace, dry_run=args.dry_run)
        emit(dict(result, exe=exe, gpu=gpu, proton=proton['root'],
                  loader_evidence=evidence.get('loader_evidence', []),
                  mod_loading_verified=False, warnings=loader_warnings), args)
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print('Cancelled. Run status before your next operation.', file=sys.stderr)
        return 130
