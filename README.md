# sportsbet-scrapper

Shows the prices for every runner in a Sportsbet race.

## Web app

```bash
python3 -m venv .venv            # first time only
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:5050 to see every race for the day (filter by horses,
greyhounds, harness and region; step through days with ‹ ›). Click a race to
see its prices, or paste a race link, e.g.
`https://www.sportsbet.com.au/horse-racing/australia-nz/mornington/race-2-11139920`.
The **⌂ Home** button on a race page goes back to the race list.

### Betfair back/lay prices (optional)

Put your Betfair details in a `.env` file in the project folder (it is git-ignored):

```
BETFAIR_APP_KEY=your-app-key
BETFAIR_USERNAME=your-betfair-username
BETFAIR_PASSWORD=your-betfair-password
```

Restart the app. Each race gets Back (blue) and Lay (pink) columns from the
matching Betfair Exchange WIN market, found by venue and start time.

Resulted races show the Betfair SP (BSP). Betfair stops listing a market once
it closes, so while the app is running it saves the day's racing markets
(all countries Betfair covers) to
`data/betfair_markets.json` every 10 minutes. Keep the app running on race day
to get BSP for races after they finish.

## Command line

```bash
python3 sportsbet_prices.py "<race link>"           # print a table
python3 sportsbet_prices.py "<race link>" --csv out.csv
```

The command-line version only needs the Python standard library.
