from setuptools import setup

APP = ['main.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': True,
    'packages': ['PyQt6', 'requests', 'pytz', 'pandas', 'openpyxl'],
    'iconfile': None,
    'plist': {
        'CFBundleName': 'MasaLogViewer',
        'CFBundleDisplayName': 'Masa Log Viewer',
        'CFBundleIdentifier': 'com.example.masalogviewer',
        'CFBundleVersion': '1.0.0',
    },
}

setup(
    app=APP,
    name='MasaLogViewer',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
