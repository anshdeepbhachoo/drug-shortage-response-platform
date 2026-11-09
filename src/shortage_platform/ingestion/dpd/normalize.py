def normalize_din(raw):
    if raw is None:
        return None
    
    text = str(raw).strip()

    if not text:
        return None
    
    if not (text.isascii() and text.isdigit()) or len(text) > 8:
        raise ValueError(f"invalid DIN: {raw!r}")
    
    return text.zfill(8)
