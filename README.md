# CoinDCX Telegram Trading Bot

This repository contains a Telegram bot to:
- create one-shot buy/sell limit orders,
- run continuous buy/sell sessions (follow market by editing limit orders),
- calculate quick profit estimates from public orderbook,
- manage user sessions via /status, /stop, /stopall.

Security: API keys must be provided via environment variables or `.env` file. Do NOT commit secrets.

Quick start (local)
1. Create a folder and place files from this repo.
2. Create `.env` from `.env.example` and fill values:
   - TELEGRAM_TOKEN
   - COINDCX_API_KEY
   - COINDCX_API_SECRET
3. Create a Python virtualenv and install dependencies:
   - python3 -m venv venv
   - source venv/bin/activate
   - pip install -r requirements.txt
4. Run:
   - python coin_dc_bot.py
5. Open Telegram, start a chat with your bot, use commands:
   - /buy, /sell, /profit, /status, /stop <id>, /stopall

Quick start (Docker)
1. Build:
   - docker build -t coin-dcx-bot .
2. Run (using .env file):
   - docker run --env-file .env --restart unless-stopped coin-dcx-bot

GitHub
- Create a new repository on GitHub (private recommended).
- Push these files to the repo and enable GitHub Actions secrets if you use CI.

Safety notes
- If your API keys were exposed earlier, rotate them immediately.
- Test with very small INR amounts first.
- Use a private repo and add `.env` to `.gitignore`.