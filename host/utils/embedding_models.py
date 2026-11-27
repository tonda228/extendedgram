def parse_embedding_models(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError("Provide between 1 and 32 embedding model names")
    if any(not isinstance(model, str) or not 1 <= len(model) <= 200
           or not model.isascii() or not model.isprintable() or any(char.isspace() for char in model)
           for model in value):
        raise ValueError("Embedding model names must be 1–200 printable ASCII characters without spaces")
    return tuple(dict.fromkeys(value))
