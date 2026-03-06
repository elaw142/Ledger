# Ledger

A self-hosted web tool for importing and categorising ANZ bank transactions into [Actual Budget](https://actualbudget.com).

## Features

- Upload one or more ANZ CSV exports at once
- AI-powered transaction categorisation via [Ollama](https://ollama.com) (llama3.1:8b)
- Rule-based overrides for known merchants — learned over time
- Multi-profile support for different users or accounts
- Live streaming progress feed during categorisation
- Merchant review step to correct and save AI suggestions
- Direct push to Actual Budget on completion

## Stack

- **Backend**: Python / Flask
- **AI**: Ollama (local LLM, llama3.1:8b)
- **Budget**: Actual Budget API
- **Service**: systemd

## Setup

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) running locally with `llama3.1:8b` pulled
- [Actual Budget](https://actualbudget.com) instance running
- Node.js (for the Actual Budget import script)

### Running locally

```bash
git clone https://github.com/elaw142/Ledger.git
cd Ledger
pip install -r requirements.txt
python3 app.py
```

The app runs on port `5007` by default.

### Running as a service

```bash
sudo systemctl enable finance-importer
sudo systemctl start finance-importer
```

### Caddy (reverse proxy)

```
import.budget.yourdomain.com {
    reverse_proxy localhost:5007
}
```

## Profile Setup

On first run, create a profile with:
- Your name
- Actual Budget server URL (internal)
- Actual Budget public URL (shown after import)
- Actual Budget password
- Budget Sync ID (found in Settings → Advanced → Sync ID)
- Account names (must match exactly in Actual Budget)

## How It Works

1. Select your profile and upload ANZ CSV exports
2. Each transaction is checked against your saved merchant corrections first
3. Unknown merchants are sent to Ollama for categorisation
4. Review and correct new merchants — corrections are saved for next time
5. Push categorised transactions directly into Actual Budget

## Deployment

Pushes to `main` automatically deploy via GitHub Actions. The workflow SSHs into the server, pulls the latest code, and restarts the systemd service.
