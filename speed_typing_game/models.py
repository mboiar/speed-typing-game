"""
Classes:

    Wordset: contains words
    TypingGame: represents typing game state

"""

import logging
from typing import Dict, List, Any, Optional, Tuple
import time
from collections import Counter
import random
import sys
from functools import lru_cache

from PyQt6.QtSql import QSqlDatabase, QSqlQuery

from speed_typing_game import config
from speed_typing_game import database


class Wordset:
    """A named set with unique words from certain language and difficulty."""
    def __init__(self, name: str, language: str, difficulty: int, words: Tuple[str], id: int = None) -> None:
        self.logger = logging.getLogger(__name__)
        self.name = name
        self.language = language
        self.difficulty = difficulty
        self.words = words
        self.id = id
        self.logger.info(f"Initializing {self}")

    def __str__(self) -> str:
        return f"Wordset {self.name} - {self.language} - {self.difficulty} - {len(self.words)} words"
    
    def __repr__(self) -> str:
        return f"Wordset {self.name} - {self.language} - {self.difficulty} - {len(self.words)} words"
    
    def get_subset_with_repetitions(self, count: int, seed: int = None) -> Tuple[str]:
        """Ranomly generate a tuple of words from this wordset, with words possibly repeating themselves."""
        if not self.words:
            self.logger.warning("Unable to permute empty wordset.")
            return ()
        word_pool = tuple(self.words)
        SeededRandom = random.Random(seed)
        indices = SeededRandom.choices(range(len(word_pool)), k=count)
        return tuple(word_pool[i] for i in indices)
    
    @classmethod
    def from_file(cls, file_path: str) -> 'Wordset':
        logger = logging.getLogger(__name__)
        with open(file_path, 'r', encoding="utf-8") as file:
            data = file.read().split('\n')
            logger.debug(data)
            if not data:
                logger.warning(f"Received empty wordset {file_path}")
                return
            if len(data[0].split()) == 3:
                name, language, difficulty = data[0].split()
                words = data[1:]
            else:
                logger.error("Unable to read wordset from file: empty header")
                return
            words = tuple(i.strip().lower() for i in set(words) if i)
            if not words:
                logger.warning(f"Received empty wordset {file_path}")
                return
            return cls(name, language, difficulty, words)

    @classmethod
    @lru_cache(maxsize=4)
    def from_database(cls, id: int = None, name: str = "") -> 'Wordset':
        logger = logging.getLogger(__name__)
        if id:
            retrieveWordsetQueryString = f"""
            SELECT id, name, language_code, difficulty FROM {config.WORDSET_TABLE} 
            WHERE ID = '{id}'
            """
        elif name:
            retrieveWordsetQueryString = f"""
            SELECT id, name, language_code, difficulty FROM {config.WORDSET_TABLE} 
            WHERE NAME = '{name}'
            """
        else:
            logger.error("Cannot retrieve wordset from database: no id or name provided")
            return
        con_name = config.CON_NAME
        wordset_tablename = config.WORDSET_TABLE
        db = QSqlDatabase.database(con_name)
        if not db.open():
            logger.error(f"Could not open database connection {con_name}")
            return
        if not database.check_table_exists(wordset_tablename, con_name):
            logger.error(f"Table '{wordset_tablename}' does not exist.")
            return
        query = QSqlQuery(db)
        query.setForwardOnly(True)
        if not query.exec(retrieveWordsetQueryString) or not query.next():
            logger.error(f"Unable to retrieve wordset {name} from table {wordset_tablename}:\n" + query.lastError().text())
            return
        id = query.value(0)
        name = query.value(1)
        language = query.value(2)
        difficulty = query.value(3)
        logger.debug(f"{id} {name} {language} {difficulty}")

        query = QSqlQuery(db)
        retrieveWordQueryString = f"""
            SELECT W.content FROM {config.WORD_TABLE} W
            WHERE W.wordset_id = '{id}'
            """
        if not query.exec(retrieveWordQueryString):
            logger.error(f"Unable to retrieve words from table {config.WORD_TABLE}:\n" + query.lastError().text())
            return
        words = []
        while query.next():
            words.append(query.value(0))
        if not words:
            logger.error(f"Received empty wordset with id {id}")
            return
        logger.debug(f"Retrieved wordset with {len(words)} words from table {db.databaseName()}.{wordset_tablename}")
        return cls(name, language, difficulty, words, id)

    def save(self) -> None:
        return database.add_wordsets_to_database([self])
    
    @staticmethod
    def get_available_ids() -> List[int]:
        return database.get_available_wordsets_ids()


