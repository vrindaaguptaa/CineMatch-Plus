const PLACEHOLDER_POSTER = '/images/poster1.png';
const authState = { user: null };
const libraryState = { favorites: [], watchlist: [], ratings: [], watch_history: [] };
let selectedRecommendationMovie = null;
let featuredMovies = [];

function showToast(message, type = 'info') {
    let toast = document.getElementById('app-toast');
    if (!toast) { toast = document.createElement('div'); toast.id = 'app-toast'; toast.className = 'app-toast'; document.body.appendChild(toast); }
    toast.textContent = message; toast.dataset.type = type; toast.classList.add('is-visible');
    clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toast.classList.remove('is-visible'), 3200);
}

const request = async (url, options = {}) => {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.error || 'Something went wrong.');
    return data;
};

function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[character]));
}

function safeMediaUrl(value, fallback = PLACEHOLDER_POSTER) {
    if (!value) return fallback;
    try {
        const url = new URL(value, window.location.origin);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : fallback;
    } catch (_) {
        return fallback;
    }
}

function movieId(movie) { return movie.movie_id || movie.tmdb_id || movie.id; }
function isSaved(collection, id) { return libraryState[collection].some(item => item.movie_id === id); }
function libraryPayload(movie) { return { movie_id: movieId(movie), title: movie.title, poster: movie.poster || movie.poster_url, backdrop: movie.backdrop || movie.backdrop_url, overview: movie.overview, release_date: movie.release_date, rating: movie.rating || movie.vote_average }; }

function renderMovieSkeletons(container, count = 8) {
    container.innerHTML = Array.from({ length: count }, () => '<article class="movie-card movie-skeleton"><div></div><p></p><p></p></article>').join('');
}

function movieCard(movie) {
    const id = movieId(movie); const card = document.createElement('article'); card.className = 'movie-card'; card.dataset.movieId = id;
    const year = movie.release_date ? String(movie.release_date).slice(0, 4) : '—';
    const rating = Number(movie.rating || movie.vote_average);
    card.innerHTML = `<button class="movie-poster-button" aria-label="Open details"><img loading="lazy" src="${escapeHTML(safeMediaUrl(movie.poster || movie.poster_url))}" alt=""></button><div class="movie-card-info"><h3></h3><p>${escapeHTML(year)} ${Number.isFinite(rating) ? `· ★ ${rating.toFixed(1)}` : ''}</p><div class="card-actions"><button data-action="favorites" aria-label="Favorite"><i class="fa-${isSaved('favorites', id) ? 'solid' : 'regular'} fa-heart"></i></button><button data-action="watchlist" aria-label="Want to watch"><i class="fa-${isSaved('watchlist', id) ? 'solid' : 'regular'} fa-bookmark"></i></button><button data-action="rate" aria-label="Rate"><i class="fa-solid fa-star"></i></button><button data-action="details" aria-label="Details"><i class="fa-solid fa-ellipsis"></i></button></div></div>`;
    card.querySelector('h3').textContent = movie.title || 'Untitled';
    card.querySelector('.movie-poster-button').onclick = () => openMovie(movie);
    card.querySelector('[data-action="details"]').onclick = () => openMovie(movie);
    ['favorites', 'watchlist'].forEach(collection => card.querySelector(`[data-action="${collection}"]`).onclick = () => toggleLibrary(collection, movie));
    card.querySelector('[data-action="rate"]').onclick = () => promptRating(movie);
    return card;
}
function renderMovieCards(container, movies) { container.innerHTML = ''; movies.forEach(movie => container.appendChild(movieCard(movie))); }

