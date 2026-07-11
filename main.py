from flask import Flask, render_template, jsonify, request, session
from werkzeug.local import LocalProxy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_pymongo import PyMongo
from flask_cors import CORS
import os
import gdown
import re
import random
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from threading import Lock
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import logging
from tmdb_service import get_tmdb_service

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def normalize_movie_title(title):
    """Normalize titles for resilient local-model matching."""
    normalized = re.sub(r'[^a-z0-9]+', ' ', str(title or '').lower()).strip()
    return re.sub(r'^the ', '', normalized)


# Initialize Flask app
app = Flask(__name__, static_folder='public', static_url_path='')

# The browser is normally served by this same Flask application.  Cross-origin
# requests are only needed for an explicitly configured frontend URL.
frontend_origins = [origin.strip() for origin in os.getenv('FRONTEND_URL', '').split(',') if origin.strip()]
if frontend_origins:
    CORS(app, resources={r'/api/*': {'origins': frontend_origins}}, supports_credentials=True)

# Configuration
app.config['DEBUG'] = os.getenv('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes'}
app.config['SECRET_KEY'] = os.getenv('SESSION_SECRET_KEY') or os.getenv('SECRET_KEY')
if not app.config['SECRET_KEY']:
    if app.config['DEBUG']:
        app.config['SECRET_KEY'] = 'local-development-only-change-me'
        logger.warning('Using an insecure local development session secret.')
    else:
        raise RuntimeError('SESSION_SECRET_KEY must be configured outside development.')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', '').lower() in {'1', 'true', 'yes'}
app.config['MONGO_URI'] = os.getenv('MONGO_URI')
mongo = None


def json_error(message, status, error=None):
    """Return the stable failure shape used by API clients."""
    return jsonify({'success': False, 'error': error or message, 'message': message}), status


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
def handle_http_error(error):
    if request.path.startswith(('/api/', '/recommend', '/signup', '/login', '/logout', '/me')):
        return json_error(error.description, error.code)
    return error


@app.errorhandler(500)
def handle_internal_error(error):
    logger.exception('Unhandled application error: %s', error)
    if request.path.startswith(('/api/', '/recommend', '/signup', '/login', '/logout', '/me')):
        return json_error('An unexpected server error occurred.', 500, 'Internal Server Error')
    return 'Internal Server Error', 500


def get_mongo_db():
    """Initialize Mongo only when the Mongo fallback is actually used."""
    global mongo
    if mongo is None:
        mongo = PyMongo(app)
    return mongo.db

# Use LocalProxy to read the global db instance with just `db`
client = LocalProxy(get_mongo_db)

# ============================================================================
# IN-MEMORY DATA STORAGE FOR RECOMMENDATION ENGINE
# ============================================================================
class RecommendationEngine:
    """Handles in-memory storage and retrieval of movie and similarity data."""
    
    def __init__(self):
        self.movies_df = None
        self.similarity_matrix = None
        self.movie_title_index = None
        self.is_loaded = False
        self.load_error = None
    
    def load_data(self):
        """Load movies.csv and the deployment-friendly similarity.npy at startup."""
        try:
            # Define file paths
            current_dir = os.path.dirname(os.path.abspath(__file__))
            movies_path = os.path.join(current_dir, 'movies.csv')
            similarity_path = os.path.join(current_dir, 'similarity.npy')
            
            # Check if files exist
            # Check if files exist
            if not os.path.exists(movies_path):
             raise FileNotFoundError(f"movies.csv not found at {movies_path}")

            if not os.path.exists(similarity_path):
             logger.info("similarity.npy not found. Downloading from Google Drive...")

             similarity_url = os.getenv("SIMILARITY_URL")

             if not similarity_url:
              raise RuntimeError("SIMILARITY_URL environment variable is not set.")

             gdown.download(
              url=similarity_url,
              output=similarity_path,
              quiet=False
             )

             if not os.path.exists(similarity_path):
              raise FileNotFoundError("Failed to download similarity.npy")
            # Load movies data
            logger.info("Loading movies.csv into memory...")
            self.movies_df = pd.read_csv(movies_path,
    header=None,
    names=["movie_id", "title", "tags"])
            logger.info(f"Loaded {len(self.movies_df)} movies")
            
            # Load similarity matrix
            logger.info("Loading similarity.npy into memory...")
            self.similarity_matrix = np.load(similarity_path, mmap_mode='r')
            if self.similarity_matrix.dtype != np.float32:
                self.similarity_matrix = self.similarity_matrix.astype(np.float32)
            if self.similarity_matrix.ndim != 2 or self.similarity_matrix.shape != (len(self.movies_df), len(self.movies_df)):
                raise ValueError(
                    f"Similarity matrix shape {self.similarity_matrix.shape} does not match "
                    f"{len(self.movies_df)} movies."
                )
            logger.info(f"Loaded similarity matrix with shape {self.similarity_matrix.shape}")
            
            # Create title-to-index mapping for O(1) lookups
            self.movie_title_index = {
                normalize_movie_title(title): idx for idx, title in enumerate(self.movies_df['title'])
            }
            logger.info("Created title-to-index mapping")
            
            self.is_loaded = True
            logger.info("Recommendation engine initialized successfully")
            
        except FileNotFoundError as e:
            self.load_error = str(e)
            logger.error(f"FileNotFoundError: {e}")
            logger.warning("Recommendation system will not be available until CSV files are present")
        except Exception as e:
            self.load_error = str(e)
            logger.error(f"Error loading recommendation data: {e}")
    
    def get_movie_index(self, movie_title):
        """Get the index of a movie by its title."""
        if not self.is_loaded:
            raise RuntimeError("Recommendation engine not initialized. CSV files may be missing.")
        
        # Case-insensitive search
        normalized_title = normalize_movie_title(movie_title)
        if normalized_title in self.movie_title_index:
            return self.movie_title_index[normalized_title]
        
        raise ValueError(f"Movie '{movie_title}' not found in database")
    
    def get_movie_details(self, index):
        """Get movie details by index."""
        if not self.is_loaded:
            raise RuntimeError("Recommendation engine not initialized.")
        
        if index < 0 or index >= len(self.movies_df):
            raise ValueError(f"Invalid movie index: {index}")
        
        return self.movies_df.iloc[index]
    
    def get_similarity_scores(self, movie_index):
        """Get similarity scores for a movie by index."""
        if not self.is_loaded:
            raise RuntimeError("Recommendation engine not initialized.")
        
        if movie_index < 0 or movie_index >= len(self.similarity_matrix):
            raise ValueError(f"Invalid movie index: {movie_index}")
        
        return self.similarity_matrix[movie_index]

