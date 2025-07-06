import asyncio
import aiohttp
import json
import logging
import time
import os
import sys
from collections import defaultdict, deque
from typing import Dict, Set, Optional, List
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode, ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram import F
import random

# ─── Imports for Dummy HTTP Server ──────────────────────────────────────────
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ─── Colored Logging System ──────────────────────────────────────────────────
class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors and emojis for better readability"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Check if we should use colors
        self.use_colors = (
            hasattr(sys.stderr, "isatty") and sys.stderr.isatty() or
            os.environ.get('FORCE_COLOR') == '1' or
            os.environ.get('TERM', '').lower() in ('xterm', 'xterm-color', 'xterm-256color', 'screen', 'screen-256color')
        )

    COLORS = {
        'DEBUG': '\x1b[36m',    # Cyan
        'INFO': '\x1b[32m',     # Green  
        'WARNING': '\x1b[33m',  # Yellow
        'ERROR': '\x1b[31m',    # Red
        'CRITICAL': '\x1b[35m', # Magenta
        'RESET': '\x1b[0m',     # Reset
        'BLUE': '\x1b[34m',     # Blue
        'PURPLE': '\x1b[35m',   # Purple
        'CYAN': '\x1b[36m',     # Cyan
        'YELLOW': '\x1b[33m',   # Yellow
        'GREEN': '\x1b[32m',    # Green
        'RED': '\x1b[31m',      # Red (alias for ERROR)
        'BOLD': '\x1b[1m',      # Bold
        'DIM': '\x1b[2m'        # Dim
    }

    def format(self, record):
        if not self.use_colors:
            return super().format(record)

        # Create a copy to avoid modifying the original
        formatted_record = logging.makeLogRecord(record.__dict__)

        # Get the basic formatted message
        message = super().format(formatted_record)

        # Apply colors to the entire message
        return self.colorize_full_message(message, record.levelname)

    def colorize_full_message(self, message, level):
        """Apply colors to the entire formatted message"""
        if not self.use_colors:
            return message

        # Color based on log level
        level_color = self.COLORS.get(level, self.COLORS['RESET'])

        # Apply level-based coloring to the entire message
        if level == 'ERROR' or level == 'CRITICAL':
            return f"{self.COLORS['ERROR']}{self.COLORS['BOLD']}{message}{self.COLORS['RESET']}"
        elif level == 'WARNING':
            return f"{self.COLORS['YELLOW']}{message}{self.COLORS['RESET']}"
        elif level == 'INFO':
            # For INFO messages, use subtle coloring
            if any(word in message for word in ['Bot', 'Game', 'User', 'Success', 'Started', 'Connected']):
                return f"{self.COLORS['GREEN']}{message}{self.COLORS['RESET']}"
            elif any(word in message for word in ['API', 'HTTP', 'Request', 'Fetching']):
                return f"{self.COLORS['BLUE']}{message}{self.COLORS['RESET']}"
            elif any(word in message for word in ['Player', 'Eliminated', 'Winner', 'extracted']):
                return f"{self.COLORS['CYAN']}{message}{self.COLORS['RESET']}"
            else:
                return f"{self.COLORS['GREEN']}{message}{self.COLORS['RESET']}"
        else:
            return f"{level_color}{message}{self.COLORS['RESET']}"

# Force color support in terminal
os.environ['FORCE_COLOR'] = '1'
os.environ['TERM'] = 'xterm-256color'

# Setup colored logging
logger = logging.getLogger("word_sensei_bot")
logger.setLevel(logging.INFO)

# Remove any existing handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Create and configure console handler with colors
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(ColoredFormatter("%(asctime)s | %(levelname)s | %(message)s"))

# Add handler to logger
logger.addHandler(console_handler)

# Prevent propagation to root logger to avoid duplicate messages
logger.propagate = False

# ─── Dummy HTTP Server to Keep Render Happy ─────────────────────────────────
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"AFK bot is alive!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))  # Render injects this
    try:
        server = HTTPServer(("0.0.0.0", port), DummyHandler)
        logger.info(f"🌐 Dummy server listening on port {port}")
        server.serve_forever()
    except OSError as e:
        if e.errno == 98:  # Address already in use
            logger.warning(f"⚠️ Port {port} already in use, HTTP server not started")
        else:
            logger.error(f"❌ HTTP server error: {e}")
    except Exception as e:
        logger.error(f"❌ Unexpected HTTP server error: {e}")

# ─── User Data Collection and Broadcasting System ─────────────────────────────
def extract_user_info(msg: Message):
    """Extract user and chat information from message"""
    logger.debug("🔍 Extracting user information from message")
    u = msg.from_user
    c = msg.chat
    info = {
        "user_id": u.id if u else 0,
        "username": u.username if u else "Unknown",
        "full_name": u.full_name if u else "Unknown User",
        "chat_id": c.id if c else 0,
        "chat_type": c.type if c else "unknown",
        "chat_title": (c.title or c.first_name or "") if c else "",
        "chat_username": f"@{c.username}" if c and c.username else "No Username",
        "chat_link": f"https://t.me/{c.username}" if c and c.username else "No Link",
    }
    logger.info(
        f"📑 User info extracted: {info['full_name']} (@{info['username']}) "
        f"[ID: {info['user_id']}] in {info['chat_title']} [{info['chat_id']}] {info['chat_link']}"
    )
    return info

# ─── Owner and Broadcasting Configuration ──────────────────────────
OWNER_ID = 5290407067  # Hardcoded owner ID
broadcast_mode = set()  # Users in broadcast mode
broadcast_target = {}  # User broadcast targets
user_ids = set()  # Track user IDs for broadcasting
group_ids = set()  # Track group IDs for broadcasting

# Bot configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

# Bot instance
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Rate limiting configuration
RATE_LIMIT_REQUESTS = 30  # requests per minute
RATE_LIMIT_WINDOW = 60  # seconds
MAX_CONCURRENT_GAMES = 10000  # Maximum concurrent games

# Global state management
class UserSession:
    def __init__(self):
        self.current_word = None
        self.guesses = []
        self.game_active = False
        self.word_length = 5
        self.language = "en"
        self.attempts = 0
        self.max_attempts = 6.0  # Changed to float to support infinity
        self.last_activity = time.time()
        self.timer_difficulty = "noob"  # hard, medium, easy, noob
        self.game_start_time = None

class BasicGameSession:
    def __init__(self, chat_id: int, creator_id: int):
        self.chat_id = chat_id
        self.creator_id = creator_id
        self.players = {}  # user_id -> {'name': str, 'full_name': str, 'user_id': int, 'eliminated': bool}
        self.turn_order = []  # List of user_ids
        self.current_turn_index = 0
        self.current_required_letter = None
        self.words_used = []  # List of accepted words
        self.game_state = "waiting"  # waiting, joining, active, finished
        self.join_time_left = 60
        self.turn_time_left = 40
        self.current_turn_timer = 40  # Individual turn countdown
        self.start_time = time.time()
        self.last_word = None
        self.total_words = 0
        self.min_word_length = 3
        self.max_players = 50
        self.min_players = 2
        self.timer_expired = False
        self.longest_word = ""
        self.longest_word_player = ""
        self.game_start_time = time.time()

class RateLimiter:
    def __init__(self):
        self.requests = defaultdict(deque)
    
    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        user_requests = self.requests[user_id]
        
        # Remove old requests
        while user_requests and now - user_requests[0] > RATE_LIMIT_WINDOW:
            user_requests.popleft()
        
        # Check if under limit
        if len(user_requests) >= RATE_LIMIT_REQUESTS:
            return False
        
        user_requests.append(now)
        return True

# Global instances
rate_limiter = RateLimiter()
user_sessions: Dict[int, UserSession] = {}
active_games: Set[int] = set()
basic_games: Dict[int, BasicGameSession] = {}  # chat_id -> BasicGameSession
group_games: Dict[int, UserSession] = {}  # chat_id -> group game session

# API Configuration
RANDOM_WORD_API = "https://random-word-api.herokuapp.com/word"
API_TIMEOUT = 10

# Bot information
BOT_NAME = "Word Sensei"
BOT_USERNAME = ""  # Will be set dynamically
CHANNEL_URL = "https://t.me/WorkGlows"
GROUP_URL = "https://t.me/TheCryptoElders"

async def get_random_words(length: int = 5, count: int = 10) -> List[str]:
    """Get random words from the API with fallback"""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as session:
            url = f"{RANDOM_WORD_API}?number={count}&length={length}"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data if isinstance(data, list) else []
                else:
                    logger.warning(f"API returned status {response.status}")
    except Exception as e:
        logger.error(f"API request failed: {e}")
    
    # Fallback words by length
    fallback_words = {
        3: ["CAT", "DOG", "SUN", "CAR", "BOY", "RUN", "EAT", "SEE", "BIG", "RED"],
        4: ["PLAY", "WORD", "GAME", "LOVE", "HELP", "WORK", "MAKE", "GOOD", "TIME", "TAKE"],
        5: ["HOUSE", "WORLD", "GREAT", "PLACE", "THINK", "WATER", "LIGHT", "SMALL", "FOUND", "STILL"],
        6: ["PERSON", "MOTHER", "FATHER", "SCHOOL", "PEOPLE", "FRIEND", "FAMILY", "STRONG", "BRIGHT", "SIMPLE"],
        7: ["EXAMPLE", "ANOTHER", "PICTURE", "SPECIAL", "PERFECT", "SUPPORT", "PROGRAM", "BECAUSE", "THROUGH", "BETWEEN"]
    }
    
    return fallback_words.get(length, fallback_words[5])[:count]

