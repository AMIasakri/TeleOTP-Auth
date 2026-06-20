from pydantic import BaseModel


class Settings(BaseModel):
    SECRET_KEY: str = "super_secret_key_change_me"
    TELEGRAM_BOT_TOKEN: str = "8983099356:AAH36CA3Ux8fqhTNtLU2NhXXXNmCzNUNom0"


settings = Settings()