async function toggleLibrary(collection, movie) {
    if (!authState.user) return openAuthModal();
    const id = movieId(movie); const saved = isSaved(collection, id);
    try {
        const data = saved ? await request(`/api/library/${collection}/${id}`, { method: 'DELETE' }) : await request(`/api/library/${collection}/${id}`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(libraryPayload(movie)) });
        libraryState[collection] = data[collection]; authState.user[collection] = data[collection]; refreshVisibleCards();
    } catch (error) { showToast(error.message, 'error'); }
}
function promptRating(movie) { if (!authState.user) return openAuthModal(); openRatingModal(movie); }
function openRatingModal(movie) {
    let modal = document.getElementById('rating-modal');
    if (!modal) { modal = document.createElement('div'); modal.id = 'rating-modal'; modal.className = 'rating-modal'; modal.innerHTML = '<section class="rating-modal-panel"><button class="rating-modal-close">×</button><img><div><p class="eyebrow">Your rating</p><h2></h2><p class="rating-current"></p><div class="premium-stars"></div><div class="rating-modal-actions"><button class="text-button">Cancel</button><button class="sub-btn" data-submit>Save rating</button></div></div></section>'; document.body.appendChild(modal); modal.querySelector('.rating-modal-close').onclick = () => modal.classList.remove('is-open'); modal.querySelector('.text-button').onclick = () => modal.classList.remove('is-open'); }
    const existing = libraryState.ratings.find(item => item.movie_id === movieId(movie))?.rating || 0; let chosen = existing;
    modal.querySelector('img').src = safeMediaUrl(movie.poster || movie.poster_url); modal.querySelector('h2').textContent = movie.title; modal.querySelector('.rating-current').textContent = existing ? `Current rating: ${existing} / 5` : 'Choose a rating from 1 to 5';
    const stars = modal.querySelector('.premium-stars'); stars.innerHTML = ''; for (let star = 1; star <= 5; star += 1) { const button = document.createElement('button'); button.type = 'button'; button.textContent = '★'; button.classList.toggle('is-filled', star <= chosen); button.onmouseenter = () => [...stars.children].forEach((item, index) => item.classList.toggle('is-filled', index < star)); button.onclick = () => { chosen = star; modal.querySelector('.rating-current').textContent = `Your rating: ${chosen} / 5`; [...stars.children].forEach((item, index) => item.classList.toggle('is-filled', index < chosen)); }; stars.appendChild(button); }
    modal.querySelector('[data-submit]').onclick = async () => { if (!chosen) return showToast('Choose a star rating first.', 'error'); try { const data = await request(`/api/library/ratings/${movieId(movie)}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...libraryPayload(movie),user_rating:chosen})}); libraryState.ratings = data.ratings; authState.user.ratings = data.ratings; modal.classList.remove('is-open'); showToast('Your rating was saved.', 'success'); } catch (error) { showToast(error.message, 'error'); } };
    modal.classList.add('is-open');
}
function refreshVisibleCards() {
    document.querySelectorAll('.movie-card[data-movie-id]').forEach(card => {
        const id = Number(card.dataset.movieId);
        ['favorites', 'watchlist'].forEach(collection => {
            const icon = card.querySelector(`[data-action="${collection}"] i`);
            if (icon) icon.className = `fa-${isSaved(collection, id) ? 'solid' : 'regular'} fa-${collection === 'favorites' ? 'heart' : 'bookmark'}`;
        });
    });
}

async function openMovie(movie) {
    let details = movie;
    if (movieId(movie)) { try { details = await request(`/api/movie/${movieId(movie)}`); } catch (_) {} }
    showMovieDetails(details);
    if (authState.user && movieId(details)) {
        try { const data = await request(`/api/library/watch_history/${movieId(details)}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(libraryPayload(details))}); libraryState.watch_history = data.watch_history; authState.user.watch_history = data.watch_history; } catch (_) {}
    }
}