# Initialize the recommendation engine
recommendation_engine = RecommendationEngine()

# Load data when the app starts
with app.app_context():
    recommendation_engine.load_data()


def get_movie_details_service():
    """Lazy-load TMDB only for movie detail endpoints."""
    try:
        service = get_tmdb_service()
        if service:
            return service
        logger.warning("TMDB Service is not available")
    except Exception as e:
        logger.warning(f"TMDB Service could not be initialized: {e}")
    return None


# ============================================================================
# AUTHENTICATION
# ============================================================================
EMAIL_PATTERN = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def serialize_user(user):
    """Return only the account fields that are safe to expose to the browser."""
    return {
        'id': str(user['_id']),
        'username': user.get('username', ''),
        'email': user.get('email', ''),
        'created_at': user.get('created_at').isoformat() if user.get('created_at') else None,
        'onboarding_completed': user.get('onboarding_completed', False),
        'favorites': user.get('favorites', []),
        'watchlist': user.get('watchlist', []),
        'ratings': user.get('ratings', []),
        'favorite_genres': user.get('favorite_genres', []),
        'favorite_actors': user.get('favorite_actors', []),
        'favorite_directors': user.get('favorite_directors', []),
        'watch_history': user.get('watch_history', []),
        'preference_profile': user.get('preference_profile'),
    }


