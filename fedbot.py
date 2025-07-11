import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext, MessageHandler, Filters
from telegram.error import TelegramError
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from config import Config
import sys

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Validate configuration
try:
    Config.validate()
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    sys.exit(1)

# --- Database Setup ---
class MongoDB:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            try:
                cls._instance.client = MongoClient(Config.MONGO_URI)
                cls._instance.db = cls._instance.client[Config.DB_NAME]
                cls._instance.appeals = cls._instance.db['appeals']
                logger.info("Connected to MongoDB successfully")
            except PyMongoError as e:
                logger.error(f"MongoDB connection error: {e}")
                sys.exit(1)
        return cls._instance

def init_db():
    """Initialize database indexes"""
    try:
        db = MongoDB().db
        db.appeals.create_index("user_id")
        db.appeals.create_index("status")
        db.appeals.create_index("appeal_type")
        db.appeals.create_index("created_at")
        logger.info("Database indexes initialized")
    except PyMongoError as e:
        logger.error(f"Database initialization error: {e}")
        sys.exit(1)

# --- User Commands ---
def start(update: Update, context: CallbackContext):
    """Start command handler"""
    try:
        update.message.reply_text(
            "📝 Welcome to the Appeals Bot!\n\n"
            "Use /appeal to submit a FedBan appeal or request Fed Admin status"
        )
        logger.info(f"User {update.effective_user.id} started the bot")
    except TelegramError as e:
        logger.error(f"Error in start command: {e}")

