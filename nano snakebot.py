"""
Telegram Snake Game Bot (Python) - Single-file
Requirements:
  pip install pyTelegramBotAPI

How it works:
- Users press /start to see menu and choose difficulty (Medium / Hard)
- The game uses an emoji grid that is edited each tick.
- Controls are inline arrow buttons.
- Each user has independent game state stored in memory (process-local).

Notes:
- This is a simple implementation intended for small-scale use / learning.
- For production you should persist state (Redis/DB) and run background worker(s).

Author: ChatGPT (GPT-5 Thinking mini)
"""

import threading
import time
import random
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ========== CONFIG ==========
TOKEN = "REPLACE_WITH_YOUR_BOT_TOKEN"
# Grid sizes for difficulties
DIFFICULTIES = {
    'medium': {'size': 10, 'tick': 0.6},  # medium: 10x10, 0.6s per move
    'hard': {'size': 12, 'tick': 0.35},   # hard: 12x12, 0.35s per move
}
# Emojis
EMPT = '⬛'  # empty
SNAKE = '🟩'  # snake body/head
FOOD = '🍎'
HEAD = '🟢'

bot = telebot.TeleBot(TOKEN)

# Per-user games: chat_id -> Game instance
games = {}

lock = threading.Lock()

class Game:
    def __init__(self, chat_id, difficulty='medium'):
        self.chat_id = chat_id
        self.difficulty = difficulty
        self.size = DIFFICULTIES[difficulty]['size']
        self.tick = DIFFICULTIES[difficulty]['tick']
        self.score = 0
        self.running = False
        self.direction = (0, 1)  # moving right initially (dx, dy)
        self.snake = []  # list of (r,c) with head at 0
        self.food = None
        self.msg_id = None
        self.timer = None
        self.lock = threading.Lock()
        self.init_board()

    def init_board(self):
        mid = self.size // 2
        # start snake of length 3 horizontally
        self.snake = [(mid, mid), (mid, mid-1), (mid, mid-2)]
        self.place_food()
        self.score = 0
        self.direction = (0, 1)
        self.running = True

    def place_food(self):
        empty = [(r, c) for r in range(self.size) for c in range(self.size) if (r,c) not in self.snake]
        self.food = random.choice(empty) if empty else None

    def render(self):
        # produce a string with the grid
        grid = [[EMPT for _ in range(self.size)] for _ in range(self.size)]
        if self.food:
            fr, fc = self.food
            grid[fr][fc] = FOOD
        for i, (r,c) in enumerate(self.snake):
            grid[r][c] = SNAKE
        # mark head
        hr, hc = self.snake[0]
        grid[hr][hc] = HEAD
        rows = [''.join(row) for row in grid]
        board = '\n'.join(rows)
        return f"Score: {self.score}  |  Difficulty: {self.difficulty.title()}\n\n{board}"

    def step(self):
        # move snake by one step; return status: 'ok', 'food', 'dead'
        if not self.running:
            return 'stopped'
        head = self.snake[0]
        dr, dc = self.direction
        new_head = ((head[0] + dr) % self.size, (head[1] + dc) % self.size)  # wrap-around
        # collision with body?
        if new_head in self.snake[:-1]:
            self.running = False
            return 'dead'
        ate = (new_head == self.food)
        self.snake.insert(0, new_head)
        if ate:
            self.score += 1
            self.place_food()
            return 'food'
        else:
            self.snake.pop()
            return 'ok'

    def change_direction(self, new_dir):
        # prevent 180-degree reversal
        with self.lock:
            dr, dc = new_dir
            cr, cc = self.direction
            if (dr == -cr and dc == -cc):
                return False
            self.direction = (dr, dc)
            return True

    def stop(self):
        with self.lock:
            self.running = False
            if self.timer:
                self.timer.cancel()
                self.timer = None

    def schedule_tick(self):
        # schedule next move
        if not self.running:
            return
        self.timer = threading.Timer(self.tick, tick_worker, args=(self.chat_id,))
        self.timer.start()

# ========== HELPERS ==========

def main_menu_markup():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton('Play — Medium', callback_data='start_medium'),
           InlineKeyboardButton('Play — Hard', callback_data='start_hard'))
    kb.row(InlineKeyboardButton('Help', callback_data='help'))
    return kb


