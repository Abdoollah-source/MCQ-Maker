"""Development-only acquisition of the small MIT-licensed Fluent icon subset."""
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets'
root.mkdir(parents=True, exist_ok=True)
base = 'https://raw.githubusercontent.com/microsoft/fluentui-system-icons/main/'
for name, folder, slug in [('create', 'Document', 'document'), ('folder', 'Folder', 'folder'),
                           ('history', 'History', 'history'), ('templates', 'Document Copy', 'document_copy'),
                           ('settings', 'Settings', 'settings')]:
    url = base + 'assets/' + folder.replace(' ', '%20') + '/SVG/ic_fluent_' + slug + '_20_regular.svg'
    with urlopen(url, timeout=30) as response:
        (root / (name + '.svg')).write_bytes(response.read())
with urlopen(base + 'LICENSE', timeout=30) as response:
    (root / 'Fluent-Icons-LICENSE.txt').write_bytes(response.read())
print('Saved five Fluent icons and license.')
