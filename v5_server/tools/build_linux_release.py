"""Build a Python-source core ZIP with Linux launch and dependency files."""
from pathlib import Path
import build_release_package as backend


def main():
    for name in ('run_server.sh', 'setup_linux.sh', 'manage_server.sh', 'linux_manager.py',
                 'admin_access.py', 'requirements.txt', 'README_Linux.md'):
        path = backend.V5_SERVER / name
        if not path.is_file() or not path.stat().st_size:
            raise FileNotFoundError(f'Missing Linux runtime file: {name}')
        if name.endswith('.sh') and b'\r' in path.read_bytes():
            raise ValueError(f'{name} must use LF line endings')
    backend.build_release_package('AetherGazer_Server_v5_Linux_Core.zip')


if __name__ == '__main__':
    main()
