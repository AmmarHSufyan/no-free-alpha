# Freqtrade benchmark (dry-run / backtest only)

This folder runs [Freqtrade](https://www.freqtrade.io) in Docker as a **benchmark** for the custom
engine in this repository. It is **paper trading and backtesting only**:

- every config has `"dry_run": true`, and the exchange `key`/`secret` are empty strings
- never add exchange API keys here, and never set `dry_run` to `false`
- third-party strategy code (`vendor/`, `user_data/strategies/`) only ever runs **inside the container**

Results are in [RESULTS.md](RESULTS.md).

The community strategies are GPL-3.0 licensed and are not redistributed here. Fetch them at the tested
commit with `./scripts/fetch_strategies.sh` (copies the 10 strategies into `user_data/strategies/`; the
built-in `SampleStrategy` comes with `freqtrade create-userdir`).

## Layout

| Path | What |
|---|---|
| `docker-compose.yml` | Bot services and a `freqtrade` tools service for one-off commands |
| `.env` (git-ignored) | Random FreqUI/API credentials (`FT_API_USERNAME`, `FT_API_PASSWORD`, ...). See `.env.example` |
| `user_data/config.json` | Shared base config: Binance spot, USDT, 5 static pairs, dry-run wallet 10,000 |
| `user_data/config.bot-*.json` | Per-bot overlay: strategy, bot name, DB file, API server |
| `user_data/strategies/` | Strategies fetched by `scripts/fetch_strategies.sh`, plus the built-in `sample_strategy.py` (not committed) |
| `user_data/data/binance/` | Downloaded candles (feather, git-ignored) |
| `user_data/backtest_results/{full,recent6m}/` | Backtest exports and logs (git-ignored) |
| `user_data/lookahead/` | Lookahead-analysis CSVs and logs (copies in `../results/freqtrade/`) |
| `scripts/run_backtests.sh` | Backtests every strategy over both periods |
| `scripts/summarize_backtests.py` | Builds the markdown result tables (runs in the container) |
| `scripts/fetch_strategies.sh` | Clones freqtrade-strategies at commit `f3340ce` and copies the tested strategies |
| `vendor/freqtrade-strategies/` | Shallow clone of github.com/freqtrade/freqtrade-strategies (git-ignored) |
| `user_data/db/` | Dry-run trade databases (`*.dryrun.sqlite`, git-ignored) |

## Running bots

| Container | Strategy | FreqUI / API | DB file |
|---|---|---|---|
| `ft-bbandrsi` | BbandRsi (1h) | http://127.0.0.1:8080 | `user_data/db/bbandrsi.dryrun.sqlite` |
| `ft-combinedbinhandcluc` | CombinedBinHAndCluc (5m) | http://127.0.0.1:8081 | `user_data/db/combinedbinhandcluc.dryrun.sqlite` |

Both ports are bound to `127.0.0.1` only, so nothing is reachable from your LAN.

All commands below are run from this folder, in a POSIX shell (on Windows: Git Bash).
Docker Desktop must be running. It does not auto-start unless you enable "Start Docker Desktop when
you sign in". The bots use `restart: unless-stopped`, so they come back on their own once Docker is up.

### Start / stop / status

```bash
docker compose up -d                          # start both bots
docker compose ps                             # status; STATUS should say "(healthy)"
docker compose stop                           # stop bots (keeps containers and DB)
docker compose start                          # start them again
docker compose restart ft-bbandrsi            # restart one bot (e.g. after editing its config/strategy)
docker compose down                           # remove containers (DB files in user_data/db are kept)
```

The dry-run trade history lives in `user_data/db/`. Delete those files only if you want to start over.

### Logs

```bash
docker compose logs -f --tail 100 ft-bbandrsi
docker compose logs -f --tail 100 ft-combinedbinhandcluc
```

File logs are also written to `user_data/logs/ft-*.log`.

### FreqUI

1. Open http://127.0.0.1:8080 (BbandRsi) or http://127.0.0.1:8081 (CombinedBinHAndCluc).
2. Log in with API URL `http://127.0.0.1:8080` (or `:8081`), with the username `FT_API_USERNAME` and
   password `FT_API_PASSWORD` from `.env`.
   Both bots share the same credentials. FreqUI can hold both bots at once; add the second one under "Add new bot".

Quick API check from Git Bash:

```bash
set -a; . ./.env; set +a
curl -s -u "$FT_API_USERNAME:$FT_API_PASSWORD" -X POST http://127.0.0.1:8080/api/v1/token/login
```

### Inspect the dry-run DB

```bash
docker compose exec ft-bbandrsi freqtrade show-trades -c /freqtrade/user_data/config.json -c /freqtrade/user_data/config.bot-bbandrsi.json
```

Always pass the bot's two `-c` configs when you `exec` into a bot container. The bot containers carry the
`FREQTRADE__API_SERVER__*` env vars, and without the configs those vars form an incomplete `api_server`
section, so config validation fails.

(In Git Bash, prefix commands that contain absolute container paths with `MSYS_NO_PATHCONV=1`, or Git Bash
will rewrite `/freqtrade/...` into a Windows path.)

## Backtesting

Data download (all 5 pairs, 5m + 1h, from 2024-09-20 so the 1h strategies have warm-up candles before 2024-10-01):

```bash
docker compose run --rm freqtrade download-data --config user_data/config.json --timerange 20240920- --timeframes 5m 1h
```

Re-running the same command later appends new candles.

Re-run every backtest (Git Bash), then rebuild the tables:

```bash
./scripts/run_backtests.sh                                     # full (20241001-) + recent6m (20260426-)
RECENT_RANGE=20260501- PERIODS=recent6m ./scripts/run_backtests.sh   # move the recent window
STRATEGIES="BbandRsi Supertrend" ./scripts/run_backtests.sh          # subset
MSYS_NO_PATHCONV=1 docker compose run --rm -T --entrypoint python freqtrade /freqtrade/scripts/summarize_backtests.py
```

A single backtest (PowerShell or Git Bash):

```bash
docker compose run --rm freqtrade backtesting --config user_data/config.json --strategy BbandRsi --timerange 20260426- --fee 0.001 --cache none --breakdown month
```

Lookahead-bias check:

```bash
docker compose run --rm freqtrade lookahead-analysis --config user_data/config.json --strategy BbandRsi --timerange 20241001- --fee 0.001
```

Hyperopt was intentionally **not** run. If you try it, cap it (e.g. `--epochs 100`) and keep the
hyperopted period separate from the period you evaluate on.

## Switching which strategy a bot runs

1. Copy a `user_data/config.bot-*.json`, then change `bot_name`, `strategy` and the `db_url` file name (one DB per strategy).
2. Add or adjust a service in `docker-compose.yml`: new `container_name`, the next free `127.0.0.1:808x:8080` port, and point `--config` at the new file.
3. Run `docker compose up -d`.

## First-time setup (if recreating from scratch)

```bash
./scripts/fetch_strategies.sh
# create .env with random secrets (Git Bash):
{ echo FT_API_USERNAME=freqtrader
  echo FT_API_PASSWORD=$(openssl rand -base64 33 | tr -dc 'A-Za-z0-9' | head -c 24)
  echo FT_JWT_SECRET_KEY=$(openssl rand -hex 32)
  echo FT_WS_TOKEN=$(openssl rand -hex 24); } > .env
docker compose pull
docker compose run --rm freqtrade create-userdir --userdir user_data   # only if user_data/ is missing
```

## Known quirks

- **Clock skew:** if the host clock drifts, the 5m bot logs occasional harmless `Candle date > last refresh`
  warnings. Keep the system clock synced.
- `FutureWarning: 'freqtrade.vendor.qtpylib.indicators' is deprecated` comes from the older community
  strategies. It is harmless for now, but those imports will need changing to `from technical import qtpylib`
  when Freqtrade removes the shim.
- The image tag `stable` moves. Results in RESULTS.md were produced with **freqtrade 2026.9**.
  Run `docker compose pull` to upgrade, and re-run the backtests after upgrading.