def get_starting_word() -> str:
    """Get a random starting word for basic game"""
    starting_words = [
        "FLARINGLY", "WONDERFUL", "BUTTERFLY", "ELEPHANT", "CHAMPION", 
        "LIBRARY", "DESTINY", "HARMONY", "CRYSTAL", "JOURNEY"
    ]
    return random.choice(starting_words)



async def is_valid_word(word: str) -> bool:
    """Check if a word is valid using dictionary API"""
    if not word or len(word) < 3:
        return False
    if not word.isalpha():
        return False
    
    # Use dictionary API to validate the word
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word.lower()}"
            async with session.get(url) as response:
                if response.status == 200:
                    return True
                else:
                    return False
    except Exception as e:
        logger.error(f"Word validation API failed: {e}")
        # Fallback to basic validation if API fails
        return word.isalpha() and len(word) >= 3

def get_current_player(game: BasicGameSession) -> Optional[int]:
    """Get the current player's user_id"""
    if not game.turn_order:
        return None
    return game.turn_order[game.current_turn_index]

def get_next_player(game: BasicGameSession) -> Optional[int]:
    """Get the next player's user_id"""
    if len(game.turn_order) <= 1:
        return None
    next_index = (game.current_turn_index + 1) % len(game.turn_order)
    return game.turn_order[next_index]

def format_basic_game_state(game: BasicGameSession) -> str:
    """Format the current basic game state for display"""
    if game.game_state == "waiting":
        return f"🔗 <b>Basic Word Chain Game</b>\n\nA classic game is starting.\n{game.min_players}-{game.max_players} players are needed.\n{game.join_time_left}s to join.\n\nPlayers joined: {len(game.players)}"
    
    elif game.game_state == "joining":
        player_mentions = [f"• <a href='tg://user?id={player['user_id']}'>{player['full_name']}</a>" for player in game.players.values() if not player['eliminated']]
        players_text = "\n".join(player_mentions)
        return f"🔗 <b>Basic Word Chain Game</b>\n\n{players_text}\n\nThere {'is' if len(game.players) == 1 else 'are'} now {len(game.players)} player{'s' if len(game.players) != 1 else ''}.\n\n{game.join_time_left}s left to join."
    
    elif game.game_state == "active":
        current_player_id = get_current_player(game)
        next_player_id = get_next_player(game)
        
        if not current_player_id:
            return "❌ Game error: No current player"
        
        current_mention = f"<a href='tg://user?id={current_player_id}'>{game.players[current_player_id]['full_name']}</a>"
        next_mention = f"<a href='tg://user?id={next_player_id}'>{game.players[next_player_id]['full_name']}</a>" if next_player_id else "None"
        
        active_players = len([p for p in game.players.values() if not p['eliminated']])
        
        turn_info = f"Turn: {current_mention}"
        if next_player_id:
            turn_info += f" (Next: {next_mention})"
        
        word_requirement = ""
        if game.current_required_letter:
            word_requirement = f"Your word must start with <b>{game.current_required_letter}</b> and include at least {game.min_word_length} letters."
        
        return f"🔗 <b>Basic Word Chain Game</b>\n\n{turn_info}\n{word_requirement}\nYou have {game.current_turn_timer}s to answer.\nPlayers remaining: {active_players}/{len(game.players)}\nTotal words: {game.total_words}"
    
    return "🔗 Basic Word Chain Game"

def get_user_session(user_id: int) -> UserSession:
    """Get or create user session"""
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession()
    user_sessions[user_id].last_activity = time.time()
    return user_sessions[user_id]

def cleanup_inactive_sessions():
    """Clean up inactive sessions to manage memory"""
    current_time = time.time()
    inactive_users = [
        user_id for user_id, session in user_sessions.items()
        if current_time - session.last_activity > 3600  # 1 hour
    ]
    
    for user_id in inactive_users:
        if user_id in user_sessions:
            del user_sessions[user_id]
        if user_id in active_games:
            active_games.discard(user_id)
    
    # Clean up inactive group games
    inactive_groups = [
        chat_id for chat_id, session in group_games.items()
        if not session.game_active or (current_time - session.last_activity > 3600)
    ]
    
    for chat_id in inactive_groups:
        if chat_id in group_games:
            del group_games[chat_id]

def get_timer_seconds(difficulty: str) -> int:
    """Get timer duration in seconds based on difficulty"""
    timer_map = {
        "hard": 30,
        "medium": 60,
        "easy": 300,
        "noob": 0  # No limit
    }
    return timer_map.get(difficulty, 0)

def is_timer_expired(session: UserSession) -> bool:
    """Check if the timer has expired for current game"""
    if session.timer_difficulty == "noob" or not session.game_start_time:
        return False
    
    timer_seconds = get_timer_seconds(session.timer_difficulty)
    if timer_seconds == 0:
        return False
    
    elapsed_time = time.time() - session.game_start_time
    return elapsed_time >= timer_seconds

def get_remaining_time(session: UserSession) -> int:
    """Get remaining time in seconds for current game"""
    if session.timer_difficulty == "noob" or not session.game_start_time:
        return 0
    
    timer_seconds = get_timer_seconds(session.timer_difficulty)
    if timer_seconds == 0:
        return 0
    
    elapsed_time = time.time() - session.game_start_time
    remaining = timer_seconds - elapsed_time
    return max(0, int(remaining))

def format_time(seconds: int) -> str:
    """Format seconds into readable time format"""
    if seconds == 0:
        return "No limit"
    elif seconds < 60:
        return f"{seconds}s"
    else:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if remaining_seconds == 0:
            return f"{minutes}m"
        else:
            return f"{minutes}m {remaining_seconds}s"

async def check_permissions(callback_query: CallbackQuery) -> bool:
    """Check if user has permission to use the button"""
    user_id = callback_query.from_user.id
    message = callback_query.message
    
    # For basic game callbacks, allow any group member to join
    if callback_query.data.startswith("basic_"):
        if message.chat.type in ['group', 'supergroup']:
            return True  # Allow any group member for basic games
        return False
    
    # Check if user is the original sender or admin
    if message.reply_to_message:
        original_sender = message.reply_to_message.from_user.id
        if user_id == original_sender:
            return True
    
    # Check if user is admin in group
    if message.chat.type in ['group', 'supergroup']:
        try:
            member = await bot.get_chat_member(message.chat.id, user_id)
            if member.status in ['administrator', 'creator']:
                return True
        except:
            pass
    
    # Check if user is the one who triggered the command (for private chats)
    if message.chat.type == 'private' and user_id == message.chat.id:
        return True
    
    return False

def create_start_keyboard() -> InlineKeyboardMarkup:
    """Create start command keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📢 Updates", url=CHANNEL_URL),
        InlineKeyboardButton(text="💬 Support", url=GROUP_URL)
    )
    if BOT_USERNAME:
        builder.row(
            InlineKeyboardButton(text="➕ Add Me To Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")
        )
    return builder.as_markup()

def create_play_keyboard() -> InlineKeyboardMarkup:
    """Create simplified play command keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎮 Quick Play", callback_data="play_quick")
    )
    builder.row(
        InlineKeyboardButton(text="🔗 Basic Game", callback_data="play_basic")
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Configure", callback_data="play_configure")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Cancel", callback_data="play_cancel")
    )
    return builder.as_markup()

def create_configure_keyboard() -> InlineKeyboardMarkup:
    """Create configuration options keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📏 Word Length", callback_data="config_length"),
        InlineKeyboardButton(text="⚙️ Max Attempts", callback_data="config_attempts")
    )
    builder.row(
        InlineKeyboardButton(text="⏰ Timer", callback_data="config_timer")
    )
    builder.row(
        InlineKeyboardButton(text="🎮 Start Game", callback_data="config_start"),
        InlineKeyboardButton(text="🔙 Back", callback_data="play_back")
    )
    return builder.as_markup()

def create_custom_length_keyboard() -> InlineKeyboardMarkup:
    """Create custom length selection keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="3️⃣", callback_data="length_3"),
        InlineKeyboardButton(text="4️⃣", callback_data="length_4"),
        InlineKeyboardButton(text="5️⃣", callback_data="length_5")
    )
    builder.row(
        InlineKeyboardButton(text="6️⃣", callback_data="length_6"),
        InlineKeyboardButton(text="7️⃣", callback_data="length_7")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Back", callback_data="config_back")
    )
    return builder.as_markup()

def create_attempts_keyboard() -> InlineKeyboardMarkup:
    """Create max attempts selection keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="3️⃣", callback_data="attempts_3"),
        InlineKeyboardButton(text="4️⃣", callback_data="attempts_4"),
        InlineKeyboardButton(text="5️⃣", callback_data="attempts_5")
    )
    builder.row(
        InlineKeyboardButton(text="6️⃣", callback_data="attempts_6"),
        InlineKeyboardButton(text="7️⃣", callback_data="attempts_7"),
        InlineKeyboardButton(text="8️⃣", callback_data="attempts_8")
    )
    builder.row(
        InlineKeyboardButton(text="♾️ Infinity", callback_data="attempts_infinity")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Back", callback_data="config_back")
    )
    return builder.as_markup()

def create_timer_keyboard() -> InlineKeyboardMarkup:
    """Create timer difficulty selection keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔥 Hard (30s)", callback_data="timer_hard"),
        InlineKeyboardButton(text="⚡ Medium (1m)", callback_data="timer_medium")
    )
    builder.row(
        InlineKeyboardButton(text="🟢 Easy (5m)", callback_data="timer_easy"),
        InlineKeyboardButton(text="🆓 Noob (No limit)", callback_data="timer_noob")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Back", callback_data="config_back")
    )
    return builder.as_markup()

