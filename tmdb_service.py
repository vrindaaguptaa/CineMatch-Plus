"""
TMDB API Service Module

Provides a reusable, cached interface to The Movie Database (TMDB) API.
Handles API calls, response caching, error handling, and rate limiting.
"""

import os
import requests
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from functools import wraps
import time
from collections import OrderedDict
from threading import Lock

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
CACHE_EXPIRY_SECONDS = 86400  # 24 hours
RATE_LIMIT_DELAY = 0.25  # Delay between requests to avoid rate limiting (4 req/sec)
MAX_CACHE_ENTRIES = 256
REQUEST_ATTEMPTS = 2


# ============================================================================
# CACHING DECORATOR
# ============================================================================
def cache_tmdb_response(expiry_seconds: int = CACHE_EXPIRY_SECONDS):
    """Decorator to cache TMDB API responses in memory with expiry."""
    cache = OrderedDict()
    lock = Lock()
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            
            # Check if cached and not expired
            with lock:
                if cache_key in cache:
                    cached_data, timestamp = cache.pop(cache_key)
                    if datetime.now() - timestamp < timedelta(seconds=expiry_seconds):
                        cache[cache_key] = (cached_data, timestamp)
                        logger.debug(f"Cache hit for {func.__name__}")
                        return cached_data
            
            # Call the function and cache result
            result = func(*args, **kwargs)
            # Do not turn a temporary TMDB outage into a 24-hour cached failure.
            if result is not None:
                with lock:
                    cache[cache_key] = (result, datetime.now())
                    while len(cache) > MAX_CACHE_ENTRIES:
                        cache.popitem(last=False)
                logger.debug(f"Cached result for {func.__name__}")
            return result
        
        return wrapper
    return decorator


