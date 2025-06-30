from flask import Flask, request, render_template_string, redirect, url_for, session, jsonify, send_from_directory, abort, send_file
import os
import random
import subprocess
import mimetypes
import sqlite3
from flask import g
import hashlib
import re
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a secure secret key

# --- Configuration of media directories ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INTERFAITH_DIR = os.path.join(BASE_DIR, 'interfaith')
GALLERY_DIR = os.path.join(BASE_DIR, 'gallery')
VIDEOS_DIR = os.path.join(BASE_DIR, 'videos')  # Optional extra videos folder

VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.mpeg', '.mpg', '.3gp'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tif', '.tiff', '.svg', '.webp', '.ico'}

# --- New User/Tab Helpers ---
USERS_DIR = os.path.join(BASE_DIR, 'users')

DATABASE = os.path.join(BASE_DIR, 'appdata.sqlite3')

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            avatar_seed TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_key TEXT NOT NULL,
            user TEXT NOT NULL,
            text TEXT NOT NULL,
            parent_id INTEGER,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_key TEXT NOT NULL,
            user TEXT NOT NULL,
            value INTEGER NOT NULL, -- 1 for like, -1 for dislike
            UNIQUE(media_key, user)
        );
        ''')
        
        # Add avatar_seed column to existing users table if it doesn't exist
        try:
            db.execute('ALTER TABLE users ADD COLUMN avatar_seed TEXT DEFAULT NULL')
            print("Added avatar_seed column to users table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("avatar_seed column already exists")
            else:
                print(f"Error adding avatar_seed column: {e}")
        
        db.commit()
        # Create default user for backward compatibility
        create_default_user()

def hash_password(password):
    """Hash a password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, password_hash):
    """Verify a password against its hash"""
    return hash_password(password) == password_hash

def validate_username(username):
    """Validate username format"""
    if len(username) < 3 or len(username) > 20:
        return False, "Username must be between 3 and 20 characters"
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return False, "Username can only contain letters, numbers, and underscores"
    return True, ""

def validate_password(password):
    """Validate password strength"""
    if len(password) < 6:
        return False, "Password must be at least 6 characters long"
    return True, ""

def validate_email(email):
    """Validate email format"""
    if not email:
        return True, ""  # Email is optional
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return False, "Invalid email format"
    return True, ""

def create_user(username, password, email=None):
    """Create a new user in the database"""
    db = get_db()
    try:
        db.execute('INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)',
                  (username, hash_password(password), email))
        db.commit()
        return True, "User created successfully"
    except sqlite3.IntegrityError:
        return False, "Username already exists"
    except Exception as e:
        return False, f"Error creating user: {str(e)}"

def authenticate_user(username, password):
    """Authenticate a user"""
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    if user and verify_password(password, user['password_hash']):
        return True, user
    return False, None

def get_user_avatar_seed(user_id):
    """Get the avatar seed for a user"""
    db = get_db()
    user = db.execute('SELECT avatar_seed FROM users WHERE id = ?', (user_id,)).fetchone()
    if user and user['avatar_seed']:
        return user['avatar_seed']
    return None

def update_user_avatar_seed(user_id, avatar_seed):
    """Update the avatar seed for a user"""
    db = get_db()
    db.execute('UPDATE users SET avatar_seed = ? WHERE id = ?', (avatar_seed, user_id))
    db.commit()
    return True

def create_default_user():
    """Create a default user for backward compatibility"""
    db = get_db()
    # Check if default user exists
    user = db.execute('SELECT * FROM users WHERE username = ?', ('krishna',)).fetchone()
    if not user:
        # Create default user with password '71124'
        db.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                  ('krishna', hash_password('71124')))
        db.commit()
        print("Default user 'krishna' created with password '71124'")
        
        # Create user directory
        user_dir = os.path.join(USERS_DIR, 'krishna')
        if not os.path.exists(user_dir):
            os.makedirs(user_dir)
            print(f"Created user directory: {user_dir}")

# Remove @app.before_first_request and use a flag with @app.before_request
_db_initialized = False

@app.before_request
def before_request():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True

def list_users():
    return [d for d in os.listdir(USERS_DIR) if os.path.isdir(os.path.join(USERS_DIR, d))]

def list_tabs(username):
    user_dir = os.path.join(USERS_DIR, username)
    if not os.path.exists(user_dir):
        return []
    return [d for d in os.listdir(user_dir) if os.path.isdir(os.path.join(user_dir, d))]

def detect_tab_type(tab_path):
    # Recursively scan the tab directory to determine type
    has_image = False
    has_video = False
    has_folder = False
    
    for entry in os.listdir(tab_path):
        # Skip thumbnail files as they don't affect the tab type
        if entry.endswith('_thumb.jpg'):
            continue
            
        full_path = os.path.join(tab_path, entry)
        if os.path.isdir(full_path):
            has_folder = True
        elif is_image(entry):
            has_image = True
        elif is_video(entry):
            has_video = True
    if has_folder:
        return 'albums'
    if has_image and not has_video:
        return 'images'
    if has_video and not has_image:
        return 'videos'
    if has_image and has_video:
        return 'mixed'
    return 'empty'

def get_tab_media(username, tab, rel_path=None):
    # rel_path is used for recursion (subfolders)
    if rel_path is None:
        tab_path = os.path.join(USERS_DIR, username, tab)
        rel_path = tab
    else:
        tab_path = os.path.join(USERS_DIR, username, rel_path)
    items = []
    for entry in os.listdir(tab_path):
        full_path = os.path.join(tab_path, entry)
        rel_entry = os.path.join(rel_path, entry).replace('\\', '/')
        if os.path.isdir(full_path):
            # Recursively get children for albums
            children = get_tab_media(username, tab, rel_entry)
            items.append({'type': 'album', 'name': entry, 'children': children})
        elif is_image(entry):
            url = url_for('files', filename=os.path.join('users', username, rel_entry).replace('\\', '/'))
            items.append({'type': 'image', 'name': entry, 'url': url})
        elif is_video(entry):
            url = url_for('files', filename=os.path.join('users', username, rel_entry).replace('\\', '/'))
            thumb = get_video_thumbnail_local(os.path.join('users', username, rel_entry).replace('\\', '/'))
            items.append({'type': 'video', 'name': entry, 'url': url, 'thumb': thumb})
    return items

# --- Paginated, non-recursive tab media ---
def get_tab_media_paged(username, tab, rel_path=None, offset=0, limit=30):
    if rel_path is None:
        tab_path = os.path.join(USERS_DIR, username, tab)
    else:
        tab_path = os.path.join(USERS_DIR, username, tab, rel_path)
    try:
        entries = os.listdir(tab_path)
        # Don't sort - keep the natural order from the filesystem
        print(f"Raw entries in {tab_path}: {entries}")
    except Exception as e:
        return []
    
    items = []
    for entry in entries:
        if entry.endswith('_thumb.jpg'):
            continue
        full_path = os.path.join(tab_path, entry)
        if rel_path is None:
            rel_entry = os.path.join(tab, entry).replace('\\', '/')
        else:
            rel_entry = os.path.join(tab, rel_path, entry).replace('\\', '/')
        
        if os.path.isdir(full_path):
            items.append({'type': 'album', 'name': entry})
            print(f"Added album: {entry}")
        elif is_image(entry):
            url = url_for('files', filename=os.path.join('users', username, rel_entry).replace('\\', '/'))
            items.append({'type': 'image', 'name': entry, 'url': url})
            print(f"Added image: {entry}")
        elif is_video(entry):
            url = url_for('files', filename=os.path.join('users', username, rel_entry).replace('\\', '/'))
            thumb = get_video_thumbnail_local(os.path.join('users', username, rel_entry).replace('\\', '/'))
            items.append({'type': 'video', 'name': entry, 'url': url, 'thumb': thumb})
            print(f"Added video: {entry}")
    
    print(f"Final items order: {[item['type'] + ':' + item['name'] for item in items]}")
    
    # Shuffle the items to ensure random mixing of images and videos
    import random
    random.shuffle(items)
    print(f"After shuffle: {[item['type'] + ':' + item['name'] for item in items]}")
    
    items = items[offset:offset+limit]
    print(f"Returning {len(items)} items for {tab_path} (offset={offset}, limit={limit})")
    return items

# --- Helper functions ---
def is_video(filename):
    return os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS

def is_image(filename):
    return os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS

def get_video_thumbnail_local(local_path):
    """
    Given a local file path relative to BASE_DIR (e.g., "interfaith/myvideo.mp4"),
    check if a thumbnail with suffix "_thumb.jpg" exists. If not, generate one using ffmpeg.
    Return the thumbnail URL or a default if generation fails.
    """
    base, ext = os.path.splitext(local_path)
    thumb_path = base + "_thumb.jpg"
    full_thumb_path = os.path.join(BASE_DIR, thumb_path)
    if not os.path.exists(full_thumb_path):
        # Generate thumbnail using ffmpeg
        print(f"Generating thumbnail for {local_path}...")
        video_full_path = os.path.join(BASE_DIR, local_path)
        try:
            subprocess.run([
                'ffmpeg',
                '-i', video_full_path,
                '-ss', '00:00:01.000',  # extract frame at 1 second
                '-vframes', '1',
                full_thumb_path
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(f"Thumbnail generated: {thumb_path}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Error generating thumbnail for {local_path}: {e}")
            return url_for('files', filename='default_video_thumb.png')
    return url_for('files', filename=thumb_path.replace('\\', '/'))

def get_interfaith_media():
    """
    Returns a list of dictionaries for media in the interfaith directory.
    - For images: { 'url': <url>, 'type': 'image' }
    - For videos: { 'url': <url>, 'type': 'video', 'thumb': <thumb_url> }
    """
    print("getting all the interfaith media")
    media = []
    if os.path.exists(INTERFAITH_DIR):
        for item in os.listdir(INTERFAITH_DIR):
            if item.endswith('_thumb.jpg'):
                continue
            local_rel = os.path.join('interfaith', item).replace('\\', '/')
            if is_image(item):
                media.append({'url': url_for('files', filename=local_rel), 'type': 'image', 'name': item})
            elif is_video(item):
                thumb = get_video_thumbnail_local(local_rel)
                media.append({'url': url_for('files', filename=local_rel), 'type': 'video', 'thumb': thumb, 'name': item})
    print(f"get_interfaith_media: Loaded {len(media)} items")
    return media

def get_gallery_structure():
    """
    Returns a tree structure representing the folder (album) structure.
    - For images: { 'type': 'image', 'name': <name>, 'url': <url> }
    - For videos: { 'type': 'video', 'name': <name>, 'url': <url>, 'thumb': <thumb_url> }
    - For folders: { 'type': 'folder', 'name': <name>, 'children': <list>, 'album_thumb': <thumb_url> }
    """
    def walk_folder(folder):
        structure = []
        for item in os.listdir(folder):
            path = os.path.join(folder, item)
            if os.path.isdir(path):
                children = walk_folder(path)
                album_thumb = find_album_thumbnail(children)
                structure.append({
                    'type': 'folder',
                    'name': item,
                    'children': children,
                    'album_thumb': album_thumb
                })
            else:
                rel_path = os.path.relpath(path, BASE_DIR).replace('\\', '/')
                if is_image(item):
                    structure.append({
                        'type': 'image',
                        'name': item,
                        'url': url_for('files', filename=rel_path)
                    })
                elif is_video(item):
                    thumb = get_video_thumbnail_local(rel_path)
                    structure.append({
                        'type': 'video',
                        'name': item,
                        'url': url_for('files', filename=rel_path),
                        'thumb': thumb
                    })
        return structure

    result = walk_folder(GALLERY_DIR) if os.path.exists(GALLERY_DIR) else []
    print(f"get_gallery_structure: Loaded gallery with {len(result)} top-level items")
    return result

def find_album_thumbnail(items):
    """
    Recursively search for the first image or video thumbnail in a list of items.
    Returns the URL of the thumbnail or None if not found.
    """
    # First, look for an image
    for item in items:
        if item['type'] == 'image':
            return item['url']
    # Then, look for a video thumbnail
    for item in items:
        if item['type'] == 'video' and 'thumb' in item:
            return item['thumb']
        elif item['type'] == 'folder':
            thumb = find_album_thumbnail(item['children'])
            if thumb:
                return thumb
    return None

def get_all_videos():
    """
    Returns a shuffled list of video URLs from all directories.
    """
    print("getting all the videos")
    videos = []
    # From interfaith
    if os.path.exists(INTERFAITH_DIR):
        for item in os.listdir(INTERFAITH_DIR):
            if item.endswith('_thumb.jpg'):
                continue
            if is_video(item):
                local_rel = os.path.join('interfaith', item).replace('\\', '/')
                videos.append(url_for('files', filename=local_rel))
    # From gallery (recursively)
    if os.path.exists(GALLERY_DIR):
        for root, dirs, files in os.walk(GALLERY_DIR):
            for file in files:
                if file.endswith('_thumb.jpg'):
                    continue
                if is_video(file):
                    rel_path = os.path.relpath(os.path.join(root, file), BASE_DIR).replace('\\', '/')
                    videos.append(url_for('files', filename=rel_path))
    # From videos directory
    if os.path.exists(VIDEOS_DIR):
        for item in os.listdir(VIDEOS_DIR):
            if item.endswith('_thumb.jpg'):
                continue
            if is_video(item):
                local_rel = os.path.join('videos', item).replace('\\', '/')
                videos.append(url_for('files', filename=local_rel))
    random.shuffle(videos)
    print(f"get_all_videos: Loaded {len(videos)} videos")
    return videos

# --- API Endpoints for Pagination ---
@app.route('/api/interfaith')
def api_interfaith():
    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 30))
    all_media = get_interfaith_media()
    paged = all_media[offset:offset + limit]
    print(f"/api/interfaith: Serving items {offset} to {offset + limit} (got {len(paged)})")
    return jsonify(paged)

@app.route('/api/tiktok')
def api_tiktok():
    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 15))
    all_videos = get_all_videos()
    paged = all_videos[offset:offset + limit]
    print(f"/api/tiktok: Serving videos {offset} to {offset + limit} (got {len(paged)})")
    return jsonify(paged)

# --- API Endpoints ---
def get_all_media_recursive(user, tab, base_path=""):
    """Recursively get all media from a tab including subdirectories"""
    media = []
    if base_path:
        tab_path = os.path.join(USERS_DIR, user, tab, base_path)
    else:
        tab_path = os.path.join(USERS_DIR, user, tab)
    
    if not os.path.exists(tab_path):
        return media
    
    try:
        for entry in os.listdir(tab_path):
            if entry.endswith('_thumb.jpg'):
                continue
            full_path = os.path.join(tab_path, entry)
            if base_path:
                rel_entry = os.path.join(tab, base_path, entry).replace('\\', '/')
            else:
                rel_entry = os.path.join(tab, entry).replace('\\', '/')
            
            if os.path.isdir(full_path):
                # Recursively get media from subdirectory
                sub_path = os.path.join(base_path, entry) if base_path else entry
                media.extend(get_all_media_recursive(user, tab, sub_path))
            elif is_image(entry):
                url = url_for('files', filename=os.path.join('users', user, rel_entry).replace('\\', '/'))
                media.append({'type': 'image', 'name': entry, 'url': url})
            elif is_video(entry):
                url = url_for('files', filename=os.path.join('users', user, rel_entry).replace('\\', '/'))
                thumb = get_video_thumbnail_local(os.path.join('users', user, rel_entry).replace('\\', '/'))
                media.append({'type': 'video', 'name': entry, 'url': url, 'thumb': thumb})
    except Exception as e:
        print(f"Error reading directory {tab_path}: {e}")
    
    return media

@app.route('/api/feed')
def api_feed():
    try:
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 30))
        users = list_users()
        feed = []
        for user in users:
            tabs = list_tabs(user)
            for tab in tabs:
                # Get all media recursively from tab and subdirectories
                media = get_all_media_recursive(user, tab)
                for item in media:
                    if item['type'] in ('image', 'video'):
                        item['user'] = user
                        item['tab'] = tab
                        feed.append(item)
        # Add interfaith media
        for item in get_interfaith_media():
            if item['type'] in ('image', 'video'):
                item['user'] = 'interfaith'
                item['tab'] = 'interfaith'
                feed.append(item)
        # Add videos and images from the root videos directory
        if os.path.exists(VIDEOS_DIR):
            for item in os.listdir(VIDEOS_DIR):
                if item.endswith('_thumb.jpg'):
                    continue
                if is_video(item):
                    local_rel = os.path.join('videos', item).replace('\\', '/')
                    url = url_for('files', filename=local_rel)
                    thumb = get_video_thumbnail_local(local_rel)
                    feed.append({'type': 'video', 'name': item, 'url': url, 'thumb': thumb, 'user': 'videos', 'tab': 'videos'})
                elif is_image(item):
                    local_rel = os.path.join('videos', item).replace('\\', '/')
                    url = url_for('files', filename=local_rel)
                    feed.append({'type': 'image', 'name': item, 'url': url, 'user': 'videos', 'tab': 'videos'})
        import random
        random.shuffle(feed)
        feed = feed[offset:offset+limit]
        return jsonify(feed)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

import json


@app.route('/api/stories')
def api_stories():
    """
    Returns a list of all stories (from story.json in any user tab marked 'story').
    Each entry is the raw story.json with 'user' and 'tab' fields added.
    """
    stories = []

    for user in list_users():
        for tab in list_tabs(user):
            story_path = os.path.join(USERS_DIR, user, tab, 'story.json')
            if not os.path.exists(story_path):
                continue

            try:
                with open(story_path, 'r', encoding='utf-8') as f:
                    story = json.load(f)

                # look up this user’s avatar_seed in your users table
                db = get_db()
                row = db.execute(
                    "SELECT avatar_seed FROM users WHERE username = ?",
                    (user,)
                ).fetchone()
                story['avatar_seed'] = row['avatar_seed'] if row else None

                story['user'] = user
                story['tab']  = tab
                stories.append(story)
            except Exception as e:
                print(f"Error loading story for {user}/{tab}: {e}")

    return jsonify(stories)




@app.route('/api/profile/<username>')
def api_profile(username):
    tabs = list_tabs(username)
    tab_info = []
    for tab in tabs:
        tab_path = os.path.join(USERS_DIR, username, tab)
        story_json_path = os.path.join(tab_path, 'story.json')
        if os.path.exists(story_json_path):
            tab_type = 'story'
        else:
            tab_type = detect_tab_type(tab_path)
        media_count = 0
        try:
            for entry in os.listdir(tab_path):
                if entry.endswith('_thumb.jpg'):
                    continue
                full_path = os.path.join(tab_path, entry)
                if os.path.isdir(full_path):
                    for root, dirs, files in os.walk(full_path):
                        for file in files:
                            if not file.endswith('_thumb.jpg'):
                                media_count += 1
                elif is_image(entry) or is_video(entry):
                    media_count += 1
        except Exception as e:
            print(f"Error counting media in {tab_path}: {e}")
            media_count = 0
        tab_info.append({
            'name': tab,
            'type': tab_type,
            'count': media_count
        })
    return jsonify({'username': username, 'tabs': tab_info})

@app.route('/api/profile/<username>/add_tab', methods=['POST'])
def api_add_tab(username):
    import json
    data = request.json
    tab_name = data.get('tab_name')
    tab_type = data.get('tab_type', 'media')
    description = data.get('description', '')
    user_dir = os.path.join(USERS_DIR, username)
    tab_dir = os.path.join(user_dir, tab_name)
    if not os.path.exists(tab_dir):
        os.makedirs(tab_dir)
    # If this is a story tab, create story.json
    if tab_type == 'story':
        story_json = {
            'name': tab_name,
            'description': description,
            'type': 'story',
            'nodes': [],
            'connections': []
        }
        with open(os.path.join(tab_dir, 'story.json'), 'w', encoding='utf-8') as f:
            json.dump(story_json, f, indent=2)
    # Optionally, create subfolders for albums (media tabs only)
    return jsonify({'success': True, 'tab': tab_name, 'type': tab_type})

@app.route('/api/profile/<username>/<tab>/upload', methods=['POST'])
def api_upload_to_tab(username, tab):
    try:
        # Get the optional album path from form data or query params
        album_path = request.form.get('album_path') or request.args.get('album_path', '')
        
        # Construct the target directory
        if album_path:
            # Upload to specific album within tab
            target_dir = os.path.join(USERS_DIR, username, tab, album_path)
        else:
            # Upload to tab root
            target_dir = os.path.join(USERS_DIR, username, tab)
        
        if not os.path.exists(target_dir):
            return jsonify({'error': 'Target directory does not exist'}), 404
        
        # Handle file uploads
        if 'files' in request.files:
            uploaded_files = []
            files = request.files.getlist('files')
            for file in files:
                if file.filename:
                    # Secure the filename
                    filename = os.path.basename(file.filename)
                    file_path = os.path.join(target_dir, filename)
                    file.save(file_path)
                    uploaded_files.append(filename)
            
            target_location = f"album '{album_path}'" if album_path else "tab root"
            return jsonify({
                'success': True, 
                'message': f'Uploaded {len(uploaded_files)} files to {target_location}',
                'files': uploaded_files,
                'album_path': album_path
            })
        
        # Handle folder creation
        data = request.json if request.is_json else None
        if data and 'folder_name' in data:
            folder_name = data['folder_name']
            folder_path = os.path.join(target_dir, folder_name)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
                return jsonify({
                    'success': True,
                    'message': f'Created folder: {folder_name}',
                    'folder': folder_name,
                    'album_path': album_path
                })
            else:
                return jsonify({'error': 'Folder already exists'}), 400
        
        return jsonify({'error': 'No files or folder specified'}), 400
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile/<username>/<tab>/files', methods=['GET'])
def api_list_tab_files(username, tab):
    try:
        tab_dir = os.path.join(USERS_DIR, username, tab)
        if not os.path.exists(tab_dir):
            return jsonify({'error': 'Tab does not exist'}), 404
        
        files = []
        for entry in os.listdir(tab_dir):
            if entry.endswith('_thumb.jpg'):
                continue
            full_path = os.path.join(tab_dir, entry)
            file_info = {
                'name': entry,
                'type': 'folder' if os.path.isdir(full_path) else 'file',
                'size': os.path.getsize(full_path) if os.path.isfile(full_path) else None
            }
            files.append(file_info)
        
        return jsonify({'files': files})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile/<username>/<tab>/media')
def api_profile_tab_media(username, tab):
    try:
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 30))
        rel_path = request.args.get('rel_path')
        items = get_tab_media_paged(username, tab, rel_path, offset, limit)
        return jsonify(items)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile/<username>/<tab>/album')
def api_profile_album(username, tab):
    try:
        album = request.args.get('album')
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 30))
        items = get_tab_media_paged(username, tab, album, offset, limit)
        return jsonify(items)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile/<username>/<tab>/delete_album', methods=['DELETE'])
def api_delete_album(username, tab):
    try:
        data = request.json
        album_path = data.get('album_path')
        
        if not album_path:
            return jsonify({'error': 'Album path is required'}), 400
        
        # Construct the full path to the album
        full_path = os.path.join(USERS_DIR, username, tab, album_path)
        
        if not os.path.exists(full_path):
            return jsonify({'error': 'Album not found'}), 404
        
        if not os.path.isdir(full_path):
            return jsonify({'error': 'Path is not a directory'}), 400
        
        # Delete the entire directory and its contents
        import shutil
        shutil.rmtree(full_path)
        
        return jsonify({'success': True, 'message': f'Album "{album_path}" deleted successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile/<username>/<tab>/delete_media', methods=['DELETE'])
def api_delete_media(username, tab):
    try:
        data = request.json
        media_path = data.get('media_path')
        
        if not media_path:
            return jsonify({'error': 'Media path is required'}), 400
        
        # Construct the full path to the media file
        full_path = os.path.join(USERS_DIR, username, tab, media_path)
        
        if not os.path.exists(full_path):
            return jsonify({'error': 'Media file not found'}), 404
        
        if os.path.isdir(full_path):
            return jsonify({'error': 'Path is a directory, not a media file'}), 400
        
        # Delete the media file
        os.remove(full_path)
        
        # Also delete thumbnail if it exists (for videos)
        base, ext = os.path.splitext(full_path)
        thumb_path = base + "_thumb.jpg"
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
        
        return jsonify({'success': True, 'message': f'Media "{media_path}" deleted successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- Route to serve local files ---
@app.route('/files/<path:filename>')
def files(filename):
    return send_from_directory(BASE_DIR, filename)

# --- Basic Login ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = request.args.get('error', "")
    show_signup = request.args.get('show_signup', False)
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            error = "Please enter both username and password"
        else:
            success, user = authenticate_user(username, password)
            if success:
                session['logged_in'] = True
                session['username'] = user['username']
                session['user_id'] = user['id']
                print(f"User {username} logged in successfully.")
                return redirect(url_for('home_page'))
            else:
                error = "Invalid username or password"
    
    return render_template_string("""
<html>
<head>
  <title>Login</title>
  <style>
    body { 
      font-family: Arial, sans-serif; 
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      display: flex; 
      justify-content: center; 
      align-items: center; 
      height: 100vh; 
      margin: 0; 
    }
    .auth-container {
      background: rgba(255, 255, 255, 0.95);
      padding: 40px;
      border-radius: 15px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.3);
      width: 100%;
      max-width: 400px;
      backdrop-filter: blur(10px);
    }
    .auth-tabs {
      display: flex;
      margin-bottom: 30px;
      border-bottom: 2px solid #eee;
    }
    .auth-tab {
      flex: 1;
      padding: 15px;
      text-align: center;
      cursor: pointer;
      border: none;
      background: none;
      font-size: 16px;
      font-weight: 600;
      color: #666;
      transition: all 0.3s ease;
    }
    .auth-tab.active {
      color: #667eea;
      border-bottom: 3px solid #667eea;
    }
    .auth-form {
      display: none;
    }
    .auth-form.active {
      display: block;
    }
    .form-group {
      margin-bottom: 20px;
    }
    label {
      display: block;
      margin-bottom: 8px;
      font-weight: 600;
      color: #333;
    }
    input {
      width: 100%;
      padding: 12px 15px;
      border: 2px solid #ddd;
      border-radius: 8px;
      font-size: 16px;
      transition: border-color 0.3s ease;
      box-sizing: border-box;
    }
    input:focus {
      outline: none;
      border-color: #667eea;
    }
    button {
      width: 100%;
      padding: 15px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      border: none;
      border-radius: 8px;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.2s ease;
    }
    button:hover {
      transform: translateY(-2px);
    }
    .error {
      color: #e74c3c;
      text-align: center;
      margin: 15px 0;
      padding: 10px;
      background: #fdf2f2;
      border-radius: 5px;
      border-left: 4px solid #e74c3c;
    }
    .success {
      color: #27ae60;
      text-align: center;
      margin: 15px 0;
      padding: 10px;
      background: #f0f9f4;
      border-radius: 5px;
      border-left: 4px solid #27ae60;
    }
  </style>
</head>
<body>
  <div class="auth-container">
    <div class="auth-tabs">
      <button class="auth-tab {% if not show_signup %}active{% endif %}" onclick="showTab('login')">Login</button>
      <button class="auth-tab {% if show_signup %}active{% endif %}" onclick="showTab('signup')">Sign Up</button>
    </div>
    
    <form method="post" class="auth-form {% if not show_signup %}active{% endif %}" id="login-form">
      <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" required>
      </div>
      <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required>
      </div>
      <button type="submit">Login</button>
      {% if error and not show_signup %}
        <div class="error">{{ error }}</div>
      {% endif %}
    </form>
    
    <form method="post" action="/signup" class="auth-form {% if show_signup %}active{% endif %}" id="signup-form">
      <div class="form-group">
        <label for="signup-username">Username</label>
        <input type="text" id="signup-username" name="username" required>
      </div>
      <div class="form-group">
        <label for="signup-email">Email (optional)</label>
        <input type="email" id="signup-email" name="email">
      </div>
      <div class="form-group">
        <label for="signup-password">Password</label>
        <input type="password" id="signup-password" name="password" required>
      </div>
      <div class="form-group">
        <label for="confirm-password">Confirm Password</label>
        <input type="password" id="confirm-password" name="confirm_password" required>
      </div>
      <button type="submit">Sign Up</button>
      {% if error and show_signup %}
        <div class="error">{{ error }}</div>
      {% endif %}
    </form>
  </div>
  
  <script>
    function showTab(tabName) {
      // Update tab buttons
      document.querySelectorAll('.auth-tab').forEach(tab => tab.classList.remove('active'));
      event.target.classList.add('active');
      
      // Update forms
      document.querySelectorAll('.auth-form').forEach(form => form.classList.remove('active'));
      if (tabName === 'login') {
        document.getElementById('login-form').classList.add('active');
      } else {
        document.getElementById('signup-form').classList.add('active');
      }
    }
  </script>
</body>
</html>
    """, error=error)

@app.route('/signup', methods=['POST'])
def signup():
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')
    
    # Validation
    if not username or not password:
        return redirect(url_for('login', error="Username and password are required", show_signup=True))
    
    if password != confirm_password:
        return redirect(url_for('login', error="Passwords do not match", show_signup=True))
    
    # Validate username
    valid, msg = validate_username(username)
    if not valid:
        return redirect(url_for('login', error=msg, show_signup=True))
    
    # Validate password
    valid, msg = validate_password(password)
    if not valid:
        return redirect(url_for('login', error=msg, show_signup=True))
    
    # Validate email
    valid, msg = validate_email(email)
    if not valid:
        return redirect(url_for('login', error=msg, show_signup=True))
    
    # Create user
    success, message = create_user(username, password, email)
    if success:
        # Auto-login after successful signup
        session['logged_in'] = True
        session['username'] = username
        db = get_db()
        user = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        session['user_id'] = user['id']
        
        # Create user directory
        user_dir = os.path.join(USERS_DIR, username)
        if not os.path.exists(user_dir):
            os.makedirs(user_dir)
        
        return redirect(url_for('home_page'))
    else:
        return redirect(url_for('login', error=message, show_signup=True))

@app.route('/logout')
def logout():
    session.clear()
    print("User logged out.")
    return redirect(url_for('login'))

# --- Page Routes ---
@app.route('/')
def home_page():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    username = session.get('username', 'krishna')
    return render_template_string("""
    <html>
<head>
      <title>Home</title>
  <style>
        body { font-family: Arial, sans-serif; background: #18191a; color: #e4e6eb; margin:0; }
        header { background: #242526; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .nav { display: flex; gap: 24px; }
        .nav a { color: #e4e6eb; text-decoration: none; font-weight: 500; padding: 8px 12px; border-radius: 6px; transition: background 0.2s; }
        .nav a:hover { background: #3a3b3c; }
        /* .nav a.active { background: #3a3b3c; }  Remove active background for home page */
        #feed-root { max-width: 600px; margin: 40px auto; background: #242526; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.12); padding: 24px; }
        .feed-card { background: #23272b; border-radius: 16px; box-shadow: 0 2px 12px rgba(0,0,0,0.18); margin-bottom: 32px; padding: 18px 18px 12px 18px; display: flex; flex-direction: column; align-items: flex-start; position: relative; transition: box-shadow 0.2s, transform 0.2s; border: 1.5px solid #313338; }
        .feed-card:hover { box-shadow: 0 4px 24px rgba(45,136,255,0.10), 0 2px 8px rgba(0,0,0,0.18); transform: translateY(-2px) scale(1.01); }
        .feed-user { font-weight: bold; color: #2d88ff; display: flex; align-items: center; gap: 8px; }
        .feed-avatar { width: 32px; height: 32px; border-radius: 50%; background: #444; object-fit: cover; margin-right: 8px; border: 2px solid #2d88ff; }
        .feed-meta { font-size: 0.95em; color: #aaa; margin-bottom: 8px; display: flex; align-items: center; gap: 10px; }
        .feed-badge { background: #2d88ff; color: #fff; font-size: 0.8em; border-radius: 6px; padding: 2px 8px; margin-left: 6px; }
        .feed-card img, .feed-card video {
          width: 100%;
          max-width: 480px;
          border-radius: 12px;
          background: #222;
          margin: 8px auto 0 auto;
          cursor: pointer;
          box-shadow: 0 1px 6px rgba(0,0,0,0.10);
          display: block;
        }
        .video-thumb-wrapper {
          position: relative;
          display: flex;
          justify-content: center;
          align-items: center;
          width: 100%;
          max-width: 480px;
          margin: 8px auto 0 auto;
        }
        .play-overlay { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); background: rgba(0,0,0,0.6); color: #fff; font-size: 2em; border-radius: 50%; padding: 8px 16px; pointer-events: none; }
        .feed-actions { display: flex; gap: 18px; margin-top: 10px; align-items: center; }
        .like-btn, .dislike-btn, .comment-btn { background: none; border: none; color: #aaa; font-size: 1.2em; cursor: pointer; display: flex; align-items: center; gap: 4px; border-radius: 6px; padding: 4px 8px; transition: background 0.15s, color 0.15s; }
        .like-btn.liked, .dislike-btn.disliked { color: #2d88ff; font-weight: bold; }
        .like-btn:hover, .dislike-btn:hover, .comment-btn:hover { background: #2d88ff22; color: #2d88ff; }
        .like-count, .dislike-count { font-size: 0.95em; color: #aaa; margin-left: 2px; }
        .spinner { display: flex; justify-content: center; align-items: center; height: 80px; }
        .spinner:after { content: ' '; display: block; width: 40px; height: 40px; border-radius: 50%; border: 6px solid #ccc; border-color: #ccc #ccc #333 #333; animation: spin 1s linear infinite; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .modal { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.82); display: flex; align-items: center; justify-content: center; z-index: 9999; }
        .modal-content {
          background: #23272b;
          padding: 0;
          border-radius: 1.2rem;
          width: 90vw;
          height: 80vh;
          min-width: 320px;
          min-height: 320px;
          max-width: 1200px;
          max-height: 92vh;
          overflow: hidden;
          box-shadow: 0 4px 32px rgba(45,136,255,0.10), 0 2px 8px rgba(0,0,0,0.18);
          position: relative;
          flex-direction: row;
          gap: 0;
          z-index: 10000;
        }
        .modal-media-col { flex: 1 1 60%; display: flex; align-items: center; justify-content: center; min-width: 0; background: #18191a; border-radius: 1.2rem 0 0 1.2rem; }
        .modal-side-col {
          flex: 1 1 40%;
          min-width: 240px;
          max-width: 420px;
          padding: 2rem 1.2rem 1.2rem 1.2rem;
          display: flex;
          flex-direction: column;
          border-radius: 0 1.2rem 1.2rem 0;
          background: #23272b;
          height: 100%;
          overflow: hidden;
        }
        @media (max-width: 900px) {
          .modal-content {
            flex-direction: column;
            width: 98vw;
            height: 92vh;
            min-width: 0;
            min-height: 0;
            border-radius: 1.2rem;
          }
          .modal-media-col, .modal-side-col {
            border-radius: 0 0 1.2rem 1.2rem;
            max-width: 100%;
            min-width: 0;
            height: auto;
          }
        }
        .modal-preview-video, .modal-media-col img {
          width: 100%;
          height: 100%;
          max-width: 100%;
          max-height: 80vh;
          min-height: 0;
          min-width: 0;
          border-radius: 12px;
          display: block;
          margin: 0 auto;
          object-fit: contain;
          background: #222;
        }
        .modal-close { 
          position: absolute; 
          top: 12px; 
          right: 18px; 
          background: rgba(0,0,0,0.5); 
          border: none; 
          color: #fff; 
          font-size: 2em; 
          cursor: pointer; 
          z-index: 10001; 
          width: 40px; 
          height: 40px; 
          border-radius: 50%; 
          display: flex; 
          align-items: center; 
          justify-content: center; 
          transition: background 0.2s ease;
        }
        .modal-close:hover {
          background: rgba(0,0,0,0.8);
        }
        
        /* Fullscreen modal compatibility */
        :fullscreen .modal,
        :-webkit-full-screen .modal,
        :-moz-full-screen .modal {
          z-index: 99999 !important;
        }
        
        :fullscreen .modal-content,
        :-webkit-full-screen .modal-content,
        :-moz-full-screen .modal-content {
          z-index: 100000 !important;
        }
        
        :fullscreen .modal-close,
        :-webkit-full-screen .modal-close,
        :-moz-full-screen .modal-close {
          z-index: 100001 !important;
        }
        
        /* Ensure modals are always on top */
        .modal {
          position: fixed !important;
          top: 0 !important;
          left: 0 !important;
          width: 100vw !important;
          height: 100vh !important;
          z-index: 99999 !important;
        }
        
        .modal-content {
          z-index: 100000 !important;
        }
        
        .modal-close {
          z-index: 100001 !important;
        }
        
        .modal-actions { display: flex; gap: 18px; margin-top: 18px; align-items: center; }
        .modal-comments-section { 
          background: #18191a;
          border-radius: 12px;
          padding: 18px;
          margin-top: 18px;
          max-width: 100%;
          box-shadow: 0 1px 6px rgba(0,0,0,0.10);
          flex: 1 1 auto;
          display: flex;
          flex-direction: column;
          min-height: 180px;
          max-height: calc(80vh - 180px);
        }
        .comments-list {
          flex: 1 1 auto;
          overflow-y: auto;
          margin-bottom: 10px;
          min-height: 0;
          max-height: none;
        }
        .comment-form {
          flex-shrink: 0;
          width: 100%;
          display: flex;
          gap: 10px;
          margin-top: 0;
          margin-bottom: 0;
          min-height: 48px;
        }
        .comment-form input {
          flex: 1;
          padding: 12px 14px;
          border-radius: 8px;
          border: 1.5px solid #333;
          background: #222;
          color: #e4e6eb;
          font-size: 1.08em;
          min-height: 44px;
        }
        .comment-form button {
          background: #2d88ff;
      color: #fff;
          border: none;
          border-radius: 8px;
          padding: 0 22px;
          font-size: 1.08em;
          min-height: 44px;
          cursor: pointer;
          font-weight: 500;
          transition: background 0.18s, color 0.18s;
        }
        .comment-form button:hover, .comment-form button:focus {
          background: #1761b0;
          color: #fff;
        }
        .comment { margin-bottom: 14px; }
        .comment-user { font-weight: bold; color: #2d88ff; }
        .comment-content { margin: 4px 0 0 0; }
        .comment-reply-btn { background: none; border: none; color: #aaa; font-size: 0.95em; cursor: pointer; margin-left: 8px; }
        .comment-reply-btn:hover { color: #2d88ff; }
        .comment-replies { margin-left: 24px; margin-top: 8px; }
        .error { color: #ff4c4c; text-align: center; margin: 16px 0; }
        @media (max-width: 700px) { #feed-root { padding: 8px; } .feed-card { padding: 10px; } }
        /* Media grid for 3 items per row */
        .media-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
        .media-grid img, .media-grid video { width: 100%; height: 120px; object-fit: cover; border-radius: 8px; background: #222; cursor: pointer; }
        /* (existing styles omitted for brevity) */
        .tab-menu { display: flex; gap: 12px; background: #242526; padding: 12px; }
        .tab-menu button { flex: 1; padding: 12px; background: #3a3b3c; color: #e4e6eb; border: none; cursor: pointer; font-size: 1em; border-radius: 6px; }
        .tab-menu button.active { background: #2d88ff; color: #fff; }
        #story-root .story-card { background: #23272b; border: 1.5px solid #313338; padding: 18px; border-radius: 12px; margin-bottom: 16px; cursor: pointer; }
        #story-root .story-title { font-weight: bold; color: #2d88ff; margin-bottom: 8px; }
        #story-modal { display: none; position: fixed; top:0; left:0; width:100vw; height:100vh; background: rgba(0,0,0,0.8); align-items: center; justify-content: center; z-index: 10000; }
        #story-modal .modal-content { background: #23272b; padding: 24px; border-radius: 12px; width: 90%; max-width: 700px; max-height: 80vh; overflow-y: auto; position: relative; }
        #story-modal .modal-close { position: absolute; top:12px; right:12px; background:none; border:none; color:#e4e6eb; font-size:1.5em; cursor:pointer; }
        #modal-title, #modal-text { color: #e4e6eb; }
        body.light-theme #modal-title, body.light-theme #modal-text { color: #23272b !important; }
  </style>
</head>
    <body>
  <header style="display: flex; align-items: center; justify-content: space-between; padding: 16px 24px;">
        <div class='nav' style="display: flex; gap: 24px;">
          <a href='/' class='home-link'>Home</a>
          <a href='/profile/{{username}}' class='active'>Profile</a>
      </div>
        <div style="display: flex; align-items: center; gap: 18px;">
        <a href='{{ url_for('logout') }}' style='color:#2d88ff;font-weight:bold;'>Logout</a>
          <button id="theme-toggle-btn" title="Toggle light/dark mode" style="background: none; border: none; color: #2d88ff; font-size: 1.5em; cursor: pointer; transition: color 0.2s;">☀️</button>
        </div>
  </header>
      <!-- TAB MENU -->
      <div class="tab-menu">
        <button id="tab-feed" class="active">Feed</button>
        <button id="tab-stories">Stories</button>
      </div>

      <!-- FEED CONTAINER -->
      <div id="feed-root">
        <div id="feed-list"></div>
        <div class="spinner" id="feed-spinner"></div>
        <div id="feed-error" class="error" style="display:none"></div>
      </div>

      <!-- STORIES CONTAINER --> <br>
      <div id="story-root"></div>
      <!-- STORY MODAL -->
      <div id="story-modal">
        <div class="modal-content">
          <button class="modal-close" onclick="closeStoryModal()">&times;</button>
          <h2 id="modal-title"></h2>
          <p id="modal-text" style="line-height:1.5em;"></p>
        </div>
      </div>

      <div id="modal-root"></div>

<script>
        let feedOffset = 0, FEED_BATCH = 20, feedEnd = false, feedLoading = false, feedCancelled = false;
        const feedList = document.getElementById('feed-list');
        const feedSpinner = document.getElementById('feed-spinner');
        const feedError = document.getElementById('feed-error');
        async function loadFeed(reset=false) {
          if (feedLoading || feedEnd || feedCancelled) return;
          feedLoading = true;
          feedSpinner.style.display = 'flex';
          feedError.style.display = 'none';
          if (reset) { feedList.innerHTML = ''; feedOffset = 0; feedEnd = false; }
          try {
            const res = await fetch(`/api/feed?offset=${feedOffset}&limit=${FEED_BATCH}`);
            let items = await res.json();
            if (res.status !== 200) throw new Error(items.error || 'Failed to load');
            // Shuffle for randomness
            for (let i = items.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [items[i], items[j]] = [items[j], items[i]]; }
            if (items.length < FEED_BATCH) feedEnd = true;
            items.forEach(item => feedList.appendChild(renderFeedCard(item)));
            feedOffset += items.length;
            // If still not filled, load more
            setTimeout(() => {
              if (!feedEnd && !feedLoading && (window.innerHeight + window.scrollY) >= document.body.offsetHeight - 10) {
                loadFeed();
              }
            }, 0);
            // Enable tabs after first successful load
            btnFeed.disabled = false;
            btnStories.disabled = false;
          } catch (e) {
            feedError.textContent = e.message;
            feedError.style.display = 'block';
          }
          feedSpinner.style.display = 'none';
          feedLoading = false;
        }
        function renderFeedCard(item) {
          if (item.type !== 'image' && item.type !== 'video') return document.createElement('div');
          const div = document.createElement('div');
          div.className = 'feed-card';
          // Add data-media-key for robust matching
          let media_key = item.url.replace(/^\/files\//, '');
          div.setAttribute('data-media-key', media_key);
          // Avatar and user/tab badge
          const meta = document.createElement('div');
          meta.className = 'feed-meta';
          meta.innerHTML = `<span class='feed-user'><img class='feed-avatar' src='https://api.dicebear.com/7.x/thumbs/svg?seed=${item.user}' alt='avatar'>@${item.user}</span>in <b>${item.tab}</b> <span class='feed-badge'>${item.type.toUpperCase()}</span>`;
          div.appendChild(meta);
          // Media
      if (item.type === 'image') {
        const img = document.createElement('img');
        img.src = item.url;
            img.loading = 'lazy';
            img.onclick = () => showMediaModal({...item, media_key});
            div.appendChild(img);
      } else if (item.type === 'video') {
        const wrapper = document.createElement('div');
            wrapper.className = 'video-thumb-wrapper';
        const img = document.createElement('img');
        img.src = item.thumb || item.url;
            img.alt = 'Video thumbnail';
            img.loading = 'lazy';
            img.onclick = () => showMediaModal({...item, media_key});
        const overlay = document.createElement('div');
        overlay.className = 'play-overlay';
        overlay.innerHTML = '►';
        wrapper.appendChild(img);
        wrapper.appendChild(overlay);
            div.appendChild(wrapper);
          }
          // Actions (like, dislike, comment)
          const actions = document.createElement('div');
          actions.className = 'feed-actions';
          // Like/dislike state (persistent)
          const likeBtn = document.createElement('button');
          likeBtn.className = 'like-btn';
          const dislikeBtn = document.createElement('button');
          dislikeBtn.className = 'dislike-btn';
          let likeCount = 0, dislikeCount = 0, userValue = 0;
          function updateLikeUI() {
            likeBtn.innerHTML = `👍 <span class='like-count'>${likeCount}</span>`;
            dislikeBtn.innerHTML = `👎 <span class='dislike-count'>${dislikeCount}</span>`;
            likeBtn.classList.toggle('liked', userValue === 1);
            dislikeBtn.classList.toggle('disliked', userValue === -1);
          }
          async function fetchLikes() {
            const res = await fetch(`/api/likes?media_key=${encodeURIComponent(media_key)}`);
            const data = await res.json();
            likeCount = data.likes;
            dislikeCount = data.dislikes;
            userValue = data.user_value;
            updateLikeUI();
          }
          likeBtn.onclick = async () => {
            if (userValue === 1) return;
            await fetch('/api/likes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key, value:1})});
            await fetchLikes();
          };
          dislikeBtn.onclick = async () => {
            if (userValue === -1) return;
            await fetch('/api/likes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key, value:-1})});
            await fetchLikes();
          };
          fetchLikes();
          const commentBtn = document.createElement('button');
          commentBtn.className = 'modal-comments-btn';
          commentBtn.innerHTML = '💬 Comments';
          // Set initial style based on theme
          function setCommentBtnTheme(btn) {
            if (document.body.classList.contains('light-theme')) {
              btn.style.background = '#f7f7fa';
              btn.style.color = '#2d88ff';
              btn.style.border = '1.5px solid #2d88ff';
            } else {
              btn.style.background = '#23272b';
              btn.style.color = '#2d88ff';
              btn.style.border = '1.5px solid #2d88ff';
            }
            btn.style.fontSize = '1.1em';
            btn.style.fontWeight = '500';
            btn.style.borderRadius = '8px';
            btn.style.padding = '10px 22px';
            btn.style.cursor = 'pointer';
            btn.style.margin = '18px 0 10px 0';
            btn.style.boxShadow = '0 2px 8px rgba(45,136,255,0.10)';
            btn.style.display = 'flex';
            btn.style.alignItems = 'center';
            btn.style.gap = '8px';
            btn.style.transition = 'background 0.18s, color 0.18s, border 0.18s';
          }
          setCommentBtnTheme(commentBtn);
          // Update on theme change
          document.addEventListener('DOMContentLoaded', function() {
            document.addEventListener('themechange', function() {
              setCommentBtnTheme(commentBtn);
            });
          });
          commentBtn.onmouseenter = function() {
            this.style.background = '#2d88ff';
            this.style.color = '#fff';
            this.style.borderColor = '#2d88ff';
          };
          commentBtn.onmouseleave = function() {
            setCommentBtnTheme(this);
          };
          commentBtn.onclick = () => showMediaModal({...item, openComments: true});
          actions.appendChild(likeBtn);
          actions.appendChild(dislikeBtn);
          actions.appendChild(commentBtn);
          div.appendChild(actions);
          return div;
        }
        // Infinite scroll
        window.onscroll = async function() {
          // Only load feed if feed tab is visible
          if (feedRoot.style.display === 'none') return;
          if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 300 && !feedEnd && !feedLoading) {
            await loadFeed();
          }
        };
        // Modal for video/image
        function showMediaModal(item) {
          // Always ensure media_key is set
          if (!item.media_key && item.url) {
            item.media_key = item.url.replace(/^\/files\//, '');
          }
          const modalRoot = document.getElementById('modal-root');
          modalRoot.innerHTML = '';
          const modal = document.createElement('div');
          modal.className = 'modal';
          const content = document.createElement('div');
          content.className = 'modal-content';
          content.style.display        = 'flex';
          content.style.flexDirection  = 'row';

          // Close button
          const closeBtn = document.createElement('button');
          closeBtn.className = 'modal-close';
          closeBtn.innerHTML = '&times;';
          // Unified close handler
          function closeModal() {
            modalRoot.innerHTML = '';
            if (item.media_key) updateFeedCardLikes(item.media_key);
          }
          closeBtn.onclick = closeModal;
          content.appendChild(closeBtn);
          // Media column
          const mediaCol = document.createElement('div');
          mediaCol.className = 'modal-media-col';
          if (item.type === 'image') {
            const img = document.createElement('img');
            img.src = item.url;
            img.style.maxWidth = '100%';
            img.style.maxHeight = '80vh';
            img.style.borderRadius = '12px';
            img.style.display = 'block';
            img.style.margin = '0 auto';
            // Dynamic theme for modal image
            function setModalImageTheme(img) {
              if (document.body.classList.contains('light-theme')) {
                img.style.background = '#fff';
                img.style.border = '2px solid #e4e6eb';
                img.style.boxShadow = '0 2px 12px #2d88ff11';
              } else {
                img.style.background = '#222';
                img.style.border = 'none';
                img.style.boxShadow = '0 1px 6px rgba(0,0,0,0.10)';
              }
            }
            setModalImageTheme(img);
            document.addEventListener('themechange', function() { setModalImageTheme(img); });
            mediaCol.appendChild(img);
          } else if (item.type === 'video') {
            mediaCol.innerHTML = `<video src='${item.url}' class='modal-preview-video' controls autoplay></video>`;
          }
          content.appendChild(mediaCol);
          // Side column (actions + comments)
          const sideCol = document.createElement('div');
          sideCol.className = 'modal-side-col';
          // Actions (like, dislike, comment)
          const actions = document.createElement('div');
          actions.className = 'modal-actions';
          const likeBtn = document.createElement('button');
          likeBtn.className = 'like-btn';
          const dislikeBtn = document.createElement('button');
          dislikeBtn.className = 'dislike-btn';
          let likeCount = 0, dislikeCount = 0, userValue = 0;
          function updateLikeUI() {
            likeBtn.innerHTML = `👍 <span class='like-count'>${likeCount}</span>`;
            dislikeBtn.innerHTML = `👎 <span class='dislike-count'>${dislikeCount}</span>`;
            likeBtn.classList.toggle('liked', userValue === 1);
            dislikeBtn.classList.toggle('disliked', userValue === -1);
          }
          async function fetchLikes() {
            const res = await fetch(`/api/likes?media_key=${encodeURIComponent(item.media_key)}`);
            const data = await res.json();
            likeCount = data.likes;
            dislikeCount = data.dislikes;
            userValue = data.user_value;
            updateLikeUI();
          }
          likeBtn.onclick = async () => {
            if (userValue === 1) return;
            await fetch('/api/likes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key:item.media_key, value:1})});
            await fetchLikes();
          };
          dislikeBtn.onclick = async () => {
            if (userValue === -1) return;
            await fetch('/api/likes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key:item.media_key, value:-1})});
            await fetchLikes();
          };
          fetchLikes();
          actions.appendChild(likeBtn);
          actions.appendChild(dislikeBtn);
          sideCol.appendChild(actions);
          // Always show comments section
          showCommentsSection(sideCol, item);
          content.appendChild(sideCol);
          modal.appendChild(content);
          modalRoot.appendChild(modal);
          // Helper to update feed card likes/dislikes if modal is closed
          function updateFeedCardLikes(media_key) {
            console.log('updateFeedCardLikes called for media_key:', media_key);
            // Find the feed card with this media_key using the data attribute
            const cards = document.querySelectorAll('.feed-card[data-media-key]');
            cards.forEach(card => {
              const cardKey = card.getAttribute('data-media-key');
              if (cardKey === media_key) {
                console.log('Found matching feed card for media_key:', media_key, card);
                // Re-fetch likes for this card
                const likeBtn = card.querySelector('.like-btn');
                const dislikeBtn = card.querySelector('.dislike-btn');
                if (likeBtn && dislikeBtn) {
                  fetch(`/api/likes?media_key=${encodeURIComponent(media_key)}`)
                    .then(res => res.json())
                    .then(data => {
                      likeBtn.innerHTML = `👍 <span class='like-count'>${data.likes}</span>`;
                      dislikeBtn.innerHTML = `👎 <span class='dislike-count'>${data.dislikes}</span>`;
                      likeBtn.classList.toggle('liked', data.user_value === 1);
                      dislikeBtn.classList.toggle('disliked', data.user_value === -1);
                      console.log('Updated like/dislike UI for card:', card, data);
                    });
                }
              } else {
                // For debugging, log non-matching keys
                console.log('No match: cardKey', cardKey, 'vs media_key', media_key);
              }
            });
          }
          modal.onclick = e => {
            if (e.target === modal) closeModal();
          };
        }
        // Recursive comments section (persistent)
        async function showCommentsSection(parentCol, item) {
          if (!item.media_key) {
            let commentsDiv = document.createElement('div');
            commentsDiv.className = 'modal-comments-section';
            commentsDiv.innerHTML = '<b>Comments</b><div style="color:red;">Comments unavailable: media_key missing.</div>';
            if (!parentCol.querySelector('.modal-comments-section'))
              parentCol.appendChild(commentsDiv);
            else
              parentCol.replaceChild(commentsDiv, parentCol.querySelector('.modal-comments-section'));
            return;
          }
          let commentsDiv = document.createElement('div');
          commentsDiv.className = 'modal-comments-section';
          commentsDiv.innerHTML = '<b>Comments</b>';
          // Comments list container
          const commentsList = document.createElement('div');
          commentsList.className = 'comments-list';
          let comments = [];
          try {
            const res = await fetch(`/api/comments?media_key=${encodeURIComponent(item.media_key)}`);
            comments = await res.json();
          } catch (e) { comments = []; }
          // Render comments recursively
          function renderComments(comments, parentDiv) {
            comments.forEach((comment, idx) => {
              const commentDiv = document.createElement('div');
              commentDiv.className = 'comment';
              commentDiv.innerHTML = `<span class='comment-user'>@${comment.user}</span><span class='comment-content'>: ${comment.text}</span>`;
              // Reply button
              const replyBtn = document.createElement('button');
              replyBtn.className = 'comment-reply-btn';
              replyBtn.textContent = 'Reply';
              replyBtn.onclick = () => {
                const replyForm = document.createElement('form');
                replyForm.className = 'comment-form';
                const input = document.createElement('input');
                input.type = 'text';
                input.placeholder = 'Write a reply...';
                const submitBtn = document.createElement('button');
                submitBtn.type = 'submit';
                submitBtn.textContent = 'Reply';
                replyForm.appendChild(input);
                replyForm.appendChild(submitBtn);
                replyForm.onsubmit = async (e) => {
                  e.preventDefault();
                  if (!input.value.trim() || !item.media_key) return;
                  await fetch('/api/comments', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key:item.media_key, text:input.value, parent_id:comment.id})});
                  showCommentsSection(parentCol, item);
                };
                commentDiv.appendChild(replyForm);
              };
              commentDiv.appendChild(replyBtn);
              // Render replies recursively
              if (comment.replies && comment.replies.length > 0) {
                const repliesDiv = document.createElement('div');
                repliesDiv.className = 'comment-replies';
                renderComments(comment.replies, repliesDiv);
                commentDiv.appendChild(repliesDiv);
              }
              parentDiv.appendChild(commentDiv);
            });
          }
          renderComments(comments, commentsList);
          // Add new comment form
          const newCommentForm = document.createElement('form');
          newCommentForm.className = 'comment-form';
          const input = document.createElement('input');
          input.type = 'text';
          input.placeholder = 'Write a comment...';
          // Dynamic theme for comment input
          function setInputTheme(input) {
            if (document.body.classList.contains('light-theme')) {
              input.style.background = '#fff';
              input.style.color = '#23272b';
              input.style.borderColor = '#2d88ff';
            } else {
              input.style.background = '#222';
              input.style.color = '#e4e6eb';
              input.style.borderColor = '#333';
            }
          }
          setInputTheme(input);
          document.addEventListener('themechange', function() { setInputTheme(input); });
          const submitBtn = document.createElement('button');
          submitBtn.type = 'submit';
          submitBtn.textContent = 'Post';
          newCommentForm.appendChild(input);
          newCommentForm.appendChild(submitBtn);
          newCommentForm.onsubmit = async (e) => {
            e.preventDefault();
            if (!input.value.trim() || !item.media_key) return;
            await fetch('/api/comments', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key:item.media_key, text:input.value})});
            showCommentsSection(parentCol, item);
          };
          commentsDiv.appendChild(commentsList);
          commentsDiv.appendChild(newCommentForm);
          // Replace or append
          if (!parentCol.querySelector('.modal-comments-section'))
            parentCol.appendChild(commentsDiv);
          else
            parentCol.replaceChild(commentsDiv, parentCol.querySelector('.modal-comments-section'));
        }
        // Helper to always pass media_key
        function openMediaModalWithKey(item) {
          let media_key = item && item.url ? item.url.replace(/^\/files\//, '') : undefined;
          console.log('openMediaModalWithKey called', {item, media_key, showMediaModal: typeof window.showMediaModal});
          showMediaModal({...item, media_key});
        }
        // Initial load
        loadFeed(true);
        // Make the modal globally available for profile page reuse
        window.showMediaModal = showMediaModal;
        // Inject light theme CSS for home page
        const lightStyle = document.createElement('style');
        lightStyle.innerHTML = `
          body.light-theme, body.light-theme .modal-content, body.light-theme .modal, body.light-theme #profile-root, body.light-theme #feed-root, body.light-theme #story-root, body.light-theme #story-modal {
            background: #f7f7fa !important;
            color: #23272b !important;
          }
          body.light-theme .feed-card, body.light-theme .modal-content, body.light-theme .modal-side-col, body.light-theme .modal-media-col, body.light-theme .modal-comments-section, body.light-theme #feed-root, body.light-theme #story-root .story-card, body.light-theme #story-modal .modal-content {
            background: #fff !important;
            color: #23272b !important;
            border-color: #e4e6eb !important;
            box-shadow: 0 2px 8px #0001 !important;
          }
          body.light-theme .nav a, body.light-theme .nav a.active, body.light-theme .nav a:hover {
            /* color: #2d88ff !important; */
            /* background: #e4e6eb !important; */
          }
          body.light-theme .like-btn, body.light-theme .dislike-btn, body.light-theme .comment-btn {
            background: #f7f7fa !important;
            color: #2d88ff !important;
            border-color: #2d88ff !important;
          }
          body.light-theme .like-btn.liked, body.light-theme .dislike-btn.disliked {
            background: #2d88ff !important;
            color: #fff !important;
          }
          body.light-theme .modal-close {
            background: #e4e6eb !important;
            color: #2d88ff !important;
          }
          body.light-theme .modal-close:hover {
            background: #2d88ff !important;
            color: #fff !important;
          }
        `;
        if (!document.head.contains(lightStyle)) document.head.appendChild(lightStyle);
        // --- THEME TOGGLE (Light/Dark) ---
        function applyTheme(theme) {
          if (theme === 'light') {
            document.body.classList.add('light-theme');
          } else {
            document.body.classList.remove('light-theme');
          }
        }
        function getTheme() {
          return localStorage.getItem('theme') || 'dark';
        }
        function setTheme(theme) {
          localStorage.setItem('theme', theme);
          applyTheme(theme);
          // Dispatch a custom event so all theme-aware components can update
          document.dispatchEvent(new Event('themechange'));
          // Update all modal-comments-btn buttons
          document.querySelectorAll('.modal-comments-btn').forEach(btn => {
            if (document.body.classList.contains('light-theme')) {
              btn.style.background = '#f7f7fa';
              btn.style.color = '#2d88ff';
              btn.style.border = '1.5px solid #2d88ff';
            } else {
              btn.style.background = '#23272b';
              btn.style.color = '#2d88ff';
              btn.style.border = '1.5px solid #2d88ff';
            }
            btn.style.fontSize = '1.1em';
            btn.style.fontWeight = '500';
            btn.style.borderRadius = '8px';
            btn.style.padding = '10px 22px';
            btn.style.cursor = 'pointer';
            btn.style.margin = '18px 0 10px 0';
            btn.style.boxShadow = '0 2px 8px rgba(45,136,255,0.10)';
            btn.style.display = 'flex';
            btn.style.alignItems = 'center';
            btn.style.gap = '8px';
            btn.style.transition = 'background 0.18s, color 0.18s, border 0.18s';
          });
        }
        function getTheme() {
          return localStorage.getItem('theme') || 'dark';
        }
        function setTheme(theme) {
          localStorage.setItem('theme', theme);
          applyTheme(theme);
          // Dispatch a custom event so all theme-aware components can update
          document.dispatchEvent(new Event('themechange'));
          // Update all modal-comments-btn buttons
          document.querySelectorAll('.modal-comments-btn').forEach(btn => {
            if (document.body.classList.contains('light-theme')) {
              btn.style.background = '#f7f7fa';
              btn.style.color = '#2d88ff';
              btn.style.border = '1.5px solid #2d88ff';
            } else {
              btn.style.background = '#23272b';
              btn.style.color = '#2d88ff';
              btn.style.border = '1.5px solid #2d88ff';
            }
            btn.style.fontSize = '1.1em';
            btn.style.fontWeight = '500';
            btn.style.borderRadius = '8px';
            btn.style.padding = '10px 22px';
            btn.style.cursor = 'pointer';
            btn.style.margin = '18px 0 10px 0';
            btn.style.boxShadow = '0 2px 8px rgba(45,136,255,0.10)';
            btn.style.display = 'flex';
            btn.style.alignItems = 'center';
            btn.style.gap = '8px';
            btn.style.transition = 'background 0.18s, color 0.18s, border 0.18s';
          });
        }
        document.addEventListener('DOMContentLoaded', function() {
          let btn = document.getElementById('theme-toggle-btn');
          if (btn) {
            btn.onclick = function() {
              const current = getTheme();
              if (current === 'dark') {
                setTheme('light');
                btn.innerHTML = '☀️';
              } else {
                setTheme('dark');
                btn.innerHTML = '🌙';
              }
            };
            // Set initial icon
            if (getTheme() === 'light') btn.innerHTML = '☀️';
            else btn.innerHTML = '🌙';
          }
          applyTheme(getTheme());
        });

        // Toggle tabs
        const btnFeed = document.getElementById('tab-feed'),
              btnStories = document.getElementById('tab-stories'),
              feedRoot = document.getElementById('feed-root'),
              storyRoot = document.getElementById('story-root');

        // Disable tabs initially
        btnFeed.disabled = true;
        btnStories.disabled = true;

        function showFeed() {
          feedCancelled = false;
          btnFeed.classList.add('active'); btnStories.classList.remove('active');
          feedRoot.style.display = ''; storyRoot.style.display = 'none';
        }
        function showStories() {
          feedCancelled = true;
          btnStories.classList.add('active'); btnFeed.classList.remove('active');
          storyRoot.style.display = ''; feedRoot.style.display = 'none';
          if (!storyRoot.hasChildNodes()) loadStories();
        }
        btnFeed.onclick = showFeed;
        btnStories.onclick = showStories;

        // Enable tabs after feed is loaded
        async function loadFeed(reset=false) {
          if (feedLoading || feedEnd || feedCancelled) return;
          feedLoading = true;
          feedSpinner.style.display = 'flex';
          feedError.style.display = 'none';
          if (reset) { feedList.innerHTML = ''; feedOffset = 0; feedEnd = false; }
          try {
            const res = await fetch(`/api/feed?offset=${feedOffset}&limit=${FEED_BATCH}`);
            let items = await res.json();
            if (res.status !== 200) throw new Error(items.error || 'Failed to load');
            // Shuffle for randomness
            for (let i = items.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [items[i], items[j]] = [items[j], items[i]]; }
            if (items.length < FEED_BATCH) feedEnd = true;
            items.forEach(item => feedList.appendChild(renderFeedCard(item)));
            feedOffset += items.length;
            // If still not filled, load more
            setTimeout(() => {
              if (!feedEnd && !feedLoading && (window.innerHeight + window.scrollY) >= document.body.offsetHeight - 10) {
                loadFeed();
              }
            }, 0);
            // Enable tabs after first successful load
            btnFeed.disabled = false;
            btnStories.disabled = false;
          } catch (e) {
            feedError.textContent = e.message;
            feedError.style.display = 'block';
          }
          feedSpinner.style.display = 'none';
          feedLoading = false;
        }
        showFeed();  // initial
        // --- Stories logic ---
        async function loadStories() {
          const storyRoot = document.getElementById('story-root');
          if (storyRoot) {
            storyRoot.style.maxWidth = '700px';
            storyRoot.style.margin = '40px auto';
            storyRoot.style.background = '#242526';
            storyRoot.style.borderRadius = '12px';
            storyRoot.style.boxShadow = '0 2px 8px rgba(0,0,0,0.12)';
            storyRoot.style.padding = '24px';
            storyRoot.style.boxSizing = 'border-box';
          }
          storyRoot.innerHTML = '<div class="spinner"></div>';
          try {
            const res = await fetch('/api/stories');
            let list = await res.json();
            storyRoot.innerHTML = '';
// … inside loadStories(), after `storyRoot.innerHTML = '';`
list.forEach(story => {
  // Create card wrapper
  const card = document.createElement('div');
  card.className = 'story-card';
  card.style = `
    display: flex;
    flex-direction: column;
    background: #23272b;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 24px;
    color: #e4e6eb;
    cursor: pointer;
    transition: background 0.2s;
  `;
  card.onmouseenter = () => card.style.background = '#2d2e31';
  card.onmouseleave = () => card.style.background = '#23272b';
  card.onclick = () => openStoryModal(story);

// 1) Header: Avatar + Username
const header = document.createElement('div');
header.style = 'display:flex; align-items:center; gap:12px; margin-bottom:12px;';
const avatar = document.createElement('img');
avatar.src   = `https://api.dicebear.com/7.x/thumbs/svg?seed=${story.avatar_seed}`;
avatar.alt   = `@${story.user}`;
avatar.style = 'width:36px; height:36px; border-radius:50%; object-fit:cover;';
const username = document.createElement('span');
username.textContent = `@${story.user}`;
username.style = 'font-weight:600; color:#2d88ff; font-size:0.95em;';
header.append(avatar, username);
card.append(header);


  // 2) Title + Excerpt
  const title = document.createElement('div');
  title.className = 'story-title';
  title.innerHTML = `
    <strong>${story.name || story.title || 'Untitled'}</strong>
    <span style="font-size:0.85em; color:#aaa;">by ${story.user || '—'}</span>
  `;
  title.style.marginBottom = '8px';
  card.append(title);

  const excerpt = document.createElement('div');
  let text = story.description
    || (Array.isArray(story.nodes) && story.nodes[0]?.content)
    || '';
  if (text.length > 120) text = text.slice(0, 120) + '…';
  excerpt.textContent = text;
  excerpt.style = 'color:#aaa; font-size:0.9em; margin-bottom:12px;';
  card.append(excerpt);

  // 3) Like / Dislike buttons
  const actions = document.createElement('div');
  actions.style = 'display:flex; gap:12px;';
  // Like
  const likeBtn = document.createElement('button');
  likeBtn.className = 'like-btn';
  likeBtn.innerHTML = `👍 <span class="like-count">${story.likeCount||0}</span>`;
  likeBtn.style = `
    background:#3a3b3c;
    color:#e4e6eb;
    border:none;
    padding:8px 14px;
    border-radius:6px;
    cursor:pointer;
    font-size:0.9em;
  `;
  likeBtn.onclick = e => {
    e.stopPropagation();
    toggleLike(story.id, likeBtn);
  };
  // Dislike
  const dislikeBtn = document.createElement('button');
  dislikeBtn.className = 'dislike-btn';
  dislikeBtn.innerHTML = `👎 <span class="dislike-count">${story.dislikeCount||0}</span>`;
  dislikeBtn.style = likeBtn.style;
  dislikeBtn.onclick = e => {
    e.stopPropagation();
    toggleDislike(story.id, dislikeBtn);
  };

  actions.append(likeBtn, dislikeBtn);
  card.append(actions);

  // Append card to DOM
  storyRoot.appendChild(card);
});

          } catch(e) {
            storyRoot.innerHTML = `<div class="error">Failed to load stories: ${e.message}</div>`;
          }
        }

function openStoryModal(story) {
  const modal   = document.getElementById('story-modal');
  const titleEl = document.getElementById('modal-title');
  const textEl  = document.getElementById('modal-text');
  window.storyNavHistory = [];

  // Build nodes lookup
  const nodes     = Array.isArray(story.nodes) ? story.nodes : [];
  const nodesById = {};
  nodes.forEach((n, i) => {
    const id      = (n.id !== undefined ? n.id : i);
    const display = n.label || n.name || n.title || '';
    nodesById[id] = { ...n, __idx: i, __display: display };
  });

  // Build adjacency list of connections
  const adj = {};
  (Array.isArray(story.connections) ? story.connections : []).forEach(conn => {
    const src = conn.from;
    if (!adj[src]) adj[src] = [];
    adj[src].push(conn);
  });

  // Remove sidebar if present
  let nodeList = modal.querySelector('.story-node-list');
  if (nodeList) nodeList.remove();
  if (modal.querySelector('.modal-content')) {
    modal.querySelector('.modal-content').style.paddingLeft = '';
  }

  // Find (or create) navigation container under the modal text
  let nav = modal.querySelector('.story-nav');
  if (!nav) {
    nav = document.createElement('div');
    nav.className = 'story-nav';
    Object.assign(nav.style, {
      marginTop: '16px',
      flexWrap:  'wrap',
      gap:       '8px',
      justifyContent: 'center'
    });
    textEl.parentNode.appendChild(nav);
  }

  // --- Story Navigation History ---
  if (!window.storyNavHistory) window.storyNavHistory = [];
  // Start at the first node
  let currentNodeId = nodes.length > 0 ? nodes[0].id : null;
  if (window.storyNavHistory.length > 0) {
    currentNodeId = window.storyNavHistory[window.storyNavHistory.length - 1];
  }

  function renderStoryNode(nodeId, isBackNav) {
    nav.innerHTML = '';
    if (!nodes.length) {
      titleEl.textContent = story.title || story.name || 'Story';
      textEl.textContent = '(No nodes in this story)';
      return;
    }
    const node = nodesById[nodeId];
    const display = node?.label || node?.name || node?.title || node?.id || '(Untitled Node)';
    // Update title + node label
    titleEl.textContent = `${story.title || story.name || 'Story'} — ${display}`;
    textEl.style.whiteSpace = 'pre-wrap';
    textEl.textContent  = node?.content || '';

    const modalContent = textEl.parentNode;
    // Style the modal node view like profile page
    textEl.parentNode.style.background = '#23272b';
    textEl.parentNode.style.padding = '32px';
    textEl.parentNode.style.borderRadius = '16px';
    textEl.parentNode.style.maxWidth = '600px';
    textEl.parentNode.style.margin = '40px auto';
    textEl.parentNode.style.textAlign = 'center';
    textEl.parentNode.style.boxShadow = '0 2px 8px #2d88ff22';
    modalContent.scrollTop = 0;

    // Manage navigation history
    if (!isBackNav) {
      if (window.storyNavHistory.length === 0 || window.storyNavHistory[window.storyNavHistory.length - 1] !== nodeId) {
        window.storyNavHistory.push(nodeId);
      }
    }
    // Back button
    if (window.storyNavHistory.length > 1) {
      const backBtn = document.createElement('button');
      backBtn.textContent = '← Back';
      // Uniform button style for all nav buttons
      backBtn.style.background = '#2d88ff';
      backBtn.style.color = '#fff';
      backBtn.style.border = 'none';
      backBtn.style.borderRadius = '8px';
      backBtn.style.padding = '0 32px';
      backBtn.style.fontSize = '1.1em';
      backBtn.style.fontWeight = '600';
      backBtn.style.cursor = 'pointer';
      backBtn.style.boxShadow = '0 2px 8px #2d88ff22';
      backBtn.style.minWidth = '120px';
      backBtn.style.height = '48px';
      backBtn.style.lineHeight = '48px';
      backBtn.style.margin = '4px 8px 4px 0';
      backBtn.style.display = 'inline-block';
      backBtn.style.verticalAlign = 'middle';
      backBtn.onclick = () => {
        if (window.storyNavHistory.length > 1) {
          window.storyNavHistory.pop();
          const prevNodeId = window.storyNavHistory[window.storyNavHistory.length - 1];
          renderStoryNode(prevNodeId, true);
        }
      };
      nav.appendChild(backBtn);
    }
    // Outgoing connections
    const conns = adj[nodeId] || [];
    if (conns.length === 0) {
      // If no further paths, show only a close button
      const btn = document.createElement('button');
      btn.textContent = 'Close Story';
      // Uniform button style for all nav buttons
      btn.style.background = '#2d88ff';
      btn.style.color = '#fff';
      btn.style.border = 'none';
      btn.style.borderRadius = '8px';
      btn.style.padding = '0 32px';
      btn.style.fontSize = '1.1em';
      btn.style.fontWeight = '600';
      btn.style.cursor = 'pointer';
      btn.style.boxShadow = '0 2px 8px #2d88ff22';
      btn.style.minWidth = '120px';
      btn.style.height = '48px';
      btn.style.lineHeight = '48px';
      btn.style.margin = '4px 8px 4px 0';
      btn.style.display = 'inline-block';
      btn.style.verticalAlign = 'middle';
      btn.onclick = closeStoryModal;
      nav.appendChild(btn);
    } else {
      // Otherwise, build one button per outgoing connection
      conns.forEach(conn => {
        const target = nodesById[conn.to];
        let nodeLabel = '';
        if (target) nodeLabel = target.label || target.name || target.title || target.id || '';
        const label = conn.label || nodeLabel;
        const btn = document.createElement('button');
        btn.textContent = label;
        // Uniform button style for all nav buttons
        btn.style.background = '#2d88ff';
        btn.style.color = '#fff';
        btn.style.border = 'none';
        btn.style.borderRadius = '8px';
        btn.style.padding = '0 32px';
        btn.style.fontSize = '1.1em';
        btn.style.fontWeight = '600';
        btn.style.cursor = 'pointer';
        btn.style.boxShadow = '0 2px 8px #2d88ff22';
        btn.style.minWidth = '120px';
        btn.style.height = '48px';
        btn.style.lineHeight = '48px';
        btn.style.margin = '4px 8px 4px 0';
        btn.style.display = 'inline-block';
        btn.style.verticalAlign = 'middle';
        btn.onclick = () => renderStoryNode(target.id, false);
        nav.appendChild(btn);
      });
    }
  }

  // Keyboard navigation (optional, simple left/right/back)
  function handleKey(e) {
    if (modal.style.display !== 'flex') return;
    if (e.key === 'ArrowLeft' && window.storyNavHistory.length > 1) {
      window.storyNavHistory.pop();
      const prevNodeId = window.storyNavHistory[window.storyNavHistory.length - 1];
      renderStoryNode(prevNodeId, true);
    } else if (e.key === 'Escape') {
      closeStoryModal();
    }
  }
  document.addEventListener('keydown', handleKey);
  // Remove event on close
  modal._removeKeyListener = () => document.removeEventListener('keydown', handleKey);

  // Display the modal and render the first node
  modal.style.display = 'flex';
  window.storyNavHistory = [currentNodeId];
  renderStoryNode(currentNodeId, false);
}

function closeStoryModal() {
  const modal = document.getElementById('story-modal');
  modal.style.display = 'none';
  if (modal._removeKeyListener) modal._removeKeyListener();
}

  function closeStoryModal() {
    document.getElementById('story-modal').style.display = 'none';
  }
</script>
    </body>
    </html>
    """, username=username)

@app.route('/profile/<username>')
def profile_page(username):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template_string("""
    <html>
    <head>
      <title>Profile</title>
      <style>
        body { font-family: Arial, sans-serif; background: #18191a; color: #e4e6eb; margin:0; }
        header { background: #242526; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .nav { display: flex; gap: 24px; }
        .nav a { color: #e4e6eb; text-decoration: none; font-weight: 500; padding: 8px 12px; border-radius: 6px; transition: background 0.2s; }
        .nav a:hover { background: #3a3b3c; }
        /* .nav a.active { background: #3a3b3c; }  Remove active background for home page */
        #profile-root { max-width: 700px; margin: 40px auto; background: #242526; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.12); padding: 24px; }
        #profile-root { box-shadow: 0 4px 32px rgba(45,136,255,0.10), 0 2px 8px rgba(0,0,0,0.18); border-radius: 18px; }
        .tab-list { 
          display: flex; 
          gap: 8px; 
          margin-bottom: 24px; 
          padding: 16px;
          background: #18191a;
          border-radius: 12px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.12);
          flex-wrap: wrap;
          border: 1px solid #313338;
          transition: all 0.3s ease;
        }
        .tab-btn { 
          background: #23272b; 
          color: #e4e6eb; 
          border: 2px solid #313338; 
          border-radius: 10px; 
          padding: 12px 18px; 
          cursor: pointer; 
          font-weight: 600;
          font-size: 0.95em;
          transition: all 0.2s ease;
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 120px;
          justify-content: center;
          position: relative;
          box-shadow: 0 2px 4px rgba(0,0,0,0.1);
          user-select: none;
        }
        .tab-btn:hover { 
          background: #2d88ff; 
          color: #fff; 
          border-color: #2d88ff;
          transform: translateY(-1px);
          box-shadow: 0 4px 12px rgba(45,136,255,0.3);
        }
        .tab-btn.active { 
          background: #2d88ff; 
          color: #fff; 
          border-color: #2d88ff;
          box-shadow: 0 4px 16px rgba(45,136,255,0.4);
          transform: translateY(-1px);
          animation: tabPulse 0.3s ease;
        }
        @keyframes tabPulse {
          0% { transform: translateY(-1px) scale(1); }
          50% { transform: translateY(-1px) scale(1.05); }
          100% { transform: translateY(-1px) scale(1); }
        }
        .tab-btn .tab-icon {
          font-size: 1.2em;
          opacity: 0.9;
          transition: transform 0.2s ease;
        }
        .tab-btn:hover .tab-icon {
          transform: scale(1.1);
        }
        .tab-btn .tab-count {
          background: rgba(255,255,255,0.2);
          color: #fff;
          border-radius: 12px;
          padding: 2px 8px;
          font-size: 0.8em;
          font-weight: 700;
          min-width: 20px;
          text-align: center;
          margin-left: 4px;
          transition: all 0.2s ease;
        }
        .tab-btn.active .tab-count {
          background: rgba(255,255,255,0.3);
          transform: scale(1.1);
        }
        .tab-btn .tab-type {
          font-size: 0.75em;
          opacity: 0.7;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          margin-top: 2px;
          transition: opacity 0.2s ease;
        }
        .tab-btn:hover .tab-type,
        .tab-btn.active .tab-type {
          opacity: 0.9;
        }
        .tab-add-btn {
          background: #23272b;
          color: #2d88ff;
          border: 2px dashed #2d88ff;
          border-radius: 10px;
          padding: 12px 18px;
          cursor: pointer;
          font-weight: 600;
          font-size: 0.95em;
          transition: all 0.2s ease;
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 120px;
          justify-content: center;
          opacity: 0.8;
          user-select: none;
        }
        .tab-add-btn:hover {
          background: #2d88ff;
          color: #fff;
          border-color: #2d88ff;
          opacity: 1;
          transform: translateY(-1px);
          box-shadow: 0 4px 12px rgba(45,136,255,0.3);
        }
        .tab-section-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 16px;
          padding: 0 4px;
          width: 100%;
        }
        .tab-section-title {
          font-size: 1.1em;
          font-weight: 600;
          color: #e4e6eb;
        }
        .tab-section-subtitle {
          font-size: 0.9em;
          color: #aaa;
          margin-top: 4px;
        }
        .tab-search {
          background: #23272b;
          border: 1px solid #313338;
          border-radius: 8px;
          padding: 8px 12px;
          color: #e4e6eb;
          font-size: 0.9em;
          min-width: 200px;
          transition: border-color 0.2s ease;
        }
        .tab-search:focus {
          outline: none;
          border-color: #2d88ff;
          box-shadow: 0 0 0 2px rgba(45,136,255,0.2);
        }
        .tab-search::placeholder {
          color: #aaa;
        }
        .tab-btn.hidden {
          display: none;
        }
        @media (max-width: 768px) {
          .tab-list {
            padding: 12px;
            gap: 6px;
          }
          .tab-btn, .tab-add-btn {
            min-width: 100px;
            padding: 10px 14px;
            font-size: 0.9em;
          }
          .tab-btn .tab-icon {
            font-size: 1.1em;
          }
          .tab-section-header {
            flex-direction: column;
            align-items: flex-start;
            gap: 8px;
          }
          .tab-search {
            min-width: 100%;
            margin-top: 8px;
          }
        }
        .spinner { display: flex; justify-content: center; align-items: center; height: 80px; }
        .spinner:after { content: ' '; display: block; width: 40px; height: 40px; border-radius: 50%; border: 6px solid #ccc; border-color: #ccc #ccc #333 #333; animation: spin 1s linear infinite; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .media-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 18px; }
        .media-grid img, .media-grid video { width: 100%; aspect-ratio: 1/1; object-fit: cover; border-radius: 12px; background: #222; cursor: pointer; box-shadow: 0 1px 6px rgba(0,0,0,0.10); transition: box-shadow 0.18s, transform 0.18s; display: block; }
        .media-grid img:hover, .media-grid video:hover { box-shadow: 0 4px 16px rgba(45,136,255,0.18); transform: scale(1.04); }
        .video-thumb-wrapper { position: relative; display: flex; justify-content: center; align-items: center; width: 100%; aspect-ratio: 1/1; background: #222; border-radius: 12px; box-shadow: 0 1px 6px rgba(0,0,0,0.10); margin: 0; transition: box-shadow 0.18s, transform 0.18s; }
        .video-thumb-wrapper:hover { box-shadow: 0 4px 16px rgba(45,136,255,0.18); transform: scale(1.04); }
        .album-card { background: #3a3b3c; border-radius: 8px; padding: 12px; min-width: 120px; max-width: 160px; cursor: pointer; margin: 8px; display: inline-block; position: relative; transition: all 0.2s ease; }
        .album-card:hover { background: #4a4b4c; transform: translateY(-2px); box-shadow: 0 4px 12px rgba(45,136,255,0.2); }
        .album-card h4 { margin: 0 0 8px 0; font-size: 1em; color: #2d88ff; }
        .album-upload-btn { position: absolute; top: 5px; right: 5px; background: #28a745; color: #fff; border: none; border-radius: 50%; width: 25px; height: 25px; font-size: 12px; cursor: pointer; display: none; z-index: 10; transition: all 0.2s ease; }
        .album-upload-btn:hover { background: #218838; transform: scale(1.1); }
        .play-overlay { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); background: rgba(0,0,0,0.6); color: #fff; font-size: 2em; border-radius: 50%; padding: 8px 16px; pointer-events: none; }
        .modal { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.82); display: flex; align-items: center; justify-content: center; z-index: 9999; }
        .modal-content {
          background: #23272b;
          padding: 0;
          border-radius: 1.2rem;
          width: 90vw;
          height: 80vh;
          min-width: 320px;
          min-height: 320px;
          max-width: 1200px;
          max-height: 92vh;
          overflow: hidden;
          box-shadow: 0 4px 32px rgba(45,136,255,0.10), 0 2px 8px rgba(0,0,0,0.18);
          position: relative;
          flex-direction: row;
          gap: 0;
          z-index: 10000;
        }
        .modal-media-col { flex: 1 1 60%; display: flex; align-items: center; justify-content: center; min-width: 0; background: #18191a; border-radius: 1.2rem 0 0 1.2rem; }
        .modal-side-col {
          flex: 1 1 40%;
          min-width: 240px;
          max-width: 420px;
          padding: 2rem 1.2rem 1.2rem 1.2rem;
          display: flex;
          flex-direction: column;
          border-radius: 0 1.2rem 1.2rem 0;
          background: #23272b;
          height: 100%;
          overflow: hidden;
        }
        @media (max-width: 900px) {
          .modal-content {
            flex-direction: column;
            width: 98vw;
            height: 92vh;
            min-width: 0;
            min-height: 0;
            border-radius: 1.2rem;
          }
          .modal-media-col, .modal-side-col {
            border-radius: 0 0 1.2rem 1.2rem;
            max-width: 100%;
            min-width: 0;
            height: auto;
          }
        }
        .modal-preview-video, .modal-media-col img {
          width: 100%;
          height: 100%;
          max-width: 100%;
          max-height: 80vh;
          min-height: 0;
          min-width: 0;
          border-radius: 12px;
          display: block;
          margin: 0 auto;
          object-fit: contain;
          background: #222;
        }
        .modal-close { 
          position: absolute; 
          top: 12px; 
          right: 18px; 
          background: rgba(0,0,0,0.5); 
          border: none; 
          color: #fff; 
          font-size: 2em; 
          cursor: pointer; 
          z-index: 10001; 
          width: 40px; 
          height: 40px; 
          border-radius: 50%; 
          display: flex; 
          align-items: center; 
          justify-content: center; 
          transition: background 0.2s ease;
        }
        .modal-close:hover {
          background: rgba(0,0,0,0.8);
        }
        .modal-actions { display: flex; gap: 18px; margin-top: 18px; align-items: center; }
        .modal-comments-section { 
          background: #18191a;
          border-radius: 12px;
          padding: 18px;
          margin-top: 18px;
          max-width: 100%;
          box-shadow: 0 1px 6px rgba(0,0,0,0.10);
          flex: 1 1 auto;
          display: flex;
          flex-direction: column;
          min-height: 180px;
          max-height: calc(80vh - 180px);
        }
        .comments-list {
          flex: 1 1 auto;
          overflow-y: auto;
          margin-bottom: 10px;
          min-height: 0;
          max-height: none;
        }
        .comment-form {
          flex-shrink: 0;
          width: 100%;
          display: flex;
          gap: 10px;
          margin-top: 0;
          margin-bottom: 0;
          min-height: 48px;
        }
        .comment-form input {
          flex: 1;
          padding: 12px 14px;
          border-radius: 8px;
          border: 1.5px solid #333;
          background: #222;
          color: #e4e6eb;
          font-size: 1.08em;
          min-height: 44px;
        }
        .comment-form button {
          background: #2d88ff;
          color: #fff;
          border: none;
          border-radius: 8px;
          padding: 0 22px;
          font-size: 1.08em;
          min-height: 44px;
          cursor: pointer;
          font-weight: 500;
          transition: background 0.18s, color 0.18s;
        }
        .comment-form button:hover, .comment-form button:focus {
          background: #1761b0;
          color: #fff;
        }
        .comment { margin-bottom: 14px; }
        .comment-user { font-weight: bold; color: #2d88ff; }
        .comment-content { margin: 4px 0 0 0; }
        .comment-reply-btn { background: none; border: none; color: #aaa; font-size: 0.95em; cursor: pointer; margin-left: 8px; }
        .comment-reply-btn:hover { color: #2d88ff; }
        .comment-replies { margin-left: 24px; margin-top: 8px; }
        .error { color: #ff4c4c; text-align: center; margin: 16px 0; }
        .like-btn, .dislike-btn { 
          background: #23272b; 
          color: #aaa; 
          border: 2px solid #313338; 
          border-radius: 10px; 
          font-size: 1.5em; 
          font-weight: 600; 
          padding: 8px 20px; 
          margin-right: 8px; 
          cursor: pointer; 
          transition: background 0.18s, color 0.18s, border 0.18s; 
          box-shadow: 0 2px 8px rgba(45,136,255,0.10); 
          display: flex; 
          align-items: center; 
          gap: 8px; 
        }
        .like-btn:hover, .dislike-btn:hover {
          background: #2d88ff;
          color: #fff;
          border-color: #2d88ff;
        }
        .like-btn.liked {
          background: #2d88ff;
          color: #fff;
          border-color: #2d88ff;
        }
        .dislike-btn.disliked {
          background: #2d88ff;
          color: #fff;
          border-color: #2d88ff;
        }
        
        /* Ensure modal-root is properly positioned in fullscreen */
        #modal-root {
          position: fixed !important;
          top: 0 !important;
          left: 0 !important;
          width: 100vw !important;
          height: 100vh !important;
          z-index: 99998 !important;
          pointer-events: none !important;
        }
        
        #modal-root .modal {
          pointer-events: auto !important;
        }
        :root {
          --story-btn-bg:        #2d88ff;
          --story-btn-color:     #fff;
          --story-btn-hover-bg:  #1761b0;
          --story-btn-radius:    8px;
          --story-btn-shadow:    0 2px 8px rgba(45, 136, 255, 0.10);

          /* Default size */
          --story-btn-height:    48px;
          --story-btn-min-width: 120px;
          --story-btn-font-size: 1.08em;

          /* Small modifier */
          --story-btn-sm-height:    36px;
          --story-btn-sm-min-width: 100px;
          --story-btn-sm-font-size: 0.9em;

          /* Large modifier */
          --story-btn-lg-height:    56px;
          --story-btn-lg-min-width: 140px;
          --story-btn-lg-font-size: 1.2em;
        }

        .story-btn {
          margin: 0 8px 18px 0;
          margin-bottom: 18px;
          background: var(--story-btn-bg);
          color: var(--story-btn-color);
          border: none;
          border-radius: var(--story-btn-radius);
          height: var(--story-btn-height);
          min-width: var(--story-btn-min-width);
          padding: 0 22px;
          font-size: var(--story-btn-font-size);
          font-weight: 500;
          cursor: pointer;
          box-shadow: var(--story-btn-shadow);
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          transition: background 0.18s, color 0.18s, transform 0.1s;
        }

        .story-btn:hover {
          background: var(--story-btn-hover-bg);
          transform: translateY(-1px);
        }

        /* size modifiers */
        .story-btn--small {
          height: var(--story-btn-sm-height);
          min-width: var(--story-btn-sm-min-width);
          font-size: var(--story-btn-sm-font-size);
        }

        .story-btn--large {
          height: var(--story-btn-lg-height);
          min-width: var(--story-btn-lg-min-width);
          font-size: var(--story-btn-lg-font-size);
        }

      </style>
    </head>
    <body>
      <header style="display: flex; align-items: center; justify-content: space-between; padding: 16px 24px;">
        <div class='nav' style="display: flex; gap: 24px;">
          <a href='/' class='home-link'>Home</a>
          <a href='/profile/{{username}}' class='active'>Profile</a>
        </div>
        <div style="display: flex; align-items: center; gap: 18px;">
        <a href='{{ url_for('logout') }}' style='color:#2d88ff;font-weight:bold;'>Logout</a>
          <button id="theme-toggle-btn" title="Toggle light/dark mode" style="background: none; border: none; color: #2d88ff; font-size: 1.5em; cursor: pointer; transition: color 0.2s;">☀️</button>
        </div>
      </header>
      <div id='profile-root'>
        <div id='profile-user' style="display:flex;align-items:center;gap:24px;margin-bottom:24px;">
          <img id='profile-avatar' src='https://api.dicebear.com/7.x/thumbs/svg?seed={{username}}' alt='avatar' style='width:80px;height:80px;border-radius:50%;border:3px solid #2d88ff;background:#222;box-shadow:0 2px 8px rgba(45,136,255,0.10);transition:background 0.18s;'>
          <div style='flex:1;'>
            <div style='font-size:2em;font-weight:bold;color:#2d88ff;'>@{{username}}</div>
            <button id='change-avatar-btn' style='margin-top:10px;background:#2d88ff;color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:1em;font-weight:500;cursor:pointer;box-shadow:0 2px 8px rgba(45,136,255,0.10);transition:background 0.18s;'>Change Profile Picture</button>
          </div>
        </div>
        <div id='profile-tabs' class='tab-list'></div>
        <div id='profile-media'></div>
        <div class='spinner' id='profile-spinner'></div>
        <div id='profile-error' class='error' style='display:none'></div>
      </div>
      <div id='modal-root'></div>
      <script>
        // Profile picture change functionality
        let currentAvatarSeed = '{{username}}';
        
        // Load avatar seed from database on page load
        async function loadAvatarSeed() {
          try {
            const res = await fetch('/api/avatar/get');
            if (res.ok) {
              const data = await res.json();
              if (data.avatar_seed) {
                currentAvatarSeed = data.avatar_seed;
                const profileAvatar = document.getElementById('profile-avatar');
                profileAvatar.src = `https://api.dicebear.com/7.x/thumbs/svg?seed=${data.avatar_seed}`;
              }
            }
          } catch (e) {
            console.log('Could not load avatar seed from database:', e);
          }
        }
        
        function showChangeAvatarModal() {
          const modalRoot = document.getElementById('modal-root');
          modalRoot.innerHTML = '';
          
          const modal = document.createElement('div');
          modal.className = 'modal';
          
          const content = document.createElement('div');
          content.className = 'modal-content';
          content.style.maxWidth = '500px';
          content.style.height = 'auto';
          content.style.maxHeight = '80vh';
          content.style.overflow = 'auto';
          
          // Create the close button
          const closeBtn = document.createElement('button');
          closeBtn.className = 'modal-close';
          closeBtn.innerHTML = '&times;';
          closeBtn.onclick = () => modalRoot.innerHTML = '';
          
          // Create the content div
          const contentDiv = document.createElement('div');
          contentDiv.style.padding = '2rem';
          contentDiv.innerHTML = `
            <h2 style="margin: 0 0 1.5rem 0; color: #2d88ff; font-size: 1.5em;">Change Profile Picture</h2>
            
            <div style="margin-bottom: 1.5rem;">
              <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Avatar Seed</label>
              <div style="display: flex; gap: 8px; align-items: center;">
                <input type="text" id="avatar-seed" value="${currentAvatarSeed}" placeholder="Enter seed for avatar..." 
                       style="flex: 1; padding: 12px; border-radius: 8px; border: 1.5px solid #333; background: #222; color: #e4e6eb; font-size: 1em;">
                <button id="random-seed-btn" 
                        style="padding: 12px 16px; background: #2d88ff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                  🎲 Random
                </button>
              </div>
              <div style="margin-top: 0.5rem; font-size: 0.9em; color: #aaa;">The seed determines your avatar's appearance. Use the same seed to get the same avatar.</div>
            </div>
            
            <div style="margin-bottom: 1.5rem; text-align: center;">
              <div style="font-weight: 600; color: #e4e6eb; margin-bottom: 0.5rem;">Preview</div>
              <img id="avatar-preview" src="https://api.dicebear.com/7.x/thumbs/svg?seed=${currentAvatarSeed}" 
                   alt="avatar preview" style="width:120px;height:120px;border-radius:50%;border:3px solid #2d88ff;background:#222;box-shadow:0 2px 8px rgba(45,136,255,0.10);">
            </div>
            
            <div style="display: flex; gap: 12px; justify-content: flex-end;">
              <button id="cancel-avatar-btn" 
                      style="padding: 12px 24px; background: #3a3b3c; color: #e4e6eb; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                Cancel
              </button>
              <button id="save-avatar-btn" 
                      style="padding: 12px 24px; background: #2d88ff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                Save Avatar
              </button>
            </div>
          `;
          
          // Append close button and content to modal
          content.appendChild(closeBtn);
          content.appendChild(contentDiv);
          modal.appendChild(content);
          modalRoot.appendChild(modal);
          
          // Event handlers
          const seedInput = document.getElementById('avatar-seed');
          const randomBtn = document.getElementById('random-seed-btn');
          const preview = document.getElementById('avatar-preview');
          const saveBtn = document.getElementById('save-avatar-btn');
          const cancelBtn = document.getElementById('cancel-avatar-btn');
          
          // Update preview when seed changes
          function updatePreview() {
            const seed = seedInput.value.trim() || 'default';
            preview.src = `https://api.dicebear.com/7.x/thumbs/svg?seed=${seed}`;
          }
          
          seedInput.addEventListener('input', updatePreview);
          
          // Random seed button
          randomBtn.addEventListener('click', () => {
            const randomSeed = Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
            seedInput.value = randomSeed;
            updatePreview();
          });
          
          // Save avatar
          saveBtn.addEventListener('click', async () => {
            const newSeed = seedInput.value.trim() || 'default';
            
            try {
              // Save to database
              const res = await fetch('/api/avatar/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({avatar_seed: newSeed})
              });
              
              if (res.ok) {
                currentAvatarSeed = newSeed;
                
                // Update the profile avatar
                const profileAvatar = document.getElementById('profile-avatar');
                profileAvatar.src = `https://api.dicebear.com/7.x/thumbs/svg?seed=${newSeed}`;
                
                // Close modal
                modalRoot.innerHTML = '';
              } else {
                const error = await res.json();
                alert('Error saving avatar: ' + (error.error || 'Unknown error'));
              }
            } catch (e) {
              alert('Error saving avatar: ' + e.message);
            }
          });
          
          // Cancel button
          cancelBtn.addEventListener('click', () => {
            modalRoot.innerHTML = '';
          });
          
          // Close on outside click
          modal.addEventListener('click', (e) => {
            if (e.target === modal) {
              modalRoot.innerHTML = '';
            }
          });
        }
        
        // Initialize avatar from database on page load
        document.addEventListener('DOMContentLoaded', async () => {
          await loadAvatarSeed();
          
          // Add click handler to change avatar button
          const changeAvatarBtn = document.getElementById('change-avatar-btn');
          if (changeAvatarBtn) {
            changeAvatarBtn.addEventListener('click', showChangeAvatarModal);
          }
        });
        
        // --- Modal and Comments Logic (FULL HOME PAGE IMPLEMENTATION) ---
        function showMediaModal(item) {
          if (!item.media_key && item.url) {
            item.media_key = item.url.replace(/^\/files\//, '');
          }
          const modalRoot = document.getElementById('modal-root');
          modalRoot.innerHTML = '';
          const modal = document.createElement('div');
          modal.className = 'modal';
          const content = document.createElement('div');
          content.className = 'modal-content';
          content.style.display        = 'flex';
          content.style.flexDirection  = 'row';
          const closeBtn = document.createElement('button');
          closeBtn.className = 'modal-close';
          closeBtn.innerHTML = '&times;';
          function closeModal() {
            modalRoot.innerHTML = '';
            if (item.media_key) updateFeedCardLikes(item.media_key);
          }
          closeBtn.onclick = closeModal;
          content.appendChild(closeBtn);
          const mediaCol = document.createElement('div');
          mediaCol.className = 'modal-media-col';
          if (item.type === 'image') {
            const img = document.createElement('img');
            img.src = item.url;
            img.style.maxWidth = '100%';
            img.style.maxHeight = '80vh';
            img.style.borderRadius = '12px';
            img.style.display = 'block';
            img.style.margin = '0 auto';
            // Dynamic theme for modal image
            function setModalImageTheme(img) {
              if (document.body.classList.contains('light-theme')) {
                img.style.background = '#fff';
                img.style.border = '2px solid #e4e6eb';
                img.style.boxShadow = '0 2px 12px #2d88ff11';
              } else {
                img.style.background = '#222';
                img.style.border = 'none';
                img.style.boxShadow = '0 1px 6px rgba(0,0,0,0.10)';
              }
            }
            setModalImageTheme(img);
            document.addEventListener('themechange', function() { setModalImageTheme(img); });
            mediaCol.appendChild(img);
          } else if (item.type === 'video') {
            mediaCol.innerHTML = `<video src='${item.url}' class='modal-preview-video' controls autoplay></video>`;
          }
          content.appendChild(mediaCol);
          const sideCol = document.createElement('div');
          sideCol.className = 'modal-side-col';
          const actions = document.createElement('div');
          actions.className = 'modal-actions';
          const likeBtn = document.createElement('button');
          likeBtn.className = 'like-btn';
          const dislikeBtn = document.createElement('button');
          dislikeBtn.className = 'dislike-btn';
          let likeCount = 0, dislikeCount = 0, userValue = 0;
          function updateLikeUI() {
            likeBtn.innerHTML = `👍 <span class='like-count'>${likeCount}</span>`;
            dislikeBtn.innerHTML = `👎 <span class='dislike-count'>${dislikeCount}</span>`;
            likeBtn.classList.toggle('liked', userValue === 1);
            dislikeBtn.classList.toggle('disliked', userValue === -1);
          }
          async function fetchLikes() {
            const res = await fetch(`/api/likes?media_key=${encodeURIComponent(item.media_key)}`);
            const data = await res.json();
            likeCount = data.likes;
            dislikeCount = data.dislikes;
            userValue = data.user_value;
            updateLikeUI();
          }
          likeBtn.onclick = async () => {
            if (userValue === 1) return;
            await fetch('/api/likes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key:item.media_key, value:1})});
            await fetchLikes();
          };
          dislikeBtn.onclick = async () => {
            if (userValue === -1) return;
            await fetch('/api/likes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key:item.media_key, value:-1})});
            await fetchLikes();
          };
          fetchLikes();
          actions.appendChild(likeBtn);
          actions.appendChild(dislikeBtn);
          sideCol.appendChild(actions);
          // Always show comments section
          showCommentsSection(sideCol, item);
          content.appendChild(sideCol);
          modal.appendChild(content);
          modalRoot.appendChild(modal);
          function updateFeedCardLikes(media_key) {
            const cards = document.querySelectorAll('.feed-card[data-media-key]');
            cards.forEach(card => {
              const cardKey = card.getAttribute('data-media-key');
              if (cardKey === media_key) {
                const likeBtn = card.querySelector('.like-btn');
                const dislikeBtn = card.querySelector('.dislike-btn');
                if (likeBtn && dislikeBtn) {
                  fetch(`/api/likes?media_key=${encodeURIComponent(media_key)}`)
                    .then(res => res.json())
                    .then(data => {
                      likeBtn.innerHTML = `👍 <span class='like-count'>${data.likes}</span>`;
                      dislikeBtn.innerHTML = `👎 <span class='dislike-count'>${data.dislikes}</span>`;
                      likeBtn.classList.toggle('liked', data.user_value === 1);
                      dislikeBtn.classList.toggle('disliked', data.user_value === -1);
                    });
                }
              }
            });
          }
          modal.onclick = e => {
            if (e.target === modal) closeModal();
          };
        }
        async function showCommentsSection(parentCol, item) {
          if (!item.media_key) {
            let commentsDiv = document.createElement('div');
            commentsDiv.className = 'modal-comments-section';
            commentsDiv.innerHTML = '<b>Comments</b><div style="color:red;">Comments unavailable: media_key missing.</div>';
            if (!parentCol.querySelector('.modal-comments-section'))
              parentCol.appendChild(commentsDiv);
            else
              parentCol.replaceChild(commentsDiv, parentCol.querySelector('.modal-comments-section'));
            return;
          }
          let commentsDiv = document.createElement('div');
          commentsDiv.className = 'modal-comments-section';
          commentsDiv.innerHTML = '<b>Comments</b>';
          // Comments list container
          const commentsList = document.createElement('div');
          commentsList.className = 'comments-list';
          let comments = [];
          try {
            const res = await fetch(`/api/comments?media_key=${encodeURIComponent(item.media_key)}`);
            comments = await res.json();
          } catch (e) { comments = []; }
          // Render comments recursively
          function renderComments(comments, parentDiv) {
            comments.forEach((comment, idx) => {
              const commentDiv = document.createElement('div');
              commentDiv.className = 'comment';
              commentDiv.innerHTML = `<span class='comment-user'>@${comment.user}</span><span class='comment-content'>: ${comment.text}</span>`;
              // Reply button
              const replyBtn = document.createElement('button');
              replyBtn.className = 'comment-reply-btn';
              replyBtn.textContent = 'Reply';
              replyBtn.onclick = () => {
                const replyForm = document.createElement('form');
                replyForm.className = 'comment-form';
                const input = document.createElement('input');
                input.type = 'text';
                input.placeholder = 'Write a reply...';
                // Dynamic theme for reply input
                function setInputTheme(input) {
                  if (document.body.classList.contains('light-theme')) {
                    input.style.background = '#fff';
                    input.style.color = '#23272b';
                    input.style.borderColor = '#2d88ff';
                  } else {
                    input.style.background = '#222';
                    input.style.color = '#e4e6eb';
                    input.style.borderColor = '#333';
                  }
                }
                setInputTheme(input);
                document.addEventListener('themechange', function() { setInputTheme(input); });
                replyForm.appendChild(input);
                replyForm.appendChild(submitBtn);
                replyForm.onsubmit = async (e) => {
                  e.preventDefault();
                  if (!input.value.trim() || !item.media_key) return;
                  await fetch('/api/comments', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key:item.media_key, text:input.value, parent_id:comment.id})});
                  showCommentsSection(parentCol, item);
                };
                commentDiv.appendChild(replyForm);
              };
              commentDiv.appendChild(replyBtn);
              // Render replies recursively
              if (comment.replies && comment.replies.length > 0) {
                const repliesDiv = document.createElement('div');
                repliesDiv.className = 'comment-replies';
                renderComments(comment.replies, repliesDiv);
                commentDiv.appendChild(repliesDiv);
              }
              parentDiv.appendChild(commentDiv);
            });
          }
          renderComments(comments, commentsList);
          // Add new comment form
          const newCommentForm = document.createElement('form');
          newCommentForm.className = 'comment-form';
          const input = document.createElement('input');
          input.type = 'text';
          input.placeholder = 'Write a comment...';
          // Dynamic theme for comment input
          function setInputTheme(input) {
            if (document.body.classList.contains('light-theme')) {
              input.style.background = '#fff';
              input.style.color = '#23272b';
              input.style.borderColor = '#2d88ff';
            } else {
              input.style.background = '#222';
              input.style.color = '#e4e6eb';
              input.style.borderColor = '#333';
            }
          }
          setInputTheme(input);
          document.addEventListener('themechange', function() { setInputTheme(input); });
          const submitBtn = document.createElement('button');
          submitBtn.type = 'submit';
          submitBtn.textContent = 'Post';
          newCommentForm.appendChild(input);
          newCommentForm.appendChild(submitBtn);
          newCommentForm.onsubmit = async (e) => {
            e.preventDefault();
            if (!input.value.trim() || !item.media_key) return;
            await fetch('/api/comments', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_key:item.media_key, text:input.value})});
            showCommentsSection(parentCol, item);
          };
          commentsDiv.appendChild(commentsList);
          commentsDiv.appendChild(newCommentForm);
          if (!parentCol.querySelector('.modal-comments-section'))
            parentCol.appendChild(commentsDiv);
          else
            parentCol.replaceChild(commentsDiv, parentCol.querySelector('.modal-comments-section'));
        }
        function openMediaModalWithKey(item) {
          let media_key = item && item.url ? item.url.replace(/^\/files\//, '') : undefined;
          showMediaModal({...item, media_key});
        }
        window.showMediaModal = showMediaModal;
        let currentRelPath = null, mediaOffset = 0, MEDIA_BATCH = 30, mediaEnd = false, mediaLoading = false, albumStack = [];
        let currentTab = null, tabType = null;
        let currentStory = null; // Track current story for graph view
        const profileSpinner = document.getElementById('profile-spinner');
        const profileTabs = document.getElementById('profile-tabs');
        const profileMedia = document.getElementById('profile-media');
        const profileError = document.getElementById('profile-error');
        async function loadProfileTabs() {
          profileSpinner.style.display = 'flex';
          profileError.style.display = 'none';
          try {
            const res = await fetch(`/api/profile/{{username}}`);
            const data = await res.json();
            if (res.status !== 200) throw new Error(data.error || 'Failed to load');
            profileTabs.innerHTML = '';
            
            // Add section header
            const header = document.createElement('div');
            header.className = 'tab-section-header';
            header.innerHTML = `
              <div>
                <div class="tab-section-title">Media Collections</div>
                <div class="tab-section-subtitle">${data.tabs.length} collections • ${data.tabs.reduce((sum, tab) => sum + tab.count, 0)} total items</div>
              </div>
              ${data.tabs.length > 5 ? '<input type="text" class="tab-search" placeholder="Search collections..." id="tab-search-input">' : ''}
            `;
            profileTabs.appendChild(header);
            
            // Add search functionality if search input exists
            const searchInput = document.getElementById('tab-search-input');
            if (searchInput) {
              searchInput.addEventListener('input', (e) => {
                const searchTerm = e.target.value.toLowerCase();
                const tabButtons = document.querySelectorAll('.tab-btn');
                let visibleCount = 0;
                
                tabButtons.forEach(btn => {
                  const tabName = btn.getAttribute('data-tab-name');
                  if (tabName && tabName.toLowerCase().includes(searchTerm)) {
                    btn.classList.remove('hidden');
                    visibleCount++;
                  } else {
                    btn.classList.add('hidden');
                  }
                });
                
                // Update subtitle with visible count
                const subtitle = header.querySelector('.tab-section-subtitle');
                if (subtitle) {
                  subtitle.textContent = `${visibleCount} of ${data.tabs.length} collections • ${data.tabs.reduce((sum, tab) => sum + tab.count, 0)} total items`;
                }
              });
            }
            
            data.tabs.forEach(tab => {
              const btn = document.createElement('button');
              btn.className = 'tab-btn' + (currentTab === tab.name ? ' active' : '');
              btn.setAttribute('data-tab-name', tab.name);
              btn.setAttribute('title', `${tab.name} (${tab.count} items) - ${tab.type} collection`);
              
              // Get icon based on tab type
              let icon = '📁';
              if (tab.type === 'images') icon = '🖼️';
              else if (tab.type === 'videos') icon = '🎥';
              else if (tab.type === 'mixed') icon = '🎬';
              else if (tab.type === 'albums') icon = '📂';
              else if (tab.type === 'story') icon = '📖'; // Use book icon for story tabs
              
              // Only render Add Content button for non-story tabs
              btn.innerHTML = `
                <span class="tab-icon">${icon}</span>
                <div style="display: flex; flex-direction: column; align-items: center;">
                  <span>${tab.name}</span>
                  <span class="tab-type">${tab.type}</span>
                </div>
                ${
                  tab.type !== 'story'
                    ? `<span class="tab-count">${tab.count}</span>`
                    : ''
                }
                ${tab.type !== 'story' ? '<button class="tab-add-content-btn" style="position: absolute; top: -5px; right: -5px; background: #2d88ff; color: #fff; border: none; border-radius: 50%; width: 20px; height: 20px; font-size: 12px; cursor: pointer; display: none; z-index: 10;" title="Add content">+</button>' : ''}
              `;
              // Only add Add Content button event listeners for non-story tabs
              if (tab.type !== 'story') {
              btn.addEventListener('mouseenter', () => {
                const addBtn = btn.querySelector('.tab-add-content-btn');
                if (addBtn) addBtn.style.display = 'block';
              });
              btn.addEventListener('mouseleave', () => {
                const addBtn = btn.querySelector('.tab-add-content-btn');
                if (addBtn) addBtn.style.display = 'none';
              });
              btn.addEventListener('click', (e) => {
                if (e.target.classList.contains('tab-add-content-btn')) {
                  e.stopPropagation();
                  showAddContentModal(tab.name);
                  return;
                }
                selectTab(tab.name, tab.type);
              });
                btn.addEventListener('contextmenu', (e) => {
                  e.preventDefault();
                  showAddContentModal(tab.name);
                });
              } else {
                // For story tabs, just select on click
                btn.addEventListener('click', () => selectTab(tab.name, tab.type));
              }
              // Keyboard navigation (always allow)
              btn.onkeydown = (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  selectTab(tab.name, tab.type);
                }
              };
              profileTabs.appendChild(btn);
            });
            
            // Add "Add Tab" button
            const addBtn = document.createElement('button');
            addBtn.className = 'tab-add-btn';
            addBtn.innerHTML = `
              <span style="font-size: 1.2em;">+</span>
              <span>Add Tab</span>
            `;
            addBtn.onclick = () => {
              showAddTabModal();
            };
            profileTabs.appendChild(addBtn);
            
            if (!currentTab && data.tabs.length > 0) selectTab(data.tabs[0].name, data.tabs[0].type);
          } catch (e) {
            profileError.textContent = e.message;
            profileError.style.display = 'block';
          }
          profileSpinner.style.display = 'none';
        }
        
        function showAddTabModal() {
          const modalRoot = document.getElementById('modal-root');
          modalRoot.innerHTML = '';
          
          const modal = document.createElement('div');
          modal.className = 'modal';
          
          const content = document.createElement('div');
          content.className = 'modal-content';
          content.style.maxWidth = '600px';
          content.style.height = 'auto';
          content.style.maxHeight = '80vh';
          content.style.overflow = 'auto';
          
          // Create the close button
          const closeBtn = document.createElement('button');
          closeBtn.className = 'modal-close';
          closeBtn.innerHTML = '&times;';
          closeBtn.onclick = () => modalRoot.innerHTML = '';
          
          // Create the content div
          const contentDiv = document.createElement('div');
          contentDiv.style.padding = '2rem';
          contentDiv.innerHTML = `
            <h2 style="margin: 0 0 1.5rem 0; color: #2d88ff; font-size: 1.5em;">Create New Collection</h2>
            <div style="margin-bottom: 1.5rem;">
              <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Tab Type</label>
              <div style="display: flex; gap: 16px; align-items: center;">
                <label><input type="radio" name="tab-type" value="media" checked> Media</label>
                <label><input type="radio" name="tab-type" value="story"> Story</label>
              </div>
            </div>
            <div style="margin-bottom: 1.5rem;">
              <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Collection Name</label>
              <input type="text" id="new-tab-name" placeholder="Enter collection name..." 
                     style="width: 100%; padding: 12px; border-radius: 8px; border: 1.5px solid #333; background: #222; color: #e4e6eb; font-size: 1em;">
            </div>
            <div id="story-fields" style="display:none;margin-bottom:1.5rem;">
              <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Description</label>
              <textarea id="story-description" placeholder="Enter story description..." style="width:100%;min-height:60px;padding:12px;border-radius:8px;border:1.5px solid #333;background:#222;color:#e4e6eb;font-size:1em;"></textarea>
            </div>
            <div id="media-fields">
              <div style="margin-bottom: 1.5rem;">
                <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Upload Files</label>
                <input type="file" id="file-upload" multiple accept="image/*,video/*" 
                       style="width: 100%; padding: 8px; border-radius: 8px; border: 1.5px solid #333; background: #222; color: #e4e6eb;">
                <div id="file-list" style="margin-top: 0.5rem; font-size: 0.9em; color: #aaa;"></div>
              </div>
              <div style="margin-bottom: 1.5rem;">
                <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Create Folder</label>
                <div style="display: flex; gap: 8px;">
                  <input type="text" id="folder-name" placeholder="Enter folder name..." 
                         style="flex: 1; padding: 12px; border-radius: 8px; border: 1.5px solid #333; background: #222; color: #e4e6eb; font-size: 1em;">
                  <button id="add-folder-btn" 
                          style="padding: 12px 16px; background: #2d88ff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                    Add Folder
                  </button>
                </div>
                <div id="folder-list" style="margin-top: 0.5rem; font-size: 0.9em; color: #aaa;"></div>
              </div>
            </div>
            <div style="display: flex; gap: 12px; justify-content: flex-end;">
              <button id="cancel-btn" 
                      style="padding: 12px 24px; background: #3a3b3c; color: #e4e6eb; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                Cancel
              </button>
              <button id="create-tab-btn" 
                      style="padding: 12px 24px; background: #2d88ff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                Create Collection
              </button>
            </div>
          `;
          
          // Append close button and content to modal
          content.appendChild(closeBtn);
          content.appendChild(contentDiv);
          modal.appendChild(content);
          modalRoot.appendChild(modal);
          
          // Event handlers
          const tabNameInput = document.getElementById('new-tab-name');
          const fileUpload = document.getElementById('file-upload');
          const fileList = document.getElementById('file-list');
          const folderNameInput = document.getElementById('folder-name');
          const folderList = document.getElementById('folder-list');
          const addFolderBtn = document.getElementById('add-folder-btn');
          const createTabBtn = document.getElementById('create-tab-btn');
          const cancelBtn = document.getElementById('cancel-btn');
          const storyFields = document.getElementById('story-fields');
          const mediaFields = document.getElementById('media-fields');
          const storyDescription = document.getElementById('story-description');
          let selectedFiles = [];
          let foldersToCreate = [];
          // Tab type toggle logic
          const tabTypeRadios = contentDiv.querySelectorAll('input[name="tab-type"]');
          tabTypeRadios.forEach(radio => {
            radio.addEventListener('change', () => {
              if (radio.value === 'story' && radio.checked) {
                storyFields.style.display = '';
                mediaFields.style.display = 'none';
              } else if (radio.value === 'media' && radio.checked) {
                storyFields.style.display = 'none';
                mediaFields.style.display = '';
              }
            });
          });
          // File upload handling
          if (fileUpload) {
            fileUpload.addEventListener('change', (e) => {
              selectedFiles = Array.from(e.target.files);
              fileList.innerHTML = selectedFiles.map(file => 
                `<div style="padding: 4px 0; border-bottom: 1px solid #333;">📄 ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)</div>`
              ).join('');
            });
          }
          // Folder creation handling
          if (addFolderBtn) {
            addFolderBtn.addEventListener('click', () => {
              const folderName = folderNameInput.value.trim();
              if (folderName && !foldersToCreate.includes(folderName)) {
                foldersToCreate.push(folderName);
                folderList.innerHTML = foldersToCreate.map(folder => 
                  `<div style="padding: 4px 0; border-bottom: 1px solid #333;">📁 ${folder}</div>`
                ).join('');
                folderNameInput.value = '';
              }
            });
          }
          // Create tab with files and folders or story
          createTabBtn.addEventListener('click', async () => {
            const tabName = tabNameInput.value.trim();
            const tabType = contentDiv.querySelector('input[name="tab-type"]:checked').value;
            if (!tabName) {
              alert('Please enter a collection name');
              return;
            }
            try {
              let body = { tab_name: tabName, tab_type: tabType };
              if (tabType === 'story') {
                body.description = storyDescription.value.trim();
              }
              // Create the tab first
              const createRes = await fetch(`/api/profile/{{username}}/add_tab`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
              });
              if (!createRes.ok) {
                throw new Error('Failed to create collection');
              }
              if (tabType === 'media') {
                // Create folders
                for (const folder of foldersToCreate) {
                  await fetch(`/api/profile/{{username}}/${tabName}/upload`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({folder_name: folder})
                  });
                }
                // Upload files
                if (selectedFiles.length > 0) {
                  const formData = new FormData();
                  selectedFiles.forEach(file => {
                    formData.append('files', file);
                  });
                  await fetch(`/api/profile/{{username}}/${tabName}/upload`, {
                    method: 'POST',
                    body: formData
                  });
                }
              }
              // Close modal and reload tabs
              modalRoot.innerHTML = '';
              await loadProfileTabs();
            } catch (e) {
              alert('Error creating collection: ' + e.message);
            }
          });
          // Cancel button
          cancelBtn.addEventListener('click', () => {
            modalRoot.innerHTML = '';
          });
          // Close on outside click
          modal.addEventListener('click', (e) => {
            if (e.target === modal) {
              modalRoot.innerHTML = '';
            }
          });
        }
        
        async function addNewTab(tabName) {
          try {
            const res = await fetch(`/api/profile/{{username}}/add_tab`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({tab_name: tabName, tab_type: 'mixed'})
            });
            if (res.ok) {
              // Reload tabs to show the new one
              await loadProfileTabs();
            } else {
              alert('Failed to create tab');
            }
          } catch (e) {
            alert('Error creating tab: ' + e.message);
          }
        }
        async function selectTab(tab, type) {
          currentTab = tab;
          tabType = type;
          currentRelPath = null;
          mediaOffset = 0;
          mediaEnd = false;
          albumStack = [];
          profileMedia.innerHTML = '';
          // Update active state of tab buttons more reliably
          const tabButtons = document.querySelectorAll('.tab-btn');
          tabButtons.forEach(btn => {
            btn.classList.remove('active');
            // Find the button that contains the tab name in its text content
            const tabNameSpan = btn.querySelector('div span');
            if (tabNameSpan && tabNameSpan.textContent === tab) {
              btn.classList.add('active');
            }
          });
          // Add loading indicator to the selected tab
          const activeBtn = document.querySelector('.tab-btn.active');
          if (activeBtn) {
            const originalContent = activeBtn.innerHTML;
            activeBtn.innerHTML = `
              <span class="tab-icon">⏳</span>
              <div style="display: flex; flex-direction: column; align-items: center;">
                <span>${tab}</span>
                <span class="tab-type">Loading...</span>
              </div>
            `;
            // Restore original content after loading
            setTimeout(() => {
              if (activeBtn.classList.contains('active')) {
                activeBtn.innerHTML = originalContent;
              }
            }, 1000);
          }
          // Only call story endpoint if type is 'story'
          if (tabType === 'story') {
            try {
              const storyRes = await fetch(`/api/profile/{{username}}/${tab}/story`);
              if (storyRes.ok) {
                const story = await storyRes.json();
                renderStoryTab(story, tab);
                return;
              }
            } catch (e) {}
          }
          // Otherwise, load media as before
          await loadTabMedia();
        }
        // --- STORY TAB UI ---
        function renderStoryTab(story, tab) {
          currentStory = story; // Set current story for graph view

          // --- STORY NAVIGATION HISTORY ---
          if (!window.storyNavHistory) window.storyNavHistory = [];

          // --- BACK BUTTON (previous node) ---
          const backBtn = document.createElement('button');
          backBtn.textContent = '← Back';
          backBtn.classList.add('story-btn')
      
          backBtn.onmouseenter = () => backBtn.style.background = '#1761b0';
          backBtn.onmouseleave = () => backBtn.style.background = '#2d88ff';
          backBtn.style.display = 'none'; // Initially hidden

          // --- FIRST PAGE BUTTON (Story View only) ---
          const firstPageBtn = document.createElement('button');
          firstPageBtn.textContent = '⏮️ First Page';
          firstPageBtn.classList.add('story-btn');
          firstPageBtn.onclick = () => {
            if (story.nodes && story.nodes.length > 0) {
              window.storyNavHistory = [story.nodes[0].id];
              showStoryView();
            }
          };

          let persistentGraphDiv = document.getElementById('persistent-story-graph-div');
          if (!persistentGraphDiv) {
            persistentGraphDiv = document.createElement('div');
            persistentGraphDiv.id = 'persistent-story-graph-div';
            persistentGraphDiv.className = 'story-graph-div';
            persistentGraphDiv.style = 'background:#18191a;border-radius:10px;padding:0;position:relative;width:100%;height:70vh;display:flex;flex-direction:column;overflow:hidden;';
          }

          const isFullscreen = document.fullscreenElement === persistentGraphDiv;

          if (isFullscreen) {
            // Only update children of persistentGraphDiv, do not touch any parent or ancestor
            while (persistentGraphDiv.firstChild) persistentGraphDiv.removeChild(persistentGraphDiv.firstChild);
            // --- REBUILD GRAPH UI INSIDE persistentGraphDiv ---
            // (move all showGraphView logic here, or call a helper)
            // ...
            // For now, call a new helper:
            buildGraphUIInside(persistentGraphDiv, story, tab);
            return;
          }

          // Not in fullscreen: normal re-render logic
          profileMedia.innerHTML = '';
          // ... rest of renderStoryTab as before ...

          // --- FULLSCREEN STATE PRESERVATION ---
          // Detect if we are in fullscreen and if the old graphDiv is fullscreened
          let wasFullscreen = false;
          if (document.fullscreenElement && persistentGraphDiv && document.fullscreenElement === persistentGraphDiv) {
            wasFullscreen = true;
          }

          const container = document.createElement('div');
          container.style = 'background:#23272b;padding:24px;border-radius:12px;box-shadow:0 2px 8px rgba(45,136,255,0.10);margin-bottom:24px;';
          
          // Header
          const h = document.createElement('h2');
          h.textContent = story.name + ' (Story)';
          h.style = 'color:#2d88ff;margin-bottom:12px;';
          container.appendChild(h);
          
          // Description (preserve new‑lines using <pre>)
          if (story.description) {
            const pre = document.createElement('pre');
            pre.textContent = story.description;
            pre.style.color = '#aaa';
            pre.style.marginBottom = '18px';
            pre.style.whiteSpace = 'pre-wrap'; // wrap long lines
            pre.style.fontFamily = 'inherit'; // match normal text
            pre.style.fontSize = 'inherit';   // match rest of UI
            pre.style.background = 'none';    // remove default gray bg
            pre.style.border = 'none';        // remove borders
            pre.style.padding = '0';          // remove extra spacing
            container.appendChild(pre);
          }








          
          // Tabs: Graph View and Story View
          const tabContainer = document.createElement('div');
          tabContainer.style = 'margin-bottom:24px;';
          const tabButtons = document.createElement('div');
          tabButtons.style = 'display:flex;gap:8px;margin-bottom:16px;';
          
          const graphBtn = document.createElement('button');
          graphBtn.textContent = '🕸️ Graph View';
          graphBtn.style = 'background:#2d88ff;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-weight:600;cursor:pointer;';
          graphBtn.onclick = () => showGraphView();
          
          const storyBtn = document.createElement('button');
          storyBtn.textContent = '📖 Story View';
          storyBtn.style = 'background:#3a3b3c;color:#e4e6eb;border:none;border-radius:8px;padding:8px 16px;font-weight:600;cursor:pointer;';
          storyBtn.onclick = () => showStoryView();
          
          tabButtons.appendChild(graphBtn);
          tabButtons.appendChild(storyBtn);
          tabContainer.appendChild(tabButtons);
          
          // Content area
          const contentArea = document.createElement('div');
          contentArea.id = 'story-content-area';
          tabContainer.appendChild(contentArea);
          container.appendChild(tabContainer);
          
          // Insert the back button at the top of the container
          container.appendChild(backBtn);
          // Insert the first page button below the back button
          container.appendChild(firstPageBtn);
          
          // --- Graph View ---
          function showGraphView() {
            graphBtn.style.background = '#2d88ff';
            graphBtn.style.color = '#fff';
            storyBtn.style.background = '#3a3b3c';
            storyBtn.style.color = '#e4e6eb';
            const isFullscreen = document.fullscreenElement && persistentGraphDiv && document.fullscreenElement === persistentGraphDiv;
            if (!isFullscreen) {
              // Not in fullscreen: clear and re-append
              while (contentArea.firstChild) contentArea.removeChild(contentArea.firstChild);
              contentArea.appendChild(persistentGraphDiv);
            }
            // Always clear children in-place
            while (persistentGraphDiv.firstChild) persistentGraphDiv.removeChild(persistentGraphDiv.firstChild);
            // Fullscreen button
            const fullscreenBtn = document.createElement('button');
            fullscreenBtn.textContent = '⛶';
            fullscreenBtn.title = 'Fullscreen';
            fullscreenBtn.style = 'position:absolute;top:16px;right:16px;z-index:20;background:#23272b;color:#2d88ff;border:none;border-radius:8px;padding:8px 16px;font-size:1.5em;cursor:pointer;box-shadow:0 2px 8px #2d88ff22;';
            fullscreenBtn.onclick = () => {
              if (!document.fullscreenElement) {
                persistentGraphDiv.requestFullscreen();
              } else {
                document.exitFullscreen();
              }
              setTimeout(resizeCanvas, 200);
            };
            persistentGraphDiv.appendChild(fullscreenBtn);

            // --- MODAL ROOT FULLSCREEN HANDLING ---
            function moveModalRootToFullscreen() {
              const modalRoot = document.getElementById('modal-root');
              if (modalRoot && document.fullscreenElement && document.fullscreenElement.contains(persistentGraphDiv)) {
                document.fullscreenElement.appendChild(modalRoot);
              }
            }
            function moveModalRootToBody() {
              const modalRoot = document.getElementById('modal-root');
              if (modalRoot && document.body !== modalRoot.parentNode) {
                document.body.appendChild(modalRoot);
              }
            }
            document.addEventListener('fullscreenchange', function() {
              if (document.fullscreenElement && document.fullscreenElement.contains(persistentGraphDiv)) {
                moveModalRootToFullscreen();
              } else {
                moveModalRootToBody();
              }
              window.ensureModalFullscreenCompatibility && window.ensureModalFullscreenCompatibility();
            });
            // Center button
            const centerBtn = document.createElement('button');
            centerBtn.textContent = '🎯';
            centerBtn.title = 'Center on First Node';
            centerBtn.style = 'position:absolute;top:16px;right:64px;z-index:20;background:#23272b;color:#2d88ff;border:none;border-radius:8px;padding:8px 16px;font-size:1.5em;cursor:pointer;box-shadow:0 2px 8px #2d88ff22;';
            centerBtn.onclick = () => {
              if (story.nodes && story.nodes.length > 0) {
                const firstNode = story.nodes[0];
                const pos = story.nodePositions[firstNode.id] || {x:0, y:0};
                if (persistentGraphDiv.viewport) {
                  persistentGraphDiv.viewport.x = -pos.x * persistentGraphDiv.viewport.scale;
                  persistentGraphDiv.viewport.y = -pos.y * persistentGraphDiv.viewport.scale;
                  window.redrawGraph();
                }
              }
            };
            persistentGraphDiv.appendChild(centerBtn);

            // Responsive canvas
            const canvas = document.createElement('canvas');
            canvas.style = 'border:2px solid #333;border-radius:8px;background:#0f0f0f;cursor:grab;width:100%;height:100%;display:block;flex:1 1 auto;';
            canvas.id = 'story-graph-canvas';
            persistentGraphDiv.appendChild(canvas);
            contentArea.appendChild(persistentGraphDiv);

            // Resize canvas to fit container
            function resizeCanvas() {
              const rect = persistentGraphDiv.getBoundingClientRect();
              canvas.width = rect.width;
              canvas.height = rect.height;
              if (typeof window.redrawGraph === 'function') window.redrawGraph();
            }
            window.addEventListener('resize', resizeCanvas);
            document.addEventListener('fullscreenchange', resizeCanvas);
            setTimeout(resizeCanvas, 100);

            // If no nodes, show Add Node button in center
            if (!story.nodes || story.nodes.length === 0) {
              const addBtn = document.createElement('button');
              addBtn.textContent = '+ Add Node';
              addBtn.style = 'position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-size:2em;background:#2d88ff;color:#fff;border:none;border-radius:12px;padding:32px 48px;font-weight:700;cursor:pointer;z-index:10;box-shadow:0 4px 24px #2d88ff44;';
              addBtn.onclick = () => showNodeModal(null, tab, story, {x:0, y:0});
              persistentGraphDiv.appendChild(addBtn);
              return;
            }

            // Graph logic
            initGraphVisualization(canvas, story, tab, persistentGraphDiv);
            // Store viewport on persistentGraphDiv for access in centerBtn
            if (persistentGraphDiv.viewport) {
              // already set
            } else {
              persistentGraphDiv.viewport = { x: 0, y: 0, scale: 1 };
            }
          }

          // --- Story View ---
          function showStoryView() {
            
            graphBtn.style.background = '#3a3b3c';
            graphBtn.style.color = '#e4e6eb';
            storyBtn.style.background = '#2d88ff';
            storyBtn.style.color = '#fff';
            contentArea.innerHTML = '';
            // Start at first node
            let currentNodeId = story.nodes.length > 0 ? story.nodes[0].id : null;
            if (window.storyNavHistory.length > 0) {
              currentNodeId = window.storyNavHistory[window.storyNavHistory.length - 1];
            }
            function renderStoryNode(nodeId, isBackNav) {
              contentArea.innerHTML = '';
              const node = story.nodes.find(n => n.id === nodeId);
              if (!node) {
                // Show error and first page button
                const errorDiv = document.createElement('div');
                errorDiv.style = 'color:#e74c3c;font-size:1.2em;margin-bottom:18px;';
                errorDiv.textContent = 'Node not found.';
                contentArea.appendChild(errorDiv);
                // Always show first page button
                const fpBtn = firstPageBtn.cloneNode(true);
                fpBtn.onclick = firstPageBtn.onclick;
                contentArea.appendChild(fpBtn);
                return;
              }
              // Manage navigation history
              if (!isBackNav) {
                if (window.storyNavHistory.length === 0 || window.storyNavHistory[window.storyNavHistory.length - 1] !== nodeId) {
                  window.storyNavHistory.push(nodeId);
                }
              }
              // Enable/disable back button
              if (window.storyNavHistory.length <= 1) {
                backBtn.style.display = 'none';
              } else {
                backBtn.style.display = '';
                backBtn.disabled = false;
              }
              backBtn.onclick = () => {
                if (window.storyNavHistory.length > 1) {
                  window.storyNavHistory.pop();
                  const prevNodeId = window.storyNavHistory[window.storyNavHistory.length - 1];
                  renderStoryNode(prevNodeId, true);
                }
              };
              // updated code – preserves all newlines in node.content
              const nodeDiv = document.createElement('div');
              Object.assign(nodeDiv.style, {
                background:   '#23272b',
                padding:      '32px',
                borderRadius: '16px',
                maxWidth:     '600px',
                margin:       '40px auto',
                textAlign:    'center',
                boxShadow:    '0 2px 8px #2d88ff22',
              });

              const titleEl = document.createElement('h2');
              titleEl.style.color = '#2d88ff';
              titleEl.textContent = node.id;
              nodeDiv.appendChild(titleEl);

              const contentEl = document.createElement('div');
              const isLight = document.body.classList.contains('light-theme');
              // This is the key: preserve all newlines and wrap long lines
              Object.assign(contentEl.style, {
                margin:     '24px 0',
                fontSize:   '1.2em',
                color:      isLight ? '#23272b' : '#e4e6eb',
                whiteSpace: 'pre-wrap',   // <-- honour line breaks
                textAlign:  'center',       // optional: left‑align paragraphs
              });
              contentEl.textContent = node.content || '(No content)';
              nodeDiv.appendChild(contentEl);

              // Outgoing connections
              const outgoing = story.connections.filter(c => c.from === node.id);
              if (outgoing.length > 0) {
                const btnRow = document.createElement('div');
                Object.assign(btnRow.style, {
                  display:           'inline-grid',
                  gridTemplateColumns: 'repeat(3, auto)',
                  justifyItems:      'center',
                  justifyContent:      'center',
                  gap:               '18px',
                });
                outgoing.forEach(conn => {
                  const toNode = story.nodes.find(n => n.id === conn.to);
                  if (toNode) {
                    const btn = document.createElement('button');
                    btn.textContent = toNode.id;
                    btn.style = 'background:#2d88ff;color:#fff;border:none;border-radius:8px;padding:16px 32px;font-size:1.1em;font-weight:600;cursor:pointer;box-shadow:0 2px 8px #2d88ff22;';
                    btn.onclick = () => renderStoryNode(toNode.id, false);
                    btnRow.appendChild(btn);
                  }
                });
                nodeDiv.appendChild(btnRow);
              } else {
                nodeDiv.innerHTML += `<div style='color:#aaa;margin-top:32px;'>End of story.</div>`;
              }
              contentArea.appendChild(nodeDiv);
              contentArea.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
            if (currentNodeId) renderStoryNode(currentNodeId, false);
          }

          // Show graph view by default
          showGraphView();
          profileMedia.appendChild(container);
          // At the end of renderStoryTab, re-assign global showNodeModal
          window.showNodeModal = showNodeModal;

          // --- RESTORE FULLSCREEN IF NEEDED ---
          // After DOM is updated, if we were in fullscreen, re-request fullscreen on the new persistentGraphDiv
          setTimeout(() => {
            const newPersistentGraphDiv = document.getElementById('persistent-story-graph-div');
            if (wasFullscreen && newPersistentGraphDiv && document.fullscreenElement !== newPersistentGraphDiv) {
              // Only request if not already fullscreened
              if (newPersistentGraphDiv.requestFullscreen) {
                newPersistentGraphDiv.requestFullscreen();
              } else if (newPersistentGraphDiv.webkitRequestFullscreen) {
                newPersistentGraphDiv.webkitRequestFullscreen();
              } else if (newPersistentGraphDiv.mozRequestFullScreen) {
                newPersistentGraphDiv.mozRequestFullScreen();
              } else if (newPersistentGraphDiv.msRequestFullscreen) {
                newPersistentGraphDiv.msRequestFullscreen();
              }
            }
            // Move modal-root if needed
            let modalRoot = document.getElementById('modal-root');
            if (modalRoot && document.fullscreenElement && document.fullscreenElement.contains(newPersistentGraphDiv)) {
              document.fullscreenElement.appendChild(modalRoot);
            }
            // --- ENSURE MODAL ROOT EXISTS ---
            // If modal-root is missing, create and append it
            if (!modalRoot) {
              modalRoot = document.createElement('div');
              modalRoot.id = 'modal-root';
              if (document.fullscreenElement && document.fullscreenElement.contains(newPersistentGraphDiv)) {
                document.fullscreenElement.appendChild(modalRoot);
              } else {
                document.body.appendChild(modalRoot);
              }
            }
          }, 30);
        }

        // --- Graph Visualization with Infinite Canvas, Fullscreen, and Panning ---
        function initGraphVisualization(canvas, story, tab, persistentGraphDiv) {
          let nodes = [];
          let connections = [];
          let selectedNode = null;
          let isDragging = false;
          let dragOffset = { x: 0, y: 0 };
          let nodeMenu = null;
          let addConnectionFrom = null;
          let isPanning = false;
          let panStart = { x: 0, y: 0 };
          let viewport = persistentGraphDiv.viewport || { x: 0, y: 0, scale: 1 };
          persistentGraphDiv.viewport = viewport;

          // Responsive sizing
          function getCanvasSize() {
            return { width: canvas.width, height: canvas.height };
          }

          // Initialize node positions if not set
          if (!story.nodePositions) {
            story.nodePositions = {};
            story.nodes.forEach((node, index) => {
              const angle = (index / story.nodes.length) * 2 * Math.PI;
              const radius = 150;
              story.nodePositions[node.id] = {
                x: Math.cos(angle) * radius,
                y: Math.sin(angle) * radius
              };
            });
          }

          // Create node objects
          function updateNodes() {
            nodes = story.nodes.map(node => ({
              ...node,
              x: story.nodePositions[node.id]?.x || 0,
              y: story.nodePositions[node.id]?.y || 0,
              width: 120,
              height: 60
            }));
            connections = story.connections;
          }
          updateNodes();

          // Draw function with smooth animations
          let animationFrame = null;
          let lastDrawTime = 0;
          
          function draw() {
            const currentTime = Date.now();
            const deltaTime = currentTime - lastDrawTime;
            lastDrawTime = currentTime;
            
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            
            // Draw grid background
            ctx.save();
            ctx.translate(canvas.width/2 + viewport.x, canvas.height/2 + viewport.y);
            ctx.scale(viewport.scale, viewport.scale);
            
            // Draw subtle animated grid
            const gridSize = 50;
            const gridOffsetX = (viewport.x % (gridSize * viewport.scale)) / viewport.scale;
            const gridOffsetY = (viewport.y % (gridSize * viewport.scale)) / viewport.scale;
            ctx.strokeStyle = '#1a1a1a';
            ctx.lineWidth = 1;
            ctx.setLineDash([]);
            
            for (let x = -canvas.width/2 - gridOffsetX; x < canvas.width/2; x += gridSize) {
              ctx.beginPath();
              ctx.moveTo(x, -canvas.height/2);
              ctx.lineTo(x, canvas.height/2);
              ctx.stroke();
            }
            for (let y = -canvas.height/2 - gridOffsetY; y < canvas.height/2; y += gridSize) {
              ctx.beginPath();
              ctx.moveTo(-canvas.width/2, y);
              ctx.lineTo(canvas.width/2, y);
              ctx.stroke();
            }
            
            // Draw connections with smooth animations
            connections.forEach(conn => {
              const fromNode = nodes.find(n => n.id === conn.from);
              const toNode = nodes.find(n => n.id === conn.to);
              if (fromNode && toNode) {
                const dx = toNode.x - fromNode.x;
                const dy = toNode.y - fromNode.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                if (distance > 0) {
                  const unitX = dx / distance;
                  const unitY = dy / distance;
                  const startX = fromNode.x + unitX * (fromNode.width / 2);
                  const startY = fromNode.y + unitY * (fromNode.height / 2);
                  const endX = toNode.x - unitX * (toNode.width / 2);
                  const endY = toNode.y - unitY * (toNode.height / 2);
                  
                  // Check if this connection is being hovered
                  const isHovered = hoverConnection === conn;
                  
                  // Smooth hover animation
                  const hoverIntensity = isHovered ? 1 : 0;
                  const lineWidth = 3 + (hoverIntensity * 3); // Smooth transition from 3 to 6
                  
                  // Create gradient for connection with smooth color transitions
                  const gradient = ctx.createLinearGradient(startX, startY, endX, endY);
                  if (isHovered) {
                    gradient.addColorStop(0, '#e74c3c');
                    gradient.addColorStop(1, '#c0392b');
                  } else {
                    gradient.addColorStop(0, addConnectionFrom ? '#f39c12' : '#2d88ff');
                    gradient.addColorStop(1, addConnectionFrom ? '#e67e22' : '#1761b0');
                  }
                  
                  ctx.strokeStyle = gradient;
                  ctx.lineWidth = lineWidth;
                  ctx.setLineDash([]);
                  ctx.beginPath();
                  ctx.moveTo(startX, startY);
                  ctx.lineTo(endX, endY);
                  ctx.stroke();
                  
                  // Enhanced arrow with smooth transitions
                  const arrowLength = 20;
                  const arrowAngle = Math.PI / 6;
                  ctx.strokeStyle = isHovered ? '#e74c3c' : (addConnectionFrom ? '#f39c12' : '#2d88ff');
                  ctx.lineWidth = 2 + hoverIntensity; // Smooth transition from 2 to 3
                  ctx.beginPath();
                  ctx.moveTo(endX, endY);
                  ctx.lineTo(
                    endX - arrowLength * Math.cos(Math.atan2(dy, dx) - arrowAngle),
                    endY - arrowLength * Math.sin(Math.atan2(dy, dx) - arrowAngle)
                  );
                  ctx.moveTo(endX, endY);
                  ctx.lineTo(
                    endX - arrowLength * Math.cos(Math.atan2(dy, dx) + arrowAngle),
                    endY - arrowLength * Math.sin(Math.atan2(dy, dx) + arrowAngle)
                  );
                  ctx.stroke();
                  
                  // Show delete button when hovering connection with smooth fade
                  if (isHovered) {
                    const midX = (startX + endX) / 2;
                    const midY = (startY + endY) / 2;
                    
                    // Draw delete button background with smooth appearance
                    const btnSize = 24;
                    ctx.fillStyle = 'rgba(231, 76, 60, 0.95)';
                    ctx.strokeStyle = '#e74c3c';
                    ctx.lineWidth = 2;
                    ctx.beginPath();
                    ctx.arc(midX, midY, btnSize/2, 0, 2 * Math.PI);
                    ctx.fill();
                    ctx.stroke();
                    
                    // Draw X symbol
                    ctx.fillStyle = '#ffffff';
                    ctx.font = 'bold 14px Arial';
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText('×', midX, midY);
                  }
                }
              }
            });
            
            // Draw connection preview line when in connection mode with smooth animation
            if (addConnectionFrom) {
              const fromNode = nodes.find(n => n.id === addConnectionFrom);
              if (fromNode) {
                const dx = mousePos.x - fromNode.x;
                const dy = mousePos.y - fromNode.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                if (distance > 0) {
                  const unitX = dx / distance;
                  const unitY = dy / distance;
                  const startX = fromNode.x + unitX * (fromNode.width / 2);
                  const startY = fromNode.y + unitY * (fromNode.height / 2);
                  
                  // Draw animated dashed preview line with smooth animation
                  ctx.strokeStyle = '#f39c12';
                  ctx.lineWidth = 4;
                  ctx.setLineDash([10, 5]);
                  ctx.lineDashOffset = -currentTime / 100; // Smooth animated dash
                  ctx.beginPath();
                  ctx.moveTo(startX, startY);
                  ctx.lineTo(mousePos.x, mousePos.y);
                  ctx.stroke();
                  
                  // Draw preview arrow with smooth animation
                  ctx.strokeStyle = '#f39c12';
                  ctx.lineWidth = 3;
                  ctx.setLineDash([]);
                  const arrowLength = 20;
                  const arrowAngle = Math.PI / 6;
                  ctx.beginPath();
                  ctx.moveTo(mousePos.x, mousePos.y);
                  ctx.lineTo(
                    mousePos.x - arrowLength * Math.cos(Math.atan2(dy, dx) - arrowAngle),
                    mousePos.y - arrowLength * Math.sin(Math.atan2(dy, dx) - arrowAngle)
                  );
                  ctx.moveTo(mousePos.x, mousePos.y);
                  ctx.lineTo(
                    mousePos.x - arrowLength * Math.cos(Math.atan2(dy, dx) + arrowAngle),
                    mousePos.y - arrowLength * Math.sin(Math.atan2(dy, dx) + arrowAngle)
                  );
                  ctx.stroke();
                }
              }
            }
            
            // Draw nodes with smooth animations and enhanced styling
            nodes.forEach(node => {
              // Enhanced node shadow with smooth transitions
              const shadowIntensity = selectedNode === node ? 0.5 : (hoverNode === node ? 0.4 : 0.3);
              ctx.shadowColor = 'rgba(0,0,0,' + shadowIntensity + ')';
              ctx.shadowBlur = 12;
              ctx.shadowOffsetX = 3;
              ctx.shadowOffsetY = 3;
              
              // Create enhanced gradient for node background with smooth transitions
              const gradient = ctx.createRadialGradient(
                node.x - node.width/3, node.y - node.height/3, 0,
                node.x, node.y, node.width/1.5
              );
              
              if (selectedNode === node) {
                // Selected node - bright blue gradient
                gradient.addColorStop(0, '#5dade2');
                gradient.addColorStop(0.5, '#3498db');
                gradient.addColorStop(1, '#2980b9');
              } else if (hoverNode === node) {
                // Hovered node - bright gradient
                gradient.addColorStop(0, '#5d6d7e');
                gradient.addColorStop(0.5, '#4a5568');
                gradient.addColorStop(1, '#2d3748');
              } else {
                // Normal node - sophisticated gradient
                gradient.addColorStop(0, '#6c757d');
                gradient.addColorStop(0.3, '#495057');
                gradient.addColorStop(0.7, '#343a40');
                gradient.addColorStop(1, '#212529');
              }
              
              ctx.fillStyle = gradient;
              
              // Enhanced border with gradient and smooth transitions
              const borderGradient = ctx.createLinearGradient(
                node.x - node.width/2, node.y - node.height/2,
                node.x + node.width/2, node.y + node.height/2
              );
              
              if (selectedNode === node) {
                borderGradient.addColorStop(0, '#3498db');
                borderGradient.addColorStop(1, '#2980b9');
              } else if (hoverNode === node) {
                borderGradient.addColorStop(0, '#2d88ff');
                borderGradient.addColorStop(1, '#1761b0');
              } else {
                borderGradient.addColorStop(0, '#2d88ff');
                borderGradient.addColorStop(1, '#1e3a8a');
              }
              
              ctx.strokeStyle = borderGradient;
              ctx.lineWidth = selectedNode === node ? 4 : 3;
              
              // Enhanced rounded rect with smooth corners
              const x = node.x - node.width/2;
              const y = node.y - node.height/2;
              const width = node.width;
              const height = node.height;
              const radius = 15;
              
              ctx.beginPath();
              ctx.moveTo(x + radius, y);
              ctx.lineTo(x + width - radius, y);
              ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
              ctx.lineTo(x + width, y + height - radius);
              ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
              ctx.lineTo(x + radius, y + height);
              ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
              ctx.lineTo(x, y + radius);
              ctx.quadraticCurveTo(x, y, x + radius, y);
              ctx.closePath();
              ctx.fill();
              ctx.stroke();
              
              // Add inner highlight for depth with smooth transitions
              ctx.shadowColor = 'transparent';
              ctx.shadowBlur = 0;
              ctx.shadowOffsetX = 0;
              ctx.shadowOffsetY = 0;
              
              const innerGradient = ctx.createRadialGradient(
                node.x - node.width/4, node.y - node.height/4, 0,
                node.x, node.y, node.width/3
              );
              innerGradient.addColorStop(0, 'rgba(255,255,255,0.1)');
              innerGradient.addColorStop(1, 'rgba(255,255,255,0)');
              
              ctx.fillStyle = innerGradient;
              ctx.beginPath();
              ctx.moveTo(x + radius, y);
              ctx.lineTo(x + width - radius, y);
              ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
              ctx.lineTo(x + width, y + height - radius);
              ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
              ctx.lineTo(x + radius, y + height);
              ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
              ctx.lineTo(x, y + radius);
              ctx.quadraticCurveTo(x, y, x + radius, y);
              ctx.closePath();
              ctx.fill();
              
              // Button column only on hover
              const showButtons = hoverNode === node;
              const btnColWidth = 36;
              const btnHeight = (height - 0) / 2;
              const btnX = x + width - btnColWidth;
              const btnY1 = y;
              const btnY2 = y + btnHeight;

              // Draw left side (name/content) - fill node if not hovered
              ctx.save();
              ctx.beginPath();
              ctx.rect(x + 8, y + 4, showButtons ? width - btnColWidth - 16 : width - 16, height - 8);
              ctx.clip();
              ctx.fillStyle = '#fff';
              ctx.font = 'bold 14px Arial';
              ctx.textAlign = 'left';
              ctx.textBaseline = 'top';
              ctx.fillStyle = '#fff';
              ctx.fillText(node.id, x + 14, y + 10, (showButtons ? width - btnColWidth - 20 : width - 20));
              if (node.content) {
                ctx.font = '12px Arial';
                ctx.fillStyle = '#b8c5d1';
                ctx.fillText(node.content.substring(0, 24) + (node.content.length > 24 ? '...' : ''), x + 14, y + 30, (showButtons ? width - btnColWidth - 20 : width - 20));
              }
              ctx.restore();

              if (showButtons) {
                // Draw right column background (single rounded rect, right corners only)
                ctx.save();
                ctx.beginPath();
                ctx.moveTo(btnX, y);
                ctx.lineTo(btnX + btnColWidth - radius, y);
                ctx.quadraticCurveTo(btnX + btnColWidth, y, btnX + btnColWidth, y + radius);
                ctx.lineTo(btnX + btnColWidth, y + height - radius);
                ctx.quadraticCurveTo(btnX + btnColWidth, y + height, btnX + btnColWidth - radius, y + height);
                ctx.lineTo(btnX, y + height);
                ctx.closePath();
                // Subtle background
                ctx.fillStyle = 'rgba(40,50,70,0.97)';
                ctx.fill();
                ctx.restore();

                // Draw separator line between buttons
                ctx.save();
                ctx.strokeStyle = 'rgba(255,255,255,0.10)';
                ctx.lineWidth = 1.1;
                ctx.beginPath();
                ctx.moveTo(btnX + 6, y + btnHeight);
                ctx.lineTo(btnX + btnColWidth - 6, y + btnHeight);
                ctx.stroke();
                ctx.restore();

                // Draw +Node button (top)
                ctx.save();
                let isHoverNodeBtn = false;
                if (mousePos.x >= btnX && mousePos.x <= btnX + btnColWidth && mousePos.y >= btnY1 && mousePos.y <= btnY1 + btnHeight) {
                  isHoverNodeBtn = true;
                }
                ctx.beginPath();
                ctx.arc(btnX + btnColWidth/2, btnY1 + btnHeight/2, 13, 0, 2 * Math.PI);
                ctx.fillStyle = isHoverNodeBtn ? 'rgba(39,174,96,0.18)' : 'rgba(255,255,255,0.07)';
                ctx.fill();
                ctx.font = '20px Arial';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = isHoverNodeBtn ? '#27ae60' : '#b8c5d1';
                ctx.fillText('➕', btnX + btnColWidth/2, btnY1 + btnHeight/2 + 1);
                ctx.restore();
                
                // Draw +Conn button (bottom)
                ctx.save();
                let isHoverConnBtn = false;
                if (mousePos.x >= btnX && mousePos.x <= btnX + btnColWidth && mousePos.y >= btnY2 && mousePos.y <= btnY2 + btnHeight) {
                  isHoverConnBtn = true;
                }
                ctx.beginPath();
                ctx.arc(btnX + btnColWidth/2, btnY2 + btnHeight/2, 13, 0, 2 * Math.PI);
                ctx.fillStyle = isHoverConnBtn ? 'rgba(243,156,18,0.18)' : 'rgba(255,255,255,0.07)';
                ctx.fill();
                ctx.font = '20px Arial';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = isHoverConnBtn ? '#f39c12' : '#b8c5d1';
                ctx.fillText('🔗', btnX + btnColWidth/2, btnY2 + btnHeight/2 + 1);
                ctx.restore();
              }
            });
            
            ctx.restore();
            
            // Request next frame for smooth animation
            animationFrame = requestAnimationFrame(draw);
          }

          // Redraw function for resize with smooth animation handling
          window.redrawGraph = () => {
            updateNodes();
            if (animationFrame) {
              cancelAnimationFrame(animationFrame);
            }
            draw();
          };

          // Convert screen to world coordinates
          function screenToWorld(x, y) {
            return {
              x: (x - canvas.width/2 - viewport.x) / viewport.scale,
              y: (y - canvas.height/2 - viewport.y) / viewport.scale
            };
          }
          function worldToScreen(x, y) {
            return {
              x: (x * viewport.scale) + canvas.width/2 + viewport.x,
              y: (y * viewport.scale) + canvas.height/2 + viewport.y
            };
          }

          // --- Node hover buttons (inside node) with smooth transitions ---
          let hoverNode = null;
          let hoverConnection = null; // Track hovered connection
          let mousePos = { x: 0, y: 0 }; // Track mouse position for connection preview
          let targetHoverNode = null; // For smooth hover transitions
          let targetHoverConnection = null; // For smooth connection hover transitions
          
          canvas.addEventListener('mousemove', (e) => {
            const rect = canvas.getBoundingClientRect();
            const sx = e.clientX - rect.left;
            const sy = e.clientY - rect.top;
            const {x, y} = screenToWorld(sx, sy);
            mousePos = { x, y }; // Update mouse position
            
            // Check for node hover with smooth transitions
            let foundNode = null;
            for (const node of nodes) {
              if (x >= node.x - node.width/2 && x <= node.x + node.width/2 &&
                  y >= node.y - node.height/2 && y <= node.y + node.height/2) {
                foundNode = node;
                break;
              }
            }
            
            // Check for connection hover (only if not hovering a node) with smooth transitions
            let foundConnection = null;
            if (!foundNode) {
              for (const conn of connections) {
                const fromNode = nodes.find(n => n.id === conn.from);
                const toNode = nodes.find(n => n.id === conn.to);
                if (fromNode && toNode) {
                  const dx = toNode.x - fromNode.x;
                  const dy = toNode.y - fromNode.y;
                  const distance = Math.sqrt(dx * dx + dy * dy);
                  if (distance > 0) {
                    const unitX = dx / distance;
                    const unitY = dy / distance;
                    const startX = fromNode.x + unitX * (fromNode.width / 2);
                    const startY = fromNode.y + unitY * (fromNode.height / 2);
                    const endX = toNode.x - unitX * (toNode.width / 2);
                    const endY = toNode.y - unitY * (toNode.height / 2);
                    
                    // Check if point is near the line (within 8 pixels)
                    const A = endY - startY;
                    const B = startX - endX;
                    const C = endX * startY - startX * endY;
                    const distanceToLine = Math.abs(A * x + B * y + C) / Math.sqrt(A * A + B * B);
                    
                    if (distanceToLine <= 8) {
                      // Check if point is within the line segment bounds
                      const dot1 = (x - startX) * (endX - startX) + (y - startY) * (endY - startY);
                      const dot2 = (endX - startX) * (endX - startX) + (endY - startY) * (endY - startY);
                      if (dot1 >= 0 && dot1 <= dot2) {
                        foundConnection = conn;
                        break;
                      }
                    }
                  }
                }
              }
            }
            
            // Update hover states with smooth transitions
            if (foundNode !== targetHoverNode || foundConnection !== targetHoverConnection) {
              targetHoverNode = foundNode;
              targetHoverConnection = foundConnection;
              
              // Smooth transition for hover states
              setTimeout(() => {
                hoverNode = targetHoverNode;
                hoverConnection = targetHoverConnection;
              }, 50); // Small delay for smooth transition
            }
          });
          
          canvas.addEventListener('mouseleave', () => {
            targetHoverNode = null;
            targetHoverConnection = null;
            setTimeout(() => {
              hoverNode = null;
              hoverConnection = null;
            }, 50);
          });

          // Handle clicks on buttons inside nodes and connections
          canvas.addEventListener('click', (e) => {
            const rect = canvas.getBoundingClientRect();
            const sx = e.clientX - rect.left;
            const sy = e.clientY - rect.top;
            const {x, y} = screenToWorld(sx, sy);

            // 1. If in connection mode, handle connection creation or cancel
            if (addConnectionFrom) {
              let foundTarget = false;
              for (const node of nodes) {
                if (x >= node.x - node.width/2 && x <= node.x + node.width/2 &&
                    y >= node.y - node.height/2 && y <= node.y + node.height/2) {
                  if (node.id !== addConnectionFrom) {
                    // Add connection
                    fetch(`/api/profile/{{username}}/${tab}/story/connection`, {
                      method: 'POST',
                      headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({from: addConnectionFrom, to: node.id, action: 'add'})
                    }).then(response => response.json()).then(data => {
                      if (data.success) {
                        // Update local connections array
                        connections.push({from: addConnectionFrom, to: node.id});
                        // Update story connections
                        story.connections = connections;
                        // Exit connection mode after creating one connection
                        addConnectionFrom = null;
                        canvas.style.cursor = 'grab';
                        draw(); // Redraw to show new connection
                      }
                    }).catch(error => {
                      console.error('Error creating connection:', error);
                    });
                  } else {
                    // Clicked on the same node - cancel connection mode
                    addConnectionFrom = null;
                    canvas.style.cursor = 'grab';
                    draw();
                  }
                  foundTarget = true;
                  break;
                }
              }
              // If clicked on empty space, stay in connection mode (don't cancel)
              if (!foundTarget) {
                // Optional: Add visual feedback that we're still in connection mode
                draw();
              }
              return;
            }

            // 2. Check for connection click (delete connection)
            if (hoverConnection) {
              // Check if click is on the delete button (center of connection)
              const fromNode = nodes.find(n => n.id === hoverConnection.from);
              const toNode = nodes.find(n => n.id === hoverConnection.to);
              if (fromNode && toNode) {
                const dx = toNode.x - fromNode.x;
                const dy = toNode.y - fromNode.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                if (distance > 0) {
                  const unitX = dx / distance;
                  const unitY = dy / distance;
                  const startX = fromNode.x + unitX * (fromNode.width / 2);
                  const startY = fromNode.y + unitY * (fromNode.height / 2);
                  const endX = toNode.x - unitX * (toNode.width / 2);
                  const endY = toNode.y - unitY * (toNode.height / 2);
                  
                  const midX = (startX + endX) / 2;
                  const midY = (startY + endY) / 2;
                  const btnSize = 24;
                  
                  // Check if click is within the delete button area
                  const clickDistance = Math.sqrt((x - midX) * (x - midX) + (y - midY) * (y - midY));
                  if (clickDistance <= btnSize/2) {
                    // Delete the connection
                    fetch(`/api/profile/{{username}}/${tab}/story/connection`, {
                      method: 'POST',
                      headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({from: hoverConnection.from, to: hoverConnection.to, action: 'remove'})
                    }).then(response => response.json()).then(data => {
                      if (data.success) {
                        // Remove from local connections array
                        connections = connections.filter(c => !(c.from === hoverConnection.from && c.to === hoverConnection.to));
                        // Update story connections
                        story.connections = connections;
                        hoverConnection = null;
                        draw(); // Redraw to show updated connections
                      }
                    }).catch(error => {
                      console.error('Error deleting connection:', error);
                    });
                    return;
                  }
                }
              }
            }

            // 3. If not hovering a node, do nothing
            if (!hoverNode) return;

            // 4. Check if click is on +Node or +Conn button (right side)
            const node = hoverNode;
            const nodeX = node.x - node.width/2;
            const nodeY = node.y - node.height/2;
            const btnColWidth = 36;
            const btnHeight = (node.height - 0) / 2;
            const btnX = nodeX + node.width - btnColWidth;
            const btnY1 = nodeY;
            const btnY2 = nodeY + btnHeight;
            // +Node
            if (x >= btnX && x <= btnX + btnColWidth && y >= btnY1 && y <= btnY1 + btnHeight) {
              showNodeModal(null, tab, story, {x:node.x+150, y:node.y+150}, node.id);
              return;
            }
            // +Conn
            if (x >= btnX && x <= btnX + btnColWidth && y >= btnY2 && y <= btnY2 + btnHeight) {
              addConnectionFrom = node.id;
              canvas.style.cursor = 'crosshair';
              draw();
              return;
            }
          });

          // Mouse event handlers with smooth transitions
          canvas.addEventListener('mousedown', (e) => {
            const rect = canvas.getBoundingClientRect();
            const sx = e.clientX - rect.left;
            const sy = e.clientY - rect.top;
            const {x, y} = screenToWorld(sx, sy);
            let hitNode = null;
            for (let i = nodes.length - 1; i >= 0; i--) {
              const node = nodes[i];
              if (x >= node.x - node.width/2 && x <= node.x + node.width/2 &&
                  y >= node.y - node.height/2 && y <= node.y + node.height/2) {
                hitNode = node;
                break;
              }
            }
            if (hitNode) {
              selectedNode = hitNode;
              isDragging = true;
              dragOffset.x = x - hitNode.x;
              dragOffset.y = y - hitNode.y;
              canvas.style.cursor = 'grabbing';
              // showNodeMenu(hitNode, sx, sy); // REMOVE THIS LINE
            } else {
              isPanning = true;
              panStart.x = sx;
              panStart.y = sy;
              canvas.style.cursor = 'grabbing';
            }
          });

          canvas.addEventListener('mousemove', (e) => {
            const rect = canvas.getBoundingClientRect();
            const sx = e.clientX - rect.left;
            const sy = e.clientY - rect.top;
            const {x, y} = screenToWorld(sx, sy);
            if (isDragging && selectedNode) {
              selectedNode.x = x - dragOffset.x;
              selectedNode.y = y - dragOffset.y;
              // Update story positions
              story.nodePositions[selectedNode.id] = { x: selectedNode.x, y: selectedNode.y };
            } else if (isPanning) {
              viewport.x += sx - panStart.x;
              viewport.y += sy - panStart.y;
              panStart.x = sx;
              panStart.y = sy;
            }
          });

          canvas.addEventListener('mouseup', () => {
            isDragging = false;
            selectedNode = null;
            isPanning = false;
            // Reset cursor based on connection mode with smooth transition
            if (addConnectionFrom) {
              canvas.style.cursor = 'crosshair';
            } else {
              canvas.style.cursor = 'grab';
            }
            // Persist node positions to backend
            fetch(`/api/profile/{{username}}/${tab}/story`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ nodePositions: story.nodePositions })
            });
          });

          // Double click to edit node
          canvas.addEventListener('dblclick', (e) => {
            const rect = canvas.getBoundingClientRect();
            const sx = e.clientX - rect.left;
            const sy = e.clientY - rect.top;
            const {x, y} = screenToWorld(sx, sy);
            for (const node of nodes) {
              if (x >= node.x - node.width/2 && x <= node.x + node.width/2 &&
                  y >= node.y - node.height/2 && y <= node.y + node.height/2) {
                showNodeModal(node, tab, story);
                break;
              }
            }
          });

          // Node menu with smooth animations
          function showNodeMenu(node, sx, sy) {
            if (nodeMenu) {
              // Smooth fade out
              nodeMenu.style.opacity = '0';
              nodeMenu.style.transform = 'scale(0.9)';
              setTimeout(() => nodeMenu.remove(), 150);
            }
            
            nodeMenu = document.createElement('div');
            nodeMenu.style = `
              position: absolute;
              left: ${sx+20}px;
              top: ${sy+20}px;
              z-index: 1002;
              background: #23272b;
              border: 2px solid #2d88ff;
              border-radius: 10px;
              padding: 18px;
              box-shadow: 0 4px 24px rgba(45,136,255,0.3);
              display: flex;
              flex-direction: column;
              gap: 12px;
              min-width: 180px;
              opacity: 0;
              transform: scale(0.9);
              transition: all 0.2s ease;
            `;
            
            // Edit
            const editBtn = document.createElement('button');
            editBtn.textContent = 'Edit Node';
            editBtn.style = 'background:#2d88ff;color:#fff;border:none;border-radius:8px;padding:10px 18px;font-weight:600;cursor:pointer;transition:all 0.2s ease;';
            editBtn.onmouseenter = () => editBtn.style.transform = 'scale(1.05)';
            editBtn.onmouseleave = () => editBtn.style.transform = 'scale(1)';
            editBtn.onclick = () => { 
              showNodeModal(node, tab, story); 
              nodeMenu.remove(); 
            };
            
            // Add Node
            const addNodeBtn = document.createElement('button');
            addNodeBtn.textContent = '+ Add Node';
            addNodeBtn.style = 'background:#27ae60;color:#fff;border:none;border-radius:8px;padding:10px 18px;font-weight:600;cursor:pointer;transition:all 0.2s ease;';
            addNodeBtn.onmouseenter = () => addNodeBtn.style.transform = 'scale(1.05)';
            addNodeBtn.onmouseleave = () => addNodeBtn.style.transform = 'scale(1)';
            addNodeBtn.onclick = () => { 
              showNodeModal(null, tab, story, {x:node.x+150, y:node.y+150}, node.id); 
              nodeMenu.remove(); 
            };
            
            // Add Connection
            const addConnBtn = document.createElement('button');
            addConnBtn.textContent = '+ Add Connection';
            addConnBtn.style = 'background:#f39c12;color:#fff;border:none;border-radius:8px;padding:10px 18px;font-weight:600;cursor:pointer;transition:all 0.2s ease;';
            addConnBtn.onmouseenter = () => addConnBtn.style.transform = 'scale(1.05)';
            addConnBtn.onmouseleave = () => addConnBtn.style.transform = 'scale(1)';
            addConnBtn.onclick = () => { 
              addConnectionFrom = node.id; 
              canvas.style.cursor = 'crosshair';
              nodeMenu.remove(); 
            };
            
            // Cancel Connection Mode (if active)
            if (addConnectionFrom) {
              const cancelConnBtn = document.createElement('button');
              cancelConnBtn.textContent = '❌ Cancel Connection';
              cancelConnBtn.style = 'background:#e74c3c;color:#fff;border:none;border-radius:8px;padding:10px 18px;font-weight:600;cursor:pointer;transition:all 0.2s ease;';
              cancelConnBtn.onmouseenter = () => cancelConnBtn.style.transform = 'scale(1.05)';
              cancelConnBtn.onmouseleave = () => cancelConnBtn.style.transform = 'scale(1)';
              cancelConnBtn.onclick = () => { 
                addConnectionFrom = null; 
                canvas.style.cursor = 'grab';
                nodeMenu.remove(); 
              };
              nodeMenu.appendChild(cancelConnBtn);
            }
            
            nodeMenu.appendChild(editBtn);
            nodeMenu.appendChild(addNodeBtn);
            nodeMenu.appendChild(addConnBtn);
            document.body.appendChild(nodeMenu);
            
            // Smooth fade in
            setTimeout(() => {
              nodeMenu.style.opacity = '1';
              nodeMenu.style.transform = 'scale(1)';
            }, 10);
            
            // Remove menu on click elsewhere with smooth transition
            setTimeout(() => {
              document.addEventListener('mousedown', function handler(ev) {
                if (!nodeMenu.contains(ev.target)) { 
                  nodeMenu.style.opacity = '0';
                  nodeMenu.style.transform = 'scale(0.9)';
                  setTimeout(() => {
                    if (nodeMenu && nodeMenu.parentNode) {
                      nodeMenu.remove();
                    }
                  }, 150);
                  document.removeEventListener('mousedown', handler); 
                }
              });
            }, 10);
          }
        }
        async function loadTabMedia(relPath=null, reset=false) {
          if (mediaLoading) return;
          mediaLoading = true;
          profileSpinner.style.display = 'flex';
          profileError.style.display = 'none';
          if (reset) { profileMedia.innerHTML = ''; mediaOffset = 0; mediaEnd = false; }
          let url = `/api/profile/{{username}}/${currentTab}/media?offset=${mediaOffset}&limit=${MEDIA_BATCH}`;
          if (relPath) url += `&rel_path=${encodeURIComponent(relPath)}`;
          try {
            const res = await fetch(url);
            const items = await res.json();
            if (res.status !== 200) throw new Error(items.error || 'Failed to load');
            if (items.length < MEDIA_BATCH) mediaEnd = true;
            renderTabMedia(items, relPath);
            mediaOffset += items.length;
            currentRelPath = relPath;
          } catch (e) {
            profileError.textContent = e.message;
            profileError.style.display = 'block';
          }
          profileSpinner.style.display = 'none';
          mediaLoading = false;
        }
        function renderTabMedia(items, relPath) {
          // Albums
          const albums = items.filter(x => x.type === 'album');
          if (albums.length > 0) {
            const albumRow = document.createElement('div');
            albumRow.style.display = 'flex';
            albumRow.style.flexWrap = 'wrap';
            albumRow.style.gap = '10px';
            albums.forEach(album => {
              const card = document.createElement('div');
              card.className = 'album-card';
              card.style.position = 'relative';
              card.innerHTML = `<h4>${album.name}</h4>`;
              
              // Add delete button to album card
              const deleteBtn = document.createElement('button');
              deleteBtn.innerHTML = '🗑️';
              deleteBtn.title = 'Delete this album';
              deleteBtn.style = 'position:absolute;top:5px;right:5px;background:#e74c3c;color:#fff;border:none;border-radius:50%;width:25px;height:25px;font-size:12px;cursor:pointer;display:none;z-index:10;transition:background 0.2s;';
              deleteBtn.onmouseenter = () => deleteBtn.style.background = '#c0392b';
              deleteBtn.onmouseleave = () => deleteBtn.style.background = '#e74c3c';
              deleteBtn.onclick = (e) => {
                e.stopPropagation();
                if (confirm(`Are you sure you want to delete the album "${album.name}" and all its contents? This cannot be undone.`)) {
                  deleteAlbum(album.name, relPath);
                }
              };
              
              card.appendChild(deleteBtn);
              
              // Show/hide delete button on hover
              card.addEventListener('mouseenter', () => {
                deleteBtn.style.display = 'block';
              });
              card.addEventListener('mouseleave', () => {
                deleteBtn.style.display = 'none';
              });
              
              card.onclick = () => {
                const nextRelPath = relPath ? relPath + '/' + album.name : album.name;
                albumStack.push(nextRelPath);
                profileMedia.innerHTML = '';
                mediaOffset = 0;
                mediaEnd = false;
                loadTabMedia(nextRelPath, true);
                // Add back button and upload button
                const navButtons = document.createElement('div');
                navButtons.style = 'margin-bottom:16px;display:flex;gap:12px;align-items:center;';
                
                const backBtn = document.createElement('button');
                backBtn.className = 'modal-back';
                backBtn.style = 'background:#2d88ff;color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:1.08em;font-weight:500;cursor:pointer;box-shadow:0 2px 8px rgba(45,136,255,0.10);display:inline-flex;align-items:center;gap:8px;transition:background 0.18s,color 0.18s;';
                backBtn.innerHTML = '<span style="font-size:1.2em;">&#8592;</span> Back';
                backBtn.onmouseenter = () => backBtn.style.background = '#1761b0';
                backBtn.onmouseleave = () => backBtn.style.background = '#2d88ff';
                backBtn.onclick = () => {
                  albumStack.pop();
                  let parent = albumStack.length > 0 ? albumStack[albumStack.length-1] : null;
                  profileMedia.innerHTML = '';
                  mediaOffset = 0;
                  mediaEnd = false;
                  loadTabMedia(parent, true);
                };
                
                const albumUploadBtn = document.createElement('button');
                albumUploadBtn.innerHTML = '📁+ Upload to Album';
                albumUploadBtn.style = 'background:#28a745;color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:1.08em;font-weight:500;cursor:pointer;box-shadow:0 2px 8px rgba(40,167,69,0.10);display:inline-flex;align-items:center;gap:8px;transition:background 0.18s,color 0.18s;';
                albumUploadBtn.onmouseenter = () => albumUploadBtn.style.background = '#218838';
                albumUploadBtn.onmouseleave = () => albumUploadBtn.style.background = '#28a745';
                albumUploadBtn.onclick = () => {
                  showAlbumUploadModal(currentTab, nextRelPath, album.name);
                };
                
                navButtons.appendChild(backBtn);
                navButtons.appendChild(albumUploadBtn);
                profileMedia.prepend(navButtons);
              };
              albumRow.appendChild(card);
            });
            profileMedia.appendChild(albumRow);
          }
          // Images and videos
          const grid = document.createElement('div');
          grid.className = 'media-grid';
          items.forEach(item => {
            if (item.type === 'image') {
              const container = document.createElement('div');
              container.style.position = 'relative';
              
              const img = document.createElement('img');
              img.src = item.url;
              img.loading = 'lazy';
              img.style.cursor = 'pointer';
              img.onclick = () => openMediaModalWithKey(item);
              
              // Add delete button
              const deleteBtn = document.createElement('button');
              deleteBtn.innerHTML = '🗑️';
              deleteBtn.title = 'Delete this image';
              deleteBtn.style = 'position:absolute;top:8px;right:8px;background:rgba(231,76,60,0.9);color:#fff;border:none;border-radius:50%;width:30px;height:30px;font-size:14px;cursor:pointer;display:none;z-index:10;transition:background 0.2s;';
              deleteBtn.onmouseenter = () => deleteBtn.style.background = 'rgba(192,57,43,0.9)';
              deleteBtn.onmouseleave = () => deleteBtn.style.background = 'rgba(231,76,60,0.9)';
              deleteBtn.onclick = (e) => {
                e.stopPropagation();
                if (confirm(`Are you sure you want to delete "${item.name}"? This cannot be undone.`)) {
                  deleteMedia(item.name, currentRelPath);
                }
              };
              
              container.appendChild(img);
              container.appendChild(deleteBtn);
              
              // Show/hide delete button on hover
              container.addEventListener('mouseenter', () => {
                deleteBtn.style.display = 'block';
              });
              container.addEventListener('mouseleave', () => {
                deleteBtn.style.display = 'none';
              });
              
              grid.appendChild(container);
            } else if (item.type === 'video') {
              const wrapper = document.createElement('div');
              wrapper.className = 'video-thumb-wrapper';
              wrapper.style.position = 'relative';
              
              const img = document.createElement('img');
              img.src = item.thumb || item.url;
              img.alt = 'Video thumbnail';
              img.loading = 'lazy';
              img.style.cursor = 'pointer';
              img.onclick = () => openMediaModalWithKey(item);
              
              const overlay = document.createElement('div');
              overlay.className = 'play-overlay';
              overlay.innerHTML = '►';
              
              // Add delete button
              const deleteBtn = document.createElement('button');
              deleteBtn.innerHTML = '🗑️';
              deleteBtn.title = 'Delete this video';
              deleteBtn.style = 'position:absolute;top:8px;right:8px;background:rgba(231,76,60,0.9);color:#fff;border:none;border-radius:50%;width:30px;height:30px;font-size:14px;cursor:pointer;display:none;z-index:10;transition:background 0.2s;';
              deleteBtn.onmouseenter = () => deleteBtn.style.background = 'rgba(192,57,43,0.9)';
              deleteBtn.onmouseleave = () => deleteBtn.style.background = 'rgba(231,76,60,0.9)';
              deleteBtn.onclick = (e) => {
                e.stopPropagation();
                if (confirm(`Are you sure you want to delete "${item.name}"? This cannot be undone.`)) {
                  deleteMedia(item.name, currentRelPath);
                }
              };
              
              wrapper.appendChild(img);
              wrapper.appendChild(overlay);
              wrapper.appendChild(deleteBtn);
              
              // Show/hide delete button on hover
              wrapper.addEventListener('mouseenter', () => {
                deleteBtn.style.display = 'block';
              });
              wrapper.addEventListener('mouseleave', () => {
                deleteBtn.style.display = 'none';
              });
              
              grid.appendChild(wrapper);
            }
          });
          if (grid.children.length > 0) profileMedia.appendChild(grid);
        }
        // Infinite scroll
        function checkProfileScroll() {
          const el = document.getElementById('profile-root');
          if (el.scrollTop + el.clientHeight > el.scrollHeight - 300 && !mediaEnd) {
            loadTabMedia(currentRelPath);
          }
        }
        document.getElementById('profile-root').addEventListener('scroll', checkProfileScroll);
        window.addEventListener('scroll', checkProfileScroll);
        
        // Add album upload modal function
        function showAlbumUploadModal(tabName, albumPath, albumDisplayName) {
          const modalRoot = document.getElementById('modal-root');
          modalRoot.innerHTML = '';
          
          const modal = document.createElement('div');
          modal.className = 'modal';
          
          const content = document.createElement('div');
          content.className = 'modal-content';
          content.style.maxWidth = '600px';
          content.style.height = 'auto';
          content.style.maxHeight = '80vh';
          content.style.overflow = 'auto';
          
          // Create the close button
          const closeBtn = document.createElement('button');
          closeBtn.className = 'modal-close';
          closeBtn.innerHTML = '&times;';
          closeBtn.onclick = () => modalRoot.innerHTML = '';
          
          // Create the content div
          const contentDiv = document.createElement('div');
          contentDiv.style.padding = '2rem';
          contentDiv.innerHTML = `
            <h2 style="margin: 0 0 1.5rem 0; color: #2d88ff; font-size: 1.5em;">Upload to Album: ${albumDisplayName}</h2>
            <div style="margin-bottom: 1rem; padding: 12px; background: #3a3b3c; border-radius: 8px; color: #aaa; font-size: 0.9em;">
              Uploading to: ${tabName}/${albumPath}
            </div>
            <div style="margin-bottom: 1.5rem;">
              <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Upload Files</label>
              <input type="file" id="album-file-upload" multiple accept="image/*,video/*" 
                     style="width: 100%; padding: 8px; border-radius: 8px; border: 1.5px solid #333; background: #222; color: #e4e6eb;">
              <div id="album-file-list" style="margin-top: 0.5rem; font-size: 0.9em; color: #aaa;"></div>
            </div>
            <div style="margin-bottom: 1.5rem;">
              <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Create Subfolder</label>
              <div style="display: flex; gap: 8px;">
                <input type="text" id="album-folder-name" placeholder="Enter subfolder name..." 
                       style="flex: 1; padding: 12px; border-radius: 8px; border: 1.5px solid #333; background: #222; color: #e4e6eb; font-size: 1em;">
                <button id="album-add-folder-btn" 
                        style="padding: 12px 16px; background: #2d88ff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                  Create Folder
                </button>
              </div>
              <div id="album-folder-list" style="margin-top: 0.5rem; font-size: 0.9em; color: #aaa;"></div>
            </div>
            <div style="display: flex; gap: 12px; justify-content: flex-end;">
              <button id="album-cancel-btn" 
                      style="padding: 12px 24px; background: #3a3b3c; color: #e4e6eb; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                Cancel
              </button>
              <button id="album-upload-btn" 
                      style="padding: 12px 24px; background: #28a745; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                Upload Files
              </button>
            </div>
          `;
          
          // Append close button and content to modal
          content.appendChild(closeBtn);
          content.appendChild(contentDiv);
          modal.appendChild(content);
          modalRoot.appendChild(modal);
          
          // Event handlers
          const fileUpload = document.getElementById('album-file-upload');
          const fileList = document.getElementById('album-file-list');
          const folderNameInput = document.getElementById('album-folder-name');
          const folderList = document.getElementById('album-folder-list');
          const addFolderBtn = document.getElementById('album-add-folder-btn');
          const uploadBtn = document.getElementById('album-upload-btn');
          const cancelBtn = document.getElementById('album-cancel-btn');
          
          let selectedFiles = [];
          let foldersToCreate = [];
          
          // File upload handling
          fileUpload.addEventListener('change', (e) => {
            selectedFiles = Array.from(e.target.files);
            fileList.innerHTML = selectedFiles.map(file => 
              `<div style="padding: 4px 0; border-bottom: 1px solid #333;">📄 ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)</div>`
            ).join('');
          });
          
          // Folder creation handling
          addFolderBtn.addEventListener('click', async () => {
            const folderName = folderNameInput.value.trim();
            if (folderName && !foldersToCreate.includes(folderName)) {
              try {
                const response = await fetch(`/api/profile/{{username}}/${tabName}/upload?album_path=${encodeURIComponent(albumPath)}`, {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({folder_name: folderName})
                });
                
                if (response.ok) {
                  foldersToCreate.push(folderName);
                  folderList.innerHTML = foldersToCreate.map(folder => 
                    `<div style="padding: 4px 0; border-bottom: 1px solid #333;">📁 ${folder} ✓</div>`
                  ).join('');
                  folderNameInput.value = '';
                } else {
                  const error = await response.json();
                  alert('Error creating folder: ' + (error.error || 'Unknown error'));
                }
              } catch (e) {
                alert('Error creating folder: ' + e.message);
              }
            }
          });
          
          // Upload files
          uploadBtn.addEventListener('click', async () => {
            if (selectedFiles.length === 0) {
              alert('Please select files to upload');
              return;
            }
            
            try {
              uploadBtn.disabled = true;
              uploadBtn.textContent = 'Uploading...';
              
              const formData = new FormData();
              selectedFiles.forEach(file => {
                formData.append('files', file);
              });
              formData.append('album_path', albumPath);
              
              const response = await fetch(`/api/profile/{{username}}/${tabName}/upload`, {
                method: 'POST',
                body: formData
              });
              
              if (response.ok) {
                const result = await response.json();
                alert(result.message);
                modalRoot.innerHTML = '';
                // Refresh the current view and maintain navigation buttons
                await refreshCurrentAlbumView();
              } else {
                const error = await response.json();
                alert('Error uploading files: ' + (error.error || 'Unknown error'));
              }
            } catch (e) {
              alert('Error uploading files: ' + e.message);
            } finally {
              uploadBtn.disabled = false;
              uploadBtn.textContent = 'Upload Files';
            }
          });
          
          // Cancel button
          cancelBtn.addEventListener('click', () => {
            modalRoot.innerHTML = '';
          });
          
          // Close on outside click
          modal.addEventListener('click', (e) => {
            if (e.target === modal) {
              modalRoot.innerHTML = '';
            }
          });
        }
        
        // Helper function to refresh current album view while maintaining navigation buttons
        async function refreshCurrentAlbumView() {
          profileMedia.innerHTML = '';
          mediaOffset = 0;
          mediaEnd = false;
          
          // Load the media content first
          await loadTabMedia(currentRelPath, false);
          
          // If we're in an album (currentRelPath exists), add back navigation buttons after loading
          if (currentRelPath && albumStack.length > 0) {
            const navButtons = document.createElement('div');
            navButtons.style = 'margin-bottom:16px;display:flex;gap:12px;align-items:center;';
            
            const backBtn = document.createElement('button');
            backBtn.className = 'modal-back';
            backBtn.style = 'background:#2d88ff;color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:1.08em;font-weight:500;cursor:pointer;box-shadow:0 2px 8px rgba(45,136,255,0.10);display:inline-flex;align-items:center;gap:8px;transition:background 0.18s,color 0.18s;';
            backBtn.innerHTML = '<span style="font-size:1.2em;">&#8592;</span> Back';
            backBtn.onmouseenter = () => backBtn.style.background = '#1761b0';
            backBtn.onmouseleave = () => backBtn.style.background = '#2d88ff';
            backBtn.onclick = () => {
              albumStack.pop();
              let parent = albumStack.length > 0 ? albumStack[albumStack.length-1] : null;
              profileMedia.innerHTML = '';
              mediaOffset = 0;
              mediaEnd = false;
              loadTabMedia(parent, true);
            };
            
            const albumUploadBtn = document.createElement('button');
            albumUploadBtn.innerHTML = '📁+ Upload to Album';
            albumUploadBtn.style = 'background:#28a745;color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:1.08em;font-weight:500;cursor:pointer;box-shadow:0 2px 8px rgba(40,167,69,0.10);display:inline-flex;align-items:center;gap:8px;transition:background 0.18s,color 0.18s;';
            albumUploadBtn.onmouseenter = () => albumUploadBtn.style.background = '#218838';
            albumUploadBtn.onmouseleave = () => albumUploadBtn.style.background = '#28a745';
            albumUploadBtn.onclick = () => {
              // Get the current album name from currentRelPath
              const albumName = currentRelPath.split('/').pop();
              showAlbumUploadModal(currentTab, currentRelPath, albumName);
            };
            
            navButtons.appendChild(backBtn);
            navButtons.appendChild(albumUploadBtn);
            profileMedia.prepend(navButtons);
          }
        }
        
        // Delete album function
        async function deleteAlbum(albumName, relPath) {
          try {
            const albumPath = relPath ? relPath + '/' + albumName : albumName;
            const response = await fetch(`/api/profile/{{username}}/${currentTab}/delete_album`, {
              method: 'DELETE',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({album_path: albumPath})
            });
            
            if (response.ok) {
              alert('Album deleted successfully');
              // Refresh current view
              profileMedia.innerHTML = '';
              mediaOffset = 0;
              mediaEnd = false;
              await loadTabMedia(currentRelPath, true);
            } else {
              const error = await response.json();
              alert('Error deleting album: ' + (error.error || 'Unknown error'));
            }
          } catch (e) {
            alert('Error deleting album: ' + e.message);
          }
        }
        
        // Delete media function
        async function deleteMedia(mediaName, relPath) {
          try {
            const mediaPath = relPath ? relPath + '/' + mediaName : mediaName;
            const response = await fetch(`/api/profile/{{username}}/${currentTab}/delete_media`, {
              method: 'DELETE',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({media_path: mediaPath})
            });
            
            if (response.ok) {
              alert('Media deleted successfully');
              // Refresh current view
              await refreshCurrentAlbumView();
            } else {
              const error = await response.json();
              alert('Error deleting media: ' + (error.error || 'Unknown error'));
            }
          } catch (e) {
            alert('Error deleting media: ' + e.message);
          }
        }
        
        // Add content to existing tab function
        function showAddContentModal(tabName) {
          const modalRoot = document.getElementById('modal-root');
          modalRoot.innerHTML = '';
          
          const modal = document.createElement('div');
          modal.className = 'modal';
          
          const content = document.createElement('div');
          content.className = 'modal-content';
          content.style.maxWidth = '600px';
          content.style.height = 'auto';
          content.style.maxHeight = '80vh';
          content.style.overflow = 'auto';
          
          // Create the close button
          const closeBtn = document.createElement('button');
          closeBtn.className = 'modal-close';
          closeBtn.innerHTML = '&times;';
          closeBtn.onclick = () => modalRoot.innerHTML = '';
          
          // Create the content div
          const contentDiv = document.createElement('div');
          contentDiv.style.padding = '2rem';
          contentDiv.innerHTML = `
            <h2 style="margin: 0 0 1.5rem 0; color: #2d88ff; font-size: 1.5em;">Add Content to "${tabName}"</h2>
            
            <div style="margin-bottom: 1.5rem;">
              <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Upload Files</label>
              <input type="file" id="file-upload" multiple accept="image/*,video/*" 
                     style="width: 100%; padding: 8px; border-radius: 8px; border: 1.5px solid #333; background: #222; color: #e4e6eb;">
              <div id="file-list" style="margin-top: 0.5rem; font-size: 0.9em; color: #aaa;"></div>
            </div>
            
            <div style="margin-bottom: 1.5rem;">
              <label style="display: block; margin-bottom: 0.5rem; color: #e4e6eb; font-weight: 600;">Create Folder</label>
              <div style="display: flex; gap: 8px;">
                <input type="text" id="folder-name" placeholder="Enter folder name..." 
                       style="flex: 1; padding: 12px; border-radius: 8px; border: 1.5px solid #333; background: #222; color: #e4e6eb; font-size: 1em;">
                <button id="add-folder-btn" 
                        style="padding: 12px 16px; background: #2d88ff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                  Add Folder
                </button>
              </div>
              <div id="folder-list" style="margin-top: 0.5rem; font-size: 0.9em; color: #aaa;"></div>
            </div>
            
            <div style="display: flex; gap: 12px; justify-content: flex-end;">
              <button id="cancel-btn" 
                      style="padding: 12px 24px; background: #3a3b3c; color: #e4e6eb; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                Cancel
              </button>
              <button id="add-content-btn" 
                      style="padding: 12px 24px; background: #2d88ff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;">
                Add Content
              </button>
            </div>
          `;
          
          // Append close button and content to modal
          content.appendChild(closeBtn);
          content.appendChild(contentDiv);
          modal.appendChild(content);
          modalRoot.appendChild(modal);
          
          // Event handlers
          const fileUpload = document.getElementById('file-upload');
          const fileList = document.getElementById('file-list');
          const folderNameInput = document.getElementById('folder-name');
          const folderList = document.getElementById('folder-list');
          const addFolderBtn = document.getElementById('add-folder-btn');
          const addContentBtn = document.getElementById('add-content-btn');
          const cancelBtn = document.getElementById('cancel-btn');
          
          let selectedFiles = [];
          let foldersToCreate = [];
          
          // File upload handling
          fileUpload.addEventListener('change', (e) => {
            selectedFiles = Array.from(e.target.files);
            fileList.innerHTML = selectedFiles.map(file => 
              `<div style="padding: 4px 0; border-bottom: 1px solid #333;">📄 ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)</div>`
            ).join('');
          });
          
          // Folder creation handling
          addFolderBtn.addEventListener('click', () => {
            const folderName = folderNameInput.value.trim();
            if (folderName && !foldersToCreate.includes(folderName)) {
              foldersToCreate.push(folderName);
              folderList.innerHTML = foldersToCreate.map(folder => 
                `<div style="padding: 4px 0; border-bottom: 1px solid #333;">📁 ${folder}</div>`
              ).join('');
              folderNameInput.value = '';
            }
          });
          
          // Add content to existing tab
          addContentBtn.addEventListener('click', async () => {
            try {
              // Create folders
              for (const folder of foldersToCreate) {
                await fetch(`/api/profile/{{username}}/${tabName}/upload`, {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({folder_name: folder})
                });
              }
              
              // Upload files
              if (selectedFiles.length > 0) {
                const formData = new FormData();
                selectedFiles.forEach(file => {
                  formData.append('files', file);
                });
                
                await fetch(`/api/profile/{{username}}/${tabName}/upload`, {
                  method: 'POST',
                  body: formData
                });
              }
              
              // Close modal and reload tabs
              modalRoot.innerHTML = '';
              await loadProfileTabs();
              
              // If this tab is currently selected, reload its content
              if (currentTab === tabName) {
                await loadTabMedia(null, true);
              }
              
            } catch (e) {
              alert('Error adding content: ' + e.message);
            }
          });
          
          // Cancel button
          cancelBtn.addEventListener('click', () => {
            modalRoot.innerHTML = '';
          });
          
          // Close on outside click
          modal.addEventListener('click', (e) => {
            if (e.target === modal) {
              modalRoot.innerHTML = '';
            }
          });
        }
        
        loadProfileTabs();

        // Node modal for add/edit (make global) with smooth animations
        function showNodeModal(node, tab, story, pos, connectFromId) {
          // If adding the first node and no pos is given, center it
          if (!node && !pos && (!story.nodes || story.nodes.length === 0)) {
            const canvas = document.getElementById('story-graph-canvas');
            if (canvas) {
              pos = { x: canvas.width / 2, y: canvas.height / 2 };
            } else {
              pos = { x: 400, y: 300 };
            }
          }
          // Function to ensure modal is visible in fullscreen and in correct parent
          function ensureModalRootParent() {
            const modalRoot = document.getElementById('modal-root');
            if (!modalRoot) return;
            if (document.fullscreenElement && document.fullscreenElement.contains(document.getElementById('story-graph-canvas')?.parentNode)) {
              // Move modalRoot to fullscreen element
              if (modalRoot.parentNode !== document.fullscreenElement) {
                document.fullscreenElement.appendChild(modalRoot);
              }
            } else {
              // Move modalRoot to body
              if (modalRoot.parentNode !== document.body) {
                document.body.appendChild(modalRoot);
            }
          }
          }
          ensureModalRootParent();
          
          const modalRoot = document.getElementById('modal-root');
          modalRoot.innerHTML = '';
          const modal = document.createElement('div');
          modal.className = 'modal';
          modal.style.zIndex = '99999'; // Very high z-index for fullscreen compatibility
          modal.style.pointerEvents = 'auto';
          
          const modalContent = document.createElement('div');
          modalContent.className = 'modal-content';
          modalContent.style.width = '900px';
          modalContent.style.height = '520px';
          modalContent.style.maxWidth = 'none';
          modalContent.style.maxHeight = 'none';
          modalContent.style.overflow = 'visible';
          modalContent.style.display = 'flex';
          modalContent.style.flexDirection = 'row';
          modalContent.style.alignItems = 'stretch';
          modalContent.style.background = '#23272b';
          modalContent.style.border = '4px solid #2d88ff';
          modalContent.style.boxShadow = '0 12px 48px #2d88ff55, 0 2px 8px #0002';
          modalContent.style.borderRadius = '2.5rem';
          modalContent.style.padding = '2.5rem';
          modalContent.style.position = 'relative';
          modalContent.style.opacity = '0';
          modalContent.style.transform = 'scale(0.9)';
          modalContent.style.transition = 'all 0.3s ease';
          // Responsive stacking for mobile
          const styleTag = document.createElement('style');
          styleTag.innerHTML = `@media (max-width: 1100px) { .modal-content { flex-direction: column !important; min-width: 0 !important; max-width: 100vw !important; width: 98vw !important; height: auto !important; padding: 1.2rem !important; } }`;
          document.head.appendChild(styleTag);
          
          const closeBtn = document.createElement('button');
          closeBtn.className = 'modal-close';
          closeBtn.innerHTML = '&times;';
          closeBtn.style.zIndex = '100001'; // Very high z-index for fullscreen compatibility
          closeBtn.onclick = () => {
            if (modalContent && modalContent.style) {
              modalContent.style.opacity = '0';
              modalContent.style.transform = 'scale(0.9)';
              setTimeout(() => {
                modalRoot.innerHTML = '';
                ensureModalRootParent(); // Ensure modalRoot is in correct parent after close
                // Refocus canvas for keyboard navigation
                const canvas = document.getElementById('story-graph-canvas');
                if (canvas) canvas.focus && canvas.focus();
              }, 300);
            } else {
              modalRoot.innerHTML = '';
              ensureModalRootParent();
              const canvas = document.getElementById('story-graph-canvas');
              if (canvas) canvas.focus && canvas.focus();
            }
          };
          
          const contentDiv = document.createElement('div');
          contentDiv.style.padding = '2rem';
          // Tips for story writing
          const tips = [
            'Tip: Use Enter to start a new paragraph. Paste from Word/Google Docs for rich stories.',
            'Tip: Write freely! You can use as many paragraphs as you want.',
            'Tip: Use Ctrl+Z to undo and Ctrl+Y to redo your writing.',
            'Tip: You can copy-paste from anywhere, formatting is preserved.',
            'Tip: Use the textarea resize handle to make your writing space bigger.',
            'Tip: Write your story in your own style. There are no limits!',
            'Tip: Use emojis, line breaks, and even poetry in your story.',
            'Tip: Save often to avoid losing your work.',
            'Tip: You can write multi-chapter stories by splitting content into nodes.',
            'Tip: Use the graph to organize your story flow visually.'
          ];
          const randomTip = tips[Math.floor(Math.random() * tips.length)];
          contentDiv.innerHTML = `
            <div style="display: flex; flex-direction: row; gap: 2.5rem; width: 100%; height: 100%; align-items: stretch;">
              <div style="flex: 1 1 260px; display: flex; flex-direction: column; justify-content: flex-start; height: 100%;">
                <h2 style="margin: 0 0 2.2rem 0; color: #2d88ff; font-size: 2.5em; letter-spacing: 0.5px; font-weight: 900; text-shadow: 0 4px 16px #2d88ff33; text-align: left;">${node ? 'Edit' : 'Add'} Node</h2>
                <label for="node-id" style="color: #aaa; font-size: 1.13em; font-weight: 700; margin-bottom: 0.7em;">Node ID</label>
                <input type="text" id="node-id" value="${node ? node.id : ''}" placeholder="Node ID..." style="width: 100%; padding: 22px 18px 10px 18px; border-radius: 16px; border: 2.5px solid #2d88ff; background: #18191a; color: #e4e6eb; font-size: 1.18em; font-weight: 600; transition: border 0.2s, box-shadow 0.2s; box-shadow: 0 2px 8px #2d88ff11; outline: none; margin-bottom: 2.2rem;" ${node ? 'readonly' : ''}>
                <div style="display: flex; flex-direction: column; gap: 1.2em; margin-top: auto;">
                  <button id="cancel-node-btn" style="padding: 16px 32px; background: #3a3b3c; color: #e4e6eb; border: none; border-radius: 12px; cursor: pointer; font-weight: 800; font-size: 1.13em; transition: all 0.2s; box-shadow: 0 2px 8px #2d88ff11;">Cancel</button>
                  <button id="save-node-btn" style="padding: 16px 32px; background: linear-gradient(90deg,#2d88ff 60%,#1761b0 100%); color: #fff; border: none; border-radius: 12px; cursor: pointer; font-weight: 900; font-size: 1.13em; transition: all 0.2s; box-shadow: 0 4px 16px #2d88ff22; letter-spacing: 0.2px;">Save Node</button>
                  ${node ? '<button id="delete-node-btn" style="padding: 16px 32px; background: #e74c3c; color: #fff; border: none; border-radius: 12px; cursor: pointer; font-weight: 800; font-size: 1.13em; transition: all 0.2s; box-shadow: 0 2px 8px #e74c3c22;">Delete Node</button>' : ''}
            </div>
            </div>
              <div style="flex: 2 1 480px; display: flex; flex-direction: column; justify-content: flex-start; height: 100%;">
                <label for="node-content" style="color: #aaa; font-size: 1.13em; font-weight: 700; margin-bottom: 0.7em;">Content</label>
                <textarea id="node-content" placeholder="Write your story here..." style="width:100%;height:340px;min-height:340px;max-height:340px;padding:28px 20px 16px 20px;border-radius:20px;border:2.5px solid #2d88ff;background:linear-gradient(120deg,#18191a 90%,#2d88ff11 100%);color:#e4e6eb;font-size:1.22em;line-height:1.85;resize:vertical;box-shadow:0 6px 24px #2d88ff22;transition:border 0.2s, box-shadow 0.2s;outline:none;font-family:'Segoe UI',Arial,sans-serif;font-weight:500;letter-spacing:0.1px;">${node ? node.content : ''}</textarea>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">
                  <span id="node-content-count" style="font-size:1.08em;color:#aaa;"></span>
                  <span id="node-content-tip" style="font-size:1.05em;color:#2d88ff;background:#23272b;padding:8px 18px;border-radius:10px;font-weight:700;box-shadow:0 2px 8px #2d88ff11;">${randomTip}</span>
            </div>
              </div>
            </div>
            <style>@media (max-width: 1100px) { .modal-content > div { flex-direction: column !important; min-width: 0 !important; max-width: 100vw !important; width: 98vw !important; height: auto !important; } }</style>
          `;
          
          modalContent.appendChild(closeBtn);
          modalContent.appendChild(contentDiv);
          modal.appendChild(modalContent);
          modalRoot.appendChild(modal);
          
          // Smooth fade in
          setTimeout(() => {
            if (modalContent && modalContent.style) {
              modalContent.style.opacity = '1';
              modalContent.style.transform = 'scale(1)';
            }
          }, 10);
          
          // Add hover effects to buttons
          const saveBtn = document.getElementById('save-node-btn');
          const cancelBtn = document.getElementById('cancel-node-btn');
          const nodeIdInput = document.getElementById('node-id');
          const nodeContentTextarea = document.getElementById('node-content');
          
          // Button hover effects
          if (saveBtn) {
            saveBtn.onmouseenter = () => {
              saveBtn.style.transform = 'scale(1.05)';
              saveBtn.style.background = '#1761b0';
            };
            saveBtn.onmouseleave = () => {
              saveBtn.style.transform = 'scale(1)';
              saveBtn.style.background = '#2d88ff';
            };
          }
          
          if (cancelBtn) {
            cancelBtn.onmouseenter = () => {
              cancelBtn.style.transform = 'scale(1.05)';
              cancelBtn.style.background = '#4a5568';
            };
            cancelBtn.onmouseleave = () => {
              cancelBtn.style.transform = 'scale(1)';
              cancelBtn.style.background = '#3a3b3c';
            };
          }
          
          // Input focus effects
          if (nodeIdInput) {
            nodeIdInput.onfocus = () => {
              nodeIdInput.style.borderColor = '#2d88ff';
              nodeIdInput.style.boxShadow = '0 0 0 2px rgba(45,136,255,0.2)';
            };
            nodeIdInput.onblur = () => {
              nodeIdInput.style.borderColor = '#333';
              nodeIdInput.style.boxShadow = 'none';
            };
          }
          
          if (nodeContentTextarea) {
            nodeContentTextarea.onfocus = () => {
              nodeContentTextarea.style.borderColor = '#2d88ff';
              nodeContentTextarea.style.boxShadow = '0 0 0 2px rgba(45,136,255,0.2)';
            };
            nodeContentTextarea.onblur = () => {
              nodeContentTextarea.style.borderColor = '#333';
              nodeContentTextarea.style.boxShadow = 'none';
            };
          }
          
          // Save/cancel logic with smooth transitions
          if (saveBtn) {
            saveBtn.onclick = async () => {
              const id = nodeIdInput ? nodeIdInput.value.trim() : '';
              const nodeContent = nodeContentTextarea ? nodeContentTextarea.value.trim() : '';
              if (!id) return alert('Node ID required');
              // Add loading state
              saveBtn.textContent = 'Saving...';
              saveBtn.style.background = '#27ae60';
              saveBtn.disabled = true;
              // --- FULLSCREEN PERSISTENCE: detect if in fullscreen before save ---
              const oldGraphDiv = document.getElementById('story-graph-canvas')?.parentNode;
              const wasFullscreen = document.fullscreenElement && oldGraphDiv && document.fullscreenElement === oldGraphDiv;
              try {
                await fetch(`/api/profile/{{username}}/${tab}/story/node`, {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({id, content: nodeContent})
                });
                if (!node && pos) {
                  await fetch(`/api/profile/{{username}}/${tab}/story`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({nodePositions: {...story.nodePositions, [id]: pos}})
                  });
                  if (connectFromId) {
                    await fetch(`/api/profile/{{username}}/${tab}/story/connection`, {
                      method: 'POST',
                      headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({from: connectFromId, to: id, action: 'add'})
                    });
                  }
                }
                const storyRes = await fetch(`/api/profile/{{username}}/${tab}/story`);
                if (storyRes.ok) {
                  const latestStory = await storyRes.json();
                if (modalContent && modalContent.style) {
                  modalContent.style.opacity = '0';
                  modalContent.style.transform = 'scale(0.9)';
                  setTimeout(() => {
                    modalRoot.innerHTML = '';
                      ensureModalRootParent();
                      // --- FULLSCREEN PERSISTENCE: re-request fullscreen if needed ---
                      if (wasFullscreen) {
                        const newPersistentGraphDiv = document.getElementById('persistent-story-graph-div');
                        if (newPersistentGraphDiv) {
                          newPersistentGraphDiv.innerHTML = '';
                          buildGraphUIInside(newPersistentGraphDiv, latestStory, tab);
                          if (document.fullscreenElement !== newPersistentGraphDiv) {
                          if (newPersistentGraphDiv.requestFullscreen) newPersistentGraphDiv.requestFullscreen();
                          else if (newPersistentGraphDiv.webkitRequestFullscreen) newPersistentGraphDiv.webkitRequestFullscreen();
                          else if (newPersistentGraphDiv.mozRequestFullScreen) newPersistentGraphDiv.mozRequestFullScreen();
                          else if (newPersistentGraphDiv.msRequestFullscreen) newPersistentGraphDiv.msRequestFullscreen();
                        }
                      }
                      } else {
                        renderStoryTab(latestStory, tab); // Always update story mode immediately after save
                      }
                      renderStoryTab(latestStory, tab); // Always update main story view
                      const canvas = document.getElementById('story-graph-canvas');
                      if (canvas) canvas.focus && canvas.focus();
                  }, 300);
                } else {
                  modalRoot.innerHTML = '';
                    ensureModalRootParent();
                    if (wasFullscreen) {
                      const newPersistentGraphDiv = document.getElementById('persistent-story-graph-div');
                      if (newPersistentGraphDiv) {
                        buildGraphUIInside(newPersistentGraphDiv, latestStory, tab);
                        if (document.fullscreenElement !== newPersistentGraphDiv) {
                        if (newPersistentGraphDiv.requestFullscreen) newPersistentGraphDiv.requestFullscreen();
                        else if (newPersistentGraphDiv.webkitRequestFullscreen) newPersistentGraphDiv.webkitRequestFullscreen();
                        else if (newPersistentGraphDiv.mozRequestFullScreen) newPersistentGraphDiv.mozRequestFullScreen();
                        else if (newPersistentGraphDiv.msRequestFullscreen) newPersistentGraphDiv.msRequestFullscreen();
                      }
                    }
                    } else {
                      renderStoryTab(latestStory, tab);
                    }
                    renderStoryTab(latestStory, tab); // Always update main story view
                    const canvas = document.getElementById('story-graph-canvas');
                    if (canvas) canvas.focus && canvas.focus();
                  }
                } else {
                  if (modalContent && modalContent.style) {
                    modalContent.style.opacity = '0';
                    modalContent.style.transform = 'scale(0.9)';
                    setTimeout(() => {
                      modalRoot.innerHTML = '';
                      ensureModalRootParent(); // Ensure modalRoot is in correct parent after close
                      // Refocus canvas for keyboard navigation
                      const canvas = document.getElementById('story-graph-canvas');
                      if (canvas) canvas.focus && canvas.focus();
                    }, 300);
                  } else {
                    modalRoot.innerHTML = '';
                    ensureModalRootParent();
                    const canvas = document.getElementById('story-graph-canvas');
                    if (canvas) canvas.focus && canvas.focus();
                  }
                }
              } catch (error) {
                console.error('Error saving node:', error);
                alert('Error saving node. Please try again.');
                // Reset button state
                saveBtn.textContent = 'Save Node';
                saveBtn.style.background = '#2d88ff';
                saveBtn.disabled = false;
              }
            };
          }
          
          if (cancelBtn) {
            cancelBtn.onclick = () => {
              if (modalContent && modalContent.style) {
                modalContent.style.opacity = '0';
                modalContent.style.transform = 'scale(0.9)';
                setTimeout(() => {
                  modalRoot.innerHTML = '';
                  ensureModalRootParent(); // Ensure modalRoot is in correct parent after close
                  // Refocus canvas for keyboard navigation
                  const canvas = document.getElementById('story-graph-canvas');
                  if (canvas) canvas.focus && canvas.focus();
                }, 300);
              } else {
                modalRoot.innerHTML = '';
                ensureModalRootParent();
                const canvas = document.getElementById('story-graph-canvas');
                if (canvas) canvas.focus && canvas.focus();
              }
            };
          }
          
          modal.onclick = (e) => { 
            if (e.target === modal) {
              if (modalContent && modalContent.style) {
                modalContent.style.opacity = '0';
                modalContent.style.transform = 'scale(0.9)';
                setTimeout(() => {
                  modalRoot.innerHTML = '';
                  ensureModalRootParent(); // Ensure modalRoot is in correct parent after close
                  // Refocus canvas for keyboard navigation
                  const canvas = document.getElementById('story-graph-canvas');
                  if (canvas) canvas.focus && canvas.focus();
                }, 300);
              } else {
                modalRoot.innerHTML = '';
                ensureModalRootParent();
                const canvas = document.getElementById('story-graph-canvas');
                if (canvas) canvas.focus && canvas.focus();
              }
            }
          };
          // After modal is shown, ensure modalRoot is in correct parent (in case fullscreen was toggled)
          setTimeout(ensureModalRootParent, 20);
          // Add delete logic if editing an existing node
          if (node) {
            const deleteBtn = document.getElementById('delete-node-btn');
            if (deleteBtn) {
              deleteBtn.onmouseenter = () => {
                deleteBtn.style.transform = 'scale(1.05)';
                deleteBtn.style.background = '#c0392b';
              };
              deleteBtn.onmouseleave = () => {
                deleteBtn.style.transform = 'scale(1)';
                deleteBtn.style.background = '#e74c3c';
              };
              deleteBtn.onclick = async () => {
                if (!confirm('Are you sure you want to delete this node? This cannot be undone.')) return;
                deleteBtn.textContent = 'Deleting...';
                deleteBtn.disabled = true;
                try {
                  await fetch(`/api/profile/{{username}}/${tab}/story/node`, {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: node.id})
                  });
                  // After delete, close modal and refresh story/graph
                  modalRoot.innerHTML = '';
                  ensureModalRootParent();
                  // Always update both fullscreen and normal view
                  const res = await fetch(`/api/profile/{{username}}/${tab}/story`);
                  if (res.ok) {
                    const latestStory = await res.json();
                    const newPersistentGraphDiv = document.getElementById('persistent-story-graph-div');
                    if (newPersistentGraphDiv) {
                      newPersistentGraphDiv.innerHTML = '';
                      buildGraphUIInside(newPersistentGraphDiv, latestStory, tab);
                    }
                    renderStoryTab(latestStory, tab);
                  }
                } catch (e) {
                  alert('Error deleting node.');
                }
              };
            }
          }
          // --- Enhance textarea for story writing ---
          if (nodeContentTextarea) {
            // Live word/character count
            function updateCount() {
              const val = nodeContentTextarea.value;
              const words = val.trim().length > 0 ? val.trim().split(/\s+/).length : 0;
              const chars = val.length;
              document.getElementById('node-content-count').textContent = `${words} words • ${chars} chars`;
            }
            nodeContentTextarea.addEventListener('input', updateCount);
            updateCount();
          }
        }
        
        // Global function to ensure modals work in fullscreen mode
        window.ensureModalFullscreenCompatibility = function() {
          const modalRoot = document.getElementById('modal-root');
          if (modalRoot) {
            modalRoot.style.position = 'fixed';
            modalRoot.style.top = '0';
            modalRoot.style.left = '0';
            modalRoot.style.width = '100vw';
            modalRoot.style.height = '100vh';
            modalRoot.style.zIndex = '99998';
            modalRoot.style.pointerEvents = 'none';
          }
        };
        
        // Call this function when entering fullscreen
        document.addEventListener('fullscreenchange', function() {
          if (document.fullscreenElement) {
            window.ensureModalFullscreenCompatibility();
          }
        });
        
        // Also call on webkit and moz fullscreen changes
        document.addEventListener('webkitfullscreenchange', window.ensureModalFullscreenCompatibility);
        document.addEventListener('mozfullscreenchange', window.ensureModalFullscreenCompatibility);

        // Add this helper function after renderStoryTab:
        function buildGraphUIInside(persistentGraphDiv, story, tab) {
          // Fullscreen button
          const fullscreenBtn = document.createElement('button');
          fullscreenBtn.textContent = '⛶';
          fullscreenBtn.title = 'Fullscreen';
          fullscreenBtn.style = 'position:absolute;top:16px;right:16px;z-index:20;background:#23272b;color:#2d88ff;border:none;border-radius:8px;padding:8px 16px;font-size:1.5em;cursor:pointer;box-shadow:0 2px 8px #2d88ff22;';
          fullscreenBtn.onclick = () => {
            if (!document.fullscreenElement) {
              persistentGraphDiv.requestFullscreen();
            } else {
              document.exitFullscreen();
            }
            setTimeout(resizeCanvas, 200);
          };
          persistentGraphDiv.appendChild(fullscreenBtn);

          // Center button
          const centerBtn = document.createElement('button');
          centerBtn.textContent = '🎯';
          centerBtn.title = 'Center on First Node';
          centerBtn.style = 'position:absolute;top:16px;right:64px;z-index:20;background:#23272b;color:#2d88ff;border:none;border-radius:8px;padding:8px 16px;font-size:1.5em;cursor:pointer;box-shadow:0 2px 8px #2d88ff22;';
          centerBtn.onclick = () => {
            if (story.nodes && story.nodes.length > 0) {
              const firstNode = story.nodes[0];
              const pos = story.nodePositions[firstNode.id] || {x:0, y:0};
              if (persistentGraphDiv.viewport) {
                persistentGraphDiv.viewport.x = -pos.x * persistentGraphDiv.viewport.scale;
                persistentGraphDiv.viewport.y = -pos.y * persistentGraphDiv.viewport.scale;
                window.redrawGraph();
              }
            }
          };
          persistentGraphDiv.appendChild(centerBtn);

          // Canvas
          const canvas = document.createElement('canvas');
          canvas.style = 'border:2px solid #333;border-radius:8px;background:#0f0f0f;cursor:grab;width:100%;height:100%;display:block;flex:1 1 auto;';
          canvas.id = 'story-graph-canvas';
          persistentGraphDiv.appendChild(canvas);

          // Resize canvas to fit container
          function resizeCanvas() {
            const rect = persistentGraphDiv.getBoundingClientRect();
            canvas.width = rect.width;
            canvas.height = rect.height;
            if (typeof window.redrawGraph === 'function') window.redrawGraph();
          }
          window.addEventListener('resize', resizeCanvas);
          document.addEventListener('fullscreenchange', resizeCanvas);
          setTimeout(resizeCanvas, 100);

          // If no nodes, show Add Node button in center
          if (!story.nodes || story.nodes.length === 0) {
            const addBtn = document.createElement('button');
            addBtn.textContent = '+ Add Node';
            addBtn.style = 'position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-size:2em;background:#2d88ff;color:#fff;border:none;border-radius:12px;padding:32px 48px;font-weight:700;cursor:pointer;z-index:10;box-shadow:0 4px 24px #2d88ff44;';
            addBtn.onclick = () => showNodeModal(null, tab, story, {x:0, y:0});
            persistentGraphDiv.appendChild(addBtn);
            return;
          }

          // Graph logic
          initGraphVisualization(canvas, story, tab, persistentGraphDiv);
          // Store viewport on persistentGraphDiv for access in centerBtn
          if (persistentGraphDiv.viewport) {
            // already set
          } else {
            persistentGraphDiv.viewport = { x: 0, y: 0, scale: 1 };
          }

          // Ensure #modal-root exists after graph UI rebuild
          let modalRoot = document.getElementById('modal-root');
          if (!modalRoot) {
            modalRoot = document.createElement('div');
            modalRoot.id = 'modal-root';
            if (document.fullscreenElement) {
              document.fullscreenElement.appendChild(modalRoot);
            } else {
              document.body.appendChild(modalRoot);
            }
          }
        }

        document.addEventListener('fullscreenchange', async function() {
          if (!document.fullscreenElement) {
            // Exited fullscreen, reload the latest story and re-render
            if (typeof currentTab !== 'undefined' && currentTab) {
              try {
                const res = await fetch(`/api/profile/{{username}}/${currentTab}/story`);
                if (res.ok) {
                  const latestStory = await res.json();
                  window.currentStory = latestStory; // update global if used
                  renderStoryTab(latestStory, currentTab);
                }
              } catch (e) {
                // Optionally show an error
              }
            }
          }
        });

  // --- THEME TOGGLE (Light/Dark) ---
  function applyTheme(theme) {
    if (theme === 'light') {
      document.body.classList.add('light-theme');
    } else {
      document.body.classList.remove('light-theme');
    }
  }
  function getTheme() {
    return localStorage.getItem('theme') || 'dark';
  }
  function setTheme(theme) {
    localStorage.setItem('theme', theme);
    applyTheme(theme);
  }

  document.addEventListener('DOMContentLoaded', function() {
    const header = document.querySelector('header');
    let btn = document.getElementById('theme-toggle-btn');

    function toggleIcon(t) {
      btn.innerHTML = (t === 'light') ? '☀️' : '🌙';
    }

    if (btn) {
      btn.onclick = () => {
        const current = getTheme();
        const next = (current === 'dark' ? 'light' : 'dark');
        setTheme(next);
        toggleIcon(next);
      };
    } else if (header) {
      btn = document.createElement('button');
      btn.id = 'theme-toggle-btn';
      btn.style.cssText = 'margin-left:18px;background:none;border:none;color:#2d88ff;font-size:1.5em;cursor:pointer;transition:color 0.2s;';
      btn.title = 'Toggle light/dark mode';
      btn.onclick = () => {
        const current = getTheme();
        const next = (current === 'dark' ? 'light' : 'dark');
        setTheme(next);
        toggleIcon(next);
      };
      header.appendChild(btn);
    }

    const initTheme = getTheme();
    applyTheme(initTheme);
    if (btn) toggleIcon(initTheme);
  });

  // --- LIGHT THEME CSS OVERRIDES ---
  const lightStyle = document.createElement('style');
  lightStyle.innerHTML = `
    /* Base containers */
    body.light-theme,
    body.light-theme .modal-content,
    body.light-theme .modal,
    body.light-theme #profile-root,
    body.light-theme #feed-root,
    body.light-theme #story-root,
    body.light-theme #story-modal {
      background: #f7f7fa !important;
      color:      #23272b !important;
    }

    /* Feed & Story cards, modals */
    body.light-theme .feed-card,
    body.light-theme .modal-content,
    body.light-theme .modal-side-col,
    body.light-theme .modal-media-col,
    body.light-theme .modal-comments-section,
    body.light-theme #feed-root,
    body.light-theme #story-root .story-card,
    body.light-theme #story-modal .modal-content {
      background:   #fff !important;
      color:        #23272b !important;
      border-color: #e4e6eb !important;
      box-shadow:   0 2px 8px #0001 !important;
    }

    /* Profile tabs */
    body.light-theme .tab-menu,
    body.light-theme .tab-menu button,
    body.light-theme .tab-btn {
      background:   #fff !important;
      color:        #23272b !important;
      border-color: #e4e6eb !important;
    }
    body.light-theme .tab-menu button.active,
    body.light-theme .tab-btn.active {
      background: #2d88ff !important;
      color:      #fff      !important;
    }

    /* Tab section headers & subtitles */
    body.light-theme .tab-section-header,
    body.light-theme .tab-section-title,
    body.light-theme .tab-section-subtitle {
      background: transparent !important;
      color:      #23272b    !important;
    }

    /* Tab search */
    body.light-theme .tab-search {
      background:   #fff !important;
      color:        #23272b !important;
      border-color: #e4e6eb !important;
    }
    body.light-theme .tab-search::placeholder {
      color: #888 !important;
    }

    /* Album cards */
    body.light-theme .album-card {
      background:   #fff !important;
      color:        #23272b !important;
      border-color: #e4e6eb !important;
      box-shadow:   0 2px 8px #0001 !important;
    }

    /* Profile media grid */
    body.light-theme #profile-media {
      background:   #fff !important;
      border-color: #e4e6eb !important;
    }
    body.light-theme #profile-media img,
    body.light-theme #profile-media video {
      background:   #fff !important;
      color:        #23272b !important;
      box-shadow:   0 1px 6px rgba(0,0,0,0.10) !important;
    }

    /* Modal back & add‑content buttons */
    body.light-theme .modal-back,
    body.light-theme .tab-add-btn {
      background:   #e4e6eb !important;
      color:        #2d88ff !important;
      border-color: #2d88ff !important;
    }
    body.light-theme .modal-back:hover,
    body.light-theme .tab-add-btn:hover {
      background: #2d88ff !important;
      color:      #fff      !important;
    }

    /* Media grid placeholders & thumbnails */
    body.light-theme .media-grid {
      background: transparent !important;
    }
    body.light-theme .media-grid img,
    body.light-theme .media-grid video,
    body.light-theme .video-thumb-wrapper {
      background: #f7f7fa !important;
      box-shadow: 0 1px 6px rgba(0,0,0,0.10) !important;
    }
    body.light-theme .video-thumb-wrapper .play-overlay {
      background: rgba(0,0,0,0.1) !important;
      color:      #2d88ff      !important;
    }

    /* Comments & errors */
    body.light-theme .modal-comments-section,
    body.light-theme .error,
    body.light-theme #feed-error,
    body.light-theme #profile-error {
      background: transparent !important;
      color:      #b00020      !important;
    }
    body.light-theme .spinner:after {
      border-color: #ccc #ccc #888 #888 !important;
    }
    /* Profile-tabs → light / dark */
  body.light-theme #profile-tabs,
  body.light-theme #profile-tabs > * {
    background:   #fff      !important;
    color:        #23272b   !important;
    border-color: #e4e6eb   !important;
  }
  body.light-theme #profile-tabs button {
    background:   #fff              !important;
    color:        #23272b          !important;
    border:       1px solid #e4e6eb !important;
  }
  body.light-theme #profile-tabs button.active,
  body.light-theme #profile-tabs button:hover {
    background: #2d88ff !important;
    color:      #fff      !important;
  }

  body:not(.light-theme) #profile-tabs,
  body:not(.light-theme) #profile-tabs > * {
    background: #18191a !important;
    color:      #e4e6eb !important;
  }
  body:not(.light-theme) #profile-tabs button {
    background:   #18191a !important;
    color:        #e4e6eb !important;
    border:       none      !important;
  }
  body:not(.light-theme) #profile-tabs button.active,
  body:not(.light-theme) #profile-tabs button:hover {
    background: #2d88ff !important;
    color:      #fff      !important;
  }

  /* Profile media grid */
  body.light-theme #profile-media {
    background:   #fff !important;
    border-color: #e4e6eb !important;
  }
  body.light-theme #profile-media img,
  body.light-theme #profile-media video {
    background:   #fff !important;
    color:        #23272b !important;
    box-shadow:   0 1px 6px rgba(0,0,0,0.10) !important;
  }
  /* ——— Override inline dark styles inside #profile-media ——— */
  body.light-theme #profile-media > div[style*="background: rgb(35, 39, 43)"] {
    background: #fff       !important;
    box-shadow: 0 2px 8px #0001 !important;
  }
  body.light-theme #profile-media > div[style*="background: rgb(35, 39, 43)"] h2 {
    color: #2d88ff         !important;
  }
  body.light-theme #profile-media > div[style*="background: rgb(35, 39, 43)"] pre,
  body.light-theme #profile-media > div[style*="background: rgb(35, 39, 43)"] div {
    color: #23272b         !important;
  }
  /* ——— Light‑mode buttons ——— */
  body.light-theme .like-btn,
  body.light-theme .dislike-btn {
    background:   #e4e6eb !important;  /* light grey button */
    color:        #23272b !important;  /* dark text */
    border:       1px solid #ccc !important;
  }
  body.light-theme .like-btn.liked,
  body.light-theme .dislike-btn.disliked {
    background:   #2d88ff !important;  /* blue active */
    color:        #fff     !important;  /* white text */
  }

  /* ——— Light‑mode modals & form fields ——— */
  body.light-theme .modal-content {
    background:   #fff      !important;
    color:        #23272b   !important;
  }
  body.light-theme .modal-content h2,
  body.light-theme .modal-content h3,
  body.light-theme .modal-content p,
  body.light-theme .modal-content label {
    color:        #23272b   !important;
  }
  body.light-theme .modal-content input,
  body.light-theme .modal-content textarea,
  body.light-theme .modal-content select {
    background:   #f7f7fa !important;
    color:        #23272b !important;
    border:       1px solid #ddd !important;
  }
  body.light-theme .modal-content input::placeholder,
  body.light-theme .modal-content textarea::placeholder {
    color: #888     !important;
  }
  body.light-theme .modal-content button {
    background:   #2d88ff !important;
    color:        #fff     !important;
    border:       none     !important;
  }
  body.light-theme .modal-content button:hover {
    background:   #1a6ed8 !important;
  }
  /* ——— Tip “node-content‑tip” override ——— */
  body.light-theme #node-content-tip {
    background: #f7f7fa    !important;
    color:      #23272b    !important;
    box-shadow: 0 2px 8px #0001 !important;
  }

  /* ——— Canvas border & background override ——— */
  body.light-theme #story-graph-canvas {
    background:   #fff      !important;
    border-color: #ddd     !important;  /* light border */
  }


  /* ———————————————————————————————— */
  /* Inline Profile Story cards (override inline styles) */
  body.light-theme #story-content-area > div[style*="background"] {
    background: #fff       !important;
    box-shadow: 0 2px 8px #0001 !important;
  }
  body.light-theme #story-content-area > div[style*="background"] h2 {
    color: #2d88ff !important;
  }
  body.light-theme #story-content-area > div[style*="background"] div {
    color: #23272b !important;
  }

  /* ————————— Dark‑mode restore for Profile tabs & Story cards ——————— */
  /* Profile‑tabs back to dark */
  body:not(.light-theme) .profile-tabs,
  body:not(.light-theme) .profile-tabs > * {
    background: #18191a !important;
    color:      #e4e6eb !important;
  }
  body:not(.light-theme) .profile-tabs button {
    background:   #18191a !important;
    color:        #e4e6eb !important;
    border:       none      !important;
  }
  body:not(.light-theme) .profile-tabs button.active,
  body:not(.light-theme) .profile-tabs button:hover {
    background: #2d88ff !important;
    color:      #fff      !important;
  }

  /* Story‑cards back to dark inline style */
  body:not(.light-theme) #story-content-area > div[style*="background"] {
    background: rgb(35, 39, 43) !important;
    box-shadow: rgba(45, 136, 255, 0.133) 0px 2px 8px !important;
  }
  body:not(.light-theme) #story-content-area > div[style*="background"] h2 {
    color: rgb(45, 136, 255) !important;
  }
  body:not(.light-theme) #story-content-area > div[style*="background"] div {
    color: rgb(228, 230, 235) !important;
  }

body.light-theme .profile-tabs,
body.light-theme .profile-tabs > * {
  background:   #fff !important;
  color:        #23272b !important;
  border-color: #e4e6eb !important;
}
body.light-theme .profile-tabs button {
  background:   #fff     !important;
  color:        #23272b !important;
  border:       1px solid #e4e6eb !important;
}
body.light-theme .profile-tabs button.active,
body.light-theme .profile-tabs button:hover {
  background: #2d88ff !important;
  color:      #fff      !important;
}

/* PROFILE MEDIA GRID */
body.light-theme #profile-media {
  background:   #fff !important;
  border-color: #e4e6eb !important;
}
body.light-theme #profile-media img,
body.light-theme #profile-media video {
  background:   #fff !important;
  color:        #23272b !important;
  box-shadow:   0 1px 6px rgba(0,0,0,0.10) !important;
}


  `;
  if (!document.head.contains(lightStyle)) {
    document.head.appendChild(lightStyle);
  }
  </script>
    </body>
    </html>
    """, username=username)

@app.route('/profile/<username>/<tab>')
def tab_page(username, tab):
    from flask import abort
    abort(404)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                              'favicon.ico', mimetype='image/vnd.microsoft.icon')

# --- Comments API ---
@app.route('/api/comments', methods=['GET'])
def api_get_comments():
    media_key = request.args.get('media_key')
    db = get_db()
    rows = db.execute('SELECT * FROM comments WHERE media_key = ? ORDER BY created ASC', (media_key,)).fetchall()
    def build_tree(parent_id=None):
        tree = []
        for row in rows:
            if row['parent_id'] == parent_id:
                item = dict(row)
                item['replies'] = build_tree(row['id'])
                tree.append(item)
        return tree
    return jsonify(build_tree())

@app.route('/api/comments', methods=['POST'])
def api_post_comment():
    data = request.json
    media_key = data.get('media_key')
    text = data.get('text')
    parent_id = data.get('parent_id')
    user = session.get('username', 'guest')
    if not media_key or media_key == 'undefined':
        return jsonify({'error': 'media_key is required'}), 400
    db = get_db()
    db.execute('INSERT INTO comments (media_key, user, text, parent_id) VALUES (?, ?, ?, ?)', (media_key, user, text, parent_id))
    db.commit()
    return jsonify({'success': True})

# --- Likes API ---
@app.route('/api/likes', methods=['GET'])
def api_get_likes():
    media_key = request.args.get('media_key')
    user = session.get('username', 'guest')
    db = get_db()
    row = db.execute('SELECT SUM(value) as likes, SUM(CASE WHEN value=-1 THEN 1 ELSE 0 END) as dislikes FROM likes WHERE media_key = ?', (media_key,)).fetchone()
    user_row = db.execute('SELECT value FROM likes WHERE media_key = ? AND user = ?', (media_key, user)).fetchone()
    return jsonify({'likes': row['likes'] or 0, 'dislikes': row['dislikes'] or 0, 'user_value': user_row['value'] if user_row else 0})

@app.route('/api/likes', methods=['POST'])
def api_post_like():
    data = request.json
    media_key = data.get('media_key')
    value = int(data.get('value')) # 1 for like, -1 for dislike
    user = session.get('username', 'guest')
    db = get_db()
    db.execute('INSERT OR REPLACE INTO likes (media_key, user, value) VALUES (?, ?, ?)', (media_key, user, value))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/avatar/update', methods=['POST'])
def api_update_avatar():
    if not session.get('logged_in'):
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    avatar_seed = data.get('avatar_seed')
    user_id = session.get('user_id')
    
    if not avatar_seed or not user_id:
        return jsonify({'error': 'Missing avatar seed or user ID'}), 400
    
    try:
        update_user_avatar_seed(user_id, avatar_seed)
        return jsonify({'success': True, 'message': 'Avatar updated successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/avatar/get')
def api_get_avatar():
    if not session.get('logged_in'):
        return jsonify({'error': 'Not logged in'}), 401
    
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Missing user ID'}), 400
    
    try:
        avatar_seed = get_user_avatar_seed(user_id)
        return jsonify({'avatar_seed': avatar_seed})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_story_json(tab_dir):
    story_path = os.path.join(tab_dir, 'story.json')
    if not os.path.exists(story_path):
        return None
    try:
        with open(story_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading story.json: {e}")
        return None

def save_story_json(tab_dir, data):
    story_path = os.path.join(tab_dir, 'story.json')
    try:
        with open(story_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error writing story.json: {e}")
        # Optionally, write a backup or skip writing
        return False
    return True

@app.route('/api/profile/<username>/<tab>/story', methods=['GET'])
def api_get_story(username, tab):
    tab_dir = os.path.join(USERS_DIR, username, tab)
    story = get_story_json(tab_dir)
    if story is None:
        return jsonify({'error': 'Story not found'}), 404
    return jsonify(story)

@app.route('/api/profile/<username>/<tab>/story', methods=['POST'])
def api_save_story(username, tab):
    tab_dir = os.path.join(USERS_DIR, username, tab)
    story = get_story_json(tab_dir)
    if story is None:
        return jsonify({'error': 'Story not found'}), 404
    
    data = request.json
    # Update story with new data (including node positions)
    story.update(data)
    save_story_json(tab_dir, story)
    return jsonify({'success': True, 'story': story})

@app.route('/api/profile/<username>/<tab>/story/node', methods=['POST', 'DELETE'])
def api_story_node(username, tab):
    tab_dir = os.path.join(USERS_DIR, username, tab)
    story = get_story_json(tab_dir)
    if story is None:
        return jsonify({'error': 'Story not found'}), 404
    if request.method == 'POST':
        data = request.json
        node_id = data.get('id')
        content = data.get('content', '')
        if not node_id:
            return jsonify({'error': 'Node id required'}), 400
        # Check if node exists
        found = False
        for node in story['nodes']:
            if node['id'] == node_id:
                node['content'] = content
                found = True
                break
        if not found:
            story['nodes'].append({'id': node_id, 'content': content})
        save_story_json(tab_dir, story)
        return jsonify({'success': True, 'nodes': story['nodes']})
    elif request.method == 'DELETE':
        data = request.json
        node_id = data.get('id')
        if not node_id:
            return jsonify({'error': 'Node id required'}), 400
        # Remove node
        story['nodes'] = [n for n in story['nodes'] if n['id'] != node_id]
        # Remove connections involving this node
        story['connections'] = [c for c in story['connections'] if c['from'] != node_id and c['to'] != node_id]
        save_story_json(tab_dir, story)
        return jsonify({'success': True, 'nodes': story['nodes'], 'connections': story['connections']})

@app.route('/api/profile/<username>/<tab>/story/connection', methods=['POST'])
def api_story_connection(username, tab):
    tab_dir = os.path.join(USERS_DIR, username, tab)
    story = get_story_json(tab_dir)
    if story is None:
        return jsonify({'error': 'Story not found'}), 404
    data = request.json
    from_id = data.get('from')
    to_id = data.get('to')
    action = data.get('action', 'add')  # 'add' or 'remove'
    if not from_id or not to_id:
        return jsonify({'error': 'from and to required'}), 400
    if action == 'add':
        if not any(c['from'] == from_id and c['to'] == to_id for c in story['connections']):
            story['connections'].append({'from': from_id, 'to': to_id})
    elif action == 'remove':
        story['connections'] = [c for c in story['connections'] if not (c['from'] == from_id and c['to'] == to_id)]
    save_story_json(tab_dir, story)
    return jsonify({'success': True, 'connections': story['connections']})

if __name__ == '__main__':
    app.run(debug=True)