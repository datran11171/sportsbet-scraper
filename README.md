# sportsbet-scrapper

Shows the prices for every runner in a Sportsbet race.

## Web app

```bash
python3 -m venv .venv            # first time only
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:5050 and paste a race link, e.g.
`https://www.sportsbet.com.au/horse-racing/australia-nz/mornington/race-2-11139920`.

## Command line

```bash
python3 sportsbet_prices.py "<race link>"           # print a table
python3 sportsbet_prices.py "<race link>" --csv out.csv
```

The command-line version only needs the Python standard library.
