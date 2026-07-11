# CineMatch+

CineMatch+ is a Flask and vanilla JavaScript movie-discovery app. It uses a
local cosine-similarity model for recommendations, TMDB for current movie
metadata, and MongoDB Atlas for accounts and personal libraries.

## Requirements

- Python 3.11 (the deployment version is pinned in `.python-version`)
- A MongoDB Atlas connection string for authentication and saved lists
- A TMDB API key for discovery, autocomplete, posters, trailers, and details

The app remains usable if TMDB is unavailable: discovery lists become empty
and local recommendations still work. Authentication-dependent features return
a clear `503` response if MongoDB is unavailable.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Open `http://localhost:5001`.

For local HTTP development, set `SESSION_COOKIE_SECURE=false`. Do not set
`FLASK_DEBUG=true` in a deployed environment.

## Environment variables

Copy `.env.example` and configure these values:

```dotenv
# Required in production for signed login sessions.
SESSION_SECRET_KEY=use-a-long-random-value

# Required for login, signup, favorites, watchlist, ratings, and onboarding.
MONGO_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/MovieRS?retryWrites=true&w=majority

# Required for TMDB-backed discovery and movie details.
TMDB_API_KEY=<tmdb-api-key>

# true on HTTPS deployments such as Vercel; false for local HTTP.
SESSION_COOKIE_SECURE=true

# Optional: comma-separated allowed browser origins when the frontend is hosted
# separately from this Flask application.
FRONTEND_URL=https://your-frontend.example

# Optional local debugging only. Never enable in production.
FLASK_DEBUG=false
```

## Recommendation data

`movies.csv` contains the movie catalog. `similarity.npy` is the production
float32 similarity matrix and is loaded with NumPy memory mapping. The original
`similarity.csv` is retained only as a source artifact and excluded from Vercel
function bundles; do not delete `similarity.npy`.

If the model notebook is regenerated, recreate the binary matrix before
deployment:

```bash
python - <<'PY'
from pathlib import Path
import numpy as np

source, target, rows = Path('similarity.csv'), Path('similarity.npy'), 4806
matrix = np.lib.format.open_memmap(target, mode='w+', dtype=np.float32, shape=(rows, rows))
with source.open(encoding='utf-8') as file:
    for index, line in enumerate(file):
        values = np.fromstring(line, sep=',', dtype=np.float32)
        if values.size != rows:
            raise ValueError(f'Row {index + 1} has {values.size} values; expected {rows}.')
        matrix[index] = values
matrix.flush()
PY
```

## Deploy to Vercel

`app.py` is the Vercel Flask entry point. Static files live in `public/` so
Vercel serves them from its CDN; `vercel.json` rewrites application requests to
the Flask app and excludes local-only artifacts from the function bundle.

1. Import this repository into Vercel or run `vercel` from the repository root.
2. In **Project Settings → Environment Variables**, add every production value
   listed above (except `FLASK_DEBUG`).
3. Deploy with `vercel --prod`.

Before deployment, run:

```bash
python -m py_compile app.py main.py tmdb_service.py
node --check public/script.js
```

## API summary

- `POST /recommend` — local or hybrid movie recommendations
- `GET /api/discovery/featured`, `/popular`, `/search?q=` — TMDB discovery
- `GET /api/movie/<id>` — TMDB movie details
- `POST /signup`, `POST /login`, `POST /logout`, `GET /me` — session auth
- `GET|POST|PUT|DELETE /api/library/...` — authenticated personal library
- `GET|POST /api/onboarding` and `GET /api/dashboard` — profile preferences

API failures return JSON with `error` and `message`; framework-level API
failures also include `success: false`.
