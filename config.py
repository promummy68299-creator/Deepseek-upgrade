import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'bot_database.db')
    PORT = int(os.getenv('PORT', 8080))
    
    # Payment settings
    DEFAULT_QR = os.getenv('DEFAULT_QR', '')
    DEFAULT_UPI = os.getenv('DEFAULT_UPI', '')
    DEFAULT_DELIVERY_LINK = os.getenv('DEFAULT_DELIVERY_LINK', '')
    
    # Welcome settings
    DEFAULT_WELCOME_TEXT = """Welcome to Premium Subscription Bot! 🎉

Get access to premium content with our subscription plans.

Choose a plan below to get started!"""
    
    DEFAULT_WELCOME_IMAGE = ''
    
    # Plan defaults
    DEFAULT_PLAN_VALIDITY = 30  # days
    
    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is required")
        if not cls.ADMIN_IDS:
            raise ValueError("ADMIN_IDS is required")
        return True