# ============================================================================
# TMDB SERVICE CLASS
# ============================================================================
class TMDBService:
    """Service for interacting with The Movie Database (TMDB) API."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize TMDB Service.
        
        Args:
            api_key (str, optional): TMDB API key. If not provided, reads from env.
        """
        self.api_key = api_key or os.getenv('TMDB_API_KEY')
        if not self.api_key:
            logger.error("TMDB API key not found in environment variables")
            raise ValueError("TMDB_API_KEY not set in environment variables")
        
        self.session = requests.Session()
        self.last_request_time = 0
        logger.info("TMDB Service initialized")
    
    def _rate_limit(self):
        """Apply rate limiting to avoid hitting API limits."""
        elapsed = time.time() - self.last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self.last_request_time = time.time()
    
    def _make_request(self, endpoint: str, params: Dict[str, Any] = None) -> Optional[Dict]:
        """
        Make a GET request to TMDB API with error handling and rate limiting.
        
        Args:
            endpoint (str): API endpoint (relative path)
            params (dict): Query parameters
        
        Returns:
            dict: JSON response or None if error
        """
        try:
            self._rate_limit()
            
            url = f"{TMDB_BASE_URL}{endpoint}"
            if params is None:
                params = {}
            
            params['api_key'] = self.api_key
            
            for attempt in range(REQUEST_ATTEMPTS):
                response = self.session.get(url, params=params, timeout=(3.05, 10))
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < REQUEST_ATTEMPTS:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                response.raise_for_status()
                return response.json()
            return None
        
        except requests.exceptions.Timeout:
            logger.error(f"Timeout requesting {endpoint}")
            return None
        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                logger.warning("TMDB API rate limit reached")
            elif response.status_code == 404:
                logger.debug(f"Resource not found: {endpoint}")
            else:
                logger.error(f"HTTP error {response.status_code}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error making TMDB request to {endpoint}: {e}")
            return None
    
    @cache_tmdb_response()
    def get_movie_details(self, movie_id: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a movie by ID.
        
        Args:
            movie_id (int): TMDB movie ID
        
        Returns:
            dict: Movie details including poster, runtime, release date, genres, etc.
        """
        try:
            response = self._make_request(
                f"/movie/{movie_id}",
                params={'append_to_response': 'credits,videos'}
            )
            
            if not response:
                return None
            
            # Extract relevant fields
            movie_data = {
                'tmdb_id': response.get('id'),
                'title': response.get('title'),
                'overview': response.get('overview'),
                'poster_path': response.get('poster_path'),
                'backdrop_path': response.get('backdrop_path'),
                'release_date': response.get('release_date'),
                'runtime': response.get('runtime'),
                'vote_average': response.get('vote_average'),
                'vote_count': response.get('vote_count'),
                'genres': [g['name'] for g in response.get('genres', [])],
                'cast': self._extract_cast(response.get('credits', {})),
                'director': self._extract_director(response.get('credits', {})),
                'trailer_url': self._extract_trailer_url(response.get('videos', {})),
                'poster_url': self._build_image_url(response.get('poster_path'), size='w500'),
                'backdrop_url': self._build_image_url(response.get('backdrop_path'), size='w1280'),
            }
            
            logger.info(f"Successfully fetched TMDB details for movie ID {movie_id}")
            return movie_data
        
        except Exception as e:
            logger.error(f"Error fetching movie details for ID {movie_id}: {e}")
            return None
    
    @cache_tmdb_response()
    def search_movie(self, query: str, year: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
        """
        Search for movies by title.
        
        Args:
            query (str): Movie title to search for
            year (int, optional): Release year to narrow results
        
        Returns:
            list: List of matching movies with basic info
        """
        try:
            params = {'query': query, 'include_adult': False}
            if year:
                params['year'] = year
            
            response = self._make_request("/search/movie", params=params)
            
            if not response or not response.get('results'):
                logger.warning(f"No results found for search query: {query}")
                return []
            
            results = []
            for movie in response['results'][:5]:  # Limit to top 5 results
                results.append({
                    'tmdb_id': movie.get('id'),
                    'title': movie.get('title'),
                    'release_date': movie.get('release_date'),
                    'poster_url': self._build_image_url(movie.get('poster_path'), size='w500'),
                    'overview': movie.get('overview'),
                    'vote_average': movie.get('vote_average'),
                })
            
            logger.info(f"Found {len(results)} movies matching '{query}'")
            return results
        
        except Exception as e:
            logger.error(f"Error searching for movie '{query}': {e}")
            return []

    @cache_tmdb_response()
    def get_popular_movies(self, limit: int = 40) -> List[Dict[str, Any]]:
        """Return the first ``limit`` movies from TMDB's popular catalog.

        TMDB returns at most 20 results per page, so a 40-item catalog needs
        the first two pages. This keeps onboarding to endpoint-level requests
        rather than issuing a request for every displayed movie.
        """
        return self._get_popular_results('/movie/popular', limit, 'movie')

    @cache_tmdb_response()
    def get_popular_people(self, limit: int = 40) -> List[Dict[str, Any]]:
        """Return the first ``limit`` people from TMDB's popular catalog."""
        return self._get_popular_results('/person/popular', limit, 'person')

    @cache_tmdb_response()
    def get_similar_movies(self, movie_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """Return TMDB similar-movie results when the local model has no match."""
        response = self._make_request(f'/movie/{movie_id}/similar', {'page': 1})
        if not response:
            return []
        return [{
            'movie_id': item.get('id'),
            'title': item.get('title'),
            'poster_url': self._build_image_url(item.get('poster_path'), size='w500'),
            'overview': item.get('overview'),
            'release_date': item.get('release_date'),
            'vote_average': item.get('vote_average'),
            'popularity': item.get('popularity'),
            'recommendation_source': 'tmdb_similar',
            'similarity_score': 0.0,
            'explanation': 'Showing TMDB recommendations because this title is not available in the local recommendation model.',
        } for item in response.get('results', [])[:limit]]

    def _get_popular_results(self, endpoint: str, limit: int, result_type: str) -> List[Dict[str, Any]]:
        """Fetch paginated popular results and normalize their public fields."""
        if limit <= 0:
            return []

        results = []
        # TMDB paginates at 20 items; calculate only the pages necessary.
        page_count = (limit + 19) // 20
        for page in range(1, page_count + 1):
            response = self._make_request(endpoint, {'page': page, 'include_adult': False})
            if not response:
                break

            for item in response.get('results', []):
                if result_type == 'movie':
                    results.append({
                        'movie_id': item.get('id'),
                        'title': item.get('title'),
                        'poster_url': self._build_image_url(item.get('poster_path'), size='w500'),
                        'backdrop_url': self._build_image_url(item.get('backdrop_path'), size='w1280'),
                        'overview': item.get('overview'),
                        'release_date': item.get('release_date'),
                        'vote_average': item.get('vote_average'),
                    })
                else:
                    results.append({
                        'id': item.get('id'),
                        'name': item.get('name'),
                        'profile_url': self._build_image_url(item.get('profile_path'), size='w185'),
                        'known_for_department': item.get('known_for_department'),
                    })

                if len(results) >= limit:
                    return results[:limit]

        return results[:limit]
    
    @cache_tmdb_response()
    def get_movie_by_tmdb_id(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """
        Get movie details by TMDB ID. Alias for get_movie_details.
        
        Args:
            tmdb_id (int): TMDB movie ID
        
        Returns:
            dict: Complete movie data
        """
        return self.get_movie_details(tmdb_id)
    
    def search_and_get_details(self, movie_title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Search for a movie and return detailed information about the first result.
        
        Args:
            movie_title (str): Movie title to search for
            year (int, optional): Release year
        
        Returns:
            dict: Detailed movie information or None if not found
        """
        try:
            results = self.search_movie(movie_title, year)
            if results:
                first_result = results[0]
                # Fetch full details for the first result
                return self.get_movie_details(first_result['tmdb_id'])
            return None
        except Exception as e:
            logger.error(f"Error searching and getting details for '{movie_title}': {e}")
            return None
    
    def _extract_cast(self, credits: Dict[str, Any], max_cast: int = 5) -> List[Dict[str, str]]:
        """
        Extract top cast members from credits.
        
        Args:
            credits (dict): Credits data from API response
            max_cast (int): Maximum number of cast members to return
        
        Returns:
            list: List of cast members with name and character
        """
        try:
            cast = []
            for member in credits.get('cast', [])[:max_cast]:
                cast.append({
                    'name': member.get('name'),
                    'character': member.get('character'),
                    'profile_path': member.get('profile_path'),
                })
            return cast
        except Exception as e:
            logger.error(f"Error extracting cast: {e}")
            return []

    def _extract_director(self, credits: Dict[str, Any]) -> Optional[str]:
        """Extract the movie director from credits data."""
        try:
            for member in credits.get('crew', []):
                if member.get('job') == 'Director':
                    return member.get('name')
            return None
        except Exception as e:
            logger.error(f"Error extracting director: {e}")
            return None
    
    def _extract_trailer_url(self, videos: Dict[str, Any]) -> Optional[str]:
        """
        Extract YouTube trailer URL from videos data.
        
        Args:
            videos (dict): Videos data from API response
        
        Returns:
            str: YouTube trailer URL or None
        """
        try:
            for video in videos.get('results', []):
                if video.get('type') == 'Trailer' and video.get('site') == 'YouTube':
                    video_key = video.get('key')
                    if video_key:
                        return f"https://www.youtube.com/watch?v={video_key}"
            return None
        except Exception as e:
            logger.error(f"Error extracting trailer URL: {e}")
            return None
    
    def _build_image_url(self, path: Optional[str], size: str = 'w500') -> Optional[str]:
        """
        Build full image URL from TMDB path.
        
        Args:
            path (str): Image path from API response
            size (str): Image size (w500, w1280, etc.)
        
        Returns:
            str: Full image URL or None if no path
        """
        if not path:
            return None
        return f"{TMDB_IMAGE_BASE_URL}/{size}{path}"
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Return cache statistics for monitoring."""
        return {
            'last_request_time': self.last_request_time,
            'session_active': self.session is not None,
        }


# ============================================================================
# GLOBAL SERVICE INSTANCE
# ============================================================================
def get_tmdb_service() -> TMDBService:
    """Get or create the global TMDB service instance."""
    global _tmdb_service
    if '_tmdb_service' not in globals():
        try:
            _tmdb_service = TMDBService()
        except ValueError as e:
            logger.error(f"Failed to initialize TMDB service: {e}")
            return None
    return _tmdb_service
