import configparser
import toml
from flask import Flask, render_template, jsonify, request
from werkzeug.local import LocalProxy
from flask_pymongo import PyMongo
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import logging
from tmdb_service import get_tmdb_service

load_dotenv()
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configuration
config = configparser.ConfigParser()
app.config['DEBUG'] = True
app.config['USERNAME'] = os.getenv('USERNAME')
app.config['PASSWORD'] = os.getenv('PASSWORD')
app.config['MONGO_URI'] = (
    "mongodb+srv://"
    + app.config['USERNAME']
    + ":"
    + app.config['PASSWORD']
    + "@cluster0.xsimi.mongodb.net/MovieRS?retryWrites=true&w=majority&appName=Cluster0"
)
mongo = PyMongo(app)

# Use LocalProxy to read the global db instance with just `db`
client = LocalProxy(lambda: mongo.db)

# Initialize TMDB Service (optional, will warn if API key not found)
try:
    tmdb_service = get_tmdb_service()
    logger.info("TMDB Service initialized successfully")
except Exception as e:
    tmdb_service = None
    logger.warning(f"TMDB Service could not be initialized: {e}")

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
        """Load movies.csv and similarity.csv into memory at app startup."""
        try:
            # Define file paths
            current_dir = os.path.dirname(os.path.abspath(__file__))
            movies_path = os.path.join(current_dir, 'movies.csv')
            similarity_path = os.path.join(current_dir, 'similarity.csv')
            
            # Check if files exist
            if not os.path.exists(movies_path):
                raise FileNotFoundError(f"movies.csv not found at {movies_path}")
            if not os.path.exists(similarity_path):
                raise FileNotFoundError(f"similarity.csv not found at {similarity_path}")
            
            # Load movies data
            logger.info("Loading movies.csv into memory...")
            self.movies_df = pd.read_csv(movies_path)
            logger.info(f"Loaded {len(self.movies_df)} movies")
            
            # Load similarity matrix
            logger.info("Loading similarity.csv into memory...")
            similarity_df = pd.read_csv(similarity_path, index_col=0)
            self.similarity_matrix = similarity_df.values.astype(np.float32)
            logger.info(f"Loaded similarity matrix with shape {self.similarity_matrix.shape}")
            
            # Create title-to-index mapping for O(1) lookups
            self.movie_title_index = {
                title: idx for idx, title in enumerate(self.movies_df['title'])
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
        normalized_title = movie_title.strip()
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


@app.route('/')
def home():
    return render_template('index.html')

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def fetch_poster(movie_id):
    """
    Fetch movie poster from TMDB API.
    Uses TMDB service if available for better caching and error handling.
    """
    if tmdb_service:
        try:
            details = tmdb_service.get_movie_details(movie_id)
            if details and details.get('poster_url'):
                return details['poster_url']
        except Exception as e:
            logger.debug(f"TMDB service fallback failed: {e}")
    
    # Fallback to direct API call
    try:
        url = f"https://api.themoviedb.org/3/movie/{movie_id}"
        
        params = {
    "api_key": TMDB_API_KEY,
    "language": "en-US"
}
        data = requests.get(url, timeout=5)
        data = data.json()
        
        if 'poster_path' not in data or data['poster_path'] is None:
            return None
        
        poster_path = data['poster_path']
        full_path = "https://image.tmdb.org/t/p/w500/" + poster_path
        return full_path
    except Exception as e:
        logger.error(f"Error fetching poster for movie_id {movie_id}: {e}")
        return None


def enrich_movie_with_tmdb(movie_data, movie_id):
    """
    Enrich movie data with TMDB information.
    
    Args:
        movie_data (dict): Basic movie data from CSV
        movie_id (int): TMDB movie ID
    
    Returns:
        dict: Enriched movie data
    """
    if not tmdb_service:
        return movie_data
    
    try:
        tmdb_details = tmdb_service.get_movie_details(movie_id)
        if tmdb_details:
            # Merge TMDB data with existing movie data
            enriched = movie_data.copy()
            enriched.update({
                'tmdb_id': tmdb_details.get('tmdb_id'),
                'overview': tmdb_details.get('overview') or enriched.get('overview'),
                'poster_url': tmdb_details.get('poster_url'),
                'backdrop_url': tmdb_details.get('backdrop_url'),
                'genres': tmdb_details.get('genres', []),
                'runtime': tmdb_details.get('runtime'),
                'release_date': tmdb_details.get('release_date') or enriched.get('release_date'),
                'vote_average': tmdb_details.get('vote_average'),
                'vote_count': tmdb_details.get('vote_count'),
                'cast': tmdb_details.get('cast', []),
                'trailer_url': tmdb_details.get('trailer_url'),
            })
            return enriched
    except Exception as e:
        logger.warning(f"Error enriching movie with TMDB data: {e}")
    
    return movie_data


# ============================================================================
# RECOMMENDATION FUNCTIONS
# ============================================================================
def recommend(movie, num_recommendations=8, enrich_tmdb=True):
    """
    Get movie recommendations using in-memory similarity matrix.
    Optionally enriches with TMDB data for enhanced details.
    
    Args:
        movie (str): Movie title to get recommendations for
        num_recommendations (int): Number of recommendations to return
        enrich_tmdb (bool): Whether to enrich with TMDB data
    
    Returns:
        list: List of dictionaries with movie details
    """
    try:
        # Get the index of the input movie
        movie_index = recommendation_engine.get_movie_index(movie)
        
        # Get similarity scores for this movie
        similarity_scores = recommendation_engine.get_similarity_scores(movie_index)
        
        # Find top similar movies (exclude the movie itself by starting from index 1)
        distances = sorted(
            enumerate(similarity_scores),
            key=lambda x: x[1],
            reverse=True
        )
        
        recom = []
        count = 0
        
        # Collect recommendations, skipping the first one (the movie itself)
        for idx, score in distances[1:]:
            if count >= num_recommendations:
                break
            
            try:
                movie_details = recommendation_engine.get_movie_details(idx)
                movie_id = movie_details.get('id')
                movie_title = movie_details.get('title')
                
                # Build recommendation dict
                recom_item = {
                    'title': movie_title,
                    'url': fetch_poster(movie_id),  # Basic recommendation format
                    'similarity_score': float(score),
                }
                
                # Enrich with TMDB data if requested
                if enrich_tmdb and tmdb_service:
                    enriched = enrich_movie_with_tmdb(dict(movie_details), movie_id)
                    recom_item.update({
                        'tmdb_id': enriched.get('tmdb_id'),
                        'overview': enriched.get('overview'),
                        'genres': enriched.get('genres', []),
                        'runtime': enriched.get('runtime'),
                        'release_date': enriched.get('release_date'),
                        'vote_average': enriched.get('vote_average'),
                        'vote_count': enriched.get('vote_count'),
                        'poster_url': enriched.get('poster_url'),
                        'backdrop_url': enriched.get('backdrop_url'),
                        'cast': enriched.get('cast', []),
                        'trailer_url': enriched.get('trailer_url'),
                    })
                
                if recom_item.get('url'):  # Only add if we got a poster
                    recom.append(recom_item)
                    count += 1
                else:
                    logger.warning(f"Could not fetch poster for {movie_title}")
                    
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



@app.route('/recommend', methods=['POST'])
def get_recommendations():
    """
    API endpoint to get movie recommendations.
    
    Request body: {"movie": "Movie Title"}
    Response: [{"title": "...", "url": "..."}, ...]
    
    Returns:
        JSON list of recommended movies with titles and poster URLs
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
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request", "message": "No JSON body provided"}), 400
        
        movie = data.get('movie', '').strip()
        if not movie:
            return jsonify({"error": "Invalid request", "message": "Movie title is required"}), 400
        
        logger.info(f"Processing recommendation request for movie: {movie}")
        
        # Get recommendations from in-memory engine
        recommendations = recommend(movie)
        
        logger.info(f"Successfully retrieved {len(recommendations)} recommendations for {movie}")
        
        # Return in the same format as before for frontend compatibility
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
    except Exception as e:
        logger.error(f"Unexpected error in get_recommendations: {e}")
        return jsonify({
            "error": "Internal Server Error",
            "message": str(e)
        }), 500

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


if __name__ == "__main__":
    # app.run(debug=True, port=5001)
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5001)),
        debug=True
    )