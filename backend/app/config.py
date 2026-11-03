from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "mysql+asyncmy://locker:locker@db:3306/locker?charset=utf8mb4"
    sync_database_url: str = "mysql+pymysql://locker:locker@db:3306/locker?charset=utf8mb4"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = "dev-webhook-secret"
    mqtt_host: str = ""  # empty = MQTT disabled (commands are only logged)
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    frontend_origin: str = "http://localhost:3000"
    booking_hold_minutes: int = 15  # REQ-BOOK-004
    # price per hour in paise (REQ-PAY-004)
    price_per_hour_paise: dict = {"M": 4000, "L": 6000}


settings = Settings()
