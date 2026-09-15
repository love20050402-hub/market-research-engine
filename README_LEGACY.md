# Market Research Engine

Market Research Engine is a local, read-only command-line tool for personal,
non-commercial market research.

It analyzes a small number of public Hacker News and Reddit posts to identify
recurring pain points, requests for solutions, and manual workarounds. The
expected Reddit volume is about 50–100 public posts per analysis run. The
current configuration limits a run to 75 Reddit records.

The tool does not:

- post, comment, vote, or send messages on Reddit;
- modify Reddit content or user accounts;
- profile individuals;
- access private messages or private communities;
- train AI models using Reddit data;
- sell or redistribute Reddit data.

## Pipeline

```text
Reddit/HN public signals
→ normalization
→ pain qualification
→ clustering
→ evidence-based scoring
→ market opportunity report
```

Semantic analysis runs locally through Ollama. Deterministic Python code
performs evidence validation and scoring. Reddit access uses Reddit's official
OAuth Data API and only read-only search requests.

## Requirements

- Windows, macOS, or Linux
- Python 3.11 or newer
- A local Ollama installation
- An Ollama model configured in `config.yaml`
- Reddit Data API approval and OAuth credentials for Reddit collection

No paid AI API is required.

## Setup

Create and activate a Python virtual environment if desired, then install the
project requirements:

```powershell
python -m pip install -r requirements.txt
```

Install Ollama and download the model named in `config.yaml`:

```powershell
ollama pull qwen3:8b
```

Copy the environment template:

```powershell
Copy-Item .env.example .env
```

Enter the approved Reddit OAuth values in the local `.env` file:

```dotenv
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=
```

Use a descriptive User-Agent in Reddit's documented format, including the
platform, application name, version, and Reddit account name. Never commit the
`.env` file. It is excluded by `.gitignore`.

## Run

```powershell
python run.py
```

Generated research artifacts are written to `output/`. That directory is
excluded from Git because it may contain copied public post text, author names,
URLs, and research results.

## Reddit API behavior

The Reddit collector is implemented in `collectors/reddit.py`. It:

- requests OAuth using locally supplied credentials;
- sends authenticated `GET /search` requests;
- retrieves public submissions only;
- skips deleted, removed, and over-18 submissions;
- applies a small per-run record limit and a delay between queries;
- does not contain endpoints for posting, commenting, voting, messaging, or
  account actions.

Reddit credentials are read from environment variables. No credential is
stored in source code, configuration, test output, or generated reports.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Tests use deterministic fixtures and fake HTTP responses. They do not contact
Reddit, Hacker News, Ollama, Gemini, OpenAI, or any paid API.