def get_request_json():
    """Return a JSON object or a consistent validation response."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, json_error('A JSON object is required.', 400, 'Invalid request')
    return data, None


def current_authenticated_user():
    """Look up the current session user and clear invalid/stale sessions."""
    user_id = session.get('user_id')
    if not user_id:
        return None

    try:
        from bson import ObjectId
        user = client.users.find_one({'_id': ObjectId(user_id)})
    except Exception as error:
        logger.warning('Unable to resolve authenticated user: %s', error)
        user = None

    if user is None:
        session.clear()
    return user


# ==========================================================================
# PERSONAL LIBRARY AND DISCOVERY APIs
# ==========================================================================
LIBRARY_COLLECTIONS = {'favorites', 'watchlist', 'ratings', 'watch_history'}


def serialize_library_item(item):
    """Serialize a stored library item without leaking arbitrary client data."""
    serialized = dict(item)
    timestamp = serialized.get('updated_at') or serialized.get('viewed_at')
    if isinstance(timestamp, datetime):
        serialized['updated_at'] = timestamp.isoformat()
        serialized['viewed_at'] = timestamp.isoformat()
    return serialized


def normalize_library_movie(data):
    """Keep user library records compact, predictable, and safe to display."""
    movie_id = data.get('movie_id')
    if not isinstance(movie_id, int) or isinstance(movie_id, bool) or movie_id <= 0:
        return None
    return {
        'movie_id': movie_id,
        'title': str(data.get('title') or '').strip()[:300],
        'poster': str(data.get('poster') or data.get('poster_url') or '')[:1000],
        'backdrop': str(data.get('backdrop') or '')[:1000],
        'overview': str(data.get('overview') or '')[:3000],
        'release_date': str(data.get('release_date') or '')[:20],
        'rating': data.get('rating') if isinstance(data.get('rating'), (int, float)) else None,
    }


def update_user_library(user, collection, movie, remove=False, rating=None):
    """Update one collection while de-duplicating by TMDB movie ID."""
    items = [item for item in user.get(collection, []) if item.get('movie_id') != movie['movie_id']]
    if not remove:
        movie['updated_at'] = datetime.now(timezone.utc)
        if rating is not None:
            movie['rating'] = rating
        items.insert(0, movie)
    profile = dict(user.get('preference_profile') or {})
    interactions = list(profile.get('recent_interactions') or [])
    interactions.insert(0, {
        'type': collection,
        'movie_id': movie['movie_id'],
        'title': movie.get('title', ''),
        'occurred_at': datetime.now(timezone.utc),
    })
    profile['recent_interactions'] = interactions[:50]
    profile['last_updated_at'] = datetime.now(timezone.utc)
    try:
        client.users.update_one(
            {'_id': user['_id']},
            {'$set': {collection: items, 'preference_profile': profile}},
        )
    except Exception:
        logger.exception('Unable to update %s', collection)
        return None
    return [serialize_library_item(item) for item in items]


def user_dashboard(user):
    """Build dashboard data from the account document without extra storage."""
    ratings = user.get('ratings', [])
    distribution = {str(star): 0 for star in range(1, 6)}
    for item in ratings:
        rating = item.get('rating')
        if isinstance(rating, int) and 1 <= rating <= 5:
            distribution[str(rating)] += 1
    return {
        'counts': {
            'favorites': len(user.get('favorites', [])),
            'watchlist': len(user.get('watchlist', [])),
            'ratings': len(ratings),
            'history': len(user.get('watch_history', [])),
        },
        'preferences': {
            'genres': user.get('favorite_genres', []),
            'actors': user.get('favorite_actors', []),
            'directors': user.get('favorite_directors', []),
            'rating_distribution': distribution,
        },
        'recent_activity': [serialize_library_item(item) for item in user.get('watch_history', [])[:8]],
        'recommendation_statistics': {
            'recent_interactions': len((user.get('preference_profile') or {}).get('recent_interactions', [])),
            'onboarding_completed': user.get('onboarding_completed', False),
        },
    }


@app.route('/api/library/<collection>', methods=['GET'])
def get_library(collection):
    if collection not in LIBRARY_COLLECTIONS:
        return jsonify({'error': 'Unknown library collection'}), 404
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    return jsonify({collection: [serialize_library_item(item) for item in user.get(collection, [])]}), 200


@app.route('/api/library/<collection>/<int:movie_id>', methods=['POST', 'PUT', 'DELETE'])
def mutate_library(collection, movie_id):
    if collection not in LIBRARY_COLLECTIONS:
        return jsonify({'error': 'Unknown library collection'}), 404
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401

    if request.method == 'DELETE':
        items = [item for item in user.get(collection, []) if item.get('movie_id') != movie_id]
        try:
            client.users.update_one({'_id': user['_id']}, {'$set': {collection: items}})
        except Exception:
            logger.exception('Unable to remove %s item', collection)
            return jsonify({'error': 'Unable to update library'}), 503
        return jsonify({collection: [serialize_library_item(item) for item in items]}), 200

    data, error_response = get_request_json()
    if error_response:
        return error_response
    data['movie_id'] = movie_id
    movie = normalize_library_movie(data)
    if not movie:
        return jsonify({'error': 'Invalid movie'}), 400

    rating = data.get('user_rating')
    if collection == 'ratings':
        if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
            return jsonify({'error': 'user_rating must be an integer from 1 to 5'}), 400
    elif collection == 'watch_history':
        movie['viewed_at'] = datetime.now(timezone.utc)

    items = update_user_library(user, collection, movie, rating=rating)
    if items is None:
        return jsonify({'error': 'Unable to update library'}), 503
    return jsonify({collection: items}), 200


@app.route('/api/library/watchlist/<int:movie_id>/watched', methods=['POST'])
def mark_watchlist_item_watched(movie_id):
    """Move a title from Watchlist to history in one authenticated action."""
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    item = next((entry for entry in user.get('watchlist', []) if entry.get('movie_id') == movie_id), None)
    if not item:
        return jsonify({'error': 'Movie is not in the watchlist'}), 404
    watchlist = [entry for entry in user.get('watchlist', []) if entry.get('movie_id') != movie_id]
    history = [entry for entry in user.get('watch_history', []) if entry.get('movie_id') != movie_id]
    item = dict(item)
    item['viewed_at'] = datetime.now(timezone.utc)
    item['updated_at'] = item['viewed_at']
    history.insert(0, item)
    try:
        client.users.update_one({'_id': user['_id']}, {'$set': {'watchlist': watchlist, 'watch_history': history}})
    except Exception:
        logger.exception('Unable to mark watchlist movie as watched')
        return jsonify({'error': 'Unable to update library'}), 503
    return jsonify({
        'watchlist': [serialize_library_item(entry) for entry in watchlist],
        'watch_history': [serialize_library_item(entry) for entry in history],
    }), 200


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    return jsonify(user_dashboard(user)), 200


@app.route('/api/history', methods=['GET'])
def get_history():
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    return jsonify({'history': [serialize_library_item(item) for item in user.get('watch_history', [])]}), 200


@app.route('/api/profile', methods=['GET'])
def get_profile():
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    return jsonify({'user': serialize_user(user), 'dashboard': user_dashboard(user)}), 200


@app.route('/api/discovery/featured', methods=['GET'])
def get_featured_movie():
    """Serve a cached TMDB-popular title for the discovery hero."""
    service = get_movie_details_service()
    if not service:
        return jsonify({'movie': None}), 200
    try:
        movies = service.get_popular_movies(limit=20)
        movie = random.choice(movies) if movies else None
        if movie and movie.get('movie_id'):
            details = service.get_movie_details(movie['movie_id'])
            if details:
                movie.update({
                    'runtime': details.get('runtime'),
                    'genres': details.get('genres', []),
                    'trailer': details.get('trailer_url'),
                    'backdrop_url': details.get('backdrop_url') or movie.get('backdrop_url'),
                })
        return jsonify({'movie': movie}), 200
    except Exception:
        logger.exception('Unable to load featured movie')
        return jsonify({'movie': None}), 200


@app.route('/api/discovery/popular', methods=['GET'])
def get_popular_discovery_movies():
    """Return a compact, cached popular rail for the discovery homepage."""
    service = get_movie_details_service()
    if not service:
        return jsonify({'movies': []}), 200
    try:
        return jsonify({'movies': service.get_popular_movies(limit=20)}), 200
    except Exception:
        logger.exception('Unable to load popular discovery movies')
        return jsonify({'movies': []}), 200


@app.route('/api/discovery/search', methods=['GET'])
def discovery_search():
    """Small TMDB-backed autocomplete endpoint used only by the search UI."""
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify({'results': []}), 200
    service = get_movie_details_service()
    if not service:
        return jsonify({'results': []}), 200
    try:
        return jsonify({'results': service.search_movie(query)[:5]}), 200
    except Exception:
        logger.exception('Unable to autocomplete movie search')
        return jsonify({'results': []}), 200


@app.route('/signup', methods=['POST'])
def signup():
    data, error_response = get_request_json()
    if error_response:
        return error_response

    username = str(data.get('username', '')).strip()
    email = str(data.get('email', '')).strip().lower()
    password = data.get('password', '')

    if not 2 <= len(username) <= 40:
        return jsonify({'error': 'Invalid username', 'message': 'Username must be between 2 and 40 characters.'}), 400
    if not EMAIL_PATTERN.fullmatch(email):
        return jsonify({'error': 'Invalid email', 'message': 'Enter a valid email address.'}), 400
    if not isinstance(password, str) or len(password) < 8:
        return jsonify({'error': 'Invalid password', 'message': 'Password must be at least 8 characters long.'}), 400

    try:
        users = client.users
        users.create_index('email', unique=True)
        user = {
            'username': username,
            'email': email,
            'password_hash': generate_password_hash(password),
            'created_at': datetime.now(timezone.utc),
            'onboarding_completed': False,
            'favorites': [],
            'watchlist': [],
            'ratings': [],
            'favorite_genres': [],
            'favorite_actors': [],
            'favorite_directors': [],
            'watch_history': [],
            'preference_profile': None,
        }
        result = users.insert_one(user)
        user['_id'] = result.inserted_id
    except Exception as exc:
        # DuplicateKeyError is intentionally matched by code so this remains
        # compatible with both local and Atlas-backed PyMongo clients.
        if getattr(exc, 'code', None) == 11000:
            return jsonify({'error': 'Email already registered', 'message': 'An account with that email already exists.'}), 409
        logger.exception('Unable to create user account')
        return jsonify({'error': 'Registration unavailable', 'message': 'Please try again later.'}), 503

    session.clear()
    session['user_id'] = str(user['_id'])
    return jsonify({'message': 'Account created successfully.', 'user': serialize_user(user)}), 201


@app.route('/login', methods=['POST'])
def login():
    data, error_response = get_request_json()
    if error_response:
        return error_response

    email = str(data.get('email', '')).strip().lower()
    password = data.get('password', '')
    if not email or not isinstance(password, str):
        return jsonify({'error': 'Invalid credentials', 'message': 'Email and password are required.'}), 400

    try:
        user = client.users.find_one({'email': email})
    except Exception:
        logger.exception('Unable to retrieve user account')
        return jsonify({'error': 'Login unavailable', 'message': 'Please try again later.'}), 503

    if not user or not check_password_hash(user.get('password_hash', ''), password):
        return jsonify({'error': 'Invalid credentials', 'message': 'Incorrect email or password.'}), 401

    session.clear()
    session['user_id'] = str(user['_id'])
    return jsonify({'message': 'Logged in successfully.', 'user': serialize_user(user)}), 200


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Logged out successfully.'}), 200


@app.route('/me', methods=['GET'])
def me():
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required', 'message': 'Please log in to access your profile.'}), 401
    return jsonify({'user': serialize_user(user)}), 200


# ============================================================================
# ONBOARDING PREFERENCES
# ============================================================================
ONBOARDING_GENRES = [
    'Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Drama', 'Fantasy',
    'Horror', 'Mystery', 'Romance', 'Science Fiction', 'Thriller', 'War',
    'Western', 'Family'
]
ONBOARDING_DIRECTORS = [
    'Christopher Nolan', 'James Cameron', 'Steven Spielberg', 'Denis Villeneuve',
    'Martin Scorsese', 'Ridley Scott', 'David Fincher', 'Quentin Tarantino'
]

# Onboarding is shared in-process and refreshed at most once per day. A lock
# prevents concurrent first requests from triggering duplicate TMDB refreshes.
ONBOARDING_CACHE_TTL = timedelta(hours=24)
cached_onboarding_catalog = None
cached_timestamp = None
onboarding_cache_lock = Lock()


def empty_onboarding_catalog():
    """Return the stable catalog shape used when TMDB is unavailable."""
    return {'movies': [], 'actors': []}


def fetch_popular_movies():
    """Load up to 40 TMDB popular movies without per-movie searches."""
    service = get_movie_details_service()
    if not service:
        return []
    try:
        movies = service.get_popular_movies(limit=40)
        return [movie for movie in movies if movie.get('movie_id') and movie.get('title')]
    except Exception:
        logger.exception('Unable to fetch popular movies for onboarding')
        return []


def fetch_popular_people():
    """Load up to 40 TMDB popular people without per-person searches."""
    service = get_movie_details_service()
    if not service:
        return []
    try:
        people = service.get_popular_people(limit=40)
        return [person for person in people if person.get('id') and person.get('name')]
    except Exception:
        logger.exception('Unable to fetch popular people for onboarding')
        return []


def refresh_onboarding_cache():
    """Refresh the catalog, retaining the last known-good cache on failure."""
    global cached_onboarding_catalog, cached_timestamp

    with onboarding_cache_lock:
        logger.info('Refreshing onboarding cache...')
        movies = fetch_popular_movies()
        people = fetch_popular_people()
        # Do not replace a complete catalog with partial data during a
        # temporary TMDB outage on either endpoint.
        if not movies or not people:
            logger.warning('TMDB onboarding refresh failed; retaining the last cached catalog.')
            return cached_onboarding_catalog or empty_onboarding_catalog()

        catalog = {
            'movies': random.sample(movies, min(15, len(movies))),
            'actors': random.sample(people, min(12, len(people))),
        }
        cached_onboarding_catalog = catalog
        cached_timestamp = datetime.now(timezone.utc)
        logger.info('Loaded %d movies.', len(catalog['movies']))
        logger.info('Loaded %d actors.', len(catalog['actors']))
        return catalog


def get_cached_onboarding_catalog():
    """Return a fresh cache, refresh it when expired, or safely fall back."""
    if cached_onboarding_catalog and cached_timestamp:
        if datetime.now(timezone.utc) - cached_timestamp < ONBOARDING_CACHE_TTL:
            logger.info('Returning cached onboarding catalog.')
            return cached_onboarding_catalog
    return refresh_onboarding_cache()


def validate_selection(value, allowed_values, field_name, minimum=0, maximum=None):
    """Validate a unique list of predefined preference values."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None, f'{field_name} must be a list.'
    if len(value) != len(set(value)):
        return None, f'{field_name} cannot contain duplicate selections.'
    if len(value) < minimum:
        return None, f'Select at least {minimum} {field_name.lower()}.'
    if maximum is not None and len(value) > maximum:
        return None, f'Select no more than {maximum} {field_name.lower()}.'
    if any(item not in allowed_values for item in value):
        return None, f'{field_name} contains an unsupported selection.'
    return value, None


