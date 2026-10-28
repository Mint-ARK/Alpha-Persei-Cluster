"""Compatibility entry for the consolidated CDN foundation regression suite.

The former streaming tests allowed bytes to reach a client before validation;
the stronger pre-publication contract is exercised by test_cdn_foundation.py.
"""
if __name__ == '__main__':
    from pathlib import Path
    import runpy
    runpy.run_path(str(Path(__file__).with_name('test_cdn_foundation.py')), run_name='__main__')
