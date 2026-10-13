"""Arranque portable; mantiene separados los imports y los índices de PDF y web."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

BASE = Path(__file__).resolve().parents[1]
MOTOR = BASE / 'motores' / 'chatbot_web'


def available(port):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'El puerto {port} está ocupado. Detén ese servidor o configura otro puerto.') from exc


def wait_ready(process, port, route):
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'El servicio terminó durante el arranque (código {process.returncode}).')
        try:
            with opener.open(f'http://127.0.0.1:{port}{route}', timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f'El servicio del puerto {port} no respondió en 60 segundos.')


def stop_process(process):
    """Detiene solo el servicio propio y sus hijos del entorno virtual Windows."""
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # venv/Scripts/python.exe puede ser un lanzador de otro python.exe.
        # terminate() sobre el padre deja al servidor hijo escuchando.
        subprocess.run(
            ['taskkill', '/PID', str(process.pid), '/T', '/F'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, check=False,
        )
    else:
        process.terminate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('modo', choices=['integrado', 'web', 'reindexar', 'preguntar', 'visor'])
    args, extra = parser.parse_known_args()
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    if args.modo in {'reindexar', 'preguntar'}:
        command = [sys.executable, str(MOTOR / (args.modo + '.py'))]
        if args.modo == 'preguntar':
            command += ['--url', 'http://127.0.0.1:' + env.get('UNAMBA_WEB_PORT', '8002')]
        else:
            command += ['--output', str(BASE / 'datos/web/actualizacion.json')]
        return subprocess.call(command + extra, cwd=MOTOR, env=env)
    if extra:
        parser.error('Argumentos no reconocidos: ' + ' '.join(extra))
    web_port = int(env.get('UNAMBA_WEB_PORT', '8002'))
    app_port = int(env.get('UNAMBA_PORT', '5000'))
    port = int(env.get('UNAMBA_VISOR_PORT', '8003')) if args.modo == 'visor' else web_port
    processes = []
    try:
        available(port)
        if args.modo == 'integrado':
            if port == app_port:
                raise RuntimeError('UNAMBA_PORT y UNAMBA_WEB_PORT deben ser diferentes.')
            available(app_port)
        module = 'ver_indice:app' if args.modo == 'visor' else 'main:app'
        backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', module,
            '--host', '127.0.0.1', '--port', str(port), '--workers', '1'], cwd=MOTOR, env=env)
        processes.append(backend)
        wait_ready(backend, port, '/indice/resumen')
        print(f'Explorador web: http://127.0.0.1:{port}/', flush=True)
        if args.modo == 'integrado':
            server = subprocess.Popen([sys.executable, str(BASE / 'servidor/app_integrada.py')], cwd=BASE, env=env)
            processes.append(server)
            wait_ready(server, app_port, '/salud')
            print(f'Servidor integrado: http://127.0.0.1:{app_port} (/chat/docs y /chat/web)', flush=True)
        print('Pulsa Ctrl+C para detener estos servicios.', flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError('Uno de los servicios se detuvo. Revisa el error anterior.')
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            stop_process(process)
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    raise SystemExit(main())