def validate_ratings(value):
    """Validate optional, unique 1-5 star ratings received from onboarding."""
    if value is None:
        return [], None
    if not isinstance(value, list) or len(value) > 15:
        return None, 'Ratings must be a list of up to 15 movies.'

    ratings = []
    movie_ids = set()
    for item in value:
        if not isinstance(item, dict):
            return None, 'Each rating must contain movie_id and rating.'
        movie_id, rating = item.get('movie_id'), item.get('rating')
        if not isinstance(movie_id, int) or isinstance(movie_id, bool) or movie_id <= 0:
            return None, 'Each rating needs a valid movie_id.'
        if movie_id in movie_ids:
            return None, 'Each movie can only be rated once.'
        if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
            return None, 'Ratings must be whole numbers from 1 to 5.'
        movie_ids.add(movie_id)
        ratings.append({'movie_id': movie_id, 'rating': rating})
    return ratings, None


@app.route('/api/onboarding', methods=['GET'])
def get_onboarding():
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required', 'message': 'Please log in to continue.'}), 401

    response = {
        'onboarding_completed': user.get('onboarding_completed', False),
        'user': serialize_user(user),
    }
    if not response['onboarding_completed']:
        response['catalog'] = get_cached_onboarding_catalog()
    return jsonify(response), 200


