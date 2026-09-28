"""Read the saved Google key and print compatible model metadata without exposing it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcq_maker.ai_library import AILibrary
from mcq_maker.ai_providers import list_models


library = AILibrary(Path(sys.argv[1]))
key = next(item for item in library.load()['keys'] if item['provider'] == 'google')
models = list_models('google', library.secret(key['id']))
library.set_models('google', models)
print(f'ACCOUNT={key["nickname"]}')
for model_id, name, thinking in models:
    print(f'{model_id}\t{name}\tthinking={thinking}')