function showMovieDetails(movie) {
    const modal = getMovieModal(); modal.dataset.movie = JSON.stringify(libraryPayload(movie));
    const backdrop = safeMediaUrl(movie.backdrop || movie.backdrop_url, '');
    modal.querySelector('.movie-modal-backdrop').style.backgroundImage = backdrop ? `url("${backdrop}")` : 'none';
    const poster = modal.querySelector('.movie-modal-poster'); poster.src = safeMediaUrl(movie.poster || movie.poster_url); poster.alt = movie.title || 'Movie poster';
    modal.querySelector('.movie-modal-title').textContent = movie.title || 'Movie details';
    modal.querySelector('.movie-modal-meta').textContent = [movie.release_date, movie.runtime && `${movie.runtime} min`, (movie.rating || movie.vote_average) && `★ ${Number(movie.rating || movie.vote_average).toFixed(1)}`, movie.director && `Director: ${movie.director}`].filter(Boolean).join(' · ');
    modal.querySelector('.movie-modal-overview').textContent = movie.overview || 'No overview available.';
    const cast = Array.isArray(movie.cast) ? movie.cast.map(item => item.name).filter(Boolean).join(', ') : ''; modal.querySelector('.movie-modal-cast').textContent = [Array.isArray(movie.genres) && movie.genres.join(', '), cast && `Cast: ${cast}`].filter(Boolean).join(' · ');
    const trailer = modal.querySelector('.movie-modal-trailer'); trailer.style.display = movie.trailer ? 'inline-flex' : 'none'; trailer.href = movie.trailer || '#';
    ['favorites', 'watchlist'].forEach(collection => { const button = modal.querySelector(`[data-action="${collection}"]`); const saved = isSaved(collection, movieId(movie)); button.classList.toggle('is-active', saved); button.querySelector('i').className = `fa-${saved ? 'solid' : 'regular'} fa-${collection === 'favorites' ? 'heart' : 'bookmark'}`; });
    modal.classList.add('is-open');
}
function getMovieModal() {
    let modal = document.getElementById('movie-details-modal'); if (modal) return modal;
    modal = document.createElement('div'); modal.id = 'movie-details-modal'; modal.className = 'movie-modal';
    modal.innerHTML = `<div class="movie-modal-backdrop"></div><div class="movie-modal-panel"><button class="movie-modal-close" aria-label="Close">×</button><img class="movie-modal-poster" alt="Movie poster"><div class="movie-modal-content"><h2 class="movie-modal-title"></h2><p class="movie-modal-meta"></p><p class="movie-modal-overview"></p><p class="movie-modal-cast"></p><div class="modal-actions"><button data-action="favorites"><i class="fa-regular fa-heart"></i> Favorite</button><button data-action="watchlist"><i class="fa-regular fa-bookmark"></i> Want to watch</button><button data-action="rate"><i class="fa-solid fa-star"></i> Rate</button></div><a class="movie-modal-trailer" target="_blank" rel="noopener">Watch trailer</a></div></div>`;
    modal.querySelector('.movie-modal-close').onclick = () => modal.classList.remove('is-open'); modal.onclick = event => { if (event.target === modal) modal.classList.remove('is-open'); };
    ['favorites', 'watchlist'].forEach(collection => modal.querySelector(`[data-action="${collection}"]`).onclick = () => { toggleLibrary(collection, JSON.parse(modal.dataset.movie)); setTimeout(() => showMovieDetails(JSON.parse(modal.dataset.movie)), 0); });
    modal.querySelector('[data-action="rate"]').onclick = () => promptRating(JSON.parse(modal.dataset.movie)); document.body.appendChild(modal); return modal;
}

async function getRecommendation() {
    const movie = document.getElementById('ret').value; if (!movie) return;
    const row = document.getElementById('recommendation-row'); renderMovieSkeletons(row);
    const watched = libraryState.watch_history.some(item => item.title === movie);
    const reason = document.getElementById('recommendation-reason'); reason.hidden = false; reason.textContent = watched ? 'Similar to movies in your Watch History' : `Because you selected ${movie}`;
    try { const recommendations = await request('/recommend', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({movie, title: selectedRecommendationMovie?.title || movie, tmdb_id: selectedRecommendationMovie?.tmdb_id, year: selectedRecommendationMovie?.release_date?.slice(0, 4)})}); if (recommendations[0]?.explanation && !watched) reason.textContent = recommendations[0].explanation; const movies = await Promise.all(recommendations.map(async item => { try { return {...await request(`/api/movie/${item.movie_id}`), explanation: item.explanation, similarity_score: item.similarity_score, hybrid_score: item.hybrid_score}; } catch (_) { return item; } })); renderMovieCards(row, movies); } catch (error) { row.innerHTML = `<p class="empty-state">${error.message}</p>`; }
}