@app.route('/api/onboarding', methods=['POST'])
def complete_onboarding():
    user = current_authenticated_user()
    if not user:
        return jsonify({'error': 'Authentication required', 'message': 'Please log in to continue.'}), 401
    if user.get('onboarding_completed', False):
        return jsonify({'error': 'Onboarding already completed', 'message': 'Preferences can only be set during onboarding.'}), 409

    data, error_response = get_request_json()
    if error_response:
        return error_response

    genres, validation_error = validate_selection(
        data.get('favorite_genres'), ONBOARDING_GENRES, 'Genres', minimum=3, maximum=5
    )
    if validation_error:
        return jsonify({'error': 'Invalid preferences', 'message': validation_error}), 400
    actors, validation_error = validate_selection(
        data.get('favorite_actors', []),
        [actor['name'] for actor in get_cached_onboarding_catalog()['actors']],
        'Actors',
        maximum=5,
    )
    if validation_error:
        return jsonify({'error': 'Invalid preferences', 'message': validation_error}), 400
    directors, validation_error = validate_selection(
        data.get('favorite_directors', []), ONBOARDING_DIRECTORS, 'Directors', maximum=3
    )
    if validation_error:
        return jsonify({'error': 'Invalid preferences', 'message': validation_error}), 400
    ratings, validation_error = validate_ratings(data.get('ratings', []))
    if validation_error:
        return jsonify({'error': 'Invalid preferences', 'message': validation_error}), 400

    completed_at = datetime.now(timezone.utc)
    preference_profile = {
        'genres': {genre: 1.0 for genre in genres},
        'completed_at': completed_at,
    }
    updates = {
        'favorite_genres': genres,
        'favorite_actors': actors,
        'favorite_directors': directors,
        'ratings': ratings,
        'preference_profile': preference_profile,
        'onboarding_completed': True,
    }
    try:
        client.users.update_one({'_id': user['_id']}, {'$set': updates})
        user.update(updates)
    except Exception:
        logger.exception('Unable to save onboarding preferences')
        return jsonify({'error': 'Unable to save preferences', 'message': 'Please try again.'}), 503

    return jsonify({'message': 'Onboarding completed successfully.', 'user': serialize_user(user)}), 200