def create_stop_keyboard() -> InlineKeyboardMarkup:
    """Create stop command keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎮 Play Again", callback_data="stop_play_again"),
        InlineKeyboardButton(text="❌ Close", callback_data="stop_close")
    )
    return builder.as_markup()

def create_game_keyboard(session: UserSession) -> InlineKeyboardMarkup:
    """Create game keyboard with current game state"""
    builder = InlineKeyboardBuilder()
    
    # Add guess input button
    builder.row(
        InlineKeyboardButton(text="✏️ Make a Guess", callback_data="game_guess")
    )
    
    # Add game control buttons
    builder.row(
        InlineKeyboardButton(text="🔄 New Word", callback_data="game_new_word"),
        InlineKeyboardButton(text="🛑 Stop Game", callback_data="game_stop")
    )
    
    return builder.as_markup()

def create_basic_game_keyboard(player_count: int = 0) -> InlineKeyboardMarkup:
    """Create basic game join keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎮 Join Game", callback_data="basic_join")
    )
    
    # Add Force Play button if there are 2+ players
    if player_count >= 2:
        builder.row(
            InlineKeyboardButton(text="🚀 Force Play", callback_data="basic_force_start")
        )
    
    builder.row(
        InlineKeyboardButton(text="❌ Cancel", callback_data="basic_cancel")
    )
    return builder.as_markup()

def format_game_state(session: UserSession) -> str:
    """Format the current game state for display"""
    if not session.game_active or not session.current_word:
        return "🎮 No active game"
    
    word_display = " ".join("_" for _ in session.current_word)
    
    # Handle attempts display for infinity mode
    if session.max_attempts == float('inf'):
        attempts_display = f"🔢 Attempts: {session.attempts}/∞"
    else:
        attempts_left = int(session.max_attempts - session.attempts)
        attempts_display = f"🔢 Attempts left: {attempts_left}/{int(session.max_attempts)}"
    
    # Add timer information
    timer_display = ""
    if session.timer_difficulty != "noob" and session.game_start_time:
        remaining_time = get_remaining_time(session)
        if remaining_time > 0:
            timer_display = f"\n⏰ Time left: {format_time(remaining_time)}"
        else:
            timer_display = "\n⏰ Time's up!"
    
    # Show all previous guesses with Wordle-style feedback
    guesses_display = ""
    if session.guesses:
        guesses_display = "\n\n📋 <b>Previous Guesses:</b>\n"
        for guess in session.guesses:
            feedback = get_wordle_feedback(guess, session.current_word)
            guesses_display += f"{feedback} {guess}\n"
    
    # Add a unique identifier to make each message different
    game_id = str(int(session.game_start_time)) if session.game_start_time else "new"
    
    return f"""🎯 <b>Word Guessing Game</b> #{game_id}

📝 Word: <code>{word_display}</code>
🎲 Length: {len(session.current_word)} letters
{attempts_display}{timer_display}{guesses_display}
💡 Send your guess as a message!"""

@dp.message(CommandStart())
async def start_command(message: types.Message):
    """Handle /start command with user data collection"""
    try:
        # Extract user information and log it
        info = extract_user_info(message)
        
        # Rate limiting check
        if not rate_limiter.is_allowed(message.from_user.id):
            logger.warning(f"🚫 Rate limit exceeded for user {info['user_id']} ({info['full_name']})")
            await message.answer("🚫 Too many requests. Please wait a moment.")
            return
        
        # Collect user data for broadcasting
        if message.chat.type == "private":
            user_ids.add(message.from_user.id)
            logger.info(f"👤 Added user to broadcast list: {info['full_name']} [ID: {info['user_id']}] (Total users: {len(user_ids)})")
        else:
            group_ids.add(message.chat.id)
            logger.info(f"👥 Added group to broadcast list: {info['chat_title']} [ID: {info['chat_id']}] (Total groups: {len(group_ids)})")
        
        user_name = message.from_user.full_name
        user_mention = f"<a href='tg://user?id={message.from_user.id}'>{user_name}</a>"
        
        welcome_text = f"""👋 Welcome {user_mention}!

🎮 I'm <b>{BOT_NAME}</b>, your friendly word guessing game bot!

🎯 <b>What I do:</b>
• Play exciting word guessing games
• Challenge yourself with different word lengths
• Perfect for groups and private chats

🚀 <b>Get Started:</b>
Use /play to start a game or /help for more info!"""
        
        await message.answer(
            welcome_text,
            reply_markup=create_start_keyboard(),
            parse_mode=ParseMode.HTML
        )
        logger.info(f"✅ Start command completed for {info['full_name']}")
        
    except Exception as e:
        logger.error(f"❌ Error in start command: {e}")
        await message.answer("⚠️ An error occurred. Please try again later.")

@dp.message(Command("play"))
async def play_command(message: types.Message):
    """Handle /play command"""
    if not rate_limiter.is_allowed(message.from_user.id):
        await message.answer("🚫 Too many requests. Please wait a moment.")
        return
    
    if len(active_games) >= MAX_CONCURRENT_GAMES:
        await message.answer("🚫 Too many active games. Please try again later.")
        return
    
    session = get_user_session(message.from_user.id)
    
    if session.game_active:
        await message.answer(
            "🎮 You already have an active game!\n\n" + format_game_state(session),
            reply_markup=create_game_keyboard(session),
            parse_mode=ParseMode.HTML
        )
        return
    
    play_text = f"""🎮 <b>Let's Play Word Guessing!</b>

🎯 Choose your game mode:
• <b>Quick Play</b>: 5-letter words (standard)
• <b>Custom Length</b>: Choose word length (3-7 letters)

🎲 <b>How to play:</b>
1. I'll give you a word with hidden letters
2. Guess the word by typing your answer
3. You have 6 attempts to guess correctly!

Ready to challenge your vocabulary? 🧠"""
    
    await message.answer(
        play_text,
        reply_markup=create_play_keyboard(),
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("stop"))
async def stop_command(message: types.Message):
    """Handle /stop command"""
    if not rate_limiter.is_allowed(message.from_user.id):
        await message.answer("🚫 Too many requests. Please wait a moment.")
        return
    
    session = get_user_session(message.from_user.id)
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if there's any active game to stop
    has_individual_game = session.game_active
    has_group_game = chat_id != user_id and chat_id in group_games
    has_basic_game = chat_id != user_id and chat_id in basic_games
    
    if not has_individual_game and not has_group_game and not has_basic_game:
        await message.answer("🚫 No active game to stop.")
        return
    
    # Stop the game
    session.game_active = False
    
    # Clean up individual game
    if user_id in active_games:
        active_games.discard(user_id)
    
    # Clean up group game if this is a group chat
    if chat_id != user_id and chat_id in group_games:
        del group_games[chat_id]
    
    # Clean up basic game if this is a group chat
    if chat_id != user_id and chat_id in basic_games:
        game = basic_games[chat_id]
        game.game_state = "cancelled"
        del basic_games[chat_id]
    
    stop_text = f"""🛑 <b>Game Stopped!</b>

📊 <b>Game Summary:</b>
• Word was: <b>{session.current_word}</b>
• Attempts made: {session.attempts}/{session.max_attempts}
• Guesses: {', '.join(session.guesses) if session.guesses else 'None'}

Thanks for playing! 🎮"""
    
    await message.answer(
        stop_text,
        reply_markup=create_stop_keyboard(),
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("debug"))
async def debug_command(message: types.Message):
    """Handle /debug command - show current game states"""
    if not rate_limiter.is_allowed(message.from_user.id):
        await message.answer("🚫 Too many requests. Please wait a moment.")
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    session = get_user_session(user_id)
    
    debug_info = f"🔍 <b>Debug Info:</b>\n\n"
    debug_info += f"Chat ID: {chat_id}\n"
    debug_info += f"User ID: {user_id}\n\n"
    debug_info += f"<b>Individual Game:</b>\n"
    debug_info += f"session.game_active: {session.game_active}\n"
    debug_info += f"user in active_games: {user_id in active_games}\n"
    debug_info += f"session.current_word: '{session.current_word}'\n\n"
    debug_info += f"<b>Group Games:</b>\n"
    debug_info += f"chat_id in group_games: {chat_id in group_games}\n"
    if chat_id in group_games:
        group_session = group_games[chat_id]
        debug_info += f"group_session.game_active: {group_session.game_active}\n"
        debug_info += f"group_session.current_word: '{group_session.current_word}'\n"
    debug_info += f"\n<b>Basic Games:</b>\n"
    debug_info += f"chat_id in basic_games: {chat_id in basic_games}\n"
    if chat_id in basic_games:
        basic_game = basic_games[chat_id]
        debug_info += f"basic_game.game_state: {basic_game.game_state}\n"
    
    await message.answer(debug_info, parse_mode=ParseMode.HTML)