async function loadDiscovery() {
    const popular = document.getElementById('popular-row'); renderMovieSkeletons(popular);
    try { const featured = await request('/api/discovery/featured'); const data = await request('/api/discovery/popular'); featuredMovies = data.movies || []; setFeaturedMovie(featured.movie || featuredMovies[0]); renderMovieCards(popular, featuredMovies); if (featuredMovies.length > 1) setInterval(() => setFeaturedMovie(featuredMovies[Math.floor(Math.random() * featuredMovies.length)]), 25000); } catch (err) { console.error(err);popular.innerHTML = '<p class="empty-state">Popular picks are temporarily unavailable.</p>'; }
}
// function setFeaturedMovie(movie) {
//     const hero = document.getElementById('featured-hero'); if (!movie) return hero.classList.remove('is-loading');
//     hero.querySelector('.hero-art').style.backgroundImage = `url("${movie.backdrop_url || movie.poster_url}")`;
//     hero.querySelector('.movie-title').textContent = movie.title; hero.querySelector('.movie-des').textContent = movie.overview || 'A popular pick chosen for tonight.';
//     const button = hero.querySelector('.watch'); button.disabled = false; button.onclick = () => openMovie(movie); hero.classList.remove('is-loading');
// }


function setFeaturedMovie(movie) {
    const hero = document.getElementById('featured-hero');

    if (!movie) {
        hero.classList.remove('is-loading');
        return;
    }

    const art = hero.querySelector('.hero-art');
    const title = hero.querySelector('.movie-title');
    const desc = hero.querySelector('.movie-des');
    const button = hero.querySelector('.hero-details'); const favorite = hero.querySelector('.hero-favorite'); const meta = hero.querySelector('.hero-meta'); const genres = hero.querySelector('.hero-genres'); const trailer = hero.querySelector('.hero-trailer');

    art.style.backgroundImage = `url("${safeMediaUrl(movie.backdrop_url || movie.poster_url)}")`;

    title.textContent = movie.title;
    desc.textContent =
        movie.overview || "A popular pick chosen for tonight.";
    meta.textContent = [movie.release_date?.slice(0, 4), movie.runtime && `${movie.runtime} min`, movie.vote_average && `★ ${Number(movie.vote_average).toFixed(1)}`].filter(Boolean).join(' · ');
    genres.textContent = Array.isArray(movie.genres) ? movie.genres.join(' · ') : '';

    // REMOVE SKELETON CLASSES
    title.classList.remove("skeleton-text");
    desc.classList.remove("skeleton-text");

    button.disabled = false;
    button.onclick = () => openMovie(movie);
    favorite.disabled = false; favorite.onclick = () => toggleLibrary('favorites', movie); favorite.classList.toggle('is-active', isSaved('favorites', movieId(movie)));
    trailer.hidden = !movie.trailer; trailer.href = movie.trailer || '#';

    hero.classList.remove("is-loading");
}