@app.route('/')
def home():
    return render_template('index.html')

# ============================================================================
# RECOMMENDATION FUNCTIONS
# ============================================================================
def recommend(movie, num_recommendations=8):
    """
    Get movie recommendations using in-memory similarity matrix.
    This path intentionally uses only movies.csv and similarity.npy.
    
    Args:
        movie (str): Movie title to get recommendations for
        num_recommendations (int): Number of recommendations to return
    
    Returns:
        list: List of dictionaries with movie_id, title, and similarity_score
    """
    try:
        # Get the index of the input movie
        movie_index = recommendation_engine.get_movie_index(movie)
        
        # Get similarity scores for this movie
        similarity_scores = recommendation_engine.get_similarity_scores(movie_index)
        
        # Find top similar movies (exclude the movie itself by starting from index 1)
        limit = min(num_recommendations + 1, len(similarity_scores))
        candidate_indices = np.argpartition(similarity_scores, -limit)[-limit:]
        distances = sorted(
            ((int(index), float(similarity_scores[index])) for index in candidate_indices),
            key=lambda item: item[1],
            reverse=True,
        )
        
        recom = []
        count = 0
        
        # Collect recommendations, skipping the first one (the movie itself)
        for idx, score in distances[1:]:
            if count >= num_recommendations:
                break
            
            try:
                movie_details = recommendation_engine.get_movie_details(idx)
                movie_id = movie_details.get('movie_id')
                movie_title = movie_details.get('title')
                
                recom_item = {
                    'movie_id': int(movie_id),
                    'title': movie_title,
                    'similarity_score': float(score),
                }
                recom.append(recom_item)
                count += 1
                    
            except Exception as e:
                logger.error(f"Error processing recommendation for index {idx}: {e}")
                continue
        
        return recom
    
    except ValueError as e:
        logger.error(f"ValueError in recommend: {e}")
        raise
    except RuntimeError as e:
        logger.error(f"RuntimeError in recommend: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in recommend: {e}")
        raise


def recommend_hybrid(movie, user=None, num_recommendations=8):
    """Re-rank content recommendations with decayed, account-level signals.

    The existing cosine-similarity engine remains the candidate generator.
    This layer only adds explainable preference scoring, so its data contract
    and the recommendation algorithm's source files stay intact.
    """
    candidates = recommend(movie, num_recommendations=80)
    if not user or not recommendation_engine.is_loaded:
        return candidates[:num_recommendations]

    preferences = {
        'genres': [item.lower() for item in user.get('favorite_genres', [])],
        'actors': [item.lower() for item in user.get('favorite_actors', [])],
        'directors': [item.lower() for item in user.get('favorite_directors', [])],
    }
    source_rating = next(
        (item.get('rating', 0) for item in user.get('ratings', []) if item.get('title') == movie),
        0,
    )
    watched_ids = {item.get('movie_id') for item in user.get('watch_history', [])}
    unseen = [item for item in candidates if item['movie_id'] not in watched_ids]
    pool = unseen if len(unseen) >= num_recommendations else candidates
    max_similarity = max((item.get('similarity_score', 0) for item in pool), default=1) or 1

    ranked = []
    for candidate in pool:
        row = recommendation_engine.movies_df.loc[
            recommendation_engine.movies_df['movie_id'] == candidate['movie_id']
        ]
        tags = '' if row.empty else str(row.iloc[0].get('tags', '')).lower()
        genre_match = any(token in tags for token in preferences['genres'])
        actor_match = next((name for name in preferences['actors'] if name in tags), None)
        director_match = next((name for name in preferences['directors'] if name in tags), None)
        content_score = candidate.get('similarity_score', 0) / max_similarity
        # The popularity component remains neutral until the optional TMDB
        # enrichment is available; this avoids per-card network requests.
        hybrid_score = (
            .45 * content_score + .25 * float(genre_match) +
            .10 * float(bool(actor_match)) + .05 * float(bool(director_match)) +
            .10 * (source_rating / 5) + .05 * .5
        )
        if actor_match:
            explanation = f"Features {actor_match.title()}"
        elif director_match:
            explanation = f"Directed by {director_match.title()}"
        elif genre_match:
            explanation = 'Matches your favorite genre'
        else:
            explanation = f'Because you liked {movie}'
        ranked.append({**candidate, 'hybrid_score': round(hybrid_score, 4), 'explanation': explanation})

    return sorted(ranked, key=lambda item: item['hybrid_score'], reverse=True)[:num_recommendations]