def controls_markup():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton('⬆️', callback_data='dir_up')
    )
    kb.row(
        InlineKeyboardButton('⬅️', callback_data='dir_left'),
        InlineKeyboardButton('⏸️', callback_data='pause'),
        InlineKeyboardButton('➡️', callback_data='dir_right')
    )
    kb.row(
        InlineKeyboardButton('⬇️', callback_data='dir_down'),
    )
    kb.row(
        InlineKeyboardButton('Restart', callback_data='restart'),
        InlineKeyboardButton('Quit', callback_data='quit')
    )
    return kb

# ========== BOT COMMANDS ==========

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = "Welcome to Snake Bot!\nChoose a difficulty to start playing.\n\nControls: Use the arrow buttons to move. The snake wraps around edges.\nEat the apple (🍎) to grow and increase score.\nAvoid hitting your body."
    bot.send_message(message.chat.id, text, reply_markup=main_menu_markup())

# ========== CALLBACKS ==========

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    data = call.data
    cid = call.message.chat.id

    # Start game
    if data.startswith('start_'):
        diff = data.split('_', 1)[1]
        start_game(cid, diff, call.message.message_id)
        bot.answer_callback_query(call.id, f"Starting {diff.title()}...")
        return

    # In-game controls
    if data.startswith('dir_') or data in ('pause', 'restart', 'quit'):
        if cid not in games:
            bot.answer_callback_query(call.id, "No active game. Press Play to start.")
            return
        game = games[cid]
        if data == 'pause':
            if game.running:
                game.stop()
                bot.edit_message_text(game.render() + "\n\n⏸️ Paused.", cid, game.msg_id, reply_markup=main_menu_markup())
                bot.answer_callback_query(call.id, "Paused")
            else:
                # resume
                game.running = True
                bot.edit_message_text(game.render(), cid, game.msg_id, reply_markup=controls_markup())
                game.schedule_tick()
                bot.answer_callback_query(call.id, "Resumed")
            return
        if data == 'restart':
            diff = game.difficulty
            start_game(cid, diff, call.message.message_id)
            bot.answer_callback_query(call.id, "Restarting...")
            return
        if data == 'quit':
            game.stop()
            del games[cid]
            bot.edit_message_text("You quit the game.", cid, call.message.message_id, reply_markup=main_menu_markup())
            bot.answer_callback_query(call.id, "Quit")
            return
        # direction
        if data == 'dir_up':
            game.change_direction((-1, 0))
        elif data == 'dir_down':
            game.change_direction((1, 0))
        elif data == 'dir_left':
            game.change_direction((0, -1))
        elif data == 'dir_right':
            game.change_direction((0, 1))
        bot.answer_callback_query(call.id, "Direction changed")
        return

    if data == 'help':
        bot.answer_callback_query(call.id)
        bot.send_message(cid, "Help:\nChoose a difficulty to start. Use arrows to move. Restart to restart. Quit to stop.")
        return

# ========== GAME LIFECYCLE ==========

def start_game(chat_id, difficulty, message_id=None):
    with lock:
        # stop existing game if present
        if chat_id in games:
            try:
                games[chat_id].stop()
            except Exception:
                pass
        game = Game(chat_id, difficulty=difficulty)
        games[chat_id] = game

    # send initial board
    sent = bot.send_message(chat_id, game.render(), reply_markup=controls_markup())
    game.msg_id = sent.message_id
    # schedule first tick
    game.schedule_tick()


def tick_worker(chat_id):
    # called by timer thread
    try:
        game = games.get(chat_id)
        if not game or not game.running:
            return
        status = game.step()
        if status == 'dead':
            bot.edit_message_text(game.render() + f"\n\n💀 You crashed! Final score: {game.score}", chat_id, game.msg_id, reply_markup=main_menu_markup())
            # cleanup
            with lock:
                if chat_id in games:
                    games[chat_id].stop()
                    del games[chat_id]
            return
        else:
            # Update board message
            bot.edit_message_text(game.render(), chat_id, game.msg_id, reply_markup=controls_markup())
            # schedule next
            game.schedule_tick()
    except Exception as e:
        print('Tick error:', e)

# ========== RUN ==========

if __name__ == '__main__':
    print('Bot is running...')
    bot.infinity_polling()