class TypingGame:
    def __init__(
            self,
            wordset_id: int = None, 
            seed: int = time.time(),
            mode: str = "default", 
            pos: int = 0,
            incorrect_chars: str = '',
            elapsed: float = 0,
            created_at: float = None,
            id: int = None,
            duration: int = 30*1000
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.seed = seed if seed else time.time()
        if wordset_id:
            self.wordset = Wordset.from_database(wordset_id)
        else:
            ids = Wordset.get_available_ids()
            if not ids:
                self.logger.error("Found no available wordsets in database")
                raise
            wordset_id = ids[1]
            self.wordset = Wordset.from_database(wordset_id)
            self.logger.warning(f"Using default wordset {self.wordset}")
            self.logger.debug(Wordset.from_database.cache_info())
        self.text = " ".join(self.wordset.get_subset_with_repetitions(100, self.seed))
        self.pos = pos
        self.mode = mode if mode else 'default'
        self.duration = duration if duration else 30*1000
        self.incorrect_chars = Counter(incorrect_chars)
        self.in_progress = False
        self.start_time = created_at
        self.elapsed = elapsed
        self.last_paused: float = 0
        self.id = id
        self.logger.info(f"Initializing {self}")

    def __str__(self) -> str:
        return f"TypingGame: wordset {self.wordset}, seed {self.seed}, mode {self.mode}, elapsed: {self.elapsed} out of {self.duration} ms"
    
    def __repr__(self) -> str:
        return f"TypingGame: wordset {self.wordset}, seed {self.seed}, mode {self.mode}, elapsed: {self.elapsed} out of {self.duration} ms"

    @classmethod
    def from_database(cls, id: int = None, created_at: int = None) -> 'TypingGame':
        logger = logging.getLogger(__name__)
        if id:
            retrieveGameQueryString = f"""
            SELECT id, mode, wordset_id, seed, pos, incorrect_chars, elapsed, created_at
            FROM {config.GAME_TABLE} 
            WHERE ID = '{id}'
            """
        elif created_at:
            retrieveGameQueryString = f"""
            SELECT id, mode, wordset_id, seed, pos, incorrect_chars, elapsed, created_at
            FROM {config.GAME_TABLE} 
            WHERE created_at = '{created_at}'
            """
        else:
            logger.error("Cannot retrieve game from database: no id or created_at provided")
            return
        con_name = config.CON_NAME
        game_tablename = config.GAME_TABLE
        db = QSqlDatabase.database(con_name)
        if not db.open():
            logger.error(f"Could not open database connection {con_name}")
            return
        if not database.check_table_exists(game_tablename, con_name):
            logger.error(f"Game table does not exist.")
            return
        query = QSqlQuery(db)
        if not query.exec(retrieveGameQueryString) or not query.next():
            logger.error(f"Unable to retrieve game from table {game_tablename}:\n" + query.lastError().text())
            return
        
        id = query.value(0)
        mode = query.value(1)
        wordset_id = query.value(2)
        seed = query.value(3)
        pos = query.value(4)
        incorrect_chars = query.value(5)
        elapsed = query.value(6)
        created_at = query.value(7)

        logger.debug(f"Retrieved game created at {created_at} from table {db.databaseName()}.{game_tablename}")
        return cls(wordset_id, seed, mode, pos, incorrect_chars, elapsed, created_at, id)

    def start_or_resume(self) -> bool:
        if self.in_progress or self.is_finished():
            self.logger.warning("Unable to start: game in progress or finished")
            return False
        if not self.start_time:
            self.start_time = time.time()
            self.last_paused = self.start_time
        self.in_progress = True
        self.logger.info(f"Started/resumed game {self}")
        return True

    def finish_or_pause(self, save: bool = True) -> bool:
        if not self.in_progress or self.is_finished():
            self.logger.warning("Unable to pause: game not in progress or finished")
            return False
        pause_time = time.time()
        self.elapsed += pause_time - self.last_paused
        self.last_paused = pause_time
        self.in_progress = False
        if save:
            return self.save()
        else:
            return True

    def get_word_count(self) -> int:
        return len(self.text[:self.pos].split())
    
    def get_wpm(self) -> float:
        if self.elapsed:
            return self.get_word_count()/self.elapsed
        else:
            return None
        
    def get_accuracy(self) -> float:
        return (self.pos - sum(self.incorrect_chars.values())) / self.pos
    
    def get_incorrect_char_freq(self):
        return self.incorrect_chars.items()
    
    def is_finished(self) -> bool:
        return self.elapsed > self.duration
    
    def get_stats(self) -> Dict:
        return {
            "duration": self.duration,
            "elapsed": self.elapsed, 
            "wpm": self.get_wpm(),
            "incorrect characters frequency": self.get_incorrect_char_freq(),
            "accuracy": self.get_accuracy(),
            "last character position": self.pos
        }
    
    def extend_text(self, count: int = 100) -> str:
        self.logger.info(f"Game: extending text by {count} words")
        self.text += " ".join(self.wordset.get_subset_with_repetitions(count, self.seed))
        return self.text
    
    def get_database_id(self) -> int:
        if not self.start_time:
            self.logger.warning("Cannot access game entry: game not started")
            return
        """Return id of game entry if it exists in database."""
        retrieveGameQueryString = f"""
            SELECT id
            FROM {config.GAME_TABLE} 
            WHERE created_at = '{self.start_time}'
            """
        con_name = config.CON_NAME
        game_tablename = config.GAME_TABLE
        db = QSqlDatabase.database(con_name)
        if not db.open():
            self.logger.error(f"Could not open database connection {con_name}")
            return False
        if not database.check_table_exists(game_tablename, con_name):
            self.logger.error(f"Game table does not exist.")
            return False
        query = QSqlQuery(db)
        if not query.exec(retrieveGameQueryString) or not query.next():
            self.logger.error(f"Unable to retrieve game data from table {game_tablename}:\n" + query.lastError().text())
            return False
        id = query.value(0)
        return id
    
    def save(self) -> bool:
        id = self.get_database_id()
        if id:
            # if game exists, update
            self.id = id
            return self._update_database_entry()
        else:
            # create new entry
            return database.add_games_to_database([self])
        
    def _update_database_entry(self) -> bool:
        con_name = config.CON_NAME
        game_tablename = config.GAME_TABLE
        db = QSqlDatabase.database(con_name)
        if not db.open():
            self.logger.error(f"Could not open database connection {con_name}")
            return False
        if not database.check_table_exists(game_tablename, con_name):
            self.logger.error(f"Game table does not exist.")
            return False
        updateGameQueryStr = f"""
            UPDATE {game_tablename}
            SET pos={self.pos}, incorrect_chars='{''.join(self.incorrect_chars.elements())}', elapsed={self.elapsed}
            WHERE id={self.id};
        """
        query = QSqlQuery(db)
        if not query.exec(updateGameQueryStr):
            self.logger.error(f"Unable to update game {self.id} in table {game_tablename}:\n" + query.lastError().text())
            return False

        self.logger.debug(f"Updated game {self.id} in table {db.databaseName()}.{game_tablename}")
        return True
    