def resolve_local_movie(title, tmdb_id=None, release_year=None):
    """Map TMDB-selected titles to local CSV titles without exact-match brittleness."""
    if not recommendation_engine.is_loaded:
        return None
    movies = recommendation_engine.movies_df
    if isinstance(tmdb_id, int):
        id_match = movies[movies['movie_id'] == tmdb_id]
        if not id_match.empty:
            return id_match.iloc[0]['title']
    normalized = normalize_movie_title(title)
    exact = movies[movies['title'].map(normalize_movie_title) == normalized]
    if not exact.empty:
        return exact.iloc[0]['title']
    best_title, best_score = None, 0.0
    for candidate in movies['title']:
        score = SequenceMatcher(None, normalized, normalize_movie_title(candidate)).ratio()
        if score > best_score:
            best_title, best_score = candidate, score
    return best_title if best_score >= .72 else None

@app.route('/recommend', methods=['POST'])
def get_recommendations():
    """
    API endpoint to get movie recommendations.
    
    Request body: {"movie": "Movie Title"}
    Response: [{"movie_id": 123, "title": "...", "similarity_score": 0.98}, ...]
    
    Returns:
        JSON list of recommended movies from local CSV data only
    """
    try:
        # Check if recommendation engine is loaded
        if not recommendation_engine.is_loaded:
            error_msg = (
                "Recommendation system not available. "
                f"Issue: {recommendation_engine.load_error}"
            )
            logger.error(error_msg)
            return jsonify({
                "error": "Service unavailable",
                "message": error_msg
            }), 503
        
        data, error_response = get_request_json()
        if error_response:
            return error_response
        
        movie = str(data.get('movie') or data.get('title') or '').strip()
        if not movie:
            return jsonify({"error": "Invalid request", "message": "Movie title is required"}), 400
        
        local_movie = resolve_local_movie(data.get('title', movie), data.get('tmdb_id'), data.get('year'))
        if not local_movie:
            service = get_movie_details_service()
            fallback = service.get_similar_movies(data['tmdb_id']) if service and data.get('tmdb_id') else []
            if fallback:
                return jsonify(fallback), 200
            return jsonify([]), 200

        logger.info(f"Processing recommendation request for movie: {local_movie}")
        
        # Preserve local content recommendations, then personalize them when
        # the caller has an authenticated CineMatch profile.
        recommendations = recommend_hybrid(local_movie, current_authenticated_user(), num_recommendations=8)
        
        logger.info(f"Successfully retrieved {len(recommendations)} recommendations for {movie}")
        
        return jsonify(recommendations), 200
    
    except ValueError as e:
        logger.warning(f"ValueError in get_recommendations: {e}")
        return jsonify({
            "error": "Movie not found",
            "message": str(e)
        }), 404
    except RuntimeError as e:
        logger.error(f"RuntimeError in get_recommendations: {e}")
        return jsonify({
            "error": "Service error",
            "message": str(e)
        }), 503
    except Exception:
        logger.exception('Unexpected error in get_recommendations')
        return jsonify({
            "error": "Internal Server Error",
            "message": "Unable to generate recommendations right now."
        }), 500


@app.route('/api/recommendations', methods=['GET'])
def get_personalized_recommendations():
    """REST-friendly personalized recommendation endpoint for clients."""
    movie = request.args.get('movie', '').strip()
    if not movie:
        return jsonify({'error': 'movie query parameter is required'}), 400
    try:
        return jsonify({'recommendations': recommend_hybrid(movie, current_authenticated_user())}), 200
    except ValueError as error:
        return jsonify({'error': 'Movie not found', 'message': str(error)}), 404
    except RuntimeError as error:
        return jsonify({'error': 'Recommendation service unavailable', 'message': str(error)}), 503

@app.route('/api/data', methods=['GET'])
def get_movies_data():
    """
    API endpoint to get all movies data.
    
    Returns movies from in-memory storage if available, otherwise falls back to MongoDB.
    This is kept for backward compatibility and user-related features.
    """
    try:
        # Try to use in-memory data first (faster)
        if recommendation_engine.is_loaded and recommendation_engine.movies_df is not None:
            logger.info("Retrieving movies data from in-memory storage")
            movies_list = recommendation_engine.movies_df.to_dict('records')
            return jsonify(movies_list), 200
        
        # Fallback to MongoDB if in-memory data not available
        logger.info("In-memory data not available, falling back to MongoDB")
        collection = client.pklsmov
        items = collection.find()
        items_list = list(items)
        
        # Convert ObjectId to string if needed
        for item in items_list:
            item['_id'] = str(item['_id'])
        
        return jsonify(items_list), 200
    
    except Exception as e:
        logger.error(f"Error in get_movies_data: {e}")
        return jsonify({
            "error": "Failed to retrieve movies data",
            "message": str(e)
        }), 500


