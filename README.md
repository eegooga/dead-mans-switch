# dead-mans-switch

## Run with Docker Compose

1. Copy env template and fill in real values:
   ```bash
   cp .env.example .env
   ```
2. Put one or more message files into `messages/`.
   Each file format:
   - line 1: comma-separated recipients
   - line 2: `Subject: ...` (or `Onderwerp: ...`)
   - line 3+: email body
3. Start the app:
   ```bash
   docker compose up -d --build
   ```
4. Watch logs:
   ```bash
   docker compose logs -f
   ```

## Stop

```bash
docker compose down
```


### SMTP auth note

If your SMTP provider rejects login with `535 Incorrect authentication data`, check the SMTP mode:
- `EMAIL_SECURITY=auto` — uses `STARTTLS` on ports other than `465`, and `SSL` on `465`
- `EMAIL_SECURITY=starttls` — always use STARTTLS
- `EMAIL_SECURITY=ssl` — always use SSL/TLS (implicit TLS)

For many providers, `465 + ssl` or `587 + starttls` is required.
