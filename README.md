# JAXN Assist — Full MVP

A runnable MVP for a source-grounded industrial knowledge assistant.

## Included
- Admin + employee login
- Organization-scoped data model
- PDF upload and page indexing
- Local document retrieval (works without a paid AI API)
- Optional OpenAI Responses API synthesis when an API key is added
- Source list with page numbers
- Conservative safety fallback when approved documents do not support an answer
- Admin dashboard
- Feedback capture
- SQLite database
- Render deployment blueprint
- Docker support
- Synthetic sample SOP for safe testing

## Demo logins
Admin: `admin@jaxnassist.demo` / `JaxnAdmin123!`
Worker: `worker@jaxnassist.demo` / `JaxnWorker123!`

Change these before any real deployment.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```
Open http://127.0.0.1:8000

## AI connection
The app functions in local retrieval/demo mode without a key. To add generated answers grounded in retrieved excerpts, put an OpenAI API key into `.env`:

`OPENAI_API_KEY=...`

Default model in this starter is `gpt-5.6-luna` to control prototype cost. Model choice should be evaluated before production.

## Important before a real industrial pilot
This is an MVP, not safety-certified operational software. Before using confidential or safety-critical company content, add professional security review, enterprise identity/SSO, tenant isolation testing, encryption/key management, retention policy, audit logging, backups, document version approval, malware scanning, monitoring, legal terms, privacy policy, evaluation sets, red-team testing, incident response, and human oversight.

JAXN Assist should make approved knowledge easier to retrieve; it should not replace required procedures, qualified personnel, permits, lockout/tagout, or safety systems.