function setupAutocomplete() {
    const input = document.getElementById('fornamesake'), results = document.getElementById('search-results'), hidden = document.getElementById('ret'), submit = document.getElementById('Recommendations'); let timer, controller, activeIndex = -1, movies = [];
    const choose = movie => { selectedRecommendationMovie = movie; hidden.value = movie.title; 
        input.value = movie.title;
        // `${movie.title}${movie.release_date ? ` (${movie.release_date.slice(0, 4)})` : ''}`; 
        results.replaceChildren(); submit.disabled = false; activeIndex = -1; };
    const render = () => { results.innerHTML = ''; movies.forEach((movie, index) => { 
        
        const option = document.createElement('button'); option.type = 'button'; option.className = index === activeIndex ? 'is-active' : ''; option.innerHTML = `<img src="${escapeHTML(safeMediaUrl(movie.poster_url))}" alt=""><span>${escapeHTML(movie.title)}<small>${escapeHTML(movie.release_date ? String(movie.release_date).slice(0, 4) : 'Release date unavailable')}</small></span>`; option.onclick = () => choose(movie); 
    


    results.appendChild(option);}); };
    input.oninput = () => { clearTimeout(timer); hidden.value = ''; selectedRecommendationMovie = null; submit.disabled = true; const query = input.value.trim(); if (query.length < 2) return results.replaceChildren(); timer = setTimeout(async () => { controller?.abort(); controller = new AbortController(); try { const data = await request(`/api/discovery/search?q=${encodeURIComponent(query)}`, {signal: controller.signal}); 
    
    
    movies = data.results || []; activeIndex = -1; render(); } catch (error) { if (error.name !== 'AbortError') results.replaceChildren(); } }, 240); };
    input.onkeydown = event => { if (!movies.length) return; if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); activeIndex = event.key === 'ArrowDown' ? Math.min(activeIndex + 1, movies.length - 1) : Math.max(activeIndex - 1, 0); render(); } else if (event.key === 'Enter' && activeIndex >= 0) { event.preventDefault(); choose(movies[activeIndex]); } else if (event.key === 'Escape') results.replaceChildren(); };
}

async function hydrateLibrary() { if (!authState.user) return; await Promise.all(Object.keys(libraryState).map(async collection => { try { const data = await request(`/api/library/${collection}`); libraryState[collection] = data[collection]; authState.user[collection] = data[collection]; } catch (_) {} })); }
function openLibrary(collection, label) { if (!authState.user) return openAuthModal(); closeProfileDropdown(); const view = document.getElementById('library-view'), sort = document.getElementById('library-sort'), row = document.getElementById('library-row'); view.hidden = false; document.getElementById('dashboard-view').hidden = true; document.getElementById('library-title').textContent = label; sort.innerHTML = collection === 'favorites' ? '<option value="newest">Newest</option><option value="rating">Highest Rated</option><option value="title">Alphabetical</option>' : '<option value="newest">Recently Added</option><option value="title">Alphabetical</option>'; 
    
    
    
    const render = () => { const items = [...libraryState[collection]].sort((a, b) => sort.value === 'title' ? a.title.localeCompare(b.title) : sort.value === 'rating' ? (b.rating || 0) - (a.rating || 0) : new Date(b.updated_at || b.viewed_at || 0) - new Date(a.updated_at || a.viewed_at || 0)); if (!items.length) { row.innerHTML = `<p class="empty-state">No ${collection === 'favorites' ? 'favorite movies' : 'movies in your watchlist'} yet.</p>`; return; } 
    
    renderMovieCards(row, items); if (collection === 'watchlist') row.querySelectorAll('.movie-card').forEach(card => { const button = document.createElement('button'); button.className = 'mark-watched'; button.textContent = 'Mark as watched'; 
        
        button.onclick = () => markAsWatched(Number(card.dataset.movieId)); card.querySelector('.movie-card-info').appendChild(button); }); }; sort.onchange = render; render(); view.scrollIntoView({behavior:'smooth'}); }
