import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Required configuration
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_ID = os.getenv('ADMIN_ID')
    DB_PATH = os.getenv('DB_PATH', 'appeals.db')
    
    @classmethod
    def validate(cls):
        """Validate all required configuration"""
        errors = []
        
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN is required in environment variables")
        elif not cls.BOT_TOKEN.startswith('') or ':' not in cls.BOT_TOKEN:
            errors.append("BOT_TOKEN appears to be invalid")
        
        if not cls.ADMIN_ID:
            errors.append("ADMIN_ID is required in environment variables")
        else:
            try:
                cls.ADMIN_ID = int(cls.ADMIN_ID)  # Convert to integer
            except ValueError:
                errors.append("ADMIN_ID must be a valid numeric ID")
        
        if errors:
            raise ValueError("\n".join(errors))
