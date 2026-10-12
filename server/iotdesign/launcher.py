"""Windows-only hidden process launcher. Reuses this installation and refuses unrelated port owners."""
import argparse
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import psutil

from .config import Settings
from .db import Database
from .worker import worker_lock


def local_health(port):
    try:
        with urlopen(f'https://127.0.0.1:{port}/api/health', timeout=3,
                     context=ssl._create_unverified_context()) as response:
            return json.load(response)
    except (URLError, OSError, ValueError):
        return None


def port_busy(port):
    with socket.socket() as connection:
        return connection.connect_ex(('127.0.0.1', port)) == 0


def worker_arguments(profile, root, data):
    args = [sys.executable, '-m', 'iotdesign.cli', '--data', str(data), 'worker',
            '--repo', str(root / 'vendor' / 'vggt'), '--resolution', str(profile['resolution']),
            '--max-frames', str(profile['max_frames']), '--weight-precision', profile['weight_precision'],
            '--max-points', str(profile['max_points']), '--sam-model', profile['sam_model'],
            '--segmentation-edge', str(profile['segmentation_edge']),
            '--vram-budget-gb', str(profile['vram_budget_gb'])]
    if profile['semantic_objects']:
        args.append('--semantic-objects')
    return args


def start_hidden(args, root, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('ab', buffering=0) as output:
        return subprocess.Popen(args, cwd=str(root), stdout=output, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)


def stop_owned(data):
    path = data / 'launcher-processes.json'
    if not path.exists():
        print('No launcher-owned processes recorded.')
        return
    record = json.loads(path.read_text())
    for name in ('worker', 'server'):
        item = record.get(name)
        if not item or not psutil.pid_exists(item['pid']):
            continue
        process = psutil.Process(item['pid'])
        command = process.cmdline()
        if ('iotdesign.cli' not in command or '--data' not in command
                or Path(command[command.index('--data') + 1]).resolve() != data
                or abs(process.create_time() - item['created']) > 1):
            raise RuntimeError(f'Refusing to stop {name}: PID identity no longer matches this installation')
        children = process.children(recursive=True)
        owned_children = []
        for child in children:
            # CREATE_NO_WINDOW still creates a private console host on Windows.
            # Let that verified OS helper exit when its Python clients terminate.
            console_host = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32' / 'conhost.exe'
            if Path(child.exe()).resolve() == console_host.resolve():
                continue
            child_command = child.cmdline()
            if ('iotdesign.cli' not in child_command or '--data' not in child_command
                    or Path(child_command[child_command.index('--data') + 1]).resolve() != data):
                raise RuntimeError('Unexpected child process; stop it manually after inspection')
            owned_children.append(child)
        for target in reversed(owned_children):
            target.terminate()
        process.terminate()
        _, alive = psutil.wait_procs([*owned_children, process], timeout=5)
        if alive:
            raise RuntimeError(f'{name} did not stop; retaining process records for inspection')
        print(f'Stopped launcher-owned {name}.', flush=True)
    path.unlink(missing_ok=True)


def launch(root, data, profile, port, open_browser=False):
    settings = Settings(data=data)
    expected_id = Database(settings.db_path).get_meta('server_id', lambda: str(uuid.uuid4()))
    record_path = data / 'launcher-processes.json'
    # Separate launcher lock permits the worker to hold its own GPU lock continuously.
    import msvcrt
    with (data / 'launcher.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            record = json.loads(record_path.read_text()) if record_path.exists() else {}
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Another launcher is starting this installation. Try again shortly.') from exc
        try:
            health = local_health(port)
            if health and health.get('server_id') != expected_id:
                raise RuntimeError(f'Port {port} belongs to a different IoTDesign installation')
            if health is None and port_busy(port):
                raise RuntimeError(f'Port {port} is occupied by an unrelated service')
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            if health is None:
                log = data / 'logs' / f'server-{stamp}.log'
                process = start_hidden([sys.executable, '-m', 'iotdesign.cli', '--data', str(data),
                                        'serve', '--port', str(port), '--name', socket.gethostname()], root, log)
                record['server'] = {'pid': process.pid, 'created': psutil.Process(process.pid).create_time(),
                                    'log': str(log)}
                record_path.write_text(json.dumps(record, indent=2))
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    health = local_health(port)
                    if health:
                        break
                    if process.poll() is not None:
                        raise RuntimeError(f'Server failed to start. Read {log}')
                    time.sleep(0.5)
                if not health:
                    raise RuntimeError(f'Server startup timed out. Read {log}')
            else:
                print('Reusing the running server for this installation.', flush=True)
            if profile['worker_enabled']:
                available = True
                try:
                    with worker_lock(data):
                        pass
                except RuntimeError:
                    available = False
                if available:
                    log = data / 'logs' / f'worker-{stamp}.log'
                    process = start_hidden(worker_arguments(profile, root, data), root, log)
                    record['worker'] = {'pid': process.pid, 'created': psutil.Process(process.pid).create_time(),
                                        'log': str(log), 'profile': profile['profile']}
                    record_path.write_text(json.dumps(record, indent=2))
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        health = local_health(port)
                        if health and health.get('ready'):
                            break
                        if process.poll() is not None:
                            raise RuntimeError(f'Worker failed to start. Read {log}')
                        time.sleep(0.5)
                    if not health or not health.get('ready'):
                        raise RuntimeError(f'Worker startup timed out. Read {log}')
                    print(f"GPU worker ready with {profile['profile']} settings.", flush=True)
                else:
                    print('A GPU worker already owns this data directory; retaining its current settings. '
                          'Stop it before changing profiles.', flush=True)
            else:
                try:
                    with worker_lock(data):
                        pass
                except RuntimeError:
                    print('An existing GPU worker is still running. Stop it to use capture-only mode.', flush=True)
                else:
                    print('Capture-only server: local reconstruction worker disabled.', flush=True)
            url = f'https://127.0.0.1:{port}'
            print(f'Station: {url}\nLogs: {data / "logs"}', flush=True)
            if open_browser:
                webbrowser.open(url)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    if os.name != 'nt':
        raise RuntimeError('This launcher supports Windows only')
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile-file', type=Path)
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    root, data = Path.cwd().resolve(), args.data.resolve()
    data.mkdir(parents=True, exist_ok=True)
    if args.stop:
        stop_owned(data)
    else:
        if not args.profile_file:
            parser.error('--profile-file is required to start')
        launch(root, data, json.loads(args.profile_file.read_text(encoding='utf-8-sig')),
               args.port, args.open)


if __name__ == '__main__':
    main()
