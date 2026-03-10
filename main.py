import threading
import bot
import web_app


def run_web():
    web_app.start_web()


def main():
    t = threading.Thread(target=run_web, daemon=True)
    t.start()
    bot.main()


if __name__ == "__main__":
    main()
