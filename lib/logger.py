import logging
import datetime
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler

LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

class Logger():

    @classmethod
    def config_root_logger(self):
        timestamp = datetime.datetime.now()
        root_filename = f"{LOGS_DIR}/{timestamp}.log"
        Path(root_filename).touch()

        file_handler = RotatingFileHandler(root_filename, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
        format = logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                   datefmt="%Y-%m-%d %H:%M:%S")
        
        def conversation(self, message, *args, **kwargs):
            CONVERSATION_LEVEL = 25
            logging.addLevelName(CONVERSATION_LEVEL, "CONVERSATION")

            if self.isEnabledFor(CONVERSATION_LEVEL):
                self._log(CONVERSATION_LEVEL, message, args, **kwargs)

        logging.Logger.conversation = conversation
        
        file_handler.setFormatter(format)

        cli_handler = logging.StreamHandler()
        cli_handler.setFormatter(format)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        root_logger.addHandler(cli_handler)
        root_logger.addHandler(file_handler)

        root_logger.info("Initialized logger")