async function markAsWatched(id) { try { const data = await request(`/api/library/watchlist/${id}/watched`, {method:'POST'}); libraryState.watchlist = data.watchlist; libraryState.watch_history = data.watch_history; openLibrary('watchlist', 'Watchlist'); showToast('Moved to your watch history.', 'success'); } catch (error) { showToast(error.message, 'error'); } }
async function openDashboard() { if (!authState.user) return openAuthModal(); try { const data = await request('/api/dashboard'); const dashboard = document.getElementById('dashboard-view'); document.getElementById('library-view').hidden = true; dashboard.hidden = false; const c = data.counts, p = data.preferences, recent = data.recent_activity.map(item => item.title).filter(Boolean).join(' · ') || 'No activity yet'; document.getElementById('dashboard-content').innerHTML = `<article><strong>${c.favorites}</strong><span>Favorites</span></article><article><strong>${c.ratings}</strong><span>Ratings</span></article><article><strong>${c.watchlist}</strong><span>Watchlist</span></article><article><strong>${c.history}</strong><span>History</span></article><article class="dashboard-wide"><h3>Preference profile</h3><p>Genres: ${p.genres.join(', ') || 'No genres yet'}</p><p>Actors: ${p.actors.join(', ') || 'No actors yet'}</p><p>Directors: ${p.directors.join(', ') || 'No directors yet'}</p></article><article class="dashboard-wide"><h3>Rating distribution</h3><p>${Object.entries(p.rating_distribution).map(([star, count]) => `★ ${star}: ${count}`).join(' · ')}</p></article><article class="dashboard-wide"><h3>Recent activity</h3><p>${recent}</p><p>${data.recommendation_statistics.recent_interactions} recent interaction signals are improving your recommendations.</p></article>`; dashboard.scrollIntoView({behavior:'smooth'}); } catch (error) { showToast(error.message, 'error'); } }