@dp.message(Command("help"))
async def help_command(message: types.Message):
    """Handle /help command"""
    if not rate_limiter.is_allowed(message.from_user.id):
        await message.answer("🚫 Too many requests. Please wait a moment.")
        return
    
    user_name = message.from_user.full_name
    user_mention = f"<a href='tg://user?id={message.from_user.id}'>{user_name}</a>"
    
    help_text = f"""🆘 <b>Help - {user_mention}</b>

🎮 <b>Available Commands:</b>
• /start - Welcome message and bot info
• /play - Start a new word guessing game
• /stop - Stop current game
• /help - Show this help message

🎯 <b>How to Play:</b>
1. Use /play to start a game
2. Choose word length (3-7 letters)
3. I'll show you a word with hidden letters
4. Type your guess as a regular message
5. You have 6 attempts to guess correctly!

📱 <b>Features:</b>
• Works in groups and private chats
• Multiple difficulty levels
• Real-time game tracking
• Professional inline keyboards

💡 <b>Tips:</b>
• Start with common letters (A, E, I, O, U)
• Think of common word patterns
• Use context clues from previous guesses

Need more help? Join our support group! 💬"""
    
    await message.answer(
        help_text,
        reply_markup=create_start_keyboard(),
        parse_mode=ParseMode.HTML
    )

