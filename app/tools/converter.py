from pydantic2ts import generate_typescript_defs

# uv run app/converter.py
generate_typescript_defs('app.models', './interfaces/types.ts')