def appeal(update: Update, context: CallbackContext):
    """Appeal command handler"""
    try:
        keyboard = [
            [InlineKeyboardButton("🔓 Fed Unban Appeal", callback_data="unban")],
            [InlineKeyboardButton("👑 Fed Admin Request", callback_data="admin")]
        ]
        update.message.reply_text(
            "Select appeal type:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"User {update.effective_user.id} requested appeal menu")
    except TelegramError as e:
        logger.error(f"Error in appeal command: {e}")
        update.message.reply_text("❌ An error occurred. Please try again later.")

# Store temporary data for users writing appeals
user_appeals = {}

def handle_appeal_type(update: Update, context: CallbackContext):
    """Handle appeal type selection"""
    try:
        query = update.callback_query
        query.answer()
        user = query.from_user
        
        if query.data not in ['unban', 'admin']:
            query.edit_message_text("❌ Invalid appeal type")
            return
        
        user_appeals[user.id] = {'type': query.data}
        
        template = (
            "\n\n📝 Please write your appeal in detail. Example:\n"
            "1. Why were you banned?\n"
            "2. What have you learned?\n"
            "3. Why should we unban you?\n"
            "4. Any additional information?"
        ) if query.data == "unban" else (
            "\n\n📝 Please write your admin request. Example:\n"
            "1. Why do you want to be an admin?\n"
            "2. What experience do you have?\n"
            "3. How will you help the community?\n"
            "4. Any additional information?"
        )
            
        query.edit_message_text(
            f"✍️ Please write and submit your {'unban' if query.data == 'unban' else 'admin request'} appeal.{template}\n\n"
            "Type your appeal now:"
        )
        
        context.user_data['expecting_appeal_text'] = True
        context.user_data['appeal_type'] = query.data
        logger.info(f"User {user.id} selected {query.data} appeal type")
            
    except TelegramError as e:
        logger.error(f"Telegram error in handle_appeal_type: {e}")

def handle_appeal_text(update: Update, context: CallbackContext):
    """Handle user's appeal text submission"""
    try:
        if not context.user_data.get('expecting_appeal_text'):
            return
            
        user = update.message.from_user
        appeal_text = update.message.text
        appeal_type = context.user_data['appeal_type']
        
        try:
            db = MongoDB().db
            appeal_data = {
                "user_id": user.id,
                "username": user.username or f"{user.first_name or ''} {user.last_name or ''}".strip(),
                "appeal_type": appeal_type,
                "appeal_text": appeal_text,
                "status": "pending",
                "created_at": datetime.utcnow(),
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            result = db.appeals.insert_one(appeal_data)
            appeal_id = str(result.inserted_id)
            
            update.message.reply_text(
                f"✅ {appeal_type.capitalize()} appeal submitted successfully!\n"
                f"Appeal ID: {appeal_id}\n\n"
                "Your appeal will be reviewed by an admin."
            )
            
            try:
                context.bot.send_message(
                    Config.ADMIN_ID,
                    f"🚨 New Appeal {appeal_id}\n"
                    f"User: {appeal_data['username']} (ID: {user.id})\n"
                    f"Type: {appeal_type.capitalize()}\n"
                    f"Time: {appeal_data['timestamp']}\n\n"
                    f"📝 Appeal Text:\n{appeal_text}\n\n"
                    f"Use /approve {appeal_id} to approve\n"
                    f"Use /reject {appeal_id} to reject\n\n"
                    f"Use /pending to view all pending appeals"
                )
            except TelegramError as e:
                logger.error(f"Failed to notify admin: {e}")
                
            logger.info(f"Appeal {appeal_id} submitted by user {user.id}")
            
            del context.user_data['expecting_appeal_text']
            del context.user_data['appeal_type']
            if user.id in user_appeals:
                del user_appeals[user.id]
                
        except PyMongoError as e:
            logger.error(f"Database error in handle_appeal_text: {e}")
            update.message.reply_text("❌ Database error. Please try again later.")
            
    except TelegramError as e:
        logger.error(f"Telegram error in handle_appeal_text: {e}")

# --- Admin Commands ---
def pending(update: Update, context: CallbackContext):
    """Show pending appeals (admin only)"""
    try:
        if update.effective_user.id != Config.ADMIN_ID:
            update.message.reply_text("❌ Access denied.")
            return
            
        try:
            db = MongoDB().db
            appeals = list(db.appeals.find({"status": "pending"}).sort("created_at", -1).limit(50))
            
            if not appeals:
                update.message.reply_text("📋 No pending appeals!")
                return
            
            response = "📋 Pending Appeals:\n\n"
            for appeal in appeals:
                response += (
                    f"ID: {appeal['_id']}\n"
                    f"User: {appeal['username']} (ID: {appeal['user_id']})\n"
                    f"Type: {appeal['appeal_type'].capitalize()}\n"
                    f"Time: {appeal['timestamp']}\n"
                    f"Text: {appeal['appeal_text'][:100]}...\n"
                    "───────────────\n"
                )
            
            for i in range(0, len(response), 4096):
                update.message.reply_text(response[i:i+4096])
                
            logger.info(f"Admin {update.effective_user.id} viewed pending appeals")
            
        except PyMongoError as e:
            logger.error(f"Database error in pending: {e}")
            update.message.reply_text("❌ Database error. Please try again later.")
            
    except TelegramError as e:
        logger.error(f"Telegram error in pending: {e}")

def view_appeal(update: Update, context: CallbackContext):
    """View full appeal details (admin only)"""
    try:
        if update.effective_user.id != Config.ADMIN_ID:
            update.message.reply_text("❌ Access denied.")
            return
            
        if not context.args:
            update.message.reply_text("❌ Usage: /view <appeal_id>")
            return
            
        appeal_id = context.args[0]
            
        try:
            db = MongoDB().db
            appeal = db.appeals.find_one({"_id": appeal_id})
            
            if not appeal:
                update.message.reply_text(f"❌ Appeal {appeal_id} not found.")
                return
                
            response = (
                f"📄 Appeal Details {appeal['_id']}\n"
                f"User: {appeal['username']} (ID: {appeal['user_id']})\n"
                f"Type: {appeal['appeal_type'].capitalize()}\n"
                f"Status: {appeal['status']}\n"
                f"Time: {appeal['timestamp']}\n\n"
                f"📝 Appeal Text:\n{appeal['appeal_text']}\n\n"
                f"Use /approve {appeal['_id']} to approve\n"
                f"Use /reject {appeal['_id']} to reject"
            )
            
            update.message.reply_text(response)
            logger.info(f"Admin viewed appeal {appeal_id}")
            
        except PyMongoError as e:
            logger.error(f"Database error in view_appeal: {e}")
            update.message.reply_text("❌ Database error. Please try again later.")
            
    except TelegramError as e:
        logger.error(f"Telegram error in view_appeal: {e}")

def approve(update: Update, context: CallbackContext):
    """Approve appeal (admin only)"""
    try:
        if update.effective_user.id != Config.ADMIN_ID:
            update.message.reply_text("❌ Access denied.")
            return
            
        if not context.args:
            update.message.reply_text("❌ Usage: /approve <appeal_id>")
            return
            
        appeal_id = context.args[0]
            
        try:
            db = MongoDB().db
            appeal = db.appeals.find_one_and_update(
                {"_id": appeal_id, "status": "pending"},
                {"$set": {"status": "approved"}},
                return_document=True
            )
            
            if not appeal:
                update.message.reply_text(f"❌ Appeal {appeal_id} not found or already processed.")
                return
                
            update.message.reply_text(f"✅ Appeal {appeal_id} approved successfully!")
            
            try:
                context.bot.send_message(
                    appeal['user_id'], 
                    f"🎉 Your {appeal['appeal_type']} appeal has been approved!\n"
                    f"Appeal ID: {appeal_id}\n\n"
                    f"Your appeal text:\n{appeal['appeal_text']}"
                )
                logger.info(f"User {appeal['user_id']} notified about approved appeal {appeal_id}")
            except TelegramError as e:
                logger.error(f"Failed to notify user {appeal['user_id']}: {e}")
                update.message.reply_text(f"Appeal approved but failed to notify user.")
                
            logger.info(f"Appeal {appeal_id} approved by admin {update.effective_user.id}")
            
        except PyMongoError as e:
            logger.error(f"Database error in approve: {e}")
            update.message.reply_text("❌ Database error. Please try again later.")
            
    except TelegramError as e:
        logger.error(f"Telegram error in approve: {e}")

def reject(update: Update, context: CallbackContext):
    """Reject appeal (admin only)"""
    try:
        if update.effective_user.id != Config.ADMIN_ID:
            update.message.reply_text("❌ Access denied.")
            return
            
        if not context.args:
            update.message.reply_text("❌ Usage: /reject <appeal_id>")
            return
            
        appeal_id = context.args[0]
            
        try:
            db = MongoDB().db
            appeal = db.appeals.find_one_and_update(
                {"_id": appeal_id, "status": "pending"},
                {"$set": {"status": "rejected"}},
                return_document=True
            )
            
            if not appeal:
                update.message.reply_text(f"❌ Appeal {appeal_id} not found or already processed.")
                return
                
            update.message.reply_text(f"❌ Appeal {appeal_id} rejected.")
            
            try:
                context.bot.send_message(
                    appeal['user_id'], 
                    f"❌ Your {appeal['appeal_type']} appeal has been rejected.\n"
                    f"Appeal ID: {appeal_id}\n\n"
                    f"Your appeal text:\n{appeal['appeal_text']}\n\n"
                    "You may submit a new appeal if you wish."
                )
                logger.info(f"User {appeal['user_id']} notified about rejected appeal {appeal_id}")
            except TelegramError as e:
                logger.error(f"Failed to notify user {appeal['user_id']}: {e}")
                update.message.reply_text(f"Appeal rejected but failed to notify user.")
                
            logger.info(f"Appeal {appeal_id} rejected by admin {update.effective_user.id}")
            
        except PyMongoError as e:
            logger.error(f"Database error in reject: {e}")
            update.message.reply_text("❌ Database error. Please try again later.")
            
    except TelegramError as e:
        logger.error(f"Telegram error in reject: {e}")

def stats(update: Update, context: CallbackContext):
    """Show appeal statistics (admin only)"""
    try:
        if update.effective_user.id != Config.ADMIN_ID:
            update.message.reply_text("❌ Access denied.")
            return
            
        try:
            db = MongoDB().db
            
            # Get basic stats
            total = db.appeals.count_documents({})
            pending = db.appeals.count_documents({"status": "pending"})
            approved = db.appeals.count_documents({"status": "approved"})
            rejected = db.appeals.count_documents({"status": "rejected"})
            
            # Get appeal type distribution
            type_stats = []
            for stat in db.appeals.aggregate([
                {"$group": {"_id": "$appeal_type", "count": {"$sum": 1}}}
            ]):
                type_stats.append(f"• {stat['_id'].capitalize()}: {stat['count']}")
            
            # Get recent activity
            last_24h = db.appeals.count_documents({
                "created_at": {"$gte": datetime.utcnow() - timedelta(days=1)}
            })
            
            last_7d = db.appeals.count_documents({
                "created_at": {"$gte": datetime.utcnow() - timedelta(days=7)}
            })
            
            response = (
                "📊 <b>Appeal Statistics</b>\n\n"
                f"<b>Total Appeals:</b> {total}\n"
                f"<b>Pending:</b> {pending}\n"
                f"<b>Approved:</b> {approved}\n"
                f"<b>Rejected:</b> {rejected}\n\n"
                f"<b>Recent Activity:</b>\n"
                f"• Last 24h: {last_24h}\n"
                f"• Last 7 days: {last_7d}\n\n"
                f"<b>By Appeal Type:</b>\n"
                f"{'\n'.join(type_stats)}\n\n"
                "Use /pending to view pending appeals"
            )
            
            update.message.reply_text(response, parse_mode='HTML')
            logger.info(f"Admin {update.effective_user.id} viewed statistics")
            
        except PyMongoError as e:
            logger.error(f"Database error in stats: {e}")
            update.message.reply_text("❌ Database error. Please try again later.")
            
    except TelegramError as e:
        logger.error(f"Telegram error in stats: {e}")

def error_handler(update: Update, context: CallbackContext):
    """Global error handler"""
    logger.error(f"Update {update} caused error {context.error}")

# --- Bot Setup ---
def main():
    """Main function to run the bot"""
    try:
        # Initialize database
        init_db()
        
        # Create updater
        updater = Updater(Config.BOT_TOKEN, use_context=True)
        dp = updater.dispatcher
        
        # User commands
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("appeal", appeal))
        
        # Admin commands
        dp.add_handler(CommandHandler("pending", pending))
        dp.add_handler(CommandHandler("view", view_appeal))
        dp.add_handler(CommandHandler("approve", approve))
        dp.add_handler(CommandHandler("reject", reject))
        dp.add_handler(CommandHandler("stats", stats))
        
        # Callbacks
        dp.add_handler(CallbackQueryHandler(handle_appeal_type))
        
        # Text handler for appeal submission
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_appeal_text))
        
        # Error handler
        dp.add_error_handler(error_handler)
        
        logger.info("Bot started successfully")
        print("Bot is running...")
        
        updater.start_polling()
        updater.idle()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