function openAuthModal(mode = 'login') { let modal = document.getElementById('auth-modal'); if (!modal) { modal = document.createElement('div'); modal.id = 'auth-modal'; modal.className = 'auth-modal'; modal.innerHTML = `<div class="auth-panel"><button class="auth-close">×</button><h2>Welcome to CineMatch</h2><p class="auth-subtitle"></p><p class="auth-message"></p><form><label class="auth-username-field">Username<input name="username"></label><label>Email<input name="email" type="email" required></label><label>Password<input name="password" type="password" minlength="8" required></label><button class="auth-submit">Continue</button></form><button class="auth-switch"></button></div>`; document.body.appendChild(modal); modal.querySelector('.auth-close').onclick = () => modal.classList.remove('is-open'); modal.querySelector('.auth-switch').onclick = () => configureAuth(modal.dataset.mode === 'signup' ? 'login' : 'signup'); modal.querySelector('form').onsubmit = submitAuthForm; } configureAuth(mode); modal.classList.add('is-open'); }
function configureAuth(mode) { const modal = document.getElementById('auth-modal'), signup = mode === 'signup'; modal.dataset.mode = mode; modal.querySelector('.auth-subtitle').textContent = signup ? 'Create an account to save your discoveries.' : 'Sign in to your personal cinema.'; modal.querySelector('.auth-username-field').classList.toggle('is-visible', signup); modal.querySelector('.auth-switch').textContent = signup ? 'Already have an account? Log in' : 'New here? Create an account'; }
async function submitAuthForm(event) { event.preventDefault(); const modal = document.getElementById('auth-modal'), form = event.currentTarget, signup = modal.dataset.mode === 'signup'; try { const data = await request(signup ? '/signup' : '/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: form.username.value.trim(), email: form.email.value.trim(), password: form.password.value})}); authState.user = data.user; await hydrateLibrary(); updateAuthTrigger(); modal.classList.remove('is-open'); if (!authState.user.onboarding_completed) openOnboarding(); } catch (error) { modal.querySelector('.auth-message').textContent = error.message; } }
function updateAuthTrigger() { document.getElementById('auth-trigger').textContent = authState.user ? authState.user.username : 'Login'; }
function closeProfileDropdown() { document.getElementById('profile-dropdown')?.remove(); }
function openProfileDropdown() { if (!authState.user) return openAuthModal(); closeProfileDropdown(); const u = authState.user, dropdown = document.createElement('div'); dropdown.id = 'profile-dropdown'; dropdown.className = 'profile-dropdown'; dropdown.innerHTML = `<p class="profile-name"></p><p class="profile-email"></p><div class="profile-stats"><button data-page="dashboard">Dashboard</button><button data-page="favorites">Favorites <strong>${libraryState.favorites.length}</strong></button><button data-page="watchlist">Watchlist <strong>${libraryState.watchlist.length}</strong></button></div><button class="profile-logout">Logout</button>`; dropdown.querySelector('.profile-name').textContent = u.username; dropdown.querySelector('.profile-email').textContent = u.email; dropdown.querySelector('[data-page="dashboard"]').onclick = openDashboard; dropdown.querySelector('[data-page="favorites"]').onclick = () => openLibrary('favorites', 'Favorites'); dropdown.querySelector('[data-page="watchlist"]').onclick = () => openLibrary('watchlist', 'Watchlist'); dropdown.querySelector('.profile-logout').onclick = logout; document.body.appendChild(dropdown); const rect = document.getElementById('auth-trigger').getBoundingClientRect(); dropdown.style.top = `${rect.bottom + scrollY + 8}px`; dropdown.style.right = `${innerWidth - rect.right}px`; }
async function logout() { try { await fetch('/logout', {method: 'POST'}); } finally { authState.user = null; Object.keys(libraryState).forEach(key => libraryState[key] = []); closeProfileDropdown(); updateAuthTrigger(); } }
async function loadCurrentUser() { try { const data = await request('/me'); authState.user = data.user; await hydrateLibrary(); const genre = authState.user.favorite_genres?.[0]; if (genre) document.getElementById('recommendation-reason').textContent = `Based on your ${genre} preference`; if (!authState.user.onboarding_completed) openOnboarding(); } catch (_) {} updateAuthTrigger(); }

// The onboarding flow retains its four steps and existing data contract.
const onboardingState = { step: 0, genres: [], actors: [], directors: [], ratings: {}, catalog: {movies: [], actors: []} };
const ONBOARDING_GENRES = ['Action','Adventure','Animation','Comedy','Crime','Drama','Fantasy','Horror','Mystery','Romance','Science Fiction','Thriller','War','Western','Family'];
const ONBOARDING_DIRECTORS = ['Christopher Nolan','James Cameron','Steven Spielberg','Denis Villeneuve','Martin Scorsese','Ridley Scott','David Fincher','Quentin Tarantino'];
async function openOnboarding() { let modal = document.getElementById('onboarding-modal'); if (!modal) { modal = document.createElement('div'); modal.id = 'onboarding-modal'; modal.className = 'onboarding-modal'; modal.innerHTML = `<section class="onboarding-panel"><div class="onboarding-progress"><span></span><div class="onboarding-progress-track"><i></i></div></div><div class="onboarding-content"></div><div class="onboarding-actions"><button class="onboarding-back">Back</button><button class="onboarding-next">Continue</button></div></section>`; document.body.appendChild(modal); modal.querySelector('.onboarding-back').onclick = () => changeOnboardingStep(-1); modal.querySelector('.onboarding-next').onclick = () => changeOnboardingStep(1); } modal.classList.add('is-open'); try { onboardingState.catalog = (await request('/api/onboarding')).catalog || onboardingState.catalog; } catch (_) {} renderOnboarding(); }
function renderOnboarding() { const modal = document.getElementById('onboarding-modal'), step = onboardingState.step, content = modal.querySelector('.onboarding-content'); modal.querySelector('.onboarding-progress span').textContent = `Step ${step + 1} of 4`; modal.querySelector('.onboarding-progress-track i').style.width = `${(step + 1) * 25}%`; modal.querySelector('.onboarding-back').style.visibility = step ? 'visible' : 'hidden'; const next = modal.querySelector('.onboarding-next'); next.textContent = step === 3 ? 'Finish' : 'Continue'; if (step === 0) choiceStep(content, 'What do you love watching?', 'genres', ONBOARDING_GENRES, 5); else if (step === 1) ratingStep(content); else if (step === 2) choiceStep(content, 'Choose your favorite actors', 'actors', onboardingState.catalog.actors.map(x => x.name), 5, onboardingState.catalog.actors); else choiceStep(content, 'Who are your favorite directors?', 'directors', ONBOARDING_DIRECTORS, 3); }
function choiceStep(content, title, key, choices, maximum, actors = []) { content.innerHTML = `<h2>${escapeHTML(title)}</h2><p>Select up to ${maximum} options.</p><div class="onboarding-choice-grid"></div>`; const grid = content.querySelector('div'); choices.forEach(name => { const button = document.createElement('button'); button.className = `onboarding-choice ${onboardingState[key].includes(name) ? 'is-selected' : ''}`; const person = actors.find(a => a.name === name); button.innerHTML = `${person?.profile_url ? `<img src="${escapeHTML(safeMediaUrl(person.profile_url))}" alt="">` : '<span class="choice-icon">✦</span>'}<span>${escapeHTML(name)}</span>`; button.onclick = () => { const selected = onboardingState[key], index = selected.indexOf(name); if (index >= 0) selected.splice(index, 1); else if (selected.length < maximum) selected.push(name); renderOnboarding(); }; grid.appendChild(button); }); }
function ratingStep(content) { content.innerHTML = '<h2>Rate a few favorites</h2><p>Choose any movies you have seen.</p><div class="rating-grid"></div>'; const grid = content.querySelector('div'); onboardingState.catalog.movies.forEach(movie => { const card = document.createElement('article'); card.className = 'rating-card'; card.innerHTML = `<img src="${escapeHTML(safeMediaUrl(movie.poster_url))}" alt=""><p>${escapeHTML(movie.title)}</p><div class="rating-stars"></div>`; for(let star=1; star<=5; star++) { const b = document.createElement('button'); b.textContent = '★'; b.className = star <= (onboardingState.ratings[movie.movie_id] || 0) ? 'is-filled' : ''; b.onclick = () => { onboardingState.ratings[movie.movie_id] = star; renderOnboarding(); }; card.querySelector('.rating-stars').appendChild(b); } grid.appendChild(card); }); }
function changeOnboardingStep(direction) { if (direction < 0) onboardingState.step = Math.max(0, onboardingState.step - 1); else if (onboardingState.step === 3) return completeOnboarding(); else onboardingState.step += 1; renderOnboarding(); }
async function completeOnboarding() { try { const data = await request('/api/onboarding', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({favorite_genres: onboardingState.genres, favorite_actors: onboardingState.actors, favorite_directors: onboardingState.directors, ratings: Object.entries(onboardingState.ratings).map(([movie_id, rating]) => ({movie_id: Number(movie_id), rating}))})}); authState.user = data.user; document.getElementById('onboarding-modal').classList.remove('is-open'); updateAuthTrigger(); } catch (error) { showToast(error.message, 'error'); } }

document.addEventListener('DOMContentLoaded', () => { document.getElementById('Recommendations').onclick = getRecommendation; document.getElementById('auth-trigger').onclick = openProfileDropdown; document.getElementById('close-library').onclick = () => { document.getElementById('library-view').hidden = true; }; document.getElementById('close-dashboard').onclick = () => { document.getElementById('dashboard-view').hidden = true; }; document.querySelectorAll('[data-library-link]').forEach(link => link.onclick = () => openLibrary(link.dataset.libraryLink, link.dataset.libraryLink === 'favorites' ? 'Favorites' : 'Watchlist')); document.getElementById('dashboard-link').onclick = openDashboard; document.addEventListener('click', event => { const dropdown = document.getElementById('profile-dropdown'); if (dropdown && !dropdown.contains(event.target) && event.target.id !== 'auth-trigger') closeProfileDropdown(); }); setupAutocomplete(); loadCurrentUser(); loadDiscovery(); });
