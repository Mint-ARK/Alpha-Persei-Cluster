"""Compatibility entry for the three-platform manifest manager acceptance suite."""
if __name__ == '__main__':
    from pathlib import Path
    import runpy
    runpy.run_path(str(Path(__file__).with_name('verify_manifest_management.py')), run_name='__main__')