@app.route('/api/search', methods=['POST'])
def search_movie_tmdb():
    """
    Search for a movie on TMDB.
    Useful when a movie is not found in the local CSV dataset.
    
    Request body: {"query": "Movie Title", "year": 2020}  (year is optional)
    Response: List of matching movies with details
    """
    try:
        return jsonify({
            "error": "Endpoint disabled",
            "message": "TMDB is only used by movie detail endpoints. Use GET /movie/<movie_id>."
        }), 410
    
    except Exception as e:
        logger.error(f"Error searching TMDB: {e}")
        return jsonify({
            "error": "Search failed",
            "message": str(e)
        }), 500


def format_movie_details(movie_data):
    """Expose the movie details contract expected by the frontend."""
    return {
        'movie_id': movie_data.get('tmdb_id'),
        'title': movie_data.get('title'),
        'poster': movie_data.get('poster_url'),
        'backdrop': movie_data.get('backdrop_url'),
        'overview': movie_data.get('overview'),
        'genres': movie_data.get('genres', []),
        'runtime': movie_data.get('runtime'),
        'release_date': movie_data.get('release_date'),
        'rating': movie_data.get('vote_average'),
        'cast': movie_data.get('cast', []),
        'director': movie_data.get('director'),
        'trailer': movie_data.get('trailer_url'),
    }


@app.route('/movie/<int:movie_id>', methods=['GET'])
@app.route('/api/movie/<int:movie_id>', methods=['GET'])
def get_movie_details(movie_id):
    """
    Get detailed information about a movie by TMDB ID.
    
    Returns: Complete movie details including cast, genres, trailer, etc.
    """
    try:
        tmdb_service = get_movie_details_service()
        if not tmdb_service:
            return jsonify({
                "error": "TMDB service not available",
                "message": "TMDB API key not configured"
            }), 503
        
        logger.info(f"Fetching TMDB details for movie ID: {movie_id}")
        
        movie_data = tmdb_service.get_movie_details(movie_id)
        
        if not movie_data:
            return jsonify({
                "error": "Movie not found",
                "message": f"No movie found with TMDB ID {movie_id}"
            }), 404
        
        logger.info(f"Successfully retrieved details for TMDB movie {movie_id}")
        return jsonify(format_movie_details(movie_data)), 200
    
    except Exception:
        logger.exception('Error getting movie details')
        return jsonify({
            "error": "Failed to retrieve movie details",
            "message": "Movie details are temporarily unavailable."
        }), 500


@app.route('/api/movie/search/<movie_title>', methods=['GET'])
def search_and_get_movie(movie_title):
    """
    Search for a movie by title and return its full details.
    
    Useful for finding a specific movie and getting all available information.
    """
    try:
        return jsonify({
            "error": "Endpoint disabled",
            "message": "TMDB is only used by movie detail endpoints. Use GET /movie/<movie_id>."
        }), 410
    
    except Exception as e:
        logger.error(f"Error searching and getting movie: {e}")
        return jsonify({
            "error": "Search failed",
            "message": str(e)
        }), 500


@app.route('/api/recommend/enriched', methods=['POST'])
def get_enriched_recommendations():
    """
    Get movie recommendations with full TMDB enrichment.
    
    Returns the same recommendations but with additional TMDB data:
    - Overview, genres, runtime, release date
    - Vote average and count
    - Cast information
    - Trailer URL
    - Backdrop images
    
    Request body: {"movie": "Movie Title"}
    """
    try:
        if not recommendation_engine.is_loaded:
            error_msg = (
                "Recommendation system not available. "
                f"Issue: {recommendation_engine.load_error}"
            )
            logger.error(error_msg)
            return jsonify({
                "error": "Service unavailable",
                "message": error_msg
            }), 503
        
        data, error_response = get_request_json()
        if error_response:
            return error_response
        
        movie = data.get('movie', '').strip()
        if not movie:
            return jsonify({"error": "Invalid request", "message": "Movie title is required"}), 400
        
        logger.info(f"Processing enriched recommendation request for: {movie}")
        
        # Kept for backward compatibility; recommendations remain local-only.
        recommendations = recommend(movie, num_recommendations=8)
        
        logger.info(f"Retrieved {len(recommendations)} enriched recommendations for '{movie}'")
        return jsonify(recommendations), 200
    
    except ValueError as e:
        logger.warning(f"ValueError in get_enriched_recommendations: {e}")
        return jsonify({
            "error": "Movie not found",
            "message": str(e)
        }), 404
    except RuntimeError as e:
        logger.error(f"RuntimeError in get_enriched_recommendations: {e}")
        return jsonify({
            "error": "Service error",
            "message": str(e)
        }), 503
    except Exception as e:
        logger.error(f"Unexpected error in get_enriched_recommendations: {e}")
        return jsonify({
            "error": "Internal Server Error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5001)),
        debug=app.config['DEBUG'],
    )
