def generate_allowed_origins(domain: str) -> list[str]:

    domain = (
        domain.lower()
        .replace("http://", "")
        .replace("https://", "")
        .replace("www.", "")
        .rstrip("/")
    )

    return [
        f"https://{domain}",
        f"https://www.{domain}"
    ]