import logging

class LoggerService:

    @staticmethod
    def get_logger():
        logging.basicConfig(
            level=logging.DEBUG,  # Change to INFO if you want less noise
            format="%(asctime)s [%(levelname)s] %(message)s"
        )

        logger = logging.getLogger(__name__)
        return logger
