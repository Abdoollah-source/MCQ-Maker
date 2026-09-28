"""Report unresolved direct PE imports for a packaged extension."""
from pathlib import Path
import os
import sys

import pefile


target = Path(sys.argv[1]).resolve()
search = [target.parent, target.parent.parent, Path(os.environ['SystemRoot']) / 'System32']
pe = pefile.PE(str(target), fast_load=False)
for dependency in pe.DIRECTORY_ENTRY_IMPORT:
    name = dependency.dll.decode(errors='replace')
    candidate = next((folder / name for folder in search if (folder / name).exists()), None)
    if candidate is None:
        print(f'MISSING DLL {name}')
        continue
    dep = pefile.PE(str(candidate), fast_load=False)
    exports = getattr(dep, 'DIRECTORY_ENTRY_EXPORT', None)
    names = {symbol.name for symbol in exports.symbols if symbol.name} if exports else set()
    ordinals = {symbol.ordinal for symbol in exports.symbols} if exports else set()
    missing = []
    for item in dependency.imports:
        if item.name is not None and item.name not in names:
            missing.append(item.name.decode(errors='replace'))
        elif item.name is None and item.ordinal not in ordinals:
            missing.append(f'ordinal {item.ordinal}')
    if missing:
        print(f'{name} from {candidate}: {missing}')
