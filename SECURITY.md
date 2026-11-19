# Security

The drug shortage response platform is a hackathon project that models hospital drug-shortage workflows. It is not a production clinical system and does not have a formal vulnerability-response program.

## Reporting a concern

If you find a security issue (for example an accidentally committed secret or unsafe handling of credentials), contact the repository maintainers through the private team channel or GitHub private vulnerability reporting if enabled. Do not open a public issue that includes secret values.

## Secrets

- Keep credentials in a local `.env` file. Never commit `.env`.
- Use `.env.example` only for empty or placeholder values.
- Do not paste API keys, database passwords, AWS tokens, or private keys into pull requests, screenshots, or chat logs.
- If a secret is exposed, rotate it with the service provider and tell the maintainers.

## Scope notes

- Synthetic hospital data in this repository is fictional demonstration data.
- Do not load real patient information into local databases or commits.
- CI does not use production credentials, AWS accounts, or live hospital systems.
- Order submission defaults to simulation (no live supplier order). Do not configure live supplier credentials in a shared demo environment without team agreement.