# ─── Ping Command Handler ───────────────────────────────────────────
@dp.message(Command("ping"))
async def ping_command(message: types.Message):
    """Handle /ping command with hidden support link"""
    if not rate_limiter.is_allowed(message.from_user.id):
        await message.answer("🚫 Too many requests. Please wait a moment.")
        return
    
    # Calculate response time (simulated)
    import random
    response_time = round(random.uniform(200, 800), 2)
    
    ping_text = f'🏓 <a href="{GROUP_URL}">Pong!</a> {response_time}ms'
    
    await message.answer(
        ping_text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

# ─── Broadcast Command Handler ───────────────────────────────────────
@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message):
    """Handle broadcast command (owner only)"""
    try:
        info = extract_user_info(msg)
        logger.info(f"📢 Broadcast command attempted by {info['full_name']}")

        if not msg.from_user or msg.from_user.id != OWNER_ID:
            logger.warning(f"🚫 Unauthorized broadcast attempt by user {msg.from_user.id if msg.from_user else 'Unknown'}")
            await bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
            response = await msg.answer("⛔ This command is restricted.")
            logger.info(f"⚠️ Unauthorized access message sent, ID: {response.message_id}")
            return

        await bot.send_chat_action(msg.chat.id, ChatAction.TYPING)

        # Create inline keyboard for broadcast target selection
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"👥 Users ({len(user_ids)})", callback_data="broadcast_users"),
                InlineKeyboardButton(text=f"📢 Groups ({len(group_ids)})", callback_data="broadcast_groups")
            ]
        ])

        response = await msg.answer(
            "📣 <b>Choose broadcast target:</b>\n\n"
            f"👥 <b>Users:</b> {len(user_ids)} individual users\n"
            f"📢 <b>Groups:</b> {len(group_ids)} groups\n\n"
            "Select where you want to send your broadcast message:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        logger.info(f"✅ Broadcast target selection sent, message ID: {response.message_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in broadcast command: {e}")
        await msg.answer("⚠️ An error occurred while setting up broadcast.")

# ─── Broadcast Callback Handlers ─────────────────────────────────────
@dp.callback_query(lambda c: c.data.startswith("broadcast_"))
async def handle_broadcast_callbacks(callback_query: CallbackQuery):
    """Handle broadcast target selection callbacks"""
    try:
        if not callback_query.from_user or callback_query.from_user.id != OWNER_ID:
            await callback_query.answer("⛔ This action is restricted.", show_alert=True)
            return

        if callback_query.data == "broadcast_users":
            broadcast_mode.add(callback_query.from_user.id)
            broadcast_target[callback_query.from_user.id] = "users"
            logger.info(f"📢 Broadcast mode enabled for users by {callback_query.from_user.id}")
            
            await callback_query.message.edit_text(
                f"📤 <b>Broadcast to Users Active</b>\n\n"
                f"👥 <b>Target:</b> {len(user_ids)} individual users\n\n"
                "📝 Send your message now and it will be broadcasted to all users.\n"
                "Use /start to cancel broadcast mode.",
                parse_mode=ParseMode.HTML
            )
            await callback_query.answer("📤 Ready to broadcast to users!")

        elif callback_query.data == "broadcast_groups":
            broadcast_mode.add(callback_query.from_user.id)
            broadcast_target[callback_query.from_user.id] = "groups"
            logger.info(f"📢 Broadcast mode enabled for groups by {callback_query.from_user.id}")
            
            await callback_query.message.edit_text(
                f"📤 <b>Broadcast to Groups Active</b>\n\n"
                f"📢 <b>Target:</b> {len(group_ids)} groups\n\n"
                "📝 Send your message now and it will be broadcasted to all groups.\n"
                "Use /start to cancel broadcast mode.",
                parse_mode=ParseMode.HTML
            )
            await callback_query.answer("📤 Ready to broadcast to groups!")
            
    except Exception as e:
        logger.error(f"❌ Error in broadcast callback: {e}")
        await callback_query.answer("⚠️ An error occurred.", show_alert=True)

# ─── Live Message Handler for Private Messages ──────────────────────
@dp.message(F.chat.type == "private", ~F.text.startswith('/'), ~F.text.regexp(r'^[a-zA-Z]{2,8}$'))
async def handle_private_messages(msg: Message):
    """Handle private messages with broadcast functionality and user data collection"""
    logger.info(f"📨 PRIVATE MSG HANDLER: User {msg.from_user.id} sent '{msg.text}' in chat {msg.chat.id}")
    try:
        # Extract user info for all private messages
        info = extract_user_info(msg)
        
        # Add user to broadcast list if not already added
        user_ids.add(msg.from_user.id)
        logger.debug(f"👤 User tracked for broadcasting: {info['full_name']} [Total: {len(user_ids)}]")
        
        # Check for broadcast mode first (owner bypass)
        if msg.from_user and msg.from_user.id in broadcast_mode:
            logger.info(f"📢 Broadcasting message from owner {msg.from_user.id}")

            target = broadcast_target.get(msg.from_user.id, "users")
            target_list = user_ids if target == "users" else group_ids

            success_count = 0
            failed_count = 0

            for target_id in target_list:
                try:
                    await bot.copy_message(
                        chat_id=target_id,
                        from_chat_id=msg.chat.id,
                        message_id=msg.message_id
                    )
                    success_count += 1
                    logger.info(f"✅ Message sent to {target_id}")
                except Exception as e:
                    failed_count += 1
                    logger.warning(f"❌ Failed to send to {target_id}: {e}")

            # Send broadcast summary
            await msg.answer(
                f"📊 <b>Broadcast Summary:</b>\n\n"
                f"✅ <b>Sent:</b> {success_count}\n"
                f"❌ <b>Failed:</b> {failed_count}\n"
                f"🎯 <b>Target:</b> {target}\n\n"
                "Broadcast mode is still active. Send another message or use /start to disable.",
                parse_mode=ParseMode.HTML
            )

            # Remove from broadcast mode after sending
            broadcast_mode.discard(msg.from_user.id)
            if msg.from_user.id in broadcast_target:
                del broadcast_target[msg.from_user.id]

            logger.info(f"🔓 Broadcast mode disabled for {msg.from_user.id}")
            return
            
        # This handler now only processes non-word messages (numbers, special chars, long text)
        # Word guesses (2-8 letters) are handled by the guess handler
        
        # Continue with normal message processing for non-broadcast messages
        logger.info(f"📤 Private handler finished processing message from user {user_id} - allowing other handlers")
        # This allows the existing message handlers to process the message normally
        
    except Exception as e:
        logger.error(f"❌ Error in private message handler: {e}")

# ─── Group Message Handler for Data Collection ────────────────────────
@dp.message(F.chat.type.in_({"group", "supergroup"}), ~F.text.startswith('/'), ~F.text.regexp(r'^[a-zA-Z]{2,8}$'))
async def handle_group_messages(msg: Message):
    """Handle group messages for data collection"""
    try:
        # Extract info for all group messages
        info = extract_user_info(msg)
        
        # Add group to broadcast list if not already added
        group_ids.add(msg.chat.id)
        logger.debug(f"👥 Group tracked for broadcasting: {info['chat_title']} [Total: {len(group_ids)}]")
        
        # Add user to user list as well (they're in a group but still a user)
        if msg.from_user:
            user_ids.add(msg.from_user.id)
            logger.debug(f"👤 User from group tracked: {info['full_name']} [Total users: {len(user_ids)}]")
        
    except Exception as e:
        logger.error(f"❌ Error in group message handler: {e}")

@dp.callback_query(lambda c: c.data.startswith("play_"))
async def handle_play_callbacks(callback_query: CallbackQuery):
    """Handle play command callbacks"""
    # Allow any user to start their own game
    # No permission check needed for starting games
    
    user_id = callback_query.from_user.id
    session = get_user_session(user_id)
    
    if callback_query.data == "play_quick":
        # Quick play with defaults: 4 letters, no time limit, no attempt limit
        chat_id = callback_query.message.chat.id
        
        # Check if this is a group chat
        if chat_id != user_id:
            # This is a group chat - create group game
            # Check for any existing group game (including inactive ones that weren't cleaned up)
            if chat_id in group_games:
                group_session = group_games[chat_id]
                # If the group game isn't actually active, clean it up
                if not group_session.game_active:
                    del group_games[chat_id]
                else:
                    await callback_query.answer("A word guessing game is already running in this group!", show_alert=True)
                    return
            
            # Create group game session
            group_session = UserSession()
            group_session.word_length = 4
            group_session.max_attempts = float('inf')
            group_session.timer_difficulty = "noob"
            group_games[chat_id] = group_session
            await start_new_game(callback_query, group_session, is_group=True)
        else:
            # Private chat - individual game
            # Check if user already has an active game
            if session.game_active or user_id in active_games:
                await callback_query.answer("You already have an active game! Use /stop to end it first.", show_alert=True)
                return
            
            session.word_length = 4
            session.max_attempts = float('inf')
            session.timer_difficulty = "noob"
            await start_new_game(callback_query, session)
    
    elif callback_query.data == "play_basic":
        # Start basic word chain game (groups only)
        chat_id = callback_query.message.chat.id
        
        # Check if this is a group chat
        if chat_id == user_id:
            await callback_query.answer("Basic word chain game is only available in groups! Use it in a group chat.", show_alert=True)
            return
        
        if chat_id in basic_games:
            await callback_query.answer("A basic game is already running in this chat!", show_alert=True)
            return
        
        # Create new basic game
        game = BasicGameSession(chat_id, user_id)
        
        # Add creator to the game
        creator_name = callback_query.from_user.first_name or "Player"
        creator_full_name = creator_name
        if callback_query.from_user.last_name:
            creator_full_name += f" {callback_query.from_user.last_name}"
        
        game.players[user_id] = {
            'name': creator_name,
            'full_name': creator_full_name,
            'user_id': user_id,
            'eliminated': False
        }
        game.turn_order.append(user_id)
        game.game_state = "joining"
        
        basic_games[chat_id] = game
        
        # Start the timer in background
        asyncio.create_task(start_basic_game_timer(chat_id))
        
        await callback_query.message.edit_text(
            format_basic_game_state(game),
            reply_markup=create_basic_game_keyboard(0),
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("Basic word chain game created!")
        return
    
    elif callback_query.data == "play_configure":
        # Show configuration options
        await callback_query.message.edit_text(
            "⚙️ <b>Configure Game Settings</b>\n\nCustomize your game preferences:",
            reply_markup=create_configure_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif callback_query.data == "play_attempts":
        # Show max attempts options
        await callback_query.message.edit_text(
            "⚙️ <b>Choose Max Attempts:</b>\n\nSelect the maximum number of attempts:",
            reply_markup=create_attempts_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif callback_query.data == "play_timer":
        # Show timer options
        await callback_query.message.edit_text(
            "⏰ <b>Timer Settings</b>\n\nSelect your preferred timer difficulty:\n\n🔥 Hard: 30 seconds\n⚡ Medium: 1 minute\n🟢 Easy: 5 minutes\n🆓 Noob: No time limit",
            reply_markup=create_timer_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif callback_query.data == "play_cancel":
        # Cancel game setup
        await callback_query.message.edit_text(
            "❌ Game cancelled. Use /play to start again!",
            reply_markup=None
        )
        await callback_query.answer("Game setup cancelled")
        return
    
    elif callback_query.data == "play_back":
        # Go back to main play menu
        await callback_query.message.edit_text(
            "🎮 <b>Let's Play Word Guessing!</b>\n\n🎯 Choose your game mode:",
            reply_markup=create_play_keyboard(),
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("Back to main menu")
        return
    
    await callback_query.answer("Setting up game...")

@dp.callback_query(lambda c: c.data.startswith("config_"))
async def handle_config_callbacks(callback_query: CallbackQuery):
    """Handle configuration callbacks"""
    # Allow any user to configure their own game settings
    
    user_id = callback_query.from_user.id
    session = get_user_session(user_id)
    
    if callback_query.data == "config_length":
        # Show word length options
        await callback_query.message.edit_text(
            "📏 <b>Choose Word Length:</b>\n\nSelect the number of letters for your word:",
            reply_markup=create_custom_length_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif callback_query.data == "config_attempts":
        # Show max attempts options
        await callback_query.message.edit_text(
            "⚙️ <b>Choose Max Attempts:</b>\n\nSelect the maximum number of attempts:",
            reply_markup=create_attempts_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif callback_query.data == "config_timer":
        # Show timer options
        await callback_query.message.edit_text(
            "⏰ <b>Timer Settings</b>\n\nSelect your preferred timer difficulty:\n\n🔥 Hard: 30 seconds\n⚡ Medium: 1 minute\n🟢 Easy: 5 minutes\n🆓 Noob: No time limit",
            reply_markup=create_timer_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif callback_query.data == "config_start":
        # Start game with current settings
        await start_new_game(callback_query, session)
        await callback_query.answer("Starting game with current settings!")
        return
    
    await callback_query.answer("Opening configuration...")

@dp.callback_query(lambda c: c.data.startswith("length_"))
async def handle_length_callbacks(callback_query: CallbackQuery):
    """Handle word length selection callbacks"""
    user_id = callback_query.from_user.id
    session = get_user_session(user_id)
    
    # Extract length from callback data
    length = int(callback_query.data.split("_")[1])
    session.word_length = length
    
    # Go back to configuration menu instead of starting game
    await callback_query.message.edit_text(
        f"📏 <b>Word length set to {length} letters!</b>\n\nConfigure other settings or start the game:",
        reply_markup=create_configure_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await callback_query.answer(f"Word length set to {length} letters!")

@dp.callback_query(lambda c: c.data.startswith("attempts_"))
async def handle_attempts_callbacks(callback_query: CallbackQuery):
    """Handle max attempts selection callbacks"""
    user_id = callback_query.from_user.id
    session = get_user_session(user_id)
    
    # Handle attempts setting
    if callback_query.data == "attempts_infinity":
        session.max_attempts = float('inf')
        await callback_query.message.edit_text(
            "⚙️ <b>Max Attempts Set to Infinity!</b>\n\nYou can now make unlimited attempts until you guess correctly.\n\nConfigure other settings or start the game:",
            reply_markup=create_configure_keyboard(),
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("Max attempts set to unlimited!")
        return
    else:
        # Extract attempts from callback data
        attempts = int(callback_query.data.split("_")[1])
        session.max_attempts = attempts
        
        await callback_query.message.edit_text(
            f"⚙️ <b>Max Attempts Set!</b>\n\nYou can now make up to {attempts} attempts per game.\n\nConfigure other settings or start the game:",
            reply_markup=create_configure_keyboard(),
            parse_mode=ParseMode.HTML
        )
    await callback_query.answer(f"Max attempts set to {attempts}!")

@dp.callback_query(lambda c: c.data.startswith("timer_"))
async def handle_timer_callbacks(callback_query: CallbackQuery):
    """Handle timer difficulty selection callbacks"""
    user_id = callback_query.from_user.id
    session = get_user_session(user_id)
    
    # Extract timer difficulty from callback data
    difficulty = callback_query.data.split("_")[1]
    session.timer_difficulty = difficulty
    
    # Format timer display
    timer_seconds = get_timer_seconds(difficulty)
    timer_display = format_time(timer_seconds)
    
    difficulty_names = {
        "hard": "🔥 Hard",
        "medium": "⚡ Medium", 
        "easy": "🟢 Easy",
        "noob": "🆓 Noob"
    }
    
    await callback_query.message.edit_text(
        f"⏰ <b>Timer Set!</b>\n\n{difficulty_names[difficulty]} difficulty selected.\nTime limit: {timer_display}\n\nConfigure other settings or start the game:",
        reply_markup=create_configure_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await callback_query.answer(f"Timer set to {difficulty_names[difficulty]} ({timer_display})!")

# Add config_back and length handlers
@dp.callback_query(lambda c: c.data == "config_back")
async def handle_config_back(callback_query: CallbackQuery):
    """Handle back to configure menu"""
    await callback_query.message.edit_text(
        "⚙️ <b>Configure Game Settings</b>\n\nCustomize your game preferences:",
        reply_markup=create_configure_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await callback_query.answer("Back to configuration menu")



@dp.callback_query(lambda c: c.data.startswith("stop_"))
async def handle_stop_callbacks(callback_query: CallbackQuery):
    """Handle stop command callbacks"""
    user_id = callback_query.from_user.id
    session = get_user_session(user_id)
    
    if callback_query.data == "stop_play_again":
        # Start a new game
        await callback_query.message.edit_text(
            "🎮 <b>Let's Play Again!</b>\n\n🎯 Choose your game mode:",
            reply_markup=create_play_keyboard(),
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("Let's play again!")
        return
    
    elif callback_query.data == "stop_close":
        # Close the message
        await callback_query.message.edit_text(
            "👋 Game closed. Use /play to start a new game!",
            reply_markup=None
        )
        await callback_query.answer("Game closed")
        return
    
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("game_"))
async def handle_game_callbacks(callback_query: CallbackQuery):
    """Handle in-game callbacks"""
    user_id = callback_query.from_user.id
    session = get_user_session(user_id)
    
    if callback_query.data == "game_guess":
        await callback_query.answer("Type your guess as a message!", show_alert=True)
        return
    
    elif callback_query.data == "game_new_word":
        # Generate new word with same settings
        try:
            # Get new word with current settings
            words = await get_random_words(session.word_length, 1)
            if words:
                new_word = words[0].upper()
            else:
                # Fallback word generation
                fallback_words = {
                    3: ["CAT", "DOG", "SUN", "BOY", "RED", "BIG"],
                    4: ["PLAY", "WORD", "GAME", "LOVE", "HELP", "WORK"],
                    5: ["HOUSE", "WORLD", "GREAT", "PLACE", "THINK", "WATER"],
                    6: ["PERSON", "MOTHER", "FATHER", "SCHOOL", "PEOPLE", "FRIEND"],
                    7: ["EXAMPLE", "ANOTHER", "PICTURE", "SPECIAL", "PERFECT", "SUPPORT"]
                }
                import random
                available_words = fallback_words.get(session.word_length, fallback_words[5])
                # Make sure we get a different word if possible
                if session.current_word and session.current_word in available_words and len(available_words) > 1:
                    available_words = [w for w in available_words if w != session.current_word]
                new_word = random.choice(available_words)
            
            # Reset session with new word and new timestamp
            session.current_word = new_word
            session.guesses = []
            session.attempts = 0
            session.game_active = True
            session.game_start_time = time.time()  # New timestamp for unique ID
            
            # Add to active games
            active_games.add(user_id)
            
            # Update message with new game state
            game_text = format_game_state(session)
            await callback_query.message.edit_text(
                game_text,
                reply_markup=create_game_keyboard(session),
                parse_mode=ParseMode.HTML
            )
            await callback_query.answer("✨ New word generated!")
            
        except Exception as e:
            logger.error(f"Error generating new word: {e}")
            await callback_query.answer("❌ Failed to generate new word", show_alert=True)
        return
    
    elif callback_query.data == "game_stop":
        # Stop current game
        session.game_active = False
        if user_id in active_games:
            active_games.discard(user_id)
        
        await callback_query.message.edit_text(
            f"🛑 <b>Game Stopped!</b>\n\nThe word was: <b>{session.current_word}</b>\n\nThanks for playing! 🎮",
            reply_markup=create_stop_keyboard(),
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("Game stopped")
        return
    
    await callback_query.answer("Button pressed")

@dp.callback_query(lambda c: c.data.startswith("basic_"))
async def handle_basic_game_callbacks(callback_query: CallbackQuery):
    """Handle basic game callbacks"""
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    if callback_query.data == "basic_join":
        # Join the basic game
        if chat_id not in basic_games:
            await callback_query.answer("No basic game found in this chat!", show_alert=True)
            return
        
        game = basic_games[chat_id]
        
        # Check if user already joined
        if user_id in game.players:
            await callback_query.answer("You have already joined this game!", show_alert=True)
            return
        
        # Check if game is full
        if len(game.players) >= game.max_players:
            await callback_query.answer("Game is full!", show_alert=True)
            return
        
        # Add player to game
        user_name = callback_query.from_user.first_name or "Player"
        full_name = user_name
        if callback_query.from_user.last_name:
            full_name += f" {callback_query.from_user.last_name}"
        
        game.players[user_id] = {
            'name': user_name,
            'full_name': full_name,
            'user_id': user_id,
            'eliminated': False
        }
        game.turn_order.append(user_id)
        game.game_state = "joining"
        
        # Update the message
        await callback_query.message.edit_text(
            format_basic_game_state(game),
            reply_markup=create_basic_game_keyboard(len(game.players)),
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer(f"You joined the game! Players: {len(game.players)}")
        return
    
    elif callback_query.data == "basic_force_start":
        # Force start the basic game if there are enough players
        if chat_id not in basic_games:
            await callback_query.answer("No basic game found in this chat!", show_alert=True)
            return
        
        game = basic_games[chat_id]
        
        # Check if there are at least 2 players
        if len(game.players) < 2:
            await callback_query.answer("Need at least 2 players to force start!", show_alert=True)
            return
        
        # Force start the game
        game.game_state = "active"
        game.join_time_left = 0  # Stop the countdown timer
        random.shuffle(game.turn_order)  # Randomize turn order
        
        # Set starting word and first random letter
        starting_word = get_starting_word()
        game.last_word = starting_word
        game.current_required_letter = starting_word[-1]
        
        turn_order_text = "\n".join([f"<a href='tg://user?id={uid}'>{game.players[uid]['full_name']}</a>" for uid in game.turn_order])
        await callback_query.message.edit_text(
            f"Game force started!\n\nThe first word is {starting_word}.\n\nTurn order:\n{turn_order_text}\n\n{format_basic_game_state(game)}",
            reply_markup=None,
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("Game force started!")
        return
    
    elif callback_query.data == "basic_cancel":
        # Cancel the basic game
        if chat_id in basic_games:
            game = basic_games[chat_id]
            game.game_state = "cancelled"  # Set state to cancelled to stop countdown
            del basic_games[chat_id]
        
        await callback_query.message.edit_text(
            "🎮 <b>Basic Game Cancelled</b>\n\nUse /play to start a new game!",
            reply_markup=None,
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("Basic game cancelled")
        return
    
    await callback_query.answer("Button pressed")

async def handle_group_guess(message: types.Message, group_session: UserSession):
    """Handle word guesses for group games"""
    user_id = message.from_user.id
    guess = message.text.upper().strip()
    
    # Check timer expiration first
    if is_timer_expired(group_session):
        group_session.game_active = False
        group_session.timer_expired = True
        chat_id = message.chat.id
        if chat_id in group_games:
            del group_games[chat_id]
        
        await message.answer(
            f"⏰ Time's up! The word was <b>{group_session.current_word}</b>. Try again with /play!",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Validate guess
    if not guess.isalpha():
        return
    
    if len(guess) < 3 or len(guess) > 7:
        return
    
    if len(guess) != len(group_session.current_word):
        return
    
    if guess in group_session.guesses:
        await message.answer("❌ That word was already guessed!")
        return
    
    # Process guess
    group_session.guesses.append(guess)
    group_session.attempts += 1
    
    user_mention = f"<a href='tg://user?id={user_id}'>{message.from_user.full_name}</a>"
    
    if guess == group_session.current_word:
        # Correct guess!
        group_session.game_active = False
        chat_id = message.chat.id
        if chat_id in group_games:
            del group_games[chat_id]
        
        feedback_squares = "🟩" * len(group_session.current_word)
        
        await message.answer(f"{feedback_squares} {guess}")
        await message.answer(
            f"🎉 Congratulations {user_mention}! You guessed it right in {group_session.attempts} attempts! 🎯",
            parse_mode=ParseMode.HTML
        )
    
    elif group_session.attempts >= group_session.max_attempts and group_session.max_attempts != float('inf'):
        # Game over (only if not infinity mode)
        group_session.game_active = False
        chat_id = message.chat.id
        if chat_id in group_games:
            del group_games[chat_id]
        
        feedback_squares = get_wordle_feedback(guess, group_session.current_word)
        
        await message.answer(f"{feedback_squares} {guess}")
        await message.answer(
            f"💔 Game over! The word was <b>{group_session.current_word}</b>. Better luck next time! 🎮",
            parse_mode=ParseMode.HTML
        )
    else:
        # Continue game with feedback
        feedback_squares = get_wordle_feedback(guess, group_session.current_word)
        
        # Build cumulative feedback display
        feedback_lines = []
        for i, prev_guess in enumerate(group_session.guesses):
            prev_feedback = get_wordle_feedback(prev_guess, group_session.current_word)
            feedback_lines.append(f"{prev_feedback} {prev_guess}")
        
        await message.answer("\n".join(feedback_lines))

async def handle_basic_game_word(message: types.Message, game: BasicGameSession):
    """Handle word submission in basic game"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    word = message.text.upper().strip()
    
    # Check if it's the current player's turn
    current_player = get_current_player(game)
    if current_player != user_id:
        return  # Not this player's turn
    
    # Validate word
    if not await is_valid_word(word):
        await message.answer(f"{word} is not a valid English word (must be at least {game.min_word_length} letters).")
        return
    
    # Check minimum word length requirement
    if len(word) < game.min_word_length:
        await message.answer(f"{word} is too short. Word must be at least {game.min_word_length} letters long.")
        return
    
    # Check if word starts with required letter
    if game.current_required_letter and not word.startswith(game.current_required_letter):
        await message.answer(f"{word} does not start with {game.current_required_letter}.")
        return
    
    # Check if word was already used
    if word.lower() in [w.lower() for w in game.words_used]:
        await message.answer(f"{word} has already been used.")
        return
    
    # Accept the word
    game.words_used.append(word)
    game.total_words += 1
    game.last_word = word
    game.current_required_letter = word[-1]  # Next word must start with last letter
    
    # Track longest word
    if len(word) > len(game.longest_word):
        game.longest_word = word
        player_mention = f"<a href='tg://user?id={user_id}'>{game.players[user_id]['full_name']}</a>"
        game.longest_word_player = player_mention
    
    # Progressive difficulty: decrease timer and increase word length
    old_time_limit = game.turn_time_left
    old_min_length = game.min_word_length
    
    # Decrease timer by 5 seconds every 3 words (minimum 15 seconds)
    if game.total_words % 3 == 0 and game.turn_time_left > 15:
        game.turn_time_left = max(15, game.turn_time_left - 5)
    
    # Increase minimum word length every 2 words (maximum 9 letters)
    if game.total_words % 2 == 0 and game.min_word_length < 9:
        game.min_word_length += 1
    
    # Move to next player
    game.current_turn_index = (game.current_turn_index + 1) % len(game.turn_order)
    
    # Reset individual turn timer to current time limit
    game.current_turn_timer = game.turn_time_left
    
    # Create acceptance message with difficulty changes
    acceptance_msg = f"{word} is accepted."
    
    if game.turn_time_left < old_time_limit:
        acceptance_msg += f"\n\nTime limit decreased from {old_time_limit}s to {game.turn_time_left}s."
    if game.min_word_length > old_min_length:
        acceptance_msg += f"\nMinimum letters per word increased from {old_min_length} to {game.min_word_length}."
    

    
    await message.answer(acceptance_msg)
    
    # Send updated game state
    try:
        await bot.edit_message_text(
            text=format_basic_game_state(game),
            chat_id=chat_id,
            message_id=message.message_id - 1,  # Approximate the game message
            parse_mode=ParseMode.HTML
        )
    except:
        # If edit fails, send new message
        await message.answer(format_basic_game_state(game), parse_mode=ParseMode.HTML)

async def start_basic_game_timer(chat_id: int):
    """Start the basic game countdown timer"""
    if chat_id not in basic_games:
        return
    
    game = basic_games[chat_id]
    
    # Join phase timer
    while game.join_time_left > 0 and game.game_state in ["waiting", "joining"]:
        await asyncio.sleep(1)
        game.join_time_left -= 1
        
        # Check if game was cancelled or doesn't exist anymore
        if chat_id not in basic_games or game.game_state == "cancelled":
            return
        
        # Send countdown warnings
        if game.join_time_left == 30:
            try:
                await bot.send_message(chat_id, "30s left to join.")
            except:
                pass
        elif game.join_time_left == 15:
            try:
                await bot.send_message(chat_id, "15s left to join.")
            except:
                pass
    
    # Check if enough players joined
    if len(game.players) < game.min_players:
        try:
            await bot.send_message(chat_id, "Not enough players joined. Game cancelled.")
        except:
            pass
        if chat_id in basic_games:
            del basic_games[chat_id]
        return
    
    # Start the game
    game.game_state = "active"
    random.shuffle(game.turn_order)  # Randomize turn order
    
    # Set starting word and first random letter
    starting_word = get_starting_word()
    game.last_word = starting_word
    game.current_required_letter = starting_word[-1]
    
    try:
        turn_order_text = "\n".join([f"<a href='tg://user?id={uid}'>{game.players[uid]['full_name']}</a>" for uid in game.turn_order])
        await bot.send_message(
            chat_id,
            f"Game is starting...\n\nThe first word is {starting_word}.\n\nTurn order:\n{turn_order_text}\n\n{format_basic_game_state(game)}",
            parse_mode=ParseMode.HTML
        )
    except:
        pass
    
    # Turn timer loop
    while game.game_state == "active" and len([p for p in game.players.values() if not p['eliminated']]) > 1:
        await asyncio.sleep(1)
        game.current_turn_timer -= 1
        
        # Check if game was cancelled or doesn't exist anymore
        if chat_id not in basic_games or game.game_state == "cancelled":
            return
        
        if game.current_turn_timer <= 0:
            # Current player is eliminated
            current_player = get_current_player(game)
            if current_player:
                game.players[current_player]['eliminated'] = True
                player_mention = f"<a href='tg://user?id={current_player}'>{game.players[current_player]['full_name']}</a>"
                
                # Remove from turn order
                game.turn_order.remove(current_player)
                if game.current_turn_index >= len(game.turn_order):
                    game.current_turn_index = 0
                
                # Reset timer for next player
                game.current_turn_timer = game.turn_time_left
                
                try:
                    await bot.send_message(chat_id, f"{player_mention} is eliminated for not answering in time!", parse_mode=ParseMode.HTML)
                except:
                    pass
                
                # Check if game should end
                active_players = [p for p in game.players.values() if not p['eliminated']]
                if len(active_players) <= 1:
                    # Game over - format final statistics
                    game_duration = time.time() - game.game_start_time
                    duration_minutes = int(game_duration // 60)
                    duration_seconds = int(game_duration % 60)
                    
                    if active_players:
                        winner_mention = f"<a href='tg://user?id={active_players[0]['user_id']}'>{active_players[0]['full_name']}</a>"
                        final_msg = f"🏆 {winner_mention} won the game out of {len(game.players)} players!\n"
                    else:
                        final_msg = "🏆 Game ended with no remaining players!\n"
                    
                    final_msg += f"Total words: {game.total_words}\n"
                    if game.longest_word:
                        final_msg += f"Longest word: {game.longest_word} from {game.longest_word_player}\n"
                    final_msg += f"Game length: {duration_minutes:02d}:{duration_seconds:02d}"
                    
                    try:
                        await bot.send_message(chat_id, final_msg, parse_mode=ParseMode.HTML)
                    except:
                        pass
                    
                    # Clean up game
                    if chat_id in basic_games:
                        del basic_games[chat_id]
                    return
                    if chat_id in basic_games:
                        del basic_games[chat_id]
                    return
                
                # Reset timer for next player
                game.turn_time_left = 40
                
                try:
                    await bot.send_message(chat_id, format_basic_game_state(game), parse_mode=ParseMode.HTML)
                except:
                    pass

async def start_new_game(callback_query: CallbackQuery, session: UserSession, is_group: bool = False):
    """Start a new word guessing game"""
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    if len(active_games) >= MAX_CONCURRENT_GAMES:
        await callback_query.message.edit_text(
            "🚫 Too many active games. Please try again later.",
            reply_markup=None
        )
        return
    
    # Get random word
    words = await get_random_words(session.word_length, 1)
    if not words:
        await callback_query.message.edit_text(
            "❌ Failed to get a word. Please try again later.",
            reply_markup=None
        )
        return
    
    # Initialize game
    session.current_word = words[0].upper()
    session.guesses = []
    session.attempts = 0
    session.game_active = True
    session.game_start_time = time.time()  # Initialize timer
    session.timer_expired = False
    
    if not is_group:
        active_games.add(user_id)
    
    # Create game message
    if is_group:
        timer_text = 'No limit' if session.timer_difficulty == 'noob' else f"{get_timer_seconds(session.timer_difficulty)}s"
        game_text = f"🎮 <b>Group Word Guessing Game Started!</b>\n\n📝 Word: {'_ ' * session.word_length}\n📏 Length: {session.word_length} letters\n🎯 Max Attempts: {'∞' if session.max_attempts == float('inf') else int(session.max_attempts)}\n⏱ Timer: {timer_text}\n\n💬 Everyone can guess by typing {session.word_length}-letter words!"
    else:
        game_text = format_game_state(session)
    
    # Update message with game state
    await callback_query.message.edit_text(
        game_text,
        reply_markup=create_game_keyboard(session),
        parse_mode=ParseMode.HTML
    )

@dp.message(lambda message: message.text and not message.text.startswith('/'))
async def handle_guess(message: types.Message):
    """Handle word guesses and basic game words"""
    logger.info(f"🎯 GUESS HANDLER TRIGGERED: User {message.from_user.id} sent '{message.text}' in chat {message.chat.id}")
    
    if not rate_limiter.is_allowed(message.from_user.id):
        logger.info(f"⚠️ Rate limited user {message.from_user.id}")
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    session = get_user_session(user_id)
    logger.info(f"📊 Retrieved session for user {user_id}: active={session.game_active}, word='{session.current_word}'")
    
    # Check for basic game first
    if chat_id in basic_games:
        logger.info(f"🎲 Basic game found in chat {chat_id}")
        game = basic_games[chat_id]
        if game.game_state == "active" and user_id in game.players:
            logger.info(f"🎲 Processing basic game word from user {user_id}")
            await handle_basic_game_word(message, game)
            return
        else:
            logger.info(f"🎲 Basic game not active or user not in game: state={game.game_state}, user_in_game={user_id in game.players}")
    
    # Check for group game first (if in group chat)
    if chat_id != user_id and chat_id in group_games:
        logger.info(f"👥 Group game found in chat {chat_id}")
        # This is a group chat with an active group game
        group_session = group_games[chat_id]
        if group_session.game_active and group_session.current_word:
            logger.info(f"👥 Processing group game guess from user {user_id}")
            await handle_group_guess(message, group_session)
            return
        else:
            logger.info(f"👥 Group game not active: active={group_session.game_active}, word='{group_session.current_word}'")
    
    # Only process if there's an active regular game (for private chats or no group game)
    if not session.game_active or not session.current_word:
        logger.info(f"❌ User {user_id} sent '{message.text}' but no active game (active={session.game_active}, word='{session.current_word}')")
        logger.info(f"🔍 DEBUGGING: chat_id={chat_id}, user_id={user_id}, is_private={chat_id == user_id}")
        return
    
    # Check timer expiration first
    if is_timer_expired(session):
        session.game_active = False
        session.timer_expired = True
        active_games.discard(user_id)
        user_mention = f"<a href='tg://user?id={user_id}'>{message.from_user.full_name}</a>"
        
        await message.answer(
            f"⏰ Time's up {user_mention}! The word was <b>{session.current_word}</b>. Try again with /play!",
            parse_mode=ParseMode.HTML
        )
        return
    
    guess = message.text.upper().strip()
    
    # Debug logging
    logger.info(f"Processing message from user {user_id}: '{message.text}' -> guess: '{guess}'")
    logger.info(f"Session state: active={session.game_active}, word='{session.current_word}', attempts={session.attempts}/{session.max_attempts}")
    
    # Only respond to messages that could be valid guesses
    if not guess.isalpha():
        logger.info(f"Ignoring non-alpha guess: '{guess}'")
        return
    
    if len(guess) < 3 or len(guess) > 7:
        logger.info(f"Ignoring invalid length guess: '{guess}' (len={len(guess)})")
        return
    
    if len(guess) != len(session.current_word):
        logger.info(f"Wrong length guess: '{guess}' (len={len(guess)}) vs word len={len(session.current_word)}")
        return
    
    if guess in session.guesses:
        await message.answer("❌ You already guessed that word!")
        return
    
    logger.info(f"Processing valid guess: '{guess}' - current attempts: {session.attempts}")
    
    # Process guess
    session.guesses.append(guess)
    session.attempts += 1
    
    if guess == session.current_word:
        # Correct guess!
        feedback_squares = "🟩" * len(session.current_word)
        user_mention = f"<a href='tg://user?id={user_id}'>{message.from_user.full_name}</a>"
        
        # Clean up game state
        session.game_active = False
        session.current_word = ""  # Clear current word
        active_games.discard(user_id)
        
        await message.answer(f"{feedback_squares} {guess}")
        await message.answer(
            f"🎉 Congratulations {user_mention}! You guessed it right in {session.attempts} attempts! 🎯",
            parse_mode=ParseMode.HTML
        )
    
    elif session.attempts >= session.max_attempts and session.max_attempts != float('inf'):
        # Game over (only if not infinity mode)
        feedback_squares = get_wordle_feedback(guess, session.current_word)
        word_to_show = session.current_word  # Store word before clearing
        
        # Clean up game state
        session.game_active = False
        session.current_word = ""  # Clear current word
        active_games.discard(user_id)
        user_mention = f"<a href='tg://user?id={user_id}'>{message.from_user.full_name}</a>"
        
        await message.answer(f"{feedback_squares} {guess}")
        await message.answer(
            f"💔 Game over {user_mention}! The word was <b>{word_to_show}</b>. Better luck next time! 🎮",
            parse_mode=ParseMode.HTML
        )
    
    else:
        # Continue game - show wordle-style feedback
        feedback_squares = get_wordle_feedback(guess, session.current_word)
        
        # Build cumulative feedback display
        feedback_lines = []
        for i, prev_guess in enumerate(session.guesses):
            prev_feedback = get_wordle_feedback(prev_guess, session.current_word)
            feedback_lines.append(f"{prev_feedback} {prev_guess}")
        
        await message.answer("\n".join(feedback_lines))

def get_guess_feedback(guess: str, target: str) -> str:
    """Generate feedback for a guess"""
    if len(guess) != len(target):
        return "❌ Wrong length!"
    
    feedback = []
    for i, char in enumerate(guess):
        if char == target[i]:
            feedback.append(f"✅ {char} (correct position)")
        elif char in target:
            feedback.append(f"🟡 {char} (in word, wrong position)")
        else:
            feedback.append(f"❌ {char} (not in word)")
    
    return "\n".join(feedback)

def get_wordle_feedback(guess: str, target: str) -> str:
    """Generate Wordle-style colored square feedback"""
    if len(guess) != len(target):
        return "❌"
    
    feedback = []
    target_chars = list(target)
    
    # First pass: mark exact matches
    for i, char in enumerate(guess):
        if char == target[i]:
            feedback.append("🟩")
            target_chars[i] = None  # Mark as used
        else:
            feedback.append(None)  # Placeholder
    
    # Second pass: mark wrong positions and not in word
    for i, char in enumerate(guess):
        if feedback[i] is None:  # Not an exact match
            if char in target_chars:
                feedback[i] = "🟨"
                target_chars[target_chars.index(char)] = None  # Mark as used
            else:
                feedback[i] = "⬜"
    
    return "".join(feedback)

@dp.message(lambda message: message.new_chat_members)
async def welcome_new_members(message: types.Message):
    """Welcome message when bot is added to a group"""
    for member in message.new_chat_members:
        if member.id == bot.id:
            # Bot was added to the group
            welcome_text = f"""👋 <b>Hello everyone!</b>

🎮 I'm <b>{BOT_NAME}</b>, your new word guessing game bot!

🎯 <b>What I can do:</b>
• Play exciting word guessing games
• Challenge members with different difficulty levels
• Keep everyone entertained with vocabulary puzzles

🚀 <b>Get Started:</b>
• Use /play to start a game
• Use /help for more information
• Have fun and enjoy the games!

Let's play! 🎮"""
            
            await message.answer(
                welcome_text,
                parse_mode=ParseMode.HTML
            )
            break

async def cleanup_task():
    """Periodic cleanup task"""
    while True:
        try:
            cleanup_inactive_sessions()
            await asyncio.sleep(300)  # Run every 5 minutes
        except Exception as e:
            logger.error(f"Cleanup task error: {e}")
            await asyncio.sleep(60)

async def main():
    """Main bot function with enhanced error handling and colored logging"""
    global BOT_USERNAME
    
    try:
        # Enhanced startup logging with colored output
        logger.info(f"🚀 Starting {BOT_NAME} bot with enhanced features...")
        logger.info(f"🔧 Owner ID: {OWNER_ID}")
        logger.info(f"📊 Max concurrent games: {MAX_CONCURRENT_GAMES}")
        
        # Start dummy HTTP server (needed for Render health check)
        logger.info("🌐 Starting dummy HTTP server for health checks...")
        threading.Thread(target=start_dummy_server, daemon=True).start()
        logger.info("✅ HTTP server started successfully")
        
        # Get bot info to set username dynamically
        try:
            logger.info("🔍 Fetching bot information...")
            bot_info = await bot.get_me()
            BOT_USERNAME = bot_info.username
            logger.info(f"✅ Bot username: @{BOT_USERNAME}")
            logger.info(f"🤖 Bot ID: {bot_info.id}")
            logger.info(f"📛 Bot name: {bot_info.first_name}")
        except Exception as e:
            logger.error(f"❌ Failed to get bot info: {e}")
            raise
        
        # Set bot commands with enhanced logging
        logger.info("⚙️ Setting bot commands...")
        commands = [
            types.BotCommand(command="start", description="Welcome message and bot info"),
            types.BotCommand(command="play", description="Start a word guessing game"),
            types.BotCommand(command="stop", description="Stop current game"),
            types.BotCommand(command="help", description="Show help information"),
        ]
        
        await bot.set_my_commands(commands)
        logger.info(f"✅ Bot commands set successfully ({len(commands)} commands)")
        
        # Initialize data structures with logging
        logger.info("🔧 Initializing data structures...")
        logger.info(f"📊 Active games: {len(active_games)}")
        logger.info(f"👥 Group games: {len(group_games)}")
        logger.info(f"🎲 Basic games: {len(basic_games)}")
        logger.info(f"👤 User sessions: {len(user_sessions)}")
        logger.info(f"📢 Users for broadcast: {len(user_ids)}")
        logger.info(f"📢 Groups for broadcast: {len(group_ids)}")
        
        # Log handler registration order for debugging
        logger.info("🔧 Message handlers registered in this order:")
        logger.info("  1. CommandStart() - /start")
        logger.info("  2. Command('play') - /play")
        logger.info("  3. Command('stop') - /stop")
        logger.info("  4. Command('debug') - /debug")
        logger.info("  5. Command('help') - /help")
        logger.info("  6. Command('ping') - /ping")
        logger.info("  7. Command('broadcast') - /broadcast")
        logger.info("  8. F.chat.type == 'private' - private messages (with regex filter)")
        logger.info("  9. F.chat.type.in_({'group', 'supergroup'}) - group messages (with regex filter)")
        logger.info("  10. lambda message: message.text and not message.text.startswith('/') - guess handler")
        logger.info("  11. lambda message: message.new_chat_members - new members")
        
        # Start cleanup task with enhanced logging
        logger.info("🧹 Starting cleanup task...")
        cleanup_task_handle = asyncio.create_task(cleanup_task())
        logger.info("✅ Cleanup task started")
        
        # Final startup message
        logger.info("🎮 Bot startup completed successfully!")
        logger.info("🔄 Starting polling for updates...")
        
        # Start polling with enhanced error handling
        await dp.start_polling(
            bot, 
            allowed_updates=["message", "callback_query", "my_chat_member"],
            skip_updates=True  # Skip pending updates on startup
        )
        
    except KeyboardInterrupt:
        logger.warning("⚠️ Bot received shutdown signal (Ctrl+C)")
        raise
    except Exception as e:
        logger.error(f"💥 Critical error during bot startup: {e}")
        logger.error(f"🔍 Error type: {type(e).__name__}")
        raise
    finally:
        logger.info("🛑 Bot shutdown sequence initiated...")
        try:
            if 'cleanup_task_handle' in locals():
                cleanup_task_handle.cancel()
                logger.info("🧹 Cleanup task cancelled")
            
            if bot.session and not bot.session.closed:
                await bot.session.close()
                logger.info("🔌 Bot session closed")
                
            logger.info("✅ Bot shutdown completed gracefully")
        except Exception as e:
            logger.error(f"❌ Error during shutdown: {e}")

if __name__ == "__main__":
    try:
        logger.info("🎮 WordSensei Word Game Bot - Starting up...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("⚠️ Bot stopped by user (Ctrl+C)")
        logger.info("👋 Thanks for using WordSensei Bot!")
    except Exception as e:
        logger.error(f"💥 Bot crashed with critical error: {e}")
        logger.error(f"🔍 Error type: {type(e).__name__}")
        logger.error("📞 Please check your bot token and network connection")
        raise
